"""Synthetic-only runner verification; never loads market data or fitted artifacts."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import numpy as np
import pandas as pd
import pytest
from sklearn.pipeline import Pipeline

from spy_market_agent.research import phase3c, phase3c_fixture
from spy_market_agent.research.phase3c_fixture import (
    ELIGIBLE_SOURCE_ROWS,
    _generate_fixture,
    _prepare_fixture,
    _SyntheticFixture,
)
from spy_market_agent.research.phase3c_reporting import CANDIDATE, MODEL_NAMES
from spy_market_agent.research.qqq_iwm_policy import BASELINE_COLUMNS, CANDIDATE_COLUMNS


@pytest.fixture(scope="module")
def fixture() -> _SyntheticFixture:
    return _generate_fixture()


@pytest.fixture(scope="module")
def reference(fixture: _SyntheticFixture) -> phase3c.SyntheticExperimentResult:
    return phase3c._evaluate_fixture(fixture)


def test_fixture_and_entire_result_are_reproducible(
    fixture: _SyntheticFixture,
    reference: phase3c.SyntheticExperimentResult,
) -> None:
    assert fixture == _generate_fixture()
    reproduced = phase3c.run_synthetic_experiment()
    assert reproduced == reference
    assert reproduced.result_checksum == reference.result_checksum
    assert reference.evidence_kind == "synthetic_fixture"
    assert reference.execution_authority == "none"
    assert reference.disposition == "NO CANDIDATE PROMOTION"
    payload = json.loads(reference.payload_json)
    assert payload["dimensions"] == {
        "generated_source_rows": 1200,
        "eligible_source_rows": 1182,
        "excluded_protected_source_rows": 18,
        "supervised_rows": 1116,
        "fold_count": 3,
        "unassessed_rows": 0,
    }
    lineage = payload["lineage"]
    assert lineage["runtime"]["git_commit"]
    assert len(lineage["runtime"]["source_checksums"]) >= 17
    assert lineage["configuration"]["random_seed"] == 42
    assert lineage["configuration"]["commission_bps_per_side"] == "0.125"
    assert lineage["configuration"]["slippage_bps_per_side"] == "0.25"
    assert tuple(lineage["models"][CANDIDATE]["feature_columns"]) == CANDIDATE_COLUMNS
    candidate_definition = lineage["models"][CANDIDATE]["definition"]
    assert (
        candidate_definition == lineage["models"]["logistic_regression_research_00"]["definition"]
    )
    params = dict(candidate_definition["parameters"])
    assert params["scaler"] == "StandardScaler"
    assert params["classifier.C"] == 0.1
    assert params["classifier.penalty"] == "l2"
    assert params["classifier.solver"] == "liblinear"
    assert params["classifier.class_weight"] is None
    assert params["classifier.random_state"] == 42


def test_disjoint_folds_exact_chronology_and_all_five_comparisons(
    fixture: _SyntheticFixture,
    reference: phase3c.SyntheticExperimentResult,
) -> None:
    payload = json.loads(reference.payload_json)
    seen: set[str] = set()
    previous_exit: str | None = None
    assert [len(f["membership"]["assessment_rows"]) for f in payload["folds"]] == [126, 126, 90]
    assert [len(f["membership"]["training_rows"]) for f in payload["folds"]] == [756, 888, 1020]
    for fold in payload["folds"]:
        membership = fold["membership"]
        assessments = membership["assessment_sessions"]
        assert len(membership["purge_rows"]) == 6
        assert membership["training_rows"] == list(range(len(membership["training_rows"])))
        assert membership["training_last_exit"] < assessments[0]
        assert membership["training_outcome_purge_verified"]
        assert fold["previous_assessment_outcome_purge_verified"]
        if previous_exit:
            assert previous_exit < assessments[0]
        previous_exit = membership["assessment_last_exit"]
        assert seen.isdisjoint(assessments)
        seen.update(assessments)
        assert set(fold["models"]) == set(MODEL_NAMES)
        for population in ("training", "purge", "assessment"):
            for prediction, entry, exit_session in zip(
                membership[f"{population}_sessions"],
                membership[f"{population}_entry_sessions"],
                membership[f"{population}_exit_sessions"],
                strict=True,
            ):
                index = next(
                    i for i, s in enumerate(fixture.sessions) if s.isoformat() == prediction
                )
                assert fixture.sessions[index + 1].isoformat() == entry
                assert fixture.sessions[index + 6].isoformat() == exit_session
                assert exit_session < fixture.sessions[ELIGIBLE_SOURCE_ROWS].isoformat()
        for model in fold["models"].values():
            assert model["row_count"] == len(assessments)
            assert model["positive_count"] + model["negative_count"] == len(assessments)
            assert len(model["reliability_bins"]) > 0
        candidate = fold["models"][CANDIDATE]
        for control in MODEL_NAMES[:-1]:
            control_score = fold["models"][control]
            assert (candidate["positive_count"], candidate["negative_count"]) == (
                control_score["positive_count"],
                control_score["negative_count"],
            )
            for metric, delta in fold["candidate_minus_control"][control].items():
                assert delta["value"] == pytest.approx(
                    candidate["metrics"][metric]["value"]
                    - control_score["metrics"][metric]["value"]
                )
    for model in payload["aggregates"]["models"].values():
        for aggregate in model.values():
            assert aggregate["defined_fold_count"] + aggregate["undefined_fold_count"] == 3
            assert aggregate["median"]["value"] is not None
            assert aggregate["standard_deviation"]["value"] >= 0


def test_train_only_preprocessing_prevalence_and_exact_model_populations(
    fixture: _SyntheticFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, supervised = _prepare_fixture(fixture)
    fitted: list[object] = []
    training: list[tuple[str, tuple[Any, ...], tuple[int, ...]]] = []
    assessment: list[tuple[Any, ...]] = []
    original_fit = phase3c.fit_development_estimator
    original_predict = phase3c.development_positive_probabilities

    def fit(estimator: object, X: pd.DataFrame, y: pd.Series, *, model_name: str) -> object:
        assert tuple(X.columns) == (
            CANDIDATE_COLUMNS if model_name == CANDIDATE else BASELINE_COLUMNS
        )
        assert X.index.equals(y.index)
        training.append((model_name, tuple(X.index), tuple(y)))
        assert all(estimator is not previous for previous in fitted)
        fitted.append(estimator)
        result = original_fit(estimator, X, y, model_name=model_name)
        if isinstance(result, Pipeline):
            scaler = result.named_steps["scaler"]
            assert scaler.n_samples_seen_ == len(X)
            np.testing.assert_allclose(scaler.mean_, X.mean().to_numpy(), rtol=1e-12, atol=1e-12)
        return result

    def predict(estimator: object, X: pd.DataFrame) -> tuple[float, ...]:
        assessment.append(tuple(X.index))
        return original_predict(estimator, X)

    monkeypatch.setattr(phase3c, "fit_development_estimator", fit)
    monkeypatch.setattr(phase3c, "development_positive_probabilities", predict)
    payload = json.loads(phase3c._evaluate_fixture(fixture).payload_json)
    assert len(fitted) == 12
    for i, fold in enumerate(payload["folds"]):
        train_records = training[i * 4 : (i + 1) * 4]
        assert [record[0] for record in train_records] == list(MODEL_NAMES[1:])
        assert all(record[1:] == train_records[0][1:] for record in train_records)
        assert all(sessions == assessment[i * 4] for sessions in assessment[i * 4 : (i + 1) * 4])
        rows = fold["membership"]["training_rows"]
        prevalence = float(supervised.labels.iloc[rows]["target"].mean())
        score = fold["models"]["training_prevalence"]
        for model in fold["models"].values():
            assert model["metrics"]["training_prevalence"]["value"] == prevalence
        assert score["predicted_positive_rate"] == float(prevalence >= 0.5)
        assert len(score["reliability_bins"]) == 1
        assert score["reliability_bins"][0]["mean_probability"] == pytest.approx(prevalence)


def _increasing_segment(fixture: _SyntheticFixture, start: int, end: int) -> _SyntheticFixture:
    rows = list(fixture.spy_rows)
    price = rows[start][0]
    for i in range(start, end):
        price *= 1.002 + 0.0001 * np.sin(i)
        rows[i] = (price, price * 1.002, price / 1.002, price * 1.0001, rows[i][4])
    return replace(fixture, spy_rows=tuple(rows))


def test_one_class_assessment_preserves_undefined_metrics(
    fixture: _SyntheticFixture,
) -> None:
    # First assessment predictions are source rows 822..947, exits through 953.
    mutated = _increasing_segment(fixture, 823, 954)
    payload = json.loads(phase3c._evaluate_fixture(mutated).payload_json)
    for model in payload["folds"][0]["models"].values():
        assert model["positive_count"] == 126
        assert model["negative_count"] == 0
        for name in ("roc_auc", "balanced_accuracy", "negative_recall"):
            assert model["metrics"][name]["value"] is None
            assert model["metrics"][name]["undefined_reason"]
        assert model["metrics"]["log_loss"]["value"] is not None
    for model in payload["aggregates"]["models"].values():
        assert model["roc_auc"]["undefined_fold_count"] >= 1
    for control in payload["folds"][0]["candidate_minus_control"].values():
        assert control["roc_auc"]["value"] is None


def test_one_class_training_rejected_before_fit(
    fixture: _SyntheticFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*_args: Any, **_kwargs: Any) -> object:
        pytest.fail("model constructed before training class check")

    monkeypatch.setattr(phase3c, "build_development_estimator", forbidden)
    with pytest.raises(ValueError, match="training requires both classes"):
        phase3c._evaluate_fixture(_increasing_segment(fixture, 0, 822))


@pytest.mark.parametrize("invalid", ["missing_context", "bad_proof"])
def test_ineligible_context_denied_before_any_fit(
    fixture: _SyntheticFixture, monkeypatch: pytest.MonkeyPatch, invalid: str
) -> None:
    original = phase3c._context_history

    def history(*args: Any) -> Any:
        result = original(*args)
        if invalid == "missing_context":
            return replace(result, bundles=result.bundles[:-1])
        bundle = result.bundles[0]
        proof = replace(bundle.proofs[0], permitted_usage_evidence="UNVERIFIED")
        return replace(
            result,
            bundles=(replace(bundle, proofs=(proof, *bundle.proofs[1:])), *result.bundles[1:]),
        )

    def forbidden(*_args: Any, **_kwargs: Any) -> object:
        pytest.fail("model constructed before context eligibility")

    monkeypatch.setattr(phase3c, "_context_history", history)
    monkeypatch.setattr(phase3c, "build_development_estimator", forbidden)
    with pytest.raises(
        ValueError, match=r"synthetic_context_ineligible_before_fit|unverified_source_evidence"
    ):
        phase3c._evaluate_fixture(fixture)


def test_model_fit_failure_returns_no_partial_experiment(
    fixture: _SyntheticFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = phase3c.fit_development_estimator
    calls: list[str] = []

    def fit(*args: Any, **kwargs: Any) -> object:
        calls.append(kwargs["model_name"])
        if kwargs["model_name"] == CANDIDATE:
            raise RuntimeError("injected synthetic failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(phase3c, "fit_development_estimator", fit)
    with pytest.raises(phase3c.SyntheticExperimentError, match="synthetic_model_comparison_failed"):
        phase3c._evaluate_fixture(fixture)
    assert calls == list(MODEL_NAMES[1:])


def test_future_mutation_cannot_change_earlier_fold_predictions_or_metrics(
    fixture: _SyntheticFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = phase3c.development_positive_probabilities
    probabilities: list[tuple[float, ...]] = []

    def predict(*args: Any) -> tuple[float, ...]:
        values = original(*args)
        probabilities.append(values)
        return values

    monkeypatch.setattr(phase3c, "development_positive_probabilities", predict)
    first = phase3c._evaluate_fixture(fixture)
    first_payload = json.loads(first.payload_json)
    first_predictions = probabilities[:4]
    final_exit = first_payload["folds"][0]["membership"]["assessment_last_exit"]
    start = next(i for i, s in enumerate(fixture.sessions) if s.isoformat() == final_exit) + 1
    # Mutate every source only AFTER the earlier fold's last outcome is observed.
    rows = tuple(
        row if i < start else (row[0] * 1.2, row[1] * 1.2, row[2] * 1.2, row[3] * 1.2, row[4])
        for i, row in enumerate(fixture.spy_rows)
    )
    changed = replace(
        fixture,
        spy_rows=rows,
        qqq_closes=tuple(x if i < start else x * 1.3 for i, x in enumerate(fixture.qqq_closes)),
        iwm_closes=tuple(x if i < start else x * 1.4 for i, x in enumerate(fixture.iwm_closes)),
    )
    probabilities.clear()
    second = phase3c._evaluate_fixture(changed)
    assert second.experiment_id != first.experiment_id
    assert probabilities[:4] == first_predictions
    assert json.loads(second.payload_json)["folds"][0] == first_payload["folds"][0]


def test_protected_source_removed_before_label_builder(
    fixture: _SyntheticFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = phase3c_fixture.build_forward_label_set
    calls: list[int] = []

    def labels(batch: Any, **kwargs: Any) -> Any:
        calls.append(len(batch.data))
        assert batch.data["session"].iloc[-1] < fixture.sessions[ELIGIBLE_SOURCE_ROWS]
        return original(batch, **kwargs)

    monkeypatch.setattr(phase3c_fixture, "build_forward_label_set", labels)
    _prepare_fixture(fixture)
    assert calls == [ELIGIBLE_SOURCE_ROWS]
