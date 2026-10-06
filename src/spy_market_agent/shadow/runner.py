from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

from spy_market_agent.market_data.acquisition import CanonicalDailyBar, DatasetManifest, utc_now
from spy_market_agent.market_data.calendar import (
    MARKET_CALENDAR,
    MARKET_TIMEZONE,
    TradingCalendar,
    XNYSCalendar,
)
from spy_market_agent.market_data.errors import ChecksumMismatch, ManifestValidationFailure
from spy_market_agent.market_data.manifest import (
    canonical_bars_from_csv_bytes,
    canonical_content_checksum,
    sha256_bytes,
)
from spy_market_agent.market_data.models import MARKET_SYMBOL, MARKET_TIMEFRAME
from spy_market_agent.market_data.storage import DatasetStore
from spy_market_agent.persistence.serialization import datetime_to_text
from spy_market_agent.shadow.freshness import evaluate_market_data_freshness
from spy_market_agent.shadow.identity import shadow_run_identity
from spy_market_agent.shadow.persistence import (
    ShadowAlertRecord,
    ShadowDuplicateRunError,
    ShadowHealthEventRecord,
    ShadowInputSnapshotRecord,
    ShadowOperationalRunStatus,
    ShadowRecoveryRequiredError,
    ShadowRunRecord,
    ShadowSQLiteRepository,
)
from spy_market_agent.shadow.policy import evaluate_observation_only_run
from spy_market_agent.shadow.types import (
    DailyMarketDataStatus,
    DataSnapshotLineage,
    FreshnessDecision,
    FreshnessStatus,
    ModelAdmissionStatus,
    ShadowHealthStatus,
    ShadowMode,
    ShadowModelMetadata,
    ShadowRunConfiguration,
    ShadowRunDecision,
    ShadowRunRequest,
    ShadowRunStatus,
)

SHADOW_OPERATION_CONFIGURATION_VERSION = "phase4-observation-pipeline-v1"
SHADOW_OPERATION_FEATURE_SCHEMA = "phase4-observation-only-no-model-v1"
PHASE4_SHADOW_ADJUSTMENT_POLICY = "all"

Clock = Callable[[], datetime]

_BLOCKING_ALERT_CODES: dict[str, str] = {
    "stale_data": "stale_data",
    "missing_session": "missing_session",
    "invalid_ohlcv": "invalid_market_data",
    "provider_not_finalized": "provider_not_finalized",
    "lineage_failure": "lineage_failure",
    "duplicate_run": "duplicate_run",
    "persistence_failure": "persistence_failure",
    "recovery_required": "recovery_required",
    "unexpected_configuration": "unexpected_configuration",
}


