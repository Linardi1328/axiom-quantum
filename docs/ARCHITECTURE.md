# Architecture

## System Overview

`spy-market-agent` is organized as a layered Version 1 research and paper-execution
system. Market data, feature engineering, modeling, strategies, risk, backtesting,
persistence, API presentation, dashboard rendering, and paper execution are separate
packages.

The central rule is that models produce diagnostics, not orders. Strategies convert locked
final-test probabilities into proposed long-or-cash targets, and every proposed order must
pass through independent risk checks before a backtest fill or an explicitly invoked paper
submission can proceed.

## Package Responsibilities

- `spy_market_agent.config` (`src/spy_market_agent/config`): typed settings, safe defaults, live-mode rejection, secret-safe
  display values, and explicit settings loading.
- `spy_market_agent.market_data` (`src/spy_market_agent/market_data`): SPY daily data contracts, provider protocol, XNYS calendar,
  deterministic dataset checksums, and the Version 2 Phase 1 explicit historical acquisition
  path for raw snapshots, canonical CSV, manifests, and local verification.
- `spy_market_agent.validation` (`src/spy_market_agent/validation`): canonical SPY daily OHLCV validation.
- `spy_market_agent.features` (`src/spy_market_agent/features`): leakage-safe trailing feature construction.
- `spy_market_agent.datasets` (`src/spy_market_agent/datasets`): forward label construction, feature/label alignment, and
  chronological train/validation/test partitions.
- `spy_market_agent.modeling` (`src/spy_market_agent/modeling`): deterministic logistic-regression and gradient-boosting
  candidates, validation-only selection, locked refit, and final-test diagnostics.
- `spy_market_agent.strategies` (`src/spy_market_agent/strategies`): fixed long-or-cash signal policy and next-session execution
  mapping.
- `spy_market_agent.risk` (`src/spy_market_agent/risk`): independent SPY-only, long-only risk evaluation.
- `spy_market_agent.backtesting` (`src/spy_market_agent/backtesting`): in-memory next-open backtest accounting, execution-price
  lineage, and metrics.
- `spy_market_agent.benchmark` (`src/spy_market_agent/benchmark`): Version 2 Phase 2
  file-based real historical benchmark workflow, immutable locks, final-test access
  controls, baseline/regime diagnostics, and artifact verification.
- `spy_market_agent.research` (`src/spy_market_agent/research`): Version 2 Phase 3
  walk-forward fold construction, leakage guards, experiment/feature/model registries,
  research metrics, baselines, candidate selection, protected-evaluation denial, and
  ignored artifact schemas.
- `spy_market_agent.shadow` (`src/spy_market_agent/shadow`): Version 2 Phase 4
  observation-only shadow operational pipeline for verified local Phase 1 manifests,
  deterministic shadow run identity, model-admission locks, freshness/schedule policy
  functions, dedicated shadow SQLite audit persistence, monitoring events, local alerts,
  and read-only inspection. It has no model-inference or order-submission capability.
- `spy_market_agent.persistence` (`src/spy_market_agent/persistence`): explicit SQLite initialization, schema validation, and
  artifact repositories.
- `spy_market_agent.api` (`src/spy_market_agent/api`): read-only FastAPI application and response service.
- `spy_market_agent.dashboard` (`src/spy_market_agent/dashboard`): read-only Streamlit dashboard and HTTP API client.
- `spy_market_agent.execution` (`src/spy_market_agent/execution`): broker-independent paper-execution models, approvals,
  fingerprinting, ledger repository, and service.
- `spy_market_agent.execution.alpaca_paper` (`src/spy_market_agent/execution/alpaca_paper.py`): isolated Alpaca paper-only adapter.

## Architecture Diagram

