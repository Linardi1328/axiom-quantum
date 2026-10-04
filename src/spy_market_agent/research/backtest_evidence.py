from __future__ import annotations

import math
from datetime import datetime

from pydantic import BaseModel, ConfigDict, model_validator

from spy_market_agent.backtesting.models import BacktestMetrics
from spy_market_agent.research.experiment_core import (
    ExperimentLifecycleState,
    ExperimentOutcome,
    ExperimentResult,
    StrategyResearchState,
)

BACKTEST_EVIDENCE_SCHEMA_VERSION = "axiom-backtest-evidence-v1"


class CanonicalBacktestEvidence(BaseModel):
    """Research-only canonical view of an already validated backtest metric set."""

    model_config = ConfigDict(frozen=True)

    schema_version: str = BACKTEST_EVIDENCE_SCHEMA_VERSION
    metrics: dict[str, int | float]

    @model_validator(mode="after")
    def _validate_metrics(self) -> CanonicalBacktestEvidence:
        if self.schema_version != BACKTEST_EVIDENCE_SCHEMA_VERSION:
            raise ValueError("unsupported canonical backtest evidence schema version")
        if not self.metrics:
            raise ValueError("canonical backtest evidence must contain metrics")
        for name, value in self.metrics.items():
            if not name or isinstance(value, bool):
                raise ValueError("canonical backtest metric names and values must be valid")
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError(f"canonical backtest metric {name!r} must be finite")
        return self


def canonical_backtest_evidence(metrics: BacktestMetrics) -> CanonicalBacktestEvidence:
    """Convert validated deterministic backtest metrics into canonical research evidence."""

    snapshot: dict[str, int | float] = {
        "session_count": metrics.session_count,
        "initial_cash": metrics.initial_cash,
        "final_cash": metrics.final_cash,
        "final_shares": metrics.final_shares,
        "final_market_value": metrics.final_market_value,
        "final_equity": metrics.final_equity,
        "total_return": metrics.total_return,
        "maximum_drawdown": metrics.maximum_drawdown,
        "total_reference_notional": metrics.total_reference_notional,
        "total_execution_notional": metrics.total_execution_notional,
        "total_commission": metrics.total_commission,
        "total_slippage_cost": metrics.total_slippage_cost,
        "total_transaction_cost": metrics.total_transaction_cost,
        "turnover_ratio": metrics.turnover_ratio,
        "exposure_fraction": metrics.exposure_fraction,
        "proposed_order_count": metrics.proposed_order_count,
        "approved_order_count": metrics.approved_order_count,
        "rejected_order_count": metrics.rejected_order_count,
        "fill_count": metrics.fill_count,
        "buy_fill_count": metrics.buy_fill_count,
        "sell_fill_count": metrics.sell_fill_count,
        "transaction_cost_fraction_initial_cash": (
            metrics.total_transaction_cost / metrics.initial_cash
        ),
    }
    if metrics.total_execution_notional > 0.0:
        snapshot["transaction_cost_bps_execution_notional"] = (
            metrics.total_transaction_cost / metrics.total_execution_notional * 10_000.0
        )
    if metrics.proposed_order_count > 0:
        snapshot["fill_rate"] = metrics.fill_count / metrics.proposed_order_count

    return CanonicalBacktestEvidence(metrics=dict(sorted(snapshot.items())))


def backtest_metrics_to_axiom_result(
    *,
    experiment_id: str,
    metrics: BacktestMetrics,
    completed_at: datetime,
) -> ExperimentResult:
    """Record backtest performance evidence without granting promotion or execution authority."""

    evidence = canonical_backtest_evidence(metrics)
    metric_snapshot: dict[str, str | int | float | bool | None] = dict(evidence.metrics)
    return ExperimentResult(
        experiment_id=experiment_id,
        lifecycle_state=ExperimentLifecycleState.COMPLETED,
        outcome=ExperimentOutcome.COMPLETED,
        strategy_state=StrategyResearchState.RESEARCH_ONLY,
        summary=(
            f"Canonical backtest evidence across {metrics.session_count} sessions; "
            f"return={metrics.total_return:.6f}, drawdown={metrics.maximum_drawdown:.6f}."
        ),
        conclusion=(
            "Backtest performance evidence recorded for research comparison only; "
            "no validation promotion or execution authority is granted."
        ),
        metric_snapshot=metric_snapshot,
        completed_at=completed_at,
    )
