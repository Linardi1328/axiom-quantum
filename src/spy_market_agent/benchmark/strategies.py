from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import pandas as pd
import sklearn

from spy_market_agent.backtesting.engine import run_strategy_signal_backtest
from spy_market_agent.backtesting.models import (
    BacktestConfig,
    BacktestCostAssumptions,
    BacktestResult,
)
from spy_market_agent.benchmark.artifacts import to_jsonable
from spy_market_agent.benchmark.locks import CostScenario, StrategyMetricSet
from spy_market_agent.benchmark.metrics import strategy_metric_payload
from spy_market_agent.datasets.splits import ChronologicalSplitSpec, DatasetPartition
from spy_market_agent.market_data.models import MarketDataBatch
from spy_market_agent.modeling.models import MODEL_SCHEMA_VERSION
from spy_market_agent.risk.models import RiskConfig
from spy_market_agent.strategies.models import (
    SIGNAL_COLUMNS,
    STRATEGY_LONG_PROBABILITY_THRESHOLD,
    STRATEGY_SCHEMA_VERSION,
    StrategySignalSet,
)


def strategy_comparator_metrics(
    *,
    benchmark_id: str,
    dataset_id: str,
    market_data: MarketDataBatch,
    partition: DatasetPartition,
    probabilities: Sequence[float] | None,
    selected_model_name: str,
    cost_scenarios: Sequence[CostScenario],
    partition_name: str,
    created_at: datetime,
) -> dict[str, StrategyMetricSet]:
    results: dict[str, StrategyMetricSet] = {}
    signal_sessions = list(partition.labels["session"].to_list())
    market_sessions = tuple(market_data.data["session"].to_list())
    for scenario in cost_scenarios:
        config = BacktestConfig(
            cost_assumptions=BacktestCostAssumptions(
                commission_bps_per_side=scenario.commission_bps_per_side,
                slippage_bps_per_side=scenario.slippage_bps_per_side,
            )
        )
        if probabilities is not None:
            results[f"selected_model:{scenario.name}"] = _metric_set_from_backtest(
                benchmark_id=benchmark_id,
                dataset_id=dataset_id,
                strategy_name=f"selected_model_{selected_model_name}",
                partition_name=partition_name,
                cost_scenario=scenario.name,
                result=_run_targets(
                    market_data=market_data,
                    partition=partition,
                    selected_model_name=selected_model_name,
                    signal_sessions=signal_sessions,
                    probabilities=[float(value) for value in probabilities],
                    config=config,
                    created_at=created_at,
                ),
            )
        results[f"always_cash:{scenario.name}"] = _metric_set_from_backtest(
            benchmark_id=benchmark_id,
            dataset_id=dataset_id,
            strategy_name="always_cash",
            partition_name=partition_name,
            cost_scenario=scenario.name,
            result=_run_targets(
                market_data=market_data,
                partition=partition,
                selected_model_name=selected_model_name,
                signal_sessions=signal_sessions,
                probabilities=[0.0 for _ in signal_sessions],
                config=config,
                created_at=created_at,
            ),
        )
        buy_hold_sessions, buy_hold_probabilities = _buy_and_hold_signals(
            market_sessions=market_sessions,
            first_entry_session=partition.labels.iloc[0]["entry_session"],
            final_exit_session=partition.labels.iloc[-1]["exit_session"],
        )
        results[f"buy_and_hold:{scenario.name}"] = _metric_set_from_backtest(
            benchmark_id=benchmark_id,
            dataset_id=dataset_id,
            strategy_name="buy_and_hold",
            partition_name=partition_name,
            cost_scenario=scenario.name,
            result=_run_targets(
                market_data=market_data,
                partition=partition,
                selected_model_name=selected_model_name,
                signal_sessions=buy_hold_sessions,
                probabilities=buy_hold_probabilities,
                config=config,
                created_at=created_at,
            ),
        )
        momentum_targets = _momentum_targets(market_data.data, signal_sessions)
        results[f"fixed_20_session_momentum:{scenario.name}"] = _metric_set_from_backtest(
            benchmark_id=benchmark_id,
            dataset_id=dataset_id,
            strategy_name="fixed_20_session_momentum",
            partition_name=partition_name,
            cost_scenario=scenario.name,
            result=_run_targets(
                market_data=market_data,
                partition=partition,
                selected_model_name=selected_model_name,
                signal_sessions=signal_sessions,
                probabilities=[1.0 if target == 1 else 0.0 for target in momentum_targets],
                config=config,
                created_at=created_at,
            ),
        )
    return results


