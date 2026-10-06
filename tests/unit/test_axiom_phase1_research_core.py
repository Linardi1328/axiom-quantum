from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from spy_market_agent.research.artifacts import ResearchArtifactStore
from spy_market_agent.research.errors import ResearchArtifactError, ResearchRegistryError
from spy_market_agent.research.experiment_core import (
    ExperimentDefinition,
    ExperimentLifecycleState,
    ExperimentOutcome,
    ExperimentResult,
    ResearchDatasetRef,
    ResearchRuntimeLineage,
    StrategyResearchState,
    experiment_identity,
    result_identity,
)
from spy_market_agent.research.memory import (
    AXIOM_EXPERIMENT_MANIFEST_NAME,
    ResearchMemoryRegistry,
)


def _runtime() -> ResearchRuntimeLineage:
    return ResearchRuntimeLineage(
        git_commit_sha="7fdaa4a849482ace97a3d07515b409a9e0273798",
        package_version="2.0.0b1",
        python_version="3.12.12",
        dependency_versions={"pydantic": "2.13.4", "pandas": "2.3.3"},
    )


def _dataset(checksum: str = "a" * 64) -> ResearchDatasetRef:
    return ResearchDatasetRef(
        dataset_id="spy-daily-canonical-v1",
        checksum=checksum,
        feature_schema="spy-feature-v1",
        label_schema="spy-label-v1",
        first_session=date(2020, 1, 2),
        last_session=date(2026, 9, 30),
    )


def _definition(*, checksum: str = "a" * 64, notes: str = "") -> ExperimentDefinition:
    return ExperimentDefinition(
        name="SPY momentum regime stability",
        hypothesis="Momentum should retain measurable edge across forward regimes.",
        research_question="Does the candidate survive walk-forward evaluation after costs?",
        asset_universe=("SPY",),
        datasets=(_dataset(checksum),),
        feature_families=("trailing_returns", "realized_volatility"),
        strategy_ids=("momentum-v1",),
        model_ids=("logistic-v1",),
        evaluation_protocol="walk-forward-v1",
        cost_model_id="costs-v1",
        primary_metrics=("expectancy", "max_drawdown", "sharpe"),
        runtime_lineage=_runtime(),
        tags=("phase1", "spy"),
        notes=notes,
    )


def _registry(tmp_path: Path) -> ResearchMemoryRegistry:
    store = ResearchArtifactStore(
        Path("artifacts/research"),
        repository_root=tmp_path,
    )
    return ResearchMemoryRegistry(store)


def test_experiment_identity_is_deterministic_and_ignores_operator_notes() -> None:
    first = _definition(notes="owner note one")
    second = _definition(notes="owner note two")

    assert experiment_identity(first) == experiment_identity(second)
    assert experiment_identity(first).startswith("aq-exp-")


def test_register_experiment_is_idempotent_across_operator_note_edits(
    tmp_path: Path,
) -> None:
    registry = _registry(tmp_path)
    first = _definition(notes="owner note one")
    second = _definition(notes="owner note two")

    first_id = registry.register_experiment(first)
    second_id = registry.register_experiment(second)

    assert second_id == first_id
    assert registry.load_experiment(first_id).definition == first


def test_experiment_identity_changes_with_scientific_lineage() -> None:
    assert experiment_identity(_definition(checksum="a" * 64)) != experiment_identity(
        _definition(checksum="b" * 64)
    )


def test_definition_fails_closed_on_unsafe_authority_duplicates_and_notes() -> None:
    base = _definition().model_dump(mode="python")

    with pytest.raises(ValidationError):
        ExperimentDefinition.model_validate({**base, "execution_authority": "paper"})
    with pytest.raises(ValidationError, match="must not contain duplicates"):
        ExperimentDefinition.model_validate({**base, "asset_universe": ("SPY", "SPY")})
    with pytest.raises(ValidationError, match="must not contain secrets"):
        ExperimentDefinition.model_validate({**base, "notes": "api_key=do-not-store-this"})


