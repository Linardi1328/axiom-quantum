from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

import spy_market_agent.phase6_execution.reporting as reporting_module
from spy_market_agent.benchmark.artifacts import sha256_bytes
from spy_market_agent.execution import (
    PaperExecutionBrokerRejectionError,
    PaperExecutionPermissionError,
    PaperExecutionService,
    PaperExecutionSubmissionUnknownError,
)
from spy_market_agent.execution.models import PaperOrderReceipt
from spy_market_agent.execution.protocols import PaperBrokerProtocol
from spy_market_agent.paper_ops import build_paper_execution_session
from spy_market_agent.phase6_execution import (
    PaperExecutionMemoryRegistry,
    PaperExecutionReportArtifact,
    PaperExecutionWorkflowResult,
    load_paper_execution_report,
    render_paper_execution_report,
    run_paper_reconciliation_workflow,
    run_paper_submission_workflow,
    write_paper_execution_report,
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
    assert result.report.outcome_ids == (result.outcomes[-1].paper_execution_outcome_id,)
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
    run_paper_submission_workflow(
        assessment=session.assessment,
        invocation_id="phase6-report-duplicate",
        instruction=instruction,
        approval=approval,
        readiness_registry=readiness,
        execution_registry=memory,
        service=cast(PaperExecutionService, service),
        broker=cast(PaperBrokerProtocol, FakePaperBroker()),
    )

    with pytest.raises(ResearchRegistryError, match="already been consumed"):
        run_paper_submission_workflow(
            assessment=session.assessment,
            invocation_id="phase6-report-duplicate",
            instruction=instruction,
            approval=approval,
            readiness_registry=readiness,
            execution_registry=memory,
            service=cast(PaperExecutionService, service),
            broker=cast(PaperBrokerProtocol, FakePaperBroker()),
        )
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
        service=cast(
            PaperExecutionService,
            _Service(submit_receipt=make_receipt(instruction)),
        ),
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
        "tradingclient(",
        "alpacapaperbroker(",
        "schedule.every",
        "subprocess.",
        "threading.",
        "asyncio.create_task",
        "while true:",
    ):
        assert forbidden not in text


@pytest.mark.parametrize(
    ("field_name", "value", "message"),
    [
        ("relative_path", "", "nonempty POSIX"),
        ("relative_path", "../unsafe.md", "stay relative"),
        ("report_id", "not-a-report", "report_id must be canonical"),
        (
            "paper_submission_authorization_id",
            "not-an-authorization",
            "paper_submission_authorization_id must be canonical",
        ),
        (
            "paper_execution_session_id",
            "not-a-session",
            "paper_execution_session_id must be canonical",
        ),
        ("outcome_ids", (), "outcome_ids must contain"),
        ("checksum", "not-a-checksum", "checksum must be canonical"),
        (
            "report_id",
            "aq-paper-execution-report-000000000000000000000000",
            "derived from the exact report checksum",
        ),
    ],
)
def test_phase6_report_artifact_rejects_malformed_metadata(
    tmp_path: Path,
    field_name: str,
    value: object,
    message: str,
) -> None:
    store, readiness, session = _session(tmp_path)
    memory = PaperExecutionMemoryRegistry(store)
    instruction = make_instruction()
    result = run_paper_submission_workflow(
        assessment=session.assessment,
        invocation_id="phase6-report-validation",
        instruction=instruction,
        approval=make_approval(instruction),
        readiness_registry=readiness,
        execution_registry=memory,
        service=cast(PaperExecutionService, _Service(submit_receipt=make_receipt(instruction))),
        broker=cast(PaperBrokerProtocol, FakePaperBroker()),
    )
    payload = result.report.model_dump(mode="python")
    payload[field_name] = value

    with pytest.raises(ValueError, match=message):
        PaperExecutionReportArtifact.model_validate(payload)


