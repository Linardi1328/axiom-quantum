from __future__ import annotations

import sqlite3
import subprocess
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from spy_market_agent.market_data.errors import ChecksumMismatch, ManifestValidationFailure
from spy_market_agent.market_data.manifest import (
    finalized_manifest_with_checksum,
    manifest_json_bytes,
)
from spy_market_agent.persistence.serialization import datetime_to_text
from spy_market_agent.shadow import (
    SHADOW_OPERATION_CONFIGURATION_VERSION,
    FreshnessStatus,
    ModelAdmissionStatus,
    ShadowAlertRecord,
    ShadowHealthEventRecord,
    ShadowHealthStatus,
    ShadowMode,
    ShadowOperationalRunStatus,
    ShadowRunRecord,
    ShadowSQLiteRepository,
    build_operational_snapshot,
    initialize_shadow_database,
    resolve_latest_completed_target_session,
    run_due_observation,
    run_observation,
    shadow_run_identity,
)
from spy_market_agent.shadow.schedule_ops import (
    ScheduledObservationAction,
    ScheduledObservationHistoryStatus,
    ScheduledObservationState,
    evaluate_scheduled_observation,
)
from unit.phase4_shadow_helpers import SyntheticPhase1Dataset, write_synthetic_phase1_dataset

ROOT = Path(__file__).resolve().parents[2]
FIXED_CREATED = datetime(2025, 1, 8, 0, 1, tzinfo=UTC)
FIXED_COMPLETED = datetime(2025, 1, 8, 0, 2, tzinfo=UTC)


class FixedClock:
    def __init__(self) -> None:
        self._values = [FIXED_CREATED, FIXED_COMPLETED, FIXED_COMPLETED]

    def __call__(self) -> datetime:
        return self._values.pop(0)


def _run_count(database_path: Path) -> int:
    with sqlite3.connect(database_path) as connection:
        return int(connection.execute("SELECT COUNT(*) FROM shadow_runs").fetchone()[0])


def _event_count(database_path: Path, event_code: str) -> int:
    with sqlite3.connect(database_path) as connection:
        return int(
            connection.execute(
                "SELECT COUNT(*) FROM shadow_health_events WHERE event_code = ?",
                (event_code,),
            ).fetchone()[0]
        )


def _run_observation_for_dataset(
    dataset: SyntheticPhase1Dataset,
    *,
    shadow_db: Path,
    repository_root: Path,
    session: date,
    as_of: datetime,
    provider_finalized: bool = True,
) -> str:
    result = run_observation(
        manifest_path=dataset.manifest_path,
        data_root=dataset.data_root,
        shadow_db=shadow_db,
        session=session,
        as_of=as_of,
        provider_finalized=provider_finalized,
        provider_finalization_policy_id="synthetic-provider-finalized-v1",
        repository_root=repository_root,
        clock=FixedClock(),
    )
    return result.shadow_run_id


def _reserve_current_run(
    dataset: SyntheticPhase1Dataset,
    *,
    shadow_db: Path,
    session: date,
    as_of: datetime,
) -> str:
    snapshot = build_operational_snapshot(
        manifest=dataset.manifest,
        canonical_bars=dataset.canonical_bars,
        session=session,
        as_of=as_of,
        provider_finalized=True,
        provider_finalization_policy_id="synthetic-provider-finalized-v1",
    )
    run_id = shadow_run_identity(snapshot.request)
    repository = initialize_shadow_database(shadow_db)
    repository.reserve_run(
        ShadowRunRecord(
            shadow_run_id=run_id,
            configuration_version=SHADOW_OPERATION_CONFIGURATION_VERSION,
            mode=ShadowMode.OBSERVATION_ONLY_NO_MODEL,
            symbol="SPY",
            timeframe="1Day",
            signal_session=session.isoformat(),
            as_of=datetime_to_text(as_of),
            parent_dataset_id=dataset.manifest.dataset_id,
            canonical_dataset_checksum=dataset.manifest.canonical_content_checksum,
            provider_finalization_policy_id="synthetic-provider-finalized-v1",
            run_status=ShadowOperationalRunStatus.RESERVED,
            freshness_status=FreshnessStatus.BLOCKED,
            monitoring_status=ShadowHealthStatus.BLOCKED,
            model_gate_status=ModelAdmissionStatus.BLOCKED_NO_APPROVED_MODEL,
            created_at=datetime_to_text(FIXED_CREATED),
        )
    )
    return run_id


