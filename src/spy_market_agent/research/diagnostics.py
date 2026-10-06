from __future__ import annotations

import math
import statistics
from datetime import date
from typing import cast

import pandas as pd

from spy_market_agent.market_data.models import MarketDataBatch
from spy_market_agent.research.campaign import ResearchCampaignConfig
from spy_market_agent.research.errors import ResearchMetricError, raise_research_error
from spy_market_agent.research.features import ResearchSupervisedDataset
from spy_market_agent.research.models import ClassificationMetricSet, WalkForwardFold


def fold_regime_diagnostics(
    *,
    market_data: MarketDataBatch,
    supervised: ResearchSupervisedDataset,
    fold: WalkForwardFold,
    probabilities: tuple[float, ...],
    config: ResearchCampaignConfig,
) -> dict[str, object]:
    """Build descriptive fold regimes without fitting on assessment rows."""

    assessment = _rows_for_sessions(supervised.labels, fold.assessment.prediction_sessions)
    probability_by_session = dict(
        zip(fold.assessment.prediction_sessions, probabilities, strict=True)
    )
    trend_labels = _trend_200_by_session(market_data)
    volatility_threshold = _training_volatility_threshold(supervised, fold)
    drawdown_labels = _drawdown_buckets_by_session(market_data, config)
    rows: list[dict[str, object]] = []
    for row in assessment.itertuples(index=False):
        session = cast(date, row.session)
        probability = probability_by_session[session]
        rows.append(
            {
                "session": session,
                "target": int(row.target),
                "probability": probability,
                "predicted_positive": int(
                    probability >= config.diagnostic_classification_threshold
                ),
                "trend_200": trend_labels.get(session, "unavailable"),
                "volatility_regime": (
                    "high_volatility"
                    if float(
                        _rows_for_sessions(
                            supervised.features,
                            (session,),
                        ).iloc[0]["realized_volatility_20"]
                    )
                    >= volatility_threshold
                    else "lower_volatility"
                ),
                "drawdown_regime": drawdown_labels.get(session, "unavailable"),
                "calendar_year": session.year,
            }
        )
    return {
        "fold_id": fold.fold_id,
        "fold_period": {
            "first_assessment_session": fold.assessment.first_prediction_session,
            "last_assessment_session": fold.assessment.last_prediction_session,
        },
        "volatility_threshold_source": "fold_training_rows_only",
        "volatility_threshold": volatility_threshold,
        "trend_200": _summarize_cells(rows, "trend_200", config.small_regime_cell_rows),
        "volatility": _summarize_cells(rows, "volatility_regime", config.small_regime_cell_rows),
        "drawdown": _summarize_cells(rows, "drawdown_regime", config.small_regime_cell_rows),
        "calendar_year": _summarize_cells(rows, "calendar_year", config.small_regime_cell_rows),
        "selection_use": "descriptive_only_not_used_for_candidate_selection",
    }


def fold_drift_diagnostics(
    *,
    supervised: ResearchSupervisedDataset,
    fold: WalkForwardFold,
    feature_columns: tuple[str, ...],
    probabilities: tuple[float, ...],
    metrics: ClassificationMetricSet,
    config: ResearchCampaignConfig,
) -> dict[str, object]:
    train_features = _rows_for_sessions(supervised.features, fold.training.prediction_sessions)
    assess_features = _rows_for_sessions(supervised.features, fold.assessment.prediction_sessions)
    probability_values = list(probabilities)
    return {
        "fold_id": fold.fold_id,
        "target_prevalence": metrics.prevalence,
        "feature_missingness": {
            column: float(assess_features[column].isna().mean()) for column in feature_columns
        },
        "feature_finite_value_rate": {
            column: _finite_rate(assess_features[column]) for column in feature_columns
        },
        "predicted_positive_rate": metrics.predicted_positive_rate,
        "probability_mean": statistics.fmean(probability_values),
        "probability_standard_deviation": (
            statistics.pstdev(probability_values) if len(probability_values) > 1 else 0.0
        ),
        "probability_quantiles": _probability_quantiles(probability_values),
        "brier_score": _metric_payload(metrics, "brier_score"),
        "log_loss": _metric_payload(metrics, "log_loss"),
        "expected_calibration_error": _metric_payload(metrics, "expected_calibration_error"),
        "roc_auc": _metric_payload(metrics, "roc_auc"),
        "average_precision": _metric_payload(metrics, "average_precision"),
        "feature_distribution_drift": {
            column: _psi_for_feature(
                train_features[column],
                assess_features[column],
                bin_count=config.psi_bin_count,
                epsilon=config.psi_epsilon,
            )
            for column in feature_columns
        },
    }


