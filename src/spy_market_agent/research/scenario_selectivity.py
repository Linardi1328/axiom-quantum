from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from spy_market_agent.intelligence.scenarios import ScenarioOutcome, ScenarioProbability
from spy_market_agent.research.scenario_calibration import ScenarioCalibrationEvaluation

MI1F_SELECTIVITY_POLICY_ID = "mi1f-selective-scenario-policy-v1"
MI1F_TOP_PROBABILITY_GRID: tuple[float, ...] = (0.50, 0.55, 0.60, 0.65, 0.70)
MI1F_SEPARATION_GRID: tuple[float, ...] = (0.05, 0.10, 0.15, 0.20)
MI1F_MINIMUM_SELECTED_ROWS = 63
MI1F_TARGET_PRECISION = 0.80


class ScenarioSelectivityStatus(StrEnum):
    QUALIFYING_POLICY = "qualifying_policy"
    NO_QUALIFYING_POLICY = "no_qualifying_policy"


@dataclass(frozen=True, slots=True)
class ScenarioSelectivityPolicy:
    policy_id: str
    min_top_probability: float
    min_separation: float

    def __post_init__(self) -> None:
        if self.policy_id != MI1F_SELECTIVITY_POLICY_ID:
            raise ValueError("policy_id must match the frozen MI-1F policy.")
        if self.min_top_probability not in MI1F_TOP_PROBABILITY_GRID:
            raise ValueError("min_top_probability must belong to the frozen MI-1F grid.")
        if self.min_separation not in MI1F_SEPARATION_GRID:
            raise ValueError("min_separation must belong to the frozen MI-1F grid.")


@dataclass(frozen=True, slots=True)
class ScenarioSelectivityCandidate:
    policy: ScenarioSelectivityPolicy
    total_rows: int
    selected_rows: int
    correct_selected_rows: int
    coverage: float
    precision: float | None
    qualifies: bool

    def __post_init__(self) -> None:
        if self.total_rows <= 0:
            raise ValueError("total_rows must be positive.")
        if not 0 <= self.correct_selected_rows <= self.selected_rows <= self.total_rows:
            raise ValueError("selected row counts are inconsistent.")
        expected_coverage = self.selected_rows / self.total_rows
        if not math.isclose(self.coverage, expected_coverage, rel_tol=0.0, abs_tol=1e-12):
            raise ValueError("coverage must match selected row counts.")
        if self.selected_rows == 0:
            if self.precision is not None:
                raise ValueError("precision must be None when no rows are selected.")
        else:
            if self.precision is None:
                raise ValueError("precision is required when rows are selected.")
            expected_precision = self.correct_selected_rows / self.selected_rows
            if not math.isclose(self.precision, expected_precision, rel_tol=0.0, abs_tol=1e-12):
                raise ValueError("precision must match selected row counts.")
        expected_qualifies = (
            self.selected_rows >= MI1F_MINIMUM_SELECTED_ROWS
            and self.precision is not None
            and self.precision >= MI1F_TARGET_PRECISION
        )
        if self.qualifies != expected_qualifies:
            raise ValueError("qualifies must match the frozen MI-1F evidence rule.")


@dataclass(frozen=True, slots=True)
class ScenarioSelectivityEvaluation:
    status: ScenarioSelectivityStatus
    calibration_policy_id: str
    horizon_length: int
    development_through_session: date
    candidates: tuple[ScenarioSelectivityCandidate, ...]
    selected_policy: ScenarioSelectivityPolicy | None
    selected_coverage: float | None
    selected_precision: float | None

    def __post_init__(self) -> None:
        if not isinstance(self.status, ScenarioSelectivityStatus):
            raise ValueError("status must be a ScenarioSelectivityStatus.")
        if not isinstance(self.development_through_session, date):
            raise ValueError("development_through_session must be a date.")
        expected_count = len(MI1F_TOP_PROBABILITY_GRID) * len(MI1F_SEPARATION_GRID)
        if len(self.candidates) != expected_count:
            raise ValueError("candidates must cover the full frozen MI-1F threshold grid.")
        if self.status == ScenarioSelectivityStatus.NO_QUALIFYING_POLICY:
            if any(
                value is not None
                for value in (
                    self.selected_policy,
                    self.selected_coverage,
                    self.selected_precision,
                )
            ):
                raise ValueError("no-qualifying-policy status must not expose a selected policy.")
        else:
            if (
                self.selected_policy is None
                or self.selected_coverage is None
                or self.selected_precision is None
            ):
                raise ValueError("qualifying status requires selected policy metrics.")
            matching = next(
                (item for item in self.candidates if item.policy == self.selected_policy),
                None,
            )
            if matching is None or not matching.qualifies:
                raise ValueError("selected policy must be a qualifying candidate.")
            if not math.isclose(self.selected_coverage, matching.coverage, abs_tol=1e-12):
                raise ValueError("selected_coverage must match the selected candidate.")
            if matching.precision is None or not math.isclose(
                self.selected_precision,
                matching.precision,
                abs_tol=1e-12,
            ):
                raise ValueError("selected_precision must match the selected candidate.")