def _seed_failed_run(
    dataset: SyntheticPhase1Dataset,
    *,
    shadow_db: Path,
    session: date,
    as_of: datetime,
) -> str:
    run_id = _reserve_current_run(dataset, shadow_db=shadow_db, session=session, as_of=as_of)
    repository = ShadowSQLiteRepository(shadow_db)
    repository.mark_failed(
        shadow_run_id=run_id,
        completed_at=FIXED_COMPLETED,
        event=ShadowHealthEventRecord(
            shadow_run_id=run_id,
            event_code="unexpected_configuration",
            status=ShadowHealthStatus.BLOCKED,
            message="synthetic failed prior observation",
            event_timestamp=datetime_to_text(FIXED_COMPLETED),
        ),
        alert=ShadowAlertRecord(
            shadow_run_id=run_id,
            alert_code="unexpected_configuration",
            status=ShadowHealthStatus.BLOCKED,
            message="synthetic failed prior observation",
            created_at=datetime_to_text(FIXED_COMPLETED),
        ),
    )
    return run_id


@pytest.mark.parametrize(
    ("as_of", "expected"),
    [
        (datetime(2025, 1, 3, 22, 0, tzinfo=UTC), date(2025, 1, 3)),
        (datetime(2025, 1, 4, 12, 0, tzinfo=UTC), date(2025, 1, 3)),
        (datetime(2025, 1, 5, 12, 0, tzinfo=UTC), date(2025, 1, 3)),
        (datetime(2025, 1, 6, 20, 0, tzinfo=UTC), date(2025, 1, 3)),
        (datetime(2025, 1, 6, 22, 0, tzinfo=UTC), date(2025, 1, 6)),
        (datetime(2025, 11, 27, 22, 0, tzinfo=UTC), date(2025, 11, 26)),
        (datetime(2025, 11, 28, 18, 30, tzinfo=UTC), date(2025, 11, 28)),
        (datetime(2025, 11, 28, 17, 30, tzinfo=UTC), date(2025, 11, 26)),
    ],
)
def test_latest_completed_target_session_uses_xnys_calendar(
    as_of: datetime,
    expected: date,
) -> None:
    assert resolve_latest_completed_target_session(as_of=as_of) == expected


def test_target_resolution_rejects_naive_non_utc_and_out_of_range_timestamps() -> None:
    with pytest.raises(Exception, match="invalid_as_of"):
        resolve_latest_completed_target_session(as_of=datetime(2025, 1, 6, 22, 0))
    with pytest.raises(Exception, match="invalid_as_of"):
        resolve_latest_completed_target_session(
            as_of=datetime(2025, 1, 6, 17, 0, tzinfo=ZoneInfo("America/New_York"))
        )
    with pytest.raises(Exception, match="calendar_uncertainty"):
        resolve_latest_completed_target_session(as_of=datetime(2051, 1, 3, 22, 0, tzinfo=UTC))


