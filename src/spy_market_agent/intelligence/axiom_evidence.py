from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from spy_market_agent.benchmark.artifacts import sha256_json
from spy_market_agent.intelligence.axiom_session import IntelligenceSession
from spy_market_agent.intelligence.brief import SPYMarketIntelligenceBrief

INTELLIGENCE_EVIDENCE_SCHEMA_VERSION = "axiom-intelligence-evidence-v1"
INTELLIGENCE_EVIDENCE_ID_VERSION = "axiom-intelligence-evidence-id-v1"

_EVIDENCE_ID = re.compile(r"^aq-intel-evidence-[0-9a-f]{24}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class MarketIntelligenceEvidence(BaseModel):
    """Canonical Phase 3 bridge from a session to one exact intelligence brief."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["axiom-intelligence-evidence-v1"] = "axiom-intelligence-evidence-v1"
    evidence_id: str
    session: IntelligenceSession
    brief: SPYMarketIntelligenceBrief
    brief_digest: str
    execution_authority: Literal["none"] = "none"

    @field_validator("evidence_id")
    @classmethod
    def _canonical_evidence_id(cls, value: str) -> str:
        """Require the canonical Axiom intelligence-evidence identity shape."""

        if not _EVIDENCE_ID.fullmatch(value):
            raise ValueError("evidence_id must be a canonical Axiom intelligence identity")
        return value

    @field_validator("brief_digest")
    @classmethod
    def _canonical_brief_digest(cls, value: str) -> str:
        """Require the exact brief checksum to be a lowercase SHA-256 digest."""

        if not _SHA256.fullmatch(value):
            raise ValueError("brief_digest must be a lowercase SHA-256 value")
        return value

    @model_validator(mode="after")
    def _lineage_matches(self) -> MarketIntelligenceEvidence:
        """Reject tampered brief/session cross-links at the public model boundary."""

        if self.schema_version != INTELLIGENCE_EVIDENCE_SCHEMA_VERSION:
            raise ValueError("unsupported Intelligence OS evidence schema version")
        if self.brief.run_identity != self.session.intelligence_run:
            raise ValueError("intelligence brief run identity must match the Phase 3 session")
        if any(
            relationship.as_of != self.session.as_of for relationship in self.brief.relationships
        ):
            raise ValueError("intelligence relationships must use the session as_of cutoff")
        session_snapshots = set(self.session.snapshot_ids)
        for relationship in self.brief.relationships:
            relationship_snapshots = {
                relationship.target_snapshot_id,
                relationship.context_snapshot_id,
            } - {None}
            if not relationship_snapshots.issubset(session_snapshots):
                raise ValueError("intelligence relationship snapshots must belong to the session")
        if self.brief_digest != sha256_json(self.brief):
            raise ValueError("brief_digest must match the embedded intelligence brief")
        if self.evidence_id != intelligence_evidence_identity(self):
            raise ValueError("evidence_id must match canonical intelligence evidence content")
        return self


def intelligence_evidence_identity(evidence: MarketIntelligenceEvidence) -> str:
    """Return the content-addressed identity for one canonical intelligence snapshot."""

    payload = {
        "identity_version": INTELLIGENCE_EVIDENCE_ID_VERSION,
        "schema_version": evidence.schema_version,
        "session_id": evidence.session.session_id,
        "brief_digest": evidence.brief_digest,
        "execution_authority": evidence.execution_authority,
    }
    return f"aq-intel-evidence-{sha256_json(payload)[:24]}"


def build_market_intelligence_evidence(
    *,
    session: IntelligenceSession,
    brief: SPYMarketIntelligenceBrief,
) -> MarketIntelligenceEvidence:
    """Bind a validated Phase 3 session to its exact deterministic Market Intelligence brief."""

    canonical_session = IntelligenceSession.model_validate(session.model_dump(mode="python"))
    brief_digest = sha256_json(brief)
    payload: dict[str, object] = {
        "schema_version": INTELLIGENCE_EVIDENCE_SCHEMA_VERSION,
        "session": canonical_session,
        "brief": brief,
        "brief_digest": brief_digest,
        "execution_authority": "none",
    }
    identity_payload = {
        "identity_version": INTELLIGENCE_EVIDENCE_ID_VERSION,
        "schema_version": INTELLIGENCE_EVIDENCE_SCHEMA_VERSION,
        "session_id": canonical_session.session_id,
        "brief_digest": brief_digest,
        "execution_authority": "none",
    }
    evidence_id = f"aq-intel-evidence-{sha256_json(identity_payload)[:24]}"
    return MarketIntelligenceEvidence.model_validate({"evidence_id": evidence_id, **payload})
