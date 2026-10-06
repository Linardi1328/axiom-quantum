from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Any, cast

from spy_market_agent.features.models import FeatureSet
from spy_market_agent.intelligence.scenarios import ScenarioOutcome, ScenarioProbability
from spy_market_agent.research.scenario_calibration import (
    ScenarioCalibrationEvaluation,
    calculate_multiclass_ece,
)
from spy_market_agent.research.scenario_candidate import MI1D_FEATURE_COLUMNS
from spy_market_agent.research.scenario_evaluation import (
    ScenarioEvaluationMetrics,
    calculate_scenario_probability_metrics,
)
from spy_market_agent.research.scenario_labels import ScenarioLabelSet

MI1G_ANALOGUE_POLICY_ID = "mi1g-standardized-euclidean-causal-v1"
MI1G_REGIME_POLICY_ID = "mi1g-trend-volatility-regime-v1"
MI1G_REGIME_TRAILING_WINDOW = 252
MI1G_MINIMUM_REGIME_HISTORY = 63
MI1G_MINIMUM_REGIME_EVALUATION_ROWS = 20


class SPYRegime(StrEnum):
    POSITIVE_TREND_LOW_VOL = "positive_trend_low_vol"
    POSITIVE_TREND_HIGH_VOL = "positive_trend_high_vol"
    NEGATIVE_TREND_LOW_VOL = "negative_trend_low_vol"
    NEGATIVE_TREND_HIGH_VOL = "negative_trend_high_vol"


@dataclass(frozen=True, slots=True)
class HistoricalAnalogue:
    anchor_session: date
    outcome_session: date
    distance: float
    outcome: ScenarioOutcome
    forward_return: float

    def __post_init__(self) -> None:
        if self.anchor_session >= self.outcome_session:
            raise ValueError("analogue anchor must precede outcome session.")
        for field_name in ("distance", "forward_return"):
            value = float(getattr(self, field_name))
            if not math.isfinite(value):
                raise ValueError(f"{field_name} must be finite.")
            object.__setattr__(self, field_name, value)
        if self.distance < 0.0:
            raise ValueError("distance must be non-negative.")


@dataclass(frozen=True, slots=True)
class HistoricalAnalogueSummary:
    policy_id: str
    query_anchor_session: date
    horizon_length: int
    feature_columns: tuple[str, ...]
    candidate_history_rows: int
    analogues: tuple[HistoricalAnalogue, ...]
    downside_count: int
    range_count: int
    upside_count: int
    mean_forward_return: float
    median_forward_return: float

    def __post_init__(self) -> None:
        if self.policy_id != MI1G_ANALOGUE_POLICY_ID:
            raise ValueError("policy_id must match MI-1G analogue policy.")
        if self.horizon_length not in {5, 20}:
            raise ValueError("horizon_length must be 5 or 20.")
        if self.feature_columns != MI1D_FEATURE_COLUMNS:
            raise ValueError("analogue search must use the frozen MI-1D feature vector.")
        if self.candidate_history_rows < len(self.analogues):
            raise ValueError("candidate_history_rows must cover selected analogues.")
        counts = {
            ScenarioOutcome.DOWNSIDE: self.downside_count,
            ScenarioOutcome.RANGE: self.range_count,
            ScenarioOutcome.UPSIDE: self.upside_count,
        }
        if sum(counts.values()) != len(self.analogues):
            raise ValueError("analogue outcome counts must match analogue count.")
        if any(item.anchor_session >= self.query_anchor_session for item in self.analogues):
            raise ValueError("all analogues must precede the query anchor.")
        if any(item.outcome_session > self.query_anchor_session for item in self.analogues):
            raise ValueError("analogue outcomes must be observable by query time.")
        for field_name in ("mean_forward_return", "median_forward_return"):
            value = float(getattr(self, field_name))
            if not math.isfinite(value):
                raise ValueError(f"{field_name} must be finite.")
            object.__setattr__(self, field_name, value)


