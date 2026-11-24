"""Synthetic-only integration tests of the economic protocol inside the single-fit evaluator.

Uses the same internally generated world as ``test_retrospective_study``; no real market data,
credentials, network, provider, protected label or model beyond the existing frozen comparison.
"""

from __future__ import annotations

# ruff: noqa: F811  (fixtures are reused from the frozen study tests)
import dataclasses
import hashlib
import inspect
import json
from pathlib import Path
from typing import Any

import pytest

from integration.test_retrospective_study import World, _run, reference, world  # noqa: F401
from spy_market_agent.research import retrospective_adapter as adapter
from spy_market_agent.research import retrospective_economics as eco
from spy_market_agent.research import retrospective_study as study
from spy_market_agent.research.phase3c_reporting import CANDIDATE, MODEL_NAMES

ROOT = Path(__file__).resolve().parents[2]
# SHA-256 of ``evaluate_screen``'s source on main before D11: the frozen classification screen.
FROZEN_SCREEN_SOURCE_SHA256 = "ec7aedd1e5efb151cc1a25443b13adb348569407532cb912654fc14ba3a702aa"
EXPECTED_FITS = 28  # 7 folds x 4 fitted models; the prevalence control is not fitted


def _payload(result: study.RetrospectiveStudyResult) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(result.payload_json)
    return payload


