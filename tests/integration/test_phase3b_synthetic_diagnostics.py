from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from spy_market_agent.datasets.labels import build_forward_label_set
from spy_market_agent.datasets.models import LabelSet, TradingCostAssumptions
from spy_market_agent.market_data.models import MarketDataBatch
from spy_market_agent.research import phase3b
from spy_market_agent.research.campaign import ResearchCampaignConfig
from spy_market_agent.research.errors import ResearchRegistryError
from spy_market_agent.research.features import (
    build_research_feature_matrix,
    build_research_supervised_dataset,
)
from spy_market_agent.research.phase2_isolation import apply_phase2_final_test_session_isolation
from spy_market_agent.research.phase3b import evaluate_and_store_phase3b, load_phase3b_config
from spy_market_agent.research.phase3b_folds import construct_disjoint_folds
from spy_market_agent.research.runner import _load_verified_research_market_data
from unit.v2_phase2_helpers import write_synthetic_phase1_dataset

ROOT = Path(__file__).resolve().parents[2]


def test_full_phase3b_synthetic_evaluation_is_repeatable_and_descriptive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_path = write_synthetic_phase1_dataset(
        tmp_path, start=date(2019, 1, 2), end=date(2025, 12, 31)
    )
    manifest, parent = _load_verified_research_market_data(
        manifest_path=manifest_path,
        data_root=Path("data"),
        repository_root=tmp_path,
        config=ResearchCampaignConfig(),
    )
    sliced, boundary = apply_phase2_final_test_session_isolation(
        manifest=manifest, market_data=parent, global_feature_warmup_rows=60
    )
    timestamp = manifest.retrieval_timestamp
    features = build_research_feature_matrix(sliced, created_at=timestamp)
    labels = build_forward_label_set(
        sliced,
        cost_assumptions=TradingCostAssumptions(
            commission_bps_per_side=Decimal("0.125"),
            slippage_bps_per_side=Decimal("0.25"),
        ),
        created_at=timestamp,
    )
    supervised = build_research_supervised_dataset(features, labels, created_at=timestamp)
    folds, unassessed = construct_disjoint_folds(
        supervised.labels,
        protected_first_session=boundary.phase2_final_test_first_prediction_session,
        source_sessions=tuple(sliced.data["session"].to_list()),
    )
    original_fit = phase3b.fit_development_estimator
    fitted_rows: list[int] = []

    def checked_fit(estimator: object, X: pd.DataFrame, y: pd.Series, *, model_name: str) -> object:
        fitted_rows.append(len(X))
        assert len(X) == len(y)
        return original_fit(estimator, X, y, model_name=model_name)

    monkeypatch.setattr(phase3b, "fit_development_estimator", checked_fit)
    config = load_phase3b_config(ROOT / "configs/research/phase3b_nonoverlapping.json")
    first = evaluate_and_store_phase3b(
        config=config,
        market_data=sliced,
        supervised=supervised,
        boundary=boundary,
        folds=folds,
        unassessed_rows=unassessed,
        artifact_root=Path("artifacts/research"),
        repository_root=tmp_path,
    )
    second = evaluate_and_store_phase3b(
        config=config,
        market_data=sliced,
        supervised=supervised,
        boundary=boundary,
        folds=folds,
        unassessed_rows=unassessed,
        artifact_root=Path("artifacts/research"),
        repository_root=tmp_path,
    )
    assert first.experiment_id == second.experiment_id
    assert fitted_rows == [len(fold.training_rows) for fold in folds for _ in range(3)] * 2
    assert first.fold_count == len(folds)
    # Direct evaluation must also fail closed if a provided fold was corrupted.
    corrupted_purge = (
        replace(folds[0], purge_sessions=folds[0].purge_sessions[:-1]),
        *folds[1:],
    )
    corrupted_interwindow = (
        folds[0],
        replace(
            folds[1],
            assessment_first_prediction=folds[0].assessment_last_exit,
        ),
        *folds[2:],
    )
    for invalid_folds in (corrupted_purge, corrupted_interwindow):
        with pytest.raises(ResearchRegistryError):
            evaluate_and_store_phase3b(
                config=config,
                market_data=sliced,
                supervised=supervised,
                boundary=boundary,
                folds=invalid_folds,
                unassessed_rows=unassessed,
                artifact_root=Path("artifacts/research"),
                repository_root=tmp_path,
            )
    fold_manifest = json.loads((first.artifact_directory / "fold_manifest.json").read_text())
    assert len(fold_manifest["folds"]) == len(folds)
    observed_assessments: set[str] = set()
    for index, (record, fold) in enumerate(zip(fold_manifest["folds"], folds, strict=True)):
        expected_training = [
            supervised.labels.iloc[row]["session"].isoformat() for row in fold.training_rows
        ]
        expected_assessment = [
            supervised.labels.iloc[row]["session"].isoformat() for row in fold.assessment_rows
        ]
        assert record["training_prediction_sessions"] == expected_training
        assert record["assessment_prediction_sessions"] == expected_assessment
        assert record["purge_sessions"] == [value.isoformat() for value in fold.purge_sessions]
        assert record["training_to_assessment_outcome_purge_verified"] is True
        assert record["assessment_to_assessment_outcome_purge_verified"] is True
        assert record["previous_assessment_last_exit"] == (
            folds[index - 1].assessment_last_exit.isoformat() if index else None
        )
        assert record["training_last_exit"] < record["assessment_first_prediction"]
        if index:
            assert record["previous_assessment_last_exit"] < record["assessment_first_prediction"]
        assert observed_assessments.isdisjoint(expected_assessment)
        observed_assessments.update(expected_assessment)
    metrics = json.loads((first.artifact_directory / "metrics.json").read_text())
    diagnostics = json.loads((first.artifact_directory / "diagnostics.json").read_text())
    assert len(metrics["fold_results"]) == len(folds)
    assert set(metrics["fold_results"][0]["metrics"]) == {
        "training_prevalence",
        "logistic_regression_research_00",
        "logistic_regression",
        "gradient_boosting",
    }
    report = (first.artifact_directory / "findings.md").read_text()
    assert "NO CANDIDATE PROMOTION" in report
    assert "Candidate negative recall" in report
    assert "Fixed boosting AUC" in report
    first_diagnostic = diagnostics["fold_diagnostics"][0]
    assert first_diagnostic["regimes"]["trend_200"]
    assert first_diagnostic["regimes"]["drawdown"]
    assert all(
        "small_sample" in cell for cells in first_diagnostic["regimes"].values() for cell in cells
    )
    assert all(
        "training_median" in drift and "psi" in drift and "assessment_missingness" in drift
        for drift in first_diagnostic["feature_drift"].values()
    )
    assert not any(tmp_path.rglob("*predictions*.json"))