def regime_strategy_metric_set(
    *,
    source: StrategyMetricSet,
    benchmark_id: str,
    dataset_id: str,
    strategy_name: str,
    partition_name: str,
    cost_scenario: str,
    attributed_sessions: Iterable[date],
) -> StrategyMetricSet:
    session_set = set(attributed_sessions)
    proposed_orders = tuple(
        row
        for row in source.proposed_orders
        if _plain_date(row.get("signal_session")) in session_set
    )
    order_sequences = {int(row["sequence_number"]) for row in proposed_orders}
    risk_decisions = tuple(
        row
        for row in source.risk_decisions
        if int(row.get("order_sequence_number", -1)) in order_sequences
    )
    fills = tuple(
        row for row in source.fills if _plain_date(row.get("signal_session")) in session_set
    )
    portfolio_states = tuple(
        row
        for row in source.portfolio_states
        if _plain_date(row.get("signal_session")) in session_set
    )
    metrics = _attributed_strategy_metrics(
        proposed_orders=proposed_orders,
        risk_decisions=risk_decisions,
        fills=fills,
        portfolio_states=portfolio_states,
    )
    return StrategyMetricSet(
        benchmark_id=benchmark_id,
        dataset_id=dataset_id,
        strategy_name=strategy_name,
        partition_name=partition_name,
        cost_scenario=cost_scenario,
        metrics=metrics,
        warnings=(
            "non_contiguous_regime_strategy_subset",
            "attributed_by_signal_session",
        ),
        proposed_orders=proposed_orders,
        risk_decisions=risk_decisions,
        fills=fills,
        portfolio_states=portfolio_states,
    )


def _run_targets(
    *,
    market_data: MarketDataBatch,
    partition: DatasetPartition,
    selected_model_name: str,
    signal_sessions: Sequence[date],
    probabilities: Sequence[float],
    config: BacktestConfig,
    created_at: datetime,
) -> BacktestResult:
    signal_set = _signal_set_from_probabilities(
        market_data=market_data,
        partition=partition,
        selected_model_name=selected_model_name,
        signal_sessions=signal_sessions,
        probabilities=probabilities,
        created_at=created_at,
    )
    return run_strategy_signal_backtest(
        signal_set,
        market_data,
        backtest_config=config,
        risk_config=RiskConfig(),
        created_at=created_at,
    )


def _signal_set_from_probabilities(
    *,
    market_data: MarketDataBatch,
    partition: DatasetPartition,
    selected_model_name: str,
    signal_sessions: Sequence[date],
    probabilities: Sequence[float],
    created_at: datetime,
) -> StrategySignalSet:
    market_sessions = tuple(market_data.data["session"].to_list())
    index_by_session = {session: index for index, session in enumerate(market_sessions)}
    records: list[dict[str, object]] = []
    for signal_session, probability in zip(signal_sessions, probabilities, strict=True):
        execution_index = index_by_session[signal_session] + 1
        execution_session = market_sessions[execution_index]
        records.append(
            {
                "signal_session": signal_session,
                "execution_session": execution_session,
                "probability_positive": float(probability),
                "target_position": (
                    1 if float(probability) >= STRATEGY_LONG_PROBABILITY_THRESHOLD else 0
                ),
            }
        )
    data = pd.DataFrame.from_records(records, columns=list(SIGNAL_COLUMNS))
    data["probability_positive"] = data["probability_positive"].astype("float64")
    data["target_position"] = data["target_position"].astype("int64")
    split_spec = _strategy_split_spec(
        market_sessions=market_sessions,
        first_signal=data.iloc[0]["signal_session"],
        last_execution=data.iloc[-1]["execution_session"],
        preferred=partition.metadata.split_spec,
    )
    return StrategySignalSet(
        data=data,
        selected_model_name=selected_model_name,
        strategy_threshold=STRATEGY_LONG_PROBABILITY_THRESHOLD,
        source_market_data_checksum=market_data.metadata.dataset_checksum,
        source_schema_version=market_data.metadata.schema_version,
        feature_schema_version=partition.metadata.feature_schema_version,
        label_schema_version=partition.metadata.label_schema_version,
        model_schema_version=MODEL_SCHEMA_VERSION,
        strategy_schema_version=STRATEGY_SCHEMA_VERSION,
        feature_columns=partition.metadata.feature_columns,
        split_spec=split_spec,
        market_sessions=market_sessions,
        first_signal_session=data.iloc[0]["signal_session"],
        last_signal_session=data.iloc[-1]["signal_session"],
        first_execution_session=data.iloc[0]["execution_session"],
        last_execution_session=data.iloc[-1]["execution_session"],
        row_count=len(data),
        sklearn_version=sklearn.__version__,
        created_at=created_at,
    )


