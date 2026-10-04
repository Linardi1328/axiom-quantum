from __future__ import annotations

from datetime import UTC, datetime

from spy_market_agent.backtesting.models import BacktestMetrics
from spy_market_agent.research.backtest_evidence import (
    BACKTEST_EVIDENCE_SCHEMA_VERSION,
    backtest_metrics_to_axiom_result,
    canonical_backtest_evidence,
)
from spy_market_agent.research.experiment_core import StrategyResearchState


def _metrics(*, total_execution_notional: float = 10_000.0) -> BacktestMetrics:
    return BacktestMetrics(
        session_count=252,
        initial_cash=10_000.0,
        final_cash=10_500.0,
        final_shares=0,
        final_market_value=0.0,
        final_equity=10_500.0,
        total_return=0.05,
        maximum_drawdown=0.10,
        total_reference_notional=10_000.0,
        total_execution_notional=total_execution_notional,
        total_commission=10.0,
        total_slippage_cost=10.0,
        total_transaction_cost=20.0,
        turnover_ratio=1.0,
        exposure_fraction=0.40,
        proposed_order_count=2,
        approved_order_count=2,
        rejected_order_count=0,
        fill_count=2,
        buy_fill_count=1,
        sell_fill_count=1,
    )


def test_canonical_backtest_evidence_preserves_validated_metrics() -> None:
    evidence = canonical_backtest_evidence(_metrics())

    assert evidence.schema_version == BACKTEST_EVIDENCE_SCHEMA_VERSION
    assert evidence.metrics["total_return"] == 0.05
    assert evidence.metrics["maximum_drawdown"] == 0.10
    assert evidence.metrics["total_transaction_cost"] == 20.0
    assert evidence.metrics["turnover_ratio"] == 1.0
    assert evidence.metrics["exposure_fraction"] == 0.40
    assert evidence.metrics["transaction_cost_fraction_initial_cash"] == 0.002
    assert evidence.metrics["transaction_cost_bps_execution_notional"] == 20.0
    assert evidence.metrics["fill_rate"] == 1.0


def test_canonical_backtest_evidence_omits_undefined_cost_rate() -> None:
    metrics = _metrics(total_execution_notional=0.0)
    evidence = canonical_backtest_evidence(metrics)

    assert "transaction_cost_bps_execution_notional" not in evidence.metrics


def test_backtest_result_remains_research_only() -> None:
    result = backtest_metrics_to_axiom_result(
        experiment_id="aq-exp-111111111111111111111111",
        metrics=_metrics(),
        completed_at=datetime(2026, 10, 5, tzinfo=UTC),
    )

    assert result.strategy_state == StrategyResearchState.RESEARCH_ONLY
    assert result.metric_snapshot["final_equity"] == 10_500.0
    assert "no validation promotion" in result.conclusion
