from __future__ import annotations

import subprocess
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

import spy_market_agent
from spy_market_agent.market_data.calendar import CalendarDataError
from spy_market_agent.shadow import (
    BLOCKED_NO_APPROVED_MODEL,
    NO_APPROVED_SHADOW_MODEL,
    DailyMarketDataStatus,
    DataSnapshotLineage,
    DuplicateShadowRunError,
    FreshnessStatus,
    HypotheticalTargetState,
    ModelAdmissionStatus,
    ProposalStatus,
    ShadowHealthStatus,
    ShadowMode,
    ShadowModelAdmissionError,
    ShadowModelMetadata,
    ShadowMonitoringEvent,
    ShadowPolicyError,
    ShadowProposal,
    ShadowRunConfiguration,
    ShadowRunDecision,
    ShadowRunRequest,
    ShadowRunStatus,
    build_monitoring_state,
    evaluate_market_data_freshness,
    evaluate_model_admission,
    evaluate_observation_only_run,
    evaluate_shadow_run,
    monitoring_event,
    require_model_admission,
    shadow_run_identity,
    validate_shadow_model_metadata,
)
from spy_market_agent.shadow.schedule import ShadowScheduleInput, evaluate_shadow_schedule

ROOT = Path(__file__).resolve().parents[2]


class _CalendarUncertainty:
    name = "XNYS"
    timezone_name = "America/New_York"

    @property
    def calendar_code(self) -> str:
        return "XNYS"

    def is_session(self, session: date) -> bool:
        raise CalendarDataError("synthetic_calendar_uncertainty", session.isoformat())

    def sessions_between(self, _start_session: date, _end_session: date) -> tuple[date, ...]:
        return ()

    def missing_sessions(self, _observed_sessions: tuple[date, ...]) -> tuple[date, ...]:
        return ()

    def is_session_complete(self, session: date, *, as_of: datetime) -> bool:
        _ = as_of
        raise CalendarDataError("synthetic_calendar_uncertainty", session.isoformat())

    def to_utc(self, timestamp: datetime) -> datetime:
        return timestamp.astimezone(UTC)


class _IncompleteSessionCalendar:
    name = "XNYS"
    timezone_name = "America/New_York"

    @property
    def calendar_code(self) -> str:
        return "XNYS"

    def is_session(self, _session: date) -> bool:
        return True

    def sessions_between(self, start_session: date, end_session: date) -> tuple[date, ...]:
        return (start_session, end_session)

    def missing_sessions(self, _observed_sessions: tuple[date, ...]) -> tuple[date, ...]:
        return ()

    def is_session_complete(self, session: date, *, as_of: datetime) -> bool:
        _ = (session, as_of)
        return False

    def to_utc(self, timestamp: datetime) -> datetime:
        return timestamp.astimezone(UTC)


def _lineage(session: date = date(2025, 1, 2)) -> DataSnapshotLineage:
    return DataSnapshotLineage(
        dataset_id="synthetic-shadow-dataset",
        canonical_dataset_checksum="0" * 64,
        provider="synthetic",
        feed="synthetic",
        adjustment="all",
        session=session,
        row_count=1,
    )


def _configuration(
    mode: ShadowMode = ShadowMode.OBSERVATION_ONLY_NO_MODEL,
) -> ShadowRunConfiguration:
    return ShadowRunConfiguration(
        configuration_version="phase4-shadow-scaffold-v1",
        provider_finalization_policy_id="synthetic-provider-finalized-v1",
        mode=mode,
    )


def _request(
    *,
    mode: ShadowMode = ShadowMode.OBSERVATION_ONLY_NO_MODEL,
    session: date = date(2025, 1, 2),
    model_metadata: ShadowModelMetadata | None = None,
) -> ShadowRunRequest:
    return ShadowRunRequest(
        configuration=_configuration(mode),
        data_lineage=_lineage(session),
        signal_session=session,
        feature_schema="synthetic-shadow-feature-schema-v1",
        as_of=datetime(2025, 1, 3, 0, 0, tzinfo=UTC),
        model_metadata=model_metadata,
    )