```mermaid
flowchart TD
    Provider[MarketDataProvider protocol] --> Validation[SPY daily validation]
    Validation --> Features[Trailing features]
    Validation --> Labels[Open t+1 to open t+6 labels]
    Features --> Dataset[Supervised dataset]
    Labels --> Dataset
    Dataset --> Splits[Chronological train validation test splits]
    Dataset --> Research[Phase 3 walk-forward research framework]
    Splits --> Modeling[Logistic regression and gradient boosting]
    Modeling --> Selection[Validation-only selection]
    Selection --> FinalTest[Locked final-test evaluation]
    FinalTest --> Strategy[Long-or-cash signal policy]
    Strategy --> Risk[Independent risk engine]
    Risk --> Backtest[Next-open backtest engine]
    Manifest[Phase 1 dataset manifest] --> Benchmark[Phase 2 file-based benchmark locks]
    Benchmark --> Modeling
    Benchmark --> Backtest
    Research --> ResearchArtifacts[Ignored artifacts/research manifests]
    Validation --> Shadow[Phase 4 shadow observation-only scaffold]
    Shadow --> ShadowHealth[Shadow readiness and monitoring state]
    Backtest --> Persistence[SQLite artifact repository]
    FinalTest --> Persistence
    Validation --> Persistence
    Persistence --> API[Read-only FastAPI]
    API --> Dashboard[Read-only Streamlit]
    Risk --> PaperInstruction[Paper order instruction]
    PaperInstruction --> PaperService[Explicit paper-execution service]
    PaperService --> Ledger[SQLite paper-execution ledger]
    PaperService --> Alpaca[Alpaca paper adapter]
```

## Research Data Flow

1. A provider implementation returns SPY daily OHLCV data through the provider protocol.
2. Validation enforces canonical columns, XNYS sessions, chronological order, no duplicates,
   complete sessions, finite positive prices, and valid volume.
3. Feature engineering builds trailing features using only information available through
   session `t`.
4. Label construction uses entry at the open of `t + 1` and exit at the open of `t + 6`.
5. Supervised datasets keep feature columns separate from label audit columns.
6. Chronological partitions preserve order and keep labels inside each split boundary.
7. Candidate models train on train data only and are compared on validation data only.
8. The selected model is refit on train plus validation and evaluated once on the final test
   partition.

## Version 2 Phase 1 Acquisition Flow

**Accepted for alpha release preparation:** Phase 1 adds a separate explicit acquisition
path. It is not called by imports, tests, API startup, dashboard startup, model training,
backtesting, or paper execution.

```mermaid
flowchart TD
    CLI[Explicit CLI acquire command] --> Request[AcquisitionRequest validation]
    Request --> Credentials[Market-data environment credentials]
    Credentials --> Alpaca[Alpaca StockHistoricalDataClient]
    Alpaca --> Raw[Immutable sanitized raw JSON snapshot]
    Raw --> Canonicalize[Canonical SPY daily bars]
    Canonicalize --> XNYS[XNYS session and OHLCV validation]
    XNYS --> Manifest[Dataset manifest]
    Manifest --> Checksums[SHA-256 source canonical and artifact checksums]
    Checksums --> Storage[Ignored local data/raw data/canonical data/manifests]
```

The acquisition path uses `ALPACA_MARKET_DATA_API_KEY` and
`ALPACA_MARKET_DATA_SECRET_KEY`, not paper-trading credentials. It stores restricted provider
data only in ignored local directories. Synthetic fixtures under `data/fixtures/` are the
only Phase 1 market-data artifacts intended for Git.

Phase 1 still does not train a model, run a benchmark, contact the trading API, submit paper
orders, add API write routes, add dashboard controls, or enable live trading.

## Version 2 Phase 2 Benchmark Flow

**Engineering accepted for alpha release preparation:** Phase 2 adds a dedicated
`spy_market_agent.benchmark` package that accepts a verified Phase 1 manifest, not an
arbitrary CSV. It deep-verifies the raw, canonical, and manifest artifacts, reuses the
existing feature, label, model, signal, risk, and backtest logic, and writes ignored
immutable files under `artifacts/benchmarks/<benchmark_id>/`.

The benchmark workflow is split into locked stages:

1. `record-feed-decision` records owner-provided offline feed evidence without contacting
   Alpaca.
2. `prepare` verifies the Phase 1 dataset, checks eligibility, constructs features/labels,
   builds the deterministic chronological split, freezes cost/regime/model/baseline policy,
   and writes `benchmark_lock.json`.