def evaluate_selective_scenario_policy(
    calibration: ScenarioCalibrationEvaluation,
) -> ScenarioSelectivityEvaluation:
    outcomes = tuple(outcome for fold in calibration.folds for outcome in fold.assessment_outcomes)
    probability_rows = tuple(
        row for fold in calibration.folds for row in fold.calibrated_probability_rows
    )
    candidates = tuple(
        _evaluate_candidate(outcomes, probability_rows, top, separation)
        for top in MI1F_TOP_PROBABILITY_GRID
        for separation in MI1F_SEPARATION_GRID
    )
    qualifying = tuple(item for item in candidates if item.qualifies)
    if not qualifying:
        return ScenarioSelectivityEvaluation(
            status=ScenarioSelectivityStatus.NO_QUALIFYING_POLICY,
            calibration_policy_id=calibration.policy_id,
            horizon_length=calibration.horizon_length,
            development_through_session=calibration.development_through_session,
            candidates=candidates,
            selected_policy=None,
            selected_coverage=None,
            selected_precision=None,
        )
    selected = max(
        qualifying,
        key=lambda item: (
            item.coverage,
            item.precision if item.precision is not None else -1.0,
            item.policy.min_top_probability,
            item.policy.min_separation,
        ),
    )
    return ScenarioSelectivityEvaluation(
        status=ScenarioSelectivityStatus.QUALIFYING_POLICY,
        calibration_policy_id=calibration.policy_id,
        horizon_length=calibration.horizon_length,
        development_through_session=calibration.development_through_session,
        candidates=candidates,
        selected_policy=selected.policy,
        selected_coverage=selected.coverage,
        selected_precision=selected.precision,
    )


def select_scenario_from_probabilities(
    row: tuple[ScenarioProbability, ...],
    policy: ScenarioSelectivityPolicy | None,
) -> ScenarioOutcome | None:
    if policy is None:
        return None
    by_outcome = {item.outcome: item.probability for item in row}
    if set(by_outcome) != set(ScenarioOutcome) or len(row) != len(ScenarioOutcome):
        raise ValueError("probability row must contain all three scenarios exactly once.")
    ranked = sorted(
        by_outcome.items(),
        key=lambda item: (-item[1], tuple(ScenarioOutcome).index(item[0])),
    )
    top, second = ranked[0], ranked[1]
    if top[1] < policy.min_top_probability or top[1] - second[1] < policy.min_separation:
        return None
    return top[0]


def _evaluate_candidate(
    outcomes: tuple[ScenarioOutcome, ...],
    probability_rows: tuple[tuple[ScenarioProbability, ...], ...],
    top_threshold: float,
    separation_threshold: float,
) -> ScenarioSelectivityCandidate:
    policy = ScenarioSelectivityPolicy(
        policy_id=MI1F_SELECTIVITY_POLICY_ID,
        min_top_probability=top_threshold,
        min_separation=separation_threshold,
    )
    selected = 0
    correct = 0
    for outcome, row in zip(outcomes, probability_rows, strict=True):
        selected_outcome = select_scenario_from_probabilities(row, policy)
        if selected_outcome is None:
            continue
        selected += 1
        if selected_outcome == outcome:
            correct += 1
    precision = correct / selected if selected else None
    return ScenarioSelectivityCandidate(
        policy=policy,
        total_rows=len(outcomes),
        selected_rows=selected,
        correct_selected_rows=correct,
        coverage=selected / len(outcomes),
        precision=precision,
        qualifies=(
            selected >= MI1F_MINIMUM_SELECTED_ROWS
            and precision is not None
            and precision >= MI1F_TARGET_PRECISION
        ),
    )
