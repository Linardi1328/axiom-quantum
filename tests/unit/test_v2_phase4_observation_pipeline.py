from __future__ import annotations

import sqlite3
import subprocess
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from spy_market_agent.market_data.errors import ChecksumMismatch, ManifestValidationFailure
from spy_market_agent.persistence.serialization import datetime_to_text
from spy_market_agent.shadow import (
    SHADOW_DB_SCHEMA_VERSION,
    SHADOW_OPERATION_CONFIGURATION_VERSION,
    SHADOW_OPERATION_FEATURE_SCHEMA,
    FreshnessStatus,
    ModelAdmissionStatus,
    ShadowAlertRecord,
    ShadowHealthEventRecord,
    ShadowHealthStatus,
    ShadowInputSnapshotRecord,
    ShadowMode,
    ShadowModelMetadata,
    ShadowObservationResult,
    ShadowOperationalRunStatus,
    ShadowPersistenceError,
    ShadowRecoveryRequiredError,
    ShadowRunRecord,
    ShadowSchemaError,
    ShadowSQLiteRepository,
    build_operational_snapshot,
    evaluate_market_data_freshness,
    initialize_shadow_database,
    run_observation,
    shadow_run_identity,
)
from spy_market_agent.shadow.persistence import ShadowDuplicateRunError
from spy_market_agent.shadow.runner import ShadowOperationalError
from unit.phase4_shadow_helpers import SyntheticPhase1Dataset, write_synthetic_phase1_dataset

ROOT = Path(__file__).resolve().parents[2]
FIXED_CREATED = datetime(2025, 1, 3, 0, 1, tzinfo=UTC)
FIXED_COMPLETED = datetime(2025, 1, 3, 0, 2, tzinfo=UTC)
FIXED_RETRY = datetime(2025, 1, 3, 0, 3, tzinfo=UTC)


class FixedClock:
    def __init__(self) -> None:
        self._values = [FIXED_CREATED, FIXED_COMPLETED, FIXED_COMPLETED]

    def __call__(self) -> datetime:
        return self._values.pop(0)


def _approved_model_metadata() -> ShadowModelMetadata:
    return ShadowModelMetadata(
        model_id="synthetic-approved-shadow-model",
        experiment_id="synthetic-experiment",
        campaign_id="synthetic-campaign",
        model_artifact_checksum="a" * 64,
        feature_schema=SHADOW_OPERATION_FEATURE_SCHEMA,
        label_schema="synthetic-label-schema-v1",
        git_commit_sha="abcdef1234567890",
        source_lineage="synthetic-test-only",
        approval_status="approved",
        approved_for_shadow=True,
    )


def _run_healthy(tmp_path: Path) -> tuple[SyntheticPhase1Dataset, ShadowObservationResult]:
    dataset = write_synthetic_phase1_dataset(tmp_path)
    result = run_observation(
        manifest_path=dataset.manifest_path,
        data_root=dataset.data_root,
        shadow_db=tmp_path / "shadow.sqlite3",
        session=date(2025, 1, 2),
        as_of=datetime(2025, 1, 3, 0, 0, tzinfo=UTC),
        provider_finalized=True,
        provider_finalization_policy_id="synthetic-provider-finalized-v1",
        repository_root=tmp_path,
        clock=FixedClock(),
    )
    return dataset, result


def _run_row_count(database_path: Path, shadow_run_id: str) -> int:
    with sqlite3.connect(database_path) as connection:
        return int(
            connection.execute(
                "SELECT COUNT(*) FROM shadow_runs WHERE shadow_run_id = ?",
                (shadow_run_id,),
            ).fetchone()[0]
        )


def _reserve_synthetic_run(
    *,
    tmp_path: Path,
    dataset: SyntheticPhase1Dataset,
    database_path: Path,
) -> str:
    run = _synthetic_reserved_run_record(dataset)
    repository = initialize_shadow_database(database_path)
    repository.reserve_run(run)
    assert tmp_path.exists()
    return run.shadow_run_id


