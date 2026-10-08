"""Deterministic Phase 7 audit report; never creates trading permission."""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from spy_market_agent.benchmark.artifacts import sha256_bytes
from spy_market_agent.phase7_validation.assessment import (
    Phase7OperationalAssessment,
    build_phase7_operational_assessment,
    replay_phase7_operational_assessment,
)
from spy_market_agent.phase7_validation.memory import Phase7ValidationMemoryRegistry
from spy_market_agent.phase7_validation.pilot import Phase7PaperPilotObservation

REPORT_SCHEMA_VERSION = "axiom-paper-validation-report-v1"
_REPORT_ID = re.compile(r"^aq-paper-validation-report-[0-9a-f]{24}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class Phase7ValidationReportArtifact(BaseModel):
    """Checksum-bound reference to a reproducible non-authorizing report."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["axiom-paper-validation-report-v1"] = "axiom-paper-validation-report-v1"
    report_id: str
    assessment: Phase7OperationalAssessment
    validation_assessment_id: str
    experiment_id: str
    relative_path: str
    checksum: str
    operational_verdict: Literal["no_go"] = "no_go"

    @field_validator("relative_path")
    @classmethod
    def _safe_path(cls, value: str) -> str:
        if not value.strip() or "\\" in value:
            raise ValueError("report relative path must be safe POSIX")
        path = PurePosixPath(value)
        if path.is_absolute() or any(part in {".", ".."} for part in path.parts):
            raise ValueError("report relative path must be safe POSIX")
        return path.as_posix()

    @model_validator(mode="after")
    def _identity(self) -> Phase7ValidationReportArtifact:
        if not _REPORT_ID.fullmatch(self.report_id) or not _SHA256.fullmatch(self.checksum):
            raise ValueError("report id and checksum must be canonical")
        if self.report_id != f"aq-paper-validation-report-{self.checksum[:24]}":
            raise ValueError("report identity must match checksum")
        if (
            self.validation_assessment_id != self.assessment.validation_assessment_id
            or self.experiment_id != self.assessment.experiment_id
        ):
            raise ValueError("report must link exact assessment")
        return self


def phase7_assessment_name(assessment: Phase7OperationalAssessment) -> str:
    return f"axiom_phase7_validation_assessment_{assessment.validation_assessment_id}.json"


def phase7_report_name(assessment: Phase7OperationalAssessment) -> str:
    return f"axiom_phase7_validation_report_{assessment.validation_assessment_id}.md"


def render_phase7_validation_report(assessment: Phase7OperationalAssessment) -> str:
    """Deterministic Markdown; synthetic tests do not constitute real paper evidence."""

    a = Phase7OperationalAssessment.model_validate(assessment.model_dump(mode="python"))
    lines = [
        "# Axiom Quantum Phase 7 Supervised Paper-Trading Validation",
        "",
        f"- Report schema: {REPORT_SCHEMA_VERSION}",
        f"- Experiment ID: {a.experiment_id}",
        f"- Assessment ID: {a.validation_assessment_id}",
        f"- Total operator-attested pilot sessions: {a.pilot_session_count}",
        f"- NYSE market sessions: {a.market_session_count}",
        f"- Abstentions: {a.abstention_count}",
        f"- Recorded executions: {a.execution_count}",
        f"- Accepted/reconciled outcomes: {a.accepted_order_count}",
        f"- Safety fixture suites: {len(a.safety_evidence_ids)}",
        (
            "- All registered critical safety fixtures passed: "
            f"{str(a.all_critical_safety_passed).lower()}"
        ),
        f"- Operational incidents: {a.incident_count}",
        f"- Unresolved submission unknowns: {a.unresolved_unknown_count}",
        "- Initial observation threshold: 20 market sessions",
        f"- Ready for independent owner audit: {str(a.ready_for_owner_audit).lower()}",
        f"- Broker source: {a.evidence_provenance}",
        f"- Model-connected paper operation: {a.model_connected_execution}",
        "",
        "## Evidence identities (fully enumerated)",
        "",
        "### Synthetic critical safety records",
        "",
    ]
    lines.extend(f"- {value}" for value in a.safety_evidence_ids)
    lines.extend(["", "### Paper pilot records (operator-attested)", ""])
    lines.extend(f"- {value}" for value in a.pilot_observation_ids)
    lines.extend(
        [
            "",
            "## Unchanged safety and review boundaries",
            "",
            (
                "- **Operational verdict: NO_GO.** This report never approves "
                "automated or live trading."
            ),
            "- An operator-attested receipt digest is not independent proof of real broker origin.",
            "- A real paper-broker audit and explicit owner signoff are still required.",
            "- All synthetic CI fixtures are ineligible to prove a real paper pilot.",
            "- No model has been approved for model-connected execution.",
            "- No unattended sessions, schedulers, order retries, or trade notifications.",
            "- No forced trades to reach an observation or sample count.",
            "",
        ]
    )
    return "\n".join(lines)


def write_phase7_validation_report(
    *,
    experiment_id: str,
    registry: Phase7ValidationMemoryRegistry,
) -> Phase7ValidationReportArtifact:
    """Snapshot all stored evidence and write immutable checksum-verified Markdown."""

    assessment = build_phase7_operational_assessment(experiment_id=experiment_id, registry=registry)
    registry.store.write_json(
        experiment_id, phase7_assessment_name(assessment), assessment, allow_replace=False
    )
    if (
        Phase7OperationalAssessment.model_validate(
            registry.store.read_json(experiment_id, phase7_assessment_name(assessment))
        )
        != assessment
    ):
        raise ValueError("stored Phase 7 assessment differs after canonical reload")
    data = render_phase7_validation_report(assessment).encode("utf-8")
    checksum = sha256_bytes(data)
    filename = phase7_report_name(assessment)
    registry.store.write_bytes(
        experiment_id, filename, data, expected_checksum=checksum, allow_replace=False
    )
    artifact = Phase7ValidationReportArtifact(
        report_id=f"aq-paper-validation-report-{checksum[:24]}",
        assessment=assessment,
        validation_assessment_id=assessment.validation_assessment_id,
        experiment_id=experiment_id,
        relative_path=registry.store.relative_path(
            registry.store.artifact_path(experiment_id, filename)
        ),
        checksum=checksum,
    )
    if load_phase7_validation_report(artifact, registry=registry) != data.decode("utf-8"):
        raise ValueError("stored Phase 7 report differs after canonical reload")
    return artifact


def load_phase7_validation_report(
    artifact: Phase7ValidationReportArtifact,
    *,
    registry: Phase7ValidationMemoryRegistry,
) -> str:
    """Recompute *all* stored evidence, rejecting tampering and cherry-picking."""

    canonical = Phase7ValidationReportArtifact.model_validate(artifact.model_dump(mode="python"))
    recomputed = replay_phase7_operational_assessment(
        snapshot=canonical.assessment, registry=registry
    )
    if recomputed != canonical.assessment:
        raise ValueError("report assessment no longer matches its stored evidence snapshot")
    stored = Phase7OperationalAssessment.model_validate(
        registry.store.read_json(
            canonical.experiment_id, phase7_assessment_name(canonical.assessment)
        )
    )
    if stored != recomputed:
        raise ValueError("persisted assessment does not match exact evidence")
    filename = phase7_report_name(recomputed)
    path = registry.store.artifact_path(canonical.experiment_id, filename)
    if registry.store.relative_path(path) != canonical.relative_path:
        raise ValueError("report relative path does not match canonical location")
    if registry.store.checksum(canonical.experiment_id, filename) != canonical.checksum:
        raise ValueError("report checksum mismatch")
    expected = render_phase7_validation_report(recomputed)
    if sha256_bytes(expected.encode("utf-8")) != canonical.checksum:
        raise ValueError("report rendering checksum mismatch")
    if path.read_text(encoding="utf-8") != expected:
        raise ValueError("stored report content mismatch")
    return expected


def run_phase7_observation_reporting_workflow(
    *,
    observation: Phase7PaperPilotObservation,
    registry: Phase7ValidationMemoryRegistry,
) -> Phase7ValidationReportArtifact:
    """Explicit human invocation: persist attested evidence and issue NO_GO report."""

    registry.record_session(observation.session)
    registry.record_pilot(observation)
    return write_phase7_validation_report(
        experiment_id=observation.experiment_id,
        registry=registry,
    )
