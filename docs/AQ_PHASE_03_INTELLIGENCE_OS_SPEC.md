# Axiom Quantum Phase 3 - Intelligence OS Specification

Status: Owner-authorized implementation

Linear: `RIC-30` and follow-on Phase 3 slices

Authorized on: 2026-10-05

## Purpose

Phase 3 turns the completed Axiom Research Core and Validation Engine into a deterministic,
human-invoked Intelligence OS. Its job is to bind an exact Phase 2 validated research candidate to
an exact point-in-time Market Intelligence run, summarize that intelligence as canonical evidence,
apply fail-closed decision-support gates, retain the result in append-only memory, and render a
human-auditable report.

Phase 3 remains decision support. It does not create a trading signal, order, risk approval,
execution permission, or autonomous operating loop.

The governing principle remains:

**AI interprets. Data measures. Statistics test. Risk controls constrain. Evidence decides.
Humans remain responsible.**

## Existing Baseline

Phase 3 reuses rather than replaces:

- Phase 1 canonical experiments/results, Research Memory, backtest/robustness evidence, and
  reproducible reporting;
- Phase 2 validation cases, policies, deterministic resampling, validation verdicts, Validation
  Memory, Strategy Graveyard, and validation reporting;
- the existing `spy_market_agent.intelligence` point-in-time contracts, SPY market state, scenario
  probabilities/actionability, historical analogues, cross-asset relationships, degradation
  monitoring, and deterministic Market Intelligence brief;
- all existing execution, paper-operation, risk, shadow, and protected-evaluation boundaries.

Only a Phase 2 `validated_research_candidate` may enter the Phase 3 session contract. That verdict
is still research-only and carries no execution authority.

## Phase 3 Implementation Slices

1. **Intelligence Session Contract (`RIC-30`)**
   - deterministic content-addressed session identity;
   - exact Phase 2 validated-decision lineage;
   - exact point-in-time Market Intelligence run lineage;
   - invocation source fixed to `human_requested`;
   - execution authority fixed to `none`.
2. **Canonical Market Intelligence Bridge**
   - deterministic evidence snapshot from an exact Market Intelligence brief;
   - scenario, market-state, data-quality, degradation, relationship, analogue, and limitation
     lineage retained without introducing execution semantics;
   - exact run/session cross-link validation.
3. **Decision-Support Gate Engine**
   - explicit Phase 3 decision-support policy;
   - fail-closed gates over candidate validation, data quality, market-state availability,
     scenario actionability, and degradation evidence;
   - terminal result limited to `present_for_human_review` or `abstain`;
   - no trade direction, quantity, position size, or order fields.
4. **Intelligence Memory**
   - append-only session, evidence, and decision-support records;
   - deterministic content identities and exact reload verification;
   - conflict/corruption/cross-link mismatches fail closed.
5. **Intelligence Reporting and Completion**
   - deterministic human-auditable Intelligence OS report;
   - append-only checksum-verified report persistence;
   - end-to-end human-invoked workflow orchestration;
   - integration coverage for human-review and abstention paths;
   - final Phase 3 completion contract.

## Slice 1 Contract

### Admission

An `IntelligenceSession` may be created only from a canonical Phase 2 `ValidationDecision` whose
verdict is `validated_research_candidate`. Rejected or insufficient-evidence decisions fail closed.

The session binds the exact validation decision identity, validation case, experiment, result,
validation policy, and policy digest. It also binds the exact Market Intelligence run identity,
target instrument, `as_of` timestamp, analysis profile, snapshot identities, code revision, and
configuration hash.

### Identity

The Phase 3 session identity is content-addressed as:

`aq-intel-session-<24 lowercase hex characters>`

Equivalent canonical inputs produce the same identity. A change to validation lineage,
point-in-time intelligence lineage, or human invocation identifier produces a different identity.

### Invocation Boundary

`invocation_source` has one legal value: `human_requested`.

The session contract contains no scheduler, recurrence, auto-run, daemon, webhook, or autonomous
invocation state. A later phase requires a separate authorization boundary before any such behavior
may be introduced.

## Safety Boundary

Phase 3 explicitly does **not** authorize or implement:

- protected final-test access or reopening;
- unsolicited trade prompts or signal-session scheduling;
- shadow or paper execution authorization;
- broker order submission, cancellation, replacement, or reconciliation;
- live-money trading;
- position sizing, leverage, capital allocation, or portfolio optimization;
- automatic strategy/model promotion;
- autonomous model retraining or strategy generation;
- network acquisition or new external dependencies;
- any output that bypasses the independent risk layer.

Every Phase 3 contract retains `execution_authority = "none"`. A future
`present_for_human_review` result is a decision-support result only, not an instruction to trade.

## Slice 1 Acceptance Gates

Slice 1 is acceptable only when:

- only `validated_research_candidate` Phase 2 decisions can create a session;
- the exact validation decision identity is retained and verified;
- Market Intelligence run lineage is copied exactly into the session;
- equivalent canonical inputs have deterministic session identity;
- changed validation or intelligence lineage changes session identity;
- invocation source cannot be changed from `human_requested`;
- execution authority cannot be changed from `none`;
- public intelligence-package exports are available;
- the intelligence package remains isolated from execution and paper-operation imports;
- focused tests pass;
- Ruff check and format check pass;
- Mypy passes for `src` and `tests`;
- full pytest coverage gate passes;
- diff whitespace checks pass;
- Betterleaks passes;
- CodeRabbit performs a genuine ready-for-review pass with no unresolved actionable finding.

Phase 3 is not complete when Slice 1 passes. All five slices and the Phase 3 completion gates must
pass before Phase 3 may be confirmed or approved.
