# Axiom Quantum Phase 6 - Human-Confirmed Paper Execution Specification

Status: completion candidate; Slices 1-4 are merged and Slice 5 completes the implementation. Phase 6 is complete only after Slice 5 is squash-merged and merged-main verification passes.

Linear: `RIC-48` through `RIC-52`

Authorized on: 2026-10-07

## Purpose

Phase 6 connects verified Axiom Phase 5 paper-readiness evidence to the repository's existing reviewed paper-execution safeguards. It introduces a deterministic, explicitly human-invoked governance layer for paper-only submission and lookup-only reconciliation.

Phase 6 does not create autonomous trading. It does not create model-connected paper operation. It does not authorize live trading.

The existing Version 1 `PaperExecutionService`, repository, approval, risk, kill-switch, duplicate-protection, and Alpaca paper-only adapter contracts remain the execution safety boundary. Phase 6 binds Axiom lineage and human intent to those controls rather than rewriting or bypassing them.

## Preconditions

Phase 6 admission begins from one exact stored Phase 5 `PaperReadinessAssessment`.

The assessment and its complete stored Phase 5/4/3/2 lineage must reload and verify before Phase 6 state may be created. Admission requires:

- `outcome = offline_readiness_only`;
- inherited P5-A remains authorized;
- inherited P5-B remains blocked until a separate immutable Phase 6 submission authorization is created;
- inherited P5-C remains blocked because no approved paper model exists;
- no prior paper attempt is required or fabricated.

## Phase 6 Slices

1. **Human-Confirmed Paper Execution Session (`RIC-48`)**
   - exact stored Phase 5 readiness assessment parent;
   - explicit `human_requested` invocation;
   - deterministic `aq-paper-execution-session-*` identity;
   - execution authority remains `none`;
   - no broker, credential, order, scheduler, notification, or live path.
2. **Immutable Paper Submission Authorization (`RIC-49`)**
   - exact Phase 6 session parent;
   - exact immutable legacy `PaperOrderInstruction` and `PaperOrderApproval` binding;
   - single-use, content-addressed human authorization evidence;
   - no broker call in the authorization constructor;
   - P5-C remains blocked.
3. **Governed Paper Submission and Reconciliation (`RIC-50`)**
   - explicit human-invoked bridge to the existing `PaperExecutionService`;
   - injected `PaperBrokerProtocol` only;
   - exactly one submission call for one unused authorization;
   - uncertain states reconcile by lookup-only `client_order_id`;
   - never automatically resubmit.
4. **Paper Execution Audit Memory (`RIC-51`)**
   - append-only session, authorization, and outcome persistence;
   - exact Phase 5/6 lineage verification on every load;
   - deterministic listing and duplicate-consumption prevention;
   - fail-closed corruption, substitution, and conflict handling.
5. **Execution Reporting and Completion (`RIC-52`)**
   - deterministic checksum-bound human-auditable Phase 6 report;
   - explicit human-invoked end-to-end orchestration;
   - accepted, blocked/rejected, submission-unknown, reconciliation, duplicate-use, tamper, and deterministic replay coverage;
   - final Phase 6 completion contract and merged-main verification.

## Authority Boundary

Phase 6 may implement paper-only submission only through the existing reviewed execution service and only after exact immutable human authorization.

Phase 6 may not add or authorize:

- autonomous or unattended trading;
- scheduling, cron, daemons, recurrence, background workers, or unsolicited trade notifications;
- automatic approval or automatic resubmission;
- model-connected paper execution while P5-C is blocked;
- live trading or live broker endpoints;
- leverage, margin, short selling, fractional shares, new assets, or new order types;
- API or dashboard submission controls;
- cancellation, replacement, liquidation, bracket, OCO, or OTO behavior;
- credential persistence or logging.

Real paper credentials are never required by tests or repository validation. Production-like submission remains disabled by the existing configuration and durable kill-switch defaults unless the owner deliberately enables the existing runtime gates for a specific session.

## Slice 1 Contract

Slice 1 introduces `PaperExecutionSession` and `build_paper_execution_session`.

A Phase 6 execution session:

- embeds one canonical Phase 5 readiness assessment;
- requires `PaperReadinessMemoryRegistry.load_assessment` to verify exact stored Phase 5/4/3/2 lineage;
- requires `offline_readiness_only`;
- preserves exact assessment, readiness-session, Phase 4 supervision-report, and experiment identities;
- records one path-safe human invocation identifier;
- fixes `invocation_source` to `human_requested`;
- fixes `execution_authority` to `none`;
- derives its identity from canonical content.