def _synthetic_reserved_run_record(dataset: SyntheticPhase1Dataset) -> ShadowRunRecord:
    snapshot = build_operational_snapshot(
        manifest=dataset.manifest,
        canonical_bars=dataset.canonical_bars,
        session=date(2025, 1, 2),
        as_of=datetime(2025, 1, 3, 0, 0, tzinfo=UTC),
        provider_finalized=True,
        provider_finalization_policy_id="synthetic-provider-finalized-v1",
    )
    run_id = shadow_run_identity(snapshot.request)
    return ShadowRunRecord(
        shadow_run_id=run_id,
        configuration_version=SHADOW_OPERATION_CONFIGURATION_VERSION,
        mode=ShadowMode.OBSERVATION_ONLY_NO_MODEL,
        symbol="SPY",
        timeframe="1Day",
        signal_session="2025-01-02",
        as_of="2025-01-03T00:00:00Z",
        parent_dataset_id=dataset.manifest.dataset_id,
        canonical_dataset_checksum=dataset.manifest.canonical_content_checksum,
        provider_finalization_policy_id="synthetic-provider-finalized-v1",
        run_status=ShadowOperationalRunStatus.RESERVED,
        freshness_status=FreshnessStatus.BLOCKED,
        monitoring_status=ShadowHealthStatus.BLOCKED,
        model_gate_status=ModelAdmissionStatus.BLOCKED_NO_APPROVED_MODEL,
        created_at=datetime_to_text(FIXED_CREATED),
    )


def _synthetic_snapshot_record(
    *,
    shadow_run_id: str,
    dataset: SyntheticPhase1Dataset,
) -> ShadowInputSnapshotRecord:
    return ShadowInputSnapshotRecord(
        shadow_run_id=shadow_run_id,
        parent_dataset_id=dataset.manifest.dataset_id,
        canonical_dataset_checksum=dataset.manifest.canonical_content_checksum,
        symbol="SPY",
        timeframe="1Day",
        provider=dataset.manifest.provider,
        feed=dataset.manifest.feed,
        adjustment=dataset.manifest.adjustment_mode,
        first_session="2025-01-02",
        latest_session="2025-01-02",
        target_session="2025-01-02",
        row_count=len(dataset.canonical_bars),
        provider_finalization_policy_id="synthetic-provider-finalized-v1",
        manifest_artifact_checksum=dataset.manifest.manifest_artifact_checksum,
        snapshot_created_at=datetime_to_text(FIXED_COMPLETED),
    )


def _audit_event(
    shadow_run_id: str, event_code: str = "unexpected_configuration"
) -> ShadowHealthEventRecord:
    return ShadowHealthEventRecord(
        shadow_run_id=shadow_run_id,
        event_code=event_code,
        status=ShadowHealthStatus.BLOCKED,
        message=f"synthetic {event_code}",
        event_timestamp=datetime_to_text(FIXED_RETRY),
    )


def _audit_alert(
    shadow_run_id: str, alert_code: str = "unexpected_configuration"
) -> ShadowAlertRecord:
    return ShadowAlertRecord(
        shadow_run_id=shadow_run_id,
        alert_code=alert_code,
        status=ShadowHealthStatus.BLOCKED,
        message=f"synthetic {alert_code}",
        created_at=datetime_to_text(FIXED_RETRY),
    )


def test_shadow_database_initializes_with_dedicated_schema(tmp_path: Path) -> None:
    repository = initialize_shadow_database(tmp_path / "shadow.sqlite3")
    repository.initialize()

    with sqlite3.connect(tmp_path / "shadow.sqlite3") as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
            if not str(row[0]).startswith("sqlite_")
        }
        version = connection.execute(
            "SELECT schema_version FROM shadow_schema_metadata WHERE singleton_id = 1"
        ).fetchone()[0]

    assert version == SHADOW_DB_SCHEMA_VERSION
    assert tables == {
        "shadow_schema_metadata",
        "shadow_runs",
        "shadow_input_snapshots",
        "shadow_health_events",
        "shadow_alerts",
    }


def test_shadow_database_rejects_incompatible_or_mixed_schema(tmp_path: Path) -> None:
    incompatible = tmp_path / "incompatible.sqlite3"
    with sqlite3.connect(incompatible) as connection:
        connection.execute(
            "CREATE TABLE shadow_schema_metadata "
            "(singleton_id INTEGER PRIMARY KEY, schema_version TEXT NOT NULL)"
        )
        connection.execute("INSERT INTO shadow_schema_metadata VALUES (1, 'future-shadow-schema')")
        connection.execute("CREATE TABLE shadow_runs (shadow_run_id TEXT PRIMARY KEY)")

    with pytest.raises(ShadowSchemaError, match="incomplete_shadow_schema"):
        ShadowSQLiteRepository(incompatible).initialize()

    mixed = tmp_path / "mixed.sqlite3"
    with sqlite3.connect(mixed) as connection:
        connection.execute("CREATE TABLE paper_execution_attempts (id TEXT)")

    with pytest.raises(ShadowSchemaError, match="mixed_shadow_database"):
        ShadowSQLiteRepository(mixed).initialize()