def test_fresh_local_manifest_matching_due_target_is_eligible(tmp_path: Path) -> None:
    dataset = write_synthetic_phase1_dataset(tmp_path)

    decision = evaluate_scheduled_observation(
        manifest_path=dataset.manifest_path,
        data_root=dataset.data_root,
        shadow_db=tmp_path / "missing-shadow.sqlite3",
        as_of=datetime(2025, 1, 3, 0, 0, tzinfo=UTC),
        provider_finalized=True,
        provider_finalization_policy_id="synthetic-provider-finalized-v1",
        repository_root=tmp_path,
    )

    assert decision.target_session == date(2025, 1, 2)
    assert decision.latest_canonical_session == date(2025, 1, 2)
    assert decision.eligible is True
    assert decision.state == ScheduledObservationState.DUE
    assert decision.history_status == ScheduledObservationHistoryStatus.NO_PRIOR_HISTORY
    assert not (tmp_path / "missing-shadow.sqlite3").exists()


def test_schedule_blocks_stale_data_data_ahead_and_unfinalized_provider(
    tmp_path: Path,
) -> None:
    stale_dataset = write_synthetic_phase1_dataset(tmp_path / "stale")
    stale = evaluate_scheduled_observation(
        manifest_path=stale_dataset.manifest_path,
        data_root=stale_dataset.data_root,
        shadow_db=tmp_path / "stale.sqlite3",
        as_of=datetime(2025, 1, 4, 0, 0, tzinfo=UTC),
        provider_finalized=True,
        provider_finalization_policy_id="synthetic-provider-finalized-v1",
        repository_root=tmp_path / "stale",
    )
    ahead_dataset = write_synthetic_phase1_dataset(
        tmp_path / "ahead",
        start_session=date(2025, 1, 2),
        end_session=date(2025, 1, 3),
        retrieval_timestamp=datetime(2025, 1, 4, 0, 0, tzinfo=UTC),
    )
    ahead = evaluate_scheduled_observation(
        manifest_path=ahead_dataset.manifest_path,
        data_root=ahead_dataset.data_root,
        shadow_db=tmp_path / "ahead.sqlite3",
        as_of=datetime(2025, 1, 3, 0, 0, tzinfo=UTC),
        provider_finalized=True,
        provider_finalization_policy_id="synthetic-provider-finalized-v1",
        repository_root=tmp_path / "ahead",
    )
    not_finalized = evaluate_scheduled_observation(
        manifest_path=stale_dataset.manifest_path,
        data_root=stale_dataset.data_root,
        shadow_db=tmp_path / "unfinalized.sqlite3",
        as_of=datetime(2025, 1, 3, 0, 0, tzinfo=UTC),
        provider_finalized=False,
        provider_finalization_policy_id="synthetic-provider-finalized-v1",
        repository_root=tmp_path / "stale",
    )

    assert stale.eligible is False
    assert stale.refusal_reasons == ("stale_data",)
    assert ahead.eligible is False
    assert ahead.refusal_reasons == ("data_ahead_of_completed_session",)
    assert not_finalized.eligible is False
    assert "provider_not_finalized" in not_finalized.refusal_reasons