def _fresh_status(
    *,
    session: date = date(2025, 1, 2),
    as_of: datetime = datetime(2025, 1, 3, 0, 0, tzinfo=UTC),
    provider_finalized: bool = True,
    stale: bool = False,
    session_complete: bool = True,
) -> DailyMarketDataStatus:
    return DailyMarketDataStatus(
        adjustment="all",
        session=session,
        as_of=as_of,
        provider_finalized=provider_finalized,
        stale=stale,
        session_complete=session_complete,
    )


def _approved_model_metadata() -> ShadowModelMetadata:
    return ShadowModelMetadata(
        model_id="synthetic-approved-shadow-model",
        experiment_id="synthetic-experiment",
        campaign_id="synthetic-campaign",
        model_artifact_checksum="a" * 64,
        feature_schema="synthetic-shadow-feature-schema-v1",
        label_schema="synthetic-label-schema-v1",
        git_commit_sha="abcdef1234567890",
        source_lineage="synthetic-test-only",
        approval_status="approved",
        approved_for_shadow=True,
    )


def test_phase4_specification_and_release_status_are_documented() -> None:
    spec = (ROOT / "docs/V2_PHASE_04_REAL_TIME_SHADOW_MODE_SPEC.md").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    roadmap = (ROOT / "FUTURE_ROADMAP.md").read_text(encoding="utf-8")
    safety = (ROOT / "docs/SECURITY_AND_SAFETY.md").read_text(encoding="utf-8")
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    combined = "\n".join((spec, readme, roadmap, safety, changelog))
    combined_lower = combined.lower()

    assert "Status: Accepted and released as `v2.0.0-beta.1`" in spec
    assert "Target release identifier: `v2.0.0-beta.1`" in spec
    assert "Released package/runtime version: `2.0.0b1`" in spec
    assert "Public tag: `v2.0.0-beta.1` CREATED" in spec
    assert "Gate B: LOCKED" in spec
    assert "Current released identifier: `v2.0.0-beta.1`" in readme
    assert "V2 Phase 3 model outcome: `NO CANDIDATE PROMOTION`" in readme
    assert "Public `v2.0.0-beta.1` release/tag: created" in readme
    assert "Phase 5 broker paper submission: not authorized" in readme
    assert "Gate A infrastructure entry is authorized" in roadmap
    assert "Gate B model-connected inference is blocked" in roadmap
    assert "python -m spy_market_agent.shadow.cli run-observation" in readme
    assert "python -m spy_market_agent.shadow.cli schedule-preview" in readme
    assert "python -m spy_market_agent.shadow.cli run-due-observation" in readme
    assert "Unattended scheduler or daemon: not authorized" in readme
    assert "shadow.sqlite3" in gitignore
    assert "Protected evaluation: not executed" in readme
    assert "live trading" in combined_lower
    assert "prohibited" in combined_lower or "not approved" in combined_lower
    assert "artifacts/research/*" in gitignore
    assert "!artifacts/research/.gitkeep" in gitignore


def test_package_version_is_beta1_candidate_during_release_preparation() -> None:
    assert spy_market_agent.__version__ == "2.0.0b1"


def test_shadow_metadata_validators_fail_closed_on_unsafe_inputs() -> None:
    with pytest.raises(ValidationError, match="artifact_schema_version"):
        ShadowRunConfiguration(
            artifact_schema_version="wrong-schema",
            configuration_version="phase4-shadow-scaffold-v1",
            provider_finalization_policy_id="synthetic-provider-finalized-v1",
        )

    with pytest.raises(ValidationError, match="configuration_version"):
        ShadowRunConfiguration(
            configuration_version="../unsafe",
            provider_finalization_policy_id="synthetic-provider-finalized-v1",
        )

    with pytest.raises(ValidationError, match="canonical_dataset_checksum"):
        DataSnapshotLineage(
            dataset_id="synthetic-shadow-dataset",
            canonical_dataset_checksum="not-a-checksum",
            provider="synthetic",
            feed="synthetic",
            adjustment="all",
            session=date(2025, 1, 2),
            row_count=1,
        )

    with pytest.raises(ValidationError, match="approved_for_shadow"):
        ShadowModelMetadata(
            model_id="synthetic-approved-shadow-model",
            experiment_id="synthetic-experiment",
            campaign_id="synthetic-campaign",
            model_artifact_checksum="a" * 64,
            feature_schema="synthetic-shadow-feature-schema-v1",
            label_schema="synthetic-label-schema-v1",
            git_commit_sha="abcdef1234567890",
            source_lineage="synthetic-test-only",
            approval_status="pending",
            approved_for_shadow=True,
        )


