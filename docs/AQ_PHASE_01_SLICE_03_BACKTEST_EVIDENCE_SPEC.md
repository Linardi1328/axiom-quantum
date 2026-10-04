# Axiom Quantum Phase 1 Slice 3 - Canonical Backtest Performance Evidence

Status: Active implementation slice

Linear: `RIC-20`

## Purpose

Standardize already validated deterministic backtest outputs into canonical Axiom Quantum research evidence. This slice makes return, drawdown, cost, turnover, exposure, and order/fill metrics comparable across research records without changing the underlying backtest engine.

## Scope

- adapt existing `BacktestMetrics` into a canonical research metric snapshot;
- preserve validated accounting metrics exactly;
- add derived cost-burden and fill-rate diagnostics only when their denominators are defined;
- produce an `ExperimentResult` that is always `research_only`;
- expose the adapter through the research package.

## Safety Boundary

This slice does not grant validation promotion, shadow approval, paper authority, broker access, or live execution authority. Backtest evidence alone is not sufficient to promote a strategy.

No backtest accounting, strategy signal, risk, shadow, paper, broker, or protected-evaluation behavior is changed.

## Canonical Metrics

The adapter preserves the existing validated fields, including session count, starting/final capital state, total return, maximum drawdown, reference/execution notionals, commissions, slippage, total transaction costs, turnover, exposure, order counts, and fill counts.

It additionally derives:

- `transaction_cost_fraction_initial_cash`;
- `transaction_cost_bps_execution_notional` when execution notional is positive;
- `fill_rate` when proposed-order count is positive.

Undefined ratios are omitted rather than represented as zero, infinity, NaN, or another fabricated value.

## Acceptance

- canonical evidence is deterministic for equivalent validated backtest metrics;
- all numeric outputs are finite;
- existing metric semantics are preserved;
- undefined ratios are omitted;
- generated Axiom results remain `research_only`;
- no execution-path behavior changes;
- repository CI and review gates pass.