def test_schedule_rejects_invalid_phase1_lineage_and_scope(tmp_path: Path) -> None:
    dataset = write_synthetic_phase1_dataset(tmp_path)
    canonical_path = tmp_path / dataset.manifest.generated_file_locations.canonical_path
    canonical_path.write_text(
        canonical_path.read_text(encoding="utf-8").replace("101", "199", 1),
        encoding="utf-8",
    )
    with pytest.raises(ChecksumMismatch):
        evaluate_scheduled_observation(
            manifest_path=dataset.manifest_path,
            data_root=dataset.data_root,
            shadow_db=tmp_path / "shadow.sqlite3",
            as_of=datetime(2025, 1, 3, 0, 0, tzinfo=UTC),
            provider_finalized=True,
            provider_finalization_policy_id="synthetic-provider-finalized-v1",
            repository_root=tmp_path,
        )

    for field_name, field_value in (("symbol", "AAPL"), ("timeframe", "1Min")):
        root = tmp_path / field_name
        bad_dataset = write_synthetic_phase1_dataset(root)
        bad_manifest = finalized_manifest_with_checksum(
            bad_dataset.manifest.model_copy(
                update={field_name: field_value, "manifest_artifact_checksum": None}
            )
        )
        bad_dataset.manifest_path.write_bytes(manifest_json_bytes(bad_manifest))
        with pytest.raises(ManifestValidationFailure):
            evaluate_scheduled_observation(
                manifest_path=bad_dataset.manifest_path,
                data_root=bad_dataset.data_root,
                shadow_db=root / "shadow.sqlite3",
                as_of=datetime(2025, 1, 3, 0, 0, tzinfo=UTC),
                provider_finalized=True,
                provider_finalization_policy_id="synthetic-provider-finalized-v1",
                repository_root=root,
            )

    raw_dataset = write_synthetic_phase1_dataset(tmp_path / "raw", adjustment_mode="raw")
    with pytest.raises(ManifestValidationFailure, match="adjustment"):
        evaluate_scheduled_observation(
            manifest_path=raw_dataset.manifest_path,
            data_root=raw_dataset.data_root,
            shadow_db=tmp_path / "raw.sqlite3",
            as_of=datetime(2025, 1, 3, 0, 0, tzinfo=UTC),
            provider_finalized=True,
            provider_finalization_policy_id="synthetic-provider-finalized-v1",
            repository_root=tmp_path / "raw",
        )


@pytest.mark.parametrize(
    "run_status",
    [
        ShadowOperationalRunStatus.COMPLETED,
        ShadowOperationalRunStatus.BLOCKED,
        ShadowOperationalRunStatus.FAILED,
    ],
)
def test_current_terminal_run_is_already_processed_without_duplicate_audit(
    tmp_path: Path,
    run_status: ShadowOperationalRunStatus,
) -> None:
    dataset = write_synthetic_phase1_dataset(tmp_path)
    shadow_db = tmp_path / "shadow.sqlite3"
    if run_status == ShadowOperationalRunStatus.FAILED:
        _seed_failed_run(
            dataset,
            shadow_db=shadow_db,
            session=date(2025, 1, 2),
            as_of=datetime(2025, 1, 3, 0, 0, tzinfo=UTC),
        )
    else:
        _run_observation_for_dataset(
            dataset,
            shadow_db=shadow_db,
            repository_root=tmp_path,
            session=date(2025, 1, 2),
            as_of=datetime(2025, 1, 3, 0, 0, tzinfo=UTC),
            provider_finalized=(run_status == ShadowOperationalRunStatus.COMPLETED),
        )
    duplicate_events_before = _event_count(shadow_db, "duplicate_run")

    decision = evaluate_scheduled_observation(
        manifest_path=dataset.manifest_path,
        data_root=dataset.data_root,
        shadow_db=shadow_db,
        as_of=datetime(2025, 1, 3, 0, 0, tzinfo=UTC),
        provider_finalized=True,
        provider_finalization_policy_id="synthetic-provider-finalized-v1",
        repository_root=tmp_path,
    )

    assert decision.state == ScheduledObservationState.ALREADY_PROCESSED
    assert decision.existing_run_status == run_status
    assert decision.eligible is False
    assert _run_count(shadow_db) == 1
    assert _event_count(shadow_db, "duplicate_run") == duplicate_events_before


