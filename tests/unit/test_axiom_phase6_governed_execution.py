from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

import spy_market_agent.paper_ops.execution_bridge as bridge_module
from spy_market_agent.execution import (
    PaperExecutionBrokerRejectionError,
    PaperExecutionService,
    PaperExecutionSubmissionUnknownError,
)
from spy_market_agent.execution.models import (
    PaperOrderApproval,
    PaperOrderInstruction,
    PaperOrderReceipt,
)
from spy_market_agent.execution.protocols import PaperBrokerProtocol
from spy_market_agent.paper_ops import (
    PaperReadinessMemoryRegistry,
    PaperSubmissionAuthorization,
    build_paper_submission_authorization,
    reconcile_authorized_paper_order,
    submit_authorized_paper_order,
)
from unit.phase8_helpers import (
    BROKER_TIME,
    FakePaperBroker,
    make_approval,
    make_instruction,
    make_receipt,
)
from unit.test_axiom_phase6_paper_submission_authorization import _session


class _ClaimRegistry:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0
        self.authorization_ids: list[str] = []

    def claim_submission(self, authorization: PaperSubmissionAuthorization) -> None:
        self.calls += 1
        self.authorization_ids.append(authorization.paper_submission_authorization_id)
        if self.fail:
            raise ValueError("authorization already consumed")


class _Service:
    def __init__(
        self,
        *,
        submit_receipt: PaperOrderReceipt | None = None,
        submit_error: BaseException | None = None,
        reconcile_receipt: PaperOrderReceipt | None = None,
        reconcile_error: BaseException | None = None,
    ) -> None:
        self.submit_receipt = submit_receipt
        self.submit_error = submit_error
        self.reconcile_receipt = reconcile_receipt
        self.reconcile_error = reconcile_error
        self.submit_calls = 0
        self.reconcile_calls = 0

    def submit_approved_order(self, *args: object, **kwargs: object) -> PaperOrderReceipt:
        self.submit_calls += 1
        if self.submit_error is not None:
            raise self.submit_error
        assert self.submit_receipt is not None
        return self.submit_receipt

    def reconcile_by_client_order_id(
        self,
        client_order_id: str,
        **kwargs: object,
    ) -> PaperOrderReceipt | None:
        self.reconcile_calls += 1
        if self.reconcile_error is not None:
            raise self.reconcile_error
        return self.reconcile_receipt


def _authorization(
    tmp_path: Path,
) -> tuple[
    PaperReadinessMemoryRegistry,
    PaperSubmissionAuthorization,
    PaperOrderInstruction,
    PaperOrderApproval,
]:
    _, memory, session = _session(tmp_path)
    instruction = make_instruction()
    approval = make_approval(instruction)
    authorization = build_paper_submission_authorization(
        session=session,
        instruction=instruction,
        approval=approval,
        registry=memory,
    )
    return memory, authorization, instruction, approval


def test_governed_submission_claims_once_and_calls_service_once(tmp_path: Path) -> None:
    memory, authorization, instruction, approval = _authorization(tmp_path)
    claim = _ClaimRegistry()
    service = _Service(submit_receipt=make_receipt(instruction))
    broker = FakePaperBroker()

    outcome = submit_authorized_paper_order(
        authorization=authorization,
        instruction=instruction,
        approval=approval,
        registry=memory,
        claim_registry=claim,
        service=cast(PaperExecutionService, service),
        broker=cast(PaperBrokerProtocol, broker),
    )

    assert claim.calls == 1
    assert claim.authorization_ids == [authorization.paper_submission_authorization_id]
    assert service.submit_calls == 1
    assert service.reconcile_calls == 0
    assert outcome.disposition == "accepted"
    assert outcome.receipt_checksum is not None
    assert outcome.failure_code is None
    assert outcome.reconciliation_lookup_only is False