def test_deterministic_run_id_survives_persistence_reload(tmp_path: Path) -> None:
    dataset, result = _run_healthy(tmp_path)
    stored = ShadowSQLiteRepository(tmp_path / "shadow.sqlite3").get_run(result.shadow_run_id)
    snapshot = build_operational_snapshot(
        manifest=dataset.manifest,
        canonical_bars=dataset.canonical_bars,
        session=date(2025, 1, 2),
        as_of=datetime(2025, 1, 3, 0, 0, tzinfo=UTC),
        provider_finalized=True,
        provider_finalization_policy_id="synthetic-provider-finalized-v1",
    )

    assert result.shadow_run_id == shadow_run_identity(snapshot.request)
    assert stored.run.shadow_run_id == result.shadow_run_id
    assert stored.input_snapshot is not None
    assert stored.input_snapshot.parent_dataset_id == dataset.manifest.dataset_id
    assert stored.input_snapshot.canonical_dataset_checksum == (
        dataset.manifest.canonical_content_checksum
    )


def test_completed_duplicate_retry_persists_audit_without_lifecycle_change(tmp_path: Path) -> None:
    dataset, result = _run_healthy(tmp_path)
    repository = ShadowSQLiteRepository(tmp_path / "shadow.sqlite3")
    before = repository.get_run(result.shadow_run_id)

    with pytest.raises(ShadowDuplicateRunError, match="duplicate_run"):
        run_observation(
            manifest_path=dataset.manifest_path,
            data_root=dataset.data_root,
            shadow_db=tmp_path / "shadow.sqlite3",
            session=date(2025, 1, 2),
            as_of=datetime(2025, 1, 3, 0, 0, tzinfo=UTC),
            provider_finalized=True,
            provider_finalization_policy_id="synthetic-provider-finalized-v1",
            repository_root=tmp_path,
            clock=FixedClock(),
        )
    after = repository.get_run(result.shadow_run_id)

    assert _run_row_count(tmp_path / "shadow.sqlite3", result.shadow_run_id) == 1
    assert after.run.run_status == ShadowOperationalRunStatus.COMPLETED
    assert after.input_snapshot == before.input_snapshot
    assert "duplicate_run" in {event.event_code for event in after.health_events}
    assert "duplicate_run" in {alert.alert_code for alert in after.alerts}
    assert after.health_events[-1].event_timestamp == datetime_to_text(FIXED_COMPLETED)


def test_blocked_duplicate_retry_persists_audit_without_lifecycle_change(tmp_path: Path) -> None:
    blocked_dataset = write_synthetic_phase1_dataset(
        tmp_path / "blocked",
        start_session=date(2025, 1, 2),
        end_session=date(2025, 1, 2),
    )
    shadow_db = tmp_path / "blocked.sqlite3"
    blocked_result = run_observation(
        manifest_path=blocked_dataset.manifest_path,
        data_root=blocked_dataset.data_root,
        shadow_db=shadow_db,
        session=date(2025, 1, 2),
        as_of=datetime(2025, 1, 3, 0, 0, tzinfo=UTC),
        provider_finalized=False,
        provider_finalization_policy_id="synthetic-provider-finalized-v1",
        repository_root=tmp_path / "blocked",
        clock=FixedClock(),
    )
    repository = ShadowSQLiteRepository(shadow_db)
    before = repository.get_run(blocked_result.shadow_run_id)
    with pytest.raises(ShadowDuplicateRunError, match="duplicate_run"):
        run_observation(
            manifest_path=blocked_dataset.manifest_path,
            data_root=blocked_dataset.data_root,
            shadow_db=shadow_db,
            session=date(2025, 1, 2),
            as_of=datetime(2025, 1, 3, 0, 0, tzinfo=UTC),
            provider_finalized=False,
            provider_finalization_policy_id="synthetic-provider-finalized-v1",
            repository_root=tmp_path / "blocked",
            clock=FixedClock(),
        )
    after = repository.get_run(blocked_result.shadow_run_id)

    assert _run_row_count(shadow_db, blocked_result.shadow_run_id) == 1
    assert after.run.run_status == ShadowOperationalRunStatus.BLOCKED
    assert after.input_snapshot == before.input_snapshot
    assert "duplicate_run" in {event.event_code for event in after.health_events}
    assert "duplicate_run" in {alert.alert_code for alert in after.alerts}


