from __future__ import annotations

import json
from pathlib import Path

from spy_market_agent.benchmark.artifacts import sha256_bytes
from spy_market_agent.research.artifacts import ResearchArtifactStore
from spy_market_agent.research.errors import ResearchRegistryError, raise_research_error
from spy_market_agent.research.experiment_core import (
    ExperimentDefinition,
    ExperimentResult,
    ResearchEvidenceRef,
    experiment_identity,
    result_identity,
)

RESEARCH_REPORT_SCHEMA_VERSION = "axiom-research-report-v1"
RESEARCH_REPORT_PREFIX = "axiom_report_"


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
        f"- Report schema: `{RESEARCH_REPORT_SCHEMA_VERSION}`",
        f"- Experiment ID: `{experiment_id}`",
        f"- Result ID: `{result_id}`",
        f"- Execution authority: `{definition.execution_authority}`",
        "",
        "## Research question",
        "",
        f"**Name:** {definition.name}",
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
        f"- Evaluation protocol: `{definition.evaluation_protocol}`",
        f"- Cost model: `{definition.cost_model_id}`",
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
                f"### `{dataset.dataset_id}`",
                "",
                f"- Checksum: `{dataset.checksum}`",
                f"- Feature schema: `{dataset.feature_schema}`",
                f"- Label schema: `{dataset.label_schema}`",
                f"- First session: `{dataset.first_session.isoformat()}`",
                f"- Last session: `{dataset.last_session.isoformat()}`",
                "",
            ]
        )

    runtime = definition.runtime_lineage
    lines.extend(
        [
            "## Runtime lineage",
            "",
            f"- Git commit: `{runtime.git_commit_sha}`",
            f"- Package version: `{runtime.package_version}`",
            f"- Python version: `{runtime.python_version}`",
            "- Dependencies:",
        ]
    )
    for package, version in sorted(runtime.dependency_versions.items()):
        lines.append(f"  - `{package}`: `{version}`")

    lines.extend(
        [
            "",
            "## Result",
            "",
            f"- Lifecycle: `{result.lifecycle_state.value}`",
            f"- Outcome: `{result.outcome.value}`",
            f"- Strategy state: `{result.strategy_state.value}`",
            f"- Completed at: `{_utc_timestamp(result)}`",
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
            lines.append(f"- `{metric}`: `{_metric_value(value)}`")
    else:
        lines.append("- No metrics recorded.")

    lines.extend(["", "## Evidence references", ""])
    if result.evidence:
        for evidence in sorted(result.evidence, key=lambda item: item.name):
            lines.extend(
                [
                    f"### `{evidence.name}`",
                    "",
                    f"- Path: `{evidence.relative_path}`",
                    f"- SHA-256: `{evidence.checksum}`",
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
) -> ResearchEvidenceRef:
    """Persist a deterministic append-only report and return its evidence reference."""

    artifact_store = store or ResearchArtifactStore()
    content = render_research_report(definition, result).encode("utf-8")
    checksum = sha256_bytes(content)
    experiment_id = experiment_identity(definition)
    name = research_report_name(result)
    artifact_store.write_bytes(
        experiment_id,
        name,
        content,
        expected_checksum=checksum,
        allow_replace=False,
    )
    path = artifact_store.artifact_path(experiment_id, name)
    return ResearchEvidenceRef(
        name=f"research-report-{result_identity(result)}",
        relative_path=artifact_store.relative_path(path),
        checksum=checksum,
    )


def _blockquote(value: str) -> list[str]:
    return [f"> {line}" if line else ">" for line in value.splitlines()]


def _code_list(values: tuple[str, ...]) -> str:
    if not values:
        return "none"
    return ", ".join(f"`{value}`" for value in values)


def _metric_value(value: str | int | float | bool | None) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def _utc_timestamp(result: ExperimentResult) -> str:
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