3. `run-validation` trains only on training rows, evaluates only on validation rows, selects
   a model using the approved validation rule, and keeps final-test row-level labels guarded.
4. `finalize-lock` creates `final_test_lock.json` after validation artifacts are verified.
5. `run-final-test` requires explicit acknowledgement, writes `final_test_access.json`
   before loading final-test labels, refits on train plus validation only, and evaluates the
   locked final test once.
6. `verify` and audit replay perform offline checksum and lineage verification.

Phase 2 does not use SQLite for benchmark persistence, does not persist model binaries, does
not add model families, features, threshold tuning, API write routes, dashboard execution
controls, paper-execution behavior, real-time data, or live trading.

The owner-run real SIP benchmark completed under this flow for `v2.0.0-alpha.2` release
preparation. The engineering workflow passed, while the selected classifier showed weak
predictive discrimination and must not be described as a proven market edge.

## Version 2 Phase 3 Research Framework

**Released as `v2.0.0-alpha.3`:** Phase 3 PR #24 merged the approved framework and initial
research scaffolding. PR #25 merged the manual, offline, classification-first research
runner under `spy_market_agent.research`, owner development testing completed locally, and
Alpha 3 release preparation recorded sanitized evidence and package metadata under
`docs/V2_PHASE_03_WALK_FORWARD_RESEARCH_SPEC.md`. It starts from the completed Phase 2
benchmark evidence as sanitized baseline motivation only.

The default Phase 3 protocol is expanding-window walk-forward validation with chronological
assessment windows, a six-row boundary exclusion after each training window, explicit
feature warm-up handling, deterministic fold identities, deterministic experiment
identities, leakage validation, registry manifests, and ignored artifact schemas. The
framework separates classification diagnostics from strategy diagnostics and requires
experiment lineage before substantive real-data research. The development runner accepts
verified Phase 1 manifests, builds research-only OHLCV features without changing Version 1
feature contracts, reruns fixed Phase 2 baselines, evaluates finite scikit-learn candidate
grids, runs the predeclared calibration sub-study, and writes ignored research artifacts.
For accepted Phase 2 parent lineage, it reconstructs the frozen Phase 2 final-test
prediction-session boundary and truncates the development research slice before Version 1
labels are built.

Phase 3 does not introduce a production runtime package, API mutation path, dashboard
control, scheduler, broker connection, paper-execution change, live-execution behavior, or
new asset support. The Phase 3 CLI is manual, local, offline after data acquisition, and has
no acquisition or execution command. The already-opened Phase 2 final test remains frozen
evidence and must not be used for tuning. Protected evaluation, strategy optimization,
production paper operation, and live trading remain unauthorized. Phase 3 produced
`NO CANDIDATE PROMOTION`, so no model is approved for shadow or paper operation.

## Version 2 Phase 4 Scheduled Observation Shadow Pipeline

**Accepted and released as `v2.0.0-beta.1`:** Phase 4 is governed by
`docs/V2_PHASE_04_REAL_TIME_SHADOW_MODE_SPEC.md`. The package/runtime version is
`2.0.0b1`, and the public beta tag points to
`1c8feae478c0f5536b2193eeb408e580f3f7e33c`. PR #27 merged the specification/scaffold, PR
#28 merged Observation Pipeline V1, PR #29 merged Scheduled Observation Operations V1, PR
#30 merged Beta 1 release preparation, and owner acceptance evidence is recorded.

The initial `spy_market_agent.shadow` package is separate from research and execution:

1. `types.py` defines immutable shadow configuration, lineage, freshness, model-admission,
   monitoring, run decision, and proposal data structures.
2. `identity.py` derives deterministic shadow run IDs from stable session, mode, data
   lineage, feature schema, model metadata, and configuration inputs.
3. `model_gate.py` implements the fail-closed model-admission contract. The current state is
   `NO APPROVED SHADOW MODEL`.
