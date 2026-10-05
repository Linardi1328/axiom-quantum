# Axiom Quantum Phase 2 - Validation Engine Completion

Status: Final implementation candidate - complete upon squash merge of the Slice 5 pull request

Linear: `RIC-25` through `RIC-29`

## Purpose

This document closes Axiom Quantum Phase 2 once the final validation-reporting slice is merged.
Phase 2 is a research-only validation layer over the Phase 1 Research Core. It determines whether a
research candidate has complete and policy-compliant evidence, must be rejected, or still lacks
required evidence. It does not authorize any trading environment.

## Implemented Slices

1. **Validation Case and Evidence Contract (`RIC-25`, PR #59)**
   - canonical ordered validation stages;
   - checksum-bound evidence references;
   - deterministic `aq-validation-*` case identity;
   - admission only from a Phase 1 `validation_candidate` result;
   - execution authority fixed to `none`.
2. **Deterministic Resampling Risk Evidence (`RIC-26`, PR #60)**
   - seeded IID and moving-block bootstrap evidence;
   - source-return checksum binding;
   - return and drawdown distributions;
   - empirical loss and drawdown-breach frequencies without future-probability claims.
3. **Validation Gate and Verdict Engine (`RIC-27`, PR #61)**
   - caller-defined validation policy and policy digest;
   - structural, metric, robustness, and resampling gates;
   - deterministic `validated_research_candidate`, `rejected`, and `insufficient_evidence`
     verdicts;
   - missing evidence fails closed rather than passing.
4. **Strategy Graveyard and Validation Memory (`RIC-28`, PR #62)**
   - content-addressed append-only validation decisions;
   - deterministic rejected-strategy graveyard entries;
   - retained failed-gate reasons and references;
   - exact reload/link verification and corruption/conflict rejection.
5. **Validation Reporting and Completion (`RIC-29`)**
   - deterministic human-auditable validation report;
   - append-only checksum-verified report persistence;
   - end-to-end decision/graveyard/report workflow;
   - integration coverage for validated, rejected, and insufficient-evidence paths.

## End-to-End Validation Flow

The completed Phase 2 path is:

`Phase 1 validation_candidate -> ValidationCase -> explicit ValidationPolicy -> bound evidence ->`
`stage gates -> ValidationDecision -> Validation Memory -> rejected-only Strategy Graveyard ->`
`validation report`

The ordered validation stages remain:

`hypothesis -> backtest -> walk_forward -> out_of_sample -> cost_stress -> regime_stress ->`
`parameter_sensitivity -> resampling -> risk -> candidate_decision`

`candidate_decision` is an output stage, not evidence that can be supplied to the engine.

## Verdict Semantics

### `validated_research_candidate`

All required stages are evidenced and every explicit gate passes. The candidate remains research-only
and receives no trading authority.

### `rejected`

All required evidence exists, but at least one explicit validation gate fails. The exact decision is
stored and linked to an immutable Strategy Graveyard entry that retains failed-gate evidence.

### `insufficient_evidence`

At least one required stage, metric, or bound quantitative evidence object is missing. Missing
evidence takes precedence over a rejected verdict and is never interpreted as a pass. The decision
is retained in validation memory but is not graveyarded.

## Reproducibility and Integrity Guarantees

Phase 2 completion requires:

- content-addressed validation cases and decisions;
- exact validation-policy digest binding;
- checksum-bound robustness/resampling evidence when those objects support gates;
- deterministic seeded resampling evidence;
- append-only validation decisions, graveyard entries, and reports;
- exact reload verification for persisted decisions and graveyard entries;
- report generation only when a fresh evaluation reproduces the supplied decision;
- rejected reports linked to the exact canonical graveyard entry;
- deterministic ordered gates and human-readable failure/missing reasons;
- no silent imputation of missing evidence.

## Safety Boundary at Phase 2 Completion

Phase 2 explicitly does **not** provide:

- protected final-test access;
- signal-session scheduling or unsolicited trade prompts;
- shadow execution authorization;
- paper execution authorization;
- broker order submission or cancellation;
- live capital allocation;
- position sizing;
- autonomous strategy/model promotion;
- authority to relax validation policy merely to obtain a passing verdict.

Every Phase 2 contract retains `execution_authority = "none"`. A validated research candidate is not
an executable strategy.

## Completion Gates

Phase 2 is complete only after the final Slice 5 pull request satisfies all of the following:

- all five implementation slices are squash-merged into `main`;
- validation-case and decision identities remain deterministic;
- deterministic resampling and checksum binding tests pass;
- validated, rejected, and insufficient-evidence paths pass integration coverage;
- rejected decisions alone create Strategy Graveyard entries;
- missing evidence is never represented as a pass or rejection;
- persisted decisions, graveyard entries, and reports are append-only and fail closed on conflict;
- validation reports reproduce the exact gate path and evidence lineage;
- report input substitution/cross-link mismatches fail closed;
- all Phase 2 outputs retain execution authority `none`;
- Ruff check and format check pass;
- Mypy passes for `src` and `tests`;
- full pytest coverage gate passes;
- diff whitespace checks pass;
- Betterleaks passes;
- CodeRabbit performs a genuine ready-for-review pass with no unresolved actionable finding;
- no protected-evaluation, shadow, paper, broker, live, position-sizing, or capital behavior changes.

When these gates pass and Slice 5 is squash-merged, Phase 2 implementation is complete. Phase 3
(Intelligence OS) remains a separate authorization boundary.
