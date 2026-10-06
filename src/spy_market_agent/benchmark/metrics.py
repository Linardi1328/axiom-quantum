from __future__ import annotations

import math
from collections.abc import Sequence
from decimal import Decimal
from itertools import pairwise

import pandas as pd
from sklearn.metrics import average_precision_score, brier_score_loss, log_loss, roc_auc_score

from spy_market_agent.benchmark.errors import BenchmarkInputError, raise_benchmark_error
from spy_market_agent.benchmark.locks import ClassificationMetricSet, MetricValue


def classification_metric_set(
    *,
    benchmark_id: str,
    dataset_id: str,
    model_name: str,
    partition_name: str,
    targets: Sequence[int],
    probabilities: Sequence[float],
    predictions: Sequence[int],
) -> ClassificationMetricSet:
    y_true = _binary_ints(targets, field_name="targets")
    y_prob = _probabilities(probabilities)
    y_pred = _binary_ints(predictions, field_name="predictions")
    if not (len(y_true) == len(y_prob) == len(y_pred)) or not y_true:
        raise_benchmark_error(
            BenchmarkInputError,
            "metric_length_mismatch",
            "classification metrics require equal non-empty target, probability, "
            "and prediction lengths.",
        )

    tn = fp = fn = tp = 0
    for target, prediction in zip(y_true, y_pred, strict=True):
        if target == 0 and prediction == 0:
            tn += 1
        elif target == 0 and prediction == 1:
            fp += 1
        elif target == 1 and prediction == 0:
            fn += 1
        else:
            tp += 1
    row_count = len(y_true)
    positive_count = sum(y_true)
    negative_count = row_count - positive_count
    predicted_positive_count = sum(y_pred)

    metrics: dict[str, MetricValue] = {
        "accuracy": _defined((tp + tn) / row_count),
        "positive_prevalence": _defined(positive_count / row_count),
        "predicted_positive_rate": _defined(predicted_positive_count / row_count),
        "brier_score": _defined(float(brier_score_loss(y_true, y_prob, pos_label=1))),
        "log_loss": _defined(float(log_loss(y_true, y_prob, labels=[0, 1]))),
        "precision": _ratio_or_undefined(tp, tp + fp, "no predicted-positive observations"),
        "recall": _ratio_or_undefined(tp, tp + fn, "no positive observations"),
    }
    if positive_count > 0 and negative_count > 0:
        true_positive_rate = tp / positive_count
        true_negative_rate = tn / negative_count
        metrics["balanced_accuracy"] = _defined((true_positive_rate + true_negative_rate) / 2)
        metrics["roc_auc"] = _defined(float(roc_auc_score(y_true, y_prob)))
        metrics["average_precision"] = _defined(
            float(average_precision_score(y_true, y_prob, pos_label=1))
        )
    else:
        reason = "both positive and negative classes are required"
        metrics["balanced_accuracy"] = _undefined(reason)
        metrics["roc_auc"] = _undefined(reason)
        metrics["average_precision"] = _undefined(reason)
    precision = metrics["precision"].value
    recall = metrics["recall"].value
    if precision is None or recall is None or precision + recall == 0:
        metrics["f1"] = _undefined("precision and recall must be defined and non-zero")
    else:
        metrics["f1"] = _defined(2 * precision * recall / (precision + recall))

    return ClassificationMetricSet(
        benchmark_id=benchmark_id,
        dataset_id=dataset_id,
        model_name=model_name,
        partition_name=partition_name,
        row_count=row_count,
        positive_count=positive_count,
        negative_count=negative_count,
        predicted_positive_count=predicted_positive_count,
        confusion_matrix={
            "true_negative": tn,
            "false_positive": fp,
            "false_negative": fn,
            "true_positive": tp,
        },
        metrics=metrics,
    )


