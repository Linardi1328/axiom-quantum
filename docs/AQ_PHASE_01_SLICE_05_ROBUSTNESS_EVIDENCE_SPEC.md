# Axiom Quantum Phase 1 Slice 5 - Canonical Robustness Evidence

Status: Active implementation slice

Linear: `RIC-24`

## Purpose

Formalize deterministic robustness evidence above existing backtest and walk-forward outputs so
Axiom research can compare return-path risk and scenario stability without conflating measurement
with promotion or execution authority.

## Scope

- define schema version `axiom-robustness-evidence-v1`;
- calculate deterministic arithmetic-return-path metrics;
- calculate deterministic cross-scenario envelopes;
- preserve undefined ratios as absent rather than fabricating zero values;
- flatten canonical robustness evidence into an immutable `ExperimentResult`;
- hard-cap the result at `research_only`;
- expose the API through `spy_market_agent.research`.

## Return-Path Metrics

For one finite arithmetic-return path, the canonical evidence records:

- period count and terminal wealth;
- cumulative return;
- annualized return;
- mean period return;
- annualized volatility;
- annualized downside deviation;
- maximum drawdown magnitude;
- positive and non-positive period fractions;
- best and worst period return;
- Sharpe ratio when period volatility is non-zero;
- Sortino ratio when downside deviation is non-zero.

Arithmetic returns below `-1.0`, non-finite returns, empty paths, non-positive annualization factors,
and any non-finite calculated metric fail closed.

## Scenario Envelope

A scenario is a safe canonical identifier plus one return-path evidence object. The envelope:

- sorts scenarios by identifier;
- rejects duplicate identifiers;
- records profitable-scenario fraction;
- records worst and median cumulative return;
- records worst and median annualized return;
- records worst and median maximum drawdown;
- records worst and median annualized volatility;
- records defined-coverage fraction plus minimum/median values for Sharpe and Sortino when defined.

Caller ordering must not affect the resulting evidence.

## Authority Boundary

Robustness evidence measures research outcomes only. It does not:

- promote a strategy;
- authorize protected evaluation;
- authorize shadow operation;
- authorize paper trading;
- authorize broker submission;
- authorize live trading.

`robustness_evidence_to_axiom_result` always emits `StrategyResearchState.RESEARCH_ONLY`.

## Non-Goals

This slice does not add:

- stochastic Monte Carlo claims;
- bootstrap confidence intervals;
- strategy-selection thresholds;
- validation admission logic;
- new market data;
- new dependencies;
- changes to execution, paper, shadow, broker, or protected-evaluation behavior.

## Acceptance

- repeated equivalent inputs produce identical evidence;
- caller scenario ordering does not change the canonical envelope;
- impossible and non-finite return paths fail closed;
- undefined Sharpe/Sortino values are omitted rather than represented as zero;
- duplicate scenario identifiers fail closed;
- robustness-derived experiment results remain research-only;
- existing backtest, research, shadow, paper, risk, broker, and protected-evaluation behavior remains
  unchanged;
- repository CI and CodeRabbit review gates pass.
