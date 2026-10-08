from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import cast

import pytest

from spy_market_agent.execution import (
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
)
from spy_market_agent.phase6_execution import (
    reconcile_authorized_paper_order,
    submit_authorized_paper_order,
)
from spy_market_agent.phase6_execution.memory import PaperExecutionMemoryRegistry
from spy_market_agent.research.artifacts import ResearchArtifactStore
from spy_market_agent.research.errors import ResearchArtifactError, ResearchRegistryError
from unit.phase8_helpers import (
    BROKER_TIME,
    FakePaperBroker,
    make_approval,
    make_instruction,
    make_receipt,
)
from unit.test_axiom_phase6_paper_submission_authorization import _session


class _Service:
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
        self.reconcile_calls += 1
        return self.reconcile_receipt


def _chain(
    tmp_path: Path,
) -> tuple[
    ResearchArtifactStore,
    PaperReadinessMemoryRegistry,
    PaperExecutionMemoryRegistry,
    PaperSubmissionAuthorization,
    PaperOrderInstruction,
    PaperOrderApproval,
]:
    store, readiness, session = _session(tmp_path)
    memory = PaperExecutionMemoryRegistry(store)
    memory.record_session(session)
    instruction = make_instruction()
    approval = make_approval(instruction)
    authorization = build_paper_submission_authorization(
        session=session,
        instruction=instruction,
        approval=approval,
        registry=readiness,
    )
    memory.record_authorization(authorization)
    return store, readiness, memory, authorization, instruction, approval


def test_phase6_memory_round_trips_exact_session_and_authorization(tmp_path: Path) -> None:
    _, _, memory, authorization, _, _ = _chain(tmp_path)

    assert (
        memory.load_session(
            authorization.experiment_id,
            authorization.paper_execution_session_id,
        )
        == authorization.session
    )
    assert (
        memory.load_authorization(
            authorization.experiment_id,
            authorization.paper_submission_authorization_id,
        )
        == authorization
    )
    assert memory.list_session_ids(authorization.experiment_id) == (
        authorization.paper_execution_session_id,
    )
    assert memory.list_authorization_ids(authorization.experiment_id) == (
        authorization.paper_submission_authorization_id,
    )


def test_phase6_authorization_consumption_is_single_use(tmp_path: Path) -> None:
    _, _, memory, authorization, _, _ = _chain(tmp_path)

    memory.claim_submission(authorization)
    consumption = memory.load_consumption_for_authorization(authorization)

    assert consumption.paper_submission_authorization_id == (
        authorization.paper_submission_authorization_id
    )
    assert consumption.use_policy == "single_use"
    assert memory.list_consumption_ids(authorization.experiment_id) == (
        consumption.paper_authorization_consumption_id,
    )
    with pytest.raises(ResearchRegistryError, match="already been consumed"):
        memory.claim_submission(authorization)


def test_phase6_memory_records_one_accepted_submission_outcome(tmp_path: Path) -> None:
    _, readiness, memory, authorization, instruction, approval = _chain(tmp_path)
    service = _Service(submit_receipt=make_receipt(instruction))

    outcome = submit_authorized_paper_order(
        authorization=authorization,
        instruction=instruction,
        approval=approval,
        registry=readiness,
        claim_registry=memory,
        service=cast(PaperExecutionService, service),
        broker=cast(PaperBrokerProtocol, FakePaperBroker()),
    )
    memory.record_outcome(outcome)

    assert (
        memory.load_outcome(
            authorization.experiment_id,
            outcome.paper_execution_outcome_id,
        )
        == outcome
    )
    assert memory.list_outcome_ids_for_authorization(authorization) == (
        outcome.paper_execution_outcome_id,
    )
    with pytest.raises(ResearchRegistryError, match="only one submission outcome"):
        memory.record_outcome(outcome)