@dataclass(frozen=True, slots=True)
class RegimeEvaluation:
    regime: SPYRegime
    row_count: int
    metrics: ScenarioEvaluationMetrics
    ece: float

    def __post_init__(self) -> None:
        if self.row_count != self.metrics.row_count:
            raise ValueError("row_count must match regime metrics.")
        value = float(self.ece)
        if not math.isfinite(value) or not 0.0 <= value <= 1.0:
            raise ValueError("ece must lie in [0, 1].")
        object.__setattr__(self, "ece", value)


@dataclass(frozen=True, slots=True)
class RegimeRobustnessEvaluation:
    policy_id: str
    horizon_length: int
    regimes: tuple[RegimeEvaluation, ...]
    omitted_small_regimes: tuple[SPYRegime, ...]

    def __post_init__(self) -> None:
        if self.policy_id != MI1G_REGIME_POLICY_ID:
            raise ValueError("policy_id must match MI-1G regime policy.")
        seen = tuple(item.regime for item in self.regimes)
        if len(seen) != len(set(seen)):
            raise ValueError("regime evaluations must be unique.")
        if set(seen).intersection(self.omitted_small_regimes):
            raise ValueError("a regime cannot be both evaluated and omitted.")


def find_historical_analogues(
    feature_set: FeatureSet,
    label_set: ScenarioLabelSet,
    *,
    query_anchor_session: date,
    top_k: int = 10,
) -> HistoricalAnalogueSummary:
    if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k <= 0:
        raise ValueError("top_k must be a positive integer.")
    if feature_set.source_market_data_checksum != label_set.source_market_data_checksum:
        raise ValueError("feature and label source checksums must match.")
    records = cast(list[dict[str, Any]], feature_set.data.to_dict(orient="records"))
    sessions = tuple(record["session"] for record in records)
    if query_anchor_session not in sessions:
        raise ValueError("query anchor must exist in the feature set.")
    query_index = sessions.index(query_anchor_session)
    horizon = label_set.horizon.length
    label_by_anchor = {label.anchor_session: label for label in label_set.labels}
    eligible_indexes = tuple(
        index
        for index in range(0, query_index - horizon + 1)
        if sessions[index] in label_by_anchor
        and label_by_anchor[sessions[index]].outcome_session <= query_anchor_session
    )
    if not eligible_indexes:
        raise ValueError("no causal analogue history is available for the query anchor.")
    means, scales = _standardization(records, eligible_indexes)
    query_vector = tuple(float(records[query_index][column]) for column in MI1D_FEATURE_COLUMNS)
    ranked = sorted(
        (
            (_distance(query_vector, _vector(records[index]), means, scales), index)
            for index in eligible_indexes
        ),
        key=lambda item: (item[0], sessions[item[1]]),
    )
    selected: list[tuple[float, int]] = []
    for distance, index in ranked:
        if any(abs(index - prior_index) < horizon for _, prior_index in selected):
            continue
        selected.append((distance, index))
        if len(selected) == top_k:
            break
    if not selected:
        raise ValueError("analogue spacing removed all eligible candidates.")
    analogues = tuple(
        HistoricalAnalogue(
            anchor_session=sessions[index],
            outcome_session=label_by_anchor[sessions[index]].outcome_session,
            distance=distance,
            outcome=label_by_anchor[sessions[index]].outcome,
            forward_return=label_by_anchor[sessions[index]].forward_return,
        )
        for distance, index in selected
    )
    returns = tuple(item.forward_return for item in analogues)
    counts = dict.fromkeys(ScenarioOutcome, 0)
    for item in analogues:
        counts[item.outcome] += 1
    return HistoricalAnalogueSummary(
        policy_id=MI1G_ANALOGUE_POLICY_ID,
        query_anchor_session=query_anchor_session,
        horizon_length=horizon,
        feature_columns=MI1D_FEATURE_COLUMNS,
        candidate_history_rows=len(eligible_indexes),
        analogues=analogues,
        downside_count=counts[ScenarioOutcome.DOWNSIDE],
        range_count=counts[ScenarioOutcome.RANGE],
        upside_count=counts[ScenarioOutcome.UPSIDE],
        mean_forward_return=statistics.fmean(returns),
        median_forward_return=statistics.median(returns),
    )