def test_phase6_report_artifact_rejects_duplicate_and_misaligned_outcome_ids(
    tmp_path: Path,
) -> None:
    store, readiness, session = _session(tmp_path)
    memory = PaperExecutionMemoryRegistry(store)
    instruction = make_instruction()
    result = run_paper_submission_workflow(
        assessment=session.assessment,
        invocation_id="phase6-report-outcome-validation",
        instruction=instruction,
        approval=make_approval(instruction),
        readiness_registry=readiness,
        execution_registry=memory,
        service=cast(PaperExecutionService, _Service(submit_receipt=make_receipt(instruction))),
        broker=cast(PaperBrokerProtocol, FakePaperBroker()),
    )
    outcome_id = result.report.final_outcome_id
    duplicate = result.report.model_dump(mode="python")
    duplicate["outcome_ids"] = (outcome_id, outcome_id)
    with pytest.raises(ValueError, match="must not contain duplicates"):
        PaperExecutionReportArtifact.model_validate(duplicate)

    misaligned = result.report.model_dump(mode="python")
    misaligned["outcome_ids"] = (
        outcome_id,
        "aq-paper-execution-outcome-000000000000000000000000",
    )
    with pytest.raises(ValueError, match="final listed outcome"):
        PaperExecutionReportArtifact.model_validate(misaligned)


def test_phase6_workflow_result_rejects_broken_links(tmp_path: Path) -> None:
    store, readiness, session = _session(tmp_path)
    memory = PaperExecutionMemoryRegistry(store)
    instruction = make_instruction()
    result = run_paper_submission_workflow(
        assessment=session.assessment,
        invocation_id="phase6-workflow-validation",
        instruction=instruction,
        approval=make_approval(instruction),
        readiness_registry=readiness,
        execution_registry=memory,
        service=cast(PaperExecutionService, _Service(submit_receipt=make_receipt(instruction))),
        broker=cast(PaperBrokerProtocol, FakePaperBroker()),
    )
    other_session = build_paper_execution_session(
        assessment=session.assessment,
        invocation_id="phase6-workflow-other-session",
        registry=readiness,
    )
    with pytest.raises(ValueError, match="exact execution session"):
        PaperExecutionWorkflowResult(
            session=other_session,
            authorization=result.authorization,
            outcomes=result.outcomes,
            report=result.report,
        )
    with pytest.raises(ValueError, match="at least one execution outcome"):
        PaperExecutionWorkflowResult(
            session=result.session,
            authorization=result.authorization,
            outcomes=(),
            report=result.report,
        )

    wrong_report = result.report.model_copy(
        update={
            "outcome_ids": ("aq-paper-execution-outcome-000000000000000000000000",),
            "final_outcome_id": "aq-paper-execution-outcome-000000000000000000000000",
        }
    )
    with pytest.raises(ValueError, match="exact outcome chain"):
        PaperExecutionWorkflowResult(
            session=result.session,
            authorization=result.authorization,
            outcomes=result.outcomes,
            report=wrong_report,
        )
    wrong_disposition = result.report.model_copy(update={"final_disposition": "blocked"})
    with pytest.raises(ValueError, match="disposition"):
        PaperExecutionWorkflowResult(
            session=result.session,
            authorization=result.authorization,
            outcomes=result.outcomes,
            report=wrong_disposition,
        )


def test_phase6_workflow_result_rejects_foreign_outcome_and_report_links(
    tmp_path: Path,
) -> None:
    store, readiness, session = _session(tmp_path)
    memory = PaperExecutionMemoryRegistry(store)
    instruction = make_instruction()
    first = run_paper_submission_workflow(
        assessment=session.assessment,
        invocation_id="phase6-workflow-first",
        instruction=instruction,
        approval=make_approval(instruction),
        readiness_registry=readiness,
        execution_registry=memory,
        service=cast(PaperExecutionService, _Service(submit_receipt=make_receipt(instruction))),
        broker=cast(PaperBrokerProtocol, FakePaperBroker()),
    )

    second_store, second_readiness, second_session = _session(tmp_path / "foreign")
    second_memory = PaperExecutionMemoryRegistry(second_store)
    second_instruction = make_instruction(
        signal_id="signal-workflow-foreign",
        client_order_id="paper-order-workflow-foreign",
    )
    second = run_paper_submission_workflow(
        assessment=second_session.assessment,
        invocation_id="phase6-workflow-foreign",
        instruction=second_instruction,
        approval=make_approval(second_instruction, approval_id="approval-workflow-foreign"),
        readiness_registry=second_readiness,
        execution_registry=second_memory,
        service=cast(
            PaperExecutionService,
            _Service(submit_receipt=make_receipt(second_instruction)),
        ),
        broker=cast(PaperBrokerProtocol, FakePaperBroker()),
    )

    with pytest.raises(ValueError, match="exact authorization"):
        PaperExecutionWorkflowResult(
            session=first.session,
            authorization=first.authorization,
            outcomes=second.outcomes,
            report=second.report,
        )
    with pytest.raises(ValueError, match="exact outcome chain"):
        PaperExecutionWorkflowResult(
            session=first.session,
            authorization=first.authorization,
            outcomes=first.outcomes,
            report=second.report,
        )

    mismatched_disposition = PaperExecutionReportArtifact.model_validate(
        first.report.model_dump(mode="python") | {"final_disposition": "blocked"}
    )
    with pytest.raises(ValueError, match="disposition"):
        PaperExecutionWorkflowResult(
            session=first.session,
            authorization=first.authorization,
            outcomes=first.outcomes,
            report=mismatched_disposition,
        )


