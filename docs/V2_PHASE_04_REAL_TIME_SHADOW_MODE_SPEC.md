# Version 2 Phase 4 - Real-Time Shadow Mode Specification

Status: Accepted and released as `v2.0.0-beta.1`

Target release identifier: `v2.0.0-beta.1`

Release-preparation branch: `review/v2-phase-04-beta1-release-preparation`

Released package/runtime version: `2.0.0b1`

Public tag: `v2.0.0-beta.1` CREATED at
`1c8feae478c0f5536b2193eeb408e580f3f7e33c`

Gate B: LOCKED

Version 2 Phase 3 is complete and released as `v2.0.0-alpha.3`. Its scientific outcome was
`NO CANDIDATE PROMOTION`. No Phase 3 model is approved for protected evaluation, shadow
operation, production paper operation, or live trading. PR #27 merged the approved Phase 4
specification and infrastructure-first scaffold. PR #28 merged Observation-Only Operational
Pipeline V1. PR #29 merged Scheduled Observation Operations V1. The owner completed Phase 4
acceptance testing, including a sanitized real SPY observation-only smoke test on the
accepted Phase 1 parent dataset. PR #30 merged Beta 1 release preparation, and the public
`v2.0.0-beta.1` tag was created after owner approval. In this document, "scheduled" means
deterministic, schedule-aware, operator-triggered observation orchestration only. This
authorization permits deterministic target-session resolution, schedule eligibility
evaluation, durable shadow-history inspection, already-processed detection,
missed-observation detection, operator-triggered schedule preview, operator-triggered
run-due observation, exact reuse of the existing Observation Pipeline V1 runner, and
synthetic calendar/schedule tests. It does not authorize an unattended daemon, cron job,
cloud scheduler, background worker, continuously running process, model inference, or any
bypass of the Phase 3 model rejection result.

## 1. Purpose

Phase 4 defines and begins the shadow-mode operating layer for SPY daily market
intelligence. The goal is to build calendar-aware, reproducible, observable infrastructure
that can later run approved model inference against newly completed daily sessions without
submitting orders.

This Phase 4 substage is not a complete production shadow system. It establishes
terminology, governance, safety boundaries, deterministic identities, model-admission locks,
freshness checks, scheduling policy functions, monitoring state, alert records, durable
shadow SQLite persistence, manual run-once observation execution, read-only inspection,
operator-triggered schedule-aware orchestration, and synthetic tests.

## 2. Background

Version 1 created the SPY daily research, backtesting, read-only API/dashboard, and
paper-only execution safeguards. Version 2 Phase 1 added verified real SPY daily data
acquisition and manifests. Version 2 Phase 2 created a controlled real historical benchmark
and opened one protected final test. Version 2 Phase 3 added walk-forward research and a
development campaign that rejected all candidates under predeclared promotion gates.

Phase 4 therefore begins without an approved shadow model. It may build operational
infrastructure that observes data readiness and would be capable of admitting a future
model, but model-connected real-data inference remains locked.

## 3. Phase 3 Handoff

The Phase 3 handoff contains three binding facts:

- Phase 3 was accepted and released as `v2.0.0-alpha.3`.
- The owner-run development campaign ended with `NO CANDIDATE PROMOTION`.
- Protected evaluation was not executed, and no candidate was admitted to shadow mode.

These facts mean Phase 4 must default to `NO APPROVED SHADOW MODEL`. The Phase 2 selected
model, the highest-ranked Phase 3 development candidate, a dummy model, or any fallback model
must not be silently substituted for a separately approved shadow model.

## 4. Entry Gates

Phase 4 has two gates.

Gate A - Infrastructure Entry is satisfied for this branch because Phase 3 is complete and
released, PR #27 merged the specification/scaffold, PR #28 merged Observation-Only
Operational Pipeline V1, and the owner explicitly authorized Scheduled Observation
Operations V1. Gate A permits shadow architecture, verified local Phase 1 manifest
consumption, market-data readiness policy, freshness and completeness controls, calendar and
scheduling policy functions, deterministic idempotency, dedicated shadow persistence,
monitoring, local alert records, run-state management, read-only inspection,
model-admission locks, synthetic tests, manual run-once observation execution, and
operator-triggered schedule-aware orchestration.

