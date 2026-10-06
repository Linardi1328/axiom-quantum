from __future__ import annotations

import inspect
from datetime import UTC, date, datetime, time

import pandas as pd
import pytest

from spy_market_agent.intelligence import AnalysisHorizon, HorizonUnit, ScenarioOutcome
from spy_market_agent.market_data.calendar import XNYSCalendar
from spy_market_agent.market_data.models import CANONICAL_COLUMNS, MarketDataBatch
from spy_market_agent.research import scenario_labels as scenario_labels_module
from spy_market_agent.research.scenario_labels import (
    MI1B_5_SESSION_RANGE_BAND,
    MI1B_20_SESSION_RANGE_BAND,
    ScenarioBaselineKind,
    ScenarioLabelSet,
    build_spy_scenario_label_set,
    classify_scenario_return,
    fit_naive_scenario_baseline,
)
from spy_market_agent.validation.market_data_checks import validate_daily_spy_data

FIVE_SESSIONS = AnalysisHorizon(unit=HorizonUnit.SESSIONS, length=5)
TWENTY_SESSIONS = AnalysisHorizon(unit=HorizonUnit.SESSIONS, length=20)


def _sessions(row_count: int) -> list[date]:
    calendar = XNYSCalendar()
    return list(calendar.sessions_between(date(2024, 1, 2), date(2024, 5, 31)))[:row_count]


def _frame(row_count: int) -> pd.DataFrame:
    sessions = _sessions(row_count)
    closes = [100.0 * (1.003**index) for index in range(row_count)]
    opens = [close - 0.20 for close in closes]
    highs = [close + 0.75 for close in closes]
    lows = [open_ - 0.65 for open_ in opens]
    return pd.DataFrame(
        {
            "session": sessions,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": [1_000_000 + index * 10_000 for index in range(row_count)],
        },
        columns=list(CANONICAL_COLUMNS),
    )


def _timestamp(session: date, hour: int) -> datetime:
    return datetime.combine(session, time(hour=hour), tzinfo=UTC)


def _batch(frame: pd.DataFrame) -> MarketDataBatch:
    last_session = frame.iloc[-1]["session"]
    return validate_daily_spy_data(
        frame,
        provider_name="mi1b-scenario-label-fixture",
        downloaded_at=_timestamp(last_session, 21),
        created_at=_timestamp(last_session, 22),
        as_of=_timestamp(last_session, 23),
        calendar=XNYSCalendar(),
        source_description="deterministic MI-1B scenario-label fixture",
    )


def _labels(batch: MarketDataBatch, horizon: AnalysisHorizon) -> ScenarioLabelSet:
    return build_spy_scenario_label_set(
        batch,
        horizon=horizon,
        created_at=_timestamp(batch.metadata.last_session, 23),
    )


def test_frozen_scenario_bands_and_boundaries() -> None:
    assert MI1B_5_SESSION_RANGE_BAND == 0.01
    assert MI1B_20_SESSION_RANGE_BAND == 0.02

    assert classify_scenario_return(-0.0100001, horizon=FIVE_SESSIONS) == ScenarioOutcome.DOWNSIDE
    assert classify_scenario_return(-0.01, horizon=FIVE_SESSIONS) == ScenarioOutcome.RANGE
    assert classify_scenario_return(0.01, horizon=FIVE_SESSIONS) == ScenarioOutcome.RANGE
    assert classify_scenario_return(0.0100001, horizon=FIVE_SESSIONS) == ScenarioOutcome.UPSIDE

    assert classify_scenario_return(-0.0200001, horizon=TWENTY_SESSIONS) == ScenarioOutcome.DOWNSIDE
    assert classify_scenario_return(-0.02, horizon=TWENTY_SESSIONS) == ScenarioOutcome.RANGE
    assert classify_scenario_return(0.02, horizon=TWENTY_SESSIONS) == ScenarioOutcome.RANGE
    assert classify_scenario_return(0.0200001, horizon=TWENTY_SESSIONS) == ScenarioOutcome.UPSIDE


def test_builds_exact_close_to_close_5_session_labels() -> None:
    batch = _batch(_frame(30))
    label_set = _labels(batch, FIVE_SESSIONS)
    first = label_set.labels[0]

    expected_return = float(batch.data.iloc[5]["close"]) / float(batch.data.iloc[0]["close"]) - 1.0
    assert first.anchor_session == batch.data.iloc[0]["session"]
    assert first.outcome_session == batch.data.iloc[5]["session"]
    assert first.forward_return == pytest.approx(expected_return)
    assert first.outcome == classify_scenario_return(expected_return, horizon=FIVE_SESSIONS)
    assert label_set.source_rows_excluded_after_horizon == 5
    assert len(label_set.labels) == len(batch.data) - 5
    assert label_set.source_market_data_checksum == batch.metadata.dataset_checksum


def test_builds_exact_close_to_close_20_session_labels() -> None:
    batch = _batch(_frame(35))
    label_set = _labels(batch, TWENTY_SESSIONS)
    first = label_set.labels[0]

    expected_return = float(batch.data.iloc[20]["close"]) / float(batch.data.iloc[0]["close"]) - 1.0
    assert first.outcome_session == batch.data.iloc[20]["session"]
    assert first.forward_return == pytest.approx(expected_return)
    assert label_set.source_rows_excluded_after_horizon == 20
    assert len(label_set.labels) == len(batch.data) - 20


