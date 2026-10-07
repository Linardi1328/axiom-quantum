from __future__ import annotations

from dataclasses import dataclass
from inspect import getsource
from pathlib import Path

import pytest

from spy_market_agent.benchmark.artifacts import sha256_bytes
from spy_market_agent.paper_ops import (
    PaperReadinessAssessment,
    PaperReadinessMemoryRegistry,
    PaperReadinessSession,
    PaperRecoveryCase,
    build_paper_readiness_session,
    build_paper_recovery_case,
)
from spy_market_agent.research.artifacts import ResearchArtifactStore
from spy_market_agent.research.errors import ResearchArtifactError, ResearchRegistryError
from spy_market_agent.supervision.reporting import supervision_report_name
from unit.test_axiom_phase5_paper_readiness_assessment import _assessment


@dataclass(frozen=True)
class _StoredAttempt:
    attempt_status: str
    client_order_id: str
    paper_readiness_assessment_id: str


def _memory_chain(
    tmp_path: Path,
) -> tuple[
    ResearchArtifactStore,
    PaperReadinessMemoryRegistry,
    PaperReadinessSession,
    PaperReadinessAssessment,
    PaperRecoveryCase,
]:
    """Build one complete canonical Phase 5 chain for memory tests."""

    store, _, assessment = _assessment(tmp_path)
    memory = PaperReadinessMemoryRegistry(store)
    session = assessment.session
    recovery_case = build_paper_recovery_case(
        assessment=assessment,
        attempt=_StoredAttempt(
            attempt_status="submission_unknown",
            client_order_id="phase5-memory-order",
            paper_readiness_assessment_id=assessment.assessment_id,
        ),
        operator_reference="phase5-memory-review",
        registry=memory.supervision_memory,
    )
    return store, memory, session, assessment, recovery_case


def test_memory_round_trips_complete_readiness_chain(tmp_path: Path) -> None:
    """Slice 4 stores and reloads one exact append-only readiness chain."""

    _, memory, session, assessment, recovery_case = _memory_chain(tmp_path)

    assert memory.record_session(session) == session.paper_readiness_session_id
    assert memory.record_assessment(assessment) == assessment.assessment_id
    assert memory.record_recovery_case(recovery_case) == recovery_case.recovery_case_id

    assert memory.load_session(session.experiment_id, session.paper_readiness_session_id) == session
    assert memory.load_assessment(assessment.experiment_id, assessment.assessment_id) == assessment
    assert (
        memory.load_recovery_case(recovery_case.experiment_id, recovery_case.recovery_case_id)
        == recovery_case
    )
    assert memory.list_session_ids(session.experiment_id) == (session.paper_readiness_session_id,)
    assert memory.list_assessment_ids(assessment.experiment_id) == (assessment.assessment_id,)
    assert memory.list_recovery_case_ids(recovery_case.experiment_id) == (
        recovery_case.recovery_case_id,
    )
    assert recovery_case.execution_authority == "none"


def test_memory_writes_are_idempotent_but_conflicts_fail_closed(tmp_path: Path) -> None:
    """Identical records may repeat, while conflicting stored bytes cannot be replaced."""

    store, memory, session, _, _ = _memory_chain(tmp_path)
    memory.record_session(session)
    memory.record_session(session)

    store.write_json(
        session.experiment_id,
        memory._session_name(session.paper_readiness_session_id),
        {"tampered": True},
        allow_replace=True,
    )

    with pytest.raises(ResearchArtifactError, match="conflicts"):
        memory.record_session(session)


def test_assessment_reload_rejects_substituted_session_parent(tmp_path: Path) -> None:
    """A stored assessment cannot survive substitution of its exact session parent."""

    store, memory, session, assessment, _ = _memory_chain(tmp_path)
    memory.record_session(session)
    memory.record_assessment(assessment)

    second_session = build_paper_readiness_session(
        report=session.report,
        invocation_id="phase5-memory-second",
        registry=memory.supervision_memory,
    )
    assert second_session.paper_readiness_session_id != session.paper_readiness_session_id
    store.write_json(
        session.experiment_id,
        memory._session_name(session.paper_readiness_session_id),
        second_session,
        allow_replace=True,
    )

    with pytest.raises(ResearchRegistryError, match="identity and experiment"):
        memory.load_assessment(assessment.experiment_id, assessment.assessment_id)


def test_recovery_reload_rejects_corrupt_assessment_parent(tmp_path: Path) -> None:
    """Malformed stored assessment bytes fail before recovery evidence can reload."""

    store, memory, session, assessment, recovery_case = _memory_chain(tmp_path)
    memory.record_session(session)
    memory.record_assessment(assessment)
    memory.record_recovery_case(recovery_case)
    store.write_json(
        assessment.experiment_id,
        memory._assessment_name(assessment.assessment_id),
        {"broken": True},
        allow_replace=True,
    )

    with pytest.raises(ResearchRegistryError, match="failed canonical validation"):
        memory.load_recovery_case(
            recovery_case.experiment_id,
            recovery_case.recovery_case_id,
        )


