"""Deterministic synthetic safety-probe evidence; never broker authority."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from spy_market_agent.benchmark.artifacts import sha256_json
from spy_market_agent.paper_ops.memory import PaperReadinessMemoryRegistry
from spy_market_agent.phase7_validation.session import (
    PHASE7_CRITICAL_PROBES,
    Phase7ValidationSession,
)

PHASE7_SAFETY_SCHEMA_VERSION = "axiom-paper-validation-safety-v1"
PHASE7_SAFETY_ID_VERSION = "axiom-paper-validation-safety-id-v1"

SafetyProbeName = Literal[
    "duplicate_submission",
    "kill_switch",
    "unauthorized_submission",
    "submission_unknown",
    "lookup_only_reconciliation",
    "tampered_audit",
]

_ID = re.compile(r"^aq-paper-validation-safety-[0-9a-f]{24}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CODE = re.compile(r"^[a-z][a-z0-9_]{0,79}$")


class Phase7SafetyProbe(BaseModel):
    """One sanitized deterministic fixture expectation and observed outcome."""

    model_config = ConfigDict(frozen=True)

    probe: SafetyProbeName
    fixture_checksum: str
    expected_code: str
    observed_code: str
    evidence_source: Literal["synthetic_fixture"] = "synthetic_fixture"

    @field_validator("fixture_checksum")
    @classmethod
    def _digest(cls, value: str) -> str:
        """Require a canonical digest for reproducible fixture evidence."""

        if not _SHA256.fullmatch(value):
            raise ValueError("fixture_checksum must be canonical SHA-256")
        return value

    @field_validator("expected_code", "observed_code")
    @classmethod
    def _sanitized_code(cls, value: str) -> str:
        """Keep only non-identifying machine error/outcome codes."""

        if not _CODE.fullmatch(value):
            raise ValueError("probe codes must be sanitized opaque values")
        return value

    @property
    def passed(self) -> bool:
        """Pass only on exact expected-versus-observed equality."""

        return self.expected_code == self.observed_code


class Phase7SafetyEvidence(BaseModel):
    """Immutable synthetic-only suite bound to one stored human validation session."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["axiom-paper-validation-safety-v1"] = (
        "axiom-paper-validation-safety-v1"
    )
    safety_evidence_id: str
    session: Phase7ValidationSession
    validation_session_id: str
    experiment_id: str
    probes: tuple[Phase7SafetyProbe, ...]
    evidence_source: Literal["synthetic_fixture"] = "synthetic_fixture"
    execution_authority: Literal["none"] = "none"

    @model_validator(mode="after")
    def _exact_identity(self) -> Phase7SafetyEvidence:
        """Reject non-synthetic sources, duplicate probes and altered lineage."""

        if not _ID.fullmatch(self.safety_evidence_id):
            raise ValueError("safety_evidence_id must be canonical")
        session = Phase7ValidationSession.model_validate(self.session.model_dump(mode="python"))
        if session != self.session or session.mode != "synthetic":
            raise ValueError("safety evidence requires an exact synthetic validation session")
        if self.validation_session_id != session.validation_session_id:
            raise ValueError("validation_session_id must match exact session")
        if self.experiment_id != session.experiment_id:
            raise ValueError("experiment_id must match exact session")
        if tuple(item.probe for item in self.probes) != PHASE7_CRITICAL_PROBES:
            raise ValueError("safety evidence requires all six ordered critical probes")
        if len({item.fixture_checksum for item in self.probes}) != len(self.probes):
            raise ValueError("safety evidence fixture checksums must be distinct")
        if self.safety_evidence_id != phase7_safety_evidence_identity(self):
            raise ValueError("safety_evidence_id must match canonical content")
        return self

    @property
    def passed(self) -> bool:
        """A failed test remains a failed test; no caller override exists."""

        return all(item.passed for item in self.probes)


def phase7_safety_evidence_identity(evidence: Phase7SafetyEvidence) -> str:
    """Derive one canonical identity from exact synthetic fixture evidence."""

    payload = evidence.model_dump(mode="json", exclude={"safety_evidence_id"})
    payload["identity_version"] = PHASE7_SAFETY_ID_VERSION
    return f"aq-paper-validation-safety-{sha256_json(payload)[:24]}"


def build_phase7_safety_evidence(
    *,
    session: Phase7ValidationSession,
    probes: tuple[Phase7SafetyProbe, ...],
    registry: PaperReadinessMemoryRegistry,
) -> Phase7SafetyEvidence:
    """Reverify Phase 5 parent and build synthetic evidence without broker access."""

    canonical = Phase7ValidationSession.model_validate(session.model_dump(mode="python"))
    if registry.load_assessment(canonical.experiment_id, canonical.assessment_id) != (
        canonical.assessment
    ):
        raise ValueError("synthetic safety session must preserve exact stored readiness")
    payload: dict[str, object] = {
        "schema_version": PHASE7_SAFETY_SCHEMA_VERSION,
        "session": canonical,
        "validation_session_id": canonical.validation_session_id,
        "experiment_id": canonical.experiment_id,
        "probes": probes,
        "evidence_source": "synthetic_fixture",
        "execution_authority": "none",
    }
    identity = sha256_json({**payload, "identity_version": PHASE7_SAFETY_ID_VERSION})[:24]
    return Phase7SafetyEvidence.model_validate(
        {"safety_evidence_id": f"aq-paper-validation-safety-{identity}", **payload}
    )
