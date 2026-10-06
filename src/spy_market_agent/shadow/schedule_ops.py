from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from spy_market_agent.market_data.acquisition import utc_now
from spy_market_agent.market_data.calendar import CALENDAR_START, TradingCalendar, XNYSCalendar
from spy_market_agent.persistence.serialization import datetime_to_text
from spy_market_agent.run_ids import validate_run_id
from spy_market_agent.shadow.freshness import evaluate_market_data_freshness
from spy_market_agent.shadow.identity import shadow_run_identity
from spy_market_agent.shadow.persistence import (
    ShadowOperationalRunStatus,
    ShadowRunRecord,
    ShadowSQLiteRepository,
)
from spy_market_agent.shadow.runner import (
    SHADOW_OPERATION_CONFIGURATION_VERSION,
    ShadowObservationResult,
    ShadowOperationalError,
    build_operational_snapshot,
    latest_completed_xnys_session,
    require_explicit_utc_datetime,
    run_observation,
    verify_phase1_shadow_input,
)
from spy_market_agent.shadow.types import (
    ModelAdmissionStatus,
    ShadowHealthStatus,
    ShadowMode,
)

SCHEDULED_OBSERVATION_POLICY_VERSION = "phase4-scheduled-observation-v1"

_TERMINAL_STATUSES = frozenset(
    {
        ShadowOperationalRunStatus.COMPLETED,
        ShadowOperationalRunStatus.BLOCKED,
        ShadowOperationalRunStatus.FAILED,
    }
)


class ScheduledObservationState(StrEnum):
    DUE = "due"
    ALREADY_PROCESSED = "already_processed"
    RECOVERY_REQUIRED = "recovery_required"
    BLOCKED = "blocked"


class ScheduledObservationHistoryStatus(StrEnum):
    NO_PRIOR_HISTORY = "no_prior_history"
    HISTORY_AVAILABLE = "history_available"


class ScheduledObservationAction(StrEnum):
    RAN_OBSERVATION = "ran_observation"
    ALREADY_PROCESSED = "already_processed"
    RECOVERY_REQUIRED = "recovery_required"
    BLOCKED = "blocked"


class ScheduledObservationDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    schedule_policy_version: str = SCHEDULED_OBSERVATION_POLICY_VERSION
    mode: ShadowMode = ShadowMode.OBSERVATION_ONLY_NO_MODEL
    as_of: datetime
    target_session: date | None
    latest_completed_xnys_session: date | None
    latest_canonical_session: date | None
    provider_finalized: bool
    provider_finalization_policy_id: str
    shadow_run_id: str | None = None
    already_processed: bool = False
    existing_run_status: ShadowOperationalRunStatus | None = None
    recovery_required: bool = False
    missed_observation_sessions: tuple[date, ...] = ()
    history_status: ScheduledObservationHistoryStatus
    latest_prior_observation_session: date | None = None
    latest_prior_observation_status: ShadowOperationalRunStatus | None = None
    eligible: bool
    status: ShadowHealthStatus
    state: ScheduledObservationState
    refusal_reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @field_validator("as_of")
    @classmethod
    def _validate_utc_as_of(cls, value: datetime) -> datetime:
        return require_explicit_utc_datetime(value, field_name="as_of")

    @field_validator("provider_finalization_policy_id")
    @classmethod
    def _validate_provider_policy(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed or "/" in trimmed or "\\" in trimmed or ".." in trimmed:
            msg = "provider_finalization_policy_id must be nonempty and path-safe."
            raise ValueError(msg)
        return trimmed

    @field_validator("shadow_run_id")
    @classmethod
    def _validate_run_id(cls, value: str | None) -> str | None:
        if value is not None:
            validate_run_id(value)
        return value

    @model_validator(mode="after")
    def _validate_consistency(self) -> ScheduledObservationDecision:
        if self.mode != ShadowMode.OBSERVATION_ONLY_NO_MODEL:
            msg = "scheduled observation currently requires observation-only mode."
            raise ValueError(msg)
        if self.eligible and self.refusal_reasons:
            msg = "eligible schedule decisions must not include blocking refusal reasons."
            raise ValueError(msg)
        if self.eligible and self.state != ScheduledObservationState.DUE:
            msg = "eligible schedule decisions must be due."
            raise ValueError(msg)
        if self.eligible and self.status == ShadowHealthStatus.BLOCKED:
            msg = "eligible schedule decisions must not be blocked."
            raise ValueError(msg)
        if self.already_processed:
            if self.eligible:
                msg = "already-processed schedule decisions must not be eligible."
                raise ValueError(msg)
            if self.state != ScheduledObservationState.ALREADY_PROCESSED:
                msg = "already-processed decisions must use already_processed state."
                raise ValueError(msg)
            if self.existing_run_status not in _TERMINAL_STATUSES:
                msg = "already-processed decisions require a terminal existing run status."
                raise ValueError(msg)
        if self.recovery_required:
            if self.eligible:
                msg = "recovery-required schedule decisions must not be eligible."
                raise ValueError(msg)
            if self.state != ScheduledObservationState.RECOVERY_REQUIRED:
                msg = "recovery-required decisions must use recovery_required state."
                raise ValueError(msg)
            if self.existing_run_status != ShadowOperationalRunStatus.RESERVED:
                msg = "recovery-required decisions require a reserved existing run."
                raise ValueError(msg)
        if not self.provider_finalized and "provider_not_finalized" not in self.refusal_reasons:
            msg = "provider_finalized=false must block with provider_not_finalized."
            raise ValueError(msg)
        if self.missed_observation_sessions and "missed_observation_sessions" not in self.warnings:
            msg = "missed observation sessions must be exposed as warnings."
            raise ValueError(msg)
        if self.eligible and self.warnings and self.status != ShadowHealthStatus.DEGRADED:
            msg = "eligible schedule decisions with warnings must be degraded."
            raise ValueError(msg)
        if not self.eligible and self.state == ScheduledObservationState.DUE:
            msg = "ineligible schedule decisions must not remain due."
            raise ValueError(msg)
        return self

    def sanitized_summary_lines(self) -> tuple[str, ...]:
        reasons = _joined(self.refusal_reasons + self.warnings)
        missed_sessions = _joined(
            tuple(session.isoformat() for session in self.missed_observation_sessions)
        )
        existing_status = self.existing_run_status.value if self.existing_run_status else "none"
        prior_status = (
            self.latest_prior_observation_status.value
            if self.latest_prior_observation_status
            else "none"
        )
        return (
            f"schedule_state={self.state.value}",
            f"mode={self.mode.value}",
            f"as_of={datetime_to_text(self.as_of)}",
            f"target_session={_date_or_none(self.target_session)}",
            f"latest_completed_xnys_session={_date_or_none(self.latest_completed_xnys_session)}",
            f"latest_canonical_session={_date_or_none(self.latest_canonical_session)}",
            f"provider_finalized={str(self.provider_finalized).lower()}",
            f"provider_finalization_policy_id={self.provider_finalization_policy_id}",
            f"shadow_run_id={self.shadow_run_id or 'none'}",
            f"already_processed={str(self.already_processed).lower()}",
            f"existing_run_status={existing_status}",
            f"recovery_required={str(self.recovery_required).lower()}",
            f"history_status={self.history_status.value}",
            f"latest_prior_observation_session={_date_or_none(self.latest_prior_observation_session)}",
            f"latest_prior_observation_status={prior_status}",
            f"missed_observation_count={len(self.missed_observation_sessions)}",
            f"missed_observation_sessions={missed_sessions}",
            f"eligible={str(self.eligible).lower()}",
            f"status={self.status.value}",
            f"model_gate_status={ModelAdmissionStatus.BLOCKED_NO_APPROVED_MODEL.value}",
            f"reasons={reasons}",
        )


@dataclass(frozen=True, slots=True)
class ScheduledObservationRunResult:
    action: ScheduledObservationAction
    decision: ScheduledObservationDecision
    observation_result: ShadowObservationResult | None = None


def resolve_latest_completed_target_session(
    *,
    as_of: datetime,
    calendar: TradingCalendar | None = None,
) -> date:
    calendar = calendar or XNYSCalendar()
    as_of_utc = require_explicit_utc_datetime(as_of, field_name="as_of")
    latest_completed = latest_completed_xnys_session(
        calendar=calendar,
        as_of=as_of_utc,
        earliest_session=CALENDAR_START,
    )
    if latest_completed is None:
        raise ShadowOperationalError(
            "calendar_uncertainty",
            "no completed XNYS session is available for the supplied as_of timestamp.",
        )
    return latest_completed


def evaluate_scheduled_observation(
    *,
    manifest_path: Path,
    data_root: Path,
    shadow_db: Path,
    as_of: datetime,
    provider_finalized: bool,
    provider_finalization_policy_id: str,
    repository_root: Path | None = None,
    calendar: TradingCalendar | None = None,
    mode: ShadowMode = ShadowMode.OBSERVATION_ONLY_NO_MODEL,
) -> ScheduledObservationDecision:
    if mode != ShadowMode.OBSERVATION_ONLY_NO_MODEL:
        raise ShadowOperationalError(
            "observation_only_mode_required",
            "scheduled observation currently permits observation-only mode only.",
        )
    as_of_utc = require_explicit_utc_datetime(as_of, field_name="as_of")
    calendar = calendar or XNYSCalendar()
    target_session = resolve_latest_completed_target_session(
        as_of=as_of_utc,
        calendar=calendar,
    )
    verified_input = verify_phase1_shadow_input(
        manifest_path=manifest_path,
        data_root=data_root,
        repository_root=repository_root,
    )
    sessions = tuple(bar.session_date for bar in verified_input.canonical_bars)
    latest_canonical_session = sessions[-1] if sessions else None
    refusal_reasons: list[str] = []
    warnings: list[str] = []
    shadow_run_id: str | None = None

    if not provider_finalized:
        refusal_reasons.append("provider_not_finalized")
    if latest_canonical_session is None:
        refusal_reasons.append("missing_session")
    elif latest_canonical_session < target_session:
        refusal_reasons.append("stale_data")
    elif latest_canonical_session > target_session:
        refusal_reasons.append("data_ahead_of_completed_session")

    if latest_canonical_session == target_session:
        snapshot = build_operational_snapshot(
            manifest=verified_input.manifest,
            canonical_bars=verified_input.canonical_bars,
            session=target_session,
            as_of=as_of_utc,
            provider_finalized=provider_finalized,
            provider_finalization_policy_id=provider_finalization_policy_id,
        )
        shadow_run_id = shadow_run_identity(snapshot.request)
        freshness = evaluate_market_data_freshness(snapshot.market_data_status, calendar=calendar)
        refusal_reasons.extend(freshness.reasons)

    history = _load_history(shadow_db)
    current_run = _run_by_id(history.runs, shadow_run_id) if shadow_run_id is not None else None
    existing_run_status = current_run.run_status if current_run is not None else None
    already_processed = existing_run_status in _TERMINAL_STATUSES
    recovery_required = existing_run_status == ShadowOperationalRunStatus.RESERVED

    if already_processed:
        refusal_reasons.append("already_processed")
    if recovery_required:
        refusal_reasons.append("recovery_required")

    missed_sessions, latest_prior = _missed_observation_sessions(
        runs=history.runs,
        target_session=target_session,
        calendar=calendar,
    )
    if missed_sessions:
        warnings.append("missed_observation_sessions")
    if latest_prior is not None and latest_prior.run_status == ShadowOperationalRunStatus.FAILED:
        warnings.append("prior_failed_observation")

    unique_refusals = _unique(refusal_reasons)
    unique_warnings = _unique(warnings)
    if already_processed:
        state = ScheduledObservationState.ALREADY_PROCESSED
        eligible = False
        status = (
            current_run.monitoring_status if current_run is not None else ShadowHealthStatus.BLOCKED
        )
    elif recovery_required:
        state = ScheduledObservationState.RECOVERY_REQUIRED
        eligible = False
        status = ShadowHealthStatus.BLOCKED
    elif unique_refusals:
        state = ScheduledObservationState.BLOCKED
        eligible = False
        status = ShadowHealthStatus.BLOCKED
    else:
        state = ScheduledObservationState.DUE
        eligible = True
        status = ShadowHealthStatus.DEGRADED if unique_warnings else ShadowHealthStatus.HEALTHY

    return ScheduledObservationDecision(
        mode=ShadowMode.OBSERVATION_ONLY_NO_MODEL,
        as_of=as_of_utc,
        target_session=target_session,
        latest_completed_xnys_session=target_session,
        latest_canonical_session=latest_canonical_session,
        provider_finalized=provider_finalized,
        provider_finalization_policy_id=provider_finalization_policy_id,
        shadow_run_id=shadow_run_id,
        already_processed=already_processed,
        existing_run_status=existing_run_status,
        recovery_required=recovery_required,
        missed_observation_sessions=missed_sessions,
        history_status=history.status,
        latest_prior_observation_session=(
            _run_session_date(latest_prior) if latest_prior is not None else None
        ),
        latest_prior_observation_status=(
            latest_prior.run_status if latest_prior is not None else None
        ),
        eligible=eligible,
        status=status,
        state=state,
        refusal_reasons=unique_refusals,
        warnings=unique_warnings,
    )


def run_due_observation(
    *,
    manifest_path: Path,
    data_root: Path,
    shadow_db: Path,
    as_of: datetime,
    provider_finalized: bool,
    provider_finalization_policy_id: str,
    repository_root: Path | None = None,
    clock: Callable[[], datetime] = utc_now,
) -> ScheduledObservationRunResult:
    decision = evaluate_scheduled_observation(
        manifest_path=manifest_path,
        data_root=data_root,
        shadow_db=shadow_db,
        as_of=as_of,
        provider_finalized=provider_finalized,
        provider_finalization_policy_id=provider_finalization_policy_id,
        repository_root=repository_root,
    )
    if decision.state == ScheduledObservationState.ALREADY_PROCESSED:
        return ScheduledObservationRunResult(
            action=ScheduledObservationAction.ALREADY_PROCESSED,
            decision=decision,
        )
    if decision.state == ScheduledObservationState.RECOVERY_REQUIRED:
        return ScheduledObservationRunResult(
            action=ScheduledObservationAction.RECOVERY_REQUIRED,
            decision=decision,
        )
    if not decision.eligible or decision.target_session is None:
        return ScheduledObservationRunResult(
            action=ScheduledObservationAction.BLOCKED,
            decision=decision,
        )

    observation_result = run_observation(
        manifest_path=manifest_path,
        data_root=data_root,
        shadow_db=shadow_db,
        session=decision.target_session,
        as_of=decision.as_of,
        provider_finalized=provider_finalized,
        provider_finalization_policy_id=provider_finalization_policy_id,
        repository_root=repository_root,
        clock=clock,
        mode=ShadowMode.OBSERVATION_ONLY_NO_MODEL,
        configuration_version=SHADOW_OPERATION_CONFIGURATION_VERSION,
    )
    return ScheduledObservationRunResult(
        action=ScheduledObservationAction.RAN_OBSERVATION,
        decision=decision,
        observation_result=observation_result,
    )


@dataclass(frozen=True, slots=True)
class _ObservationHistory:
    status: ScheduledObservationHistoryStatus
    runs: tuple[ShadowRunRecord, ...]


def _load_history(shadow_db: Path) -> _ObservationHistory:
    if not shadow_db.exists():
        return _ObservationHistory(
            status=ScheduledObservationHistoryStatus.NO_PRIOR_HISTORY,
            runs=(),
        )
    runs = ShadowSQLiteRepository(shadow_db).list_observation_runs()
    return _ObservationHistory(
        status=(
            ScheduledObservationHistoryStatus.HISTORY_AVAILABLE
            if runs
            else ScheduledObservationHistoryStatus.NO_PRIOR_HISTORY
        ),
        runs=runs,
    )


def _missed_observation_sessions(
    *,
    runs: tuple[ShadowRunRecord, ...],
    target_session: date,
    calendar: TradingCalendar,
) -> tuple[tuple[date, ...], ShadowRunRecord | None]:
    prior_terminal_runs = tuple(
        run
        for run in runs
        if run.run_status in _TERMINAL_STATUSES and _run_session_date(run) < target_session
    )
    if not prior_terminal_runs:
        return (), None
    latest_prior = max(prior_terminal_runs, key=_run_session_date)
    latest_prior_session = _run_session_date(latest_prior)
    try:
        expected_between = calendar.sessions_between(latest_prior_session, target_session)
    except Exception as exc:
        raise ShadowOperationalError(
            "calendar_uncertainty",
            "missed observation sessions cannot be determined.",
        ) from exc
    recorded_sessions = {_run_session_date(run) for run in runs}
    missed = tuple(
        session
        for session in expected_between
        if latest_prior_session < session < target_session and session not in recorded_sessions
    )
    return missed, latest_prior


def _run_by_id(
    runs: tuple[ShadowRunRecord, ...],
    shadow_run_id: str | None,
) -> ShadowRunRecord | None:
    if shadow_run_id is None:
        return None
    return next((run for run in runs if run.shadow_run_id == shadow_run_id), None)


def _run_session_date(run: ShadowRunRecord) -> date:
    return date.fromisoformat(run.signal_session)


def _unique(values: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _joined(values: tuple[str, ...]) -> str:
    return ",".join(values) if values else "none"


def _date_or_none(value: date | None) -> str:
    return value.isoformat() if value is not None else "none"


__all__ = [
    "SCHEDULED_OBSERVATION_POLICY_VERSION",
    "ScheduledObservationAction",
    "ScheduledObservationDecision",
    "ScheduledObservationHistoryStatus",
    "ScheduledObservationRunResult",
    "ScheduledObservationState",
    "evaluate_scheduled_observation",
    "resolve_latest_completed_target_session",
    "run_due_observation",
]