4. `freshness.py` evaluates daily SPY/XNYS freshness and completeness decisions.
5. `schedule.py` exposes deterministic schedule eligibility functions without creating an
   OS scheduler, daemon, or background worker.
6. `schedule_ops.py` resolves the latest completed XNYS target from explicit UTC `as_of`,
   inspects durable shadow history, detects already-processed, recovery-required, stale,
   provider-finalization, data-ahead, and missed-observation states, and delegates eligible
   work to the existing observation runner at most once.
7. `policy.py` combines freshness and admission decisions for observation-only or future
   model-connected run eligibility.
8. `monitoring.py` summarizes health events as `healthy`, `degraded`, or `blocked`.
9. `persistence.py` owns a dedicated shadow SQLite schema, `spy-v2-phase4-shadow-db-v1`,
   with `shadow_schema_metadata`, `shadow_runs`, `shadow_input_snapshots`,
   `shadow_health_events`, and `shadow_alerts`.
10. `runner.py` orchestrates manual observation-only execution from verified Phase 1
   manifest, through target-session/freshness checks, durable reservation, health/alert
   writes, and terminal lifecycle update.
11. `cli.py` exposes `run-observation`, read-only `show-run`, read-only `schedule-preview`,
    and operator-triggered `run-due-observation`.

`observation_only_no_model` mode can report readiness and why inference is unavailable. It
consumes verified local Phase 1 manifests only and persists sanitized shadow audit state in
a separate SQLite database supplied explicitly by the operator. It cannot generate
predictions, produce model-based `LONG` or `CASH` proposals from real market data, create
`ShadowProposal` runtime outputs, submit paper orders, contact brokers, modify SQLite
paper-execution state, or run Phase 5 production paper behavior. `model_connected` mode
raises a model-admission error in this scaffold even when caller-supplied metadata
self-declares approval. A future
separately approved immutable model-admission registry or artifact is required before Gate B
can unlock.

Scheduled observation is orchestration, not a second observation engine. It does not
duplicate Phase 1 verification, OHLCV validation, freshness checks, shadow run construction,
durable reservation, lifecycle finalization, health persistence, or alert persistence. It
does not change the shadow database schema version. A schedule no-op for an already
processed run does not append duplicate audit records, while direct manual duplicate
`run-observation` attempts keep the approved duplicate audit behavior.

## Version 2 Phase 5 Production Paper Operation Scaffold

**Active specification + non-submitting safety/recovery scaffold:** Phase 5 is governed by
`docs/V2_PHASE_05_PRODUCTION_PAPER_OPERATION_SPEC.md` for target future release
`v2.0.0-beta.2`. Package/runtime remains `2.0.0b1`, and no beta.2 tag exists.

The new `spy_market_agent.paper_ops` package is intentionally separate from the existing
execution adapter and service:

1. `types.py` defines immutable gate, readiness, recovery-disposition, and operational
   issue contracts.
2. `gates.py` exposes the current Phase 5 gate posture: P5-A authorized, P5-B broker
   submission blocked pending separate owner authorization, and P5-C model-connected paper
   operation blocked because no approved paper model exists.
3. `recovery.py` classifies persisted paper-attempt states from the existing execution
   model into operator recovery dispositions without network, filesystem, settings, or
   broker side effects.
4. `policy.py` combines the current gate decisions for offline inspection.

The existing `spy_market_agent.execution` package remains the owner of paper-order
instruction construction, approval validation, repository transitions, paper-only Alpaca
adapter, and explicit paper execution service. Phase 5 does not rewrite those controls. The
first Phase 5 scaffold does not import the Alpaca adapter, instantiate a trading client,
read credentials, submit orders, perform broker reconciliation, start a scheduler, load a
model, expose API mutation routes, or add dashboard execution controls.

## Backtest Data Flow

1. Locked final-test predictions produce fixed long-or-cash target positions.
2. Signals map to the next validated market-data row, not calendar-day arithmetic.
3. Proposed orders are generated only when the target changes.
4. Independent risk evaluation approves or rejects each proposed order.
5. Approved orders become fills with configured commission and slippage assumptions.
6. Rejected orders do not create fills and do not change portfolio state.
7. Backtest results retain source market data, execution prices, orders, decisions, fills,
   portfolio rows, metrics, schema versions, checksums, and runtime lineage.