def test_consumed_authorization_blocks_before_service_submission(tmp_path: Path) -> None:
    memory, authorization, instruction, approval = _authorization(tmp_path)
    claim = _ClaimRegistry(fail=True)
    service = _Service(submit_receipt=make_receipt(instruction))

    with pytest.raises(ValueError, match="already consumed"):
        submit_authorized_paper_order(
            authorization=authorization,
            instruction=instruction,
            approval=approval,
            registry=memory,
            claim_registry=claim,
            service=cast(PaperExecutionService, service),
            broker=cast(PaperBrokerProtocol, FakePaperBroker()),
        )

    assert claim.calls == 1
    assert service.submit_calls == 0


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (PaperExecutionBrokerRejectionError("broker_rejected", "rejected"), "rejected"),
        (
            PaperExecutionSubmissionUnknownError("submission_outcome_unknown", "unknown"),
            "submission_unknown",
        ),
    ],
)
def test_governed_submission_records_fail_closed_outcomes(
    tmp_path: Path,
    error: BaseException,
    expected: str,
) -> None:
    memory, authorization, instruction, approval = _authorization(tmp_path)
    service = _Service(submit_error=error)

    outcome = submit_authorized_paper_order(
        authorization=authorization,
        instruction=instruction,
        approval=approval,
        registry=memory,
        claim_registry=_ClaimRegistry(),
        service=cast(PaperExecutionService, service),
        broker=cast(PaperBrokerProtocol, FakePaperBroker()),
    )

    assert service.submit_calls == 1
    assert outcome.disposition == expected
    assert outcome.failure_code is not None
    assert outcome.receipt_checksum is None


def test_reconciliation_is_lookup_only_and_never_submits(tmp_path: Path) -> None:
    memory, authorization, instruction, _ = _authorization(tmp_path)
    receipt = make_receipt(instruction)
    service = _Service(reconcile_receipt=receipt)

    outcome = reconcile_authorized_paper_order(
        authorization=authorization,
        registry=memory,
        service=cast(PaperExecutionService, service),
        broker=cast(PaperBrokerProtocol, FakePaperBroker()),
        now_utc=BROKER_TIME,
    )

    assert service.reconcile_calls == 1
    assert service.submit_calls == 0
    assert outcome.disposition == "reconciled"
    assert outcome.reconciliation_lookup_only is True
    assert outcome.receipt_checksum is not None


def test_reconciliation_not_found_remains_submission_unknown(tmp_path: Path) -> None:
    memory, authorization, _, _ = _authorization(tmp_path)
    service = _Service(reconcile_receipt=None)

    outcome = reconcile_authorized_paper_order(
        authorization=authorization,
        registry=memory,
        service=cast(PaperExecutionService, service),
        broker=cast(PaperBrokerProtocol, FakePaperBroker()),
        now_utc=BROKER_TIME,
    )

    assert service.reconcile_calls == 1
    assert service.submit_calls == 0
    assert outcome.disposition == "submission_unknown"
    assert outcome.failure_code == "reconciliation_order_not_found"
    assert outcome.reconciliation_lookup_only is True


def test_execution_bridge_rejects_substituted_legacy_pair_before_claim(tmp_path: Path) -> None:
    memory, authorization, _, _ = _authorization(tmp_path)
    substituted = make_instruction(client_order_id="paper-order-substitution")
    claim = _ClaimRegistry()
    service = _Service(submit_receipt=make_receipt(substituted))

    with pytest.raises(ValueError, match="does not match authorization"):
        submit_authorized_paper_order(
            authorization=authorization,
            instruction=substituted,
            approval=make_approval(substituted, approval_id="approval-substitution"),
            registry=memory,
            claim_registry=claim,
            service=cast(PaperExecutionService, service),
            broker=cast(PaperBrokerProtocol, FakePaperBroker()),
        )

    assert claim.calls == 0
    assert service.submit_calls == 0


def test_phase6_execution_bridge_has_no_hidden_runtime_or_live_client() -> None:
    source = bridge_module.__file__
    assert source is not None
    text = Path(source).read_text(encoding="utf-8").lower()

    for forbidden in (
        "tradingclient",
        "alpaca_paper",
        "scheduler",
        "cron",
        "background",
        "while true",
        "live trading",
    ):
        assert forbidden not in text