def test_repeated_duplicate_attempts_append_audit_without_new_run_rows(tmp_path: Path) -> None:
    dataset, result = _run_healthy(tmp_path)
    repository = ShadowSQLiteRepository(tmp_path / "shadow.sqlite3")

    for _ in range(2):
        with pytest.raises(ShadowDuplicateRunError, match="duplicate_run"):
            run_observation(
                manifest_path=dataset.manifest_path,
                data_root=dataset.data_root,
                shadow_db=tmp_path / "shadow.sqlite3",
                session=date(2025, 1, 2),
                as_of=datetime(2025, 1, 3, 0, 0, tzinfo=UTC),
                provider_finalized=True,
                provider_finalization_policy_id="synthetic-provider-finalized-v1",
                repository_root=tmp_path,
                clock=FixedClock(),
            )
    stored = repository.get_run(result.shadow_run_id)
    duplicate_events = [
        event for event in stored.health_events if event.event_code == "duplicate_run"
    ]
    duplicate_alerts = [alert for alert in stored.alerts if alert.alert_code == "duplicate_run"]

    assert _run_row_count(tmp_path / "shadow.sqlite3", result.shadow_run_id) == 1
    assert stored.run.run_status == ShadowOperationalRunStatus.COMPLETED
    assert len(duplicate_events) == 2
    assert len(duplicate_alerts) == 2


def test_incomplete_reserved_run_requires_recovery_review(tmp_path: Path) -> None:
    dataset = write_synthetic_phase1_dataset(tmp_path)
    run_id = _reserve_synthetic_run(
        tmp_path=tmp_path,
        dataset=dataset,
        database_path=tmp_path / "shadow.sqlite3",
    )
    repository = ShadowSQLiteRepository(tmp_path / "shadow.sqlite3")

    with pytest.raises(ShadowRecoveryRequiredError, match="recovery_required"):
        run_observation(
            manifest_path=dataset.manifest_path,
            data_root=dataset.data_root,
            shadow_db=tmp_path / "shadow.sqlite3",
            session=date(2025, 1, 2),
            as_of=datetime(2025, 1, 3, 0, 0, tzinfo=UTC),
            provider_finalized=True,
            provider_finalization_policy_id="synthetic-provider-finalized-v1",
            repository_root=tmp_path,
            clock=FixedClock(),
        )
    stored = repository.get_run(run_id)

    assert _run_row_count(tmp_path / "shadow.sqlite3", run_id) == 1
    assert stored.run.run_status == ShadowOperationalRunStatus.RESERVED
    assert stored.input_snapshot is None
    assert "recovery_required" in {event.event_code for event in stored.health_events}
    assert "recovery_required" in {alert.alert_code for alert in stored.alerts}


@pytest.mark.parametrize("mismatched_child", ["snapshot", "event", "alert"])
def test_child_run_id_mismatch_during_finalize_fails_closed(
    tmp_path: Path,
    mismatched_child: str,
) -> None:
    root = tmp_path / mismatched_child
    dataset = write_synthetic_phase1_dataset(root)
    database_path = root / "shadow.sqlite3"
    run_id = _reserve_synthetic_run(tmp_path=root, dataset=dataset, database_path=database_path)
    wrong_run_id = f"{run_id}-other"
    snapshot_run_id = wrong_run_id if mismatched_child == "snapshot" else run_id
    event_run_id = wrong_run_id if mismatched_child == "event" else run_id
    alert_run_id = wrong_run_id if mismatched_child == "alert" else run_id
    repository = ShadowSQLiteRepository(database_path)

    with pytest.raises(ShadowPersistenceError, match="audit_identity_mismatch"):
        repository.finalize_run(
            shadow_run_id=run_id,
            terminal_status=ShadowOperationalRunStatus.COMPLETED,
            freshness_status=FreshnessStatus.FRESH,
            monitoring_status=ShadowHealthStatus.HEALTHY,
            model_gate_status=ModelAdmissionStatus.BLOCKED_NO_APPROVED_MODEL,
            completed_at=FIXED_COMPLETED,
            input_snapshot=_synthetic_snapshot_record(
                shadow_run_id=snapshot_run_id,
                dataset=dataset,
            ),
            health_events=(_audit_event(event_run_id, "fresh_data"),),
            alerts=(_audit_alert(alert_run_id, "duplicate_run"),),
        )
    stored = repository.get_run(run_id)

    assert stored.run.run_status == ShadowOperationalRunStatus.RESERVED
    assert stored.input_snapshot is None
    assert stored.health_events == ()
    assert stored.alerts == ()


