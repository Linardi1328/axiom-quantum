"""Phase 7 records operator attestations but never submits broker orders."""

from __future__ import annotations

from datetime import date
from inspect import getsource
from pathlib import Path
from typing import cast

import pytest

import spy_market_agent.phase7_validation.pilot as module
from spy_market_agent.benchmark.artifacts import sha256_json
from spy_market_agent.execution import PaperExecutionService
from spy_market_agent.execution.protocols import PaperBrokerProtocol
from spy_market_agent.phase6_execution import (
    PaperExecutionMemoryRegistry,
    run_paper_submission_workflow,
)
from spy_market_agent.phase7_validation.pilot import (
    Phase7PaperPilotObservation,
    build_phase7_pilot_observation,
)
from spy_market_agent.phase7_validation.session import build_phase7_validation_session
from unit.phase8_helpers import FakePaperBroker, make_approval, make_instruction, make_receipt
from unit.test_axiom_phase6_execution_reporting import _Service
from unit.test_axiom_phase6_paper_submission_authorization import _session


def _parents(tmp_path: Path) -> tuple[object, object]:
    store, readiness, p6session = _session(tmp_path)
    p7session = build_phase7_validation_session(
        assessment=p6session.assessment,
        invocation_id="pilot-human-day",
        observation_date=date(2026, 10, 8),
        mode="paper_broker",
        registry=readiness,
    )
    return p7session, PaperExecutionMemoryRegistry(store)


def test_explicit_abstention_does_not_fabricate_an_order(tmp_path: Path) -> None:
    session, registry = _parents(tmp_path)
    observation = build_phase7_pilot_observation(
        session=session,  # type: ignore[arg-type]
        state="abstained",
        attestation_ref="human-no-signal",
        registry=registry,  # type: ignore[arg-type]
        abstain_reason="no_qualifying_setup",
    )
    assert observation.state == "abstained"
    assert observation.outcome is None
    assert observation.broker_verification == "not_independently_verified"
    assert not observation.accepted_order
    assert not observation.unresolved_unknown
    assert observation.evidence_source == "operator_attested_paper_broker"
    assert observation == build_phase7_pilot_observation(
        session=session,  # type: ignore[arg-type]
        state="abstained",
        attestation_ref="human-no-signal",
        registry=registry,  # type: ignore[arg-type]
        abstain_reason="no_qualifying_setup",
    )


def test_pilot_execution_links_exact_phase6_persisted_report(tmp_path: Path) -> None:
    store, readiness, p6session = _session(tmp_path)
    registry = PaperExecutionMemoryRegistry(store)
    session = build_phase7_validation_session(
        assessment=p6session.assessment,
        invocation_id="pilot-phase6-attestation",
        observation_date=date(2026, 10, 8),
        mode="paper_broker",
        registry=readiness,
    )
    instruction = make_instruction()
    workflow = run_paper_submission_workflow(
        assessment=p6session.assessment,
        invocation_id="pilot-phase6-parent",
        instruction=instruction,
        approval=make_approval(instruction),
        readiness_registry=readiness,
        execution_registry=registry,
        service=cast(PaperExecutionService, _Service(submit_receipt=make_receipt(instruction))),
        broker=cast(PaperBrokerProtocol, FakePaperBroker()),
    )
    checksum = sha256_json({"sanitized_fixture": "fake-not-a-real-broker"})
    observation = build_phase7_pilot_observation(
        session=session,
        state="execution_recorded",
        attestation_ref="human-reviewed-001",
        registry=registry,
        outcome=workflow.outcomes[-1],
        report=workflow.report,
        broker_artifact_checksum=checksum,
    )
    assert observation.accepted_order
    assert not observation.unresolved_unknown
    assert observation.execution_authority == "none"
    assert observation.report == workflow.report
    assert observation.outcome == workflow.outcomes[-1]
    assert observation.broker_verification == "not_independently_verified"


def test_pilot_rejects_incomplete_order_and_false_abstention(tmp_path: Path) -> None:
    session, registry = _parents(tmp_path)
    with pytest.raises(ValueError, match="requires outcome"):
        build_phase7_pilot_observation(
            session=session,  # type: ignore[arg-type]
            state="execution_recorded",
            attestation_ref="operator",
            registry=registry,  # type: ignore[arg-type]
            broker_artifact_checksum=sha256_json({"payload": "no-report"}),
        )
    with pytest.raises(ValueError, match="abstention must have a reason"):
        build_phase7_pilot_observation(
            session=session,  # type: ignore[arg-type]
            state="abstained",
            attestation_ref="operator",
            registry=registry,  # type: ignore[arg-type]
        )


def test_pilot_rejects_synthetic_mode_and_tamper(tmp_path: Path) -> None:
    store, readiness, p6session = _session(tmp_path)
    synthetic = build_phase7_validation_session(
        assessment=p6session.assessment,
        invocation_id="fake-pilot",
        observation_date=date(2026, 10, 8),
        mode="synthetic",
        registry=readiness,
    )
    with pytest.raises(ValueError, match="paper_broker session"):
        build_phase7_pilot_observation(
            session=synthetic,
            state="abstained",
            attestation_ref="operator",
            registry=PaperExecutionMemoryRegistry(store),
            abstain_reason="no_qualifying_setup",
        )
    session, registry = _parents(tmp_path)
    valid = build_phase7_pilot_observation(
        session=session,  # type: ignore[arg-type]
        state="abstained",
        attestation_ref="operator",
        registry=registry,  # type: ignore[arg-type]
        abstain_reason="no_qualifying_setup",
    )
    with pytest.raises(ValueError, match="canonical content"):
        Phase7PaperPilotObservation.model_validate(
            valid.model_dump(mode="python") | {"attestation_ref": "altered"}
        )
    with pytest.raises(ValueError, match="safe opaque"):
        build_phase7_pilot_observation(
            session=session,  # type: ignore[arg-type]
            state="abstained",
            attestation_ref="../outside",
            registry=registry,  # type: ignore[arg-type]
            abstain_reason="no_qualifying_setup",
        )


def test_pilot_module_has_no_trading_calls() -> None:
    source = getsource(module)
    for forbidden in (
        "TradingClient",
        "AlpacaPaperBroker",
        "submit_approved_order",
        "submit_market_day_order",
        "PaperBrokerProtocol",
        "BackgroundScheduler",
    ):
        assert forbidden not in source
