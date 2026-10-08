"""Fail-closed Phase 7 evidence assessment; never approves operational trading."""

from __future__ import annotations

import re
from typing import Literal

import exchange_calendars
from pydantic import BaseModel, ConfigDict, model_validator

from spy_market_agent.benchmark.artifacts import sha256_json
from spy_market_agent.phase7_validation.memory import Phase7ValidationMemoryRegistry
from spy_market_agent.phase7_validation.pilot import Phase7PaperPilotObservation
from spy_market_agent.phase7_validation.safety import Phase7SafetyEvidence
from spy_market_agent.phase7_validation.session import PHASE7_REQUIRED_PAPER_SESSIONS

ASSESSMENT_SCHEMA_VERSION = "axiom-paper-validation-assessment-v1"
ASSESSMENT_ID_VERSION = "axiom-paper-validation-assessment-id-v1"
_ID = re.compile(r"^aq-paper-validation-assessment-[0-9a-f]{24}$")


class Phase7OperationalAssessment(BaseModel):
    """Deterministic review readiness; always no-go for automated execution."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["axiom-paper-validation-assessment-v1"] = (
        "axiom-paper-validation-assessment-v1"
    )
    validation_assessment_id: str
    experiment_id: str
    safety_evidence_ids: tuple[str, ...]
    pilot_observation_ids: tuple[str, ...]
    pilot_session_count: int
    market_session_count: int
    abstention_count: int
    execution_count: int
    accepted_order_count: int
    incident_count: int
    unresolved_unknown_count: int
    all_critical_safety_passed: bool
    ready_for_owner_audit: bool
    evidence_provenance: Literal["operator_attested_not_independently_verified"] = (
        "operator_attested_not_independently_verified"
    )
    model_connected_execution: Literal["blocked_no_approved_paper_model"] = (
        "blocked_no_approved_paper_model"
    )
    trading_authority: Literal["none"] = "none"
    operational_verdict: Literal["no_go"] = "no_go"

    @model_validator(mode="after")
    def _invariants(self) -> Phase7OperationalAssessment:
        if not _ID.fullmatch(self.validation_assessment_id):
            raise ValueError("assessment id must be canonical")
        if self.safety_evidence_ids != tuple(sorted(set(self.safety_evidence_ids))):
            raise ValueError("safety IDs must be unique and sorted")
        if self.pilot_observation_ids != tuple(sorted(set(self.pilot_observation_ids))):
            raise ValueError("pilot IDs must be unique and sorted")
        values = (
            self.pilot_session_count,
            self.market_session_count,
            self.abstention_count,
            self.execution_count,
            self.accepted_order_count,
            self.incident_count,
            self.unresolved_unknown_count,
        )
        if any(value < 0 for value in values):
            raise ValueError("assessment counts must be nonnegative")
        if self.pilot_session_count != len(self.pilot_observation_ids):
            raise ValueError("pilot count must match all pilot IDs")
        if self.pilot_session_count != self.abstention_count + self.execution_count:
            raise ValueError("all pilot observations must be classified")
        if (
            self.market_session_count > self.pilot_session_count
            or self.accepted_order_count > self.execution_count
            or self.unresolved_unknown_count > self.execution_count
        ):
            raise ValueError("invalid Phase 7 operational counts")
        expected_ready = (
            self.pilot_session_count >= PHASE7_REQUIRED_PAPER_SESSIONS
            and self.market_session_count == self.pilot_session_count
            and self.accepted_order_count > 0
            and self.incident_count == 0
            and self.unresolved_unknown_count == 0
            and self.all_critical_safety_passed
        )
        if self.ready_for_owner_audit != expected_ready:
            raise ValueError("owner-audit readiness must match exact evidence gates")
        if self.validation_assessment_id != phase7_operational_assessment_identity(self):
            raise ValueError("assessment id must match canonical content")
        return self


def phase7_operational_assessment_identity(assessment: Phase7OperationalAssessment) -> str:
    payload = assessment.model_dump(mode="json", exclude={"validation_assessment_id"})
    payload["identity_version"] = ASSESSMENT_ID_VERSION
    return f"aq-paper-validation-assessment-{sha256_json(payload)[:24]}"


def _is_xnys_market_session(day: str) -> bool:
    """Require an actual NYSE session; weekends and holidays do not count."""

    try:
        return bool(exchange_calendars.get_calendar("XNYS").is_session(day))
    except (ValueError, TypeError):
        return False


def build_phase7_operational_assessment(
    *,
    experiment_id: str,
    registry: Phase7ValidationMemoryRegistry,
) -> Phase7OperationalAssessment:
    """Assess *all* stored evidence, never a cherry-picked caller-supplied subset."""

    safeties = tuple(
        registry.load_safety(experiment_id, value)
        for value in registry.list_safety_ids(experiment_id)
    )
    pilots = registry.list_pilots(experiment_id)
    return _evaluate_records(experiment_id=experiment_id, safeties=safeties, pilots=pilots)


def replay_phase7_operational_assessment(
    *,
    snapshot: Phase7OperationalAssessment,
    registry: Phase7ValidationMemoryRegistry,
) -> Phase7OperationalAssessment:
    """Verify an older immutable snapshot even when newer evidence is appended."""

    safeties = tuple(
        registry.load_safety(snapshot.experiment_id, value)
        for value in snapshot.safety_evidence_ids
    )
    pilots = tuple(
        registry.load_pilot(snapshot.experiment_id, value)
        for value in snapshot.pilot_observation_ids
    )
    return _evaluate_records(
        experiment_id=snapshot.experiment_id,
        safeties=safeties,
        pilots=pilots,
    )


def _evaluate_records(
    *,
    experiment_id: str,
    safeties: tuple[Phase7SafetyEvidence, ...],
    pilots: tuple[Phase7PaperPilotObservation, ...],
) -> Phase7OperationalAssessment:
    """Compute every metric from fully reloaded, typed evidence."""

    executed = tuple(item for item in pilots if item.state == "execution_recorded")
    pilot_count = len(pilots)
    market_count = sum(
        _is_xnys_market_session(item.session.observation_date.isoformat()) for item in pilots
    )
    abstention_count = sum(item.state == "abstained" for item in pilots)
    accepted_count = sum(item.accepted_order for item in executed)
    incident_count = sum(len(item.incident_codes) for item in pilots)
    unknown_count = sum(item.unresolved_unknown for item in executed)
    safety_passed = bool(safeties) and all(item.passed for item in safeties)
    ready = (
        pilot_count >= PHASE7_REQUIRED_PAPER_SESSIONS
        and market_count == pilot_count
        and accepted_count > 0
        and incident_count == 0
        and unknown_count == 0
        and safety_passed
    )
    payload: dict[str, object] = {
        "schema_version": ASSESSMENT_SCHEMA_VERSION,
        "experiment_id": experiment_id,
        "safety_evidence_ids": tuple(sorted(item.safety_evidence_id for item in safeties)),
        "pilot_observation_ids": tuple(sorted(item.pilot_observation_id for item in pilots)),
        "pilot_session_count": pilot_count,
        "market_session_count": market_count,
        "abstention_count": abstention_count,
        "execution_count": len(executed),
        "accepted_order_count": accepted_count,
        "incident_count": incident_count,
        "unresolved_unknown_count": unknown_count,
        "all_critical_safety_passed": safety_passed,
        "ready_for_owner_audit": ready,
        "evidence_provenance": "operator_attested_not_independently_verified",
        "model_connected_execution": "blocked_no_approved_paper_model",
        "trading_authority": "none",
        "operational_verdict": "no_go",
    }
    digest = sha256_json({**payload, "identity_version": ASSESSMENT_ID_VERSION})[:24]
    return Phase7OperationalAssessment.model_validate(
        {"validation_assessment_id": f"aq-paper-validation-assessment-{digest}", **payload}
    )