def test_child_run_id_mismatch_during_failure_and_retry_audit_fails_closed(
    tmp_path: Path,
) -> None:
    failure_root = tmp_path / "failure"
    failure_dataset = write_synthetic_phase1_dataset(failure_root)
    failure_db = failure_root / "shadow.sqlite3"
    failure_run_id = _reserve_synthetic_run(
        tmp_path=failure_root,
        dataset=failure_dataset,
        database_path=failure_db,
    )
    failure_repository = ShadowSQLiteRepository(failure_db)
    with pytest.raises(ShadowPersistenceError, match="audit_identity_mismatch"):
        failure_repository.mark_failed(
            shadow_run_id=failure_run_id,
            completed_at=FIXED_COMPLETED,
            event=_audit_event(f"{failure_run_id}-other"),
            alert=_audit_alert(failure_run_id),
        )
    failure_stored = failure_repository.get_run(failure_run_id)
    assert failure_stored.run.run_status == ShadowOperationalRunStatus.RESERVED
    assert failure_stored.health_events == ()
    assert failure_stored.alerts == ()

    retry_root = tmp_path / "retry"
    retry_dataset = write_synthetic_phase1_dataset(retry_root)
    retry_db = retry_root / "shadow.sqlite3"
    retry_run_id = _reserve_synthetic_run(
        tmp_path=retry_root,
        dataset=retry_dataset,
        database_path=retry_db,
    )
    retry_repository = ShadowSQLiteRepository(retry_db)
    with pytest.raises(ShadowPersistenceError, match="audit_identity_mismatch"):
        retry_repository.record_retry_rejection(
            shadow_run_id=retry_run_id,
            event=_audit_event(retry_run_id, "recovery_required"),
            alert=_audit_alert(f"{retry_run_id}-other", "recovery_required"),
        )
    retry_stored = retry_repository.get_run(retry_run_id)
    assert retry_stored.run.run_status == ShadowOperationalRunStatus.RESERVED
    assert retry_stored.health_events == ()
    assert retry_stored.alerts == ()


