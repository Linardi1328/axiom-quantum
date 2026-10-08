"""Human-attested pilot observations; no broker calls or trading authority."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from spy_market_agent.benchmark.artifacts import sha256_json
from spy_market_agent.phase6_execution.bridge import PaperExecutionOutcome
from spy_market_agent.phase6_execution.memory import PaperExecutionMemoryRegistry
from spy_market_agent.phase6_execution.reporting import (
    PaperExecutionReportArtifact,
    load_paper_execution_report,
)
from spy_market_agent.phase7_validation.session import Phase7ValidationSession

PILOT_SCHEMA_VERSION = "axiom-paper-validation-pilot-v1"
PILOT_ID_VERSION = "axiom-paper-validation-pilot-id-v1"

_ID = re.compile(r"^aq-paper-validation-pilot-[0-9a-f]{24}$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

PilotState = Literal["abstained", "execution_recorded"]
AbstainReason = Literal["no_qualifying_setup", "market_closed", "safety_blocked"]
IncidentCode = Literal[
    "unauthorized_submission",
    "duplicate_submission",
    "risk_violation",
    "audit_gap",
]


class Phase7PaperPilotObservation(BaseModel):
    """Audit-bound operator attestation, never independently verified broker evidence."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["axiom-paper-validation-pilot-v1"] = "axiom-paper-validation-pilot-v1"
    pilot_observation_id: str
    session: Phase7ValidationSession
    validation_session_id: str
    experiment_id: str
    state: PilotState
    abstain_reason: AbstainReason | None
    outcome: PaperExecutionOutcome | None
    report: PaperExecutionReportArtifact | None
    broker_artifact_checksum: str | None
    incident_codes: tuple[IncidentCode, ...] = ()
    attestation_ref: str
    human_reviewed: Literal[True]
    evidence_source: Literal["operator_attested_paper_broker"] = "operator_attested_paper_broker"
    broker_verification: Literal["not_independently_verified"] = "not_independently_verified"
    execution_authority: Literal["none"] = "none"

    @field_validator("attestation_ref")
    @classmethod
    def _opaque_ref(cls, value: str) -> str:
        if not _SAFE.fullmatch(value) or value in {".", ".."}:
            raise ValueError("attestation_ref must be a safe opaque reference")
        return value

    @field_validator("broker_artifact_checksum")
    @classmethod
    def _checksum(cls, value: str | None) -> str | None:
        if value is not None and not _SHA256.fullmatch(value):
            raise ValueError("broker_artifact_checksum must be canonical SHA-256")
        return value

    @model_validator(mode="after")
    def _links(self) -> Phase7PaperPilotObservation:
        if not _ID.fullmatch(self.pilot_observation_id):
            raise ValueError("pilot_observation_id must be canonical")
        session = Phase7ValidationSession.model_validate(self.session.model_dump(mode="python"))
        if session != self.session or session.mode != "paper_broker":
            raise ValueError("pilot observation must use an exact paper_broker session")
        if self.validation_session_id != session.validation_session_id:
            raise ValueError("pilot observation session identity mismatch")
        if self.experiment_id != session.experiment_id:
            raise ValueError("pilot observation experiment identity mismatch")
        if len(set(self.incident_codes)) != len(self.incident_codes):
            raise ValueError("pilot observation incidents must be unique")
        if self.state == "abstained":
            if (
                self.abstain_reason is None
                or self.outcome is not None
                or self.report is not None
                or self.broker_artifact_checksum is not None
            ):
                raise ValueError("abstention must have a reason and no broker-order evidence")
        else:
            if (
                self.abstain_reason is not None
                or self.outcome is None
                or self.report is None
                or self.broker_artifact_checksum is None
            ):
                raise ValueError("execution observation requires outcome, report and checksum")
            if self.outcome.experiment_id != session.experiment_id:
                raise ValueError("execution outcome experiment must match session")
            if self.outcome.authorization.session.assessment != session.assessment:
                raise ValueError("execution outcome must match exact Phase 5 parent")
            if (
                self.report.final_outcome_id != self.outcome.paper_execution_outcome_id
                or self.report.final_disposition != self.outcome.disposition
                or self.report.experiment_id != session.experiment_id
                or self.report.paper_submission_authorization_id
                != self.outcome.paper_submission_authorization_id
            ):
                raise ValueError("execution report must match the exact final outcome")
        if self.pilot_observation_id != phase7_pilot_observation_identity(self):
            raise ValueError("pilot_observation_id must match canonical content")
        return self

    @property
    def unresolved_unknown(self) -> bool:
        return self.outcome is not None and self.outcome.disposition == "submission_unknown"

    @property
    def accepted_order(self) -> bool:
        return self.outcome is not None and self.outcome.disposition in {"accepted", "reconciled"}


def phase7_pilot_observation_identity(observation: Phase7PaperPilotObservation) -> str:
    payload = observation.model_dump(mode="json", exclude={"pilot_observation_id"})
    payload["identity_version"] = PILOT_ID_VERSION
    return f"aq-paper-validation-pilot-{sha256_json(payload)[:24]}"


def build_phase7_pilot_observation(
    *,
    session: Phase7ValidationSession,
    state: PilotState,
    attestation_ref: str,
    human_reviewed: bool,
    registry: PaperExecutionMemoryRegistry,
    abstain_reason: AbstainReason | None = None,
    outcome: PaperExecutionOutcome | None = None,
    report: PaperExecutionReportArtifact | None = None,
    broker_artifact_checksum: str | None = None,
    incident_codes: tuple[IncidentCode, ...] = (),
) -> Phase7PaperPilotObservation:
    """Verify exact persisted Phase 5/6 evidence and create no broker interactions."""

    canonical = Phase7ValidationSession.model_validate(session.model_dump(mode="python"))
    stored_parent = registry.readiness_memory.load_assessment(
        canonical.experiment_id, canonical.assessment_id
    )
    if stored_parent != canonical.assessment:
        raise ValueError("pilot session parent must match exact stored Phase 5 readiness")
    if outcome is not None:
        persisted = registry.load_outcome(outcome.experiment_id, outcome.paper_execution_outcome_id)
        if persisted != outcome:
            raise ValueError("pilot outcome must match exact stored Phase 6 outcome")
    if report is not None:
        load_paper_execution_report(report, registry=registry)
    payload: dict[str, object] = {
        "schema_version": PILOT_SCHEMA_VERSION,
        "session": canonical,
        "validation_session_id": canonical.validation_session_id,
        "experiment_id": canonical.experiment_id,
        "state": state,
        "abstain_reason": abstain_reason,
        "outcome": outcome,
        "report": report,
        "broker_artifact_checksum": broker_artifact_checksum,
        "incident_codes": incident_codes,
        "attestation_ref": attestation_ref,
        "human_reviewed": human_reviewed,
        "evidence_source": "operator_attested_paper_broker",
        "broker_verification": "not_independently_verified",
        "execution_authority": "none",
    }
    digest = sha256_json({**payload, "identity_version": PILOT_ID_VERSION})[:24]
    return Phase7PaperPilotObservation.model_validate(
        {"pilot_observation_id": f"aq-paper-validation-pilot-{digest}", **payload}
    )
