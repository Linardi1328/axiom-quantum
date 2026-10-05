from __future__ import annotations

import html
import json
import re
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from spy_market_agent.benchmark.artifacts import sha256_bytes, sha256_json
from spy_market_agent.research.artifacts import ResearchArtifactStore
from spy_market_agent.research.errors import ResearchRegistryError, raise_research_error
from spy_market_agent.research.experiment_core import ExperimentResult, result_identity
from spy_market_agent.research.resampling import (
    CanonicalResamplingEvidence,
    ResamplingDistributionSummary,
)
from spy_market_agent.research.robustness import CanonicalRobustnessEvidence
from spy_market_agent.research.validation_contract import (
    ValidationCase,
    ValidationEvidenceSourceKind,
)
from spy_market_agent.research.validation_engine import (
    ValidationDecision,
    ValidationPolicy,
    ValidationVerdict,
    evaluate_validation_case,
    validation_policy_digest,
)
from spy_market_agent.research.validation_memory import (
    StrategyGraveyardEntry,
    ValidationMemoryRegistry,
    build_strategy_graveyard_entry,
    validation_decision_identity,
)

VALIDATION_REPORT_SCHEMA_VERSION = "axiom-validation-report-v1"
VALIDATION_REPORT_PREFIX = "axiom_validation_report_"
_DECISION_ID = re.compile(r"^aq-decision-[0-9a-f]{24}$")
_VALIDATION_ID = re.compile(r"^aq-validation-[0-9a-f]{24}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MARKDOWN_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+\-.!|~])")
_BACKTICK_RUN = re.compile(r"`+")