def test_sqlite_operational_failure_is_typed_and_cli_fails_closed(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bad_database_path = tmp_path / "not-a-database.sqlite3"
    bad_database_path.mkdir()

    with pytest.raises(ShadowPersistenceError) as exc_info:
        ShadowSQLiteRepository(bad_database_path).get_run("synthetic-run")
    assert exc_info.value.code == "shadow_database_unavailable"
    assert isinstance(exc_info.value, ShadowPersistenceError)

    from spy_market_agent.shadow.cli import main

    exit_code = main(
        [
            "show-run",
            "--shadow-db",
            str(bad_database_path),
            "--run-id",
            "synthetic-run",
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 1
    assert "status=failed_closed" in output
    assert "reason=shadow_database_unavailable" in output


def test_competing_reservation_fails_closed_without_duplicate_rows(tmp_path: Path) -> None:
    dataset = write_synthetic_phase1_dataset(tmp_path)
    database_path = tmp_path / "shadow.sqlite3"
    repository = initialize_shadow_database(database_path)
    run = _synthetic_reserved_run_record(dataset)
    locking_connection = sqlite3.connect(database_path)
    try:
        locking_connection.execute("BEGIN IMMEDIATE")
        with pytest.raises(ShadowPersistenceError) as exc_info:
            repository.reserve_run(run)
        assert exc_info.value.code == "persistence_failure"
    finally:
        locking_connection.rollback()
        locking_connection.close()

    assert _run_row_count(database_path, run.shadow_run_id) == 0
    repository.reserve_run(run)
    with pytest.raises(ShadowRecoveryRequiredError, match="recovery_required"):
        repository.reserve_run(run)
    assert _run_row_count(database_path, run.shadow_run_id) == 1


def test_target_contract_rejects_wrong_lineage_scope_and_sessions(tmp_path: Path) -> None:
    dataset = write_synthetic_phase1_dataset(
        tmp_path,
        start_session=date(2025, 1, 2),
        end_session=date(2025, 1, 3),
        retrieval_timestamp=datetime(2025, 1, 4, 0, 0, tzinfo=UTC),
    )

    with pytest.raises(ManifestValidationFailure, match="symbol"):
        build_operational_snapshot(
            manifest=dataset.manifest.model_copy(update={"symbol": "AAPL"}),
            canonical_bars=dataset.canonical_bars,
            session=date(2025, 1, 3),
            as_of=datetime(2025, 1, 4, 0, 0, tzinfo=UTC),
            provider_finalized=True,
            provider_finalization_policy_id="synthetic-provider-finalized-v1",
        )
    with pytest.raises(ManifestValidationFailure, match="timeframe"):
        build_operational_snapshot(
            manifest=dataset.manifest.model_copy(update={"timeframe": "1Min"}),
            canonical_bars=dataset.canonical_bars,
            session=date(2025, 1, 3),
            as_of=datetime(2025, 1, 4, 0, 0, tzinfo=UTC),
            provider_finalized=True,
            provider_finalization_policy_id="synthetic-provider-finalized-v1",
        )
    with pytest.raises(ManifestValidationFailure, match="calendar"):
        build_operational_snapshot(
            manifest=dataset.manifest.model_copy(
                update={"relevant_configuration": {"calendar": "XNAS"}}
            ),
            canonical_bars=dataset.canonical_bars,
            session=date(2025, 1, 3),
            as_of=datetime(2025, 1, 4, 0, 0, tzinfo=UTC),
            provider_finalized=True,
            provider_finalization_policy_id="synthetic-provider-finalized-v1",
        )
    with pytest.raises(ShadowOperationalError, match="not_xnys_session"):
        build_operational_snapshot(
            manifest=dataset.manifest,
            canonical_bars=dataset.canonical_bars,
            session=date(2025, 1, 4),
            as_of=datetime(2025, 1, 5, 0, 0, tzinfo=UTC),
            provider_finalized=True,
            provider_finalization_policy_id="synthetic-provider-finalized-v1",
        )
    with pytest.raises(ShadowOperationalError, match="target_session_not_latest"):
        build_operational_snapshot(
            manifest=dataset.manifest,
            canonical_bars=dataset.canonical_bars,
            session=date(2025, 1, 2),
            as_of=datetime(2025, 1, 4, 0, 0, tzinfo=UTC),
            provider_finalized=True,
            provider_finalization_policy_id="synthetic-provider-finalized-v1",
        )
    with pytest.raises(ShadowOperationalError, match="missing_session"):
        build_operational_snapshot(
            manifest=dataset.manifest,
            canonical_bars=dataset.canonical_bars[:1],
            session=date(2025, 1, 3),
            as_of=datetime(2025, 1, 4, 0, 0, tzinfo=UTC),
            provider_finalized=True,
            provider_finalization_policy_id="synthetic-provider-finalized-v1",
        )


def test_stale_incomplete_provider_unfinalized_and_invalid_ohlcv_block(tmp_path: Path) -> None:
    stale_dataset = write_synthetic_phase1_dataset(tmp_path / "stale")
    stale_result = run_observation(
        manifest_path=stale_dataset.manifest_path,
        data_root=stale_dataset.data_root,
        shadow_db=tmp_path / "stale.sqlite3",
        session=date(2025, 1, 2),
        as_of=datetime(2025, 1, 4, 0, 0, tzinfo=UTC),
        provider_finalized=True,
        provider_finalization_policy_id="synthetic-provider-finalized-v1",
        repository_root=tmp_path / "stale",
        clock=FixedClock(),
    )
    stale_stored = ShadowSQLiteRepository(tmp_path / "stale.sqlite3").get_run(
        stale_result.shadow_run_id
    )
    assert stale_result.run_status == ShadowOperationalRunStatus.BLOCKED
    assert "stale_data" in stale_result.alerts
    assert "stale_data" in {event.event_code for event in stale_stored.health_events}

    incomplete_dataset = write_synthetic_phase1_dataset(tmp_path / "incomplete")
    incomplete_result = run_observation(
        manifest_path=incomplete_dataset.manifest_path,
        data_root=incomplete_dataset.data_root,
        shadow_db=tmp_path / "incomplete.sqlite3",
        session=date(2025, 1, 2),
        as_of=datetime(2025, 1, 2, 12, 0, tzinfo=UTC),
        provider_finalized=True,
        provider_finalization_policy_id="synthetic-provider-finalized-v1",
        repository_root=tmp_path / "incomplete",
        clock=FixedClock(),
    )
    incomplete_events = (
        ShadowSQLiteRepository(tmp_path / "incomplete.sqlite3")
        .get_run(incomplete_result.shadow_run_id)
        .health_events
    )
    assert incomplete_result.run_status == ShadowOperationalRunStatus.BLOCKED
    assert "incomplete_session" in {event.event_code for event in incomplete_events}

    unfinalized_dataset = write_synthetic_phase1_dataset(tmp_path / "unfinalized")
    unfinalized_result = run_observation(
        manifest_path=unfinalized_dataset.manifest_path,
        data_root=unfinalized_dataset.data_root,
        shadow_db=tmp_path / "unfinalized.sqlite3",
        session=date(2025, 1, 2),
        as_of=datetime(2025, 1, 3, 0, 0, tzinfo=UTC),
        provider_finalized=False,
        provider_finalization_policy_id="synthetic-provider-finalized-v1",
        repository_root=tmp_path / "unfinalized",
        clock=FixedClock(),
    )
    assert unfinalized_result.run_status == ShadowOperationalRunStatus.BLOCKED
    assert "provider_not_finalized" in unfinalized_result.alerts

    invalid_bar = unfinalized_dataset.canonical_bars[-1].model_copy(update={"high": "1"})
    invalid_snapshot = build_operational_snapshot(
        manifest=unfinalized_dataset.manifest,
        canonical_bars=(invalid_bar,),
        session=date(2025, 1, 2),
        as_of=datetime(2025, 1, 3, 0, 0, tzinfo=UTC),
        provider_finalized=True,
        provider_finalization_policy_id="synthetic-provider-finalized-v1",
    )
    freshness = evaluate_market_data_freshness(invalid_snapshot.market_data_status)
    assert invalid_snapshot.market_data_status.ohlcv_valid is False
    assert "invalid_ohlcv" in freshness.reasons


def test_healthy_observation_only_run_persists_expected_state(tmp_path: Path) -> None:
    _, result = _run_healthy(tmp_path)
    stored = ShadowSQLiteRepository(tmp_path / "shadow.sqlite3").get_run(result.shadow_run_id)

    assert result.run_status == ShadowOperationalRunStatus.COMPLETED
    assert result.monitoring_status == ShadowHealthStatus.HEALTHY
    assert result.model_gate_status == ModelAdmissionStatus.BLOCKED_NO_APPROVED_MODEL
    assert result.proposal_generated is False
    assert stored.run.run_status == ShadowOperationalRunStatus.COMPLETED
    assert stored.run.model_gate_status == ModelAdmissionStatus.BLOCKED_NO_APPROVED_MODEL
    assert stored.input_snapshot is not None
    assert stored.input_snapshot.target_session == "2025-01-02"
    assert {event.event_code for event in stored.health_events} == {
        "fresh_data",
        "model_not_approved",
    }
    assert stored.alerts == ()


def test_show_run_cli_is_read_only_and_deterministic(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, result = _run_healthy(tmp_path)
    before = ShadowSQLiteRepository(tmp_path / "shadow.sqlite3").get_run(result.shadow_run_id)

    from spy_market_agent.shadow.cli import main

    exit_code = main(
        [
            "show-run",
            "--shadow-db",
            str(tmp_path / "shadow.sqlite3"),
            "--run-id",
            result.shadow_run_id,
        ]
    )
    captured = capsys.readouterr().out
    after = ShadowSQLiteRepository(tmp_path / "shadow.sqlite3").get_run(result.shadow_run_id)

    assert exit_code == 0
    assert f"shadow_run_id={result.shadow_run_id}" in captured
    assert "model_gate_status=blocked_no_approved_model" in captured
    assert before == after


def test_lineage_checksum_failure_blocks_before_healthy_state(tmp_path: Path) -> None:
    dataset = write_synthetic_phase1_dataset(tmp_path)
    canonical_path = tmp_path / dataset.manifest.generated_file_locations.canonical_path
    canonical_path.write_text(canonical_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    with pytest.raises(ChecksumMismatch):
        run_observation(
            manifest_path=dataset.manifest_path,
            data_root=dataset.data_root,
            shadow_db=tmp_path / "shadow.sqlite3",
            session=date(2025, 1, 2),
            as_of=datetime(2025, 1, 3, 0, 0, tzinfo=UTC),
            provider_finalized=True,
            provider_finalization_policy_id="synthetic-provider-finalized-v1",
            repository_root=tmp_path,
            clock=FixedClock(),
        )
    assert not (tmp_path / "shadow.sqlite3").exists()


def test_observation_runner_rejects_model_connected_mode_and_self_declared_metadata(
    tmp_path: Path,
) -> None:
    with pytest.raises(ShadowOperationalError, match="observation_only_mode_required"):
        run_observation(
            manifest_path=tmp_path / "unused.manifest.json",
            data_root=Path("data"),
            shadow_db=tmp_path / "shadow.sqlite3",
            session=date(2025, 1, 2),
            as_of=datetime(2025, 1, 3, 0, 0, tzinfo=UTC),
            provider_finalized=True,
            provider_finalization_policy_id="synthetic-provider-finalized-v1",
            repository_root=tmp_path,
            mode=ShadowMode.MODEL_CONNECTED,
            model_metadata=_approved_model_metadata(),
        )
    with pytest.raises(ShadowOperationalError, match="model_metadata"):
        run_observation(
            manifest_path=tmp_path / "unused.manifest.json",
            data_root=Path("data"),
            shadow_db=tmp_path / "shadow.sqlite3",
            session=date(2025, 1, 2),
            as_of=datetime(2025, 1, 3, 0, 0, tzinfo=UTC),
            provider_finalized=True,
            provider_finalization_policy_id="synthetic-provider-finalized-v1",
            repository_root=tmp_path,
            model_metadata=_approved_model_metadata(),
        )


def test_shadow_cli_does_not_require_credential_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    dataset = write_synthetic_phase1_dataset(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ALPACA_MARKET_DATA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_MARKET_DATA_SECRET_KEY", raising=False)
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)

    from spy_market_agent.shadow.cli import main

    exit_code = main(
        [
            "run-observation",
            "--manifest",
            str(dataset.manifest_path),
            "--data-root",
            str(dataset.data_root),
            "--shadow-db",
            str(tmp_path / "shadow.sqlite3"),
            "--session",
            "2025-01-02",
            "--as-of",
            "2025-01-03T00:00:00Z",
            "--provider-finalized",
            "--provider-finalization-policy-id",
            "synthetic-provider-finalized-v1",
        ]
    )
    captured = capsys.readouterr().out

    assert exit_code == 0
    assert "run_status=completed" in captured
    assert "model_gate_status=blocked_no_approved_model" in captured


def test_shadow_static_boundary_excludes_execution_network_and_credential_names() -> None:
    shadow_files = sorted((ROOT / "src/spy_market_agent/shadow").glob("*.py"))
    assert shadow_files
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
        "cron",
    )

    for path in shadow_files:
        text = path.read_text(encoding="utf-8")
        for fragment in forbidden_fragments:
            assert fragment not in text, f"{path}: {fragment}"


def test_shadow_import_has_no_side_effects_or_forbidden_modules(tmp_path: Path) -> None:
    script = """
import sys
import spy_market_agent.shadow
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


def test_shadow_sqlite_errors_remain_typed(tmp_path: Path) -> None:
    repository = initialize_shadow_database(tmp_path / "shadow.sqlite3")

    with pytest.raises(ShadowPersistenceError, match="list limit"):
        repository.list_runs(limit=0)


def test_manifest_adjustment_policy_must_be_all(tmp_path: Path) -> None:
    dataset = write_synthetic_phase1_dataset(
        tmp_path,
        adjustment_mode="raw",
    )

    with pytest.raises(ManifestValidationFailure, match="adjustment"):
        run_observation(
            manifest_path=dataset.manifest_path,
            data_root=dataset.data_root,
            shadow_db=tmp_path / "shadow.sqlite3",
            session=date(2025, 1, 2),
            as_of=datetime(2025, 1, 3, 0, 0, tzinfo=UTC),
            provider_finalized=True,
            provider_finalization_policy_id="synthetic-provider-finalized-v1",
            repository_root=tmp_path,
            clock=FixedClock(),
        )