def test_phase6_reporting_rejects_invalid_ordered_outcome_chains(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, readiness, session = _session(tmp_path)
    memory = PaperExecutionMemoryRegistry(store)
    instruction = make_instruction()
    result = run_paper_submission_workflow(
        assessment=session.assessment,
        invocation_id="phase6-invalid-chain",
        instruction=instruction,
        approval=make_approval(instruction),
        readiness_registry=readiness,
        execution_registry=memory,
        service=cast(PaperExecutionService, _Service(submit_receipt=make_receipt(instruction))),
        broker=cast(PaperBrokerProtocol, FakePaperBroker()),
    )

    monkeypatch.setattr(memory, "list_outcome_ids_for_authorization", lambda _authorization: ())
    with pytest.raises(ResearchRegistryError, match="exactly one submission"):
        reporting_module._ordered_outcomes(result.authorization, registry=memory)

    accepted = result.outcomes[-1]
    reconciliation = accepted.model_copy(update={"reconciliation_lookup_only": True})
    monkeypatch.setattr(
        memory,
        "list_outcome_ids_for_authorization",
        lambda _authorization: ("submission", "reconciliation"),
    )
    monkeypatch.setattr(
        memory,
        "load_outcome",
        lambda _experiment_id, outcome_id: (
            accepted if outcome_id == "submission" else reconciliation
        ),
    )
    with pytest.raises(ResearchRegistryError, match="submission_unknown parent"):
        reporting_module._ordered_outcomes(result.authorization, registry=memory)


def test_phase6_report_write_detects_reload_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, readiness, session = _session(tmp_path)
    memory = PaperExecutionMemoryRegistry(store)
    instruction = make_instruction()
    result = run_paper_submission_workflow(
        assessment=session.assessment,
        invocation_id="phase6-report-reload-mismatch",
        instruction=instruction,
        approval=make_approval(instruction),
        readiness_registry=readiness,
        execution_registry=memory,
        service=cast(PaperExecutionService, _Service(submit_receipt=make_receipt(instruction))),
        broker=cast(PaperBrokerProtocol, FakePaperBroker()),
    )
    monkeypatch.setattr(
        reporting_module,
        "load_paper_execution_report",
        lambda *_args, **_kwargs: "mismatch",
    )

    with pytest.raises(ResearchRegistryError, match="differs after deterministic reload"):
        write_paper_execution_report(result.outcomes[-1], registry=memory)


def test_phase6_report_load_rejects_foreign_outcome_reference(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, readiness, session = _session(tmp_path)
    memory = PaperExecutionMemoryRegistry(store)
    instruction = make_instruction()
    result = run_paper_submission_workflow(
        assessment=session.assessment,
        invocation_id="phase6-report-foreign-outcome",
        instruction=instruction,
        approval=make_approval(instruction),
        readiness_registry=readiness,
        execution_registry=memory,
        service=cast(PaperExecutionService, _Service(submit_receipt=make_receipt(instruction))),
        broker=cast(PaperBrokerProtocol, FakePaperBroker()),
    )
    foreign = result.outcomes[-1].model_copy(
        update={
            "paper_submission_authorization_id": (
                "aq-paper-submission-authorization-000000000000000000000000"
            )
        }
    )
    monkeypatch.setattr(memory, "load_outcome", lambda *_args, **_kwargs: foreign)

    with pytest.raises(ResearchRegistryError, match="exact authorization"):
        load_paper_execution_report(result.report, registry=memory)


def test_phase6_report_load_rejects_render_io_and_content_mismatches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, readiness, session = _session(tmp_path)
    memory = PaperExecutionMemoryRegistry(store)
    instruction = make_instruction()
    result = run_paper_submission_workflow(
        assessment=session.assessment,
        invocation_id="phase6-report-load-errors",
        instruction=instruction,
        approval=make_approval(instruction),
        readiness_registry=readiness,
        execution_registry=memory,
        service=cast(PaperExecutionService, _Service(submit_receipt=make_receipt(instruction))),
        broker=cast(PaperBrokerProtocol, FakePaperBroker()),
    )

    with monkeypatch.context() as patch:
        patch.setattr(
            reporting_module,
            "render_paper_execution_report",
            lambda *_args, **_kwargs: "different deterministic rendering\n",
        )
        with pytest.raises(ResearchRegistryError, match="artifact checksum"):
            load_paper_execution_report(result.report, registry=memory)

    report_path = store.artifact_path(
        result.authorization.experiment_id,
        f"axiom_paper_execution_report_{result.report.final_outcome_id}.md",
    )
    real_read_text = Path.read_text

    def fail_report_read(
        path: Path,
        encoding: str | None = None,
        errors: str | None = None,
    ) -> str:
        if path == report_path:
            raise OSError("synthetic read failure")
        return real_read_text(path, encoding=encoding, errors=errors)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "read_text", fail_report_read)
        with pytest.raises(ResearchRegistryError, match="could not be loaded"):
            load_paper_execution_report(result.report, registry=memory)

    def alter_report_read(
        path: Path,
        encoding: str | None = None,
        errors: str | None = None,
    ) -> str:
        if path == report_path:
            return "different stored content\n"
        return real_read_text(path, encoding=encoding, errors=errors)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "read_text", alter_report_read)
        with pytest.raises(ResearchRegistryError, match="content does not match"):
            load_paper_execution_report(result.report, registry=memory)