def test_labels_depend_only_on_adjusted_closes() -> None:
    original = _frame(30)
    changed = original.copy(deep=True)
    changed["open"] = changed["close"] - 5.0
    changed["high"] = changed["close"] + 7.0
    changed["low"] = changed["open"] - 4.0
    changed["volume"] += 9_000_000

    original_labels = _labels(_batch(original), FIVE_SESSIONS)
    changed_labels = _labels(_batch(changed), FIVE_SESSIONS)

    assert [label.forward_return for label in original_labels.labels] == pytest.approx(
        [label.forward_return for label in changed_labels.labels]
    )
    assert [label.outcome for label in original_labels.labels] == [
        label.outcome for label in changed_labels.labels
    ]


def test_label_builder_does_not_mutate_source_market_data() -> None:
    batch = _batch(_frame(30))
    original = batch.data.copy(deep=True)

    _labels(batch, FIVE_SESSIONS)

    pd.testing.assert_frame_equal(batch.data, original)


def test_rejects_unsupported_horizon_and_insufficient_history() -> None:
    batch = _batch(_frame(30))
    unsupported = AnalysisHorizon(unit=HorizonUnit.SESSIONS, length=10)

    with pytest.raises(ValueError, match="supports only 5-session and 20-session"):
        build_spy_scenario_label_set(
            batch,
            horizon=unsupported,
            created_at=_timestamp(batch.metadata.last_session, 23),
        )

    short_batch = _batch(_frame(5))
    with pytest.raises(ValueError, match="more rows than the scenario horizon"):
        _labels(short_batch, FIVE_SESSIONS)


def test_rejects_non_finite_return_and_pre_source_creation_timestamp() -> None:
    with pytest.raises(ValueError, match="forward_return must be finite"):
        classify_scenario_return(float("nan"), horizon=FIVE_SESSIONS)

    batch = _batch(_frame(30))
    with pytest.raises(ValueError, match="must not precede"):
        build_spy_scenario_label_set(
            batch,
            horizon=FIVE_SESSIONS,
            created_at=_timestamp(batch.metadata.last_session, 21),
        )


def test_uniform_baseline_is_deterministic() -> None:
    label_set = _labels(_batch(_frame(30)), FIVE_SESSIONS)
    cutoff = label_set.labels[12].outcome_session

    baseline = fit_naive_scenario_baseline(
        label_set,
        baseline_kind=ScenarioBaselineKind.UNIFORM,
        fit_through_session=cutoff,
    )

    assert baseline.fit_row_count == 13
    for outcome in ScenarioOutcome:
        assert baseline.probability_for(outcome) == pytest.approx(1.0 / 3.0)


def test_empirical_prior_uses_only_outcomes_available_by_cutoff() -> None:
    frame = _frame(30)
    closes = frame["close"].copy()
    closes.iloc[5] = closes.iloc[0] * 0.98
    closes.iloc[6] = closes.iloc[1] * 1.00
    closes.iloc[7] = closes.iloc[2] * 1.03
    frame["close"] = closes
    frame["high"] = frame[["open", "close"]].max(axis=1) + 0.75
    frame["low"] = frame[["open", "close"]].min(axis=1) - 0.65
    label_set = _labels(_batch(frame), FIVE_SESSIONS)
    cutoff = label_set.labels[2].outcome_session

    baseline = fit_naive_scenario_baseline(
        label_set,
        baseline_kind=ScenarioBaselineKind.EMPIRICAL_PRIOR,
        fit_through_session=cutoff,
    )

    expected = [label.outcome for label in label_set.labels[:3]]
    assert baseline.fit_row_count == 3
    for outcome in ScenarioOutcome:
        assert baseline.probability_for(outcome) == pytest.approx(expected.count(outcome) / 3)
    assert all(
        label.outcome_session <= cutoff for label in label_set.labels[: baseline.fit_row_count]
    )
    assert label_set.labels[baseline.fit_row_count].anchor_session <= cutoff
    assert label_set.labels[baseline.fit_row_count].outcome_session > cutoff


def test_majority_baseline_is_one_hot_with_canonical_tie_break() -> None:
    frame = _frame(30)
    closes = frame["close"].copy()
    closes.iloc[5] = closes.iloc[0] * 0.98
    closes.iloc[6] = closes.iloc[1] * 1.02
    frame["close"] = closes
    frame["high"] = frame[["open", "close"]].max(axis=1) + 0.75
    frame["low"] = frame[["open", "close"]].min(axis=1) - 0.65
    label_set = _labels(_batch(frame), FIVE_SESSIONS)
    cutoff = label_set.labels[1].outcome_session

    baseline = fit_naive_scenario_baseline(
        label_set,
        baseline_kind=ScenarioBaselineKind.MAJORITY_CLASS,
        fit_through_session=cutoff,
    )

    assert baseline.fit_row_count == 2
    assert baseline.probability_for(ScenarioOutcome.DOWNSIDE) == 1.0
    assert baseline.probability_for(ScenarioOutcome.RANGE) == 0.0
    assert baseline.probability_for(ScenarioOutcome.UPSIDE) == 0.0


def test_baseline_rejects_cutoff_before_any_outcome_is_observable() -> None:
    label_set = _labels(_batch(_frame(30)), FIVE_SESSIONS)

    with pytest.raises(ValueError, match="does not include any observable"):
        fit_naive_scenario_baseline(
            label_set,
            baseline_kind=ScenarioBaselineKind.EMPIRICAL_PRIOR,
            fit_through_session=label_set.labels[0].anchor_session,
        )


def test_scenario_label_module_is_execution_isolated() -> None:
    source = inspect.getsource(scenario_labels_module)
    forbidden = ("alpaca", "paper_ops", "execution.service", "broker")
    for token in forbidden:
        assert token not in source.lower()