def test_current_reserved_run_requires_recovery_without_overwrite(tmp_path: Path) -> None:
    dataset = write_synthetic_phase1_dataset(tmp_path)
    shadow_db = tmp_path / "shadow.sqlite3"
    run_id = _reserve_current_run(
        dataset,
        shadow_db=shadow_db,
        session=date(2025, 1, 2),
        as_of=datetime(2025, 1, 3, 0, 0, tzinfo=UTC),
    )

    decision = evaluate_scheduled_observation(
        manifest_path=dataset.manifest_path,
        data_root=dataset.data_root,
        shadow_db=shadow_db,
        as_of=datetime(2025, 1, 3, 0, 0, tzinfo=UTC),
        provider_finalized=True,
        provider_finalization_policy_id="synthetic-provider-finalized-v1",
        repository_root=tmp_path,
    )
    result = run_due_observation(
        manifest_path=dataset.manifest_path,
        data_root=dataset.data_root,
        shadow_db=shadow_db,
        as_of=datetime(2025, 1, 3, 0, 0, tzinfo=UTC),
        provider_finalized=True,
        provider_finalization_policy_id="synthetic-provider-finalized-v1",
        repository_root=tmp_path,
    )
    stored = ShadowSQLiteRepository(shadow_db).get_run(run_id)

    assert decision.state == ScheduledObservationState.RECOVERY_REQUIRED
    assert result.action == ScheduledObservationAction.RECOVERY_REQUIRED
    assert stored.run.run_status == ShadowOperationalRunStatus.RESERVED
    assert stored.input_snapshot is None
    assert _run_count(shadow_db) == 1


def test_missed_observation_detection_uses_xnys_sessions_only(tmp_path: Path) -> None:
    shadow_db = tmp_path / "shadow.sqlite3"
    friday_dataset = write_synthetic_phase1_dataset(
        tmp_path / "friday",
        start_session=date(2025, 1, 3),
        end_session=date(2025, 1, 3),
        retrieval_timestamp=datetime(2025, 1, 4, 0, 0, tzinfo=UTC),
    )
    _run_observation_for_dataset(
        friday_dataset,
        shadow_db=shadow_db,
        repository_root=tmp_path / "friday",
        session=date(2025, 1, 3),
        as_of=datetime(2025, 1, 4, 0, 0, tzinfo=UTC),
    )
    monday_dataset = write_synthetic_phase1_dataset(
        tmp_path / "monday",
        start_session=date(2025, 1, 3),
        end_session=date(2025, 1, 6),
        retrieval_timestamp=datetime(2025, 1, 7, 0, 0, tzinfo=UTC),
    )
    monday = evaluate_scheduled_observation(
        manifest_path=monday_dataset.manifest_path,
        data_root=monday_dataset.data_root,
        shadow_db=shadow_db,
        as_of=datetime(2025, 1, 6, 22, 0, tzinfo=UTC),
        provider_finalized=True,
        provider_finalization_policy_id="synthetic-provider-finalized-v1",
        repository_root=tmp_path / "monday",
    )
    tuesday_dataset = write_synthetic_phase1_dataset(
        tmp_path / "tuesday",
        start_session=date(2025, 1, 3),
        end_session=date(2025, 1, 7),
        retrieval_timestamp=datetime(2025, 1, 8, 0, 0, tzinfo=UTC),
    )
    tuesday = evaluate_scheduled_observation(
        manifest_path=tuesday_dataset.manifest_path,
        data_root=tuesday_dataset.data_root,
        shadow_db=shadow_db,
        as_of=datetime(2025, 1, 8, 0, 0, tzinfo=UTC),
        provider_finalized=True,
        provider_finalization_policy_id="synthetic-provider-finalized-v1",
        repository_root=tmp_path / "tuesday",
    )
    wednesday_dataset = write_synthetic_phase1_dataset(
        tmp_path / "wednesday",
        start_session=date(2025, 1, 3),
        end_session=date(2025, 1, 8),
        retrieval_timestamp=datetime(2025, 1, 9, 0, 0, tzinfo=UTC),
    )
    wednesday = evaluate_scheduled_observation(
        manifest_path=wednesday_dataset.manifest_path,
        data_root=wednesday_dataset.data_root,
        shadow_db=shadow_db,
        as_of=datetime(2025, 1, 9, 0, 0, tzinfo=UTC),
        provider_finalized=True,
        provider_finalization_policy_id="synthetic-provider-finalized-v1",
        repository_root=tmp_path / "wednesday",
    )

    assert monday.missed_observation_sessions == ()
    assert tuesday.missed_observation_sessions == (date(2025, 1, 6),)
    assert tuesday.eligible is True
    assert tuesday.status == ShadowHealthStatus.DEGRADED
    assert wednesday.missed_observation_sessions == (date(2025, 1, 6), date(2025, 1, 7))


