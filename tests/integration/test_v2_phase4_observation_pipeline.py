from __future__ import annotations

import sqlite3
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from spy_market_agent.market_data.errors import ChecksumMismatch
from spy_market_agent.persistence.serialization import datetime_to_text
from spy_market_agent.shadow import (
    SHADOW_OPERATION_CONFIGURATION_VERSION,
    FreshnessStatus,
    ModelAdmissionStatus,
    ShadowHealthStatus,
    ShadowMode,
    ShadowOperationalRunStatus,
    ShadowRecoveryRequiredError,
    ShadowRunRecord,
    ShadowSQLiteRepository,
    build_operational_snapshot,
    run_observation,
    shadow_run_identity,
)
from spy_market_agent.shadow.persistence import ShadowDuplicateRunError
from unit.phase4_shadow_helpers import write_synthetic_phase1_dataset

FIXED_CREATED = datetime(2025, 1, 3, 0, 1, tzinfo=UTC)
FIXED_COMPLETED = datetime(2025, 1, 3, 0, 2, tzinfo=UTC)


class FixedClock:
    def __init__(self) -> None:
        self._values = [FIXED_CREATED, FIXED_COMPLETED, FIXED_COMPLETED]

    def __call__(self) -> datetime:
        return self._values.pop(0)


def loaded_execution_modules() -> set[str]:
    return {
        module_name
        for module_name in sys.modules
        if module_name == "spy_market_agent.execution"
        or module_name.startswith("spy_market_agent.execution.")
    }


def test_phase4_healthy_observation_pipeline_cli_persists_audit_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    dataset = write_synthetic_phase1_dataset(tmp_path)
    monkeypatch.chdir(tmp_path)
    execution_modules_before = loaded_execution_modules()
    from spy_market_agent.shadow.cli import main

    exit_code = main(
        [
            "run-observation",
            "--manifest",
            str(dataset.manifest_path),
            "--data-root",
            str(dataset.data_root),
            "--shadow-db",
            "shadow.sqlite3",
            "--session",
            "2025-01-02",
            "--as-of",
            "2025-01-03T00:00:00Z",
            "--provider-finalized",
            "--provider-finalization-policy-id",
            "synthetic-provider-finalized-v1",
        ]
    )
    output = capsys.readouterr().out
    run_id = next(
        line.split("=", maxsplit=1)[1]
        for line in output.splitlines()
        if line.startswith("shadow_run_id=")
    )
    stored = ShadowSQLiteRepository(tmp_path / "shadow.sqlite3").get_run(run_id)

    assert exit_code == 0
    assert "run_status=completed" in output
    assert "model_gate_status=blocked_no_approved_model" in output
    assert "proposal_generated=false" in output
    assert stored.run.run_status == ShadowOperationalRunStatus.COMPLETED
    assert stored.run.monitoring_status == ShadowHealthStatus.HEALTHY
    assert stored.run.model_gate_status == ModelAdmissionStatus.BLOCKED_NO_APPROVED_MODEL
    assert stored.alerts == ()
    assert loaded_execution_modules() == execution_modules_before


def test_phase4_stale_data_pipeline_persists_blocked_event_and_alert(tmp_path: Path) -> None:
    dataset = write_synthetic_phase1_dataset(tmp_path)
    result = run_observation(
        manifest_path=dataset.manifest_path,
        data_root=dataset.data_root,
        shadow_db=tmp_path / "shadow.sqlite3",
        session=date(2025, 1, 2),
        as_of=datetime(2025, 1, 4, 0, 0, tzinfo=UTC),
        provider_finalized=True,
        provider_finalization_policy_id="synthetic-provider-finalized-v1",
        repository_root=tmp_path,
        clock=FixedClock(),
    )
    stored = ShadowSQLiteRepository(tmp_path / "shadow.sqlite3").get_run(result.shadow_run_id)

    assert result.run_status == ShadowOperationalRunStatus.BLOCKED
    assert result.data_status == FreshnessStatus.BLOCKED
    assert "stale_data" in {event.event_code for event in stored.health_events}
    assert "stale_data" in {alert.alert_code for alert in stored.alerts}
    assert result.proposal_generated is False


def test_phase4_duplicate_pipeline_run_fails_closed_durably(tmp_path: Path) -> None:
    dataset = write_synthetic_phase1_dataset(tmp_path)
    first = run_observation(
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
    stored = ShadowSQLiteRepository(tmp_path / "shadow.sqlite3").get_run(first.shadow_run_id)

    with sqlite3.connect(tmp_path / "shadow.sqlite3") as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM shadow_runs WHERE shadow_run_id = ?",
            (first.shadow_run_id,),
        ).fetchone()[0]
    assert count == 1
    assert stored.run.run_status == ShadowOperationalRunStatus.COMPLETED
    assert "duplicate_run" in {event.event_code for event in stored.health_events}
    assert "duplicate_run" in {alert.alert_code for alert in stored.alerts}


def test_phase4_incomplete_reservation_requires_recovery_without_overwrite(
    tmp_path: Path,
) -> None:
    dataset = write_synthetic_phase1_dataset(tmp_path)
    snapshot = build_operational_snapshot(
        manifest=dataset.manifest,
        canonical_bars=dataset.canonical_bars,
        session=date(2025, 1, 2),
        as_of=datetime(2025, 1, 3, 0, 0, tzinfo=UTC),
        provider_finalized=True,
        provider_finalization_policy_id="synthetic-provider-finalized-v1",
    )
    run_id = shadow_run_identity(snapshot.request)
    repository = ShadowSQLiteRepository(tmp_path / "shadow.sqlite3")
    repository.initialize()
    repository.reserve_run(
        ShadowRunRecord(
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
    )

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
    assert stored.run.run_status == ShadowOperationalRunStatus.RESERVED
    assert stored.input_snapshot is None
    assert "recovery_required" in {event.event_code for event in stored.health_events}
    assert "recovery_required" in {alert.alert_code for alert in stored.alerts}


def test_phase4_lineage_failure_does_not_create_healthy_shadow_record(tmp_path: Path) -> None:
    dataset = write_synthetic_phase1_dataset(tmp_path)
    canonical_path = tmp_path / dataset.manifest.generated_file_locations.canonical_path
    canonical_path.write_text(
        canonical_path.read_text(encoding="utf-8").replace("101", "199", 1),
        encoding="utf-8",
    )

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
