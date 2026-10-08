"""Safety evidence must be synthetic, complete and fail-closed."""

from __future__ import annotations

from datetime import date
from inspect import getsource
from pathlib import Path

import pytest

import spy_market_agent.phase7_validation.safety as module
from spy_market_agent.benchmark.artifacts import sha256_json
from spy_market_agent.paper_ops.memory import PaperReadinessMemoryRegistry
from spy_market_agent.phase7_validation.safety import (
    Phase7SafetyEvidence,
    Phase7SafetyProbe,
    build_phase7_safety_evidence,
)
from spy_market_agent.phase7_validation.session import (
    PHASE7_CRITICAL_PROBES,
    Phase7ValidationSession,
    build_phase7_validation_session,
)
from unit.test_axiom_phase6_paper_execution_session import _stored_assessment


def _fixture(
    tmp_path: Path,
    *,
    mode: str = "synthetic",
) -> tuple[tuple[Phase7ValidationSession, PaperReadinessMemoryRegistry], tuple[Phase7SafetyProbe, ...]]:
    _, registry, assessment = _stored_assessment(tmp_path)
    session = build_phase7_validation_session(
        assessment=assessment,
        invocation_id="phase7-adversarial",
        observation_date=date(2026, 10, 8),
        mode=mode,  # type: ignore[arg-type]
        registry=registry,
    )
    probes = tuple(
        Phase7SafetyProbe(
            probe=name,  # type: ignore[arg-type]
            fixture_checksum=sha256_json({"fixture": name}),
            expected_code="blocked_as_expected",
            observed_code="blocked_as_expected",
        )
        for name in PHASE7_CRITICAL_PROBES
    )
    return (session, registry), probes


def test_safety_evidence_is_canonical_and_synthetic_only(tmp_path: Path) -> None:
    (session, registry), probes = _fixture(tmp_path)
    first = build_phase7_safety_evidence(
        session=session,
        probes=probes,
        registry=registry
    )
    second = build_phase7_safety_evidence(
        session=session,
        probes=probes,
        registry=registry
    )
    assert first == second
    assert first.passed
    assert first.execution_authority == "none"
    assert first.evidence_source == "synthetic_fixture"
    assert all(item.passed for item in first.probes)


def test_failed_probe_is_not_silently_marked_passed(tmp_path: Path) -> None:
    (session, registry), probes = _fixture(tmp_path)
    altered = probes[0].model_copy(update={"observed_code": "unexpected_success"})
    result = build_phase7_safety_evidence(
        session=session,
        probes=(altered, *probes[1:]),
        registry=registry,  # type: ignore[arg-type]
    )
    assert not result.passed
    assert not result.probes[0].passed
    assert (
        result.safety_evidence_id
        != build_phase7_safety_evidence(
            session=session,
            probes=probes,
            registry=registry,
        ).safety_evidence_id
    )


def test_safety_rejects_missing_duplicate_and_out_of_order_probes(tmp_path: Path) -> None:
    (session, registry), probes = _fixture(tmp_path)
    for invalid in (probes[:-1], (probes[0], *probes[1:-1], probes[0]), probes[::-1]):
        with pytest.raises(ValueError, match="all six ordered critical probes"):
            build_phase7_safety_evidence(
                session=session,
                probes=invalid,
                registry=registry
            )
    repeated = probes[1].model_copy(update={"fixture_checksum": probes[0].fixture_checksum})
    with pytest.raises(ValueError, match="must be distinct"):
        build_phase7_safety_evidence(
            session=session,
            probes=(probes[0], repeated, *probes[2:]),
            registry=registry,  # type: ignore[arg-type]
        )


def test_safety_rejects_paper_broker_mode(tmp_path: Path) -> None:
    (session, registry), probes = _fixture(tmp_path, mode="paper_broker")
    with pytest.raises(ValueError, match="synthetic validation session"):
        build_phase7_safety_evidence(
            session=session,
        probes=probes,
        registry=registry
        )


def test_safety_rejects_content_forgery_and_unsanitized_codes(tmp_path: Path) -> None:
    (session, registry), probes = _fixture(tmp_path)
    result = build_phase7_safety_evidence(
        session=session,
        probes=probes,
        registry=registry
    )
    payload = result.model_dump(mode="python") | {"experiment_id": "wrong"}
    with pytest.raises(ValueError, match="exact session"):
        Phase7SafetyEvidence.model_validate(payload)
    with pytest.raises(ValueError, match="canonical content"):
        Phase7SafetyEvidence.model_validate(
            result.model_dump(mode="python")
            | {"probes": (*probes[:-1], probes[-1].model_copy(update={"observed_code": "wrong"}))}
        )
    for bad in ("", "unsafe text", "SECRET=some_key"):
        with pytest.raises(ValueError, match="sanitized"):
            Phase7SafetyProbe(
                probe="kill_switch",
                fixture_checksum=sha256_json({"fixture": "kill_switch"}),
                expected_code=bad,
                observed_code="blocked_as_expected",
            )


def test_safety_contract_cannot_operate_a_broker() -> None:
    source = getsource(module)
    for forbidden in (
        "TradingClient",
        "AlpacaPaperBroker",
        "submit_approved_order",
        "reconcile_by_client_order_id",
        "BackgroundScheduler",
    ):
        assert forbidden not in source