def _strategy_split_spec(
    *,
    market_sessions: tuple[date, ...],
    first_signal: date,
    last_execution: date,
    preferred: ChronologicalSplitSpec,
) -> ChronologicalSplitSpec:
    if (
        first_signal >= preferred.test_start_session
        and last_execution <= preferred.test_end_session
    ):
        return preferred
    first_index = market_sessions.index(first_signal)
    if first_index < 2:
        msg = "Phase 2 strategy signals require at least two prior market sessions."
        raise ValueError(msg)
    return ChronologicalSplitSpec(
        train_start_session=market_sessions[0],
        train_end_session=market_sessions[0],
        validation_start_session=market_sessions[first_index - 1],
        validation_end_session=market_sessions[first_index - 1],
        test_start_session=first_signal,
        test_end_session=last_execution,
    )


def _metric_set_from_backtest(
    *,
    benchmark_id: str,
    dataset_id: str,
    strategy_name: str,
    partition_name: str,
    cost_scenario: str,
    result: BacktestResult,
) -> StrategyMetricSet:
    return StrategyMetricSet(
        benchmark_id=benchmark_id,
        dataset_id=dataset_id,
        strategy_name=strategy_name,
        partition_name=partition_name,
        cost_scenario=cost_scenario,
        metrics=_payload_from_backtest(result),
        proposed_orders=_records(result.proposed_orders),
        risk_decisions=_records(result.risk_decisions),
        fills=_records(result.fills),
        portfolio_states=_records(result.portfolio),
    )


def _payload_from_backtest(result: BacktestResult) -> dict[str, Decimal | int | float | str | None]:
    equity_curve = [Decimal(str(value)) for value in result.portfolio["equity"].to_list()]
    exposure_flags = [1 if int(value) > 0 else 0 for value in result.portfolio["shares"].to_list()]
    completed_trades = _completed_trade_profit_and_returns(result.fills)
    completed_returns = [trade_return for _, trade_return in completed_trades]
    gross_profit = sum(
        (profit for profit, _ in completed_trades if profit > 0),
        Decimal("0"),
    )
    gross_loss = sum(
        (profit for profit, _ in completed_trades if profit < 0),
        Decimal("0"),
    )
    return strategy_metric_payload(
        initial_cash=result.initial_cash,
        final_equity=Decimal(str(result.metrics.final_equity)),
        equity_curve=equity_curve,
        exposure_flags=exposure_flags,
        orders=int(result.metrics.proposed_order_count),
        fills=int(result.metrics.fill_count),
        completed_trades=int(result.metrics.sell_fill_count),
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        transaction_costs=Decimal(str(result.metrics.total_commission)),
        estimated_slippage=Decimal(str(result.metrics.total_slippage_cost)),
        rejected_risk_decisions=int(result.metrics.rejected_order_count),
        ending_cash=Decimal(str(result.metrics.final_cash)),
        ending_shares=int(result.metrics.final_shares),
        turnover=Decimal(str(result.metrics.total_reference_notional)),
        win_rate=_win_rate(completed_returns),
        average_completed_trade_return=_average(completed_returns),
    )


