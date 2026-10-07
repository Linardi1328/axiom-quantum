from __future__ import annotations

import subprocess
import sys
from inspect import getsource
from pathlib import Path

import pytest

import spy_market_agent.paper_ops.reporting as reporting
from spy_market_agent.benchmark.artifacts import sha256_bytes
from spy_market_agent.paper_ops import (
    PaperReadinessMemoryRegistry,
    PaperReadinessOutcome,
    PaperRecoveryDisposition,
)
from spy_market_agent.research.errors import ResearchRegistryError
from spy_market_agent.supervision.disposition import HumanReviewDisposition
from unit.test_axiom_phase5_paper_readiness_assessment import _readiness_parent


def _run_workflow(
    tmp_path: Path,
    *,
    actionable: bool = True,
    disposition: HumanReviewDisposition = HumanReviewDisposition.OBSERVED,
    attempt_status: str = "accepted",
    client_order_id: str = "phase5-report-order",
) -> tuple[object, object, reporting.PaperReadinessWorkflowResult]:
    """Build one exact Phase 5 end-to-end readiness/recovery outcome."""

    store, phase4 = _readiness_parent(
        tmp_path,
        actionable=actionable,
        disposition=disposition,
    )
    result = reporting.run_paper_readiness_workflow(
        supervision_report=phase4.report,
        invocation_id="phase5-reporting",
        attempt_status=attempt_status,
        client_order_id=client_order_id,
        operator_reference="phase5-report-review",
        store=store,
    )
    return store, phase4, result


def test_phase5_workflow_round_trips_offline_readiness(tmp_path: Path) -> None:
    """Observed supervision produces offline readiness and a verified report only."""

    store, phase4, result = _run_workflow(tmp_path)
    memory = PaperReadinessMemoryRegistry(store)

    assert result.session.report == phase4.report
    assert result.assessment.outcome == PaperReadinessOutcome.OFFLINE_READINESS_ONLY
    assert result.recovery_case.recovery_disposition == (
        PaperRecoveryDisposition.NO_ACTION_TERMINAL
    )
    assert result.execution_authority == "none"
    assert result.report.execution_authority == "none"
    assert (
        memory.load_session(
            result.session.experiment_id,
            result.session.paper_readiness_session_id,
        )
        == result.session
    )
    assert (
        memory.load_assessment(
            result.assessment.experiment_id,
            result.assessment.assessment_id,
        )
        == result.assessment
    )
    assert (
        memory.load_recovery_case(
            result.recovery_case.experiment_id,
            result.recovery_case.recovery_case_id,
        )
        == result.recovery_case
    )
    content = reporting.load_paper_readiness_report(result.report, registry=memory)
    assert content == reporting.render_paper_readiness_report(result.recovery_case)
    assert "offline_readiness_only" in content
    assert "P5-B" in content
    assert "P5-C" in content
    assert "Execution authority remains none." in content


def test_phase5_workflow_preserves_blocked_supervision(tmp_path: Path) -> None:
    """Deferred Phase 4 supervision remains blocked despite a terminal attempt."""

    _, _, result = _run_workflow(
        tmp_path,
        disposition=HumanReviewDisposition.DEFERRED,
    )

    assert result.assessment.outcome == PaperReadinessOutcome.BLOCKED
    assert result.execution_authority == "none"


def test_phase5_workflow_reports_reconciliation_required(tmp_path: Path) -> None:
    """Uncertain persisted state requires deterministic offline reconciliation."""

    _, _, result = _run_workflow(
        tmp_path,
        attempt_status="submission_unknown",
        client_order_id="phase5-reconcile-order",
    )

    assert result.recovery_case.recovery_disposition == (
        PaperRecoveryDisposition.RECONCILIATION_REQUIRED
    )
    assert result.recovery_case.client_order_reference == "phase5-reconcile-order"
    content = reporting.render_paper_readiness_report(result.recovery_case)
    assert "Reconciliation is required" in content
    assert "phase5-reconcile-order" in content


def test_phase5_workflow_reports_blocked_recovery(tmp_path: Path) -> None:
    """Rejected attempt evidence stays blocked and cannot become a retry path."""

    _, _, result = _run_workflow(tmp_path, attempt_status="rejected")

    assert result.recovery_case.recovery_disposition == PaperRecoveryDisposition.BLOCKED
    assert "No automatic retry" in reporting.render_paper_readiness_report(result.recovery_case)


