from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from spy_market_agent.market_data.calendar import XNYSCalendar
from spy_market_agent.research.artifacts import ResearchArtifactStore
from spy_market_agent.research.candidates import development_model_registry
from spy_market_agent.research.cli import main as cli_main
from spy_market_agent.research.constants import PHASE3_ARTIFACT_SCHEMA_VERSION
from spy_market_agent.research.diagnostics import _psi_for_feature
from spy_market_agent.research.errors import (
    ResearchArtifactError,
    ResearchRegistryError,
    WalkForwardSplitError,
)
from spy_market_agent.research.phase3b import (
    _persist_evidence,
    _verify_parent_campaign,
    load_phase3b_config,
)
from spy_market_agent.research.phase3b_folds import DisjointFold, construct_disjoint_folds

ROOT = Path(__file__).resolve().parents[2]


def _sessions_and_labels() -> tuple[tuple[date, ...], pd.DataFrame]:
    sessions = XNYSCalendar().sessions_between(date(2018, 1, 2), date(2025, 12, 31))
    rows = [
        {
            "session": sessions[index],
            "entry_session": sessions[index + 1],
            "exit_session": sessions[index + 6],
            "target": index % 2,
        }
        for index in range(60, 60 + 1190)
    ]
    return sessions, pd.DataFrame(rows)


def _folds(
    labels: pd.DataFrame, sessions: tuple[date, ...]
) -> tuple[tuple[DisjointFold, ...], int]:
    return construct_disjoint_folds(
        labels,
        protected_first_session=sessions[1400],
        source_sessions=sessions,
    )


def test_disjoint_folds_have_actual_session_purge_and_reproduce() -> None:
    sessions, labels = _sessions_and_labels()
    holiday_prediction = date(2020, 7, 2)
    assert date(2020, 7, 3) not in sessions
    assert (sessions[sessions.index(holiday_prediction) + 6] - holiday_prediction).days > 6
    folds, remainder = _folds(labels, sessions)
    rerun, rerun_remainder = _folds(labels, sessions)
    assert folds == rerun
    assert remainder == rerun_remainder
    assert len(folds) == 3
    seen: set[int] = set()
    for index, fold in enumerate(folds):
        assert len(fold.purge_rows) == 6
        assert fold.training_last_exit == fold.purge_sessions[-1]
        assert fold.training_last_exit < fold.assessment_first_prediction
        assert seen.isdisjoint(fold.assessment_rows)
        seen.update(fold.assessment_rows)
        if index:
            assert folds[index - 1].assessment_last_exit < fold.assessment_first_prediction
            assert folds[index - 1].assessment_rows[-1] in fold.training_rows
    assert (
        sessions.index(folds[0].training_last_exit)
        - sessions.index(folds[0].training_last_prediction)
        == 6
    )


def test_folds_fail_when_horizon_or_protected_boundary_is_wrong() -> None:
    sessions, labels = _sessions_and_labels()
    bad = labels.copy(deep=True)
    bad.loc[755, "exit_session"] = sessions[sessions.index(bad.loc[755, "session"]) + 5]
    with pytest.raises(WalkForwardSplitError):
        _folds(bad, sessions)
    with pytest.raises(WalkForwardSplitError):
        construct_disjoint_folds(
            labels,
            protected_first_session=labels.iloc[-1]["exit_session"],
            source_sessions=sessions,
        )


def test_one_class_assessment_is_retained_but_training_must_have_two_classes() -> None:
    sessions, labels = _sessions_and_labels()
    labels.loc[762:887, "target"] = 1
    folds, _ = _folds(labels, sessions)
    assert len(folds[0].assessment_rows) == 126
    labels.loc[:, "target"] = 1
    with pytest.raises(WalkForwardSplitError):
        _folds(labels, sessions)


def test_short_terminal_window_is_explicitly_unassessed() -> None:
    sessions, labels = _sessions_and_labels()
    folds, remainder = _folds(labels.iloc[:940].copy(), sessions)
    assert len(folds) == 1
    assert remainder == 52


def test_config_is_frozen() -> None:
    path = ROOT / "configs/research/phase3b_nonoverlapping.json"
    config = load_phase3b_config(path)
    assert config.execution_authority == "none"
    assert config.purge_rows == 6
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["threshold"] = 0.6
    with pytest.raises(ValueError, match="Phase 3B numeric policies are frozen"):
        type(config).model_validate(payload)


