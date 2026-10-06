# Changelog

## [Unreleased]

Corresponding Python package version remains: `2.0.0b1`.

- Began Version 2 Phase 5 Production Paper Operation specification plus non-submitting
  safety/recovery scaffold on `review/v2-phase-05-production-paper` after Phase 4 Beta 1
  was released at `1c8feae478c0f5536b2193eeb408e580f3f7e33c`.
- Added the governing Phase 5 specification for target future release `v2.0.0-beta.2`.
- Added a Phase 5 paper recovery runbook documenting the rule that uncertainty never means
  retry the order.
- Added the offline `spy_market_agent.paper_ops` scaffold for Phase 5 gate decisions,
  paper-model admission lock, and deterministic recovery classification of existing paper
  attempt states.
- Recorded Gate P5-A as authorized, Gate P5-B broker paper submission as blocked pending
  separate owner authorization, and Gate P5-C model-connected paper operation as
  `BLOCKED_NO_APPROVED_PAPER_MODEL`.
- Preserved existing Version 1 paper execution controls and kept package/runtime at
  `2.0.0b1`; no `v2.0.0-beta.2` tag exists.
- No real broker calls, credentials, paper order submission, automatic resubmission,
  scheduler, model inference, protected evaluation, or live trading are included.

## [2.0.0-beta.1] - 2026-08-15

Corresponding Python package version: `2.0.0b1`.

- Began Version 2 Phase 4 Beta 1 release preparation after PR #29 merged into `main` at
  `6fb92c0a6f630d7faa3626cef515d0bb6c742951` and owner acceptance completed.
- Added sanitized Beta 1 release evidence for owner observation-only smoke test
  `spy-v2p4-shadow-09c5104d1614fe7902a3fadf` using accepted Phase 1 dataset
  `spy-v2p1-825930b0a2bcab20c733b867`.
- Added Beta 1 release notes for public identifier `v2.0.0-beta.1`; the tag was created
  after review, merge, owner approval, and final verification on `main`.
- Prepared package/runtime version `2.0.0b1` for the Beta 1 release.
- Added Version 2 Phase 4 Scheduled Observation Operations V1: deterministic
  operator-triggered `schedule-preview` and `run-due-observation` commands that derive the
  latest completed XNYS target from explicit UTC `as_of`, inspect compatible shadow history,
  treat already-processed runs as schedule no-ops, surface recovery-required and missed
  observation history, and delegate eligible work to the existing observation runner at most
  once.
- Added Version 2 Phase 4 Observation-Only Operational Pipeline V1: a manual
  `run-observation` CLI for verified local Phase 1 manifests, explicit XNYS session/as-of
  checks, provider-finalization evidence, dedicated shadow SQLite audit persistence,
  deterministic duplicate protection, persisted health events, local blocking alerts, and
  read-only `show-run` inspection.
- Began Version 2 Phase 4 Real-Time Shadow Mode specification and infrastructure-first
  scaffolding for target release `v2.0.0-beta.1`.
- Added the governing Phase 4 specification and initial `spy_market_agent.shadow` package
  for observation-only readiness checks, deterministic run identities, model-admission
  locking, freshness/schedule policy functions, and monitoring state.
- Documented that Version 2 Phase 3 is released as `v2.0.0-alpha.3`, its outcome was
  `NO CANDIDATE PROMOTION`, and model-connected shadow inference remains blocked because no
  model is approved for shadow operation.
- Recorded that Gate B remains locked, approved shadow model remains none, protected
  evaluation remains not executed, production paper operation remains unauthorized, and no
  beta tag, strategy optimization, model inference, `ShadowProposal` operational generation,
  broker communication, market-data acquisition, unattended scheduler/daemon, Phase 5
  implementation, or live trading is included.

## [2.0.0-alpha.3] - 2026-08-11

Corresponding Python package version: `2.0.0a3`.