Gate B - Model-Connected Shadow Inference is not satisfied. It requires a separately
approved model candidate with immutable approved metadata. Until Gate B is satisfied,
real-data model inference must refuse to run, no model-generated `LONG` or `CASH` proposal
may be created from real market data, no fallback model may be substituted, and no dummy
model may be used outside synthetic tests.

## 5. Explicit Non-Goals

Phase 4 specification/scaffolding does not authorize:

- Phase 3 protected evaluation execution;
- model promotion;
- strategy threshold optimization;
- strategy candidate selection;
- production paper operation;
- live-money trading;
- new paper-order behavior;
- broker communication;
- automatic scheduling, daemon, worker, or cron integration;
- API write routes or dashboard execution controls;
- new assets, intraday bars, tick data, options, futures, crypto, or multi-asset support;
- cloud services, external model APIs, AutoML, deep learning, or new major model
  dependencies;
- `v2.0.0-beta.1` tag creation before post-merge owner approval.

Phase 4 Observation-Only Operational Pipeline V1 specifically does not authorize model
artifact loading, model inference, `LONG`/`CASH` model signals, strategy inference,
risk-based order sizing, `ShadowProposal` generation from operational runs, paper order
submission, broker communication, trading credential reads, market-data acquisition,
unattended scheduling, API mutation routes, Streamlit execution controls, Phase 5 behavior,
protected evaluation, or live trading.

Phase 4 Scheduled Observation Operations V1 specifically does not authorize cron,
APScheduler, Celery, RQ, background threads, daemon processes, systemd, launchd, GitHub
Actions schedules, cloud schedulers, continuous loops, automatic market-data acquisition,
model loading, model inference, `ShadowProposal` generation, `LONG`/`CASH` signals, paper
order submission, broker communication, Phase 5 behavior, protected evaluation, or live
trading.

## 6. Shadow-Mode Terminology

Real-Time Shadow Mode means the system operates according to the real exchange calendar and
newly completed daily market sessions. It does not mean intraday trading. SPY remains the
only symbol, `1Day` remains the only timeframe, and XNYS daily sessions remain the calendar
unit.

`observation_only_no_model` is the initial permitted mode. It may inspect local or synthetic
session readiness, compute deterministic run identities, record health state, and explain
why inference is unavailable. It must not generate model predictions, trade signals, broker
orders, or risk-approved orders.

`model_connected` is the future mode that would score an approved model. It is locked until
Gate B is separately satisfied.

## 7. Architecture

Phase 4 introduces the `spy_market_agent.shadow` package as a separate boundary from
research, execution, API, dashboard, and persistence. Initial modules define typed shadow
metadata, deterministic run identity, market-data readiness decisions, schedule decisions,
model admission, monitoring state, and run-policy decisions.

Allowed dependencies for this scaffold are existing project dependencies and project-owned
market-data calendar/checksum utilities. The shadow package must not import the paper
execution service, Alpaca TradingClient, broker adapters, approval services, or order
submission services.

## 8. Trust Boundaries

Shadow inputs are untrusted until their lineage, calendar semantics, freshness, completeness,
and model-admission status are validated. Missing or uncertain critical information is
`blocked`, not silently degraded.

Research artifacts are not runtime model approvals. A future model admission record must be
separate, immutable, auditable, and explicit. Shadow proposals are not executable orders.
Risk preview may inspect a hypothetical target in a later approved substage, but it must not
authorize or submit orders.

## 9. Market-Data Contract

The Phase 4 market-data contract remains:

- symbol exactly `SPY`;
- timeframe exactly `1Day`;
- exchange calendar exactly `XNYS`;
- market timezone `America/New_York`;
- one provider/feed lineage per snapshot;
- explicit adjustment policy, with `all` expected for primary Phase 1 lineage-derived data
  unless a later approved specification changes it;
- canonical checksum and dataset identity recorded;
- OHLCV validation enforced before shadow eligibility.

The shadow runner must not acquire data in this branch. Future real market-data shadow
ingestion may use the Phase 1 market-data credential boundary only after separate review.
Trading credentials are never required for shadow infrastructure.

Observation-Only Operational Pipeline V1 consumes only an already acquired and verified
local Version 2 Phase 1 dataset manifest. The runner must invoke the existing deep Phase 1
manifest verifier before loading canonical bars, verify symbol `SPY`, timeframe `1Day`,
calendar `XNYS`, and adjustment `all`, and fail closed on checksum, lineage, raw/canonical
artifact, manifest, session, or OHLCV inconsistencies. It must not accept arbitrary CSV input
that bypasses the manifest.