def test_missed_history_is_not_backfilled_and_current_due_can_run_degraded(
    tmp_path: Path,
) -> None:
    shadow_db = tmp_path / "shadow.sqlite3"
    prior = write_synthetic_phase1_dataset(
        tmp_path / "prior",
        start_session=date(2025, 1, 3),
        end_session=date(2025, 1, 3),
        retrieval_timestamp=datetime(2025, 1, 4, 0, 0, tzinfo=UTC),
    )
    _run_observation_for_dataset(
        prior,
        shadow_db=shadow_db,
        repository_root=tmp_path / "prior",
        session=date(2025, 1, 3),
        as_of=datetime(2025, 1, 4, 0, 0, tzinfo=UTC),
    )
    current = write_synthetic_phase1_dataset(
        tmp_path / "current",
        start_session=date(2025, 1, 3),
        end_session=date(2025, 1, 7),
        retrieval_timestamp=datetime(2025, 1, 8, 0, 0, tzinfo=UTC),
    )

    result = run_due_observation(
        manifest_path=current.manifest_path,
        data_root=current.data_root,
        shadow_db=shadow_db,
        as_of=datetime(2025, 1, 8, 0, 0, tzinfo=UTC),
        provider_finalized=True,
        provider_finalization_policy_id="synthetic-provider-finalized-v1",
        repository_root=tmp_path / "current",
        clock=FixedClock(),
    )
    sessions = {
        run.signal_session for run in ShadowSQLiteRepository(shadow_db).list_observation_runs()
    }

    assert result.action == ScheduledObservationAction.RAN_OBSERVATION
    assert result.decision.status == ShadowHealthStatus.DEGRADED
    assert result.decision.missed_observation_sessions == (date(2025, 1, 6),)
    assert sessions == {"2025-01-03", "2025-01-07"}


def test_prior_failed_run_is_visible_in_schedule_decision(tmp_path: Path) -> None:
    shadow_db = tmp_path / "shadow.sqlite3"
    prior = write_synthetic_phase1_dataset(
        tmp_path / "failed-prior",
        start_session=date(2025, 1, 3),
        end_session=date(2025, 1, 3),
        retrieval_timestamp=datetime(2025, 1, 4, 0, 0, tzinfo=UTC),
    )
    _seed_failed_run(
        prior,
        shadow_db=shadow_db,
        session=date(2025, 1, 3),
        as_of=datetime(2025, 1, 4, 0, 0, tzinfo=UTC),
    )
    current = write_synthetic_phase1_dataset(
        tmp_path / "current-after-failed",
        start_session=date(2025, 1, 3),
        end_session=date(2025, 1, 6),
        retrieval_timestamp=datetime(2025, 1, 7, 0, 0, tzinfo=UTC),
    )

    decision = evaluate_scheduled_observation(
        manifest_path=current.manifest_path,
        data_root=current.data_root,
        shadow_db=shadow_db,
        as_of=datetime(2025, 1, 6, 22, 0, tzinfo=UTC),
        provider_finalized=True,
        provider_finalization_policy_id="synthetic-provider-finalized-v1",
        repository_root=tmp_path / "current-after-failed",
    )

    assert decision.latest_prior_observation_session == date(2025, 1, 3)
    assert decision.latest_prior_observation_status == ShadowOperationalRunStatus.FAILED
    assert "prior_failed_observation" in decision.warnings
    assert decision.status == ShadowHealthStatus.DEGRADED


