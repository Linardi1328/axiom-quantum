from __future__ import annotations

import html
import json
import re
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

from spy_market_agent.benchmark.artifacts import sha256_bytes
from spy_market_agent.research.artifacts import ResearchArtifactStore
from spy_market_agent.research.errors import ResearchRegistryError, raise_research_error
from spy_market_agent.research.experiment_core import (
    ExperimentDefinition,
    ExperimentResult,
    experiment_identity,
    result_identity,
)

RESEARCH_REPORT_SCHEMA_VERSION = "axiom-research-report-v1"
RESEARCH_REPORT_PREFIX = "axiom_report_"
_REPORT_RESULT_ID = re.compile(r"^aq-result-[0-9a-f]{24}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MARKDOWN_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+\-.!|~])")
_BACKTICK_RUN = re.compile(r"`+")


class ResearchReportArtifact(BaseModel):
    """Reference to a rendered report without mutating the source result identity."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["axiom-research-report-v1"] = "axiom-research-report-v1"
    result_id: str
    relative_path: str
    checksum: str

    @field_validator("result_id")
    @classmethod
    def _result_id(cls, value: str) -> str:
        """Require the report to reference one canonical Axiom result identity."""

        if not _REPORT_RESULT_ID.fullmatch(value):
            raise ValueError("result_id must be a canonical Axiom result identity.")
        return value

    @field_validator("relative_path")
    @classmethod
    def _relative_path(cls, value: str) -> str:
        """Require a traversal-free POSIX path beneath the configured artifact root."""

        if not value.strip() or "\\" in value:
            raise ValueError("relative_path must be a nonempty POSIX-style relative path.")
        path = PurePosixPath(value)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise ValueError("relative_path must stay relative without traversal components.")
        return path.as_posix()

    @field_validator("checksum")
    @classmethod
    def _checksum(cls, value: str) -> str:
        """Require the rendered report checksum to be canonical lowercase SHA-256."""

        if not _SHA256.fullmatch(value):
            raise ValueError("checksum must be a lowercase SHA-256 digest.")
        return value


def research_report_name(result: ExperimentResult) -> str:
    """Return the immutable report filename derived from canonical result identity."""

    return f"{RESEARCH_REPORT_PREFIX}{result_identity(result)}.md"


def render_research_report(
    definition: ExperimentDefinition,
    result: ExperimentResult,
) -> str:
    """Render deterministic Markdown for one canonical experiment/result pair."""

    experiment_id = experiment_identity(definition)
    if result.experiment_id != experiment_id:
        raise_research_error(
            ResearchRegistryError,
            "research_report_experiment_identity_mismatch",
            "report result must belong to the canonical experiment definition.",
        )

    result_id = result_identity(result)
    lines = [
        "# Axiom Quantum Research Report",
        "",
        f"- Report schema: {_code_span(RESEARCH_REPORT_SCHEMA_VERSION)}",
        f"- Experiment ID: {_code_span(experiment_id)}",
        f"- Result ID: {_code_span(result_id)}",
        f"- Execution authority: {_code_span(definition.execution_authority)}",
        "",
        "## Research question",
        "",
        "**Name:**",
        *_blockquote(definition.name),
        "",
        "**Hypothesis:**",
        *_blockquote(definition.hypothesis),
        "",
        "**Question:**",
        *_blockquote(definition.research_question),
        "",
        "## Scientific definition",
        "",
        f"- Assets: {_code_list(definition.asset_universe)}",
        f"- Evaluation protocol: {_code_span(definition.evaluation_protocol)}",
        f"- Cost model: {_code_span(definition.cost_model_id)}",
        f"- Feature families: {_code_list(definition.feature_families)}",
        f"- Strategy IDs: {_code_list(definition.strategy_ids)}",
        f"- Model IDs: {_code_list(definition.model_ids)}",
        f"- Primary metrics: {_code_list(definition.primary_metrics)}",
        f"- Tags: {_code_list(definition.tags)}",
        "",
        "## Dataset lineage",
        "",
    ]
    for dataset in definition.datasets:
        lines.extend(
            [
                f"### {_code_span(dataset.dataset_id)}",
                "",
                f"- Checksum: {_code_span(dataset.checksum)}",
                f"- Feature schema: {_code_span(dataset.feature_schema)}",
                f"- Label schema: {_code_span(dataset.label_schema)}",
                f"- First session: {_code_span(dataset.first_session.isoformat())}",
                f"- Last session: {_code_span(dataset.last_session.isoformat())}",
                "",
            ]
        )

    runtime = definition.runtime_lineage
    lines.extend(
        [
            "## Runtime lineage",
            "",
            f"- Git commit: {_code_span(runtime.git_commit_sha)}",
            f"- Package version: {_code_span(runtime.package_version)}",
            f"- Python version: {_code_span(runtime.python_version)}",
            "- Dependencies:",
        ]
    )
    for package, version in sorted(runtime.dependency_versions.items()):
        lines.append(f"  - {_code_span(package)}: {_code_span(version)}")

    lines.extend(
        [
            "",
            "## Result",
            "",
            f"- Lifecycle: {_code_span(result.lifecycle_state.value)}",
            f"- Outcome: {_code_span(result.outcome.value)}",
            f"- Strategy state: {_code_span(result.strategy_state.value)}",
            f"- Completed at: {_code_span(_utc_timestamp(result))}",
            "",
            "### Summary",
            "",
            *_blockquote(result.summary),
            "",
            "### Conclusion",
            "",
            *_blockquote(result.conclusion),
            "",
            "## Metric snapshot",
            "",
        ]
    )
    if result.metric_snapshot:
        for metric, value in sorted(result.metric_snapshot.items()):
            lines.append(f"- {_code_span(metric)}: {_code_span(_metric_value(value))}")
    else:
        lines.append("- No metrics recorded.")

    lines.extend(["", "## Evidence references", ""])
    if result.evidence:
        for evidence in sorted(result.evidence, key=lambda item: item.name):
            lines.extend(
                [
                    f"### {_code_span(evidence.name)}",
                    "",
                    f"- Path: {_code_span(evidence.relative_path)}",
                    f"- SHA-256: {_code_span(evidence.checksum)}",
                    "",
                ]
            )
    else:
        lines.extend(["- No external evidence references recorded.", ""])

    lines.extend(
        [
            "## Authority boundary",
            "",
            "This report is research evidence only. It grants no shadow, paper, broker, or ",
            "live execution authority and cannot promote a strategy beyond the state already ",
            "recorded in the immutable result.",
            "",
        ]
    )
    return "\n".join(lines)


def write_research_report(
    *,
    definition: ExperimentDefinition,
    result: ExperimentResult,
    store: ResearchArtifactStore | None = None,
) -> ResearchReportArtifact:
    """Persist a deterministic append-only report without changing the source result."""

    artifact_store = store or ResearchArtifactStore()
    content = render_research_report(definition, result).encode("utf-8")
    checksum = sha256_bytes(content)
    experiment_id = experiment_identity(definition)
    result_id = result_identity(result)
    name = research_report_name(result)
    artifact_store.write_bytes(
        experiment_id,
        name,
        content,
        expected_checksum=checksum,
        allow_replace=False,
    )
    path = artifact_store.artifact_path(experiment_id, name)
    return ResearchReportArtifact(
        result_id=result_id,
        relative_path=artifact_store.relative_path(path),
        checksum=checksum,
    )


def _escape_markdown_text(value: str) -> str:
    """Escape untrusted prose so it cannot create Markdown or raw-HTML structure."""

    escaped_html = html.escape(value, quote=False)
    return _MARKDOWN_SPECIAL.sub(r"\\\1", escaped_html)


def _blockquote(value: str) -> list[str]:
    """Render escaped free-form prose inside a trusted Markdown blockquote."""

    escaped = _escape_markdown_text(value)
    return [f"> {line}" if line else ">" for line in escaped.splitlines()]


def _code_span(value: str) -> str:
    """Render arbitrary single-field text in a delimiter-safe Markdown code span."""

    normalized = value.replace("\r", r"\r").replace("\n", r"\n")
    longest_run = max(
        (len(match.group(0)) for match in _BACKTICK_RUN.finditer(normalized)),
        default=0,
    )
    fence = "`" * (longest_run + 1)
    needs_padding = normalized.startswith(("`", " ")) or normalized.endswith(("`", " "))
    padding = " " if needs_padding else ""
    return f"{fence}{padding}{normalized}{padding}{fence}"


def _code_list(values: tuple[str, ...]) -> str:
    """Render canonical identifier tuples as deterministic Markdown code spans."""

    if not values:
        return "none"
    return ", ".join(_code_span(value) for value in values)


def _metric_value(value: str | int | float | bool | None) -> str:
    """Serialize one metric scalar deterministically for report display."""

    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def _utc_timestamp(result: ExperimentResult) -> str:
    """Render the canonical UTC completion timestamp with an explicit Z suffix."""

    return result.completed_at.isoformat().replace("+00:00", "Z")


def report_artifact_path(
    *,
    definition: ExperimentDefinition,
    result: ExperimentResult,
    store: ResearchArtifactStore | None = None,
) -> Path:
    """Return the validated report path without writing the report."""

    experiment_id = experiment_identity(definition)
    if result.experiment_id != experiment_id:
        raise_research_error(
            ResearchRegistryError,
            "research_report_experiment_identity_mismatch",
            "report result must belong to the canonical experiment definition.",
        )
    artifact_store = store or ResearchArtifactStore()
    return artifact_store.artifact_path(experiment_id, research_report_name(result))