## 10. Daily-Session Semantics

The signal session is the completed daily SPY session whose close is known. Features, if a
future approved model exists, may use information only through that session close.
Prediction occurs after session completion and provider finalization. Any executable entry
would remain no earlier than a later reviewed next-session path; this branch creates no
execution path.

Weekends and exchange holidays are not missing sessions. Non-XNYS dates are ineligible
shadow sessions. Incomplete current-session candles are rejected.

Every manual `run-observation` targets exactly one explicit `--session YYYY-MM-DD`. That
session must be a valid XNYS session, exist exactly once in the verified canonical dataset,
match the latest canonical session represented by the operational snapshot, be complete at
the supplied `--as-of` timestamp, and contain valid canonical OHLCV data. The command must
not silently fall back to another date or use calendar-day arithmetic.

Scheduled Observation Operations V1 does not accept an arbitrary target session. Its
operator-triggered commands derive the due target session deterministically from an explicit
timezone-aware UTC `--as-of` timestamp using the approved XNYS calendar adapter. The target
is the latest XNYS session that has completed as of `as_of`, including weekends, exchange
holidays, and early-close sessions. Naive timestamps, non-UTC timestamps, and calendar
uncertainty fail closed.

## 11. Freshness Policy

Freshness and completeness are independent. A dataset can be complete but stale, or fresh in
timestamp but incomplete. Both must be checked explicitly.

Freshness requires:

- target session is a valid XNYS session;
- the target session is complete at the injected `as_of` timestamp;
- provider finalization metadata says the daily bar is final;
- expected session data is present;
- no duplicate or out-of-order sessions are detected;
- OHLCV validation passed;
- data is not stale under the configured policy.

Provider finalization delay is not invented by this specification. It must be explicit
metadata or configuration supplied by a later approved ingestion design.

For Observation-Only Operational Pipeline V1 the operator must provide explicit UTC
`--as-of` evidence and explicit provider finalization evidence such as `--provider-finalized`
plus `--provider-finalization-policy-id`. If finalization is not confirmed, the run is
blocked with `provider_not_finalized`. If the target dataset session is older than the
latest XNYS session that should be complete at `as_of`, the run is blocked with
`stale_data`.

For Scheduled Observation Operations V1, the verified canonical snapshot's latest session
must exactly match the latest completed XNYS target session. If the latest canonical session
is older than the due target, the schedule decision is blocked with `stale_data`. If the
latest canonical session is newer than the latest completed XNYS session, the decision fails
closed with `data_ahead_of_completed_session`. The schedule layer must not use a future or
current incomplete row as input, and it must not call acquisition as a fallback.

## 12. Completeness Policy

Completeness requires the expected daily session row and every required OHLCV field for the
shadow input snapshot. Missing expected data, duplicate sessions, out-of-order sessions,
non-finite prices, non-positive prices, negative volume, and OHLC inconsistencies are
blocking failures.

Completeness failures must produce deterministic machine-readable reasons such as
`missing_session`, `duplicate_session`, `out_of_order_sessions`, or `invalid_ohlcv`.

## 13. Exchange-Calendar Policy

The approved exchange calendar is XNYS through the project `XNYSCalendar` adapter. The
system must answer whether a target date is a valid session and whether it has completed at
a timezone-aware timestamp. Calendar uncertainty, unsupported dates, naive timestamps, or
out-of-range calendar queries fail closed.

## 14. Scheduling Policy

This branch must not create an unattended OS-level scheduler, daemon, or background worker.
It may implement deterministic policy functions that answer:

- whether today or a target date is an XNYS session;
- whether the target session is complete;
- whether provider finalization eligibility is satisfied;
- whether the logical session/configuration was already processed;
- whether the system is observation-only or model-enabled;
- whether a run is eligible;
- why a run was refused.

Observation-Only Operational Pipeline V1 wires these checks into explicit manual run-once
operation. Scheduled Observation Operations V1 adds deterministic schedule-aware
orchestration only:

- resolve the due target session from explicit UTC `as_of`;
- verify local Phase 1 manifest lineage before declaring a run eligible;
- compare the due target with the latest canonical session;
- inspect durable shadow history;
- identify already-processed, recovery-required, stale, provider-not-finalized, and
  data-ahead states;