- Began Version 2 Phase 3 Alpha 3 release preparation after PR #25 was owner-tested and
  merged into `main` at `cc6ee4ee404659143a1ba633d3faf7fddbc63f9f`.
- Added sanitized Alpha 3 release evidence for owner-run campaign
  `spy-v2p3-dev-3741349b8aa34020b8425af5`, which ended with `NO CANDIDATE PROMOTION`.
- Prepared package/runtime version `2.0.0a3` for public release identifier
  `v2.0.0-alpha.3`; the tag was created after merge and final verification on `main`.
- Documented that protected evaluation, strategy optimization, Phase 4 shadow mode,
  production paper operation, live trading, broker communication, and real-data artifact
  commits remain unauthorized.
- Added the Version 2 Phase 3 development-only walk-forward research runner and committed
  data-free campaign configuration for `v2.0.0-alpha.3` development evidence.
- Added research-only OHLCV feature families, feature ablation campaigns, finite
  scikit-learn candidate grids, calibration sub-study support, expected calibration error,
  regime/drift diagnostics, deterministic research artifacts, and a manual offline
  `run-development` CLI.
- Hardened Phase 3 development research so the runner reconstructs the frozen Phase 2
  final-test prediction-session boundary, truncates the research slice before label
  construction, records slice lineage, and rejects any development surface that intersects
  Phase 2 final-test prediction sessions.
- Documented that PR #24 framework/scaffolding review was merged, development
  experimentation was authorized for PR #25, protected evaluation and strategy optimization
  remained unauthorized, Phase 4 remained unauthorized, and package/runtime version remained
  `2.0.0a2` during that development substage.
- Added the Version 2 Phase 3 Walk-Forward Model Research specification.
- Implemented initial `spy_market_agent.research` scaffolding for deterministic
  walk-forward fold manifests, leakage guards, research registries, metrics, baselines,
  calibration and threshold policies, candidate selection, protected-evaluation denial, and
  ignored research artifact schemas.
- Updated roadmap, README, workflows, architecture, reproducibility, safety, and agent
  guidance to mark Phase 3 as active framework implementation and initial research
  scaffolding.
- Documented that Phase 3 must not tune against the already-opened Phase 2 final test.

### Released

- Version 2 Phase 3 was accepted, merged, and tagged as `v2.0.0-alpha.3`.
- The owner-run development campaign produced `NO CANDIDATE PROMOTION`.
- Protected evaluation, shadow inference, production paper operation, and live trading were
  not executed or authorized by Alpha 3.

## [2.0.0-alpha.2] - 2026-08-09

Corresponding Python package version: `2.0.0a2`.

### Added

- Added the Version 2 Phase 2 Real Historical Benchmark specification.
- Implemented the Version 2 Phase 2 benchmark infrastructure for review, including
  file-based benchmark artifacts, feed-decision records, dataset eligibility checks,
  deterministic split locks, validation-only model selection, final-test access controls,
  baseline comparators, cost sensitivity, regime diagnostics, CLI commands, and synthetic
  offline tests.
- Rewrote the README local dashboard Quick Start with repository-root checks, Python 3.12
  setup, explicit SQLite initialization, FastAPI and Streamlit startup order, health
  verification, expected empty-state behavior, and troubleshooting.

### Accepted

- Implementation PR #20 was merged at main commit `1155c3c`.
- Owner-run real SIP Phase 2 benchmark acceptance completed using dataset ID
  `spy-v2p1-825930b0a2bcab20c733b867` and benchmark ID
  `spy-v2p2-a065593e952e6a9d96f4be86`.
- Dataset verification, benchmark verification, runtime-lineage verification, validation,
  one controlled final-test execution, and quality gates passed in the owner environment.
- Generated real benchmark artifacts and provider data remained ignored and were not added
  to Git.

### Scientific Result

- `logistic_regression` was selected by validation ROC AUC over `gradient_boosting`.
- Final-test ROC AUC was below `0.5`, and probability metrics did not beat the
  training-prevalence baseline.
