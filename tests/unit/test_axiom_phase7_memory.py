"""Phase 7 memory must verify exact ancestors and reject duplicate dates/tamper."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from spy_market_agent.phase7_validation.memory import Phase7ValidationMemoryRegistry
from spy_market_agent.phase7_validation.pilot import build_phase7_pilot_observation
from spy_market_agent.phase7_validation.safety import build_phase7_safety_evidence
from spy_market_agent.phase7_validation.session import build_phase7_validation_session
from unit.test_axiom_phase7_pilot import _parents
from unit.test_axiom_phase7_safety import _fixture


def test_pilot_memory_reloads_exact_session_and_abstention(tmp_path: Path) -> None:
    session, execution = _parents(tmp_path)
    memory = Phase7ValidationMemoryRegistry(execution.store)
    assert memory.record_session(session) == session.validation_session_id
    assert memory.load_session(session.experiment_id, session.validation_session_id) == session

    record = build_phase7_pilot_observation(
        session=session,
        registry=execution,
        state="abstained",
        abstain_reason="no_qualifying_setup",
        human_reviewed=True,
        attestation_ref="operator-abstain",
    )
    assert memory.record_pilot(record) == record.pilot_observation_id
    assert memory.load_pilot(record.experiment_id, record.pilot_observation_id) == record
    assert memory.list_pilots(record.experiment_id) == (record,)
    assert memory.list_session_ids(record.experiment_id) == (session.validation_session_id,)
    assert memory.list_pilot_ids(record.experiment_id) == (record.pilot_observation_id,)
    assert memory.record_pilot(record) == record.pilot_observation_id


def test_pilot_memory_rejects_second_record_on_same_paper_date(tmp_path: Path) -> None:
    session, execution = _parents(tmp_path)
    memory = Phase7ValidationMemoryRegistry(execution.store)
    memory.record_session(session)
    first = build_phase7_pilot_observation(
        session=session,
        registry=execution,
        state="abstained",
        human_reviewed=True,
        attestation_ref="first",
        abstain_reason="no_qualifying_setup",
    )
    memory.record_pilot(first)

    other_session = build_phase7_validation_session(
        assessment=session.assessment,
        invocation_id="pilot-another-session",
        observation_date=session.observation_date,
        mode="paper_broker",
        registry=memory.readiness_memory,
    )
    memory.record_session(other_session)
    second = build_phase7_pilot_observation(
        session=other_session,
        registry=execution,
        state="abstained",
        human_reviewed=True,
        attestation_ref="second",
        abstain_reason="safety_blocked",
    )
    with pytest.raises(ValueError, match="already claimed"):
        memory.record_pilot(second)
    assert memory.list_pilots(session.experiment_id) == (first,)


def test_safety_memory_reloads_verified_synthetic_parent(tmp_path: Path) -> None:
    session, readiness, probes = _fixture(tmp_path)
    memory = Phase7ValidationMemoryRegistry(readiness.store)
    memory.record_session(session)
    evidence = build_phase7_safety_evidence(session=session, registry=readiness, probes=probes)
    assert memory.record_safety(evidence) == evidence.safety_evidence_id
    assert memory.load_safety(evidence.experiment_id, evidence.safety_evidence_id) == evidence
    assert memory.list_safety_ids(evidence.experiment_id) == (evidence.safety_evidence_id,)
    assert memory.record_safety(evidence) == evidence.safety_evidence_id


def test_memory_rejects_mutated_pilot_payload_and_guard(tmp_path: Path) -> None:
    session, execution = _parents(tmp_path)
    memory = Phase7ValidationMemoryRegistry(execution.store)
    memory.record_session(session)
    record = build_phase7_pilot_observation(
        session=session,
        registry=execution,
        state="abstained",
        human_reviewed=True,
        attestation_ref="operator",
        abstain_reason="no_qualifying_setup",
    )
    memory.record_pilot(record)
    experiment = record.experiment_id
    guard = memory._day_name(record.session)
    memory.store.write_json(
        experiment, guard, {"pilot_observation_id": "bogus"}, allow_replace=True
    )
    with pytest.raises(ValueError, match="date guard mismatch"):
        memory.load_pilot(experiment, record.pilot_observation_id)

    memory.store.write_json(
        experiment,
        guard,
        {"pilot_observation_id": record.pilot_observation_id},
        allow_replace=True,
    )
    memory.store.write_json(
        experiment,
        memory._pilot_name(record.pilot_observation_id),
        record.model_dump(mode="json") | {"attestation_ref": "changed"},
        allow_replace=True,
    )
    with pytest.raises(ValueError, match="canonical content"):
        memory.load_pilot(experiment, record.pilot_observation_id)


def test_memory_rejects_invalid_names_and_unstored_parents(tmp_path: Path) -> None:
    session, execution = _parents(tmp_path)
    memory = Phase7ValidationMemoryRegistry(execution.store)
    record = build_phase7_pilot_observation(
        session=session,
        registry=execution,
        state="abstained",
        human_reviewed=True,
        attestation_ref="operator",
        abstain_reason="no_qualifying_setup",
    )
    with pytest.raises(Exception, match="missing"):
        memory.record_pilot(record)
    with pytest.raises(ValueError, match="invalid Phase 7"):
        memory.load_session(record.experiment_id, "../bad-id")
    memory.store.write_json(
        record.experiment_id,
        "axiom_phase7_validation_pilot_invalid.json",
        {"untrusted": True},
    )
    with pytest.raises(ValueError, match="invalid Phase 7 memory artifact"):
        memory.list_pilot_ids(record.experiment_id)


def test_memory_rejects_corrupt_phase5_parent_on_reload(tmp_path: Path) -> None:
    session, execution = _parents(tmp_path)
    memory = Phase7ValidationMemoryRegistry(execution.store)
    memory.record_session(session)
    memory.store.write_json(
        session.experiment_id,
        memory.readiness_memory._assessment_name(session.assessment_id),
        {"tampered": True},
        allow_replace=True,
    )
    with pytest.raises(Exception, match="canonical validation"):
        memory.load_session(session.experiment_id, session.validation_session_id)


def test_different_paper_dates_are_distinct_and_sorted(tmp_path: Path) -> None:
    session, execution = _parents(tmp_path)
    memory = Phase7ValidationMemoryRegistry(execution.store)
    all_records = []
    for index, day in enumerate((date(2026, 10, 9), date(2026, 10, 8))):
        candidate = build_phase7_validation_session(
            assessment=session.assessment,
            invocation_id=f"operator-{index}",
            observation_date=day,
            mode="paper_broker",
            registry=memory.readiness_memory,
        )
        memory.record_session(candidate)
        record = build_phase7_pilot_observation(
            session=candidate,
            registry=execution,
            state="abstained",
            human_reviewed=True,
            attestation_ref=f"operator-{index}",
            abstain_reason="no_qualifying_setup",
        )
        memory.record_pilot(record)
        all_records.append(record)
    assert [
        item.session.observation_date for item in memory.list_pilots(session.experiment_id)
    ] == [date(2026, 10, 8), date(2026, 10, 9)]
    assert len(memory.list_pilot_ids(session.experiment_id)) == 2
