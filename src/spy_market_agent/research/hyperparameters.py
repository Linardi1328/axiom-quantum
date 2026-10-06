from __future__ import annotations

from itertools import product

from spy_market_agent.research.errors import ResearchRegistryError, raise_research_error
from spy_market_agent.research.models import (
    HyperparameterSearchDefinition,
    HyperparameterTrialRecord,
    PrimitiveValue,
)


def planned_trials_from_grid(
    search: HyperparameterSearchDefinition,
) -> tuple[HyperparameterTrialRecord, ...]:
    """Enumerate a finite grid without fitting any model."""

    if search.search_method != "grid":
        raise_research_error(
            ResearchRegistryError,
            "search_method_not_grid",
            "planned grid trials require search_method='grid'.",
        )
    parameter_names = tuple(sorted(search.search_space))
    value_lists = tuple(search.search_space[name] for name in parameter_names)
    records: list[HyperparameterTrialRecord] = []
    for trial_index, values in enumerate(product(*value_lists)):
        configuration: dict[str, PrimitiveValue] = dict(zip(parameter_names, values, strict=True))
        records.append(
            HyperparameterTrialRecord(
                trial_index=trial_index,
                configuration=configuration,
                status="planned",
            )
        )
    if len(records) != search.trial_count:
        raise_research_error(
            ResearchRegistryError,
            "grid_trial_count_mismatch",
            "search trial_count must match the finite grid size.",
        )
    return tuple(records)


def validate_inner_training_search(search: HyperparameterSearchDefinition) -> None:
    if search.selection_scope != "inner_training_only":
        raise_research_error(
            ResearchRegistryError,
            "outer_assessment_search_scope_rejected",
            "Phase 3 hyperparameter search must tune only inside eligible training history.",
        )
