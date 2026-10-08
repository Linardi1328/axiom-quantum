from __future__ import annotations

from datetime import date
from inspect import getsource
from pathlib import Path

import pytest

import spy_market_agent.phase7_validation.session as module
from spy_market_agent.phase7_validation import (
    PHASE7_REQUIRED_PAPER_SESSIONS,
    Phase7ValidationSession,
    build_phase7_validation_session,
)
from unit.test_axiom_phase6_paper_execution_session import _stored_assessment


def test_phase7_session_is_explicit_deterministic_and_nonexecuting(tmp_path: Path) -> None:
    _, registry, assessment = _stored_assessment(tmp_path)
    kwargs = {
        "assessment": assessment,
        "invocation_id": "phase7-20261008-operator",
        "observation_date": date(2026, 10, 8),
        "mode": "synthetic",
        "registry": registry,
    }
    first = build_phase7_validation_session(**kwargs)  # type: ignore[arg-type]
    second = build_phase7_validation_session(**kwargs)  # type: ignore[arg-type]
    assert first == second
    assert first.assessment_id == assessment.assessment_id
    assert first.mode == "synthetic"
    assert first.invocation_source == "human_requested"
    assert first.execution_authority == "none"
    assert first.model_connected_execution == "blocked_no_approved_paper_model"
    assert PHASE7_REQUIRED_PAPER_SESSIONS == 20


def test_phase7_session_id_changes_with_mode_date_and_invocation(tmp_path: Path) -> None:
    _, registry, assessment = _stored_assessment(tmp_path)

    def build(mode: str, invocation: str, day: date) -> Phase7ValidationSession:
        return build_phase7_validation_session(
            assessment=assessment,
            invocation_id=invocation,
            observation_date=day,
            mode=mode,  # type: ignore[arg-type]
            registry=registry,
        )

    ids = {
        build("synthetic", "a", date(2026, 10, 8)).validation_session_id,
        build("paper_broker", "a", date(2026, 10, 8)).validation_session_id,
        build("synthetic", "b", date(2026, 10, 8)).validation_session_id,
        build("synthetic", "a", date(2026, 10, 9)).validation_session_id,
    }
    assert len(ids) == 4


def test_phase7_session_rejects_bad_invocation_and_modified_identity(tmp_path: Path) -> None:
    _, registry, assessment = _stored_assessment(tmp_path)
    for invocation in ("../escape", "", "bad space", ".."):
        with pytest.raises(ValueError, match="safe opaque identifier"):
            build_phase7_validation_session(
                assessment=assessment,
                invocation_id=invocation,
                observation_date=date(2026, 10, 8),
                mode="synthetic",
                registry=registry,
            )
    session = build_phase7_validation_session(
        assessment=assessment,
        invocation_id="valid",
        observation_date=date(2026, 10, 8),
        mode="synthetic",
        registry=registry,
    )
    with pytest.raises(ValueError, match="canonical content"):
        Phase7ValidationSession.model_validate(
            session.model_dump(mode="python") | {"mode": "paper_broker"}
        )


def test_phase7_session_requires_intact_stored_readiness(tmp_path: Path) -> None:
    store, registry, assessment = _stored_assessment(tmp_path)
    store.write_json(
        assessment.experiment_id,
        registry._assessment_name(assessment.assessment_id),
        {"invalid": True},
        allow_replace=True,
    )
    with pytest.raises(Exception, match="canonical validation"):
        build_phase7_validation_session(
            assessment=assessment,
            invocation_id="trial",
            observation_date=date(2026, 10, 8),
            mode="synthetic",
            registry=registry,
        )


def test_phase7_session_never_imports_broker_or_submits() -> None:
    source = getsource(module)
    for forbidden in (
        "TradingClient",
        "submit_approved_order",
        "AlpacaPaperBroker",
        "submit_market_day_order",
        "BackgroundScheduler",
        "live_trading",
    ):
        assert forbidden not in source
