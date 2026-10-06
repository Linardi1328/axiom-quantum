from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from spy_market_agent.intelligence.contracts import AnalysisHorizon, HorizonUnit
from spy_market_agent.intelligence.profiles import (
    MI1_SPY_ANALYSIS_PROFILE,
    MI1_SPY_SCENARIO_SCHEMA_ID,
)
from spy_market_agent.intelligence.scenarios import ScenarioOutcome, ScenarioProbability
from spy_market_agent.market_data.models import MarketDataBatch, require_utc_datetime

MI1B_5_SESSION_RANGE_BAND = 0.01
MI1B_20_SESSION_RANGE_BAND = 0.02

_RANGE_BANDS: dict[int, float] = {
    5: MI1B_5_SESSION_RANGE_BAND,
    20: MI1B_20_SESSION_RANGE_BAND,
}


class ScenarioBaselineKind(StrEnum):
    UNIFORM = "uniform"
    EMPIRICAL_PRIOR = "empirical_prior"
    MAJORITY_CLASS = "majority_class"


@dataclass(frozen=True, slots=True)
class ScenarioLabel:
    anchor_session: date
    outcome_session: date
    horizon: AnalysisHorizon
    forward_return: float
    outcome: ScenarioOutcome

    def __post_init__(self) -> None:
        if not isinstance(self.horizon, AnalysisHorizon):
            raise ValueError("horizon must be an AnalysisHorizon.")
        if not isinstance(self.outcome, ScenarioOutcome):
            raise ValueError("outcome must be a ScenarioOutcome.")
        if self.anchor_session >= self.outcome_session:
            raise ValueError("anchor_session must be before outcome_session.")
        value = float(self.forward_return)
        if not math.isfinite(value):
            raise ValueError("forward_return must be finite.")
        object.__setattr__(self, "forward_return", value)


@dataclass(frozen=True, slots=True)
class ScenarioLabelSet:
    horizon: AnalysisHorizon
    range_band: float
    labels: tuple[ScenarioLabel, ...]
    source_market_data_checksum: str
    source_schema_version: str
    scenario_schema_id: str
    source_rows_excluded_after_horizon: int
    created_at: datetime

    def __post_init__(self) -> None:
        _validate_supported_horizon(self.horizon)
        expected_band = _range_band_for_horizon(self.horizon)
        if not math.isclose(float(self.range_band), expected_band, rel_tol=0.0, abs_tol=0.0):
            raise ValueError("range_band must match the frozen MI-1B horizon policy.")
        if not self.labels:
            raise ValueError("labels must not be empty.")
        if self.scenario_schema_id != MI1_SPY_SCENARIO_SCHEMA_ID:
            raise ValueError("scenario_schema_id must match the MI-1 SPY scenario schema.")
        if self.source_rows_excluded_after_horizon != self.horizon.length:
            raise ValueError("source_rows_excluded_after_horizon must equal horizon length.")
        if len(self.source_market_data_checksum) != 64 or any(
            character not in "0123456789abcdef" for character in self.source_market_data_checksum
        ):
            raise ValueError("source_market_data_checksum must be a lowercase SHA-256 digest.")

        anchors = tuple(label.anchor_session for label in self.labels)
        if anchors != tuple(sorted(anchors)) or len(set(anchors)) != len(anchors):
            raise ValueError("labels must have unique strictly increasing anchor sessions.")
        if any(label.horizon != self.horizon for label in self.labels):
            raise ValueError("all labels must use the label-set horizon.")
        object.__setattr__(
            self,
            "created_at",
            require_utc_datetime(self.created_at, field_name="created_at"),
        )


@dataclass(frozen=True, slots=True)
class ScenarioBaseline:
    baseline_kind: ScenarioBaselineKind
    horizon: AnalysisHorizon
    probabilities: tuple[ScenarioProbability, ...]
    fit_through_session: date
    fit_row_count: int
    fit_first_anchor_session: date
    fit_last_anchor_session: date
    fit_last_outcome_session: date

    def __post_init__(self) -> None:
        if not isinstance(self.baseline_kind, ScenarioBaselineKind):
            raise ValueError("baseline_kind must be a ScenarioBaselineKind.")
        _validate_supported_horizon(self.horizon)
        if self.fit_row_count <= 0:
            raise ValueError("fit_row_count must be positive.")
        if self.fit_first_anchor_session > self.fit_last_anchor_session:
            raise ValueError("fit anchor-session bounds are invalid.")
        if self.fit_last_outcome_session > self.fit_through_session:
            raise ValueError("baseline fit must not use outcomes after fit_through_session.")
        by_outcome = {item.outcome: item for item in self.probabilities}
        if set(by_outcome) != set(ScenarioOutcome) or len(self.probabilities) != len(
            ScenarioOutcome
        ):
            raise ValueError("baseline probabilities must contain all three scenario outcomes.")
        total = sum(item.probability for item in self.probabilities)
        if not math.isclose(total, 1.0, rel_tol=0.0, abs_tol=1e-12):
            raise ValueError("baseline probabilities must sum to 1.0.")
        object.__setattr__(
            self,
            "probabilities",
            tuple(by_outcome[outcome] for outcome in ScenarioOutcome),
        )

    def probability_for(self, outcome: ScenarioOutcome) -> float:
        return next(item.probability for item in self.probabilities if item.outcome == outcome)