def _completed_trade_profit_and_returns(fills: pd.DataFrame) -> list[tuple[Decimal, Decimal]]:
    if fills.empty:
        return []
    completed: list[tuple[Decimal, Decimal]] = []
    entry_cash_change: Decimal | None = None
    for row in fills.itertuples(index=False):
        side = str(row.side)
        cash_change = Decimal(str(row.cash_change))
        if side == "buy":
            entry_cash_change = cash_change
        elif side == "sell" and entry_cash_change is not None:
            trade_profit = entry_cash_change + cash_change
            completed.append((trade_profit, trade_profit / abs(entry_cash_change)))
            entry_cash_change = None
    return completed


def _win_rate(values: Sequence[Decimal]) -> Decimal | None:
    if not values:
        return None
    wins = sum(1 for value in values if value > 0)
    return Decimal(wins) / Decimal(len(values))


def _average(values: Sequence[Decimal]) -> Decimal | None:
    if not values:
        return None
    return sum(values, Decimal("0")) / Decimal(len(values))


def _records(frame: pd.DataFrame) -> tuple[dict[str, Any], ...]:
    return tuple(
        {str(key): to_jsonable(value) for key, value in row.items()}
        for row in frame.to_dict(orient="records")
    )


def _buy_and_hold_signals(
    *,
    market_sessions: tuple[date, ...],
    first_entry_session: date,
    final_exit_session: date,
) -> tuple[list[date], list[float]]:
    entry_signal_index = market_sessions.index(first_entry_session) - 1
    exit_signal_index = market_sessions.index(final_exit_session) - 1
    signal_sessions = list(market_sessions[entry_signal_index : exit_signal_index + 1])
    probabilities = [1.0 for _ in signal_sessions]
    probabilities[-1] = 0.0
    return signal_sessions, probabilities


def _momentum_targets(market_data: pd.DataFrame, signal_sessions: Sequence[date]) -> list[int]:
    rows = market_data.reset_index(drop=True)
    index_by_session = {session: index for index, session in enumerate(rows["session"].to_list())}
    closes = rows["close"].astype("float64")
    targets: list[int] = []
    for session in signal_sessions:
        index = index_by_session[session]
        if index < 20:
            targets.append(0)
        else:
            momentum = closes.iloc[index] / closes.iloc[index - 20] - 1.0
            targets.append(1 if momentum > 0 else 0)
    return targets


def _attributed_strategy_metrics(
    *,
    proposed_orders: tuple[dict[str, Any], ...],
    risk_decisions: tuple[dict[str, Any], ...],
    fills: tuple[dict[str, Any], ...],
    portfolio_states: tuple[dict[str, Any], ...],
) -> dict[str, Decimal | int | float | str | None]:
    approved = sum(1 for row in risk_decisions if bool(row.get("approved")))
    rejected = len(risk_decisions) - approved
    transaction_costs = sum(
        (Decimal(str(row.get("commission", "0"))) for row in fills),
        Decimal("0"),
    )
    slippage = sum(
        (Decimal(str(row.get("slippage_cost", "0"))) for row in fills),
        Decimal("0"),
    )
    turnover = sum(
        (Decimal(str(row.get("reference_notional", "0"))) for row in fills),
        Decimal("0"),
    )
    ending_cash: Decimal | None = None
    ending_shares: int | None = None
    exposure_percentage: Decimal | None = None
    if portfolio_states:
        ending_cash = Decimal(str(portfolio_states[-1]["cash"]))
        ending_shares = int(portfolio_states[-1]["shares"])
        exposure_percentage = (
            Decimal(sum(1 for row in portfolio_states if int(row["shares"]) > 0))
            / Decimal(len(portfolio_states))
            * Decimal("100")
        )
    return {
        "orders": len(proposed_orders),
        "fills": len(fills),
        "approved_risk_decisions": approved,
        "rejected_risk_decisions": rejected,
        "transaction_costs": transaction_costs,
        "estimated_slippage": slippage,
        "turnover": turnover,
        "ending_cash": ending_cash,
        "ending_shares": ending_shares,
        "exposure_percentage": exposure_percentage,
        "total_return": None,
        "annualized_return": None,
        "annualized_volatility": None,
        "maximum_drawdown": None,
        "sharpe_ratio": None,
        "undefined_reason": (
            "strategy regime cells are non-contiguous signal-session attributions, "
            "not standalone portfolio backtests"
        ),
    }


def _plain_date(value: object) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value)
    return None
