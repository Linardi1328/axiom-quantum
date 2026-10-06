from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import pandas as pd

from spy_market_agent.benchmark.locks import (
    ClassificationMetricSet,
    RegimeCellDiagnostics,
    RegimeDiagnostics,
    RegimePolicy,
    StrategyMetricSet,
)
from spy_market_agent.benchmark.metrics import classification_metric_set
from spy_market_agent.benchmark.strategies import regime_strategy_metric_set


@dataclass(frozen=True, slots=True)
class RegimeFrame:
    data: pd.DataFrame
    volatility_threshold: Decimal


def training_volatility_threshold(
    market_data: pd.DataFrame,
    *,
    training_sessions: Iterable[date],
) -> Decimal:
    labels = regime_frame(market_data, volatility_threshold=None)
    training_set = set(training_sessions)
    values = [
        Decimal(str(value))
        for value in labels.data.loc[
            labels.data["session"].isin(training_set), "realized_volatility_20_value"
        ].to_list()
        if pd.notna(value) and math.isfinite(float(value))
    ]
    if not values:
        return Decimal("0")
    values = sorted(values)
    midpoint = len(values) // 2
    if len(values) % 2:
        return values[midpoint]
    return (values[midpoint - 1] + values[midpoint]) / Decimal("2")


def regime_frame(
    market_data: pd.DataFrame,
    *,
    volatility_threshold: Decimal | None,
) -> RegimeFrame:
    frame = market_data.loc[:, ["session", "close"]].copy(deep=True).reset_index(drop=True)
    close = frame["close"].astype("float64")
    trend_mean = close.rolling(window=200, min_periods=200, center=False).mean()
    log_returns = (close / close.shift(1)).map(math.log)
    realized_volatility = log_returns.rolling(window=20, min_periods=20, center=False).std(
        ddof=1
    ) * math.sqrt(252)
    running_peak = close.cummax()
    drawdown = close / running_peak - 1.0
    threshold = volatility_threshold
    if threshold is None:
        threshold = Decimal("0")
    threshold_float = float(threshold)
    frame["trend_200"] = [
        "unavailable" if pd.isna(mean) else ("bull" if price >= mean else "bear")
        for price, mean in zip(close.to_list(), trend_mean.to_list(), strict=True)
    ]
    frame["realized_volatility_20_value"] = realized_volatility.astype("float64")
    frame["realized_volatility_20"] = [
        (
            "unavailable"
            if pd.isna(value)
            else ("high_volatility" if float(value) > threshold_float else "lower_volatility")
        )
        for value in realized_volatility.to_list()
    ]
    frame["drawdown_10"] = ["drawdown" if float(value) <= -0.10 else "normal" for value in drawdown]
    frame["drawdown_10_value"] = drawdown.astype("float64")
    frame["calendar_year"] = [str(session.year) for session in frame["session"].to_list()]
    return RegimeFrame(data=frame, volatility_threshold=threshold)


def regime_policy_summary(policy: RegimePolicy) -> dict[str, object]:
    return {
        "trend_200": policy.trend_200,
        "realized_volatility_20": policy.realized_volatility_20,
        "drawdown_10": policy.drawdown_10,
        "calendar_year": policy.calendar_year,
        "volatility_threshold": policy.volatility_threshold,
        "strategy_attribution_rule": policy.strategy_attribution_rule,
        "small_sample_warning_threshold": policy.small_sample_warning_threshold,
    }


def regime_counts(
    *,
    labels: pd.DataFrame,
    regimes: pd.DataFrame,
    sessions: Iterable[date],
    small_sample_threshold: int = 40,
) -> dict[str, dict[str, dict[str, int | str]]]:
    session_set = set(sessions)
    target_by_session = labels.set_index("session")["target"].to_dict()
    joined = regimes[regimes["session"].isin(session_set)].copy(deep=True)
    result: dict[str, dict[str, dict[str, int | str]]] = {}
    for regime_name in ("trend_200", "realized_volatility_20", "drawdown_10", "calendar_year"):
        cells: dict[str, dict[str, int | str]] = {}
        for value, group in joined.groupby(regime_name, sort=True):
            targets = [int(target_by_session[session]) for session in group["session"].to_list()]
            positive = sum(targets)
            total = len(targets)
            warning = "small_sample" if total < small_sample_threshold else ""
            cells[str(value)] = {
                "sample_size": total,
                "positive_count": positive,
                "negative_count": total - positive,
                "warning": warning,
            }
        result[regime_name] = cells
    return result