def test_observation_only_mode_is_permitted_without_model_inference() -> None:
    freshness = evaluate_market_data_freshness(_fresh_status())

    decision = evaluate_shadow_run(_request(), freshness)

    assert decision.run_status == ShadowRunStatus.OBSERVATION_READY
    assert decision.observation_allowed is True
    assert decision.model_inference_allowed is False
    assert decision.admission_status == ModelAdmissionStatus.NOT_REQUIRED_OBSERVATION_ONLY
    assert decision.freshness_status == FreshnessStatus.FRESH
    assert decision.monitoring_status == ShadowHealthStatus.HEALTHY
    assert decision.refusal_reasons == (BLOCKED_NO_APPROVED_MODEL,)


def test_observation_only_request_cannot_carry_model_metadata() -> None:
    with pytest.raises(ValidationError, match="observation-only shadow requests"):
        _request(model_metadata=_approved_model_metadata())


def test_model_connected_inference_is_rejected_without_approval() -> None:
    with pytest.raises(ShadowModelAdmissionError, match=NO_APPROVED_SHADOW_MODEL):
        evaluate_model_admission(ShadowMode.MODEL_CONNECTED, None)

    with pytest.raises(ShadowModelAdmissionError, match=NO_APPROVED_SHADOW_MODEL):
        require_model_admission(None)

    with pytest.raises(ShadowModelAdmissionError, match=BLOCKED_NO_APPROVED_MODEL):
        evaluate_shadow_run(
            _request(mode=ShadowMode.MODEL_CONNECTED),
            evaluate_market_data_freshness(_fresh_status()),
        )


def test_model_connected_runtime_remains_locked_with_self_declared_approved_metadata() -> None:
    metadata = _approved_model_metadata()

    with pytest.raises(ShadowModelAdmissionError, match=BLOCKED_NO_APPROVED_MODEL):
        evaluate_model_admission(ShadowMode.MODEL_CONNECTED, metadata)

    with pytest.raises(ShadowModelAdmissionError, match=BLOCKED_NO_APPROVED_MODEL):
        require_model_admission(metadata)


def test_synthetic_approved_metadata_can_pass_structural_validation_only() -> None:
    metadata = _approved_model_metadata()

    validated = validate_shadow_model_metadata(metadata)

    assert validated == metadata
    with pytest.raises(ShadowModelAdmissionError, match=BLOCKED_NO_APPROVED_MODEL):
        evaluate_model_admission(ShadowMode.MODEL_CONNECTED, validated)


def test_daily_spy_xnys_policy_rejects_unsupported_symbol_timeframe_or_calendar() -> None:
    with pytest.raises(ValidationError, match="symbol"):
        DailyMarketDataStatus(
            symbol="AAPL",
            adjustment="all",
            session=date(2025, 1, 2),
            as_of=datetime(2025, 1, 3, 0, 0, tzinfo=UTC),
            provider_finalized=True,
        )

    with pytest.raises(ValidationError, match="timeframe"):
        DailyMarketDataStatus(
            timeframe="1Min",
            adjustment="all",
            session=date(2025, 1, 2),
            as_of=datetime(2025, 1, 3, 0, 0, tzinfo=UTC),
            provider_finalized=True,
        )

    with pytest.raises(ValidationError, match="exchange_calendar"):
        DailyMarketDataStatus(
            exchange_calendar="NASDAQ",
            adjustment="all",
            session=date(2025, 1, 2),
            as_of=datetime(2025, 1, 3, 0, 0, tzinfo=UTC),
            provider_finalized=True,
        )


