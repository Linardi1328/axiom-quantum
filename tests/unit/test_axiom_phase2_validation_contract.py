from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from spy_market_agent.research.experiment_core import (
    ExperimentLifecycleState,
    ExperimentOutcome,
    ExperimentResult,
    StrategyResearchState,
)
from spy_market_agent.research.validation_contract import (
    VALIDATION_REQUIRED_EVIDENCE_STAGES,
    ValidationCase,
    ValidationEvidenceRef,
    ValidationEvidenceSourceKind,
    ValidationStage,
    build_validation_case,
    validation_case_identity,
)

COMPLETED_AT = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


def _candidate_result(
    *, strategy_state: StrategyResearchState = StrategyResearchState.VALIDATION_CANDIDATE
) -> ExperimentResult:
    return ExperimentResult(
        experiment_id="aq-exp-111111111111111111111111",
        lifecycle_state=ExperimentLifecycleState.COMPLETED,
        outcome=ExperimentOutcome.COMPLETED,
        strategy_state=strategy_state,
        summary="Candidate research completed.",
        conclusion="Candidate may enter validation.",
        metric_snapshot={"expectancy": 0.01},
        completed_at=COMPLETED_AT,
    )


def _evidence(
    stage: ValidationStage,
    *,
    suffix: str,
    source_kind: ValidationEvidenceSourceKind = ValidationEvidenceSourceKind.RESEARCH_ARTIFACT,
) -> ValidationEvidenceRef:
    return ValidationEvidenceRef(
        stage=stage,
        source_kind=source_kind,
        evidence_id=f"evidence-{suffix}",
        source_id=f"source-{suffix}",
        checksum=(suffix[0] if suffix[0] in "abcdef" else "a") * 64,
    )


def test_validation_case_identity_is_order_independent_and_tracks_missing_stages() -> None:
    result = _candidate_result()
    backtest = _evidence(ValidationStage.BACKTEST, suffix="b")
    hypothesis = _evidence(ValidationStage.HYPOTHESIS, suffix="a")

    first = build_validation_case(
        result=result,
        policy_id="phase2-policy-v1",
        evidence=(backtest, hypothesis),
    )
    second = build_validation_case(
        result=result,
        policy_id="phase2-policy-v1",
        evidence=(hypothesis, backtest),
    )

    assert first == second
    assert first.validation_id == validation_case_identity(first)
    assert first.evidence == (hypothesis, backtest)
    assert first.evidenced_stages == (ValidationStage.HYPOTHESIS, ValidationStage.BACKTEST)
    assert first.missing_stages == VALIDATION_REQUIRED_EVIDENCE_STAGES[2:]
    assert first.execution_authority == "none"


def test_validation_case_identity_changes_with_scientific_evidence() -> None:
    result = _candidate_result()
    baseline = build_validation_case(
        result=result,
        policy_id="phase2-policy-v1",
        evidence=(_evidence(ValidationStage.HYPOTHESIS, suffix="a"),),
    )
    changed = build_validation_case(
        result=result,
        policy_id="phase2-policy-v1",
        evidence=(_evidence(ValidationStage.HYPOTHESIS, suffix="b"),),
    )

    assert baseline.validation_id != changed.validation_id


def test_validation_case_requires_validation_candidate_source_result() -> None:
    with pytest.raises(ValueError, match="validation_candidate"):
        build_validation_case(
            result=_candidate_result(strategy_state=StrategyResearchState.RESEARCH_ONLY),
            policy_id="phase2-policy-v1",
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("evidence_id", "../bad", "path-safe"),
        ("source_id", "bad/source", "path-safe"),
        ("checksum", "not-a-checksum", "SHA-256"),
    ],
)
def test_validation_evidence_rejects_unsafe_identifiers_and_checksums(
    field: str,
    value: str,
    message: str,
) -> None:
    payload: dict[str, object] = {
        "stage": ValidationStage.HYPOTHESIS,
        "source_kind": ValidationEvidenceSourceKind.CANONICAL_EXPERIMENT,
        "evidence_id": "hypothesis-definition",
        "source_id": "aq-exp-111111111111111111111111",
        "checksum": "a" * 64,
    }
    payload[field] = value

    with pytest.raises(ValidationError, match=message):
        ValidationEvidenceRef.model_validate(payload)


def test_validation_evidence_rejects_candidate_decision_as_input_stage() -> None:
    with pytest.raises(ValidationError, match="output stage"):
        _evidence(ValidationStage.CANDIDATE_DECISION, suffix="a")


def test_validation_case_rejects_duplicate_stage_evidence_keys() -> None:
    evidence = _evidence(ValidationStage.HYPOTHESIS, suffix="a")
    with pytest.raises(ValueError, match="must be unique"):
        build_validation_case(
            result=_candidate_result(),
            policy_id="phase2-policy-v1",
            evidence=(evidence, evidence),
        )


def test_validation_case_rejects_tampered_identity_and_execution_authority() -> None:
    case = build_validation_case(
        result=_candidate_result(),
        policy_id="phase2-policy-v1",
        evidence=(_evidence(ValidationStage.HYPOTHESIS, suffix="a"),),
    )
    payload = case.model_dump(mode="python")
    payload["validation_id"] = "aq-validation-000000000000000000000000"
    with pytest.raises(ValidationError, match="must match canonical"):
        ValidationCase.model_validate(payload)

    payload = case.model_dump(mode="python")
    payload["execution_authority"] = "paper"
    with pytest.raises(ValidationError):
        ValidationCase.model_validate(payload)
