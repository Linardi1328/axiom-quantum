# Version 2 Phase 5 - Production Paper Operation Specification

Status: Specification + non-submitting safety/recovery scaffold active

Target future release identifier: `v2.0.0-beta.2`

Current Phase 5 branch: `review/v2-phase-05-production-paper`

Current package/runtime version: `2.0.0b1`

Public `v2.0.0-beta.2` tag: NOT CREATED

## 1. Purpose

Version 2 Phase 5 prepares the production paper-operation layer for SPY daily long-or-cash
workflows. This first substage creates the governing specification, documents inherited
Version 1 paper-execution controls, and adds deterministic non-submitting safety/recovery
contracts. It does not submit paper orders.

## 2. Current Baseline

Phase 5 begins from released Phase 4 Beta 1:

- Phase 4 public release: `v2.0.0-beta.1`.
- Release commit: `1c8feae478c0f5536b2193eeb408e580f3f7e33c`.
- Package/runtime version: `2.0.0b1`.
- PR #27, PR #28, PR #29, and PR #30 are merged.
- Owner Phase 4 acceptance is complete.
- Gate B model-connected shadow inference remains locked.
- Approved shadow model: none.
- Phase 3 scientific outcome: `NO CANDIDATE PROMOTION`.
- Protected evaluation: not executed.
- Live trading: prohibited.

## 3. Phase 4 Handoff

Phase 4 provides observation-only shadow infrastructure over verified local Phase 1 SPY
daily data. It can resolve XNYS sessions, enforce freshness and provider-finalization
evidence, persist shadow health/audit state, inspect schedule eligibility, and fail closed
when model admission is unavailable. It hands Phase 5 no approved model, no executable
proposal source, and no paper-order authorization.

## 4. Scientific/Model Admission Boundary

Phase 3 ended with `NO CANDIDATE PROMOTION`. Therefore:

- no approved shadow model exists;
- no approved paper model exists;
- no approved model-generated paper proposal source exists;
- protected evaluation has not been executed;
- structural metadata is not authorization.

Caller supplied values such as `approved=True`, `approved_for_paper=True`, or
`model_status="approved"` must never unlock paper-model operation. A future immutable,
trusted paper-model admission artifact may be specified later, but this PR must not invent
one as already populated.

## 5. Phase 5 Gate Model

GATE P5-A - Infrastructure Entry

- Status: `AUTHORIZED`.
- Allows: this specification, paper operational state contracts, recovery/reconciliation
  policy, runbooks, synthetic tests, dry-run/non-submitting inspection, and extension
  planning around the existing execution layer.

GATE P5-B - Broker Paper Submission By Phase 5 Workflow

- Status: `BLOCKED PENDING SEPARATE OWNER AUTHORIZATION`.
- This first PR must not submit, cancel, or reconcile a real broker order through the new
  Phase 5 layer.

GATE P5-C - Model-Connected Paper Operation

- Status: `BLOCKED_NO_APPROVED_PAPER_MODEL`.
- No model-generated paper proposal may reach execution.

No caller-controlled boolean, metadata field, environment variable, or CLI option may
self-authorize P5-B or P5-C.

## 6. Authorized Scope

This substage may add:

- the governing Phase 5 specification;
- non-submitting `spy_market_agent.paper_ops` typed contracts;
- deterministic Phase 5 gate evaluation;
- pure recovery-state classification for persisted paper attempts;
- operator recovery documentation;
- synthetic tests for gate and recovery behavior;
- documentation updates recording the Phase 4 release and Phase 5 status.

## 7. Explicit Non-Goals

This substage does not authorize:

- Alpaca paper-order submission by the new Phase 5 layer;
- model-connected paper operation;
- model loading or inference;
- protected evaluation;
- market-data acquisition;
- broker account, position, order, or reconciliation calls;
- automatic scheduling, daemons, background workers, or retry loops;
- API or dashboard write controls;
- live-money trading.

## 8. Existing V1 Execution Controls Being Inherited