def test_schedule_decision_cross_field_validation_rejects_contradictions() -> None:
    from spy_market_agent.shadow.schedule_ops import ScheduledObservationDecision

    with pytest.raises(ValueError, match="blocking refusal"):
        ScheduledObservationDecision(
            as_of=datetime(2025, 1, 3, 0, 0, tzinfo=UTC),
            target_session=date(2025, 1, 2),
            latest_completed_xnys_session=date(2025, 1, 2),
            latest_canonical_session=date(2025, 1, 2),
            provider_finalized=True,
            provider_finalization_policy_id="synthetic-provider-finalized-v1",
            history_status=ScheduledObservationHistoryStatus.NO_PRIOR_HISTORY,
            eligible=True,
            status=ShadowHealthStatus.HEALTHY,
            state=ScheduledObservationState.DUE,
            refusal_reasons=("stale_data",),
        )
    with pytest.raises(ValueError, match="already-processed"):
        ScheduledObservationDecision(
            as_of=datetime(2025, 1, 3, 0, 0, tzinfo=UTC),
            target_session=date(2025, 1, 2),
            latest_completed_xnys_session=date(2025, 1, 2),
            latest_canonical_session=date(2025, 1, 2),
            provider_finalized=True,
            provider_finalization_policy_id="synthetic-provider-finalized-v1",
            history_status=ScheduledObservationHistoryStatus.HISTORY_AVAILABLE,
            already_processed=True,
            existing_run_status=ShadowOperationalRunStatus.COMPLETED,
            eligible=False,
            status=ShadowHealthStatus.HEALTHY,
            state=ScheduledObservationState.BLOCKED,
        )


def test_schedule_preview_cli_does_not_create_missing_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    dataset = write_synthetic_phase1_dataset(tmp_path)
    monkeypatch.chdir(tmp_path)

    from spy_market_agent.shadow.cli import main

    exit_code = main(
        [
            "schedule-preview",
            "--manifest",
            str(dataset.manifest_path),
            "--data-root",
            str(dataset.data_root),
            "--shadow-db",
            str(tmp_path / "preview.sqlite3"),
            "--as-of",
            "2025-01-03T00:00:00Z",
            "--provider-finalized",
            "--provider-finalization-policy-id",
            "synthetic-provider-finalized-v1",
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "schedule_state=due" in output
    assert "history_status=no_prior_history" in output
    assert not (tmp_path / "preview.sqlite3").exists()


def test_schedule_static_boundary_excludes_execution_credentials_and_loops() -> None:
    schedule_files = (
        ROOT / "src/spy_market_agent/shadow/schedule.py",
        ROOT / "src/spy_market_agent/shadow/schedule_ops.py",
        ROOT / "src/spy_market_agent/shadow/cli.py",
    )
    forbidden_fragments = (
        "alpaca.trading",
        "TradingClient",
        "spy_market_agent.execution",
        "AlpacaPaperBroker",
        "submit_order",
        "submit_approved_order",
        "APCA_API_KEY_ID",
        "APCA_API_SECRET_KEY",
        "ALPACA_MARKET_DATA_API_KEY",
        "ALPACA_MARKET_DATA_SECRET_KEY",
        "APScheduler",
        "Celery",
        "RQ",
        "time.sleep",
        "while True",
    )

    for path in schedule_files:
        text = path.read_text(encoding="utf-8")
        for fragment in forbidden_fragments:
            assert fragment not in text, f"{path}: {fragment}"


def test_schedule_imports_have_no_side_effects_or_forbidden_modules(tmp_path: Path) -> None:
    script = """
import sys
import spy_market_agent.shadow
import spy_market_agent.shadow.schedule
import spy_market_agent.shadow.schedule_ops
import spy_market_agent.shadow.cli

for name in (
    "alpaca.trading",
    "spy_market_agent.execution",
    "spy_market_agent.execution.alpaca_paper",
    "spy_market_agent.execution.service",
):
    assert name not in sys.modules, name
print("clean")
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip() == "clean"