def test_non_xnys_incomplete_and_stale_sessions_are_blocked() -> None:
    non_session = evaluate_market_data_freshness(_fresh_status(session=date(2025, 1, 4)))
    incomplete = evaluate_market_data_freshness(
        _fresh_status(
            as_of=datetime(2025, 1, 2, 12, 0, tzinfo=UTC),
            session_complete=False,
        )
    )
    stale = evaluate_market_data_freshness(_fresh_status(stale=True))

    assert non_session.eligible is False
    assert "not_xnys_session" in non_session.reasons
    assert incomplete.eligible is False
    assert "incomplete_session" in incomplete.reasons
    assert stale.eligible is False
    assert stale.reasons == ("stale_data",)


def test_freshness_reports_all_blocking_data_quality_reasons() -> None:
    decision = evaluate_market_data_freshness(
        DailyMarketDataStatus(
            adjustment="split-only",
            session=date(2025, 1, 2),
            as_of=datetime(2025, 1, 3, 0, 0, tzinfo=UTC),
            provider_finalized=False,
            expected_session_present=False,
            duplicate_sessions_detected=True,
            out_of_order_sessions_detected=True,
            ohlcv_valid=False,
        )
    )

    assert decision.eligible is False
    assert decision.status == FreshnessStatus.BLOCKED
    assert decision.health_status == ShadowHealthStatus.BLOCKED
    assert decision.reasons == (
        "unsupported_adjustment",
        "missing_session",
        "duplicate_session",
        "out_of_order_sessions",
        "invalid_ohlcv",
        "provider_not_finalized",
    )


def test_freshness_and_schedule_fail_closed_on_calendar_uncertainty() -> None:
    freshness = evaluate_market_data_freshness(
        _fresh_status(),
        calendar=_CalendarUncertainty(),
    )
    schedule = evaluate_shadow_schedule(
        ShadowScheduleInput(
            target_session=date(2025, 1, 2),
            as_of=datetime(2025, 1, 3, 0, 0, tzinfo=UTC),
            provider_finalized=True,
        ),
        calendar=_CalendarUncertainty(),
    )

    assert freshness.reasons == ("calendar_uncertainty", "not_xnys_session", "incomplete_session")
    assert schedule.refusal_reasons == ("calendar_uncertainty", "not_xnys_session")


def test_schedule_policy_reports_session_finalization_and_duplicate_reasons() -> None:
    complete = evaluate_shadow_schedule(
        ShadowScheduleInput(
            target_session=date(2025, 1, 2),
            as_of=datetime(2025, 1, 3, 0, 0, tzinfo=UTC),
            provider_finalized=True,
        )
    )
    duplicate = evaluate_shadow_schedule(
        ShadowScheduleInput(
            target_session=date(2025, 1, 2),
            as_of=datetime(2025, 1, 3, 0, 0, tzinfo=UTC),
            provider_finalized=True,
            already_processed=True,
        )
    )
    not_finalized = evaluate_shadow_schedule(
        ShadowScheduleInput(
            target_session=date(2025, 1, 2),
            as_of=datetime(2025, 1, 3, 0, 0, tzinfo=UTC),
            provider_finalized=False,
        )
    )

    assert complete.eligible is True
    assert duplicate.eligible is False
    assert duplicate.refusal_reasons == ("duplicate_run",)
    assert not_finalized.eligible is False
    assert not_finalized.refusal_reasons == ("provider_not_finalized",)


def test_schedule_policy_reports_incomplete_session_from_calendar() -> None:
    decision = evaluate_shadow_schedule(
        ShadowScheduleInput(
            target_session=date(2025, 1, 2),
            as_of=datetime(2025, 1, 2, 12, 0, tzinfo=UTC),
            provider_finalized=True,
        ),
        calendar=_IncompleteSessionCalendar(),
    )

    assert decision.eligible is False
    assert decision.refusal_reasons == ("session_not_complete",)