class ShadowOperationalError(RuntimeError):
    """Raised when the Phase 4 observation-only runner fails closed."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True, slots=True)
class VerifiedShadowInput:
    manifest: DatasetManifest
    canonical_bars: tuple[CanonicalDailyBar, ...]


@dataclass(frozen=True, slots=True)
class ShadowOperationalSnapshot:
    request: ShadowRunRequest
    market_data_status: DailyMarketDataStatus
    first_session: date
    latest_session: date
    row_count: int
    manifest_artifact_checksum: str | None
    provider: str
    feed: str
    adjustment: str


@dataclass(frozen=True, slots=True)
class ShadowObservationResult:
    shadow_run_id: str
    mode: ShadowMode
    signal_session: date
    run_status: ShadowOperationalRunStatus
    data_status: FreshnessStatus
    monitoring_status: ShadowHealthStatus
    model_gate_status: ModelAdmissionStatus
    alerts: tuple[str, ...]
    proposal_generated: bool
    decision: ShadowRunDecision

    def sanitized_summary_lines(self, *, shadow_db_display: str) -> tuple[str, ...]:
        return (
            f"shadow_run_id={self.shadow_run_id}",
            f"mode={self.mode.value}",
            f"session={self.signal_session.isoformat()}",
            f"data_status={self.data_status.value}",
            f"run_status={self.run_status.value}",
            f"monitoring_status={self.monitoring_status.value}",
            f"model_gate_status={self.model_gate_status.value}",
            f"alerts={','.join(self.alerts) if self.alerts else 'none'}",
            f"proposal_generated={str(self.proposal_generated).lower()}",
            f"shadow_db={shadow_db_display}",
        )


def run_observation(
    *,
    manifest_path: Path,
    data_root: Path,
    shadow_db: Path,
    session: date,
    as_of: datetime,
    provider_finalized: bool,
    provider_finalization_policy_id: str,
    repository_root: Path | None = None,
    clock: Clock = utc_now,
    mode: ShadowMode = ShadowMode.OBSERVATION_ONLY_NO_MODEL,
    model_metadata: ShadowModelMetadata | None = None,
    configuration_version: str = SHADOW_OPERATION_CONFIGURATION_VERSION,
) -> ShadowObservationResult:
    if mode != ShadowMode.OBSERVATION_ONLY_NO_MODEL:
        raise ShadowOperationalError(
            "observation_only_mode_required",
            "Phase 4 Observation Pipeline V1 only permits observation-only mode.",
        )
    if model_metadata is not None:
        raise ShadowOperationalError(
            "observation_only_model_metadata_not_allowed",
            "Phase 4 Observation Pipeline V1 does not accept model metadata.",
        )

    as_of_utc = require_explicit_utc_datetime(as_of, field_name="as_of")
    created_at = require_explicit_utc_datetime(clock(), field_name="created_at")
    verified_input = verify_phase1_shadow_input(
        manifest_path=manifest_path,
        data_root=data_root,
        repository_root=repository_root,
    )
    snapshot = build_operational_snapshot(
        manifest=verified_input.manifest,
        canonical_bars=verified_input.canonical_bars,
        session=session,
        as_of=as_of_utc,
        provider_finalized=provider_finalized,
        provider_finalization_policy_id=provider_finalization_policy_id,
        configuration_version=configuration_version,
    )
    shadow_run_id = shadow_run_identity(snapshot.request)

    repository = ShadowSQLiteRepository(shadow_db)
    repository.initialize()
    try:
        repository.reserve_run(
            ShadowRunRecordBuilder.reserved(
                snapshot=snapshot,
                shadow_run_id=shadow_run_id,
                created_at=created_at,
            )
        )
    except (ShadowDuplicateRunError, ShadowRecoveryRequiredError) as exc:
        retry_rejected_at = require_explicit_utc_datetime(
            clock(),
            field_name="retry_rejected_at",
        )
        _record_retry_rejection(
            repository=repository,
            shadow_run_id=shadow_run_id,
            code=exc.code,
            timestamp=retry_rejected_at,
        )
        raise

    try:
        freshness = evaluate_market_data_freshness(snapshot.market_data_status)
        decision = evaluate_observation_only_run(snapshot.request, snapshot.market_data_status)
        terminal_status = (
            ShadowOperationalRunStatus.COMPLETED
            if decision.run_status == ShadowRunStatus.OBSERVATION_READY
            else ShadowOperationalRunStatus.BLOCKED
        )
        event_timestamp = require_explicit_utc_datetime(clock(), field_name="event_timestamp")
        health_events = _health_events_from_decision(
            shadow_run_id=shadow_run_id,
            decision=decision,
            freshness=freshness,
            timestamp=event_timestamp,
        )
        alerts = _alerts_from_events(health_events, timestamp=event_timestamp)
        repository.finalize_run(
            shadow_run_id=shadow_run_id,
            terminal_status=terminal_status,
            freshness_status=decision.freshness_status,
            monitoring_status=decision.monitoring_status,
            model_gate_status=ModelAdmissionStatus.BLOCKED_NO_APPROVED_MODEL,
            completed_at=event_timestamp,
            input_snapshot=_input_snapshot_record(
                snapshot=snapshot,
                shadow_run_id=shadow_run_id,
                created_at=event_timestamp,
            ),
            health_events=health_events,
            alerts=alerts,
        )
        return ShadowObservationResult(
            shadow_run_id=shadow_run_id,
            mode=snapshot.request.configuration.mode,
            signal_session=session,
            run_status=terminal_status,
            data_status=decision.freshness_status,
            monitoring_status=decision.monitoring_status,
            model_gate_status=ModelAdmissionStatus.BLOCKED_NO_APPROVED_MODEL,
            alerts=tuple(alert.alert_code for alert in alerts),
            proposal_generated=False,
            decision=decision,
        )
    except Exception as exc:
        failed_at = require_explicit_utc_datetime(clock(), field_name="failed_at")
        event = ShadowHealthEventRecord(
            shadow_run_id=shadow_run_id,
            event_code="unexpected_configuration",
            status=ShadowHealthStatus.BLOCKED,
            message="shadow observation run failed after reservation.",
            event_timestamp=datetime_to_text(failed_at),
        )
        alert = ShadowAlertRecord(
            shadow_run_id=shadow_run_id,
            alert_code="unexpected_configuration",
            status=ShadowHealthStatus.BLOCKED,
            message="shadow observation run failed after reservation.",
            created_at=datetime_to_text(failed_at),
        )
        repository.mark_failed(
            shadow_run_id=shadow_run_id,
            completed_at=failed_at,
            event=event,
            alert=alert,
        )
        raise ShadowOperationalError(
            "shadow_observation_failed",
            "shadow observation run failed after reservation.",
        ) from exc


def verify_phase1_shadow_input(
    *,
    manifest_path: Path,
    data_root: Path,
    repository_root: Path | None = None,
) -> VerifiedShadowInput:
    repo_root = (repository_root or Path.cwd()).resolve(strict=False)
    store = DatasetStore(data_root, repository_root=repo_root)
    manifest = store.verify_manifest_artifacts(manifest_path)
    _validate_manifest_for_shadow(manifest)
    canonical_path = _resolve_verified_canonical_path(
        manifest=manifest,
        data_root=data_root,
        repository_root=repo_root,
    )
    canonical_bytes = canonical_path.read_bytes()
    if sha256_bytes(canonical_bytes) != manifest.artifact_checksum:
        raise ChecksumMismatch("canonical artifact checksum changed after verification.")
    bars = canonical_bars_from_csv_bytes(canonical_bytes)
    canonical_checksum = canonical_content_checksum(
        bars=bars,
        provider=manifest.provider,
        feed=manifest.feed,
        timeframe=manifest.timeframe,
        adjustment_mode=manifest.adjustment_mode,
        corporate_action_policy=manifest.corporate_action_policy,
    )
    if canonical_checksum != manifest.canonical_content_checksum:
        raise ChecksumMismatch("canonical content checksum changed after verification.")
    return VerifiedShadowInput(manifest=manifest, canonical_bars=bars)


def build_operational_snapshot(
    *,
    manifest: DatasetManifest,
    canonical_bars: tuple[CanonicalDailyBar, ...],
    session: date,
    as_of: datetime,
    provider_finalized: bool,
    provider_finalization_policy_id: str,
    configuration_version: str = SHADOW_OPERATION_CONFIGURATION_VERSION,
) -> ShadowOperationalSnapshot:
    as_of_utc = require_explicit_utc_datetime(as_of, field_name="as_of")
    _validate_manifest_for_shadow(manifest)
    configuration = ShadowRunConfiguration(
        configuration_version=configuration_version,
        provider_finalization_policy_id=provider_finalization_policy_id,
        mode=ShadowMode.OBSERVATION_ONLY_NO_MODEL,
    )
    calendar = XNYSCalendar()
    try:
        if not calendar.is_session(session):
            raise ShadowOperationalError(
                "not_xnys_session",
                "target session must be a valid XNYS session.",
            )
    except ShadowOperationalError:
        raise
    except Exception as exc:
        raise ShadowOperationalError(
            "calendar_uncertainty",
            "target session cannot be validated against XNYS calendar.",
        ) from exc

    sessions = tuple(bar.session_date for bar in canonical_bars)
    if not sessions:
        raise ShadowOperationalError("missing_session", "canonical dataset contains no sessions.")
    if sessions != tuple(sorted(sessions)):
        raise ShadowOperationalError(
            "out_of_order_sessions",
            "canonical sessions must be strictly increasing.",
        )
    if len(sessions) != len(set(sessions)):
        raise ShadowOperationalError(
            "duplicate_session",
            "canonical sessions must be unique.",
        )
    target_count = sessions.count(session)
    if target_count != 1:
        raise ShadowOperationalError(
            "missing_session" if target_count == 0 else "duplicate_session",
            "target session must exist exactly once in the canonical dataset.",
        )
    latest_session = sessions[-1]
    if session != latest_session:
        raise ShadowOperationalError(
            "target_session_not_latest",
            "target session must be the latest canonical session in the operational snapshot.",
        )
    target_bar = canonical_bars[-1]
    ohlcv_valid = _bar_ohlcv_is_valid(target_bar)
    latest_completed = latest_completed_xnys_session(
        calendar=calendar,
        as_of=as_of_utc,
        earliest_session=sessions[0],
    )
    stale = latest_completed is not None and latest_session < latest_completed
    session_complete = calendar.is_session_complete(session, as_of=as_of_utc)
    lineage = DataSnapshotLineage(
        dataset_id=manifest.dataset_id,
        canonical_dataset_checksum=manifest.canonical_content_checksum,
        provider=manifest.provider,
        feed=manifest.feed,
        timeframe=manifest.timeframe,
        adjustment=manifest.adjustment_mode,
        symbol=manifest.symbol,
        session=session,
        first_session=sessions[0],
        latest_session=latest_session,
        manifest_artifact_checksum=manifest.manifest_artifact_checksum,
        row_count=len(canonical_bars),
    )
    request = ShadowRunRequest(
        configuration=configuration,
        data_lineage=lineage,
        signal_session=session,
        feature_schema=SHADOW_OPERATION_FEATURE_SCHEMA,
        as_of=as_of_utc,
        model_metadata=None,
    )
    market_data_status = DailyMarketDataStatus(
        symbol=manifest.symbol,
        timeframe=manifest.timeframe,
        exchange_calendar=MARKET_CALENDAR,
        adjustment=manifest.adjustment_mode,
        session=session,
        as_of=as_of_utc,
        provider_finalized=provider_finalized,
        expected_session_present=True,
        session_complete=session_complete,
        duplicate_sessions_detected=False,
        out_of_order_sessions_detected=False,
        ohlcv_valid=ohlcv_valid,
        stale=stale,
    )
    return ShadowOperationalSnapshot(
        request=request,
        market_data_status=market_data_status,
        first_session=sessions[0],
        latest_session=latest_session,
        row_count=len(canonical_bars),
        manifest_artifact_checksum=manifest.manifest_artifact_checksum,
        provider=manifest.provider,
        feed=manifest.feed,
        adjustment=manifest.adjustment_mode,
    )


def latest_completed_xnys_session(
    *,
    calendar: TradingCalendar,
    as_of: datetime,
    earliest_session: date,
) -> date | None:
    as_of_utc = require_explicit_utc_datetime(as_of, field_name="as_of")
    market_date = as_of_utc.astimezone(ZoneInfo(MARKET_TIMEZONE)).date()
    if market_date < earliest_session:
        return None
    try:
        sessions = calendar.sessions_between(earliest_session, market_date)
    except Exception as exc:
        raise ShadowOperationalError(
            "calendar_uncertainty",
            "latest completed XNYS session cannot be determined.",
        ) from exc
    completed = tuple(
        session for session in sessions if calendar.is_session_complete(session, as_of=as_of_utc)
    )
    if not completed:
        return None
    return completed[-1]


def require_explicit_utc_datetime(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ShadowOperationalError(
            f"invalid_{field_name}",
            f"{field_name} must be timezone-aware UTC.",
        )
    if value.utcoffset() != timedelta(0):
        raise ShadowOperationalError(
            f"invalid_{field_name}",
            f"{field_name} must use UTC offset zero.",
        )
    return value.astimezone(UTC)


def _validate_manifest_for_shadow(manifest: DatasetManifest) -> None:
    if manifest.symbol != MARKET_SYMBOL:
        raise ManifestValidationFailure("Phase 4 shadow input symbol must be SPY.")
    if manifest.timeframe != MARKET_TIMEFRAME:
        raise ManifestValidationFailure("Phase 4 shadow input timeframe must be 1Day.")
    if manifest.adjustment_mode != PHASE4_SHADOW_ADJUSTMENT_POLICY:
        raise ManifestValidationFailure("Phase 4 shadow input adjustment must be all.")
    calendar_code = manifest.relevant_configuration.get("calendar")
    if calendar_code != MARKET_CALENDAR:
        raise ManifestValidationFailure("Phase 4 shadow input calendar must be XNYS.")


def _resolve_verified_canonical_path(
    *,
    manifest: DatasetManifest,
    data_root: Path,
    repository_root: Path,
) -> Path:
    if data_root.is_absolute() or any(part == ".." for part in data_root.parts):
        raise ShadowOperationalError(
            "unsafe_data_root",
            "data_root must be repository-relative.",
        )
    canonical_path = (repository_root / manifest.generated_file_locations.canonical_path).resolve(
        strict=False
    )
    data_root_path = (repository_root / data_root).resolve(strict=False)
    try:
        canonical_path.relative_to(data_root_path)
    except ValueError as exc:
        raise ShadowOperationalError(
            "unsafe_canonical_path",
            "canonical artifact path must remain inside data_root.",
        ) from exc
    return canonical_path


def _bar_ohlcv_is_valid(bar: CanonicalDailyBar) -> bool:
    try:
        open_price = _positive_decimal(bar.open)
        high_price = _positive_decimal(bar.high)
        low_price = _positive_decimal(bar.low)
        close_price = _positive_decimal(bar.close)
    except ShadowOperationalError:
        return False
    return (
        bar.volume >= 0
        and high_price >= low_price
        and high_price >= open_price
        and high_price >= close_price
        and low_price <= open_price
        and low_price <= close_price
    )


def _positive_decimal(value: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ShadowOperationalError(
            "invalid_ohlcv",
            "canonical OHLCV values must be finite positive decimals.",
        ) from exc
    if not parsed.is_finite() or parsed <= 0:
        raise ShadowOperationalError(
            "invalid_ohlcv",
            "canonical OHLCV values must be finite positive decimals.",
        )
    return parsed


class ShadowRunRecordBuilder:
    @staticmethod
    def reserved(
        *,
        snapshot: ShadowOperationalSnapshot,
        shadow_run_id: str,
        created_at: datetime,
    ) -> ShadowRunRecord:
        return ShadowRunRecord(
            shadow_run_id=shadow_run_id,
            configuration_version=snapshot.request.configuration.configuration_version,
            mode=snapshot.request.configuration.mode,
            symbol=snapshot.request.configuration.symbol,
            timeframe=snapshot.request.configuration.timeframe,
            signal_session=date_to_identity_text(snapshot.request.signal_session),
            as_of=datetime_to_text(snapshot.request.as_of),
            parent_dataset_id=snapshot.request.data_lineage.dataset_id,
            canonical_dataset_checksum=snapshot.request.data_lineage.canonical_dataset_checksum,
            provider_finalization_policy_id=(
                snapshot.request.configuration.provider_finalization_policy_id
            ),
            run_status=ShadowOperationalRunStatus.RESERVED,
            freshness_status=FreshnessStatus.BLOCKED,
            monitoring_status=ShadowHealthStatus.BLOCKED,
            model_gate_status=ModelAdmissionStatus.BLOCKED_NO_APPROVED_MODEL,
            created_at=datetime_to_text(created_at),
            completed_at=None,
        )


def date_to_identity_text(value: date) -> str:
    if isinstance(value, datetime) or type(value) is not date:
        raise ShadowOperationalError("invalid_session", "session must be a plain date.")
    return value.isoformat()


def _input_snapshot_record(
    *,
    snapshot: ShadowOperationalSnapshot,
    shadow_run_id: str,
    created_at: datetime,
) -> ShadowInputSnapshotRecord:
    lineage = snapshot.request.data_lineage
    return ShadowInputSnapshotRecord(
        shadow_run_id=shadow_run_id,
        parent_dataset_id=lineage.dataset_id,
        canonical_dataset_checksum=lineage.canonical_dataset_checksum,
        symbol=lineage.symbol,
        timeframe=lineage.timeframe,
        provider=snapshot.provider,
        feed=snapshot.feed,
        adjustment=snapshot.adjustment,
        first_session=date_to_identity_text(snapshot.first_session),
        latest_session=date_to_identity_text(snapshot.latest_session),
        target_session=date_to_identity_text(snapshot.request.signal_session),
        row_count=snapshot.row_count,
        provider_finalization_policy_id=(
            snapshot.request.configuration.provider_finalization_policy_id
        ),
        manifest_artifact_checksum=snapshot.manifest_artifact_checksum,
        snapshot_created_at=datetime_to_text(created_at),
    )


def _health_events_from_decision(
    *,
    shadow_run_id: str,
    decision: ShadowRunDecision,
    freshness: FreshnessDecision,
    timestamp: datetime,
) -> tuple[ShadowHealthEventRecord, ...]:
    event_timestamp = datetime_to_text(timestamp)
    freshness_reasons = freshness.reasons
    events: list[ShadowHealthEventRecord] = []
    if decision.freshness_status == FreshnessStatus.FRESH:
        events.append(
            ShadowHealthEventRecord(
                shadow_run_id=shadow_run_id,
                event_code="fresh_data",
                status=ShadowHealthStatus.HEALTHY,
                message="canonical SPY daily data is fresh for the target session.",
                event_timestamp=event_timestamp,
            )
        )
    for reason in freshness_reasons:
        events.append(
            ShadowHealthEventRecord(
                shadow_run_id=shadow_run_id,
                event_code=reason,
                status=ShadowHealthStatus.BLOCKED,
                message=f"shadow observation blocked by {reason}.",
                event_timestamp=event_timestamp,
            )
        )
    events.append(
        ShadowHealthEventRecord(
            shadow_run_id=shadow_run_id,
            event_code="model_not_approved",
            status=ShadowHealthStatus.HEALTHY,
            message="Gate B remains locked; no approved shadow model exists.",
            event_timestamp=event_timestamp,
        )
    )
    return tuple(events)


def _alerts_from_events(
    events: tuple[ShadowHealthEventRecord, ...],
    *,
    timestamp: datetime,
) -> tuple[ShadowAlertRecord, ...]:
    created_at = datetime_to_text(timestamp)
    alerts: list[ShadowAlertRecord] = []
    for event in events:
        if event.status != ShadowHealthStatus.BLOCKED:
            continue
        alert_code = _alert_code_for_event(event.event_code)
        if alert_code is None:
            continue
        alerts.append(
            ShadowAlertRecord(
                shadow_run_id=event.shadow_run_id,
                alert_code=alert_code,
                status=ShadowHealthStatus.BLOCKED,
                message=event.message,
                created_at=created_at,
            )
        )
    return tuple(alerts)


def _record_retry_rejection(
    *,
    repository: ShadowSQLiteRepository,
    shadow_run_id: str,
    code: str,
    timestamp: datetime,
) -> None:
    event_timestamp = datetime_to_text(timestamp)
    message = f"shadow observation retry rejected by {code}."
    event = ShadowHealthEventRecord(
        shadow_run_id=shadow_run_id,
        event_code=code,
        status=ShadowHealthStatus.BLOCKED,
        message=message,
        event_timestamp=event_timestamp,
    )
    alert_code = _alert_code_for_event(code)
    if alert_code is None:
        alert_code = "unexpected_configuration"
    alert = ShadowAlertRecord(
        shadow_run_id=shadow_run_id,
        alert_code=alert_code,
        status=ShadowHealthStatus.BLOCKED,
        message=message,
        created_at=event_timestamp,
    )
    repository.record_retry_rejection(
        shadow_run_id=shadow_run_id,
        event=event,
        alert=alert,
    )


def _alert_code_for_event(event_code: str) -> str | None:
    return _BLOCKING_ALERT_CODES.get(event_code)


__all__ = [
    "PHASE4_SHADOW_ADJUSTMENT_POLICY",
    "SHADOW_OPERATION_CONFIGURATION_VERSION",
    "SHADOW_OPERATION_FEATURE_SCHEMA",
    "ShadowObservationResult",
    "ShadowOperationalError",
    "ShadowOperationalSnapshot",
    "VerifiedShadowInput",
    "build_operational_snapshot",
    "date_to_identity_text",
    "latest_completed_xnys_session",
    "require_explicit_utc_datetime",
    "run_observation",
    "verify_phase1_shadow_input",
]
