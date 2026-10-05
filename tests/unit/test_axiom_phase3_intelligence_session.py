from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from spy_market_agent.intelligence.axiom_session import (
    IntelligenceSession,
    build_intelligence_session,
    intelligence_session_identity,
)
from spy_market_agent.intelligence.contracts import (
    IntelligenceRunIdentity,
    derive_intelligence_run_identity,
)
from spy_market_agent.research.validation_contract import VALIDATION_REQUIRED_EVIDENCE_STAGES
from spy_market_agent.research.validation_engine import (
    ValidationDecision,
    ValidationGateResult,
    ValidationGateStatus,
    ValidationVerdict,
)
from spy_market_agent.research.validation_memory import validation_decision_identity

AS_OF = datetime(2026, 10, 5, 20, 0, tzinfo=UTC)


def _decision(
    verdict: ValidationVerdict = ValidationVerdict.VALIDATED_RESEARCH_CANDIDATE,
    *,
    gate_reason: str = "evidence passed",
) -> ValidationDecision:
    """Build a compact canonical Phase 2 decision fixture."""

    status = (
        ValidationGateStatus.PASSED
        if verdict == ValidationVerdict.VALIDATED_RESEARCH_CANDIDATE
        else ValidationGateStatus.FAILED
    )
    gates = tuple(
        ValidationGateResult(
            stage=stage,
            status=status,
            check_ids=(f"structural:{stage.value}",),
            reasons=(gate_reason,) if status == ValidationGateStatus.PASSED else ("failed",),
        )
        for stage in VALIDATION_REQUIRED_EVIDENCE_STAGES
    )
    return ValidationDecision(
        validation_id="aq-validation-111111111111111111111111",
        experiment_id="aq-exp-222222222222222222222222",
        result_id="aq-result-333333333333333333333333",
        policy_id="phase2-policy-v1",
        policy_digest="a" * 64,
        verdict=verdict,
        gates=gates,
    )


def _intelligence_run(
    *, snapshot_ids: tuple[str, ...] = ("mi0-snapshot-a", "mi0-snapshot-b")
) -> IntelligenceRunIdentity:
    """Build one canonical point-in-time Market Intelligence run fixture."""

    return derive_intelligence_run_identity(
        target_instrument_id="spy-us-equity-etf",
        as_of=AS_OF,
        analysis_profile_id="mi1-spy-analysis-v1",
        snapshot_ids=snapshot_ids,
        code_revision="a0b4385",
        configuration_hash="b" * 64,
    )


def test_session_binds_exact_validation_and_intelligence_lineage_deterministically() -> None:
    """Equivalent inputs retain exact lineage and produce one session identity."""

    decision = _decision()
    intelligence_run = _intelligence_run()

    first = build_intelligence_session(
        decision=decision,
        intelligence_run=intelligence_run,
        invocation_id="owner-session-001",
    )
    second = build_intelligence_session(
        decision=decision,
        intelligence_run=_intelligence_run(snapshot_ids=("mi0-snapshot-b", "mi0-snapshot-a")),
        invocation_id="owner-session-001",
    )

    assert first == second
    assert first.session_id == intelligence_session_identity(first)
    assert first.decision_id == validation_decision_identity(decision)
    assert first.validation_id == decision.validation_id
    assert first.intelligence_run_id == intelligence_run.run_id
    assert first.snapshot_ids == intelligence_run.snapshot_ids
    assert first.as_of == AS_OF
    assert first.invocation_source == "human_requested"
    assert first.execution_authority == "none"


def test_session_identity_changes_with_validation_intelligence_or_invocation_lineage() -> None:
    """Every safety-relevant Phase 2, intelligence, and invocation change alters identity."""

    baseline = build_intelligence_session(
        decision=_decision(),
        intelligence_run=_intelligence_run(),
        invocation_id="owner-session-001",
    )
    changed_invocation = build_intelligence_session(
        decision=_decision(),
        intelligence_run=_intelligence_run(),
        invocation_id="owner-session-002",
    )
    changed_validation = build_intelligence_session(
        decision=_decision(gate_reason="updated evidence"),
        intelligence_run=_intelligence_run(),
        invocation_id="owner-session-001",
    )
    changed_run = build_intelligence_session(
        decision=_decision(),
        intelligence_run=derive_intelligence_run_identity(
            target_instrument_id="spy-us-equity-etf",
            as_of=datetime(2026, 10, 5, 21, 0, tzinfo=UTC),
            analysis_profile_id="mi1-spy-analysis-v1",
            snapshot_ids=("mi0-snapshot-a",),
            code_revision="a0b4385",
            configuration_hash="b" * 64,
        ),
        invocation_id="owner-session-001",
    )

    assert baseline.session_id != changed_invocation.session_id
    assert baseline.decision_id != changed_validation.decision_id
    assert baseline.session_id != changed_validation.session_id
    assert baseline.session_id != changed_run.session_id


