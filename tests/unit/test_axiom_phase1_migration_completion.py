from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from spy_market_agent.research.artifacts import ResearchArtifactStore
from spy_market_agent.research.errors import ResearchRegistryError
from spy_market_agent.research.evaluation import CandidateEvaluation, FoldEvaluation
from spy_market_agent.research.experiment_core import (
    ExperimentDefinition,
    ExperimentLifecycleState,
    ExperimentOutcome,
    ExperimentResult,
    ResearchDatasetRef,
    ResearchRuntimeLineage,
    StrategyResearchState,
    experiment_identity,
)
from spy_market_agent.research.memory import ResearchMemoryRegistry
from spy_market_agent.research.migration import (
    RESEARCH_MIGRATION_RECEIPT_SCHEMA_VERSION,
    migrate_canonical_pair_to_research_memory,
    migrate_phase3_candidate_to_research_memory,
    research_migration_identity,
)
from spy_market_agent.research.models import (
    CandidateEvaluationSummary,
    CandidateSelectionResult,
    CostAssumptions,
    DatasetLineage,
    ExperimentManifest,
    FeatureRegistry,
    MetricValue,
    ModelDefinition,
    RuntimeLineage,
)
from spy_market_agent.research.reporting import render_research_report

COMPLETED_AT = datetime(2026, 10, 5, 10, 0, tzinfo=UTC)


def _registry(tmp_path: Path) -> ResearchMemoryRegistry:
    """Build isolated append-only Axiom memory for one test."""

    store = ResearchArtifactStore(Path("artifacts/research"), repository_root=tmp_path)
    return ResearchMemoryRegistry(store)


def _definition(*, notes: str = "") -> ExperimentDefinition:
    """Return a compact canonical experiment fixture."""

    return ExperimentDefinition(
        name="migration-test",
        hypothesis="Legacy evidence should preserve canonical lineage.",
        research_question="Can selected evidence migrate idempotently?",
        asset_universe=("SPY",),
        datasets=(
            ResearchDatasetRef(
                dataset_id="phase3-source",
                checksum="a" * 64,
                feature_schema="feature-v1",
                label_schema="label-v1",
                first_session=date(2020, 1, 2),
                last_session=date(2026, 9, 30),
            ),
        ),
        model_ids=("logistic-v1",),
        evaluation_protocol="walk-forward-v1",
        cost_model_id="cost-v1",
        primary_metrics=("roc_auc",),
        runtime_lineage=ResearchRuntimeLineage(
            git_commit_sha="abcdef1",
            package_version="2.0.0b1",
            python_version="3.12.14",
            dependency_versions={"pydantic": "2.13.5"},
        ),
        notes=notes,
    )


def _result(definition: ExperimentDefinition) -> ExperimentResult:
    """Return a research-only result bound to the supplied definition."""

    return ExperimentResult(
        experiment_id=experiment_identity(definition),
        lifecycle_state=ExperimentLifecycleState.COMPLETED,
        outcome=ExperimentOutcome.COMPLETED,
        strategy_state=StrategyResearchState.RESEARCH_ONLY,
        summary="Migration fixture completed.",
        conclusion="Retain as research-only evidence.",
        metric_snapshot={"roc_auc": 0.61},
        completed_at=COMPLETED_AT,
    )


def _phase3_manifest() -> ExperimentManifest:
    """Return a minimal validated legacy Phase-3 manifest fixture."""

    model = ModelDefinition.model_construct(
        model_name="logistic-v1",
        model_family="regularized_logistic_regression",
        model_schema_version="model-v1",
        parameters=(("C", 1.0),),
        deterministic_probability_output=True,
        approved_dependency="scikit-learn",
        baseline_role=None,
    )
    return ExperimentManifest.model_construct(
        experiment_id="spy-v2p3-source123",
        phase_identifier="version2-phase3-development-research",
        dataset_lineage=DatasetLineage(
            dataset_id="synthetic-phase3-dataset",
            canonical_dataset_checksum="b" * 64,
            provider="synthetic",
            feed="sip",
            timeframe="1Day",
            adjustment="all",
            first_session=date(2020, 1, 2),
            last_session=date(2026, 9, 30),
        ),
        feature_registry=FeatureRegistry.model_construct(feature_schema="feature-v1", features=()),
        enabled_feature_families=("trailing_returns",),
        label_schema="label-v1",
        forecast_horizon="open_t_plus_1_to_open_t_plus_6",
        fold_policy_id="phase3-expanding-walk-forward-v1",
        fold_boundaries=(),
        model_family=model.model_family,
        model_configuration=model,
        hyperparameter_search=None,
        tried_configurations=(),
        calibration_policy=None,
        threshold_policy=None,
        strategy_assumptions=None,
        cost_assumptions=CostAssumptions(
            cost_scenario="phase3-costs-v1",
            commission_bps_per_side="1",
            slippage_bps_per_side="2",
        ),
        random_seeds=(42,),
        baseline_definitions=(),
        metric_definitions=("roc_auc", "log_loss", "brier_score"),
        candidate_selection_rule="phase3-selection-v1",
        candidate_selection_config=None,
        protected_evaluation_status=None,
        runtime_lineage=RuntimeLineage(
            git_commit_sha="abcdef1",
            package_version="2.0.0b1",
            python_version="3.12.14",
            dependency_versions={"pydantic": "2.13.5"},
        ),
        creation_timestamp=COMPLETED_AT,
        owner_operator_notes="legacy note",
    )