def build_spy_scenario_label_set(
    market_data: MarketDataBatch,
    *,
    horizon: AnalysisHorizon,
    created_at: datetime,
) -> ScenarioLabelSet:
    """Build offline SPY scenario labels from anchor close to horizon close."""

    _validate_supported_horizon(horizon)
    normalized_created_at = require_utc_datetime(created_at, field_name="created_at")
    if normalized_created_at < market_data.metadata.created_at:
        raise ValueError("created_at must not precede the source market-data artifact.")
    if len(market_data.data) <= horizon.length:
        raise ValueError("source history must contain more rows than the scenario horizon.")

    source = market_data.data.copy(deep=True).reset_index(drop=True)
    labels: list[ScenarioLabel] = []
    for index in range(len(source) - horizon.length):
        outcome_index = index + horizon.length
        anchor_close = float(source.at[index, "close"])
        outcome_close = float(source.at[outcome_index, "close"])
        forward_return = outcome_close / anchor_close - 1.0
        labels.append(
            ScenarioLabel(
                anchor_session=source.at[index, "session"],
                outcome_session=source.at[outcome_index, "session"],
                horizon=horizon,
                forward_return=forward_return,
                outcome=classify_scenario_return(forward_return, horizon=horizon),
            )
        )

    return ScenarioLabelSet(
        horizon=horizon,
        range_band=_range_band_for_horizon(horizon),
        labels=tuple(labels),
        source_market_data_checksum=market_data.metadata.dataset_checksum,
        source_schema_version=market_data.metadata.schema_version,
        scenario_schema_id=MI1_SPY_SCENARIO_SCHEMA_ID,
        source_rows_excluded_after_horizon=horizon.length,
        created_at=normalized_created_at,
    )


def classify_scenario_return(
    forward_return: float,
    *,
    horizon: AnalysisHorizon,
) -> ScenarioOutcome:
    """Classify one finite forward return under the frozen MI-1B version-1 bands."""

    _validate_supported_horizon(horizon)
    value = float(forward_return)
    if not math.isfinite(value):
        raise ValueError("forward_return must be finite.")
    band = _range_band_for_horizon(horizon)
    if value < -band:
        return ScenarioOutcome.DOWNSIDE
    if value > band:
        return ScenarioOutcome.UPSIDE
    return ScenarioOutcome.RANGE


def fit_naive_scenario_baseline(
    label_set: ScenarioLabelSet,
    *,
    baseline_kind: ScenarioBaselineKind,
    fit_through_session: date,
) -> ScenarioBaseline:
    """Fit a naive multiclass baseline using only outcomes observable by the cutoff."""

    if not isinstance(baseline_kind, ScenarioBaselineKind):
        raise ValueError("baseline_kind must be a ScenarioBaselineKind.")
    eligible = tuple(
        label for label in label_set.labels if label.outcome_session <= fit_through_session
    )
    if not eligible:
        raise ValueError("fit_through_session does not include any observable scenario labels.")

    counts = dict.fromkeys(ScenarioOutcome, 0)
    for label in eligible:
        counts[label.outcome] += 1

    if baseline_kind == ScenarioBaselineKind.UNIFORM:
        probabilities = {outcome: 1.0 / len(ScenarioOutcome) for outcome in ScenarioOutcome}
    elif baseline_kind == ScenarioBaselineKind.EMPIRICAL_PRIOR:
        probabilities = {outcome: counts[outcome] / len(eligible) for outcome in ScenarioOutcome}
    else:
        outcome_order = tuple(ScenarioOutcome)
        majority = max(
            outcome_order,
            key=lambda outcome: (counts[outcome], -outcome_order.index(outcome)),
        )
        probabilities = {
            outcome: 1.0 if outcome == majority else 0.0 for outcome in ScenarioOutcome
        }

    return ScenarioBaseline(
        baseline_kind=baseline_kind,
        horizon=label_set.horizon,
        probabilities=tuple(
            ScenarioProbability(outcome=outcome, probability=probabilities[outcome])
            for outcome in ScenarioOutcome
        ),
        fit_through_session=fit_through_session,
        fit_row_count=len(eligible),
        fit_first_anchor_session=eligible[0].anchor_session,
        fit_last_anchor_session=eligible[-1].anchor_session,
        fit_last_outcome_session=eligible[-1].outcome_session,
    )


def _range_band_for_horizon(horizon: AnalysisHorizon) -> float:
    _validate_supported_horizon(horizon)
    return _RANGE_BANDS[horizon.length]


def _validate_supported_horizon(horizon: AnalysisHorizon) -> None:
    if not isinstance(horizon, AnalysisHorizon):
        raise ValueError("horizon must be an AnalysisHorizon.")
    if horizon.unit != HorizonUnit.SESSIONS or horizon.length not in _RANGE_BANDS:
        raise ValueError("MI-1B supports only 5-session and 20-session horizons.")
    if horizon not in MI1_SPY_ANALYSIS_PROFILE.horizons:
        raise ValueError("horizon must belong to the MI-1 SPY analysis profile.")