def _rows_for_sessions(frame: pd.DataFrame, sessions: tuple[date, ...]) -> pd.DataFrame:
    indexed = frame.set_index("session", drop=False)
    try:
        selected = indexed.loc[list(sessions)]
    except KeyError:
        raise_research_error(
            ResearchMetricError,
            "diagnostic_session_missing",
            "diagnostic sessions must exist in the supervised data.",
        )
    return selected.reset_index(drop=True)


def _trend_200_by_session(market_data: MarketDataBatch) -> dict[date, str]:
    source = market_data.data.copy(deep=True).reset_index(drop=True)
    close = source["close"].astype("float64")
    sma_200 = close.rolling(window=200, min_periods=200, center=False).mean()
    result: dict[date, str] = {}
    for session, close_value, mean_value in zip(
        source["session"].to_list(),
        close.to_list(),
        sma_200.to_list(),
        strict=True,
    ):
        parsed_session = cast(date, session)
        if pd.isna(mean_value) or not math.isfinite(float(mean_value)):
            result[parsed_session] = "unavailable"
        elif float(close_value) >= float(mean_value):
            result[parsed_session] = "above_trailing_200_sma"
        else:
            result[parsed_session] = "below_trailing_200_sma"
    return result


def _training_volatility_threshold(
    supervised: ResearchSupervisedDataset,
    fold: WalkForwardFold,
) -> float:
    train = _rows_for_sessions(supervised.features, fold.training.prediction_sessions)
    values = [float(value) for value in train["realized_volatility_20"].to_list()]
    if not values or any(not math.isfinite(value) for value in values):
        raise_research_error(
            ResearchMetricError,
            "invalid_training_volatility_regime_values",
            "fold training volatility values must be finite.",
        )
    return statistics.median(values)


def _drawdown_buckets_by_session(
    market_data: MarketDataBatch,
    config: ResearchCampaignConfig,
) -> dict[date, str]:
    source = market_data.data.copy(deep=True).reset_index(drop=True)
    close = source["close"].astype("float64")
    running_peak = close.cummax()
    drawdown = close / running_peak - 1.0
    deep_threshold, moderate_threshold = config.drawdown_bucket_thresholds
    result: dict[date, str] = {}
    for session, value in zip(source["session"].to_list(), drawdown.to_list(), strict=True):
        parsed_session = cast(date, session)
        parsed_value = float(value)
        if not math.isfinite(parsed_value):
            result[parsed_session] = "unavailable"
        elif parsed_value <= deep_threshold:
            result[parsed_session] = "deep_drawdown"
        elif parsed_value <= moderate_threshold:
            result[parsed_session] = "moderate_drawdown"
        else:
            result[parsed_session] = "shallow_or_high"
    return result


def _summarize_cells(
    rows: list[dict[str, object]],
    key: str,
    small_regime_cell_rows: int,
) -> tuple[dict[str, object], ...]:
    groups: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        groups.setdefault(str(row[key]), []).append(row)
    summaries: list[dict[str, object]] = []
    for value in sorted(groups):
        group = groups[value]
        row_count = len(group)
        targets = [int(cast(int, item["target"])) for item in group]
        probabilities = [float(cast(float, item["probability"])) for item in group]
        predicted = [int(cast(int, item["predicted_positive"])) for item in group]
        summaries.append(
            {
                "regime": value,
                "row_count": row_count,
                "small_sample": row_count < small_regime_cell_rows,
                "target_prevalence": sum(targets) / row_count,
                "mean_probability": statistics.fmean(probabilities),
                "predicted_positive_rate": sum(predicted) / row_count,
            }
        )
    return tuple(summaries)


def _finite_rate(values: pd.Series) -> float:
    parsed: list[float] = []
    for value in values.to_list():
        try:
            parsed_value = float(cast(float, value))
        except (TypeError, ValueError, OverflowError):
            continue
        if math.isfinite(parsed_value):
            parsed.append(parsed_value)
    if len(values) == 0:
        return 0.0
    return len(parsed) / len(values)


