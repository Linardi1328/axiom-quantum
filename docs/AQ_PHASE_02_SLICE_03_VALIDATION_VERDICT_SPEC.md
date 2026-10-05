# Axiom Quantum Phase 2 Slice 3 - Validation Gate and Verdict Engine

Status: Implementation candidate

Linear: `RIC-27`

## Purpose

Slice 3 turns a canonical `ValidationCase` into a deterministic research verdict by applying only caller-declared gates. It does not discover thresholds, optimize them, or grant execution authority.

## Contracts

The validation engine defines:

- `ValidationPolicy` with explicit metric, robustness, and resampling gates;
- one `ValidationGateResult` for every required Phase 2 evidence stage;
- `ValidationVerdict` values `validated_research_candidate`, `rejected`, and `insufficient_evidence`;
- `ValidationDecision`, which is bound to the canonical validation, experiment, result, and policy IDs and fixes `execution_authority` to `none`.

There are no hidden financial defaults. A stage with referenced evidence and no policy threshold passes its structural-evidence check only; a quantitative threshold exists only when the caller explicitly supplies one.

## Fail-Closed Rules

- missing required stage evidence -> `missing` gate status;
- missing/non-numeric metric required by a policy gate -> `missing`;
- missing robustness/resampling evidence required by a policy gate -> `missing`;
- an explicit bound violation -> `failed`;
- any `missing` stage -> `insufficient_evidence`;
- otherwise any `failed` stage -> `rejected`;
- only an all-pass gate set -> `validated_research_candidate`.

The source result must still be the exact `validation_candidate` used to build the validation case. Policy, experiment, and result identity mismatches fail closed before evaluation.

## Quantitative Evidence

Metric thresholds read finite numeric values from the canonical source `ExperimentResult`. Robustness thresholds read declared metric summaries from `CanonicalRobustnessEvidence` and may constrain coverage, absolute degradation, and relative degradation. Resampling thresholds read empirical loss and configured drawdown-breach frequencies from `CanonicalResamplingEvidence`.

Resampling frequencies remain empirical research diagnostics and are not represented as guaranteed future probabilities.

## Safety Boundary

This slice does not:

- open protected evaluation data;
- promote a strategy into shadow, paper, broker, or live operation;
- size positions or allocate capital;
- schedule signals;
- mutate historical Phase 1 evidence;
- create autonomous model/strategy promotion.

A `validated_research_candidate` verdict is a Phase 2 research classification only. `execution_authority` remains `none`.

## Acceptance Gates

- every required Phase 2 evidence stage has exactly one auditable gate outcome;
- thresholds are explicit and deterministic;
- missing evidence can never silently pass;
- complete failed quantitative evidence is rejected;
- complete all-pass evidence can become `validated_research_candidate`;
- result/policy identity mismatches fail closed;
- focused tests cover validated, rejected, and insufficient paths;
- Ruff, Mypy, pytest/coverage, whitespace, Betterleaks, and CodeRabbit are green before squash merge.