- The selected model behaved close to an almost-always-long signal during the final-test
  period, with about `97.6%` predicted-positive rate.
- The final-test strategy evidence is valid benchmark evidence, but it does not establish a
  reliable predictive edge, trading readiness, or profitability.

### Not Included

- No raw Alpaca data, benchmark JSON artifacts, row-level labels, provider payloads,
  credentials, account identifiers, or authentication data are committed.
- No model research, feature changes, hyperparameter tuning, calibration, threshold
  research, API write route, dashboard execution control, paper-execution behavior, live
  trading, or Phase 3 implementation is included.

## [2.0.0-alpha.1] - 2026-08-05

Corresponding Python package version: `2.0.0a1`.

### Added

- Added the long-term future development roadmap and Version 2 phase/pre-release map.
- Added ignore rules for local smoke-test artifacts.
- Added the Version 2 Phase 1 real-SPY-data specification.
- Added explicit Alpaca historical SPY daily acquisition.
- Added separate market-data credentials.
- Added sanitized raw JSON storage.
- Added canonical validated CSV storage.
- Added XNYS session validation.
- Added deterministic manifests, SHA-256 checksums, and dataset identity.
- Added safe local storage and atomic multi-artifact rollback.
- Added deep offline dataset verification.
- Added synthetic offline fixtures and tests.
- Added provider, licensing, security, and reproducibility documentation.

### Verified

- 975 automated tests passed on the release-preparation branch.
- Branch-aware coverage was 85.20%, above the 85% gate.
- Owner-run Alpaca IEX smoke test acquired four SPY sessions for 2024-01-02 through
  2024-01-05.
- Deep verification passed for the owner-run smoke-test artifacts.
- Generated provider data remained outside Git.

### Limitations

- No real SPY dataset is distributed with the repository.
- Alpaca coverage is not proven back to SPY inception.
- SIP availability may depend on subscription.
- Corporate-action evidence remains limited to the documented provider adjustment policy.
- No model benchmark, accuracy result, or profitability claim is included.
- No real-time feed, shadow mode, or live-money execution is included.
- Version 2 Phase 2 has not begun.

## [1.0.0] - 2026-08-04

Version 1 is the completed educational and experimental SPY daily research,
risk-controlled backtesting, persistence, read-only presentation, and explicit paper-only
execution preparation release.

### Implemented

- Validated SPY-only daily OHLCV data contracts, XNYS session handling, deterministic
  checksums, and provider-independent market-data interfaces.
- Added leakage-safe trailing features and the Version 1 label: information through close
  of `t`, entry no earlier than open of `t + 1`, exit at open of `t + 6`, and a positive
  target only after estimated round-trip costs and slippage.
- Added chronological train, validation, and final-test model evaluation with gap-aware
  split contracts, deterministic logistic-regression and gradient-boosting candidates,
  validation-only selection, locked refit, and final-test diagnostics.
- Added a fixed long-or-cash strategy policy and risk-controlled backtesting with
  transaction costs, slippage, cash accounting, whole-share fills, equity curves, orders,
  risk decisions, metrics, and source-market lineage.
- Added SQLite persistence and audit replay for validated market data, model evaluations,
  backtest results, and local paper-execution ledger state.
- Added read-only FastAPI routes and a read-only Streamlit dashboard for persisted data
  status, model evaluations, backtests, risk state, and local paper-execution status.
- Added explicit Alpaca paper-only execution safeguards: broker-independent instruction
  and approval models, deterministic fingerprints, dual kill switches, paper endpoint
  verification, live-mode rejection, market-hours checks, execution-time risk evaluation,
  duplicate ID protection, same-symbol/session reservation, submission-unknown handling,
  and lookup-only reconciliation.
- Added quality and documentation work: full README rewrite, architecture,
  reproducibility, workflow, security/safety, demo, and portfolio documentation, plus
  documentation consistency tests.