def strategy_metric_payload(
    *,
    initial_cash: Decimal,
    final_equity: Decimal,
    equity_curve: Sequence[Decimal],
    exposure_flags: Sequence[int],
    orders: int,
    fills: int,
    completed_trades: int,
    gross_profit: Decimal,
    gross_loss: Decimal,
    transaction_costs: Decimal,
    estimated_slippage: Decimal,
    rejected_risk_decisions: int,
    ending_cash: Decimal,
    ending_shares: int,
    turnover: Decimal,
    win_rate: Decimal | None,
    average_completed_trade_return: Decimal | None,
    risk_free_rate: Decimal = Decimal("0"),
) -> dict[str, Decimal | int | float | str | None]:
    total_return = final_equity / initial_cash - Decimal("1")
    session_count = len(equity_curve)
    returns: list[float] = []
    for previous, current in pairwise(equity_curve):
        if previous > 0:
            returns.append(float(current / previous - Decimal("1")))
    annualized_return: Decimal | None = None
    if session_count > 1 and final_equity > 0:
        annualized_return = (final_equity / initial_cash) ** (
            Decimal(252) / Decimal(session_count)
        ) - Decimal("1")
    annualized_volatility: float | None = None
    sharpe_ratio: float | None = None
    if len(returns) > 1:
        mean = sum(returns) / len(returns)
        variance = sum((value - mean) ** 2 for value in returns) / (len(returns) - 1)
        annualized_volatility = math.sqrt(variance) * math.sqrt(252)
        if annualized_volatility > 0:
            sharpe_ratio = (mean * 252 - float(risk_free_rate)) / annualized_volatility
    max_drawdown = _maximum_drawdown(equity_curve)
    exposure = Decimal("0")
    if exposure_flags:
        exposure = Decimal(sum(exposure_flags)) / Decimal(len(exposure_flags))
    return {
        "initial_cash": initial_cash,
        "final_equity": final_equity,
        "total_return": total_return,
        "annualized_return": annualized_return,
        "annualized_volatility": annualized_volatility,
        "maximum_drawdown": max_drawdown,
        "sharpe_ratio": sharpe_ratio,
        "exposure_percentage": exposure * Decimal("100"),
        "turnover": turnover,
        "orders": orders,
        "fills": fills,
        "completed_trades": completed_trades,
        "win_rate": win_rate,
        "average_completed_trade_return": average_completed_trade_return,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "transaction_costs": transaction_costs,
        "estimated_slippage": estimated_slippage,
        "rejected_risk_decisions": rejected_risk_decisions,
        "ending_cash": ending_cash,
        "ending_shares": ending_shares,
    }


def _maximum_drawdown(equity_curve: Sequence[Decimal]) -> Decimal:
    if not equity_curve:
        return Decimal("0")
    peak = equity_curve[0]
    worst = Decimal("0")
    for equity in equity_curve:
        if equity > peak:
            peak = equity
        if peak > 0:
            worst = min(worst, equity / peak - Decimal("1"))
    return worst


def _binary_ints(values: Sequence[int], *, field_name: str) -> list[int]:
    parsed: list[int] = []
    for value in values:
        if isinstance(value, bool) or int(value) not in {0, 1}:
            raise_benchmark_error(
                BenchmarkInputError,
                f"invalid_{field_name}",
                f"{field_name} must contain only 0 and 1.",
            )
        parsed.append(int(value))
    return parsed


def _probabilities(values: Sequence[float]) -> list[float]:
    parsed: list[float] = []
    for value in values:
        probability = float(value)
        if not math.isfinite(probability) or not 0.0 <= probability <= 1.0:
            raise_benchmark_error(
                BenchmarkInputError,
                "invalid_probability",
                "probabilities must be finite values in [0, 1].",
            )
        parsed.append(probability)
    return parsed


def _defined(value: float) -> MetricValue:
    return MetricValue(value=float(value), undefined_reason=None)


def _undefined(reason: str) -> MetricValue:
    return MetricValue(value=None, undefined_reason=reason)


def _ratio_or_undefined(numerator: int, denominator: int, reason: str) -> MetricValue:
    if denominator == 0:
        return _undefined(reason)
    return _defined(numerator / denominator)


def predictions_from_frame(frame: pd.DataFrame) -> tuple[list[int], list[float], list[int]]:
    return (
        [int(value) for value in frame["target"].to_list()],
        [float(value) for value in frame["probability_positive"].to_list()],
        [int(value) for value in frame["predicted_class"].to_list()],
    )