def test_session_rejects_intelligence_run_id_that_does_not_match_lineage() -> None:
    """A forged run ID cannot be attached to otherwise-valid intelligence lineage."""

    canonical = _intelligence_run()
    forged = IntelligenceRunIdentity(
        run_id="mi0-run-forged",
        target_instrument_id=canonical.target_instrument_id,
        as_of=canonical.as_of,
        analysis_profile_id=canonical.analysis_profile_id,
        snapshot_ids=canonical.snapshot_ids,
        code_revision=canonical.code_revision,
        configuration_hash=canonical.configuration_hash,
    )

    with pytest.raises(ValueError, match="must match its canonical lineage"):
        build_intelligence_session(
            decision=_decision(),
            intelligence_run=forged,
            invocation_id="owner-session-001",
        )


@pytest.mark.parametrize(
    "verdict",
    [ValidationVerdict.REJECTED, ValidationVerdict.INSUFFICIENT_EVIDENCE],
)
def test_phase3_admission_rejects_non_validated_phase2_decisions(
    verdict: ValidationVerdict,
) -> None:
    """Rejected and incomplete Phase 2 decisions cannot enter Intelligence OS."""

    if verdict == ValidationVerdict.INSUFFICIENT_EVIDENCE:
        gates = tuple(
            ValidationGateResult(
                stage=stage,
                status=ValidationGateStatus.MISSING,
                check_ids=(f"structural:{stage.value}",),
                reasons=("missing",),
            )
            for stage in VALIDATION_REQUIRED_EVIDENCE_STAGES
        )
        decision = ValidationDecision(
            validation_id="aq-validation-111111111111111111111111",
            experiment_id="aq-exp-222222222222222222222222",
            result_id="aq-result-333333333333333333333333",
            policy_id="phase2-policy-v1",
            policy_digest="a" * 64,
            verdict=verdict,
            gates=gates,
        )
    else:
        decision = _decision(verdict)

    with pytest.raises(ValueError, match="validated_research_candidate"):
        build_intelligence_session(
            decision=decision,
            intelligence_run=_intelligence_run(),
            invocation_id="owner-session-001",
        )


def test_session_rejects_tampered_identity() -> None:
    """Stored session identity must remain content-addressed to canonical session fields."""

    session = build_intelligence_session(
        decision=_decision(),
        intelligence_run=_intelligence_run(),
        invocation_id="owner-session-001",
    )
    payload = session.model_dump(mode="python")
    payload["session_id"] = "aq-intel-session-000000000000000000000000"

    with pytest.raises(ValidationError, match="must match canonical"):
        IntelligenceSession.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("invocation_source", "scheduled", "human_requested"),
        ("execution_authority", "paper", "none"),
        ("invocation_id", "../unsafe", "path-safe"),
    ],
)
def test_session_refuses_autonomy_execution_and_unsafe_invocation(
    field: str,
    value: str,
    message: str,
) -> None:
    """Autonomous invocation, execution authority, and unsafe identifiers fail closed."""

    session = build_intelligence_session(
        decision=_decision(),
        intelligence_run=_intelligence_run(),
        invocation_id="owner-session-001",
    )
    payload = session.model_dump(mode="python")
    payload[field] = value

    with pytest.raises(ValidationError, match=message):
        IntelligenceSession.model_validate(payload)


def test_phase3_spec_and_intelligence_package_retain_non_execution_boundary() -> None:
    """Phase 3 stays human-invoked and isolated from execution-capable packages."""

    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    spec = (root / "docs/AQ_PHASE_03_INTELLIGENCE_OS_SPEC.md").read_text(encoding="utf-8")
    assert "invocation_source` has one legal value: `human_requested`" in spec
    assert 'execution_authority = "none"' in spec
    assert "live-money trading" in spec

    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((root / "src/spy_market_agent/intelligence").glob("*.py"))
    )
    for fragment in ("alpaca.trading", "TradingClient", "execution.alpaca_paper", "paper_ops"):
        assert fragment not in source