def classify_spy_regime(feature_set: FeatureSet, *, anchor_session: date) -> SPYRegime:
    records = cast(list[dict[str, Any]], feature_set.data.to_dict(orient="records"))
    sessions = tuple(record["session"] for record in records)
    if anchor_session not in sessions:
        raise ValueError("anchor_session must exist in the feature set.")
    index = sessions.index(anchor_session)
    if index < MI1G_MINIMUM_REGIME_HISTORY:
        raise ValueError("insufficient prior history for causal volatility regime classification.")
    start = max(0, index - MI1G_REGIME_TRAILING_WINDOW)
    prior_volatility = tuple(
        float(records[position]["realized_volatility_20"]) for position in range(start, index)
    )
    threshold = statistics.median(prior_volatility)
    current_volatility = float(records[index]["realized_volatility_20"])
    current_trend = float(records[index]["close_return_20d"])
    positive = current_trend >= 0.0
    high_vol = current_volatility > threshold
    if positive and not high_vol:
        return SPYRegime.POSITIVE_TREND_LOW_VOL
    if positive and high_vol:
        return SPYRegime.POSITIVE_TREND_HIGH_VOL
    if not positive and not high_vol:
        return SPYRegime.NEGATIVE_TREND_LOW_VOL
    return SPYRegime.NEGATIVE_TREND_HIGH_VOL


def evaluate_calibrated_regime_robustness(
    feature_set: FeatureSet,
    calibration: ScenarioCalibrationEvaluation,
) -> RegimeRobustnessEvaluation:
    grouped_outcomes: dict[SPYRegime, list[ScenarioOutcome]] = {regime: [] for regime in SPYRegime}
    grouped_rows: dict[SPYRegime, list[tuple[ScenarioProbability, ...]]] = {
        regime: [] for regime in SPYRegime
    }
    for fold in calibration.folds:
        for anchor, outcome, probability_row in zip(
            fold.assessment_anchor_sessions,
            fold.assessment_outcomes,
            fold.calibrated_probability_rows,
            strict=True,
        ):
            regime = classify_spy_regime(feature_set, anchor_session=anchor)
            grouped_outcomes[regime].append(outcome)
            grouped_rows[regime].append(probability_row)
    evaluations: list[RegimeEvaluation] = []
    omitted: list[SPYRegime] = []
    for regime in SPYRegime:
        outcomes = tuple(grouped_outcomes[regime])
        rows = tuple(grouped_rows[regime])
        if len(outcomes) < MI1G_MINIMUM_REGIME_EVALUATION_ROWS:
            omitted.append(regime)
            continue
        evaluations.append(
            RegimeEvaluation(
                regime=regime,
                row_count=len(outcomes),
                metrics=calculate_scenario_probability_metrics(outcomes, rows),
                ece=calculate_multiclass_ece(outcomes, rows),
            )
        )
    return RegimeRobustnessEvaluation(
        policy_id=MI1G_REGIME_POLICY_ID,
        horizon_length=calibration.horizon_length,
        regimes=tuple(evaluations),
        omitted_small_regimes=tuple(omitted),
    )


def _vector(record: dict[str, Any]) -> tuple[float, ...]:
    return tuple(float(record[column]) for column in MI1D_FEATURE_COLUMNS)


def _standardization(
    records: list[dict[str, Any]],
    indexes: tuple[int, ...],
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    columns = tuple(
        tuple(float(records[index][column]) for index in indexes) for column in MI1D_FEATURE_COLUMNS
    )
    means = tuple(statistics.fmean(values) for values in columns)
    scales = tuple(statistics.pstdev(values) or 1.0 for values in columns)
    return means, scales


def _distance(
    left: tuple[float, ...],
    right: tuple[float, ...],
    means: tuple[float, ...],
    scales: tuple[float, ...],
) -> float:
    return math.sqrt(
        sum(
            (
                (left[index] - means[index]) / scales[index]
                - (right[index] - means[index]) / scales[index]
            )
            ** 2
            for index in range(len(MI1D_FEATURE_COLUMNS))
        )
    )