Phase 5 must preserve the existing reviewed execution safeguards:

- explicit human approval;
- signal and order identity;
- instruction fingerprint binding;
- approval expiry;
- SPY-only enforcement;
- whole-share enforcement;
- long/cash-compatible risk controls;
- paper-only Alpaca adapter;
- `TradingClient(..., paper=True)`;
- fixed paper endpoint identity;
- configuration kill switch;
- durable database kill switch;
- broker environment verification;
- account validation;
- account-configuration validation;
- XNYS/broker-clock validation;
- asset validation;
- position validation;
- open-order validation;
- execution-time risk evaluation;
- reservation before submission;
- duplicate protection;
- same-session protection;
- client-order-ID lookup;
- broker existing-order detection;
- submission-unknown state;
- reconciliation by `client_order_id`;
- no automatic resubmission after uncertainty.

Phase 5 builds around these controls and must not rewrite or weaken them.

## 9. Paper Environment Isolation

Paper operation remains isolated to the canonical Alpaca paper environment when a later
substage authorizes actual submission. Live endpoints are prohibited. A configurable
`paper=False` path, live base URL, or live account mode is not allowed.

## 10. Credential Isolation

This first Phase 5 PR requires no credentials. New Phase 5 modules must not read trading or
market-data secret environment variables, instantiate settings, or construct broker clients
at import time. Future paper drills must use locally configured paper credentials only after
separate review and owner authorization.

## 11. Proposal-Source Admission

Phase 5 recognizes that not all well-formed proposals are authorized proposals. A future
paper operation must prove proposal source, model admission, data lineage, and owner
approval independently. Current status is `BLOCKED_NO_APPROVED_PAPER_MODEL`.

## 12. Immutable Owner Approval

Owner approval must be immutable and bound to the exact paper-order instruction. Approval
must not be inferred from mutable runtime flags, request metadata, dashboards, or local
operator text that is not part of the approved approval contract.

## 13. Order Fingerprinting

The order instruction fingerprint binds symbol, side, quantity, signal session, execution
session, risk decision, and cost assumptions. Any mismatch between approval and instruction
fails closed.

## 14. Pre-Trade Validation

Future authorized submission must retain all existing pre-trade checks: paper mode,
disabled dry run, kill switches, credentials, environment, account, account configuration,
clock/session, asset, position, open order, approval, stale instruction, and risk validation.

## 15. Execution-Time Risk

Execution-time risk remains independent from model output and approval records. A proposed
trade may reach paper submission only when the risk layer approves it at execution time.
Models may never bypass risk limits.

## 16. Position And Open-Order Reconciliation

Future authorized submission must verify current positions and open orders immediately
before reservation/submission. Unexpected SPY positions, short exposure, insufficient shares
for sells, or conflicting open orders fail closed.

## 17. Durable Paper State

The existing SQLite paper-execution ledger remains the durable source of paper attempt
state. Phase 5 must not change the schema in this first substage. Runtime SQLite files are
not committed.

## 18. Attempt-State Machine

The existing paper attempt states are:

- `reserved`;
- `broker_existing_order_found`;
- `accepted`;
- `rejected`;
- `submission_unknown`;
- `reconciled`;
- `blocked`.

The state machine is owned by the existing execution repository. Phase 5 only classifies
operator recovery posture for these persisted states.

## 19. Idempotency

`client_order_id`, signal identity, approval identity, instruction fingerprint, and
execution session uniqueness provide idempotency boundaries. A consumed session or prior
client order ID must not be bypassed by generating a replacement order automatically.

## 20. Concurrency

Concurrent attempts must be resolved by durable reservation semantics. At most one same
session attempt can win. Losing attempts must not reach broker submission.

## 21. Unknown Submission Outcomes

Uncertainty never means retry the order. Crash, timeout, cancellation, transport exception,
broker snapshot mismatch, local persistence failure after possible broker receipt, or
database failure after broker interaction must become `submission_unknown`,
`RECONCILIATION_REQUIRED`, or an equivalent fail-closed state.

