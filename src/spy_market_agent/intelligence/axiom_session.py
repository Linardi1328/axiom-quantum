from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from spy_market_agent.benchmark.artifacts import sha256_json
from spy_market_agent.intelligence.contracts import (
    IntelligenceRunIdentity,
    derive_intelligence_run_identity,
)
from spy_market_agent.research.validation_engine import ValidationDecision, ValidationVerdict
from spy_market_agent.research.validation_memory import validation_decision_identity

INTELLIGENCE_SESSION_SCHEMA_VERSION = "axiom-intelligence-session-v1"
INTELLIGENCE_SESSION_ID_VERSION = "axiom-intelligence-session-id-v1"

_SESSION_ID = re.compile(r"^aq-intel-session-[0-9a-f]{24}$")
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class IntelligenceSession(BaseModel):
    """Canonical Phase 3 binding between validation and point-in-time intelligence."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["axiom-intelligence-session-v1"] = "axiom-intelligence-session-v1"
    session_id: str
    invocation_id: str
    invocation_source: Literal["human_requested"] = "human_requested"
    decision_id: str
    decision: ValidationDecision
    validation_id: str
    experiment_id: str
    result_id: str
    validation_policy_id: str
    validation_policy_digest: str
    intelligence_run_id: str
    intelligence_run: IntelligenceRunIdentity
    target_instrument_id: str
    as_of: datetime
    analysis_profile_id: str
    snapshot_ids: tuple[str, ...]
    code_revision: str
    intelligence_configuration_hash: str
    execution_authority: Literal["none"] = "none"

    @field_validator(
        "invocation_id",
        "decision_id",
        "validation_id",
        "experiment_id",
        "result_id",
        "validation_policy_id",
        "intelligence_run_id",
        "target_instrument_id",
        "analysis_profile_id",
        "code_revision",
    )
    @classmethod
    def _safe_identifier(cls, value: str) -> str:
        """Require path-safe identifiers throughout the session lineage."""

        if not _SAFE_IDENTIFIER.fullmatch(value):
            raise ValueError("Phase 3 identifiers must be nonempty and path-safe")
        return value

    @field_validator("validation_policy_digest", "intelligence_configuration_hash")
    @classmethod
    def _checksum(cls, value: str) -> str:
        """Require canonical lowercase SHA-256 lineage digests."""

        if not _SHA256.fullmatch(value):
            raise ValueError("Phase 3 digests must be lowercase SHA-256 values")
        return value

    @field_validator("as_of")
    @classmethod
    def _aware_as_of(cls, value: datetime) -> datetime:
        """Normalize the point-in-time analysis cutoff to UTC."""

        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        return value.astimezone(UTC)

    @field_validator("snapshot_ids")
    @classmethod
    def _canonical_snapshots(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Require a nonempty unique canonical snapshot identity set."""

        if not value:
            raise ValueError("Phase 3 session requires at least one intelligence snapshot")
        if any(not _SAFE_IDENTIFIER.fullmatch(item) for item in value):
            raise ValueError("snapshot_ids must contain path-safe identifiers")
        if len(value) != len(set(value)):
            raise ValueError("snapshot_ids must not contain duplicates")
        return tuple(sorted(value))

    @field_validator("session_id")
    @classmethod
    def _canonical_session_id(cls, value: str) -> str:
        """Require the canonical Axiom Intelligence OS identity shape."""

        if not _SESSION_ID.fullmatch(value):
            raise ValueError("session_id must be a canonical Axiom Intelligence OS identity")
        return value

    @model_validator(mode="after")
    def _identity_matches(self) -> IntelligenceSession:
        """Enforce admission, exact lineage, and content identity at the public boundary."""

        if self.schema_version != INTELLIGENCE_SESSION_SCHEMA_VERSION:
            raise ValueError("unsupported Intelligence OS session schema version")
        canonical_decision = ValidationDecision.model_validate(
            self.decision.model_dump(mode="python")
        )
        if canonical_decision.verdict != ValidationVerdict.VALIDATED_RESEARCH_CANDIDATE:
            raise ValueError("Phase 3 requires a validated_research_candidate decision")
        if self.decision_id != validation_decision_identity(canonical_decision):
            raise ValueError("Phase 3 decision_id must match the embedded validation decision")
        if (
            self.validation_id != canonical_decision.validation_id
            or self.experiment_id != canonical_decision.experiment_id
            or self.result_id != canonical_decision.result_id
            or self.validation_policy_id != canonical_decision.policy_id
            or self.validation_policy_digest != canonical_decision.policy_digest
        ):
            raise ValueError("Phase 3 validation lineage must match the embedded decision")
        canonical_run = derive_intelligence_run_identity(
            target_instrument_id=self.intelligence_run.target_instrument_id,
            as_of=self.intelligence_run.as_of,
            analysis_profile_id=self.intelligence_run.analysis_profile_id,
            snapshot_ids=self.intelligence_run.snapshot_ids,
            code_revision=self.intelligence_run.code_revision,
            configuration_hash=self.intelligence_run.configuration_hash,
        )
        if self.intelligence_run != canonical_run:
            raise ValueError("Phase 3 intelligence run identity must match its canonical lineage")
        if (
            self.intelligence_run_id != canonical_run.run_id
            or self.target_instrument_id != canonical_run.target_instrument_id
            or self.as_of != canonical_run.as_of
            or self.analysis_profile_id != canonical_run.analysis_profile_id
            or self.snapshot_ids != canonical_run.snapshot_ids
            or self.code_revision != canonical_run.code_revision
            or self.intelligence_configuration_hash != canonical_run.configuration_hash
        ):
            raise ValueError("Phase 3 intelligence lineage must match the embedded run")
        if self.session_id != intelligence_session_identity(self):
            raise ValueError("session_id must match canonical Intelligence OS session content")
        return self