def test_economics_are_bound_into_the_result_and_identity(
    world: World, reference: study.RetrospectiveStudyResult, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _payload(reference)
    economics = payload["economics"]
    identity = payload["lineage"]["economic_policy"]
    assert identity["specification_sha256"] == eco.ECONOMIC_SPEC_SHA256
    assert identity["lock_sha256"] == eco.ECONOMIC_LOCK_SHA256
    assert economics["policy"] == identity
    assert economics["evidence_label"] == "adjusted_price_index_hypothetical"
    assert economics["execution_authority"] == "none"
    assert economics["disposition"] == "NO CANDIDATE PROMOTION"
    assert economics["candidate"] == CANDIDATE
    assert set(economics["models"]) == set(MODEL_NAMES)
    assert economics["decisional"] == (payload["screen"]["exploratory_screen"] == "CRITERIA_MET")
    assert economics["designated_comparator"] == payload["screen"]["strongest_fitted_comparator"]
    assert "adjusted-price-index hypothetical" in payload["limitations"]
    assert reference.execution_authority == "none"
    assert reference.disposition == "NO CANDIDATE PROMOTION"
    # Changing the bound policy identity changes the experiment identity.
    real_identity = eco.policy_identity
    monkeypatch.setattr(
        eco, "policy_identity", lambda policy: {**real_identity(policy), "lock_sha256": "0" * 64}
    )
    assert _run(world).experiment_id != reference.experiment_id


def test_policy_digest_mismatch_or_conflict_aborts_before_any_fit(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    fits: list[int] = []
    real = study.fit_development_estimator

    def counting(*args: Any, **kwargs: Any) -> Any:
        fits.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(study, "fit_development_estimator", counting)
    with monkeypatch.context() as patch:
        patch.setattr(eco, "ECONOMIC_LOCK_SHA256", "0" * 64)
        with pytest.raises(study.RetrospectiveStudyError, match="economic_lock_digest_mismatch"):
            _run(world)
    with monkeypatch.context() as patch:
        patch.setattr(eco, "ECONOMIC_SPEC_SHA256", "0" * 64)
        with pytest.raises(
            study.RetrospectiveStudyError, match="economic_specification_digest_mismatch"
        ):
            _run(world)
    policy = eco.load_economic_policy(ROOT)
    with monkeypatch.context() as patch:
        patch.setattr(
            eco,
            "load_economic_policy",
            lambda _root: dataclasses.replace(policy, signal_threshold=0.6),
        )
        with pytest.raises(
            study.RetrospectiveStudyError,
            match="economic_policy_conflicts_with_frozen_configuration",
        ):
            _run(world)
    assert fits == []  # all three aborts happened before the first model was fitted


def test_no_additional_fit_and_economics_reuse_the_same_out_of_fold_probabilities(
    world: World, reference: study.RetrospectiveStudyResult, monkeypatch: pytest.MonkeyPatch
) -> None:
    fits: list[str] = []
    scored: dict[tuple[str, str], tuple[float, ...]] = {}
    real_fit = study.fit_development_estimator
    real_score = study._score
    real_eval = eco.evaluate_economics
    seen: dict[str, Any] = {}

    def counting_fit(*args: Any, **kwargs: Any) -> Any:
        fits.append(kwargs["model_name"])
        return real_fit(*args, **kwargs)

    def spying_score(
        name: str, fold_id: str, targets: Any, probabilities: Any, prevalence: float
    ) -> Any:
        scored[(fold_id, name)] = tuple(float(p) for p in probabilities)
        return real_score(name, fold_id, targets, probabilities, prevalence)

    def spying_eval(policy: Any, inputs: Any, **kwargs: Any) -> Any:
        seen["fits_before"] = len(fits)
        seen["inputs"] = inputs
        result = real_eval(policy, inputs, **kwargs)
        seen["fits_after"] = len(fits)
        return result

    monkeypatch.setattr(study, "fit_development_estimator", counting_fit)
    monkeypatch.setattr(study, "_score", spying_score)
    monkeypatch.setattr(eco, "evaluate_economics", spying_eval)
    result = _run(world)
    assert result == reference  # identical result with every spy call-through
    assert len(fits) == EXPECTED_FITS
    assert seen["fits_before"] == seen["fits_after"] == EXPECTED_FITS  # economics fitted nothing
    for index, fold in enumerate(seen["inputs"]):
        for name in MODEL_NAMES:
            assert tuple(fold.probabilities[name]) == scored[(f"retro-fold-{index}", name)]


def test_economic_windows_use_only_eligible_sessions(
    world: World, reference: study.RetrospectiveStudyResult, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}
    real = study._evaluate_economics

    def spy(
        policy: Any,
        folds: Any,
        probabilities: Any,
        labels: Any,
        positions: Any,
        opens: Any,
        closes: Any,
        screen: Any,
    ) -> Any:
        captured.update(rows=len(opens), last_position=int(positions.max()), folds=len(folds))
        return real(policy, folds, probabilities, labels, positions, opens, closes, screen)

    monkeypatch.setattr(study, "_evaluate_economics", spy)
    assert _run(world) == reference
    protected_rows = len(world.parent.data) - len(world.sessions)
    assert protected_rows > 0  # the synthetic parent really contains protected rows
    assert captured["rows"] == len(world.sessions) == 1708  # price arrays hold eligible rows only
    assert captured["last_position"] + study.EXIT_OFFSET_SESSIONS < captured["rows"]


def test_label_price_misalignment_aborts_the_whole_evaluation(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = study._evaluate_economics

    def shifted(
        policy: Any,
        folds: Any,
        probabilities: Any,
        labels: Any,
        positions: Any,
        opens: Any,
        closes: Any,
        screen: Any,
    ) -> Any:
        # Prices that no longer match the frozen labels must abort, never be evaluated.
        return real(
            policy, folds, probabilities, labels, positions, opens[::-1].copy(), closes, screen
        )

    monkeypatch.setattr(study, "_evaluate_economics", shifted)
    with pytest.raises(study.RetrospectiveStudyError, match="economic_label_price_misalignment"):
        _run(world)


def test_classification_screen_and_frozen_configuration_are_unchanged(
    reference: study.RetrospectiveStudyResult,
) -> None:
    source = inspect.getsource(study.evaluate_screen)
    assert hashlib.sha256(source.encode()).hexdigest() == FROZEN_SCREEN_SOURCE_SHA256
    payload = _payload(reference)
    assert payload["screen"]["criteria_id"] == study.CRITERIA_ID == "aq-p3cr-criteria-v1"
    assert payload["lineage"]["configuration"] == json.loads(
        json.dumps(dict(study._FROZEN_CONFIGURATION))
    )
    assert "economic_policy" not in payload["lineage"]["configuration"]
    assert study.STUDY_ID == "aq-p3cr-retrospective-study-v1"
    for constant, value in (
        (study.MIN_VALID_FOLDS, 5),
        (study.MIN_CANDIDATE_MEDIAN_AUC, 0.53),
        (study.MIN_MEDIAN_AUC_IMPROVEMENT, 0.03),
        (study.COMMISSION_BPS_PER_SIDE, "0.125"),
        (study.SLIPPAGE_BPS_PER_SIDE, "0.25"),
        (study.CLASSIFICATION_THRESHOLD, 0.5),
    ):
        assert constant == value


def test_economic_results_are_reproducible_and_sanitized(
    world: World, reference: study.RetrospectiveStudyResult
) -> None:
    first = _payload(reference)["economics"]
    second = _payload(_run(world))["economics"]
    assert first == second

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                assert str(key).lower() not in adapter.FORBIDDEN_OUTPUT_KEYS
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(first)
    assert first["missing_decisive_evidence"] == [] or first["outcome"] in (
        "NOT_EVALUABLE",
        eco.NON_DECISIONAL,
    )


def _report(**overrides: Any) -> adapter.PreflightReport:
    values: dict[str, Any] = {
        "evidence_kind": "synthetic_fixture",
        "git_commit": "a" * 40,
        "python_version": "3.12.14",
        "runtime_fingerprint": "b" * 64,
        "parent_dataset_id": "synthetic-parent-0001",
        "parent_canonical_checksum": "c" * 64,
        "parent_market_data_checksum": "d" * 64,
        "parent_manifest_self_checksum": "e" * 64,
        "campaign_id": "synthetic-campaign-0001",
        "campaign_index_sha256": "1" * 64,
        "campaign_manifest_sha256": "2" * 64,
        "research_slice_checksum": "3" * 64,
        "input_snapshot_sha256": "4" * 64,
        "source_rows": 1708,
        "source_first_session": "2018-01-02",
        "source_last_session": "2024-10-15",
        "prediction_rows": 1642,
        "prediction_first_session": "2018-03-29",
        "prediction_last_session": "2024-10-07",
        "excluded_protected_source_rows": 10,
        "context": (("QQQ", "x", "y", "z"), ("IWM", "x", "y", "z")),
        "economic_specification_sha256": eco.ECONOMIC_SPEC_SHA256,
        "economic_policy_lock_sha256": eco.ECONOMIC_LOCK_SHA256,
    }
    values.update(overrides)
    return adapter.PreflightReport(**values)


def test_single_use_key_is_independent_of_code_revision_and_economic_policy() -> None:
    first = _report()
    revised = _report(git_commit="f" * 40, runtime_fingerprint="9" * 64)
    assert first.digest != revised.digest  # approval granularity binds commit and runtime
    assert first.input_digest == revised.input_digest
    assert adapter._execution_key(first) == adapter._execution_key(revised)
    # The exact policy bytes are bound to the approval digest but never to the logical key.
    other_policy = _report(
        economic_policy_lock_sha256="7" * 64, economic_specification_sha256="8" * 64
    )
    assert first.digest != other_policy.digest
    assert first.input_digest == other_policy.input_digest
    assert adapter._execution_key(first) == adapter._execution_key(other_policy)
    source = inspect.getsource(adapter._execution_key) + inspect.getsource(
        adapter.PreflightReport.input_digest.fget  # type: ignore[attr-defined]
    )
    for token in ("economic", "lock_sha256", "policy", "git_commit", "runtime_fingerprint"):
        assert token not in source
    assert study.STUDY_ID in source or "study.STUDY_ID" in source


def test_adapter_persists_sanitized_economics_and_limitations(
    reference: study.RetrospectiveStudyResult,
) -> None:
    report = _report()
    auth = adapter.ExperimentAuthorization(
        schema_version="aq-p3cr-experiment-authorization-v1",
        authorization_id="synthetic-authorization-0001",
        study_id="aq-p3cr-retrospective-study-v1",
        evidence_kind="synthetic_fixture",
        git_commit="a" * 40,
        report_digest="5" * 64,
        owner_rights_reference_id="owner-local-entitlement-check-0001",
        output_destination=adapter.OUTPUT_ROOTS["synthetic_fixture"],
        execution_authority="none",
        disposition="NO CANDIDATE PROMOTION",
    )
    files = adapter._output_files(reference.payload_json, report, auth)
    metrics = json.loads(files["metrics.json"])
    findings = json.loads(files["findings.json"])
    assert metrics["economics"]["policy"]["lock_sha256"] == eco.ECONOMIC_LOCK_SHA256
    assert findings["economic_outcome"]["evidence_label"] == "adjusted_price_index_hypothetical"
    assert findings["economic_outcome"]["outcome"] == metrics["economics"]["outcome"]
    assert b"adjusted-price-index hypothetical" in files["limitations.md"]
    assert findings["execution_authority"] == "none"
    assert findings["disposition"] == "NO CANDIDATE PROMOTION"
