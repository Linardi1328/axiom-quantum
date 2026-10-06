from __future__ import annotations

import html
import re
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from spy_market_agent.benchmark.artifacts import sha256_bytes
from spy_market_agent.intelligence.axiom_decision_support import (
    DecisionSupportAssessment,
    DecisionSupportPolicy,
    DecisionSupportVerdict,
    assess_intelligence_evidence,
)
from spy_market_agent.intelligence.axiom_evidence import (
    MarketIntelligenceEvidence,
    build_market_intelligence_evidence,
)
from spy_market_agent.intelligence.axiom_memory import IntelligenceMemoryRegistry
from spy_market_agent.intelligence.axiom_session import (
    IntelligenceSession,
    build_intelligence_session,
)
from spy_market_agent.intelligence.brief import SPYMarketIntelligenceBrief
from spy_market_agent.research.artifacts import ResearchArtifactStore
from spy_market_agent.research.errors import ResearchRegistryError, raise_research_error
from spy_market_agent.research.validation_engine import ValidationDecision

INTELLIGENCE_REPORT_SCHEMA_VERSION = "axiom-intelligence-report-v1"
INTELLIGENCE_REPORT_PREFIX = "axiom_intelligence_report_"
_REPORT_ID = re.compile(r"^aq-intel-report-[0-9a-f]{24}$")
_ASSESSMENT_ID = re.compile(r"^aq-intel-assessment-[0-9a-f]{24}$")
_EVIDENCE_ID = re.compile(r"^aq-intel-evidence-[0-9a-f]{24}$")
_SESSION_ID = re.compile(r"^aq-intel-session-[0-9a-f]{24}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MARKDOWN_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+\-.!|~])")
_BACKTICK_RUN = re.compile(r"`+")