## Paper-Execution Data Flow

```mermaid
sequenceDiagram
    participant Caller
    participant Service as PaperExecutionService
    participant Ledger as SQLite ledger
    participant Broker as AlpacaPaperBroker
    participant Risk as Risk engine

    Caller->>Service: explicit instruction plus approval
    Service->>Service: check settings and configuration kill switch
    Service->>Broker: verify paper endpoint and account state
    Service->>Broker: read initial broker clock
    Service->>Service: validate approval and staleness
    Service->>Ledger: read durable kill switch
    Service->>Broker: check SPY asset, positions, open orders
    Service->>Risk: re-evaluate order at execution time
    Service->>Ledger: reserve IDs and symbol/session
    Service->>Broker: lookup by client_order_id
    alt existing matching paper order
        Service->>Ledger: record broker_existing_order_found
    else no existing order
        Service->>Broker: refresh broker clock
        Service->>Ledger: final durable kill-switch read
        Service->>Broker: submit SPY market DAY paper order
        Service->>Ledger: record accepted or submission_unknown
    end
```

Paper execution is not available through FastAPI or Streamlit. The service path is explicit,
requires a matching human approval, and keeps uncertainty as local audit state.

## Trust Boundaries

- Raw market data is untrusted until validation returns a canonical `MarketDataBatch`.
- Phase 1 provider data is untrusted until raw snapshot schema checks, canonicalization,
  XNYS validation, checksum construction, manifest validation, and safe storage all pass.
- Feature, label, model, strategy, backtest, and persistence objects revalidate lineage and
  schema invariants at their public boundaries.
- Phase 2 benchmark artifacts are untrusted until deterministic checksums, benchmark IDs,
  dataset IDs, schema versions, split lineage, and lock references are verified.
- Stage A benchmark services receive only training and validation row-level labels plus
  non-sensitive final-test boundary and aggregate eligibility counts.
- Phase 3 experiment artifacts are untrusted until dataset, feature, label, fold, model,
  calibration, threshold, metric, candidate-selection configuration, code, package, Python,
  and dependency lineage are verified.
- Phase 4 shadow inputs are untrusted until Phase 1 manifest verification, session,
  freshness, completeness, data-lineage, duplicate-run, and model-admission checks pass.
  Observation-only readiness is not model approval.
- Models cannot access brokers. The modeling package imports no execution adapter, no
  Alpaca SDK, and no broker protocol.
- The shadow package imports no execution adapter, paper service, order approval service,
  or Alpaca TradingClient.
- Alpaca trading-client usage is isolated to `execution/alpaca_paper.py`.
- Historical market-data SDK use is isolated to `market_data/alpaca_provider.py`, which uses
  the data API client and not the trading client.
- The API and dashboard are read-only. They inspect local persisted state and do not mutate
  SQLite, change kill switches, approve orders, reconcile orders, or submit broker requests.
- API and dashboard startup do not initialize or migrate databases.
- Package imports do not load settings globally, create files, train models, contact brokers,
  or submit orders.

## Initialization and Side Effects

SQLite setup is explicit through `initialize_database(...)`. Settings loading is explicit
through `load_settings()` or direct `Settings(...)` construction. The package import surface is
designed to be safe for tests, documentation tooling, API startup, and dashboard rendering.
Phase 2 benchmark commands are manually invoked CLI workflows; imports, API startup, and
dashboard startup do not read ignored benchmark artifacts or access final-test data.
Phase 3 research scaffolding must preserve the same import and startup behavior: no
automatic acquisition, model experimentation, artifact loading, broker construction, or
order submission.
Phase 4 shadow code also preserves side-effect-free imports: no scheduler startup, broker
construction, credential reads, data acquisition, model inference, artifact loading,
database migration, or order submission occurs on import. Shadow SQLite initialization and
run persistence occur only through explicit `run-observation` or repository calls and never
reuse the paper-execution ledger.