class ValidationReportArtifact(BaseModel):
    """Checksum-bound reference to one immutable Phase 2 validation report."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["axiom-validation-report-v1"] = "axiom-validation-report-v1"
    decision_id: str
    validation_id: str
    relative_path: str
    checksum: str

    @field_validator("decision_id")
    @classmethod
    def _decision_id(cls, value: str) -> str:
        """Require the canonical content-addressed validation decision identity shape."""

        if not _DECISION_ID.fullmatch(value):
            raise ValueError("decision_id must be a canonical Axiom validation decision identity")
        return value

    @field_validator("validation_id")
    @classmethod
    def _validation_id(cls, value: str) -> str:
        """Require the canonical Phase 2 validation-case identity shape."""

        if not _VALIDATION_ID.fullmatch(value):
            raise ValueError("validation_id must be a canonical Axiom validation identity")
        return value

    @field_validator("relative_path")
    @classmethod
    def _relative_path(cls, value: str) -> str:
        """Require a traversal-free POSIX path beneath the research artifact root."""

        if not value.strip() or "\\" in value:
            raise ValueError("relative_path must be a nonempty POSIX-style relative path")
        path = PurePosixPath(value)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise ValueError("relative_path must stay relative without traversal components")
        return path.as_posix()

    @field_validator("checksum")
    @classmethod
    def _checksum(cls, value: str) -> str:
        """Require the rendered report checksum to be canonical lowercase SHA-256."""

        if not _SHA256.fullmatch(value):
            raise ValueError("checksum must be a lowercase SHA-256 digest")
        return value


class ValidationWorkflowResult(BaseModel):
    """Persisted research-only outcome of one complete Phase 2 validation workflow."""

    model_config = ConfigDict(frozen=True)

    decision: ValidationDecision
    decision_id: str
    graveyard: StrategyGraveyardEntry | None = None
    report: ValidationReportArtifact
    execution_authority: Literal["none"] = "none"

    @model_validator(mode="after")
    def _consistent_links(self) -> ValidationWorkflowResult:
        """Require exact decision, report, and rejected-only graveyard cross-links."""

        if self.decision_id != validation_decision_identity(self.decision):
            raise ValueError("workflow decision_id must match the canonical decision")
        if self.report.decision_id != self.decision_id:
            raise ValueError("workflow report must reference the canonical decision")
        if self.report.validation_id != self.decision.validation_id:
            raise ValueError("workflow report must reference the canonical validation case")
        if self.decision.verdict == ValidationVerdict.REJECTED:
            if self.graveyard is None or self.graveyard != build_strategy_graveyard_entry(
                self.decision
            ):
                raise ValueError("rejected validation workflow requires the canonical graveyard")
        elif self.graveyard is not None:
            raise ValueError("non-rejected validation workflow must not create graveyard state")
        return self

    @property
    def graveyard_id(self) -> str | None:
        """Return the linked rejected-strategy identity when the workflow was rejected."""

        return self.graveyard.graveyard_id if self.graveyard is not None else None


def validation_report_name(decision: ValidationDecision) -> str:
    """Return the immutable report filename derived from decision identity."""

    return f"{VALIDATION_REPORT_PREFIX}{validation_decision_identity(decision)}.md"


def _require_bound_quantitative_evidence(
    *,
    case: ValidationCase,
    evidence: BaseModel | None,
    source_kind: ValidationEvidenceSourceKind,
    label: str,
) -> None:
    """Reject supplied quantitative evidence that is not checksum-bound to the validation case."""

    if evidence is None:
        return
    checksum = sha256_json(evidence.model_dump(mode="json"))
    if not any(
        ref.source_kind == source_kind and ref.checksum == checksum for ref in case.evidence
    ):
        raise_research_error(
            ResearchRegistryError,
            "validation_report_unbound_evidence",
            f"supplied {label} evidence must be checksum-bound to the validation case.",
        )


def _validate_report_inputs(
    *,
    case: ValidationCase,
    source_result: ExperimentResult,
    policy: ValidationPolicy,
    decision: ValidationDecision,
    robustness_evidence: CanonicalRobustnessEvidence | None,
    resampling_evidence: CanonicalResamplingEvidence | None,
    graveyard_entry: StrategyGraveyardEntry | None,
) -> None:
    """Require every rendered object to reconstruct the exact canonical validation decision."""

    if case.policy_id != policy.policy_id or case.policy_digest != validation_policy_digest(policy):
        raise_research_error(
            ResearchRegistryError,
            "validation_report_policy_mismatch",
            "validation report policy must match the canonical validation case.",
        )
    if (
        source_result.experiment_id != case.experiment_id
        or result_identity(source_result) != case.result_id
    ):
        raise_research_error(
            ResearchRegistryError,
            "validation_report_source_result_mismatch",
            "validation report source result must match the canonical validation case.",
        )
    _require_bound_quantitative_evidence(
        case=case,
        evidence=robustness_evidence,
        source_kind=ValidationEvidenceSourceKind.ROBUSTNESS_EVIDENCE,
        label="robustness",
    )
    _require_bound_quantitative_evidence(
        case=case,
        evidence=resampling_evidence,
        source_kind=ValidationEvidenceSourceKind.RESAMPLING_EVIDENCE,
        label="resampling",
    )
    expected = evaluate_validation_case(
        case=case,
        source_result=source_result,
        policy=policy,
        robustness_evidence=robustness_evidence,
        resampling_evidence=resampling_evidence,
    )
    if expected != decision:
        raise_research_error(
            ResearchRegistryError,
            "validation_report_decision_mismatch",
            "validation report decision must equal a fresh evaluation of the supplied evidence.",
        )
    if decision.verdict == ValidationVerdict.REJECTED:
        if graveyard_entry is None or build_strategy_graveyard_entry(decision) != graveyard_entry:
            raise_research_error(
                ResearchRegistryError,
                "validation_report_graveyard_mismatch",
                "rejected validation report requires the exact canonical graveyard entry.",
            )
    elif graveyard_entry is not None:
        raise_research_error(
            ResearchRegistryError,
            "validation_report_unexpected_graveyard",
            "non-rejected validation report must not include graveyard state.",
        )


def render_validation_report(
    *,
    case: ValidationCase,
    source_result: ExperimentResult,
    policy: ValidationPolicy,
    decision: ValidationDecision,
    robustness_evidence: CanonicalRobustnessEvidence | None = None,
    resampling_evidence: CanonicalResamplingEvidence | None = None,
    graveyard_entry: StrategyGraveyardEntry | None = None,
) -> str:
    """Render deterministic Markdown over the complete Phase 2 validation gate path."""

    _validate_report_inputs(
        case=case,
        source_result=source_result,
        policy=policy,
        decision=decision,
        robustness_evidence=robustness_evidence,
        resampling_evidence=resampling_evidence,
        graveyard_entry=graveyard_entry,
    )
    decision_id = validation_decision_identity(decision)
    lines = [
        "# Axiom Quantum Validation Report",
        "",
        f"- Report schema: {_code_span(VALIDATION_REPORT_SCHEMA_VERSION)}",
        f"- Validation ID: {_code_span(case.validation_id)}",
        f"- Decision ID: {_code_span(decision_id)}",
        f"- Verdict: {_code_span(decision.verdict.value)}",
        f"- Policy ID: {_code_span(policy.policy_id)}",
        f"- Policy digest: {_code_span(case.policy_digest)}",
        f"- Experiment ID: {_code_span(case.experiment_id)}",
        f"- Result ID: {_code_span(case.result_id)}",
        f"- Execution authority: {_code_span(case.execution_authority)}",
        "",
        "## Evidence lineage",
        "",
    ]
    for evidence in case.evidence:
        lines.extend(
            [
                f"### {_code_span(evidence.stage.value)} / {_code_span(evidence.evidence_id)}",
                "",
                f"- Source kind: {_code_span(evidence.source_kind.value)}",
                f"- Source ID: {_code_span(evidence.source_id)}",
                f"- SHA-256: {_code_span(evidence.checksum)}",
                "",
            ]
        )

    lines.extend(["## Source result", ""])
    lines.append(f"- Strategy state: {_code_span(source_result.strategy_state.value)}")
    if source_result.metric_snapshot:
        for name, value in sorted(source_result.metric_snapshot.items()):
            lines.append(f"- {_code_span(name)}: {_code_span(_metric_value(value))}")
    else:
        lines.append("- No source-result metrics recorded.")

    lines.extend(["", "## Robustness evidence", ""])
    if robustness_evidence is None:
        lines.append("- Not supplied to this validation decision.")
    else:
        lines.append(f"- Baseline scenario: {_code_span(robustness_evidence.baseline_scenario_id)}")
        lines.append(f"- Scenario count: {_code_span(str(len(robustness_evidence.scenario_ids)))}")
        for summary in robustness_evidence.summaries:
            relative = (
                "undefined"
                if summary.relative_degradation is None
                else _number(summary.relative_degradation)
            )
            lines.extend(
                [
                    f"### {_code_span(summary.metric_name)}",
                    "",
                    f"- Direction: {_code_span(summary.direction.value)}",
                    f"- Coverage: {_code_span(_number(summary.coverage_fraction))}",
                    f"- Baseline: {_code_span(_number(summary.baseline_value))}",
                    f"- Worst: {_code_span(_number(summary.worst_value))}",
                    "- Absolute degradation: " + _code_span(_number(summary.absolute_degradation)),
                    f"- Relative degradation: {_code_span(relative)}",
                    "",
                ]
            )

    lines.extend(["## Resampling evidence", ""])
    if resampling_evidence is None:
        lines.append("- Not supplied to this validation decision.")
    else:
        config = resampling_evidence.config
        block_size = "none" if config.block_size is None else str(config.block_size)
        lines.extend(
            [
                f"- Method: {_code_span(config.method.value)}",
                f"- Sample count: {_code_span(str(config.sample_count))}",
                f"- Seed: {_code_span(str(config.seed))}",
                f"- Block size: {_code_span(block_size)}",
                "- Source return count: "
                + _code_span(str(resampling_evidence.source_return_count)),
                "- Source return SHA-256: "
                + _code_span(resampling_evidence.source_return_checksum),
                f"- Loss frequency: {_code_span(_number(resampling_evidence.loss_frequency))}",
                "- Drawdown-breach frequency: "
                + _code_span(_number(resampling_evidence.drawdown_breach_frequency)),
                "- Cumulative-return distribution: "
                + _distribution(resampling_evidence.cumulative_return_distribution),
                "- Maximum-drawdown distribution: "
                + _distribution(resampling_evidence.maximum_drawdown_distribution),
            ]
        )

    lines.extend(["", "## Validation gate path", ""])
    for gate in decision.gates:
        lines.extend(
            [
                f"### {_code_span(gate.stage.value)} — {_code_span(gate.status.value)}",
                "",
                f"- Checks: {', '.join(_code_span(item) for item in gate.check_ids)}",
                "- Reasons:",
            ]
        )
        for reason in gate.reasons:
            lines.extend(_blockquote(reason, prefix="  "))
        lines.append("")

    lines.extend(["## Strategy Graveyard", ""])
    if graveyard_entry is None:
        lines.append("- No graveyard entry. This verdict is not a rejection.")
    else:
        lines.extend(
            [
                f"- Graveyard ID: {_code_span(graveyard_entry.graveyard_id)}",
                f"- Linked decision ID: {_code_span(graveyard_entry.decision_id)}",
                f"- Failed gate count: {_code_span(str(len(graveyard_entry.failed_gates)))}",
            ]
        )

    lines.extend(
        [
            "",
            "## Authority boundary",
            "",
            (
                "This validation report is research evidence only. A validated research candidate "
                "is not authorized for shadow, paper, broker, or live execution."
            ),
            (
                "Insufficient evidence is not a pass, and no verdict in Phase 2 changes execution "
                "authority from `none`."
            ),
            "",
        ]
    )
    return "\n".join(lines)


def write_validation_report(
    *,
    case: ValidationCase,
    source_result: ExperimentResult,
    policy: ValidationPolicy,
    decision: ValidationDecision,
    robustness_evidence: CanonicalRobustnessEvidence | None = None,
    resampling_evidence: CanonicalResamplingEvidence | None = None,
    graveyard_entry: StrategyGraveyardEntry | None = None,
    store: ResearchArtifactStore | None = None,
) -> ValidationReportArtifact:
    """Persist one deterministic append-only validation report and verify its checksum."""

    artifact_store = store or ResearchArtifactStore()
    content = render_validation_report(
        case=case,
        source_result=source_result,
        policy=policy,
        decision=decision,
        robustness_evidence=robustness_evidence,
        resampling_evidence=resampling_evidence,
        graveyard_entry=graveyard_entry,
    ).encode("utf-8")
    checksum = sha256_bytes(content)
    decision_id = validation_decision_identity(decision)
    name = validation_report_name(decision)
    artifact_store.write_bytes(
        case.experiment_id,
        name,
        content,
        expected_checksum=checksum,
        allow_replace=False,
    )
    if artifact_store.checksum(case.experiment_id, name) != checksum:
        raise_research_error(
            ResearchRegistryError,
            "validation_report_checksum_mismatch",
            "persisted validation report checksum does not match rendered content.",
        )
    path = artifact_store.artifact_path(case.experiment_id, name)
    return ValidationReportArtifact(
        decision_id=decision_id,
        validation_id=case.validation_id,
        relative_path=artifact_store.relative_path(path),
        checksum=checksum,
    )


def run_validation_workflow(
    *,
    case: ValidationCase,
    source_result: ExperimentResult,
    policy: ValidationPolicy,
    robustness_evidence: CanonicalRobustnessEvidence | None = None,
    resampling_evidence: CanonicalResamplingEvidence | None = None,
    store: ResearchArtifactStore | None = None,
) -> ValidationWorkflowResult:
    """Evaluate, persist, graveyard when rejected, and report one Phase 2 validation case."""

    artifact_store = store or ResearchArtifactStore()
    memory = ValidationMemoryRegistry(artifact_store)
    decision = evaluate_validation_case(
        case=case,
        source_result=source_result,
        policy=policy,
        robustness_evidence=robustness_evidence,
        resampling_evidence=resampling_evidence,
    )
    decision_id = memory.record_decision(decision)
    if memory.load_decision(case.experiment_id, decision_id).decision != decision:
        raise_research_error(
            ResearchRegistryError,
            "validation_workflow_decision_reload_mismatch",
            "persisted validation decision differs from the evaluated decision.",
        )

    graveyard_entry: StrategyGraveyardEntry | None = None
    if decision.verdict == ValidationVerdict.REJECTED:
        graveyard_id = memory.record_graveyard_entry(decision)
        graveyard_entry = memory.load_graveyard_entry(case.experiment_id, graveyard_id)

    report = write_validation_report(
        case=case,
        source_result=source_result,
        policy=policy,
        decision=decision,
        robustness_evidence=robustness_evidence,
        resampling_evidence=resampling_evidence,
        graveyard_entry=graveyard_entry,
        store=artifact_store,
    )
    return ValidationWorkflowResult(
        decision=decision,
        decision_id=decision_id,
        graveyard=graveyard_entry,
        report=report,
    )


def _escape_markdown_text(value: str) -> str:
    """Escape untrusted prose so it cannot create Markdown or raw-HTML structure."""

    return _MARKDOWN_SPECIAL.sub(r"\\\1", html.escape(value, quote=False))


def _blockquote(value: str, *, prefix: str = "") -> list[str]:
    """Render escaped prose inside a trusted Markdown blockquote."""

    escaped = _escape_markdown_text(value)
    return [f"{prefix}> {line}" if line else f"{prefix}>" for line in escaped.splitlines()]


def _code_span(value: str) -> str:
    """Render arbitrary scalar text in a delimiter-safe Markdown code span."""

    normalized = value.replace("\r", r"\r").replace("\n", r"\n")
    longest_run = max(
        (len(match.group(0)) for match in _BACKTICK_RUN.finditer(normalized)),
        default=0,
    )
    fence = "`" * (longest_run + 1)
    needs_padding = normalized.startswith(("`", " ")) or normalized.endswith(("`", " "))
    padding = " " if needs_padding else ""
    return f"{fence}{padding}{normalized}{padding}{fence}"


def _metric_value(value: str | int | float | bool | None) -> str:
    """Serialize one source-result metric deterministically."""

    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def _number(value: float) -> str:
    """Render finite validation numerics deterministically."""

    return json.dumps(value, allow_nan=False)


def _distribution(summary: ResamplingDistributionSummary) -> str:
    """Render a canonical resampling distribution summary on one deterministic line."""

    return ", ".join(
        (
            f"minimum={_code_span(_number(summary.minimum))}",
            f"p05={_code_span(_number(summary.p05))}",
            f"median={_code_span(_number(summary.median))}",
            f"p95={_code_span(_number(summary.p95))}",
            f"maximum={_code_span(_number(summary.maximum))}",
        )
    )
