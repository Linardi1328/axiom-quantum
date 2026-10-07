# Axiom Quantum Phase 5 - Paper Readiness and Recovery Specification

Status: Owner-authorized implementation

Linear: `RIC-42` through `RIC-46`

Authorized on: 2026-10-07

## Purpose

Phase 5 turns completed Phase 4 supervised decision-support evidence into a deterministic, explicitly human-invoked **offline paper-readiness and recovery layer**. It may verify exact stored Phase 4 lineage, evaluate the inherited Phase 5 safety gates, classify persisted paper-attempt recovery posture, retain append-only readiness memory, and render human-auditable readiness reports.

Phase 5 does **not** authorize paper-order submission. Every new Axiom Phase 5 public contract fixes `execution_authority = "none"`.

## Inherited Version 2 Safety Boundary

The existing `spy_market_agent.paper_ops` package and Version 2 Phase 5 specification remain authoritative safety inputs:

- **P5-A — infrastructure/readiness:** authorized.
- **P5-B — broker paper submission:** blocked pending separate owner authorization.
- **P5-C — model-connected paper operation:** blocked because no approved paper model exists.
- live-money trading remains prohibited.

Caller metadata, local flags, environment variables, UI state, or human prose cannot self-authorize P5-B or P5-C.

## Preconditions

Phase 5 admission requires one exact stored Phase 4 supervision report. The report, human disposition, review item, supervised session, Phase 3 report, Phase 3 assessment/evidence/session, and Phase 2 validated-research lineage must reload and verify before Phase 5 state may be created.

Phase 5 never reconstructs, substitutes, infers, weakens, or silently repairs missing Phase 4 or Phase 3 evidence.

## Phase 5 Slices

1. **Implemented - Paper Readiness Session Contract (`RIC-42`)**
   - exact stored Phase 4 supervision report as the parent;
   - explicit `human_requested` invocation source;
   - preserved Phase 4 review status and disposition;
   - deterministic `aq-paper-readiness-session-*` identity;
   - execution authority fixed to `none`.
2. **Implemented - Paper Readiness Gate Assessment (`RIC-43`)**
   - exact readiness-session parent;
   - immutable P5-A/P5-B/P5-C gate snapshot;
   - observed sources may be classified only as `offline_readiness_only`;
   - deferred, dismissed, and preserved abstention sources remain blocked;
   - no broker-readiness or execution-approval semantics.
3. **Implemented - Fail-Closed Recovery Case (`RIC-44`)**
   - exact readiness-assessment parent;
   - deterministic classification of established paper-attempt states;
   - uncertain states require reconciliation by deterministic client-order reference;
   - no automatic resubmission or broker lookup.
4. **Paper Readiness Memory (`RIC-45`)**
   - append-only session, assessment, and recovery-case persistence;
   - exact Phase 4 and Phase 5 parent-chain verification;
   - deterministic listings and fail-closed corruption/substitution handling.
5. **Readiness Reporting and Completion (`RIC-46`)**
   - deterministic checksum-bound human-auditable report;
   - explicit human-invoked end-to-end orchestration;
   - completion contract and merged-main verification.

## Authority Boundary

Phase 5 may not add or authorize:

- trade direction, signal generation, position target, quantity, sizing, leverage, margin, or allocation;
- order construction, approval, submission, cancellation, replacement, or mutation;
- broker account, position, order, clock, asset, or reconciliation calls;
- trading credentials or broker client construction;
- automatic resubmission after uncertainty;
- paper or live execution;
- model loading, model inference, strategy promotion, or protected-evaluation reopening;
- schedulers, daemons, recurrence, unattended loops, or background workers;
- unsolicited trade signals, prompts, notifications, or alerts;
- API/dashboard execution controls;
- live trading.

Every Phase 5 workflow remains explicitly human-invoked.

## Slice 1 Contract

Slice 1 introduces `PaperReadinessSession` and `build_paper_readiness_session`.

A paper-readiness session:

- embeds one canonical Phase 4 `SupervisionReportArtifact`;
- requires `load_supervision_report` to verify report bytes, canonical path, deterministic rendering, and the complete stored Phase 4/Phase 3 parent chain before admission;
- preserves exact Phase 4 report, disposition, review-item, supervised-session, Phase 3 report, experiment, verdict, review-status, and disposition identities;
- records one path-safe human invocation identifier;
- fixes `invocation_source` to `human_requested`;
- fixes `execution_authority` to `none`;
- derives `paper_readiness_session_id` from canonical content.

A Phase 4 abstention remains an abstention. A Phase 4 observational disposition remains observational. Slice 1 creates no paper proposal, order intent, execution approval, broker authority, or model admission.

## Slice 2 Contract

Slice 2 introduces `PaperReadinessAssessment`, `PaperReadinessGateSnapshot`, and `PaperReadinessOutcome`.

A readiness assessment:

- embeds one exact canonical `PaperReadinessSession` and re-verifies its stored Phase 4 report chain before construction;
- snapshots inherited P5-A, P5-B, and P5-C in that exact order;
- requires P5-A to remain authorized, P5-B to remain blocked pending separate owner authorization, and P5-C to remain blocked with no approved paper model;
- ignores caller-controlled metadata as an authorization source;
- maps only a Phase 4 `pending_human_review` + `observed` disposition to `offline_readiness_only`;
- maps deferred, dismissed, and preserved-abstention evidence to `blocked`;
- never represents offline readiness as broker readiness, execution approval, order approval, or model admission;
- derives `assessment_id` from canonical content and fixes execution authority to `none`.

## Slice 3 Contract

Slice 3 introduces `PaperRecoveryCase` and `build_paper_recovery_case`.

A recovery case:

- embeds one exact canonical `PaperReadinessAssessment` and re-verifies its stored Phase 4 report chain before construction;
- classifies only exact known paper-attempt states through the established Phase 5 recovery matrix;
- maps `reserved` and `submission_unknown` only to reconciliation-required evidence and derives the path-safe deterministic client-order reference from the sanitized persisted attempt's exact `client_order_id`; any caller-supplied cross-check must match it exactly;
- maps `accepted`, `broker_existing_order_found`, and `reconciled` only to terminal no-action evidence;
- maps `rejected` and `blocked` only to blocked evidence;
- rejects unknown, malformed, whitespace-mutated, or otherwise noncanonical attempt states fail closed instead of repairing them;
- records one path-safe operator reference;
- rejects client-order references on states that do not require reconciliation;
- derives `aq-paper-recovery-case-*` identity from canonical content;
- performs no broker lookup, order mutation, retry, cancellation, replacement, credential access, or automatic resubmission;
- fixes execution authority to `none`.

## Quality and Merge Gates

Every Phase 5 slice must satisfy all of the following on its exact final pushed commit before squash merge:

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
- no change expands Phase 5 beyond this specification.

Each next slice must branch from the verified squash-merged `main` commit of the preceding slice.

Phase 5 may be confirmed complete only after all five slices are squash-merged, the final merged `main` tree is re-verified, and the completion contract passes.