def test_phase6_memory_allows_lookup_only_reconciliation_after_unknown(
    tmp_path: Path,
) -> None:
    _, readiness, memory, authorization, instruction, approval = _chain(tmp_path)
    submit_service = _Service(
        submit_error=PaperExecutionSubmissionUnknownError(
            "submission_outcome_unknown",
            "unknown",
        )
    )
    unknown = submit_authorized_paper_order(
        authorization=authorization,
        instruction=instruction,
        approval=approval,
        registry=readiness,
        claim_registry=memory,
        service=cast(PaperExecutionService, submit_service),
        broker=cast(PaperBrokerProtocol, FakePaperBroker()),
    )
    memory.record_outcome(unknown)

    reconcile_service = _Service(reconcile_receipt=make_receipt(instruction))
    reconciled = reconcile_authorized_paper_order(
        authorization=authorization,
        registry=readiness,
        service=cast(PaperExecutionService, reconcile_service),
        broker=cast(PaperBrokerProtocol, FakePaperBroker()),
        now_utc=BROKER_TIME,
    )
    memory.record_outcome(reconciled)

    assert unknown.disposition == "submission_unknown"
    assert reconciled.disposition == "reconciled"
    assert reconciled.reconciliation_lookup_only is True
    assert set(memory.list_outcome_ids_for_authorization(authorization)) == {
        unknown.paper_execution_outcome_id,
        reconciled.paper_execution_outcome_id,
    }
    assert reconcile_service.submit_calls == 0
    with pytest.raises(ResearchRegistryError, match="only one reconciliation"):
        memory.record_outcome(reconciled)


def test_phase6_memory_rejects_reconciliation_without_unknown_submission(
    tmp_path: Path,
) -> None:
    _, readiness, memory, authorization, instruction, _ = _chain(tmp_path)
    memory.claim_submission(authorization)
    service = _Service(reconcile_receipt=make_receipt(instruction))
    reconciled = reconcile_authorized_paper_order(
        authorization=authorization,
        registry=readiness,
        service=cast(PaperExecutionService, service),
        broker=cast(PaperBrokerProtocol, FakePaperBroker()),
        now_utc=BROKER_TIME,
    )

    with pytest.raises(ResearchRegistryError, match="prior submission_unknown"):
        memory.record_outcome(reconciled)


def test_phase6_memory_tamper_fails_closed(tmp_path: Path) -> None:
    store, _, memory, authorization, _, _ = _chain(tmp_path)
    store.write_json(
        authorization.experiment_id,
        memory._authorization_name(authorization.paper_submission_authorization_id),
        {"tampered": True},
        allow_replace=True,
    )

    with pytest.raises(ResearchRegistryError, match="canonical validation"):
        memory.load_authorization(
            authorization.experiment_id,
            authorization.paper_submission_authorization_id,
        )


def test_phase6_memory_conflicting_session_bytes_cannot_replace(tmp_path: Path) -> None:
    store, _, memory, authorization, _, _ = _chain(tmp_path)
    store.write_json(
        authorization.experiment_id,
        memory._session_name(authorization.paper_execution_session_id),
        {"tampered": True},
        allow_replace=True,
    )

    with pytest.raises(ResearchArtifactError, match="conflicts"):
        memory.record_session(authorization.session)

def test_phase6_authorization_consumption_is_atomic_under_concurrency(tmp_path: Path) -> None:
    _, _, memory, authorization, _, _ = _chain(tmp_path)
    barrier = threading.Barrier(2)

    def claim_once() -> bool:
        barrier.wait()
        try:
            memory.claim_submission(authorization)
        except ResearchRegistryError as exc:
            assert exc.code == "paper_submission_authorization_already_consumed"
            return False
        return True

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(lambda _index: claim_once(), range(2)))

    assert results.count(True) == 1
    assert results.count(False) == 1


def test_phase6_submission_outcome_slot_is_atomic_under_concurrency(tmp_path: Path) -> None:
    _, readiness, memory, authorization, instruction, approval = _chain(tmp_path)
    service = _Service(submit_receipt=make_receipt(instruction))
    outcome = submit_authorized_paper_order(
        authorization=authorization,
        instruction=instruction,
        approval=approval,
        registry=readiness,
        claim_registry=memory,
        service=cast(PaperExecutionService, service),
        broker=cast(PaperBrokerProtocol, FakePaperBroker()),
    )
    barrier = threading.Barrier(2)

    def record_once() -> bool:
        barrier.wait()
        try:
            memory.record_outcome(outcome)
        except ResearchRegistryError as exc:
            assert exc.code == "paper_submission_outcome_already_recorded"
            return False
        return True

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(lambda _index: record_once(), range(2)))

    assert results.count(True) == 1
    assert results.count(False) == 1

