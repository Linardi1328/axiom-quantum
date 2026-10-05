# Axiom Quantum Phase 1 Slice 5 - Canonical Robustness Evidence

Status: Active implementation slice

Linear: `RIC-22`

## Purpose

Formalize descriptive robustness evidence across a baseline research scenario and one or more
perturbation scenarios. The contract is intended for cost, regime, parameter, walk-forward, and
other controlled research comparisons. It records how metrics move under perturbation without
turning robustness diagnostics into automated promotion logic.

## Scope

- define schema `axiom-robustness-evidence-v1`;
- define explicit scenario kinds and metric-direction declarations;
- require a named baseline scenario plus at least one perturbation scenario;
- summarize finite numeric metrics across scenarios deterministically;
- record observation coverage, minimum, median, mean, maximum, worst directional value, absolute
  degradation, and relative degradation when the baseline denominator is nonzero;
- convert canonical robustness evidence into an immutable Axiom `ExperimentResult`;
- keep the resulting strategy state fixed at `research_only`;
- expose the robustness API through `spy_market_agent.research`.

## Directionality

Robustness logic must never infer whether a metric is desirable when high or low. The caller must
declare each metric as either:

- `higher_is_better`; or
- `lower_is_better`.

The declared metric set must exactly match the union of metrics present across supplied scenarios.
The baseline must contain every declared metric. Perturbation scenarios may omit a metric, but that
missingness is recorded through `coverage_fraction` rather than imputed.

## Degradation

For `higher_is_better` metrics, the worst value is the minimum observed value. For
`lower_is_better` metrics, the worst value is the maximum observed value.

Absolute degradation is the non-negative distance from the baseline to that worst directional
value. Relative degradation is:

`absolute_degradation / abs(baseline_value)`

when the baseline is nonzero. Relative degradation is left undefined when the baseline is zero;
the implementation must not fabricate a denominator.

## Determinism

- scenario identifiers are unique and sorted in canonical evidence;
- metric summaries are sorted by metric name;
- all numeric values must be finite;
- duplicate scenario identifiers fail closed;
- undeclared observed metrics or declared-but-unobserved metrics fail closed;
- the baseline scenario must explicitly use kind `baseline`.

## Authority Boundary

Robustness evidence is descriptive research evidence only. It cannot:

- promote a strategy;
- alter execution permissions;
- select strategy parameters automatically;
- access protected evaluation data;
- authorize shadow, paper, broker, or live operation.

`robustness_evidence_to_axiom_result` always emits `StrategyResearchState.RESEARCH_ONLY`.

## Non-Goals

This slice does not define acceptance thresholds, optimization objectives, parameter search,
portfolio allocation, execution logic, or live/paper gates. Later phases may consume robustness
evidence only through separately authorized promotion policies.

## Acceptance

- directional summaries are correct for both higher-is-better and lower-is-better metrics;
- partial perturbation coverage is explicit;
- zero-baseline relative degradation remains undefined;
- invalid, duplicate, undeclared, or non-finite inputs fail closed;
- output ordering is deterministic;
- Axiom result conversion stays research-only;
- existing research, backtest, shadow, paper, risk, broker, and protected-evaluation behavior is
  unchanged;
- repository CI and review gates pass.
