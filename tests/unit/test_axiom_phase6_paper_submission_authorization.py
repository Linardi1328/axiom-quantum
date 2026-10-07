from __future__ import annotations

from dataclasses import replace
from inspect import getsource
from pathlib import Path

import pytest

import spy_market_agent.paper_ops.authorization as authorization_module
from spy_market_agent.paper_ops import (
    PaperReadinessMemoryRegistry,
    build_paper_execution_session,
    build_paper_submission_authorization,
)
from spy_market_agent.research.errors import ResearchRegistryError
from unit.phase8_helpers import make_approval, make_instruction
from unit.test_axiom_phase6_paper_execution_session import _stored_assessment


def _session(tmp_path: Path):
    store, memory, assessment = _stored_assessment(tmp_path)
    session = build_paper_execution_session(
        assessment=assessment,
        invocation_id="phase6-authorization-session",
        registry=memory,
    )
    return store, memory, session


def test_phase6_authorization_binds_exact_session_instruction_and_approval(
    tmp_path: Path,
) -> None:
    _, memory, session = _session(tmp_path)
    instruction = make_instruction()
    approval = make_approval(instruction)

    authorization = build_paper_submission_authorization(
        session=session,
        instruction=instruction,
        approval=approval,
        registry=memory,
    )

    assert authorization.session == session
    assert authorization.paper_execution_session_id == session.paper_execution_session_id
    assert authorization.instruction == instruction
    assert authorization.approval == approval
    assert authorization.signal_id == instruction.signal_id
    assert authorization.client_order_id == instruction.client_order_id
    assert authorization.instruction_fingerprint == instruction.instruction_fingerprint
    assert authorization.approval_id == approval.approval_id
    assert authorization.authorization_source == "human_confirmed"
    assert authorization.use_policy == "single_use"
    assert authorization.execution_scope == "paper_only"
    assert (
        authorization.model_connected_execution
        == "blocked_no_approved_paper_model"
    )


def test_phase6_authorization_is_deterministic(tmp_path: Path) -> None:
    _, memory, session = _session(tmp_path)
    instruction = make_instruction()
    approval = make_approval(instruction)

    first = build_paper_submission_authorization(
        session=session,
        instruction=instruction,
        approval=approval,
        registry=memory,
    )
    second = build_paper_submission_authorization(
        session=session,
        instruction=instruction,
        approval=approval,
        registry=memory,
    )

    assert first == second
    assert (
        first.paper_submission_authorization_id
        == authorization_module.paper_submission_authorization_identity(first)
    )


def test_phase6_authorization_rejects_mismatched_approval(tmp_path: Path) -> None:
    _, memory, session = _session(tmp_path)
    instruction = make_instruction()
    other_instruction = make_instruction(client_order_id="paper-order-other")
    other_approval = make_approval(other_instruction, approval_id="approval-other")

    with pytest.raises(Exception, match="approval does not match instruction"):
        build_paper_submission_authorization(
            session=session,
            instruction=instruction,
            approval=other_approval,
            registry=memory,
        )


def test_phase6_authorization_reverifies_stored_phase5_lineage(tmp_path: Path) -> None:
    store, memory, session = _session(tmp_path)
    store.write_json(
        session.experiment_id,
        memory._assessment_name(session.assessment_id),
        {"broken": True},
        allow_replace=True,
    )

    instruction = make_instruction()
    with pytest.raises(ResearchRegistryError, match="canonical validation"):
        build_paper_submission_authorization(
            session=session,
            instruction=instruction,
            approval=make_approval(instruction),
            registry=memory,
        )


def test_phase6_authorization_identity_rejects_tamper(tmp_path: Path) -> None:
    _, memory, session = _session(tmp_path)
    instruction = make_instruction()
    authorization = build_paper_submission_authorization(
        session=session,
        instruction=instruction,
        approval=make_approval(instruction),
        registry=memory,
    )

    payload = authorization.model_dump(mode="python")
    payload["paper_submission_authorization_id"] = (
        "aq-paper-submission-authorization-" + "0" * 24
    )
    with pytest.raises(ValueError, match="canonical authorization content"):
        authorization_module.PaperSubmissionAuthorization.model_validate(payload)


def test_phase6_authorization_constructor_has_no_broker_capability() -> None:
    source = getsource(authorization_module).lower()

    for forbidden in (
        "paperbrokerprotocol",
        "submit_approved_order",
        "submit_market_day_order",
        "tradingclient",
        "scheduler",
        "cron",
        "live trading",
    ):
        assert forbidden not in source
