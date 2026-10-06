from __future__ import annotations

import json
import shutil
from datetime import date
from pathlib import Path

import pytest

from spy_market_agent.benchmark.artifacts import sha256_bytes
from spy_market_agent.research import runner
from spy_market_agent.research.cli import main as research_cli_main
from spy_market_agent.research.registries import baseline_model_registry
from unit.v2_phase2_helpers import no_network_guard, write_synthetic_phase1_dataset

ROOT = Path(__file__).resolve().parents[2]


def test_phase3_development_cli_runs_complete_synthetic_campaign(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    no_network_guard(monkeypatch)
    config_path = tmp_path / "configs/research/phase3_development_campaign.json"
    config_path.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / "configs/research/phase3_development_campaign.json", config_path)
    manifest_path = write_synthetic_phase1_dataset(
        tmp_path,
        start=date(2019, 1, 2),
        end=date(2025, 12, 31),
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        runner,
        "development_model_registry",
        lambda *, random_seed=42: baseline_model_registry(random_seed=random_seed),
    )

    exit_code = research_cli_main(
        [
            "run-development",
            "--manifest",
            manifest_path.relative_to(tmp_path).as_posix(),
            "--data-root",
            "data",
            "--campaign-config",
            "configs/research/phase3_development_campaign.json",
            "--artifact-root",
            "artifacts/research",
        ]
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "promotion_decision=NO CANDIDATE PROMOTION" in output
    campaign_id = next(
        line.removeprefix("campaign_id=")
        for line in output.splitlines()
        if line.startswith("campaign_id=")
    )
    artifact_dir = tmp_path / "artifacts/research" / campaign_id
    artifact_names = sorted(path.name for path in artifact_dir.iterdir())
    assert artifact_names == [
        "artifact_index.json",
        "calibration_results.json",
        "classification_results.json",
        "experiment_manifest.json",
        "feature_registry.json",
        "fold_manifest.json",
        "hyperparameter_trials.json",
        "model_registry.json",
        "regime_drift_results.json",
        "selection_report.md",
    ]
    assert "strategy_results.json" not in artifact_names

    experiment_manifest = json.loads((artifact_dir / "experiment_manifest.json").read_text())
    classification_results = json.loads((artifact_dir / "classification_results.json").read_text())
    calibration_results = json.loads((artifact_dir / "calibration_results.json").read_text())
    artifact_index = json.loads((artifact_dir / "artifact_index.json").read_text())
    selection_report = (artifact_dir / "selection_report.md").read_text()

    assert experiment_manifest["protected_evaluation_status"]["state"] == (
        "scaffolded_locked_no_access"
    )
    phase2_boundary = experiment_manifest["phase2_final_test_exclusion_boundary"]
    protected_sessions = set(phase2_boundary["phase2_final_test_prediction_sessions"])
    fold_manifest = json.loads((artifact_dir / "fold_manifest.json").read_text())
    fold_prediction_sessions: set[str] = set()
    for fold in fold_manifest["folds"]:
        fold_prediction_sessions.update(fold["training"]["prediction_sessions"])
        fold_prediction_sessions.update(fold["boundary_excluded_sessions"])
        fold_prediction_sessions.update(fold["assessment"]["prediction_sessions"])

    assert experiment_manifest["dataset_id"] == phase2_boundary["research_slice_id"]
    assert experiment_manifest["parent_phase1_dataset_id"] != experiment_manifest["dataset_id"]
    assert (
        experiment_manifest["phase3_development_eligibility"]["research_slice_checksum"]
        == (phase2_boundary["research_slice_checksum"])
    )
    assert phase2_boundary["excluded_source_session_count"] > 0
    assert fold_prediction_sessions.isdisjoint(protected_sessions)
    assert experiment_manifest["phase2_final_test_available_for_tuning"] is False
    assert experiment_manifest["strategy_results_artifact"] == (
        "not_generated_classification_first_branch"
    )
    assert experiment_manifest["campaign_id"] == campaign_id
    assert classification_results["final_development_selection"]["reason"] == (
        "NO CANDIDATE PROMOTION"
    )
    assert calibration_results["results"]
    assert "Phase 2 final test: unavailable for tuning" in selection_report
    assert "research_slice_id" in selection_report
    assert "protected evaluation: scaffolded_locked_no_access" in selection_report
    assert "strategy optimization: not authorized and not executed" in selection_report

    checksums = {
        entry["relative_path"].split("/")[-1]: entry["sha256_checksum"]
        for entry in artifact_index["artifacts"]
    }
    for name, checksum in checksums.items():
        assert sha256_bytes((artifact_dir / name).read_bytes()) == checksum

    second_exit_code = research_cli_main(
        [
            "run-development",
            "--manifest",
            manifest_path.relative_to(tmp_path).as_posix(),
            "--data-root",
            "data",
            "--campaign-config",
            "configs/research/phase3_development_campaign.json",
            "--artifact-root",
            "artifacts/research",
        ]
    )
    second_output = capsys.readouterr().out
    assert second_exit_code == 0
    assert f"campaign_id={campaign_id}" in second_output