- surface missed shadow-observation history without backfilling it;
- preview schedule status without creating or mutating runs;
- call the existing observation runner at most once when the current due session is
  eligible.

The schedule layer must not become a second observation engine. It must not duplicate
manifest verification, OHLCV validation, freshness evaluation, shadow run construction,
durable reservation, run finalization, health persistence, or alert persistence.
Unattended scheduled operation still requires a later reviewed Phase 4 authorization.

## 15. Run-Once Contract

The initial run-once contract is manual and local. It evaluates a target session, a verified
Phase 1 data snapshot lineage, `observation_only_no_model` mode, explicit provider
finalization evidence, and configuration metadata once. It may emit readiness and health
state and persist a local audit record. In `model_connected` mode it must fail before any
model loading or inference.

No run-once path may acquire data, construct a broker, submit an order, initialize paper
execution, or contact a trading API.

`run-due-observation` is also a run-once command. It performs at most one schedule decision
and at most one delegated observation attempt per invocation. It must not loop, sleep, poll,
retry, backfill historical sessions, or create unattended operation.

## 16. Idempotency

Each shadow run identity must be deterministic from stable inputs:

- symbol;
- signal session;
- mode;
- data snapshot lineage and canonical checksum;
- feature schema;
- model identity and checksum when applicable;
- configuration version;
- provider finalization policy identifier.

Clock time, username, hostname, absolute local paths, credentials, random UUIDs, and
unstructured operator notes must not define shadow identity. A duplicate logical run must be
detected and fail closed rather than silently creating another record. Rejected duplicate
attempts must append local health-event and alert audit evidence without changing the
existing run lifecycle or overwriting the existing input snapshot.

Schedule orchestration reuses the same underlying `shadow_run_id` as manual observation for
identical manifest, target session, `as_of`, provider-finalization policy, configuration,
and data lineage. A manual `run-observation` duplicate remains a rejected duplicate attempt
and may append duplicate audit evidence. An idempotent `run-due-observation` invocation that
finds an already terminal run is a safe schedule no-op: it must not create another
`shadow_runs` row, input snapshot, duplicate health event, or duplicate alert.

## 17. Model-Admission Gate

Model-connected shadow mode requires immutable metadata:

- model ID;
- experiment or campaign ID;
- model artifact checksum;
- feature schema;
- label schema where relevant;
- Git/source lineage;
- approval status;
- `approved_for_shadow = true`.

The current state is `NO APPROVED SHADOW MODEL`. During this specification/scaffolding
substage, every runtime model-connected request must raise a typed error with
machine-readable status `blocked_no_approved_model`, even if caller-supplied metadata is
structurally valid and self-declares `approved_for_shadow = true`. Synthetic tests may
validate the shape of future approved metadata, but structural metadata validity is not
runtime authorization. A future separately approved PR must add a trusted immutable
model-admission registry or artifact before Gate B can unlock.

## 18. Observation-Only Mode

`observation_only_no_model` is permitted now. It may:

- inspect local or synthetic session information;
- evaluate freshness and completeness;
- evaluate schedule eligibility;
- construct deterministic run identities;
- produce monitoring state;
- report why inference is unavailable.

It may not generate predictions, probabilities, long/cash model signals, risk-approved
orders, broker orders, or account actions. The absence of a model is an explicit state:
`blocked_no_approved_model`.

## 19. Shadow Proposal Contract

A shadow proposal is a non-executable audit object. Candidate fields include:

- `shadow_run_id`;
- `symbol`;
- `signal_session`;
- `generated_at`;
- `mode`;
- `model_id`;
- `model_checksum`;
- `feature_schema`;
- `data_lineage`;
- `predicted_probability` or `score`;
- hypothetical target state: `LONG` or `CASH`;
- admission status;
- freshness status;
- monitoring status;
- proposal status.

Observation-only operational commands produce no `ShadowProposal`. A future scaffolded
observation-only proposal status such as `not_generated_observation_only` may exist as a
typed contract, but this substage must not persist or emit model proposals, probabilities,
scores, or `LONG`/`CASH` targets. A shadow proposal must not reuse paper-execution order
objects and must not include submit, place, cancel, replace, reconcile, liquidation, or
broker methods.

