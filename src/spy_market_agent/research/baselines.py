from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from spy_market_agent.research.errors import ResearchMetricError, raise_research_error

ClassificationBaselineName = Literal[
    "majority_class",
    "always_positive",
    "always_negative",
    "training_prevalence",
]


def classification_baseline_probabilities(
    baseline_name: ClassificationBaselineName,
    *,
    training_targets: Sequence[int],
    assessment_row_count: int,
) -> tuple[float, ...]:
    if assessment_row_count <= 0:
        raise_research_error(
            ResearchMetricError,
            "invalid_assessment_row_count",
            "assessment_row_count must be positive.",
        )
    parsed_training_targets = _validate_training_targets(training_targets)
    positive_count = sum(parsed_training_targets)
    negative_count = len(parsed_training_targets) - positive_count
    if baseline_name == "always_positive":
        return (1.0,) * assessment_row_count
    if baseline_name == "always_negative":
        return (0.0,) * assessment_row_count
    if baseline_name == "training_prevalence":
        prevalence = positive_count / len(parsed_training_targets)
        return (prevalence,) * assessment_row_count
    if baseline_name == "majority_class":
        probability = 1.0 if positive_count > negative_count else 0.0
        return (probability,) * assessment_row_count
    raise AssertionError("unreachable")


def _validate_training_targets(training_targets: Sequence[int]) -> list[int]:
    if not training_targets:
        raise_research_error(
            ResearchMetricError,
            "empty_training_baseline_targets",
            "training targets are required for Phase 3 baselines.",
        )
    values: list[int] = []
    for target in training_targets:
        if isinstance(target, bool) or int(target) not in {0, 1}:
            raise_research_error(
                ResearchMetricError,
                "invalid_training_baseline_target",
                "training baseline targets must be binary integers.",
            )
        values.append(int(target))
    return values