def regime_diagnostics(
    *,
    benchmark_id: str,
    dataset_id: str,
    partition_name: str,
    labels: pd.DataFrame,
    regimes: pd.DataFrame,
    selected_model_predictions: pd.DataFrame,
    classification_baseline_predictions: dict[str, pd.DataFrame],
    strategy_results: dict[str, StrategyMetricSet],
    volatility_threshold: Decimal,
    strategy_attribution_rule: str,
    small_sample_threshold: int = 40,
) -> RegimeDiagnostics:
    session_set = set(labels["session"].to_list())
    joined = regimes[regimes["session"].isin(session_set)].copy(deep=True)
    target_by_session = {
        session: int(target)
        for session, target in zip(labels["session"], labels["target"], strict=True)
    }
    cells_by_regime: dict[str, dict[str, RegimeCellDiagnostics]] = {}
    for regime_name in ("trend_200", "realized_volatility_20", "drawdown_10", "calendar_year"):
        cells: dict[str, RegimeCellDiagnostics] = {}
        for value, group in joined.groupby(regime_name, sort=True):
            cell_sessions = tuple(group["session"].to_list())
            targets = [target_by_session[session] for session in cell_sessions]
            positive = sum(targets)
            total = len(targets)
            warnings = ("small_sample",) if total < small_sample_threshold else ()
            undefined_reasons: dict[str, str] = {}
            if positive == 0 or positive == total:
                undefined_reasons["classification_auc_metrics"] = (
                    "both positive and negative classes are required"
                )
            selected_classification = _classification_for_sessions(
                benchmark_id=benchmark_id,
                dataset_id=dataset_id,
                model_name="selected_model",
                partition_name=f"{partition_name}:{regime_name}:{value}",
                predictions=selected_model_predictions,
                sessions=cell_sessions,
            )
            baseline_metrics = {
                name: _classification_for_sessions(
                    benchmark_id=benchmark_id,
                    dataset_id=dataset_id,
                    model_name=name,
                    partition_name=f"{partition_name}:{regime_name}:{value}",
                    predictions=frame,
                    sessions=cell_sessions,
                )
                for name, frame in classification_baseline_predictions.items()
            }
            selected_strategy: dict[str, StrategyMetricSet] = {}
            comparator_strategy: dict[str, StrategyMetricSet] = {}
            for name, metric_set in strategy_results.items():
                attributed = regime_strategy_metric_set(
                    source=metric_set,
                    benchmark_id=benchmark_id,
                    dataset_id=dataset_id,
                    strategy_name=metric_set.strategy_name,
                    partition_name=f"{partition_name}:{regime_name}:{value}",
                    cost_scenario=metric_set.cost_scenario,
                    attributed_sessions=cell_sessions,
                )
                if name.startswith("selected_model:"):
                    selected_strategy[name] = attributed
                else:
                    comparator_strategy[name] = attributed
            if selected_strategy or comparator_strategy:
                undefined_reasons["strategy_path_metrics"] = (
                    "non-contiguous regime subsets are attributed by signal_session and "
                    "do not define standalone annualized return, drawdown, or Sharpe metrics"
                )
            cells[str(value)] = RegimeCellDiagnostics(
                sample_size=total,
                positive_count=positive,
                negative_count=total - positive,
                small_sample=total < small_sample_threshold,
                warnings=warnings,
                selected_model_classification=selected_classification,
                classification_baselines=baseline_metrics,
                selected_model_strategy=selected_strategy,
                strategy_comparators=comparator_strategy,
                undefined_reasons=undefined_reasons,
            )
        cells_by_regime[regime_name] = cells
    return RegimeDiagnostics(
        benchmark_id=benchmark_id,
        dataset_id=dataset_id,
        partition_name=partition_name,
        volatility_threshold=volatility_threshold,
        strategy_attribution_rule=strategy_attribution_rule,
        small_sample_warning_threshold=small_sample_threshold,
        regimes=cells_by_regime,
    )


def _classification_for_sessions(
    *,
    benchmark_id: str,
    dataset_id: str,
    model_name: str,
    partition_name: str,
    predictions: pd.DataFrame,
    sessions: tuple[date, ...],
) -> ClassificationMetricSet:
    subset = (
        predictions[predictions["session"].isin(set(sessions))]
        .copy(deep=True)
        .sort_values("session")
    )
    return classification_metric_set(
        benchmark_id=benchmark_id,
        dataset_id=dataset_id,
        model_name=model_name,
        partition_name=partition_name,
        targets=[int(value) for value in subset["target"].to_list()],
        probabilities=[float(value) for value in subset["probability_positive"].to_list()],
        predictions=[int(value) for value in subset["predicted_class"].to_list()],
    )