## 20. Risk-Preview Boundary

Risk preview is not execution approval. A later approved substage may allow a hypothetical
shadow target to be passed through non-mutating risk-preview checks. This branch does not
create strategy optimization, order sizing, approval, paper submission, or reconciliation.

## 21. Persistence

Phase 4 persistence must be separate from paper execution state. Observation-Only
Operational Pipeline V1 uses a dedicated SQLite database supplied explicitly by the operator
and schema version `spy-v2-phase4-shadow-db-v1`. It allows a new empty database or an
existing valid shadow database with the expected version. It fails closed if the database
contains non-shadow application tables or an incompatible/incomplete shadow schema.

All application tables use a `shadow_` prefix:

- `shadow_schema_metadata`;
- `shadow_runs`;
- `shadow_input_snapshots`;
- `shadow_health_events`;
- `shadow_alerts`.

This substage intentionally does not persist model admissions, model proposals, paper
orders, raw provider payloads, or full historical OHLCV rows. Records preserve immutable
identities, timestamps, lineage, terminal status, duplicate behavior, auditability, and
restart/recovery semantics.

## 22. Monitoring

Required health checks include:

- market-data freshness;
- expected-session availability;
- duplicate sessions;
- invalid OHLCV;
- checksum or lineage failure;
- model admission state;
- duplicate shadow run;
- storage failure;
- configuration failure;
- clock or calendar uncertainty.

Monitoring statuses are `healthy`, `degraded`, and `blocked`. Missing or uncertain critical
information must be `blocked`.

## 23. Alerts

Initial alert events may be persisted or logged without adding external alerting
dependencies. Alert classes include:

- `stale_data`;
- `missing_session`;
- `invalid_market_data`;
- `provider_not_finalized`;
- `duplicate_run`;
- `persistence_failure`;
- `recovery_required`;
- `unexpected_configuration`;
- `lineage_failure`.

No email, SMS, Slack, cloud, or external alerting dependency is required in this scaffold.

## 24. Failure Handling

Shadow failures fail closed with deterministic reasons. Blocking examples:

- unsupported symbol, timeframe, calendar, or adjustment;
- non-XNYS target date;
- incomplete session;
- provider not finalized;
- stale data;
- missing expected session;
- duplicate or out-of-order sessions;
- invalid OHLCV;
- calendar uncertainty;
- no approved model for model-connected mode;
- duplicate shadow run;
- lineage or checksum mismatch;
- persistence failure;
- configuration failure.

Failures must not fall back to another model, another dataset, another session, another
asset, another timeframe, or a paper execution path.

## 25. Restart/Recovery Behavior

Restart and recovery must be idempotent. The same session, configuration, data snapshot, and
mode must resolve to the same shadow run identity. Observation-Only Operational Pipeline V1
uses the lifecycle `reserved`, `completed`, `blocked`, and `failed`. A terminal
`completed`, `blocked`, or `failed` record rejects retries as `duplicate_run`. A prior
`reserved`, incomplete, or unknown record rejects retries as `recovery_required` and must not
be automatically resumed, deleted, or overwritten. Retry rejections are audit events, not
lifecycle transitions. They may append `duplicate_run` or `recovery_required` health events
and local alerts associated with the existing deterministic run ID. Persistence uncertainty
must fail closed through typed shadow persistence errors rather than leaking raw SQLite
exceptions through the CLI.

## 26. Audit and Lineage

Every future shadow run must record:

- phase identifier `v2-phase-04`;
- package/runtime version;
- Git/source lineage;
- symbol/timeframe/calendar/timezone;
- session;
- data snapshot lineage and checksum;
- feature schema;
- model admission metadata when applicable;
- configuration version;
- freshness/completeness decision;
- schedule decision;
- monitoring status;
- proposal status when applicable.

Machine-specific local paths, usernames, hostnames, credentials, and raw provider payloads
must not define identity or appear in committed artifacts.

## 27. Security

Shadow code must not hard-code API keys, account IDs, secrets, or credentials. It must not
print secrets. It must not import or instantiate broker clients. It must not expose API write
routes, dashboard execution controls, order approval controls, or kill-switch controls.

## 28. Credential Policy

This scaffold requires no credentials. Future real market-data shadow ingestion may use
Phase 1 market-data credentials only after separate review. Trading credentials such as
paper-order keys, broker-account credentials, and live credentials are not required and must
not be read by shadow infrastructure.