class IntelligenceReportArtifact(BaseModel):
    """Checksum-bound reference to one immutable Phase 3 report."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["axiom-intelligence-report-v1"] = "axiom-intelligence-report-v1"
    report_id: str
    assessment_id: str
    evidence_id: str
    session_id: str
    experiment_id: str
    verdict: DecisionSupportVerdict
    relative_path: str
    checksum: str
    execution_authority: Literal["none"] = "none"

    @field_validator("report_id")
    @classmethod
    def _report_id(cls, value: str) -> str:
        if not _REPORT_ID.fullmatch(value):
            raise ValueError("report_id must be a canonical Axiom report identity")
        return value

    @field_validator("assessment_id")
    @classmethod
    def _assessment_id(cls, value: str) -> str:
        if not _ASSESSMENT_ID.fullmatch(value):
            raise ValueError("assessment_id must be a canonical Axiom assessment identity")
        return value

    @field_validator("evidence_id")
    @classmethod
    def _evidence_id(cls, value: str) -> str:
        if not _EVIDENCE_ID.fullmatch(value):
            raise ValueError("evidence_id must be a canonical Axiom evidence identity")
        return value

    @field_validator("session_id")
    @classmethod
    def _session_id(cls, value: str) -> str:
        if not _SESSION_ID.fullmatch(value):
            raise ValueError("session_id must be a canonical Axiom session identity")
        return value

    @field_validator("relative_path")
    @classmethod
    def _relative_path(cls, value: str) -> str:
        if not value.strip() or "\\" in value:
            raise ValueError("relative_path must be a nonempty POSIX-style relative path")
        path = PurePosixPath(value)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise ValueError("relative_path must stay relative without traversal components")
        return path.as_posix()

    @field_validator("checksum")
    @classmethod
    def _checksum(cls, value: str) -> str:
        if not _SHA256.fullmatch(value):
            raise ValueError("checksum must be a lowercase SHA-256 digest")
        return value

    @model_validator(mode="after")
    def _identity_matches_checksum(self) -> IntelligenceReportArtifact:
        if self.report_id != f"aq-intel-report-{self.checksum[:24]}":
            raise ValueError("report_id must be derived from the exact report checksum")
        return self


class IntelligenceWorkflowResult(BaseModel):
    """Complete human-invoked Phase 3 outcome with exact lineage."""

    model_config = ConfigDict(frozen=True)

    session: IntelligenceSession
    evidence: MarketIntelligenceEvidence
    assessment: DecisionSupportAssessment
    report: IntelligenceReportArtifact
    execution_authority: Literal["none"] = "none"

    @model_validator(mode="after")
    def _links_are_exact(self) -> IntelligenceWorkflowResult:
        if self.evidence.session != self.session:
            raise ValueError("workflow evidence must embed the exact workflow session")
        if self.assessment.evidence != self.evidence:
            raise ValueError("workflow assessment must embed the exact workflow evidence")
        if self.report.session_id != self.session.session_id:
            raise ValueError("workflow report must reference the exact session")
        if self.report.evidence_id != self.evidence.evidence_id:
            raise ValueError("workflow report must reference the exact evidence")
        if self.report.assessment_id != self.assessment.assessment_id:
            raise ValueError("workflow report must reference the exact assessment")
        if self.report.experiment_id != self.session.experiment_id:
            raise ValueError("workflow report must reference the exact experiment")
        if self.report.verdict != self.assessment.verdict:
            raise ValueError("workflow report verdict must match the assessment")
        return self


def intelligence_report_name(assessment: DecisionSupportAssessment) -> str:
    """Return the immutable report filename derived from assessment identity."""

    return f"{INTELLIGENCE_REPORT_PREFIX}{assessment.assessment_id}.md"


def render_intelligence_report(assessment: DecisionSupportAssessment) -> str:
    """Render a deterministic human-auditable decision-support report."""

    canonical = DecisionSupportAssessment.model_validate(assessment.model_dump(mode="python"))
    evidence = canonical.evidence
    session = evidence.session
    brief = evidence.brief
    lines = [
        "# Axiom Quantum Intelligence OS Report",
        "",
        f"- Report schema: {_code_span(INTELLIGENCE_REPORT_SCHEMA_VERSION)}",
        f"- Session ID: {_code_span(session.session_id)}",
        f"- Invocation ID: {_code_span(session.invocation_id)}",
        f"- Invocation source: {_code_span(session.invocation_source)}",
        f"- Validation decision ID: {_code_span(session.decision_id)}",
        f"- Validation verdict: {_code_span(session.decision.verdict.value)}",
        f"- Intelligence run ID: {_code_span(session.intelligence_run_id)}",
        f"- Evidence ID: {_code_span(evidence.evidence_id)}",
        f"- Assessment ID: {_code_span(canonical.assessment_id)}",
        f"- Decision-support verdict: {_code_span(canonical.verdict.value)}",
        f"- Execution authority: {_code_span(canonical.execution_authority)}",
        "",
        "## Point-in-time lineage",
        "",
        f"- Target instrument: {_code_span(session.target_instrument_id)}",
        f"- As of: {_code_span(session.as_of.isoformat())}",
        f"- Analysis profile: {_code_span(session.analysis_profile_id)}",
        f"- Code revision: {_code_span(session.code_revision)}",
        f"- Configuration SHA-256: {_code_span(session.intelligence_configuration_hash)}",
        f"- Brief SHA-256: {_code_span(evidence.brief_digest)}",
        "- Snapshot IDs: " + ", ".join(_code_span(item) for item in session.snapshot_ids),
        "",
        "## Data quality",
        "",
        f"- Status: {_code_span(brief.data_quality.status.value)}",
        f"- Eligible: {_code_span(str(brief.data_quality.eligible).lower())}",
    ]
    if brief.data_quality.reasons:
        lines.append("- Reasons:")
        for reason in brief.data_quality.reasons:
            lines.extend(_blockquote(reason, prefix="  "))
    else:
        lines.append("- Reasons: none")

    lines.extend(["", "## Market state", ""])
    if not brief.market_state.dimensions:
        lines.append("- No market-state dimensions supplied.")
    for dimension in brief.market_state.dimensions:
        value = "unavailable" if dimension.value is None else repr(dimension.value)
        lines.append(
            "- "
            + _code_span(dimension.dimension_id)
            + ": availability="
            + _code_span(dimension.availability.value)
            + ", value="
            + _code_span(value)
            + ", unit="
            + _code_span(dimension.unit)
        )

    lines.extend(["", "## Scenario evidence", ""])
    if not brief.scenarios:
        lines.append("- No scenario evidence supplied.")
    for entry in brief.scenarios:
        horizon = f"{entry.forecast.horizon.length}-{entry.forecast.horizon.unit.value}"
        lines.extend(
            [
                f"### Horizon {_code_span(horizon)}",
                "",
                f"- Calibration: {_code_span(entry.forecast.calibration_status.value)}",
                f"- Actionability: {_code_span(entry.actionability.status.value)}",
            ]
        )
        for probability in entry.forecast.probabilities:
            lines.append(
                f"- {_code_span(probability.outcome.value)} probability: "
                + _code_span(repr(probability.probability))
            )
        if entry.actionability.reasons:
            lines.append(
                "- Abstention reasons: "
                + ", ".join(_code_span(reason.value) for reason in entry.actionability.reasons)
            )
        lines.append("")

    lines.extend(["## Degradation evidence", ""])
    if not brief.degradation:
        lines.append("- No degradation evidence supplied.")
    for item in brief.degradation:
        lines.append(f"- Status: {_code_span(item.status.value)}")

    lines.extend(["", "## Decision-support gate path", ""])
    for gate in canonical.gates:
        lines.extend(
            [
                f"### {_code_span(gate.gate.value)} — {_code_span(gate.status.value)}",
                "",
            ]
        )
        if gate.reasons:
            lines.append("- Reasons:")
            for reason in gate.reasons:
                lines.extend(_blockquote(reason, prefix="  "))
        else:
            lines.append("- Reasons: none")
        lines.append("")

    lines.extend(["## Known limitations", ""])
    if brief.limitations:
        for limitation in brief.limitations:
            lines.extend(_blockquote(limitation))
    else:
        lines.append("- No limitations were supplied by the canonical brief.")

    lines.extend(["", "## Human decision-support boundary", ""])
    if canonical.verdict == DecisionSupportVerdict.PRESENT_FOR_HUMAN_REVIEW:
        lines.append(
            "The evidence cleared the Phase 3 gates and may be presented to a human for review. "
            "No downstream authority is granted by this result."
        )
    else:
        lines.append(
            "The Phase 3 engine abstained. The result remains an abstention and the gates remain "
            "unchanged."
        )
    lines.extend(["", "Execution authority remains `none`.", ""])
    return "\n".join(lines)


def write_intelligence_report(
    assessment: DecisionSupportAssessment,
    *,
    registry: IntelligenceMemoryRegistry,
) -> IntelligenceReportArtifact:
    """Append and checksum-verify one report after verifying its stored parent chain."""

    canonical = DecisionSupportAssessment.model_validate(assessment.model_dump(mode="python"))
    experiment_id = canonical.evidence.session.experiment_id
    stored = registry.load_assessment(experiment_id, canonical.assessment_id)
    if stored != canonical:
        raise_research_error(
            ResearchRegistryError,
            "intelligence_report_assessment_link_mismatch",
            "report must match its stored decision-support assessment.",
        )
    content = render_intelligence_report(canonical).encode("utf-8")
    checksum = sha256_bytes(content)
    name = intelligence_report_name(canonical)
    registry.store.write_bytes(
        experiment_id,
        name,
        content,
        expected_checksum=checksum,
        allow_replace=False,
    )
    artifact = IntelligenceReportArtifact(
        report_id=f"aq-intel-report-{checksum[:24]}",
        assessment_id=canonical.assessment_id,
        evidence_id=canonical.evidence.evidence_id,
        session_id=canonical.evidence.session.session_id,
        experiment_id=experiment_id,
        verdict=canonical.verdict,
        relative_path=registry.store.relative_path(
            registry.store.artifact_path(experiment_id, name)
        ),
        checksum=checksum,
    )
    if load_intelligence_report(artifact, registry=registry) != content.decode("utf-8"):
        raise_research_error(
            ResearchRegistryError,
            "intelligence_report_reload_mismatch",
            "persisted report differs after deterministic reload.",
        )
    return artifact


def load_intelligence_report(
    artifact: IntelligenceReportArtifact,
    *,
    registry: IntelligenceMemoryRegistry,
) -> str:
    """Verify checksum, path, exact parent chain, and deterministic content on reload."""

    canonical = IntelligenceReportArtifact.model_validate(artifact.model_dump(mode="python"))
    assessment = registry.load_assessment(canonical.experiment_id, canonical.assessment_id)
    if (
        assessment.evidence.evidence_id != canonical.evidence_id
        or assessment.evidence.session.session_id != canonical.session_id
        or assessment.verdict != canonical.verdict
    ):
        raise_research_error(
            ResearchRegistryError,
            "intelligence_report_lineage_mismatch",
            "stored report lineage must match its assessment chain.",
        )
    name = intelligence_report_name(assessment)
    path = registry.store.artifact_path(canonical.experiment_id, name)
    if registry.store.relative_path(path) != canonical.relative_path:
        raise_research_error(
            ResearchRegistryError,
            "intelligence_report_path_mismatch",
            "stored report path must match the canonical artifact path.",
        )
    if registry.store.checksum(canonical.experiment_id, name) != canonical.checksum:
        raise_research_error(
            ResearchRegistryError,
            "intelligence_report_checksum_mismatch",
            "stored report checksum does not match its artifact record.",
        )
    expected = render_intelligence_report(assessment)
    if sha256_bytes(expected.encode("utf-8")) != canonical.checksum:
        raise_research_error(
            ResearchRegistryError,
            "intelligence_report_content_mismatch",
            "artifact checksum does not match deterministic rendering.",
        )
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        raise_research_error(
            ResearchRegistryError,
            "intelligence_report_load_failed",
            "stored report could not be loaded as UTF-8 text.",
        )
    if content != expected:
        raise_research_error(
            ResearchRegistryError,
            "intelligence_report_content_mismatch",
            "stored report differs from deterministic rendering.",
        )
    return content


def run_intelligence_os_workflow(
    *,
    decision: ValidationDecision,
    brief: SPYMarketIntelligenceBrief,
    invocation_id: str,
    store: ResearchArtifactStore | None = None,
    policy: DecisionSupportPolicy | None = None,
) -> IntelligenceWorkflowResult:
    """Run one explicit human-requested Phase 3 decision-support workflow."""

    artifact_store = store or ResearchArtifactStore()
    memory = IntelligenceMemoryRegistry(artifact_store)
    session = build_intelligence_session(
        decision=decision,
        intelligence_run=brief.run_identity,
        invocation_id=invocation_id,
    )
    memory.record_session(session)
    evidence = build_market_intelligence_evidence(session=session, brief=brief)
    memory.record_evidence(evidence)
    assessment = assess_intelligence_evidence(evidence, policy=policy)
    memory.record_assessment(assessment)
    report = write_intelligence_report(assessment, registry=memory)
    return IntelligenceWorkflowResult(
        session=session,
        evidence=evidence,
        assessment=assessment,
        report=report,
    )


def _escape_markdown_text(value: str) -> str:
    return _MARKDOWN_SPECIAL.sub(r"\\\1", html.escape(value, quote=False))


def _blockquote(value: str, *, prefix: str = "") -> list[str]:
    escaped = _escape_markdown_text(value)
    return [f"{prefix}> {line}" if line else f"{prefix}>" for line in escaped.splitlines()]


def _code_span(value: str) -> str:
    normalized = value.replace("\r", r"\r").replace("\n", r"\n")
    longest_run = max(
        (len(match.group(0)) for match in _BACKTICK_RUN.finditer(normalized)),
        default=0,
    )
    fence = "`" * (longest_run + 1)
    needs_padding = normalized.startswith(("`", " ")) or normalized.endswith(("`", " "))
    padding = " " if needs_padding else ""
    return f"{fence}{padding}{normalized}{padding}{fence}"
