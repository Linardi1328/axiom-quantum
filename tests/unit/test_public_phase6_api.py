from __future__ import annotations

import importlib
from pathlib import Path

import pytest

import spy_market_agent.backtesting as backtesting
import spy_market_agent.risk as risk
import spy_market_agent.strategies as strategies
from spy_market_agent.backtesting import (
    BACKTEST_SCHEMA_VERSION,
    EXECUTION_PRICE_COLUMNS,
    INITIAL_SIMULATED_CASH,
    TRADING_SESSIONS_PER_YEAR,
    BacktestConfig,
    BacktestCostAssumptions,
    BacktestResult,
    ExecutionPriceSet,
    run_long_or_cash_backtest,
)
from spy_market_agent.risk import (
    RISK_SCHEMA_VERSION,
    SUPPORTED_SYMBOL,
    PortfolioState,
    ProposedOrder,
    RiskConfig,
    RiskDecision,
    evaluate_order_risk,
)
from spy_market_agent.strategies import (
    STRATEGY_LONG_PROBABILITY_THRESHOLD,
    STRATEGY_SCHEMA_VERSION,
    StrategySignalSet,
    build_long_cash_strategy_signals,
)


def test_public_strategy_risk_and_backtesting_exports_are_explicit() -> None:
    expected_strategy = {
        "STRATEGY_LONG_PROBABILITY_THRESHOLD": STRATEGY_LONG_PROBABILITY_THRESHOLD,
        "STRATEGY_SCHEMA_VERSION": STRATEGY_SCHEMA_VERSION,
        "StrategySignalSet": StrategySignalSet,
        "build_long_cash_strategy_signals": build_long_cash_strategy_signals,
    }
    expected_risk = {
        "RISK_SCHEMA_VERSION": RISK_SCHEMA_VERSION,
        "SUPPORTED_SYMBOL": SUPPORTED_SYMBOL,
        "PortfolioState": PortfolioState,
        "ProposedOrder": ProposedOrder,
        "RiskConfig": RiskConfig,
        "RiskDecision": RiskDecision,
        "evaluate_order_risk": evaluate_order_risk,
    }
    expected_backtesting = {
        "BACKTEST_SCHEMA_VERSION": BACKTEST_SCHEMA_VERSION,
        "EXECUTION_PRICE_COLUMNS": EXECUTION_PRICE_COLUMNS,
        "INITIAL_SIMULATED_CASH": INITIAL_SIMULATED_CASH,
        "TRADING_SESSIONS_PER_YEAR": TRADING_SESSIONS_PER_YEAR,
        "BacktestConfig": BacktestConfig,
        "BacktestCostAssumptions": BacktestCostAssumptions,
        "BacktestResult": BacktestResult,
        "ExecutionPriceSet": ExecutionPriceSet,
        "run_long_or_cash_backtest": run_long_or_cash_backtest,
    }

    for module, expected in (
        (strategies, expected_strategy),
        (risk, expected_risk),
        (backtesting, expected_backtesting),
    ):
        for name in module.__all__:
            assert hasattr(module, name)
        for name, imported_value in expected.items():
            assert getattr(module, name) is imported_value


def test_importing_phase6_packages_has_no_external_side_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    assert importlib.import_module("spy_market_agent.strategies") is strategies
    assert importlib.import_module("spy_market_agent.risk") is risk
    assert importlib.import_module("spy_market_agent.backtesting") is backtesting
    assert list(tmp_path.iterdir()) == []