def test_shadow_run_identity_is_deterministic_and_duplicate_runs_fail_closed() -> None:
    request = _request()
    same_request = _request()
    model_request = _request(
        mode=ShadowMode.MODEL_CONNECTED,
        model_metadata=_approved_model_metadata(),
    )

    run_id = shadow_run_identity(request)

    assert run_id == shadow_run_identity(same_request)
    assert run_id != shadow_run_identity(model_request)
    with pytest.raises(DuplicateShadowRunError, match="duplicate_run"):
        evaluate_shadow_run(
            request,
            evaluate_market_data_freshness(_fresh_status()),
            existing_run_ids=(run_id,),
        )


def test_observation_policy_wrapper_blocks_when_freshness_fails() -> None:
    decision = evaluate_observation_only_run(
        _request(),
        _fresh_status(stale=True),
    )

    assert decision.run_status == ShadowRunStatus.BLOCKED
    assert decision.observation_allowed is False
    assert decision.model_inference_allowed is False
    assert decision.refusal_reasons == ("stale_data", BLOCKED_NO_APPROVED_MODEL)


def test_observation_policy_wrapper_rejects_model_connected_requests() -> None:
    request = _request(
        mode=ShadowMode.MODEL_CONNECTED,
        model_metadata=_approved_model_metadata(),
    )

    with pytest.raises(ShadowPolicyError, match="observation_only_mode_required"):
        evaluate_observation_only_run(request, _fresh_status())


def test_model_connected_synthetic_request_remains_blocked_with_approved_metadata() -> None:
    request = _request(
        mode=ShadowMode.MODEL_CONNECTED,
        model_metadata=_approved_model_metadata(),
    )

    with pytest.raises(ShadowModelAdmissionError, match=BLOCKED_NO_APPROVED_MODEL):
        evaluate_shadow_run(request, evaluate_market_data_freshness(_fresh_status()))


def test_no_runtime_path_returns_model_inference_ready() -> None:
    runtime_decisions = (
        evaluate_shadow_run(_request(), evaluate_market_data_freshness(_fresh_status())),
        evaluate_observation_only_run(_request(), _fresh_status()),
    )

    for decision in runtime_decisions:
        assert decision.run_status != ShadowRunStatus.MODEL_INFERENCE_READY

    with pytest.raises(ShadowModelAdmissionError, match=BLOCKED_NO_APPROVED_MODEL):
        evaluate_shadow_run(
            _request(
                mode=ShadowMode.MODEL_CONNECTED,
                model_metadata=_approved_model_metadata(),
            ),
            evaluate_market_data_freshness(_fresh_status()),
        )


def test_model_connected_request_rejects_feature_schema_mismatch() -> None:
    metadata = _approved_model_metadata().model_copy(update={"feature_schema": "other-schema"})

    with pytest.raises(ValidationError, match=r"model_metadata\.feature_schema"):
        _request(mode=ShadowMode.MODEL_CONNECTED, model_metadata=metadata)