def test_phase6_render_rejects_empty_or_foreign_outcome_chain(tmp_path: Path) -> None:
    store, readiness, session = _session(tmp_path)
    memory = PaperExecutionMemoryRegistry(store)
    instruction = make_instruction()
    first = run_paper_submission_workflow(
        assessment=session.assessment,
        invocation_id="phase6-render-first",
        instruction=instruction,
        approval=make_approval(instruction),
        readiness_registry=readiness,
        execution_registry=memory,
        service=cast(PaperExecutionService, _Service(submit_receipt=make_receipt(instruction))),
        broker=cast(PaperBrokerProtocol, FakePaperBroker()),
    )
    with pytest.raises(ValueError, match="at least one outcome"):
        render_paper_execution_report(first.authorization, ())

    second_store, second_readiness, second_session = _session(tmp_path / "second")
    second_memory = PaperExecutionMemoryRegistry(second_store)
    second_instruction = make_instruction(
        signal_id="signal-render-second",
        client_order_id="paper-order-render-second",
    )
    second = run_paper_submission_workflow(
        assessment=second_session.assessment,
        invocation_id="phase6-render-second",
        instruction=second_instruction,
        approval=make_approval(second_instruction, approval_id="approval-render-second"),
        readiness_registry=second_readiness,
        execution_registry=second_memory,
        service=cast(
            PaperExecutionService,
            _Service(submit_receipt=make_receipt(second_instruction)),
        ),
        broker=cast(PaperBrokerProtocol, FakePaperBroker()),
    )
    with pytest.raises(ValueError, match="exact authorization"):
        render_paper_execution_report(first.authorization, second.outcomes)


