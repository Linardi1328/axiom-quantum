from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from spy_market_agent.benchmark.artifacts import sha256_bytes
from spy_market_agent.research.artifacts import ResearchArtifactStore
from spy_market_agent.research.errors import ResearchArtifactError, ResearchRegistryError
from spy_market_agent.research.experiment_core import (
    ExperimentDefinition,
    ExperimentLifecycleState,
    ExperimentOutcome,
    ExperimentResult,
    ResearchDatasetRef,
    ResearchEvidenceRef,
    ResearchRuntimeLineage,
    StrategyResearchState,
    experiment_identity,
)
from spy_market_agent.research.reporting import (
    RESEARCH_REPORT_SCHEMA_VERSION,
    render_research_report,
    research_report_name,
    write_research_report,
)


def _definition() -> ExperimentDefinition:
    return ExperimentDefinition(
        name="SPY walk-forward research report",
        hypothesis="The candidate should remain stable across chronological folds.",
        research_question="Does the evidence remain stable after costs?",
        asset_universe=("SPY",),
        datasets=(
            ResearchDatasetRef(
                dataset_id="spy-daily-canonical-v1",
                checksum="a" * 64,
                feature_schema="spy-feature-v1",
                label_schema="spy-label-v1",
                first_session=date(2020, 1, 2),
                last_session=date(2026, 9, 30),
            ),
        ),
        feature_families=("realized-volatility", "trailing-returns"),
        strategy_ids=("momentum-v1",),
        model_ids=("logistic-v1",),
        evaluation_protocol="walk-forward-v1",
        cost_model_id="costs-v1",
        primary_metrics=("expectancy", "maximum_drawdown"),
        runtime_lineage=ResearchRuntimeLineage(
            git_commit_sha="13b1fe7bf4ec946d5b44cf4ce6845a36a66d0b14",
            package_version="2.0.0b1",
            python_version="3.12.14",
            dependency_versions={"pydantic": "2.13.5", "pandas": "2.3.3"},
        ),
        tags=("phase1", "reporting"),
        notes="operator-only note must not be rendered",
    )


def _result(definition: ExperimentDefinition) -> ExperimentResult:
    return ExperimentResult(
        experiment_id=experiment_identity(definition),
        lifecycle_state=ExperimentLifecycleState.COMPLETED,
        outcome=ExperimentOutcome.COMPLETED,
        strategy_state=StrategyResearchState.RESEARCH_ONLY,
        summary="Walk-forward evidence completed.",
        conclusion="Retain as research-only pending later validation gates.",
        metric_snapshot={
            "total_return": 0.05,
            "maximum_drawdown": 0.10,
            "qualified": True,
        },
        evidence=(
            ResearchEvidenceRef(
                name="backtest-ledger",
                relative_path="artifacts/research/source/backtest.json",
                checksum="b" * 64,
            ),
        ),
        completed_at=datetime(2026, 10, 5, 3, 0, tzinfo=UTC),
    )


def _store(tmp_path: Path) -> ResearchArtifactStore:
    return ResearchArtifactStore(Path("artifacts/research"), repository_root=tmp_path)


def test_research_report_is_deterministic_and_auditable() -> None:
    definition = _definition()
    result = _result(definition)

    first = render_research_report(definition, result)
    second = render_research_report(definition, result)

    assert first == second
    assert first.endswith("\n")
    assert RESEARCH_REPORT_SCHEMA_VERSION in first
    assert experiment_identity(definition) in first
    assert research_report_name(result) == (
        "axiom_report_aq-result-" + research_report_name(result).split("aq-result-")[1]
    )
    assert "`maximum_drawdown`: `0.1`" in first
    assert "`qualified`: `true`" in first
    assert "Execution authority: `none`" in first
    assert "grants no shadow, paper, broker" in first
    assert "operator-only note" not in first


def test_research_report_rejects_mismatched_experiment_result() -> None:
    definition = _definition()
    other = definition.model_copy(update={"name": "Different experiment"})
    result = _result(definition)

    with pytest.raises(ResearchRegistryError) as exc_info:
        render_research_report(other, result)

    assert "research_report_experiment_identity_mismatch" in exc_info.value.codes


def test_write_research_report_is_idempotent_and_content_addressed(tmp_path: Path) -> None:
    definition = _definition()
    result = _result(definition)
    store = _store(tmp_path)

    first = write_research_report(definition=definition, result=result, store=store)
    second = write_research_report(definition=definition, result=result, store=store)

    assert first == second
    assert first.name.startswith("research-report-aq-result-")
    path = tmp_path / first.relative_path
    assert path.name == research_report_name(result)
    assert first.checksum == sha256_bytes(path.read_bytes())
    assert path.read_text(encoding="utf-8") == render_research_report(definition, result)


def test_write_research_report_fails_closed_on_conflicting_existing_artifact(
    tmp_path: Path,
) -> None:
    definition = _definition()
    result = _result(definition)
    store = _store(tmp_path)
    experiment_id = experiment_identity(definition)
    name = research_report_name(result)
    conflicting = b"conflicting report\n"

    store.write_bytes(
        experiment_id,
        name,
        conflicting,
        expected_checksum=sha256_bytes(conflicting),
    )

    with pytest.raises(ResearchArtifactError) as exc_info:
        write_research_report(definition=definition, result=result, store=store)

    assert "research_artifact_conflict" in exc_info.value.codes