def _probability_quantiles(values: list[float]) -> dict[str, float]:
    series = pd.Series(values, dtype="float64")
    return {
        "p05": float(series.quantile(0.05)),
        "p25": float(series.quantile(0.25)),
        "p50": float(series.quantile(0.50)),
        "p75": float(series.quantile(0.75)),
        "p95": float(series.quantile(0.95)),
    }


def _metric_payload(metrics: ClassificationMetricSet, metric_name: str) -> dict[str, object]:
    metric = metrics.metrics[metric_name]
    return metric.model_dump(mode="python")


def _psi_for_feature(
    training: pd.Series,
    assessment: pd.Series,
    *,
    bin_count: int,
    epsilon: float,
) -> dict[str, object]:
    training_values = _finite_values(training)
    assessment_values = _finite_values(assessment)
    base = {
        "training_mean": statistics.fmean(training_values) if training_values else None,
        "assessment_mean": statistics.fmean(assessment_values) if assessment_values else None,
        "training_median": statistics.median(training_values) if training_values else None,
        "assessment_median": statistics.median(assessment_values) if assessment_values else None,
        "training_standard_deviation": (
            statistics.pstdev(training_values) if len(training_values) > 1 else 0.0
        )
        if training_values
        else None,
        "assessment_standard_deviation": (
            statistics.pstdev(assessment_values) if len(assessment_values) > 1 else 0.0
        )
        if assessment_values
        else None,
    }
    if len(training_values) < 2 or len(assessment_values) < 1:
        return {
            **base,
            "psi": None,
            "undefined_reason": "insufficient_feature_values",
            "bins": (),
        }
    quantiles = [index / bin_count for index in range(bin_count + 1)]
    raw_edges = [float(pd.Series(training_values, dtype="float64").quantile(q)) for q in quantiles]
    unique_edges = tuple(sorted(set(raw_edges)))
    if len(unique_edges) < 2:
        return {
            **base,
            "psi": None,
            "undefined_reason": "degenerate_training_feature_distribution",
            "bins": (),
        }
    edges = (-math.inf, *unique_edges[1:-1], math.inf)
    training_counts = _bin_counts(training_values, edges)
    assessment_counts = _bin_counts(assessment_values, edges)
    psi = 0.0
    bins: list[dict[str, object]] = []
    for index, (training_count, assessment_count) in enumerate(
        zip(training_counts, assessment_counts, strict=True)
    ):
        training_share = max(training_count / len(training_values), epsilon)
        assessment_share = max(assessment_count / len(assessment_values), epsilon)
        contribution = (assessment_share - training_share) * math.log(
            assessment_share / training_share
        )
        psi += contribution
        bins.append(
            {
                "bin_index": index,
                "lower_bound": _edge_payload(edges[index]),
                "upper_bound": _edge_payload(edges[index + 1]),
                "training_count": training_count,
                "assessment_count": assessment_count,
                "training_share": training_share,
                "assessment_share": assessment_share,
                "psi_contribution": contribution,
            }
        )
    if not math.isfinite(psi):
        raise_research_error(
            ResearchMetricError,
            "non_finite_psi",
            "population stability index must be finite.",
        )
    return {
        **base,
        "psi": psi,
        "undefined_reason": None,
        "bins": tuple(bins),
    }


def _finite_values(values: pd.Series) -> list[float]:
    finite_values: list[float] = []
    for value in values.to_list():
        try:
            parsed = float(cast(float, value))
        except (TypeError, ValueError, OverflowError):
            continue
        if math.isfinite(parsed):
            finite_values.append(parsed)
    return finite_values


def _bin_counts(values: list[float], edges: tuple[float, ...]) -> tuple[int, ...]:
    counts = [0 for _ in range(len(edges) - 1)]
    for value in values:
        for index in range(len(edges) - 1):
            lower = edges[index]
            upper = edges[index + 1]
            if lower <= value < upper or (index == len(edges) - 2 and value == upper):
                counts[index] += 1
                break
    return tuple(counts)


def _edge_payload(value: float) -> float | str:
    if value == -math.inf:
        return "-infinity"
    if value == math.inf:
        return "infinity"
    return value