def _phase3_evaluation() -> CandidateEvaluation:
    """Return complete non-leaky legacy candidate evidence."""

    value = lambda number: MetricValue(value=number)  # noqa: E731
    summary = CandidateEvaluationSummary(
        candidate_name="candidate-a",
        valid=True,
        leaky=False,
        lineage_complete=True,
        simplicity_rank=10,
        valid_fold_count=2,
        median_roc_auc=value(0.61),
        median_log_loss=value(0.64),
        median_brier_score=value(0.22),
        worst_quartile_roc_auc=value(0.55),
        median_training_prevalence_log_loss_delta=value(0.01),
        median_training_prevalence_brier_delta=value(0.01),
        phase2_baseline_roc_auc_delta=value(0.02),
    )
    return CandidateEvaluation(
        candidate_name="candidate-a",
        candidate_kind="model_candidate",
        feature_families=("trailing_returns",),
        feature_columns=("close_return_1d",),
        model_definition=None,
        fold_evaluations=tuple(
            FoldEvaluation(
                fold_id=f"fold-{index}",
                status="completed",
                metric_set=None,
                probabilities=(),
            )
            for index in range(2)
        ),
        summary=summary,
    )


def _selection() -> CandidateSelectionResult:
    """Return a legacy research promotion that must remain validation-only."""

    return CandidateSelectionResult(
        selected_candidate_name="candidate-a",
        promotion_allowed=True,
        reason="candidate satisfies research promotion gates",
        ranked_candidates=("candidate-a",),
    )


def test_canonical_migration_is_idempotent_and_receipt_identity_is_stable(tmp_path: Path) -> None:
    """Equivalent canonical migrations must resolve to one memory record and receipt."""

    registry = _registry(tmp_path)
    definition = _definition()
    result = _result(definition)

    first = migrate_canonical_pair_to_research_memory(
        definition=definition,
        result=result,
        source_experiment_id="legacy-exp-1",
        source_record_id="candidate-a",
        registry=registry,
    )
    second = migrate_canonical_pair_to_research_memory(
        definition=definition,
        result=result,
        source_experiment_id="legacy-exp-1",
        source_record_id="candidate-a",
        registry=registry,
    )

    assert first == second
    assert first.schema_version == RESEARCH_MIGRATION_RECEIPT_SCHEMA_VERSION
    assert first.migration_id == research_migration_identity(first)
    assert registry.list_experiment_ids() == (first.experiment_id,)
    assert registry.list_result_ids(first.experiment_id) == (first.result_id,)


def test_migration_rejects_mismatched_pair_and_same_identity_conflict(tmp_path: Path) -> None:
    """Migration must fail closed on wrong parents or non-scientific record conflicts."""

    registry = _registry(tmp_path)
    definition = _definition(notes="first note")
    result = _result(definition)
    wrong = result.model_copy(update={"experiment_id": "aq-exp-111111111111111111111111"})
    with pytest.raises(ResearchRegistryError, match="research_migration_experiment_identity_mismatch"):
        migrate_canonical_pair_to_research_memory(
            definition=definition,
            result=wrong,
            source_experiment_id="legacy-exp-1",
            source_record_id="candidate-a",
            registry=registry,
        )

    migrate_canonical_pair_to_research_memory(
        definition=definition,
        result=result,
        source_experiment_id="legacy-exp-1",
        source_record_id="candidate-a",
        registry=registry,
    )
    conflicting = _definition(notes="different operator note")
    assert experiment_identity(conflicting) == experiment_identity(definition)
    with pytest.raises(ResearchRegistryError, match="research_migration_experiment_conflict"):
        migrate_canonical_pair_to_research_memory(
            definition=conflicting,
            result=_result(conflicting),
            source_experiment_id="legacy-exp-1",
            source_record_id="candidate-a",
            registry=registry,
        )


def test_phase3_migration_preserves_provenance_state_and_reportability(tmp_path: Path) -> None:
    """Loaded migrated Phase-3 records must preserve provenance and render a report."""

    registry = _registry(tmp_path)
    manifest = _phase3_manifest()
    receipt = migrate_phase3_candidate_to_research_memory(
        manifest=manifest,
        evaluation=_phase3_evaluation(),
        selection=_selection(),
        name="Legacy Phase 3 candidate",
        hypothesis="Candidate performance should generalize across approved folds.",
        research_question="Does the candidate retain evidence across Phase-3 folds?",
        completed_at=COMPLETED_AT,
        registry=registry,
    )

    stored_experiment = registry.load_experiment(receipt.experiment_id)
    stored_result = registry.load_result(receipt.experiment_id, receipt.result_id)
    assert f"source:{manifest.experiment_id}" in stored_experiment.definition.tags
    assert receipt.source_experiment_id == manifest.experiment_id
    assert receipt.source_record_id == "candidate-a"
    assert receipt.strategy_state == StrategyResearchState.VALIDATION_CANDIDATE
    assert stored_result.result.strategy_state == receipt.strategy_state
    assert stored_experiment.definition.execution_authority == "none"

    report = render_research_report(stored_experiment.definition, stored_result.result)
    assert receipt.experiment_id in report
    assert receipt.result_id in report
    assert "Execution authority: `none`" in report


def test_phase3_repeated_migration_is_byte_stable_in_memory(tmp_path: Path) -> None:
    """Repeated legacy migration must not create duplicate canonical records."""

    registry = _registry(tmp_path)
    kwargs = {
        "manifest": _phase3_manifest(),
        "evaluation": _phase3_evaluation(),
        "selection": _selection(),
        "name": "Legacy Phase 3 candidate",
        "hypothesis": "Candidate performance should generalize across approved folds.",
        "research_question": "Does the candidate retain evidence across Phase-3 folds?",
        "completed_at": COMPLETED_AT,
        "registry": registry,
    }
    first = migrate_phase3_candidate_to_research_memory(**kwargs)
    second = migrate_phase3_candidate_to_research_memory(**kwargs)

    assert first == second
    assert registry.list_result_ids(first.experiment_id) == (first.result_id,)