def test_result_contract_rejects_non_finite_metrics_and_invalid_candidate_state() -> None:
    experiment_id = experiment_identity(_definition())
    base = {
        "experiment_id": experiment_id,
        "lifecycle_state": ExperimentLifecycleState.COMPLETED,
        "summary": "Forward evaluation completed.",
        "conclusion": "Evidence remains research-only.",
        "metric_snapshot": {"expectancy": 0.12},
        "completed_at": datetime(2026, 10, 4, 1, 0, tzinfo=UTC),
    }

    with pytest.raises(ValidationError, match="must be finite"):
        ExperimentResult.model_validate(
            {
                **base,
                "outcome": ExperimentOutcome.COMPLETED,
                "metric_snapshot": {"expectancy": float("nan")},
            }
        )
    with pytest.raises(ValidationError, match="validation_candidate requires"):
        ExperimentResult.model_validate(
            {
                **base,
                "outcome": ExperimentOutcome.INCONCLUSIVE,
                "strategy_state": StrategyResearchState.VALIDATION_CANDIDATE,
            }
        )


def test_research_memory_round_trips_and_is_append_only(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    definition = _definition()
    experiment_id = registry.register_experiment(definition)

    assert registry.register_experiment(definition) == experiment_id
    assert registry.list_experiment_ids() == (experiment_id,)
    assert registry.load_experiment(experiment_id).definition == definition

    first_result = ExperimentResult(
        experiment_id=experiment_id,
        lifecycle_state=ExperimentLifecycleState.COMPLETED,
        outcome=ExperimentOutcome.COMPLETED,
        strategy_state=StrategyResearchState.RESEARCH_ONLY,
        summary="Baseline completed.",
        conclusion="Keep in research.",
        metric_snapshot={"expectancy": 0.03, "max_drawdown": -0.08},
        completed_at=datetime(2026, 10, 4, 1, 0, tzinfo=UTC),
    )
    first_result_id = registry.record_result(first_result)
    assert registry.record_result(first_result) == first_result_id
    assert registry.load_result(experiment_id, first_result_id).result == first_result

    second_result = first_result.model_copy(
        update={
            "summary": "Independent rerun completed.",
            "completed_at": datetime(2026, 10, 4, 2, 0, tzinfo=UTC),
        }
    )
    second_result_id = registry.record_result(second_result)

    assert first_result_id != second_result_id
    assert registry.list_result_ids(experiment_id) == tuple(
        sorted((first_result_id, second_result_id))
    )
    assert result_identity(second_result) == second_result_id


def test_registry_rejects_result_for_unknown_experiment(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    result = ExperimentResult(
        experiment_id="aq-exp-000000000000000000000000",
        lifecycle_state=ExperimentLifecycleState.REJECTED,
        outcome=ExperimentOutcome.FAILED,
        summary="Could not execute research safely.",
        conclusion="Rejected.",
        metric_snapshot={},
        completed_at=datetime(2026, 10, 4, 1, 0, tzinfo=UTC),
    )

    with pytest.raises(ResearchArtifactError) as artifact_exc_info:
        registry.record_result(result)
    assert "research_artifact_missing" in artifact_exc_info.value.codes


def test_registry_fails_closed_on_corrupt_and_mismatched_manifests(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    experiment_id = experiment_identity(_definition())
    manifest_path = registry.store.artifact_path(experiment_id, AXIOM_EXPERIMENT_MANIFEST_NAME)
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text("{broken", encoding="utf-8")

    with pytest.raises(ResearchArtifactError) as artifact_exc_info:
        registry.load_experiment(experiment_id)
    assert "research_artifact_json_load_failed" in artifact_exc_info.value.codes

    manifest_path.write_text(
        '{"experiment_id":"aq-exp-000000000000000000000000","definition":{}}',
        encoding="utf-8",
    )
    with pytest.raises(ResearchRegistryError) as registry_exc_info:
        registry.load_experiment(experiment_id)
    assert "invalid_axiom_experiment_record" in registry_exc_info.value.codes