def test_memory_reverifies_phase4_report_bytes_on_load(tmp_path: Path) -> None:
    """Stored Phase 5 state becomes unusable when the Phase 4 report bytes are tampered."""

    store, memory, session, _, _ = _memory_chain(tmp_path)
    memory.record_session(session)
    disposition = memory.supervision_memory.load_disposition(
        session.experiment_id,
        session.disposition_id,
    )
    altered = b"tampered after Phase 5 persistence\n"
    store.write_bytes(
        session.experiment_id,
        supervision_report_name(disposition),
        altered,
        expected_checksum=sha256_bytes(altered),
        allow_replace=True,
    )

    with pytest.raises(ResearchRegistryError, match="checksum"):
        memory.load_session(
            session.experiment_id,
            session.paper_readiness_session_id,
        )


def test_memory_lists_readiness_sessions_in_sorted_order(tmp_path: Path) -> None:
    """Identity listings are deterministic for one experiment."""

    _, memory, first, _, _ = _memory_chain(tmp_path)
    second = build_paper_readiness_session(
        report=first.report,
        invocation_id="phase5-memory-list-two",
        registry=memory.supervision_memory,
    )
    memory.record_session(first)
    memory.record_session(second)

    assert memory.list_session_ids(first.experiment_id) == tuple(
        sorted((first.paper_readiness_session_id, second.paper_readiness_session_id))
    )


def test_memory_rejects_noncanonical_identity_requests(tmp_path: Path) -> None:
    """Path-facing loaders reject malformed Phase 5 identities."""

    _, memory, session, _, _ = _memory_chain(tmp_path)
    with pytest.raises(ResearchRegistryError, match="canonical Axiom Phase 5 identity"):
        memory.load_session(session.experiment_id, "../unsafe")


def test_memory_listing_rejects_noncanonical_prefixed_artifacts(tmp_path: Path) -> None:
    """Listings fail closed when a readiness-prefixed artifact has a malformed identity."""

    store, memory, session, _, _ = _memory_chain(tmp_path)
    store.write_json(
        session.experiment_id,
        "axiom_paper_readiness_session_not-canonical.json",
        {"unexpected": True},
        allow_replace=False,
    )

    with pytest.raises(ResearchRegistryError, match="artifact name is not canonical"):
        memory.list_session_ids(session.experiment_id)




def test_session_loader_rejects_omitted_default_bytes(tmp_path: Path) -> None:
    """Stored session bytes must match the complete canonical model serialization."""

    store, memory, session, _, _ = _memory_chain(tmp_path)
    memory.record_session(session)
    payload = session.model_dump(mode="json")
    payload.pop("execution_authority")
    store.write_json(
        session.experiment_id,
        memory._session_name(session.paper_readiness_session_id),
        payload,
        allow_replace=True,
    )

    with pytest.raises(ResearchRegistryError, match="bytes must be canonical"):
        memory.load_session(session.experiment_id, session.paper_readiness_session_id)


def test_assessment_loader_rejects_unknown_stored_fields(tmp_path: Path) -> None:
    """Unknown assessment fields cannot be normalized away by model validation."""

    store, memory, session, assessment, _ = _memory_chain(tmp_path)
    memory.record_session(session)
    memory.record_assessment(assessment)
    payload = assessment.model_dump(mode="json")
    payload["unexpected"] = True
    store.write_json(
        assessment.experiment_id,
        memory._assessment_name(assessment.assessment_id),
        payload,
        allow_replace=True,
    )

    with pytest.raises(ResearchRegistryError, match="bytes must be canonical"):
        memory.load_assessment(assessment.experiment_id, assessment.assessment_id)


def test_recovery_loader_rejects_omitted_default_bytes(tmp_path: Path) -> None:
    """Stored recovery bytes cannot omit canonical default fields."""

    store, memory, session, assessment, recovery_case = _memory_chain(tmp_path)
    memory.record_session(session)
    memory.record_assessment(assessment)
    memory.record_recovery_case(recovery_case)
    payload = recovery_case.model_dump(mode="json")
    payload.pop("execution_authority")
    store.write_json(
        recovery_case.experiment_id,
        memory._recovery_name(recovery_case.recovery_case_id),
        payload,
        allow_replace=True,
    )

    with pytest.raises(ResearchRegistryError, match="bytes must be canonical"):
        memory.load_recovery_case(recovery_case.experiment_id, recovery_case.recovery_case_id)


def test_memory_listing_rejects_prefixed_wrong_suffix(tmp_path: Path) -> None:
    """A readiness-prefixed artifact cannot evade validation by changing its suffix."""

    store, memory, session, _, _ = _memory_chain(tmp_path)
    store.write_text(
        session.experiment_id,
        "axiom_paper_readiness_session_not-canonical.md",
        "unexpected",
        allow_replace=False,
    )

    with pytest.raises(ResearchRegistryError, match="artifact name is not canonical"):
        memory.list_session_ids(session.experiment_id)


def test_slice4_memory_has_no_operational_authority() -> None:
    """Slice 4 persistence contains no broker or unattended-operation machinery."""

    from spy_market_agent.paper_ops import memory as memory_module

    source = getsource(memory_module)
    forbidden = (
        "TradingClient",
        "submit_order",
        "submit_market_day_order",
        "cancel_order",
        "position_target",
        "credentials",
        "schedule.every",
        "BackgroundScheduler",
        "send_notification",
    )
    for marker in forbidden:
        assert marker not in source