Slice 1 creates no instruction, approval, broker client, order, execution permission, scheduler, or live capability.

## Slice 2 Contract

Slice 2 introduces `PaperSubmissionAuthorization` and
`build_paper_submission_authorization`.

A Phase 6 paper submission authorization:

- embeds one canonical Phase 6 execution session and re-verifies its exact stored Phase 5 assessment;
- binds one immutable legacy `PaperOrderInstruction` and one explicit approved
  `PaperOrderApproval` by exact canonical checksums without importing broker/service modules;
- preserves exact signal, client-order, instruction-fingerprint, and approval identities;
- fixes the authorization source to `human_confirmed`;
- fixes its use policy to `single_use` and its execution scope to `paper_only`;
- preserves P5-C as `blocked_no_approved_paper_model`;
- derives its identity from canonical content.

Slice 2 creates no broker client and performs no broker call. It grants no scheduler,
recurrence, autonomous loop, model-connected execution, or live authority. Single-use
consumption is enforced by Phase 6 audit memory before the execution bridge is invoked.

## Slice 3 Contract

Slice 3 introduces `PaperExecutionOutcome`, `submit_authorized_paper_order`, and
`reconcile_authorized_paper_order`.

The governed execution bridge:

- re-verifies the authorization's exact stored Phase 5 lineage before execution-side action;
- re-verifies the exact immutable legacy instruction/approval pair;
- requires a single-use submission claim before the reviewed legacy service is invoked;
- delegates submission exactly once to the existing `PaperExecutionService.submit_approved_order`;
- classifies accepted, rejected, blocked, and submission-unknown outcomes deterministically;
- reconciles only through `reconcile_by_client_order_id` using the authorization's exact
  client-order ID;
- never automatically resubmits and never constructs a broker client.

The broker remains an injected `PaperBrokerProtocol`. Slice 3 adds no scheduler, recurrence,
background worker, unsolicited notification, model-connected execution, live endpoint, or live
trading path.

## Slice 4 Contract

Slice 4 introduces `PaperExecutionMemoryRegistry` and immutable authorization-consumption
evidence.

Phase 6 audit memory:

- persists sessions and authorizations only after exact Phase 5/6 parent verification;
- consumes one authorization exactly once through an exclusive append-only claim artifact;
- permits one submission outcome and, only after `submission_unknown`, at most one lookup-only
  reconciliation outcome;
- reloads every record through canonical bytes and complete stored lineage;
- rejects duplicate use, conflicting outcomes, malformed identities, parent substitution, and
  tampered records fail closed.

Slice 4 adds no broker behavior, credential handling, scheduler, recurrence, automatic retry,
model-connected execution, or live trading.

## Slice 5 Contract

Slice 5 introduces checksum-bound Phase 6 execution reports plus explicit submission and
reconciliation workflow orchestration.

Phase 6 completion:

- renders immutable human-auditable reports over the exact stored session, authorization, and
  outcome chain;
- derives report identity from exact deterministic report bytes and verifies checksum, path,
  lineage, and content on reload;
- orchestrates a submission only from an explicit human invocation and injected
  `PaperExecutionService` / `PaperBrokerProtocol`;
- requires any reconciliation to be a separate explicit call and preserves lookup-only
  `client_order_id` behavior with no automatic resubmission;
- covers accepted, rejected, blocked, submission-unknown, reconciliation, duplicate-use, tamper,
  and deterministic replay behavior;
- preserves `paper_only` scope and
  `blocked_no_approved_paper_model` model-connected posture.

Slice 5 creates no scheduler, daemon, recurrence, background worker, unsolicited notification,
automatic approval, automatic resubmission, live broker path, leverage, shorts, fractional
shares, new assets, or new order types.

## Quality and Merge Gates

Every Phase 6 slice must pass on its exact final pushed commit before squash merge:

- focused tests;
- Ruff check;
- Ruff format check;
- Mypy over `src` and `tests`;
- full pytest coverage gate;
- diff whitespace checks;
- Betterleaks;
- genuine CodeRabbit review with no unresolved actionable finding;
- exact-source inspection and explicit approval;
- unchanged PR head after approval.

Each next slice must branch from the verified squash-merged `main` commit of the preceding slice.

Phase 6 is complete only after all five slices are squash-merged and the final merged `main` tree is re-verified.
