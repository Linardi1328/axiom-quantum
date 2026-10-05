# Axiom Quantum Phase 1 - Research Core Completion

Status: Final implementation gates passed - complete upon squash merge of PR #58

Linear: `RIC-23`

## Purpose

This document closes Axiom Quantum Phase 1 once the final migration slice is merged. Phase 1 is a
research-only operating layer over the repository's existing market-data, research, backtest, and
historical Phase-3 infrastructure. Completion does not authorize shadow, paper, broker, or live
execution and does not open protected evaluation data.

## Implemented Slices

1. **Research Core Foundation (`RIC-7`, PR #50)**
   - canonical experiment/result contracts;
   - deterministic content identities;
   - append-only Axiom Research Memory;
   - execution authority fixed to `none`.
2. **Walk-Forward/OOS Bridge (`RIC-9`, PR #52)**
   - Phase-3 manifest/candidate adapters;
   - legacy research promotion capped at `validation_candidate`;
   - incomplete, leaky, or inconsistent promotion evidence fails closed.
3. **Canonical Backtest Evidence (`RIC-20`, PR #53)**
   - deterministic performance, cost, turnover, exposure, order, and fill evidence;
   - backtest results remain `research_only`.
4. **Reproducible Research Reporting (`RIC-21`, PR #54)**
   - deterministic human-auditable Markdown reports;
   - escaped untrusted prose and delimiter-safe scalar rendering;
   - report artifacts do not mutate result identity.
5. **Canonical Robustness Evidence (`RIC-22`, PR #55)**
   - explicit baseline and perturbation scenarios;
   - caller-declared metric direction;
   - coverage and direction-aware degradation summaries;
   - robustness results remain `research_only`.
6. **Research-Memory Migration and Completion (`RIC-23`)**
   - deterministic migration receipts;
   - idempotent persistence of selected canonical and Phase-3 research pairs;
   - exact stored-record verification and provenance preservation;
   - loaded migrated records remain reproducibly reportable.

## End-to-End Evidence Flow

The completed Phase 1 path is:

`legacy or native research input -> canonical ExperimentDefinition -> deterministic experiment ID -> canonical ExperimentResult -> deterministic result ID -> append-only ResearchMemoryRegistry -> reproducible research report`

Robustness and backtest evidence are inputs to research conclusions, not authorization artifacts.
Migration records selected historical evidence into the canonical memory layer but does not alter or
rewrite the historical source artifacts.

## Migration Guarantees

The migration layer must:

- require the result parent ID to match the canonical experiment definition;
- preserve the exact `StrategyResearchState` already produced by the source adapter;
- preserve Phase-3 source experiment provenance in canonical tags and the migration receipt;
- generate the same experiment, result, and migration receipt identities for equivalent inputs;
- make repeated migrations idempotent;
- detect an existing same-identity experiment whose stored non-scientific record differs and fail
  closed rather than silently accepting conflicting memory;
- re-load written records before returning a successful receipt;
- leave all historical Phase-3 artifacts untouched.

## Safety Boundary at Phase 1 Completion

Phase 1 explicitly does **not** provide:

- automatic strategy promotion beyond the pre-existing research state;
- protected final-test access;
- signal-session scheduling or unsolicited trade prompts;
- paper or shadow execution authorization;
- broker order submission or cancellation;
- live capital allocation;
- autonomous strategy/model self-promotion;
- package/repository renaming.

`ExperimentDefinition.execution_authority` remains fixed to `none`. A `validation_candidate` is a
research classification only and carries no trading authority.

## Completion Gates

Phase 1 is complete only after the final Slice 6 pull request satisfies all of the following:

- all six implementation slices are merged into `main`;
- deterministic identity and idempotency tests pass;
- migration conflict/corruption tests fail closed as intended;
- migrated Phase-3 provenance and research state are preserved;
- a report can be rendered from records re-loaded from Axiom Research Memory;
- Ruff check and format check pass;
- Mypy passes for `src` and `tests`;
- full pytest coverage gate passes;
- diff whitespace checks pass;
- Betterleaks passes;
- CodeRabbit reports no unresolved actionable finding;
- no execution, paper, shadow, broker, risk, or protected-evaluation behavior is changed.

When these gates pass and Slice 6 is squash-merged, Phase 1 implementation is complete and the
roadmap may proceed to Phase 2 validation work under a separate authorization boundary.