## 29. CLI/API Boundaries

Observation-Only Operational Pipeline V1 and Scheduled Observation Operations V1 add
explicit manual CLI commands only:

```bash
python -m spy_market_agent.shadow.cli run-observation \
  --manifest <phase1-manifest-path> \
  --data-root ./data \
  --shadow-db ./shadow.sqlite3 \
  --session YYYY-MM-DD \
  --as-of YYYY-MM-DDTHH:MM:SSZ \
  --provider-finalized \
  --provider-finalization-policy-id <policy-id>

python -m spy_market_agent.shadow.cli show-run \
  --shadow-db ./shadow.sqlite3 \
  --run-id <shadow-run-id>

python -m spy_market_agent.shadow.cli schedule-preview \
  --manifest <phase1-manifest-path> \
  --data-root ./data \
  --shadow-db ./shadow.sqlite3 \
  --as-of YYYY-MM-DDTHH:MM:SSZ \
  --provider-finalized \
  --provider-finalization-policy-id <policy-id>

python -m spy_market_agent.shadow.cli run-due-observation \
  --manifest <phase1-manifest-path> \
  --data-root ./data \
  --shadow-db ./shadow.sqlite3 \
  --as-of YYYY-MM-DDTHH:MM:SSZ \
  --provider-finalized \
  --provider-finalization-policy-id <policy-id>
```

`run-observation` verifies local Phase 1 artifacts, evaluates observation readiness,
reserves a deterministic run, persists sanitized lineage, writes health events and local
alerts, and marks the run `completed` or `blocked`. `show-run` is read-only.
`schedule-preview` resolves the due session, validates local lineage and compatible history,
and reports due/blocked/degraded/already-processed/recovery states without creating a
database or mutating existing run lifecycle. `run-due-observation` resolves the due session,
refuses already-processed or recovery states, and delegates to `run_observation` exactly
once only when the current due session is eligible. It does not accept `--session` because
the target is calendar-derived from `--as-of`. No Phase 4 CLI that performs real-data model
inference is approved. No API mutation route or dashboard execution control is approved.
Importing `spy_market_agent.shadow` or `spy_market_agent.shadow.cli` must have no side
effects.

## 30. Testing

Normal automated tests must be offline, synthetic, credential-free, and broker-free.
Required coverage includes:

- Phase 4 spec existence and status;
- released package/runtime version is `2.0.0b1`;
- `v2.0.0-alpha.3` documented as released;
- `v2.0.0-beta.1` documented as released;
- observation-only mode permitted;
- model-connected inference rejected even with self-declared approved metadata;
- synthetic approved metadata passes structural validation without granting runtime
  inference;
- SPY daily XNYS policy;
- incomplete and stale sessions rejected;
- provider-not-finalized blocked;
- verified local Phase 1 manifests required;
- target session must be the latest canonical session;
- shadow SQLite schema version and isolation;
- duplicate run rejected;
- incomplete reservation requires recovery;
- deterministic run identity;
- health events and blocking alerts persisted;
- read-only inspection does not mutate state;
- schedule preview is read-only and does not create a missing database;
- due-session resolution uses XNYS, including weekends, holidays, and early closes;
- schedule no-ops for already processed runs do not append duplicate audit records;
- reserved current runs surface recovery-required without resume or overwrite;
- missed shadow-operation sessions are surfaced and never backfilled automatically;
- `run-due-observation` performs at most one delegated observation attempt;
- no broker/trading imports from shadow modules;
- no credential reads;
- no automatic order submission;
- no unattended scheduler, daemon, loop, sleep, cron, APScheduler, Celery, or RQ dependency;
- no import side effects;
- live trading remains prohibited;
- Phase 5 production paper submission remains unauthorized unless separately approved.

## 31. Owner Testing

Owner testing for the Phase 4 implementation is complete. The owner verified a local Phase
1 manifest through the existing Phase 1 workflow, ran `schedule-preview`, inspected
due/blocked/degraded state, ran `run-due-observation` for an eligible latest due session,
inspected the persisted run, reran the same scheduled command to confirm
already-processed no-op behavior, and inspected stored events/alerts. Owner testing did not
rerun Phase 3 research, access protected labels, load a model, create proposals, create
paper/live orders, or configure unattended automation.