def test_contradictory_shadow_run_decision_states_are_rejected() -> None:
    with pytest.raises(ValidationError, match="MODEL_INFERENCE_READY"):
        ShadowRunDecision(
            shadow_run_id=shadow_run_identity(_request()),
            mode=ShadowMode.OBSERVATION_ONLY_NO_MODEL,
            run_status=ShadowRunStatus.MODEL_INFERENCE_READY,
            observation_allowed=True,
            model_inference_allowed=True,
            admission_status=ModelAdmissionStatus.APPROVED_FOR_SHADOW,
            freshness_status=FreshnessStatus.FRESH,
            monitoring_status=ShadowHealthStatus.HEALTHY,
            refusal_reasons=(),
        )
    with pytest.raises(ValidationError, match="OBSERVATION_READY"):
        ShadowRunDecision(
            shadow_run_id=shadow_run_identity(_request()),
            mode=ShadowMode.OBSERVATION_ONLY_NO_MODEL,
            run_status=ShadowRunStatus.OBSERVATION_READY,
            observation_allowed=True,
            model_inference_allowed=True,
            admission_status=ModelAdmissionStatus.NOT_REQUIRED_OBSERVATION_ONLY,
            freshness_status=FreshnessStatus.FRESH,
            monitoring_status=ShadowHealthStatus.HEALTHY,
            refusal_reasons=(BLOCKED_NO_APPROVED_MODEL,),
        )
    with pytest.raises(ValidationError, match="BLOCKED"):
        ShadowRunDecision(
            shadow_run_id=shadow_run_identity(_request()),
            mode=ShadowMode.OBSERVATION_ONLY_NO_MODEL,
            run_status=ShadowRunStatus.BLOCKED,
            observation_allowed=False,
            model_inference_allowed=True,
            admission_status=ModelAdmissionStatus.NOT_REQUIRED_OBSERVATION_ONLY,
            freshness_status=FreshnessStatus.BLOCKED,
            monitoring_status=ShadowHealthStatus.BLOCKED,
            refusal_reasons=(BLOCKED_NO_APPROVED_MODEL,),
        )


def test_monitoring_state_uses_most_severe_event_status() -> None:
    healthy = build_monitoring_state(())
    degraded = build_monitoring_state(
        (
            monitoring_event(
                "stale_data",
                "stale data warning",
                ShadowHealthStatus.DEGRADED,
            ),
        )
    )
    blocked = build_monitoring_state(
        (
            ShadowMonitoringEvent(
                code="model_not_approved",
                message="no approved model",
                status=ShadowHealthStatus.BLOCKED,
            ),
            monitoring_event(
                "stale_data",
                "stale data warning",
                ShadowHealthStatus.DEGRADED,
            ),
        )
    )

    assert healthy.status == ShadowHealthStatus.HEALTHY
    assert degraded.status == ShadowHealthStatus.DEGRADED
    assert blocked.status == ShadowHealthStatus.BLOCKED
    with pytest.raises(ValidationError, match="message"):
        monitoring_event("bad_event", "", ShadowHealthStatus.BLOCKED)


def test_observation_only_shadow_proposal_rejects_model_outputs() -> None:
    valid_observation_proposal = ShadowProposal(
        shadow_run_id=shadow_run_identity(_request()),
        signal_session=date(2025, 1, 2),
        generated_at=datetime(2025, 1, 3, 0, 0, tzinfo=UTC),
        mode=ShadowMode.OBSERVATION_ONLY_NO_MODEL,
        feature_schema="synthetic-shadow-feature-schema-v1",
        data_lineage=_lineage(),
        admission_status=ModelAdmissionStatus.NOT_REQUIRED_OBSERVATION_ONLY,
        freshness_status=FreshnessStatus.FRESH,
        monitoring_status=ShadowHealthStatus.HEALTHY,
        proposal_status=ProposalStatus.NOT_GENERATED_OBSERVATION_ONLY,
    )

    assert valid_observation_proposal.model_id is None
    assert valid_observation_proposal.predicted_probability is None
    with pytest.raises(ValidationError, match="observation-only proposals"):
        ShadowProposal(
            shadow_run_id=shadow_run_identity(_request()),
            signal_session=date(2025, 1, 2),
            generated_at=datetime(2025, 1, 3, 0, 0, tzinfo=UTC),
            mode=ShadowMode.OBSERVATION_ONLY_NO_MODEL,
            model_id="synthetic-approved-shadow-model",
            model_checksum="a" * 64,
            feature_schema="synthetic-shadow-feature-schema-v1",
            data_lineage=_lineage(),
            predicted_probability=0.51,
            hypothetical_target_state=HypotheticalTargetState.LONG,
            admission_status=ModelAdmissionStatus.APPROVED_FOR_SHADOW,
            freshness_status=FreshnessStatus.FRESH,
            monitoring_status=ShadowHealthStatus.HEALTHY,
            proposal_status=ProposalStatus.SCAFFOLDED_NOT_EXECUTABLE,
        )