def test_phase5_workflow_rejects_invalid_recovery_state(tmp_path: Path) -> None:
    """Malformed recovery state fails closed before any report can be produced."""

    store, phase4 = _readiness_parent(
        tmp_path,
        actionable=True,
        disposition=HumanReviewDisposition.OBSERVED,
    )
    with pytest.raises(ValueError, match="known canonical"):
        reporting.run_paper_readiness_workflow(
            supervision_report=phase4.report,
            invocation_id="phase5-invalid",
            attempt_status=" accepted ",
            client_order_id="phase5-invalid-order",
            operator_reference="phase5-invalid-review",
            store=store,
        )


def test_phase5_reporting_is_deterministic_and_idempotent(tmp_path: Path) -> None:
    """Identical canonical inputs yield identical identities and report bytes."""

    store, phase4 = _readiness_parent(
        tmp_path,
        actionable=True,
        disposition=HumanReviewDisposition.OBSERVED,
    )
    first = reporting.run_paper_readiness_workflow(
        supervision_report=phase4.report,
        invocation_id="phase5-deterministic",
        attempt_status="submission_unknown",
        client_order_id="phase5-deterministic-order",
        operator_reference="phase5-deterministic-review",
        store=store,
    )
    second = reporting.run_paper_readiness_workflow(
        supervision_report=phase4.report,
        invocation_id="phase5-deterministic",
        attempt_status="submission_unknown",
        client_order_id="phase5-deterministic-order",
        operator_reference="phase5-deterministic-review",
        store=store,
    )

    assert first == second
    assert first.report.report_id == second.report.report_id
    assert first.report.checksum == second.report.checksum


def test_phase5_report_rejects_tampered_bytes(tmp_path: Path) -> None:
    """Checksum verification makes readiness-report byte tampering fail closed."""

    store, _, result = _run_workflow(tmp_path)
    altered = b"tampered Phase 5 readiness report\n"
    store.write_bytes(
        result.recovery_case.experiment_id,
        reporting.paper_readiness_report_name(result.recovery_case),
        altered,
        expected_checksum=sha256_bytes(altered),
        allow_replace=True,
    )

    with pytest.raises(ResearchRegistryError, match="checksum"):
        reporting.load_paper_readiness_report(
            result.report,
            registry=PaperReadinessMemoryRegistry(store),
        )


def test_phase5_report_rejects_tampered_parent(tmp_path: Path) -> None:
    """Report reload fails if its exact stored readiness assessment is corrupted."""

    store, _, result = _run_workflow(tmp_path)
    memory = PaperReadinessMemoryRegistry(store)
    store.write_json(
        result.assessment.experiment_id,
        memory._assessment_name(result.assessment.assessment_id),
        {"broken": True},
        allow_replace=True,
    )

    with pytest.raises(ResearchRegistryError, match="canonical validation"):
        reporting.load_paper_readiness_report(result.report, registry=memory)


def test_phase5_report_rejects_noncanonical_identity(tmp_path: Path) -> None:
    """The public report artifact cannot carry a substituted content identity."""

    _, _, result = _run_workflow(tmp_path)
    payload = result.report.model_dump(mode="python")
    payload["report_id"] = "aq-paper-readiness-report-" + ("0" * 24)

    with pytest.raises(ValueError, match="derived from the exact report checksum"):
        reporting.PaperReadinessReportArtifact.model_validate(payload)


def test_phase5_reporting_import_order_is_cycle_safe() -> None:
    """Research-, intelligence-, supervision-, and paper-first imports remain safe."""

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import spy_market_agent.research; "
                "import spy_market_agent.intelligence; "
                "import spy_market_agent.supervision; "
                "import spy_market_agent.paper_ops; "
                "import spy_market_agent.paper_ops.reporting"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_phase5_reporting_module_has_no_operational_authority() -> None:
    """Slice 5 reporting cannot contact brokers, mutate orders, or run unattended."""

    source = getsource(reporting)
    forbidden = (
        "spy_market_agent.execution",
        "TradingClient",
        "AlpacaPaperBroker",
        "submit_order",
        "submit_market_day_order",
        "cancel_order",
        "replace_order",
        "APCA_API_KEY_ID",
        "ALPACA_API_SECRET_KEY",
        "schedule.every",
        "BackgroundScheduler",
        "send_notification",
    )
    for marker in forbidden:
        assert marker not in source
