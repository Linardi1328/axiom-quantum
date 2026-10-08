"""Phase 7 cannot grant trading authority from synthetic or attested evidence."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import cast

import exchange_calendars
import pytest

from spy_market_agent.benchmark.artifacts import sha256_bytes, sha256_json
from spy_market_agent.execution import PaperExecutionService, PaperExecutionSubmissionUnknownError
from spy_market_agent.execution.protocols import PaperBrokerProtocol
from spy_market_agent.paper_ops.assessment import PaperReadinessAssessment
from spy_market_agent.phase6_execution import run_paper_submission_workflow
from spy_market_agent.phase7_validation.assessment import (
    Phase7OperationalAssessment,
    build_phase7_operational_assessment,
    replay_phase7_operational_assessment,
)
from spy_market_agent.phase7_validation.memory import Phase7ValidationMemoryRegistry
from spy_market_agent.phase7_validation.pilot import build_phase7_pilot_observation
from spy_market_agent.phase7_validation.reporting import (
    load_phase7_validation_report,
    phase7_report_name,
    run_phase7_observation_reporting_workflow,
    write_phase7_validation_report,
)
from spy_market_agent.phase7_validation.safety import build_phase7_safety_evidence
from spy_market_agent.phase7_validation.session import build_phase7_validation_session
from unit.phase8_helpers import FakePaperBroker, make_approval, make_instruction, make_receipt
from unit.test_axiom_phase6_execution_reporting import _Service
from unit.test_axiom_phase7_safety import _fixture


def _setup(tmp_path: Path) -> tuple[Phase7ValidationMemoryRegistry, PaperReadinessAssessment]:
    session, readiness, probes = _fixture(tmp_path)
    registry = Phase7ValidationMemoryRegistry(readiness.store)
    registry.record_session(session)
    evidence = build_phase7_safety_evidence(session=session, probes=probes, registry=readiness)
    registry.record_safety(evidence)
    return registry, session.assessment


def _paper_abstention(
    registry: Phase7ValidationMemoryRegistry,
    assessment: PaperReadinessAssessment,
    *,
    day: date,
    index: int,
) -> None:
    session = build_phase7_validation_session(
        assessment=assessment,
        invocation_id=f"pilot-{index}",
        observation_date=day,
        mode="paper_broker",
        registry=registry.readiness_memory,
    )
    registry.record_session(session)
    observation = build_phase7_pilot_observation(
        session=session,
        state="abstained",
        abstain_reason="no_qualifying_setup",
        human_reviewed=True,
        attestation_ref=f"attested-{index}",
        registry=registry.execution_memory,
    )
    registry.record_pilot(observation)


def test_no_evidence_remains_no_go(tmp_path: Path) -> None:
    registry, assessment = _setup(tmp_path)
    result = build_phase7_operational_assessment(
        experiment_id=assessment.experiment_id,
        registry=registry,
    )
    assert result.pilot_session_count == 0
    assert result.market_session_count == 0
    assert result.accepted_order_count == 0
    assert result.all_critical_safety_passed
    assert not result.ready_for_owner_audit
    assert result.operational_verdict == "no_go"
    assert result.trading_authority == "none"
    assert result.model_connected_execution == "blocked_no_approved_paper_model"


def test_abstentions_and_nonmarket_dates_never_fake_executions(tmp_path: Path) -> None:
    registry, assessment = _setup(tmp_path)
    _paper_abstention(registry, assessment, day=date(2026, 10, 10), index=1)
    _paper_abstention(registry, assessment, day=date(2026, 10, 12), index=2)
    result = build_phase7_operational_assessment(
        experiment_id=assessment.experiment_id,
        registry=registry,
    )
    assert result.pilot_session_count == 2
    assert result.market_session_count == 1
    assert result.abstention_count == 2
    assert result.execution_count == 0
    assert not result.ready_for_owner_audit


def test_report_is_checksum_verified_and_stable_after_new_data(tmp_path: Path) -> None:
    registry, assessment = _setup(tmp_path)
    _paper_abstention(registry, assessment, day=date(2026, 10, 8), index=1)
    artifact = write_phase7_validation_report(
        experiment_id=assessment.experiment_id,
        registry=registry,
    )
    content = load_phase7_validation_report(artifact, registry=registry)
    assert "Operational verdict: NO_GO" in content
    assert "not independent proof of real broker origin" in content

    _paper_abstention(registry, assessment, day=date(2026, 10, 9), index=2)
    assert load_phase7_validation_report(artifact, registry=registry) == content
    assert (
        replay_phase7_operational_assessment(snapshot=artifact.assessment, registry=registry)
        == artifact.assessment
    )
    newer = write_phase7_validation_report(
        experiment_id=assessment.experiment_id,
        registry=registry,
    )
    assert newer.report_id != artifact.report_id
    assert newer.assessment.pilot_session_count == 2


def test_report_rejects_tampered_markdown_and_forged_metrics(tmp_path: Path) -> None:
    registry, assessment = _setup(tmp_path)
    artifact = write_phase7_validation_report(
        experiment_id=assessment.experiment_id,
        registry=registry,
    )
    with pytest.raises(ValueError, match="pilot count must match"):
        Phase7OperationalAssessment.model_validate(
            artifact.assessment.model_dump(mode="python") | {"pilot_session_count": 20}
        )
    filename = phase7_report_name(artifact.assessment)
    changed = b"# forged results\n"
    registry.store.write_bytes(
        artifact.experiment_id,
        filename,
        changed,
        expected_checksum=sha256_bytes(changed),
        allow_replace=True,
    )
    with pytest.raises(ValueError, match="checksum mismatch"):
        load_phase7_validation_report(artifact, registry=registry)


def test_twenty_attested_sessions_can_only_trigger_owner_audit_not_trading(
    tmp_path: Path,
) -> None:
    registry, assessment = _setup(tmp_path)
    dates = tuple(
        stamp.date()
        for stamp in exchange_calendars.get_calendar("XNYS").sessions_in_range(
            "2026-09-01", "2026-09-30"
        )
    )
    assert len(dates) >= 20
    for index, day in enumerate(dates[:20]):
        if index:
            _paper_abstention(registry, assessment, day=day, index=index)
            continue
        session = build_phase7_validation_session(
            assessment=assessment,
            invocation_id="simulated-execution-day",
            observation_date=day,
            mode="paper_broker",
            registry=registry.readiness_memory,
        )
        registry.record_session(session)
        instruction = make_instruction()
        result = run_paper_submission_workflow(
            assessment=assessment,
            invocation_id="fake-phase6-owner",
            instruction=instruction,
            approval=make_approval(instruction),
            readiness_registry=registry.readiness_memory,
            execution_registry=registry.execution_memory,
            service=cast(
                PaperExecutionService,
                _Service(submit_receipt=make_receipt(instruction)),
            ),
            broker=cast(PaperBrokerProtocol, FakePaperBroker()),
        )
        recorded = build_phase7_pilot_observation(
            session=session,
            state="execution_recorded",
            attestation_ref="synthetic-test-not-real-broker",
            human_reviewed=True,
            outcome=result.outcomes[-1],
            report=result.report,
            broker_artifact_checksum=sha256_json({"synthetic": True}),
            registry=registry.execution_memory,
        )
        registry.record_pilot(recorded)
    assessment_result = build_phase7_operational_assessment(
        experiment_id=assessment.experiment_id,
        registry=registry,
    )
    assert assessment_result.pilot_session_count == 20
    assert assessment_result.market_session_count == 20
    assert assessment_result.accepted_order_count == 1
    assert assessment_result.ready_for_owner_audit
    assert assessment_result.operational_verdict == "no_go"
    assert assessment_result.evidence_provenance == ("operator_attested_not_independently_verified")
    report = write_phase7_validation_report(
        experiment_id=assessment.experiment_id,
        registry=registry,
    )
    assert report.operational_verdict == "no_go"
    assert "NO_GO" in load_phase7_validation_report(report, registry=registry)


def test_explicit_observation_reporting_workflow_never_submits(
    tmp_path: Path,
) -> None:
    registry, assessment = _setup(tmp_path)
    session = build_phase7_validation_session(
        assessment=assessment,
        invocation_id="owner-reporting-abstention",
        observation_date=date(2026, 10, 8),
        mode="paper_broker",
        registry=registry.readiness_memory,
    )
    observation = build_phase7_pilot_observation(
        session=session,
        state="abstained",
        abstain_reason="no_qualifying_setup",
        attestation_ref="owner-no-signal",
        human_reviewed=True,
        registry=registry.execution_memory,
    )
    artifact = run_phase7_observation_reporting_workflow(
        observation=observation,
        registry=registry,
    )
    assert artifact.operational_verdict == "no_go"
    assert artifact.assessment.pilot_session_count == 1
    assert artifact.assessment.accepted_order_count == 0
    assert registry.list_pilots(assessment.experiment_id) == (observation,)
    assert "NO_GO" in load_phase7_validation_report(artifact, registry=registry)


def test_incidents_and_unknown_outcomes_are_never_silently_accepted(
    tmp_path: Path,
) -> None:
    registry, assessment = _setup(tmp_path)
    session = build_phase7_validation_session(
        assessment=assessment,
        invocation_id="uncertain-paper-review",
        observation_date=date(2026, 10, 8),
        mode="paper_broker",
        registry=registry.readiness_memory,
    )
    registry.record_session(session)
    instruction = make_instruction()
    uncertain = run_paper_submission_workflow(
        assessment=assessment,
        invocation_id="uncertain-fake-paper",
        instruction=instruction,
        approval=make_approval(instruction),
        readiness_registry=registry.readiness_memory,
        execution_registry=registry.execution_memory,
        service=cast(
            PaperExecutionService,
            _Service(
                submit_error=PaperExecutionSubmissionUnknownError(
                    "submission_outcome_unknown", "no definitive receipt"
                )
            ),
        ),
        broker=cast(PaperBrokerProtocol, FakePaperBroker()),
    )
    observation = build_phase7_pilot_observation(
        session=session,
        state="execution_recorded",
        attestation_ref="unknown-fake-broker",
        human_reviewed=True,
        outcome=uncertain.outcomes[-1],
        report=uncertain.report,
        broker_artifact_checksum=sha256_json({"synthetic_unknown": True}),
        incident_codes=("audit_gap",),
        registry=registry.execution_memory,
    )
    registry.record_pilot(observation)
    result = build_phase7_operational_assessment(
        experiment_id=assessment.experiment_id,
        registry=registry,
    )
    assert result.unresolved_unknown_count == 1
    assert result.incident_count == 1
    assert result.accepted_order_count == 0
    assert not result.ready_for_owner_audit
    assert result.operational_verdict == "no_go"