## 22. Recovery Policy

Recovery uses deterministic `client_order_id` as the primary lookup key. The operator must
first establish whether a broker order exists, then preserve evidence and update local state
through separately authorized reconciliation logic. Automatic resubmission is prohibited.

## 23. Reconciliation Policy

Reconciliation compares broker evidence against the original instruction fingerprint and
expected paper environment. Matching evidence may recover an incomplete attempt as
`reconciled`. Missing, unavailable, mismatched, or ambiguous broker evidence fails closed
and requires manual investigation.

## 24. No Automatic Resubmission Rule

No Phase 5 recovery disposition may mean "retry the order" or "submit an order." Recovery
may classify, inspect, and require operator reconciliation only.

## 25. Restart/Crash Semantics

After restart, `reserved` and `submission_unknown` attempts require reconciliation before
any future paper activity for the same logical order/session. Terminal states remain
terminal. Unknown states fail closed.

## 26. Broker-Unavailable Semantics

If broker lookup is unavailable, unavailable evidence does not prove no order exists. The
operator must preserve local evidence, keep paper operation blocked, and retry lookup only
as an investigation step, not as a submission retry.

## 27. Database-Unavailable Semantics

If local paper state is unavailable or corrupted, paper operation must stop. The operator
must preserve logs and database files for investigation. Broker calls after local state loss
require separate owner-approved recovery handling.

## 28. Kill-Switch Semantics

Configuration and durable kill switches fail closed. Disengagement requires explicit
confirmation and audit reason. A kill-switch read failure is treated as engaged.

## 29. Operator Workflow

The current operator workflow is documentation and offline inspection only:

1. Review the Phase 5 gate status.
2. Inspect persisted attempt status locally if evidence exists.
3. Use the recovery runbook for `reserved` or `submission_unknown`.
4. Do not contact brokers or submit orders through this first Phase 5 scaffold.
5. Stop and escalate if state is unknown, corrupted, or inconsistent.

## 30. Audit/Event Requirements

Future authorized paper operation must emit durable, sanitized events for gate decisions,
approval validation, reservation, broker lookup, submission, receipt persistence,
submission uncertainty, reconciliation, blocked states, kill-switch actions, and incident
handling. Evidence must exclude credentials, account IDs, raw provider responses, personal
paths, and runtime databases.

## 31. Monitoring/Alerts

Monitoring must distinguish healthy terminal states from unresolved attempts. `reserved`,
`submission_unknown`, invalid state, database failure, and broker mismatch require local
operator alerts. Alerts must not trigger automatic submission.

## 32. Incident-Response Expectations

Incidents require preserving local database snapshots, command output, sanitized logs,
client order IDs, timestamps, and operator notes. If broker evidence is unclear, stop and
perform manual investigation.

## 33. Paper Observation/Evidence Requirements

Future controlled owner paper drills must record sanitized evidence only: run ID, client
order ID, session, gate states, local attempt status, broker lookup result classification,
receipt classification, reconciliation result, and quality gates. Do not record credentials,
account identifiers, raw broker payloads, real order IDs unless explicitly sanitized, or
personal machine metadata.

## 34. Synthetic Testing Policy

Automated tests for this substage must be synthetic and offline. Tests must verify gates,
self-authorization resistance, recovery classification for every existing attempt state,
static isolation of the new package, no scheduler, no credential dependency, no broker
dependency, and no package-version bump.

## 35. Future Controlled Owner Paper Drill

A later substage may propose a controlled owner paper drill only after separate owner
authorization. That drill must still be paper-only, SPY-only, whole-share, non-live,
operator-controlled, fully audited, and blocked from model-generated paper proposals unless
P5-C is separately unlocked by a trusted admission artifact.

## 36. Acceptance Criteria

This first Phase 5 PR is acceptable only if:

- Phase 4 Beta 1 is recorded as released.
- This governing specification exists.
- Existing Version 1 paper safety controls are documented and preserved.
- No existing paper safety gate is weakened.
- P5-A is authorized.
- P5-B remains blocked.
- P5-C remains blocked.
- Recovery rules never automatically resubmit uncertain orders.
- Unknown state fails closed.
- The new scaffold is offline and non-submitting.
- No credentials are required.
- No scheduler exists.
- No live path exists.
- Package version remains `2.0.0b1`.
- No `v2.0.0-beta.2` tag exists.
- Full quality gates pass with coverage at least 85%.

## 37. Release/Version Policy

This substage keeps package/runtime version `2.0.0b1`. The future public identifier is
`v2.0.0-beta.2`, but the tag is not created here. The package should move to `2.0.0b2`
only during a later reviewed Phase 5 release-preparation branch after implementation,
owner acceptance, review, merge, and explicit tag authorization.

## 38. Explicit Live-Trading Prohibition

Live-money trading is prohibited. Phase 5 must not add live broker configuration, live
credentials, live endpoints, margin, leverage, short selling, options, crypto, futures,
additional symbols, API mutation routes, dashboard execution controls, or any path that can
submit a live order.

## Attempt-State Recovery Matrix

| Existing attempt state | Recovery disposition | Operator meaning |
| --- | --- | --- |
| `reserved` | `RECONCILIATION_REQUIRED` | Reservation exists, submission may or may not have progressed. Reconcile before any future action. |
| `submission_unknown` | `RECONCILIATION_REQUIRED` | Submission outcome is uncertain. Lookup by `client_order_id`; never retry automatically. |
| `accepted` | `NO_ACTION_TERMINAL` | Broker accepted receipt is recorded. Do not resubmit. |
| `broker_existing_order_found` | `NO_ACTION_TERMINAL` | Existing broker order matched the instruction. Do not resubmit. |
| `reconciled` | `NO_ACTION_TERMINAL` | Recovery already resolved the attempt. Do not resubmit. |
| `blocked` | `BLOCKED` | Attempt failed closed before definitive submission. Do not resubmit automatically. |
| `rejected` | `BLOCKED` | Broker definitively rejected the attempt. Do not resubmit automatically. |
| unknown/unrecognized | `INVALID_STATE` | Fail closed and require manual investigation. |

## Failure Matrix

| Failure case | Required outcome |
| --- | --- |
| invalid approval | Block before submission. |
| expired approval | Block before submission. |
| kill switch engaged | Block before submission. |
| wrong execution mode | Fail closed; live mode is a runtime error. |
| broker environment mismatch | Block before submission. |
| account blocked | Block before submission. |
| wrong account configuration | Block before submission. |
| market closed | Block before submission. |
| wrong session | Block before submission. |
| stale instruction | Block before submission. |
| unsupported symbol | Block before submission. |
| unexpected position | Block before submission. |
| unexpected open order | Block before submission. |
| risk rejection | Block before submission. |
| duplicate attempt | Block before submission. |
| same-session collision | Block before submission. |
| database lock/failure | Fail closed; preserve local evidence. |
| pre-submission clock failure | Block reserved attempt; no submission. |
| broker request construction failure | Block reserved attempt; no submission reached broker. |
| broker rejection | Record rejected; no automatic retry. |
| transport uncertainty | Mark submission outcome uncertain; reconcile by `client_order_id`. |
| cancellation during submission | Mark submission outcome uncertain; reconcile by `client_order_id`. |
| unknown submission outcome | Reconciliation required; no automatic retry. |
| broker snapshot mismatch | Reconciliation required; no automatic retry. |
| accepted order receipt persistence failure | Reconciliation required; no automatic retry. |
| reconciliation lookup failure | Keep reconciliation required; fail closed. |
| reconciliation mismatch | Keep or mark submission unknown; fail closed. |
| reconciliation persistence failure | Preserve evidence; fail closed. |

For every uncertain case: FAIL CLOSED.
