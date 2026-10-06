from __future__ import annotations

import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from spy_market_agent.shadow import (
    ModelAdmissionStatus,
    ShadowHealthStatus,
    ShadowMode,
    ShadowOperationalRunStatus,
    ShadowPersistenceError,
    ShadowSQLiteRepository,
    run_due_observation,
)
from spy_market_agent.shadow.schedule_ops import evaluate_scheduled_observation
from unit.phase4_shadow_helpers import write_synthetic_phase1_dataset


def _loaded_execution_modules() -> set[str]:
    return {
        module_name
        for module_name in sys.modules
        if module_name == "spy_market_agent.execution"
        or module_name.startswith("spy_market_agent.execution.")
    }


def _shadow_run_count(database_path: Path) -> int:
    with sqlite3.connect(database_path) as connection:
        return int(connection.execute("SELECT COUNT(*) FROM shadow_runs").fetchone()[0])


def _health_event_count(database_path: Path, event_code: str) -> int:
    with sqlite3.connect(database_path) as connection:
        return int(
            connection.execute(
                "SELECT COUNT(*) FROM shadow_health_events WHERE event_code = ?",
                (event_code,),
            ).fetchone()[0]
        )


def test_phase4_run_due_observation_cli_persists_one_fresh_due_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    dataset = write_synthetic_phase1_dataset(tmp_path)
    monkeypatch.chdir(tmp_path)
    execution_modules_before = _loaded_execution_modules()

    from spy_market_agent.shadow.cli import main

    exit_code = main(
        [
            "run-due-observation",
            "--manifest",
            str(dataset.manifest_path),
            "--data-root",
            str(dataset.data_root),
            "--shadow-db",
            "shadow.sqlite3",
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
        if line.startswith("observation_shadow_run_id=")
    )
    stored = ShadowSQLiteRepository(tmp_path / "shadow.sqlite3").get_run(run_id)

    assert exit_code == 0
    assert "schedule_action=ran_observation" in output
    assert "target_session=2025-01-02" in output
    assert "observation_run_status=completed" in output
    assert stored.run.run_status == ShadowOperationalRunStatus.COMPLETED
    assert stored.run.monitoring_status == ShadowHealthStatus.HEALTHY
    assert stored.run.model_gate_status == ModelAdmissionStatus.BLOCKED_NO_APPROVED_MODEL
    assert stored.run.mode == ShadowMode.OBSERVATION_ONLY_NO_MODEL
    assert stored.alerts == ()
    assert _shadow_run_count(tmp_path / "shadow.sqlite3") == 1
    assert _loaded_execution_modules() == execution_modules_before


def test_phase4_run_due_observation_is_orchestration_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    dataset = write_synthetic_phase1_dataset(tmp_path)
    monkeypatch.chdir(tmp_path)

    from spy_market_agent.shadow.cli import main

    first_exit = main(
        [
            "run-due-observation",
            "--manifest",
            str(dataset.manifest_path),
            "--data-root",
            str(dataset.data_root),
            "--shadow-db",
            str(tmp_path / "shadow.sqlite3"),
            "--as-of",
            "2025-01-03T00:00:00Z",
            "--provider-finalized",
            "--provider-finalization-policy-id",
            "synthetic-provider-finalized-v1",
        ]
    )
    _ = capsys.readouterr()
    second_exit = main(
        [
            "run-due-observation",
            "--manifest",
            str(dataset.manifest_path),
            "--data-root",
            str(dataset.data_root),
            "--shadow-db",
            str(tmp_path / "shadow.sqlite3"),
            "--as-of",
            "2025-01-03T00:00:00Z",
            "--provider-finalized",
            "--provider-finalization-policy-id",
            "synthetic-provider-finalized-v1",
        ]
    )
    output = capsys.readouterr().out

    assert first_exit == 0
    assert second_exit == 0
    assert "schedule_action=already_processed" in output
    assert _shadow_run_count(tmp_path / "shadow.sqlite3") == 1
    assert _health_event_count(tmp_path / "shadow.sqlite3", "duplicate_run") == 0


def test_phase4_schedule_preview_is_read_only_with_existing_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    dataset = write_synthetic_phase1_dataset(tmp_path)
    monkeypatch.chdir(tmp_path)
    result = run_due_observation(
        manifest_path=dataset.manifest_path,
        data_root=dataset.data_root,
        shadow_db=tmp_path / "shadow.sqlite3",
        as_of=datetime(2025, 1, 3, 0, 0, tzinfo=UTC),
        provider_finalized=True,
        provider_finalization_policy_id="synthetic-provider-finalized-v1",
        repository_root=tmp_path,
    )
    observation_result = result.observation_result
    assert observation_result is not None
    before = ShadowSQLiteRepository(tmp_path / "shadow.sqlite3").get_run(
        observation_result.shadow_run_id
    )

    from spy_market_agent.shadow.cli import main

    exit_code = main(
        [
            "schedule-preview",
            "--manifest",
            str(dataset.manifest_path),
            "--data-root",
            str(dataset.data_root),
            "--shadow-db",
            str(tmp_path / "shadow.sqlite3"),
            "--as-of",
            "2025-01-03T00:00:00Z",
            "--provider-finalized",
            "--provider-finalization-policy-id",
            "synthetic-provider-finalized-v1",
        ]
    )
    output = capsys.readouterr().out
    after = ShadowSQLiteRepository(tmp_path / "shadow.sqlite3").get_run(
        observation_result.shadow_run_id
    )

    assert exit_code == 0
    assert "schedule_state=already_processed" in output
    assert before == after


def test_phase4_run_due_stale_data_blocks_without_acquisition_or_proposal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    dataset = write_synthetic_phase1_dataset(tmp_path)
    monkeypatch.chdir(tmp_path)

    from spy_market_agent.shadow.cli import main

    exit_code = main(
        [
            "run-due-observation",
            "--manifest",
            str(dataset.manifest_path),
            "--data-root",
            str(dataset.data_root),
            "--shadow-db",
            str(tmp_path / "shadow.sqlite3"),
            "--as-of",
            "2025-01-04T00:00:00Z",
            "--provider-finalized",
            "--provider-finalization-policy-id",
            "synthetic-provider-finalized-v1",
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 1
    assert "schedule_action=blocked" in output
    assert "reasons=stale_data" in output
    assert not (tmp_path / "shadow.sqlite3").exists()


def test_phase4_run_due_competing_write_fails_typed_without_raw_sqlite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    dataset = write_synthetic_phase1_dataset(tmp_path)
    monkeypatch.chdir(tmp_path)
    shadow_db = tmp_path / "shadow.sqlite3"
    ShadowSQLiteRepository(shadow_db).initialize()
    locking_connection = sqlite3.connect(shadow_db)

    from spy_market_agent.shadow.cli import main

    try:
        locking_connection.execute("BEGIN IMMEDIATE")
        exit_code = main(
            [
                "run-due-observation",
                "--manifest",
                str(dataset.manifest_path),
                "--data-root",
                str(dataset.data_root),
                "--shadow-db",
                str(shadow_db),
                "--as-of",
                "2025-01-03T00:00:00Z",
                "--provider-finalized",
                "--provider-finalization-policy-id",
                "synthetic-provider-finalized-v1",
            ]
        )
    finally:
        locking_connection.rollback()
        locking_connection.close()
    output = capsys.readouterr().out

    assert exit_code == 1
    assert "status=failed_closed" in output
    assert "reason=persistence_failure" in output
    assert _shadow_run_count(shadow_db) == 0


def test_phase4_schedule_invalid_database_fails_closed_for_preview(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    dataset = write_synthetic_phase1_dataset(tmp_path)
    monkeypatch.chdir(tmp_path)
    bad_database = tmp_path / "bad.sqlite3"
    with sqlite3.connect(bad_database) as connection:
        connection.execute("CREATE TABLE paper_execution_attempts (id TEXT)")

    from spy_market_agent.shadow.cli import main

    exit_code = main(
        [
            "schedule-preview",
            "--manifest",
            str(dataset.manifest_path),
            "--data-root",
            str(dataset.data_root),
            "--shadow-db",
            str(bad_database),
            "--as-of",
            "2025-01-03T00:00:00Z",
            "--provider-finalized",
            "--provider-finalization-policy-id",
            "synthetic-provider-finalized-v1",
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 1
    assert "status=failed_closed" in output
    assert "reason=mixed_shadow_database" in output


def test_phase4_schedule_model_connected_state_remains_locked(tmp_path: Path) -> None:
    dataset = write_synthetic_phase1_dataset(tmp_path)
    with pytest.raises(Exception, match="observation_only_mode_required"):
        evaluate_scheduled_observation(
            manifest_path=dataset.manifest_path,
            data_root=dataset.data_root,
            shadow_db=tmp_path / "shadow.sqlite3",
            as_of=datetime(2025, 1, 3, 0, 0, tzinfo=UTC),
            provider_finalized=True,
            provider_finalization_policy_id="synthetic-provider-finalized-v1",
            repository_root=tmp_path,
            mode=ShadowMode.MODEL_CONNECTED,
        )

    with pytest.raises(ShadowPersistenceError):
        ShadowSQLiteRepository(tmp_path / "missing.sqlite3").get_run("synthetic-run")