def intelligence_session_identity(session: IntelligenceSession) -> str:
    """Return the content-addressed identity for an Intelligence OS session."""

    payload = session.model_dump(mode="json", exclude={"session_id"})
    payload["identity_version"] = INTELLIGENCE_SESSION_ID_VERSION
    return f"aq-intel-session-{sha256_json(payload)[:24]}"


def build_intelligence_session(
    *,
    decision: ValidationDecision,
    intelligence_run: IntelligenceRunIdentity,
    invocation_id: str,
) -> IntelligenceSession:
    """Bind one validated research candidate to one explicit human intelligence run."""

    canonical_decision = ValidationDecision.model_validate(decision.model_dump(mode="python"))
    if canonical_decision.verdict != ValidationVerdict.VALIDATED_RESEARCH_CANDIDATE:
        raise ValueError("Phase 3 requires a validated_research_candidate decision")
    canonical_intelligence_run = derive_intelligence_run_identity(
        target_instrument_id=intelligence_run.target_instrument_id,
        as_of=intelligence_run.as_of,
        analysis_profile_id=intelligence_run.analysis_profile_id,
        snapshot_ids=intelligence_run.snapshot_ids,
        code_revision=intelligence_run.code_revision,
        configuration_hash=intelligence_run.configuration_hash,
    )
    if intelligence_run != canonical_intelligence_run:
        raise ValueError("Phase 3 intelligence run identity must match its canonical lineage")

    payload: dict[str, object] = {
        "schema_version": INTELLIGENCE_SESSION_SCHEMA_VERSION,
        "invocation_id": invocation_id,
        "invocation_source": "human_requested",
        "decision_id": validation_decision_identity(canonical_decision),
        "decision": canonical_decision,
        "validation_id": canonical_decision.validation_id,
        "experiment_id": canonical_decision.experiment_id,
        "result_id": canonical_decision.result_id,
        "validation_policy_id": canonical_decision.policy_id,
        "validation_policy_digest": canonical_decision.policy_digest,
        "intelligence_run_id": canonical_intelligence_run.run_id,
        "intelligence_run": canonical_intelligence_run,
        "target_instrument_id": canonical_intelligence_run.target_instrument_id,
        "as_of": canonical_intelligence_run.as_of,
        "analysis_profile_id": canonical_intelligence_run.analysis_profile_id,
        "snapshot_ids": canonical_intelligence_run.snapshot_ids,
        "code_revision": canonical_intelligence_run.code_revision,
        "intelligence_configuration_hash": canonical_intelligence_run.configuration_hash,
        "execution_authority": "none",
    }
    identity_payload = payload | {"identity_version": INTELLIGENCE_SESSION_ID_VERSION}
    session_id = f"aq-intel-session-{sha256_json(identity_payload)[:24]}"
    return IntelligenceSession.model_validate({"session_id": session_id, **payload})