### Limitations

- No committed real SPY dataset or market-data downloader exists.
- No live trading, live endpoint support, automatic order submission, scheduler, worker,
  deployment configuration, API write route, or dashboard execution control exists.
- No assets other than SPY, intraday data, short selling, leverage, margin, fractional
  shares, cancellation, replacement, stops, limits, brackets, OCO, or OTO are implemented.
- Backtests and classification metrics are diagnostics only and are not profitability,
  real-market accuracy, investment-advice, or real-money-readiness claims.
- Exact reproducibility can differ when dependency versions differ because no lock file is
  committed.

## Version 1 Development History

### Phase 1: Specification and Agent Instructions

- Created the project specification and permanent agent guardrails.
- Established the educational purpose, SPY-only scope, no-live-trading rule, chronological
  modeling rules, and phased development plan.

### Phase 2: Project Scaffold and Tooling

- Added the Python 3.12 `src` package layout, tests, placeholder data/artifact directories,
  `pyproject.toml`, `.env.example`, and initial README.
- Configured Pytest, pytest-cov, Ruff, and MyPy.

### Phase 3: Configuration, Data Schema, and Validation

- Added typed settings with safe execution defaults and secret-aware display values.
- Added provider-independent SPY daily market-data contracts, XNYS calendar support,
  deterministic checksums, and canonical OHLCV validation.
- Hardened validation, checksum, settings redaction, and calendar-boundary behavior through
  corrective passes recorded in `reviews/PHASE_03_REVIEW.md`.

### Phase 4: Feature Engineering and Chronological Splits

- Added leakage-safe trailing feature construction.
- Added the Version 1 open `t + 1` to open `t + 6` label definition.
- Added supervised dataset alignment and chronological split assignment.
- Hardened label, target, checksum, metadata, and split validation.

### Phase 5: Machine-Learning Baselines

- Added deterministic logistic-regression and gradient-boosting candidates.
- Added validation-only model selection, locked train-plus-validation refit, and final-test
  evaluation.
- Preserved fixed model lineage and hardened fitted-estimator validation.

### Phase 6: Strategy and Backtesting

- Added the fixed long-or-cash strategy policy and next-open execution mapping.
- Added independent SPY-only long-only risk evaluation.
- Added in-memory backtesting with transaction costs, slippage, portfolio accounting,
  execution-price lineage, and metrics.
- Hardened source-market provenance and replay validation.

### Phase 7: Persistence, API, and Dashboard

- Added explicit SQLite initialization and artifact repositories.
- Added persistence for validated market data, final model evaluations, and backtest results.
- Added read-only FastAPI routes and a read-only Streamlit dashboard.
- Hardened run IDs, pagination, persistence read paths, and JSON validation.

### Phase 8: Paper-Trading Preparation

- Added broker-independent paper-execution models, approvals, fingerprints, service, and
  durable SQLite ledger.
- Added an isolated Alpaca paper-only adapter.
- Added read-only API and dashboard paper-status views.
- Added dual kill switches, market-hours enforcement, refreshed broker-clock checks,
  unknown-submission handling, lookup-only reconciliation, and same-symbol/session
  reservation protection.

### Pre-Phase-9 Quality Correction

- Raised full-suite coverage above the 85% gate.
- Converted unexpected warnings into errors with exact documented upstream warning filters.
- Preserved logistic-regression semantic lineage as `classifier.penalty="l2"` while avoiding
  the scikit-learn explicit-penalty `FutureWarning`.

### Phase 9: Documentation, Polish, and Portfolio Readiness

- Rewrote the README to describe the completed Version 1 system and its limitations.
- Added architecture, reproducibility, workflow, safety, demo, and portfolio documentation.
- Added documentation consistency tests for files, links, route inventory, module paths, and
  paper-execution safety statements.
- Updated the project specification to mark Phase 9 documentation deliverables.
