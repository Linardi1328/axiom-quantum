from __future__ import annotations

from datetime import UTC, datetime
from inspect import getsource
from pathlib import Path

import pytest

from spy_market_agent.benchmark.artifacts import sha256_bytes
from spy_market_agent.intelligence.axiom_reporting import (
    IntelligenceWorkflowResult,
    intelligence_report_name,
)
from spy_market_agent.research.artifacts import ResearchArtifactStore
from spy_market_agent.research.errors import ResearchArtifactError, ResearchRegistryError
from spy_market_agent.supervision import (
    HumanReviewDisposition,
    SupervisedDisposition,
    SupervisedReviewItem,
    SupervisedSession,
    SupervisionMemoryRegistry,
    build_supervised_disposition,
    build_supervised_review_item,
    build_supervised_session,
)
from unit.test_axiom_phase4_supervision_session import _phase3_result


def _stored_chain(
    tmp_path: Path,
    *,
    actionable: bool = True,
    invocation_id: str = "memory-session",
) -> tuple[
    ResearchArtifactStore,
    IntelligenceWorkflowResult,
    SupervisionMemoryRegistry,
    SupervisedSession,
    SupervisedReviewItem,
    SupervisedDisposition,
]:
    store, phase3 = _phase3_result(tmp_path, actionable=actionable)
    memory = SupervisionMemoryRegistry(store)
    session = build_supervised_session(
        report=phase3.report,
        invocation_id=invocation_id,
        registry=memory.intelligence_memory,
    )
    review = build_supervised_review_item(
        session=session,
        registry=memory.intelligence_memory,
    )
    disposition = build_supervised_disposition(
        review_item=review,
        disposition=(
            HumanReviewDisposition.OBSERVED
            if actionable
            else HumanReviewDisposition.ABSTENTION_ACKNOWLEDGED
        ),
        human_review_reference="memory-review",
        recorded_at=datetime(2026, 10, 7, 6, 0, tzinfo=UTC),
        registry=memory.intelligence_memory,
    )
    return store, phase3, memory, session, review, disposition


def test_memory_round_trips_complete_supervision_chain(tmp_path: Path) -> None:
    """Slice 4 stores and reloads one exact append-only supervision chain."""

    _, _, memory, session, review, disposition = _stored_chain(tmp_path)

    assert memory.record_session(session) == session.supervision_session_id
    assert memory.record_review_item(review) == review.review_item_id
    assert memory.record_disposition(disposition) == disposition.disposition_id

    assert memory.load_session(
        session.experiment_id,
        session.supervision_session_id,
    ) == session
    assert memory.load_review_item(
        review.experiment_id,
        review.review_item_id,
    ) == review
    assert memory.load_disposition(
        disposition.experiment_id,
        disposition.disposition_id,
    ) == disposition
    assert memory.list_session_ids(session.experiment_id) == (
        session.supervision_session_id,
    )
    assert memory.list_review_item_ids(review.experiment_id) == (
        review.review_item_id,
    )
    assert memory.list_disposition_ids(disposition.experiment_id) == (
        disposition.disposition_id,
    )
    assert disposition.execution_authority == "none"


def test_memory_writes_are_idempotent_but_conflicts_fail_closed(tmp_path: Path) -> None:
    """Identical bytes may repeat, but conflicting content cannot replace a record."""

    store, _, memory, session, _, _ = _stored_chain(tmp_path)
    memory.record_session(session)
    memory.record_session(session)

    name = memory._session_name(session.supervision_session_id)
    store.write_json(
        session.experiment_id,
        name,
        {"tampered": True},
        allow_replace=True,
    )

    with pytest.raises(ResearchArtifactError, match="conflicts"):
        memory.record_session(session)


def test_review_reload_rejects_substituted_session_parent(tmp_path: Path) -> None:
    """A stored review item cannot survive substitution of its exact session parent."""

    store, _, memory, first_session, review, _ = _stored_chain(tmp_path)
    memory.record_session(first_session)
    memory.record_review_item(review)

    _, _, _, second_session, _, _ = _stored_chain(
        tmp_path / "second",
        invocation_id="different-memory-session",
    )
    assert second_session.supervision_session_id != first_session.supervision_session_id
    store.write_json(
        first_session.experiment_id,
        memory._session_name(first_session.supervision_session_id),
        second_session,
        allow_replace=True,
    )

    with pytest.raises(ResearchRegistryError, match="identity and experiment"):
        memory.load_review_item(review.experiment_id, review.review_item_id)


def test_disposition_reload_rejects_corrupt_review_parent(tmp_path: Path) -> None:
    """Malformed stored review bytes fail before a human disposition can reload."""

    store, _, memory, session, review, disposition = _stored_chain(tmp_path)
    memory.record_session(session)
    memory.record_review_item(review)
    memory.record_disposition(disposition)
    store.write_json(
        review.experiment_id,
        memory._review_name(review.review_item_id),
        {"broken": True},
        allow_replace=True,
    )

    with pytest.raises(ResearchRegistryError, match="failed canonical validation"):
        memory.load_disposition(
            disposition.experiment_id,
            disposition.disposition_id,
        )


def test_memory_reverifies_phase3_report_bytes_on_load(tmp_path: Path) -> None:
    """Stored Phase 4 records become unusable if their Phase 3 report is tampered."""

    store, phase3, memory, session, _, _ = _stored_chain(tmp_path)
    memory.record_session(session)
    altered = b"tampered after Phase 4 persistence\n"
    store.write_bytes(
        phase3.session.experiment_id,
        intelligence_report_name(phase3.assessment),
        altered,
        expected_checksum=sha256_bytes(altered),
        allow_replace=True,
    )

    with pytest.raises(ResearchRegistryError, match="checksum does not match"):
        memory.load_session(
            session.experiment_id,
            session.supervision_session_id,
        )


def test_memory_lists_identities_in_sorted_order(tmp_path: Path) -> None:
    """Identity listings are deterministic for one experiment."""

    store, phase3, memory, first, _, _ = _stored_chain(tmp_path)
    second = build_supervised_session(
        report=phase3.report,
        invocation_id="memory-session-two",
        registry=memory.intelligence_memory,
    )
    memory.record_session(first)
    memory.record_session(second)

    assert memory.list_session_ids(first.experiment_id) == tuple(
        sorted((first.supervision_session_id, second.supervision_session_id))
    )
    assert store.existing_artifacts(first.experiment_id)


def test_memory_rejects_noncanonical_identity_requests(tmp_path: Path) -> None:
    """Path-facing loaders reject malformed supervision identities."""

    _, _, memory, session, _, _ = _stored_chain(tmp_path)
    with pytest.raises(
        ResearchRegistryError,
        match="canonical Axiom supervision identity",
    ):
        memory.load_session(session.experiment_id, "../unsafe")


def test_memory_module_is_authority_free() -> None:
    """Slice 4 persistence contains no trading or unattended-operation machinery."""

    from spy_market_agent.supervision import memory as memory_module

    source = getsource(memory_module)
    forbidden = (
        "spy_market_agent.execution",
        "spy_market_agent.paper_ops",
        "TradingClient",
        "submit_order",
        "position_target",
        "quantity",
        "leverage",
        "scheduler",
        "notification",
    )
    for marker in forbidden:
        assert marker not in source
