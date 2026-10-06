from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError
from tests.unit.test_axiom_phase3_reporting_completion import (
    _brief,
    _decision,
    _record_phase2,
    _store,
)

from spy_market_agent.intelligence.axiom_decision_support import DecisionSupportVerdict
from spy_market_agent.intelligence.axiom_memory import IntelligenceMemoryRegistry
from spy_market_agent.intelligence.axiom_reporting import (
    IntelligenceWorkflowResult,
    run_intelligence_os_workflow,
)
from spy_market_agent.intelligence.axiom_supervision_session import (
    SupervisedSession,
    build_supervised_session,
    supervision_session_identity,
)
from spy_market_agent.research.artifacts import ResearchArtifactStore
from spy_market_agent.research.errors import ResearchArtifactError, ResearchRegistryError


def _phase3_result(
    tmp_path: Path, *, actionable: bool = True
) -> tuple[ResearchArtifactStore, IntelligenceWorkflowResult]:
    decision = _decision()
    store = _store(tmp_path)
    _record_phase2(store, decision)
    result = run_intelligence_os_workflow(
        decision=decision,
        brief=_brief(actionable=actionable),
        invocation_id="phase3-owner-request",
        store=store,
    )
    return store, result


def test_supervised_session_binds_verified_phase3_report_deterministically(tmp_path: Path) -> None:
    store, phase3 = _phase3_result(tmp_path)
    registry = IntelligenceMemoryRegistry(store)

    session = build_supervised_session(
        report=phase3.report,
        invocation_id="phase4-owner-request",
        registry=registry,
    )
    repeated = build_supervised_session(
        report=phase3.report,
        invocation_id="phase4-owner-request",
        registry=registry,
    )

    assert session == repeated
    assert session.supervision_session_id == supervision_session_identity(session)
    assert session.phase3_report == phase3.report
    assert session.phase3_verdict == DecisionSupportVerdict.PRESENT_FOR_HUMAN_REVIEW
    assert session.invocation_source == "human_requested"
    assert session.execution_authority == "none"


def test_supervised_session_preserves_abstention_without_upgrade(tmp_path: Path) -> None:
    store, phase3 = _phase3_result(tmp_path, actionable=False)

    session = build_supervised_session(
        report=phase3.report,
        invocation_id="phase4-abstention-review",
        registry=IntelligenceMemoryRegistry(store),
    )

    assert phase3.assessment.verdict == DecisionSupportVerdict.ABSTAIN
    assert session.phase3_verdict == DecisionSupportVerdict.ABSTAIN
    assert session.execution_authority == "none"


def test_supervised_session_requires_stored_phase3_parent_chain(tmp_path: Path) -> None:
    _, phase3 = _phase3_result(tmp_path / "source")
    empty_store = _store(tmp_path / "empty")

    with pytest.raises(ResearchArtifactError, match="required research artifact is missing"):
        build_supervised_session(
            report=phase3.report,
            invocation_id="phase4-missing-parent",
            registry=IntelligenceMemoryRegistry(empty_store),
        )


def test_supervised_session_rejects_tampered_report_lineage(tmp_path: Path) -> None:
    store, phase3 = _phase3_result(tmp_path)
    altered = phase3.report.model_copy(update={"assessment_id": "aq-intel-assessment-" + "f" * 24})

    with pytest.raises((ValidationError, ResearchRegistryError, ResearchArtifactError)):
        build_supervised_session(
            report=altered,
            invocation_id="phase4-tampered-parent",
            registry=IntelligenceMemoryRegistry(store),
        )


def test_public_model_cannot_reinterpret_phase3_verdict(tmp_path: Path) -> None:
    store, phase3 = _phase3_result(tmp_path, actionable=False)
    session = build_supervised_session(
        report=phase3.report,
        invocation_id="phase4-verdict-lock",
        registry=IntelligenceMemoryRegistry(store),
    )

    payload = session.model_dump(mode="python")
    payload["phase3_verdict"] = DecisionSupportVerdict.PRESENT_FOR_HUMAN_REVIEW
    with pytest.raises(ValidationError, match="exactly match"):
        SupervisedSession.model_validate(payload)


def test_supervision_module_has_no_execution_or_scheduler_dependency() -> None:
    from inspect import getsource

    from spy_market_agent.intelligence import axiom_supervision_session

    source = getsource(axiom_supervision_session)
    assert "spy_market_agent.execution" not in source
    assert "spy_market_agent.paper_ops" not in source
    assert "spy_market_agent.shadow" not in source
    assert "TradingClient" not in source
    assert "scheduler" not in source.lower()