def test_evidence_is_idempotent_and_fails_closed_on_tamper_and_partial_write(
    tmp_path: Path,
) -> None:
    store = ResearchArtifactStore(Path("artifacts/research/phase3b"), repository_root=tmp_path)
    payloads: dict[str, object] = {
        name: {"schema_version": "aq-phase3b-diagnostics-v1"}
        for name in (
            "config.json",
            "lineage.json",
            "fold_manifest.json",
            "metrics.json",
            "diagnostics.json",
        )
    }
    _persist_evidence(store, "aq-p3b-test", payloads, "sanitized report")
    _persist_evidence(store, "aq-p3b-test", payloads, "sanitized report")
    path = store.artifact_path("aq-p3b-test", "metrics.json")
    path.write_text("tampered", encoding="utf-8")
    with pytest.raises(ResearchArtifactError):
        _persist_evidence(store, "aq-p3b-test", payloads, "sanitized report")
    path.unlink()
    with pytest.raises(ResearchArtifactError):
        _persist_evidence(store, "aq-p3b-test", payloads, "sanitized report")


def test_evidence_rejects_symlink_and_path_traversal(tmp_path: Path) -> None:
    store = ResearchArtifactStore(Path("artifacts/research/phase3b"), repository_root=tmp_path)
    with pytest.raises(ResearchArtifactError):
        store.artifact_path("../escape", "metrics.json")
    directory = store.experiment_dir("aq-p3b-link")
    directory.mkdir(parents=True)
    (directory / "metrics.json").symlink_to(tmp_path / "outside.json")
    with pytest.raises(ResearchArtifactError):
        store.write_json("aq-p3b-link", "metrics.json", {})


def test_phase3b_cli_is_manual_and_has_no_operational_options(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from spy_market_agent.research import cli

    calls: list[object] = []

    def fake_run(**kwargs: object) -> SimpleNamespace:
        calls.append(kwargs)
        return SimpleNamespace(experiment_id="aq-p3b-synthetic", fold_count=3)

    monkeypatch.setattr(cli, "run_phase3b", fake_run)
    assert cli_main(["run-phase3b", "--manifest", "data/manifest.json", "--data-root", "data"]) == 0
    assert len(calls) == 1
    assert "promotion_decision=NO CANDIDATE PROMOTION" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="2"):
        cli_main(["run-phase3b", "--manifest", "x", "--data-root", "data", "--submit-order"])


def test_parent_campaign_checksum_and_model_contract_are_verified(tmp_path: Path) -> None:
    config = load_phase3b_config(ROOT / "configs/research/phase3b_nonoverlapping.json")
    store = ResearchArtifactStore(Path("artifacts/research"), repository_root=tmp_path)
    campaign = config.parent_campaign_id
    manifest = {
        "campaign_id": campaign,
        "parent_phase1_dataset_id": config.parent_dataset_id,
        "parent_phase1_canonical_content_checksum": config.parent_canonical_checksum,
        "protected_evaluation_authorized": False,
        "phase2_final_test_available_for_tuning": False,
    }
    models = development_model_registry(random_seed=42).model_dump(mode="json")
    checksums = {
        "experiment_manifest.json": store.write_json(
            campaign, "experiment_manifest.json", manifest
        ),
        "model_registry.json": store.write_json(campaign, "model_registry.json", models),
    }
    store.write_json(
        campaign,
        "artifact_index.json",
        {
            "artifact_schema_version": PHASE3_ARTIFACT_SCHEMA_VERSION,
            "campaign_id": campaign,
            "artifacts": [
                {
                    "relative_path": store.relative_path(store.artifact_path(campaign, name)),
                    "sha256_checksum": checksum,
                    "stable_identity": campaign,
                }
                for name, checksum in checksums.items()
            ],
        },
    )
    assert _verify_parent_campaign(Path("artifacts/research"), tmp_path, config) == manifest
    store.artifact_path(campaign, "model_registry.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ResearchRegistryError):
        _verify_parent_campaign(Path("artifacts/research"), tmp_path, config)


def test_psi_handles_degenerate_training_and_train_derived_bins() -> None:
    constant = _psi_for_feature(
        pd.Series([1.0] * 20), pd.Series([1.0] * 10), bin_count=10, epsilon=0.000001
    )
    assert constant["psi"] is None
    assert constant["undefined_reason"] == "degenerate_training_feature_distribution"
    drift = _psi_for_feature(
        pd.Series([float(value) for value in range(100)]),
        pd.Series([float(value) for value in range(90, 100)]),
        bin_count=10,
        epsilon=0.000001,
    )
    assert drift["psi"] is not None
    assert drift["training_median"] == 49.5
    bins = drift["bins"]
    assert isinstance(bins, tuple)
    assert len(bins) == 10