## 32. Required Artifacts/Evidence

Beta 1 release evidence includes:

- this governing specification;
- updated governance/status documentation;
- the `spy_market_agent.shadow` observation-only pipeline;
- the schedule-aware operator-triggered orchestration layer;
- dedicated shadow SQLite persistence evidence;
- manual CLI evidence for `run-observation`, `show-run`, `schedule-preview`, and
  `run-due-observation`;
- sanitized owner acceptance evidence in `docs/V2_PHASE_04_BETA1_RELEASE_EVIDENCE.md`;
- Beta 1 release notes;
- synthetic test evidence;
- quality-gate output;
- confirmation that package/runtime is released as `2.0.0b1`;
- confirmation that the beta tag was created only after review, merge, final verification,
  and owner approval;
- confirmation that no model was promoted;
- confirmation that no broker/execution capability was added.

## 33. Acceptance Criteria

This substage may be accepted when:

- Phase 3 release status and `NO CANDIDATE PROMOTION` are documented accurately;
- Phase 4 Gate A and Gate B are documented;
- observation-only mode is implemented and tested;
- manual observation-only operation consumes verified local Phase 1 manifests only;
- schedule-aware operation resolves the latest completed XNYS target from explicit UTC
  `as_of` and delegates eligible work to the approved observation runner at most once;
- schedule preview is read-only, already-processed runs are no-ops, recovery states fail
  closed, and missed shadow-operation sessions are reported without backfill;
- dedicated shadow SQLite persistence is isolated from paper execution state;
- duplicate and incomplete-reservation retry behavior fails closed;
- monitoring events and local alerts are persisted;
- model-connected shadow inference is mechanically locked because there is no trusted
  approved model-admission registry or artifact;
- deterministic shadow run identity is implemented and tested;
- freshness, completeness, and schedule policy functions fail closed;
- shadow modules have no broker/execution imports or side effects;
- normal tests remain offline and synthetic;
- coverage remains at least 85%;
- Ruff, formatting, MyPy, `git diff --check`, and `git status --short` pass;
- no real-data experiment, protected evaluation, paper order, or live order is created.

## 34. Rejection Criteria

This substage must be rejected or held when:

- a model is silently promoted despite Phase 3's `NO CANDIDATE PROMOTION`;
- model-connected real-data shadow inference can run without approved metadata;
- a dummy or fallback model can run outside synthetic tests;
- shadow code imports broker clients or paper execution services;
- paper/live behavior changes;
- an unattended scheduler, daemon, API write route, or dashboard execution control is
  introduced;
- intraday data, another asset, protected evaluation, strategy optimization, or live
  trading is added;
- generated real-data artifacts, credentials, account identifiers, or private paths are
  committed;
- documentation claims profitability, predictive edge, paper readiness, live readiness, or
  production readiness.

## 35. Versioning Contract

Phase 4 starts from released `v2.0.0-alpha.3` and package/runtime version `2.0.0a3`.
Specification, infrastructure-first scaffolding, Observation-Only Operational Pipeline V1,
and Scheduled Observation Operations V1 must not bump the package version.

The public release identifier is `v2.0.0-beta.1`. The Beta 1 release-preparation branch
prepared package/runtime version `2.0.0b1`, recorded sanitized owner acceptance evidence,
merged through PR #30, and was tagged after final owner approval.

API, database, market-data, benchmark, and research artifact schema versions do not change
merely because Phase 4 planning begins.

## 36. Approval Boundary

This specification authorizes Phase 4 infrastructure-first shadow-mode scaffolding,
Observation-Only Operational Pipeline V1, and Scheduled Observation Operations V1 only. The
scheduled-observation substage is operator-triggered schedule-aware orchestration, not an
unattended scheduler. Beta 1 release preparation authorized release metadata, sanitized
acceptance evidence, documentation, tests, and the package/runtime candidate bump only. It
does not authorize model-connected real-data inference, protected evaluation, strategy
optimization, production paper operation, live trading, broker communication, cron, daemon,
background workers, continuously running processes, API write routes, dashboard execution
controls, new operational functionality, or a beta tag.

Any expansion beyond this document requires explicit owner approval and a new or amended
governing specification. Model-connected shadow inference remains locked until a separate
model-admission gate approves an immutable model candidate for shadow operation.
