from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from spy_market_agent.benchmark.artifacts import sha256_bytes
from spy_market_agent.execution import (
    PaperExecutionBrokerRejectionError,
    PaperExecutionPermissionError,
    PaperExecutionService,
    PaperExecutionSubmissionUnknownError,
)
from spy_market_agent.execution.models import PaperOrderReceipt
from spy_market_agent.execution.protocols import PaperBrokerProtocol
from spy_market_agent.phase6_execution import (
    PaperExecutionMemoryRegistry,
    load_paper_execution_report,
    run_paper_reconciliation_workflow,
    run_paper_submission_workflow,
)
from spy_market_agent.research.errors import ResearchRegistryError
from unit.phase8_helpers import (
    BROKER_TIME,
    FakePaperBroker,
    make_approval,
    make_instruction,
    make_receipt,
)
from unit.test_axiom_phase6_paper_submission_authorization import _session


class _Service:
    """Deterministic service double for Phase 6 reporting workflow tests."""

    def __init__(
        self,
        *,
        submit_receipt: PaperOrderReceipt | None = None,
        submit_error: BaseException | None = None,
        reconcile_receipt: PaperOrderReceipt | None = None,
    ) -> None:
        self.submit_receipt = submit_receipt
        self.submit_error = submit_error
        self.reconcile_receipt = reconcile_receipt
        self.submit_calls = 0
        self.reconcile_calls = 0

    def submit_approved_order(self, *_args: object, **_kwargs: object) -> PaperOrderReceipt:
        """Return the configured receipt or raise the configured submission failure."""

        self.submit_calls += 1
        if self.submit_error is not None:
            raise self.submit_error
        assert self.submit_receipt is not None
        return self.submit_receipt

    def reconcile_by_client_order_id(
        self,
        _client_order_id: str,
        **_kwargs: object,
    ) -> PaperOrderReceipt | None:
        """Return only configured lookup evidence and never submit."""

        self.reconcile_calls += 1
        return self.reconcile_receipt


def test_phase6_submission_workflow_persists_deterministic_accepted_report(
    tmp_path: Path,
) -> None:
    store, readiness, session = _session(tmp_path)
    memory = PaperExecutionMemoryRegistry(store)
    instruction = make_instruction()
    approval = make_approval(instruction)
    service = _Service(submit_receipt=make_receipt(instruction))

    result = run_paper_submission_workflow(
        assessment=session.assessment,
        invocation_id="phase6-report-accepted",
        instruction=instruction,
        approval=approval,
        readiness_registry=readiness,
        execution_registry=memory,
        service=cast(PaperExecutionService, service),
        broker=cast(PaperBrokerProtocol, FakePaperBroker()),
    )

    assert result.outcomes[-1].disposition == "accepted"
    assert result.report.final_disposition == "accepted"
    assert result.report.outcome_ids == (
        result.outcomes[-1].paper_execution_outcome_id,
    )
    assert service.submit_calls == 1
    content = load_paper_execution_report(result.report, registry=memory)
    assert "Final disposition: accepted" in content
    assert "Model-connected execution: blocked_no_approved_paper_model" in content
    assert "No scheduler, recurrence, unattended execution" in content


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (PaperExecutionBrokerRejectionError("broker_rejected", "rejected"), "rejected"),
        (PaperExecutionPermissionError("dry_run_enabled", "blocked"), "blocked"),
    ],
)
def test_phase6_submission_workflow_reports_fail_closed_terminal_outcomes(
    tmp_path: Path,
    error: BaseException,
    expected: str,
) -> None:
    store, readiness, session = _session(tmp_path)
    memory = PaperExecutionMemoryRegistry(store)
    instruction = make_instruction()
    service = _Service(submit_error=error)

    result = run_paper_submission_workflow(
        assessment=session.assessment,
        invocation_id=f"phase6-report-{expected}",
        instruction=instruction,
        approval=make_approval(instruction),
        readiness_registry=readiness,
        execution_registry=memory,
        service=cast(PaperExecutionService, service),
        broker=cast(PaperBrokerProtocol, FakePaperBroker()),
    )

    assert result.outcomes[-1].disposition == expected
    assert result.report.final_disposition == expected
    assert result.outcomes[-1].failure_code is not None
    assert service.submit_calls == 1


