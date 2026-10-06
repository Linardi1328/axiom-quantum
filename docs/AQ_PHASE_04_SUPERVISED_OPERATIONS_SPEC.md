# Axiom Quantum Phase 4 - Supervised Operations Specification

Status: Owner-authorized implementation

Linear: `RIC-36` through `RIC-40`

Authorized on: 2026-10-07

## Purpose

Phase 4 turns the completed Phase 3 Intelligence OS into a deterministic, explicitly human-invoked supervision layer. It lets a human open a review session over one exact stored Phase 3 report, place that report into an immutable review queue, record an observational human disposition, retain append-only supervision memory, and render a checksum-verified supervision report.

Phase 4 does not authorize trading or execution. Every public Phase 4 contract fixes `execution_authority = "none"`.

## Preconditions

Phase 4 admission requires the completed Phase 3 lineage to already exist in storage. The exact Phase 3 report, assessment, evidence, Intelligence Session, and Phase 2 validation decision must reload and verify before Phase 4 may build state from them.

Phase 4 never reconstructs, substitutes, infers, weakens, or silently repairs missing Phase 3 evidence.

## Phase 4 Slices

1. **Implemented - Supervised Session Contract (`RIC-36`)**
   - one exact stored Phase 3 report as the parent;
   - explicit `human_requested` invocation source;
   - preserved Phase 3 terminal verdict;
   - deterministic `aq-supervision-session-*` identity;
   - execution authority fixed to `none`.
2. **Planned - Supervised Review Queue (`RIC-37`)**
   - one immutable review item per exact supervised session;
   - `present_for_human_review` becomes `pending_human_review`;
   - `abstain` becomes a non-reviewable preserved abstention;
   - no trade-proposal or execution semantics.
3. **Planned - Human Review Disposition (`RIC-38`)**
   - immutable human-only observational disposition;
   - supported outcomes: `observed`, `deferred`, `dismissed`, and `abstention_acknowledged`;
   - abstentions may only be acknowledged and never upgraded;
   - no execution approval semantics.
4. **Planned - Supervision Memory (`RIC-39`)**
   - append-only session, review-item, and disposition persistence;
   - exact stored parent-chain verification;
   - conflict, corruption, substitution, and tamper rejection.
5. **Planned - Supervision Reporting and Completion (`RIC-40`)**
   - deterministic human-auditable supervision report;
   - append-only checksum-verified report persistence;
   - explicit human-invoked end-to-end orchestration over stored Phase 3 lineage;
   - final Phase 4 completion contract.

## Authority Boundary

Phase 4 is supervised decision support only. It does not add or authorize:

- trade direction or signal generation;
- position targets, quantity, sizing, leverage, margin, or allocation;
- order construction, approval, submission, cancellation, replacement, or reconciliation;
- broker clients, credentials, endpoints, or network calls;
- paper or live execution;
- strategy or model promotion;
- protected-evaluation reopening;
- schedulers, daemons, recurrence, background loops, or unattended operation;
- unsolicited trade signals, prompts, notifications, or alerts.

A human review disposition records only that a human reviewed, deferred, dismissed, or acknowledged an existing decision-support record. It never grants downstream authority.

## Human Invocation Contract

Every Phase 4 workflow begins with an explicit function call carrying a human-supplied invocation identifier. `invocation_source` is the literal `human_requested`.

The phase contains no internal timer, polling loop, recurring job, background worker, notification trigger, or startup hook capable of creating a session without a human request.

## Phase 3 Verdict Preservation

Phase 4 must preserve the exact Phase 3 decision-support verdict.

### `present_for_human_review`

The report may become a pending human-review item. Human review can record an observational disposition, but the result still grants no execution authority.

### `abstain`

The abstention remains terminal for the underlying Intelligence OS assessment. Phase 4 may only retain and acknowledge it. It may not create a review-cleared state, change a failed gate, or produce an alternative actionable outcome.

## Determinism and Integrity

For identical canonical parents and identical explicit human inputs, Phase 4 identities and rendered artifacts must be identical.

Phase 4 requires:

- content-addressed identities for sessions, review items, dispositions, and reports;
- exact Phase 3 report and parent-chain binding;
- append-only persistence once memory is introduced;
- deterministic sorted listings;
- checksum verification for report bytes;
- conflict and corruption rejection;
- fail-closed handling of missing, substituted, malformed, or tampered parents;
- no inferred replacement for absent evidence.

## Slice 1 Contract

Slice 1 introduces `SupervisedSession` and `build_supervised_session`.

A supervised session:

- embeds one canonical Phase 3 `IntelligenceReportArtifact`;
- references the exact report, assessment, evidence, Phase 3 Intelligence Session, and experiment identifiers;
- preserves the exact Phase 3 decision-support verdict;
- records one path-safe human invocation identifier;
- fixes `invocation_source` to `human_requested`;
- fixes `execution_authority` to `none`;
- derives `supervision_session_id` from canonical content.

The builder must call the Phase 3 report loader before admission so checksum, path, deterministic rendering, and the complete stored Phase 3 parent chain are verified.

## Quality and Merge Gates

Every Phase 4 slice must satisfy all of the following on its exact final pushed commit before squash merge:

- focused tests pass;
- Ruff check passes;
- Ruff format check passes;
- Mypy passes for `src` and `tests`;
- full pytest coverage gate passes;
- diff whitespace checks pass;
- Betterleaks passes;
- CodeRabbit completes a genuine ready-for-review pass with no unresolved actionable finding;
- the exact pushed commit is inspected and explicitly approved;
- the PR head is unchanged after approval;
- no change expands Phase 4 beyond this specification.

Each next slice must branch from the verified squash-merged `main` commit of the preceding slice.

Phase 4 may be confirmed complete only after all five slices are squash-merged, the final merged `main` tree is re-verified, and the completion contract passes.