def test_shadow_proposal_is_non_executable_and_validates_probability_and_checksum() -> None:
    proposal = ShadowProposal(
        shadow_run_id=shadow_run_identity(_request()),
        signal_session=date(2025, 1, 2),
        generated_at=datetime(2025, 1, 3, 0, 0, tzinfo=UTC),
        mode=ShadowMode.MODEL_CONNECTED,
        model_id="synthetic-approved-shadow-model",
        model_checksum="a" * 64,
        feature_schema="synthetic-shadow-feature-schema-v1",
        data_lineage=_lineage(),
        predicted_probability=0.51,
        hypothetical_target_state=HypotheticalTargetState.LONG,
        admission_status=ModelAdmissionStatus.APPROVED_FOR_SHADOW,
        freshness_status=FreshnessStatus.FRESH,
        monitoring_status=ShadowHealthStatus.HEALTHY,
        proposal_status=ProposalStatus.SCAFFOLDED_NOT_EXECUTABLE,
    )

    assert proposal.hypothetical_target_state == HypotheticalTargetState.LONG
    assert not hasattr(proposal, "submit")
    assert not hasattr(proposal, "cancel")
    with pytest.raises(ValidationError, match="predicted_probability"):
        ShadowProposal(
            shadow_run_id=shadow_run_identity(_request()),
            signal_session=date(2025, 1, 2),
            generated_at=datetime(2025, 1, 3, 0, 0, tzinfo=UTC),
            mode=ShadowMode.MODEL_CONNECTED,
            model_id="synthetic-approved-shadow-model",
            model_checksum="a" * 64,
            feature_schema="synthetic-shadow-feature-schema-v1",
            data_lineage=_lineage(),
            predicted_probability=2.0,
            hypothetical_target_state=HypotheticalTargetState.LONG,
            admission_status=ModelAdmissionStatus.APPROVED_FOR_SHADOW,
            freshness_status=FreshnessStatus.FRESH,
            monitoring_status=ShadowHealthStatus.HEALTHY,
            proposal_status=ProposalStatus.SCAFFOLDED_NOT_EXECUTABLE,
        )
    with pytest.raises(ValidationError, match="model_checksum"):
        ShadowProposal(
            shadow_run_id=shadow_run_identity(_request()),
            signal_session=date(2025, 1, 2),
            generated_at=datetime(2025, 1, 3, 0, 0, tzinfo=UTC),
            mode=ShadowMode.MODEL_CONNECTED,
            model_id="synthetic-approved-shadow-model",
            model_checksum="not-a-checksum",
            feature_schema="synthetic-shadow-feature-schema-v1",
            data_lineage=_lineage(),
            predicted_probability=0.51,
            hypothetical_target_state=HypotheticalTargetState.CASH,
            admission_status=ModelAdmissionStatus.APPROVED_FOR_SHADOW,
            freshness_status=FreshnessStatus.FRESH,
            monitoring_status=ShadowHealthStatus.HEALTHY,
            proposal_status=ProposalStatus.SCAFFOLDED_NOT_EXECUTABLE,
        )


def test_shadow_package_static_import_boundary_excludes_broker_and_execution_modules() -> None:
    shadow_files = sorted((ROOT / "src/spy_market_agent/shadow").glob("*.py"))
    assert shadow_files

    forbidden_fragments = (
        "alpaca.trading",
        "TradingClient",
        "spy_market_agent.execution",
        "submit_order",
        "submit_approved_order",
        "AlpacaPaperBroker",
        "APScheduler",
        "Celery",
        "RQ",
        "time.sleep",
        "while True",
    )
    for path in shadow_files:
        text = path.read_text(encoding="utf-8")
        for fragment in forbidden_fragments:
            assert fragment not in text, f"{path}: {fragment}"


def test_shadow_import_has_no_broker_or_execution_side_effects() -> None:
    script = """
import sys
import spy_market_agent.shadow

for name in (
    "alpaca.trading",
    "spy_market_agent.execution.alpaca_paper",
    "spy_market_agent.execution.service",
):
    assert name not in sys.modules, name
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout == ""
    assert result.stderr == ""