def test_phase6_unknown_submission_requires_separate_lookup_only_reconciliation(
    tmp_path: Path,
) -> None:
    store, readiness, session = _session(tmp_path)
    memory = PaperExecutionMemoryRegistry(store)
    instruction = make_instruction()
    approval = make_approval(instruction)
    submit_service = _Service(
        submit_error=PaperExecutionSubmissionUnknownError(
            "submission_outcome_unknown",
            "unknown",
        )
    )

    submitted = run_paper_submission_workflow(
        assessment=session.assessment,
        invocation_id="phase6-report-unknown",
        instruction=instruction,
        approval=approval,
        readiness_registry=readiness,
        execution_registry=memory,
        service=cast(PaperExecutionService, submit_service),
        broker=cast(PaperBrokerProtocol, FakePaperBroker()),
    )

    assert submitted.outcomes[-1].disposition == "submission_unknown"
    assert submitted.outcomes[-1].reconciliation_lookup_only is False

    reconcile_service = _Service(reconcile_receipt=make_receipt(instruction))
    reconciled = run_paper_reconciliation_workflow(
        authorization=submitted.authorization,
        execution_registry=memory,
        service=cast(PaperExecutionService, reconcile_service),
        broker=cast(PaperBrokerProtocol, FakePaperBroker()),
        now_utc=BROKER_TIME,
    )

    assert tuple(item.disposition for item in reconciled.outcomes) == (
        "submission_unknown",
        "reconciled",
    )
    assert reconciled.outcomes[-1].reconciliation_lookup_only is True
    assert reconciled.report.final_disposition == "reconciled"
    assert submit_service.submit_calls == 1
    assert reconcile_service.submit_calls == 0
    assert reconcile_service.reconcile_calls == 1
    content = load_paper_execution_report(reconciled.report, registry=memory)
    assert "Reconciliation was lookup-only by client_order_id" in content


def test_phase6_submission_workflow_refuses_duplicate_authorization_use(
    tmp_path: Path,
) -> None:
    store, readiness, session = _session(tmp_path)
    memory = PaperExecutionMemoryRegistry(store)
    instruction = make_instruction()
    approval = make_approval(instruction)
    service = _Service(submit_receipt=make_receipt(instruction))
    kwargs = {
        "assessment": session.assessment,
        "invocation_id": "phase6-report-duplicate",
        "instruction": instruction,
        "approval": approval,
        "readiness_registry": readiness,
        "execution_registry": memory,
        "service": cast(PaperExecutionService, service),
        "broker": cast(PaperBrokerProtocol, FakePaperBroker()),
    }

    run_paper_submission_workflow(**kwargs)

    with pytest.raises(ResearchRegistryError, match="already been consumed"):
        run_paper_submission_workflow(**kwargs)
    assert service.submit_calls == 1


def test_phase6_report_load_fails_closed_after_report_tamper(tmp_path: Path) -> None:
    store, readiness, session = _session(tmp_path)
    memory = PaperExecutionMemoryRegistry(store)
    instruction = make_instruction()
    result = run_paper_submission_workflow(
        assessment=session.assessment,
        invocation_id="phase6-report-tamper",
        instruction=instruction,
        approval=make_approval(instruction),
        readiness_registry=readiness,
        execution_registry=memory,
        service=cast(PaperExecutionService, _Service(submit_receipt=make_receipt(instruction))),
        broker=cast(PaperBrokerProtocol, FakePaperBroker()),
    )
    report_path = store.artifact_path(
        result.authorization.experiment_id,
        f"axiom_paper_execution_report_{result.report.final_outcome_id}.md",
    )
    altered = b"tampered phase 6 report\n"
    store.write_bytes(
        result.authorization.experiment_id,
        report_path.name,
        altered,
        expected_checksum=sha256_bytes(altered),
        allow_replace=True,
    )

    with pytest.raises(ResearchRegistryError, match="checksum"):
        load_paper_execution_report(result.report, registry=memory)


def test_phase6_reporting_source_has_no_autonomous_or_live_execution_path() -> None:
    source = Path(__file__).parents[2] / "src/spy_market_agent/phase6_execution/reporting.py"
    text = source.read_text(encoding="utf-8").lower()

    for forbidden in (
        "tradingclient",
        "scheduler",
        "cron",
        "while true",
        "live trading",
        "background worker",
        "auto-resubmit",
    ):
        assert forbidden not in text