def test_manual_runner_isolates_protected_source_before_labels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_path = write_synthetic_phase1_dataset(
        tmp_path, start=date(2019, 1, 2), end=date(2025, 12, 31)
    )
    manifest, parent = _load_verified_research_market_data(
        manifest_path=manifest_path,
        data_root=Path("data"),
        repository_root=tmp_path,
        config=ResearchCampaignConfig(),
    )
    config = load_phase3b_config(ROOT / "configs/research/phase3b_nonoverlapping.json")
    fake_manifest = manifest.model_copy(
        update={
            "dataset_id": config.parent_dataset_id,
            "canonical_content_checksum": config.parent_canonical_checksum,
        }
    )
    _, boundary = apply_phase2_final_test_session_isolation(
        manifest=fake_manifest, market_data=parent, global_feature_warmup_rows=60
    )
    monkeypatch.setattr(
        phase3b,
        "_load_verified_research_market_data",
        lambda **_kwargs: (fake_manifest, parent),
    )
    monkeypatch.setattr(
        phase3b,
        "_verify_parent_campaign",
        lambda *_args: {"phase2_final_test_exclusion_boundary": boundary.model_dump(mode="json")},
    )
    original_build_labels = phase3b.build_forward_label_set
    observed_slices: list[date] = []

    def checked_build_labels(
        source: MarketDataBatch,
        *,
        cost_assumptions: TradingCostAssumptions,
        created_at: datetime,
    ) -> LabelSet:
        last_session = source.data["session"].iloc[-1]
        observed_slices.append(last_session)
        assert last_session < boundary.phase2_final_test_first_prediction_session
        return original_build_labels(
            source, cost_assumptions=cost_assumptions, created_at=created_at
        )

    monkeypatch.setattr(phase3b, "build_forward_label_set", checked_build_labels)
    result = phase3b.run_phase3b(
        manifest_path=manifest_path,
        data_root=Path("data"),
        config_path=ROOT / "configs/research/phase3b_nonoverlapping.json",
        artifact_root=Path("artifacts/research"),
        repository_root=tmp_path,
    )
    assert result.fold_count >= 3
    assert len(observed_slices) == 1
    assert result.artifact_directory.is_dir()
