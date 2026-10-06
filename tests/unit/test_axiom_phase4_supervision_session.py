from __future__ import annotations

from inspect import getsource
from pathlib import Path

import pytest
from tests.unit.test_axiom_phase3_reporting_completion import (
    _brief,
    _decision,
    _record_phase2,
    _store,
)

from spy_market_agent.benchmark.artifacts import sha256_bytes
from spy_market_agent.intelligence.axiom_decision_support import DecisionSupportVerdict
from spy_market_agent.intelligence.axiom_memory import IntelligenceMemoryRegistry
from spy_market_agent.intelligence.axiom_reporting import (
    IntelligenceWorkflowResult,
    intelligence_report_name,
    run_intelligence_os_workflow,
)
from spy_market_agent.research.artifacts import ResearchArtifactStore
from spy_market_agent.research.errors import ResearchRegistryError
from spy_market_agent.supervision import (
    SUPERVISED_SESSION_SCHEMA_VERSION,
    SupervisedSession,
    build_supervised_session,
    supervised_session_identity,
)


def _phase3_result(
    tmp_path: Path,
    *,
    actionable: bool = True,
) -> tuple[ResearchArtifactStore, IntelligenceWorkflowResult]:
    decision = _decision()
    store = _store(tmp_path)
    _record_phase2(store, decision)
    result = run_intelligence_os_workflow(
        decision=decision,
        brief=_brief(actionable=actionable),
        invocation_id="phase4-parent-phase3",
        store=store,
    )
    return store, result


def test_supervised_session_is_deterministic_and_preserves_exact_phase3_lineage(
    tmp_path: Path,
) -> None:
    """One verified Phase 3 parent and human invocation produce one canonical session."""

    store, phase3 = _phase3_result(tmp_path)
    registry = IntelligenceMemoryRegistry(store)

    first = build_supervised_session(
        report=phase3.report,
        invocation_id="human-review-001",
        registry=registry,
    )
    repeated = build_supervised_session(
        report=phase3.report,
        invocation_id="human-review-001",
        registry=registry,
    )

    assert first == repeated
    assert first.schema_version == SUPERVISED_SESSION_SCHEMA_VERSION
    assert first.supervision_session_id == supervised_session_identity(first)
    assert first.report == phase3.report
    assert first.report_id == phase3.report.report_id
    assert first.assessment_id == phase3.report.assessment_id
    assert first.evidence_id == phase3.report.evidence_id
    assert first.intelligence_session_id == phase3.report.session_id
    assert first.experiment_id == phase3.report.experiment_id
    assert first.phase3_verdict == DecisionSupportVerdict.PRESENT_FOR_HUMAN_REVIEW
    assert first.invocation_source == "human_requested"
    assert first.execution_authority == "none"


def test_supervised_session_preserves_phase3_abstention(tmp_path: Path) -> None:
    """Phase 4 records an abstention exactly and does not turn it into another outcome."""

    store, phase3 = _phase3_result(tmp_path, actionable=False)
    session = build_supervised_session(
        report=phase3.report,
        invocation_id="human-review-abstention",
        registry=IntelligenceMemoryRegistry(store),
    )

    assert phase3.assessment.verdict == DecisionSupportVerdict.ABSTAIN
    assert session.phase3_verdict == DecisionSupportVerdict.ABSTAIN
    assert session.execution_authority == "none"


def test_supervised_session_rejects_rewritten_lineage(tmp_path: Path) -> None:
    """Public validation rejects a supervision session whose Phase 3 links are substituted."""

    store, phase3 = _phase3_result(tmp_path)
    session = build_supervised_session(
        report=phase3.report,
        invocation_id="human-review-lineage",
        registry=IntelligenceMemoryRegistry(store),
    )
    payload = session.model_dump(mode="python")
    payload["assessment_id"] = "aq-intel-assessment-000000000000000000000000"

    with pytest.raises(ValueError, match="assessment_id must match"):
        SupervisedSession.model_validate(payload)


def test_supervised_session_requires_verified_stored_phase3_report(tmp_path: Path) -> None:
    """Tampered report bytes fail closed before a Phase 4 session can be admitted."""

    store, phase3 = _phase3_result(tmp_path)
    name = intelligence_report_name(phase3.assessment)
    altered = b"tampered Phase 3 report\n"
    store.write_bytes(
        phase3.session.experiment_id,
        name,
        altered,
        expected_checksum=sha256_bytes(altered),
        allow_replace=True,
    )

    with pytest.raises(ResearchRegistryError, match="checksum does not match"):
        build_supervised_session(
            report=phase3.report,
            invocation_id="human-review-tampered",
            registry=IntelligenceMemoryRegistry(store),
        )


def test_supervision_session_module_is_authority_free() -> None:
    """Slice 1 must not import execution, broker, paper-ops, or scheduling subsystems."""

    from spy_market_agent.supervision import session as supervision_session

    source = getsource(supervision_session)
    assert "spy_market_agent.execution" not in source
    assert "spy_market_agent.paper_ops" not in source
    assert "alpaca" not in source.lower()
    assert "scheduler" not in source.lower()
    assert "TradingClient" not in source