def test_phase6_report_write_and_load_reject_substituted_links(tmp_path: Path) -> None:
    store, readiness, session = _session(tmp_path)
    memory = PaperExecutionMemoryRegistry(store)
    instruction = make_instruction()
    result = run_paper_submission_workflow(
        assessment=session.assessment,
        invocation_id="phase6-report-link-validation",
        instruction=instruction,
        approval=make_approval(instruction),
        readiness_registry=readiness,
        execution_registry=memory,
        service=cast(PaperExecutionService, _Service(submit_receipt=make_receipt(instruction))),
        broker=cast(PaperBrokerProtocol, FakePaperBroker()),
    )
    altered_outcome = result.outcomes[-1].model_copy(update={"failure_code": "substituted"})
    with pytest.raises(ResearchRegistryError, match="exact stored Phase 6 outcome"):
        write_paper_execution_report(altered_outcome, registry=memory)

    wrong_session = result.report.model_copy(
        update={"paper_execution_session_id": "aq-paper-execution-session-000000000000000000000000"}
    )
    with pytest.raises(ResearchRegistryError, match="exact execution session"):
        load_paper_execution_report(wrong_session, registry=memory)

    wrong_path = result.report.model_copy(update={"relative_path": "artifacts/fake/report.md"})
    with pytest.raises(ResearchRegistryError, match="canonical artifact path"):
        load_paper_execution_report(wrong_path, registry=memory)

    wrong_disposition = result.report.model_copy(update={"final_disposition": "blocked"})
    with pytest.raises(ResearchRegistryError, match="final outcome"):
        load_paper_execution_report(wrong_disposition, registry=memory)


def test_phase6_older_unknown_report_cannot_replace_latest_reconciliation_report(
    tmp_path: Path,
) -> None:
    store, readiness, session = _session(tmp_path)
    memory = PaperExecutionMemoryRegistry(store)
    instruction = make_instruction()
    approval = make_approval(instruction)
    submitted = run_paper_submission_workflow(
        assessment=session.assessment,
        invocation_id="phase6-report-latest",
        instruction=instruction,
        approval=approval,
        readiness_registry=readiness,
        execution_registry=memory,
        service=cast(
            PaperExecutionService,
            _Service(
                submit_error=PaperExecutionSubmissionUnknownError(
                    "submission_outcome_unknown",
                    "unknown",
                )
            ),
        ),
        broker=cast(PaperBrokerProtocol, FakePaperBroker()),
    )
    run_paper_reconciliation_workflow(
        authorization=submitted.authorization,
        execution_registry=memory,
        service=cast(
            PaperExecutionService,
            _Service(reconcile_receipt=make_receipt(instruction)),
        ),
        broker=cast(PaperBrokerProtocol, FakePaperBroker()),
        now_utc=BROKER_TIME,
    )

    with pytest.raises(ResearchRegistryError, match="latest stored outcome"):
        write_paper_execution_report(submitted.outcomes[-1], registry=memory)


def test_phase6_reconciliation_rejects_substituted_authorization(tmp_path: Path) -> None:
    store, readiness, session = _session(tmp_path)
    memory = PaperExecutionMemoryRegistry(store)
    instruction = make_instruction()
    submitted = run_paper_submission_workflow(
        assessment=session.assessment,
        invocation_id="phase6-reconcile-substitution",
        instruction=instruction,
        approval=make_approval(instruction),
        readiness_registry=readiness,
        execution_registry=memory,
        service=cast(
            PaperExecutionService,
            _Service(
                submit_error=PaperExecutionSubmissionUnknownError(
                    "submission_outcome_unknown",
                    "unknown",
                )
            ),
        ),
        broker=cast(PaperBrokerProtocol, FakePaperBroker()),
    )
    substituted = submitted.authorization.model_copy(update={"signal_id": "signal-substituted"})

    with pytest.raises(ResearchRegistryError, match="exact stored Phase 6 authorization"):
        run_paper_reconciliation_workflow(
            authorization=substituted,
            execution_registry=memory,
            service=cast(
                PaperExecutionService,
                _Service(reconcile_receipt=make_receipt(instruction)),
            ),
            broker=cast(PaperBrokerProtocol, FakePaperBroker()),
            now_utc=BROKER_TIME,
        )
