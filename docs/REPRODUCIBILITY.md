# Reproducibility

## Python Version

Use Python 3.12. Project metadata requires:

```text
>=3.12,<3.13
```

## Clean Environment Setup

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

The repository does not include a lock file, so exact transitive dependency versions can
differ across machines. Reproducibility metadata is recorded in persisted research artifacts
where available, but byte-for-byte reproduction is not guaranteed when dependency versions
change.

## Verification Commands

```bash
python -m pip install -e ".[dev]"
pytest --cov-fail-under=85
pytest tests/unit -q
pytest tests/integration -q
pytest -W error::FutureWarning
ruff check .
ruff format --check .
mypy src tests
python -c "import spy_market_agent; print(spy_market_agent.__version__)"
```

Coverage is branch-aware and must remain at least 85% for the full suite.

## Warning Policy

`pyproject.toml` configures Pytest to treat unexpected warnings as errors. The allowed
warning filters are exact upstream dependency warnings by category, message, and module.
New project warnings or broad warning suppressions should be treated as release blockers.

## Deterministic Inputs

Version 1 uses deterministic random seeds where applicable. The default modeling seed is
`42`, and fixed model parameter snapshots preserve the semantic logistic-regression lineage,
including `classifier.penalty="l2"`.

No real SPY dataset is committed. Tests use deterministic synthetic or in-memory data.
External data used by a developer must be validated, checksummed, and persisted locally before
API or dashboard inspection.

## Version 2 Phase 1 Data Foundation

**Accepted and released as `v2.0.0-alpha.1`:** Phase 1 adds explicit historical SPY
daily-data acquisition. Normal verification remains offline and does not require
market-data credentials.

Example explicit acquisition command:

```bash
python -m spy_market_agent.market_data.cli acquire \
  --provider alpaca \
  --symbol SPY \
  --start 2016-01-04 \
  --end 2016-01-08 \
  --timeframe 1Day \
  --feed sip \
  --adjustment all \
  --data-root ./data \
  --acknowledge-provider-terms
```

Required environment variables for real acquisition:

```bash
export ALPACA_MARKET_DATA_API_KEY=...
export ALPACA_MARKET_DATA_SECRET_KEY=...
export ALPACA_MARKET_DATA_FEED=sip
export MARKET_DATA_ROOT=./data
export MARKET_DATA_MAX_RETRIES=3
export MARKET_DATA_TIMEOUT_SECONDS=30
```

Do not put credentials in shell history, committed files, manifests, or command-line
arguments. The normal test suite uses synthetic data and does not contact Alpaca.
`MARKET_DATA_TIMEOUT_SECONDS` is enforced at the actual Alpaca SDK HTTP request boundary; it
is not merely retry sleep. `MARKET_DATA_MAX_RETRIES` controls retry count separately.

Deterministic artifact rules:

- Raw snapshots are UTF-8 JSON with sorted keys, compact separators, no NaN/Infinity, and a
  trailing newline.
- Canonical bars are UTF-8 CSV with fixed column order, LF line endings, ISO session dates,
  deterministic decimal text, integer-compatible volume, and a trailing newline.
- Manifests are UTF-8 JSON with sorted keys, compact separators, no credentials, and recorded
  version and dependency lineage.

Checksum definitions:

- Source checksum: SHA-256 over stable sanitized raw provider content, request parameters,
  provider identity, pagination metadata, and corporate-action evidence. Volatile retrieval
  timestamp is excluded.
- Canonical content checksum: SHA-256 over canonical rows, canonical schema version,
  provider, feed, timeframe, adjustment mode, and corporate-action policy. Derived lineage
  identifiers and local paths are excluded.
- Artifact checksum: SHA-256 over the complete written raw or canonical artifact bytes.
- Manifest self-checksum: SHA-256 over the manifest with its self-checksum field unset.

Dataset identity:

```text
symbol
+ provider
+ feed
+ timeframe
+ adjustment mode
+ requested date range
+ canonical schema version
+ canonical content checksum
+ corporate-action policy identifier
```

The dataset ID is not derived from clock time, random UUIDs, local absolute paths, usernames,
or the package version.

Idempotent behavior:

- Repeating acquisition with unchanged provider content produces the same canonical content
  checksum and dataset ID.
- Existing matching artifacts are reused without destructive rewrite.
- Existing conflicting artifacts fail closed.
- Changing provider content, provider, feed, date range, schema, or adjustment mode changes
  the dataset identity or fails explicitly.
- Raw, canonical, and manifest writes are handled as one multi-artifact operation. If a
  later artifact fails after an earlier artifact was newly created by the same attempt, the
  newly created files are removed best-effort while preserving valid matching artifacts that
  existed before the attempt.
- Acquisition captures the current timestamp once and reuses it for provider snapshot
  timestamping, incomplete-session validation, manifest reasoning, and lineage decisions.

Verify an existing local dataset without network access:

```bash
python -m spy_market_agent.market_data.cli verify \
  --manifest data/manifests/alpaca/SPY/1Day/sip/all/DATASET_ID.manifest.json \
  --data-root ./data
```

The manifest path is an example shape. Replace `DATASET_ID` with a real local ignored
dataset ID.

Verification is a deep offline reconstruction, not only a byte-hash check. It loads and
validates the manifest, verifies the manifest self-checksum, resolves paths under the
approved data root, verifies raw and canonical artifact hashes, parses the sanitized raw JSON
snapshot, parses canonical CSV into typed daily bars, recomputes source and canonical content
checksums, recomputes the dataset ID, confirms filenames and recorded generated paths, checks
row count and first/last sessions, reruns OHLCV and XNYS validation from the recorded
retrieval timestamp, and fails closed on mismatches.

## Version 2 Phase 2 Benchmark Reproducibility

**Accepted and released as `v2.0.0-alpha.2`:** Phase 2 benchmark infrastructure uses
ignored immutable files under `artifacts/benchmarks/<benchmark_id>/`. It does not change the
SQLite schema and does not persist model binaries. The final model is reconstructed from the
locked configuration and refit deterministically from verified data.

The owner-run primary benchmark used dataset ID `spy-v2p1-825930b0a2bcab20c733b867` and
benchmark ID `spy-v2p2-a065593e952e6a9d96f4be86`. Dataset verification, completed benchmark
verification, runtime-lineage verification, one controlled final-test execution, and quality
gates passed. Raw provider data, benchmark JSON artifacts, row-level labels, and generated
reports remain local ignored artifacts and are not committed.

Benchmark preparation accepts a Phase 1 manifest path only. It deep-verifies the Phase 1 raw,
canonical, and manifest artifacts before loading canonical daily bars, then reuses the
approved feature, label, model, strategy, risk, and backtest implementations.

Benchmark identity is SHA-256 over canonical JSON containing stable values: dataset ID,
canonical checksum, provider/feed/adjustment mode, feature and label schema IDs, forecast
horizon, deterministic split boundaries, candidate model configurations, random seed,
selection rule, signal and risk policy, baseline definitions, strategy comparator
definitions, full cost matrix, exact regime definitions and attribution rule, frozen
training volatility threshold, code commit, Python version, package version, dependency
versions, and artifact schema versions. It excludes clock time, usernames, hostnames, local
absolute paths, credentials, and random UUIDs.

Phase 2 generated JSON uses sorted keys, compact separators, no NaN/Infinity, deterministic
Decimal strings, LF line endings, and a terminal newline. Every artifact is covered by
checksums and `artifact_index.json`. `benchmark verify` performs deep offline semantic
verification rather than trusting hashes alone: it validates artifact schemas, verifies the
Phase 1 dataset, reconstructs features, labels, split sessions, eligibility, benchmark
identity, policies, validation artifacts, final-test locks, and completed final-test
relationships for the declared workflow stage. It rejects semantic tampering even when
`artifact_index.json` was recomputed.

Critical benchmark stages enforce frozen runtime lineage against the lock: Git commit SHA,
Python version, package/runtime version, pandas, pydantic, scikit-learn,
exchange-calendars, alpaca-py, and all dependencies included in the benchmark identity.
`run-validation`, `finalize-lock`, `run-final-test`, audit replay, and `benchmark verify
--require-runtime-lineage` fail closed on mismatches and never update the lock
automatically.

The deterministic split is positional: six supervised rows are excluded at each boundary,
assignable rows are split 70% train, 15% validation, and all rounding remainder goes to the
final test. Split construction is independent of model outcomes and fails closed on row or
class-count minimums.

Stage A validation receives row-level training and validation labels only. Final-test
row-level labels are guarded until `final_test_lock.json` exists and
`run-final-test --acknowledge-final-test-access` writes immutable started-access evidence to
`final_test_access.json`. Successful completion is recorded separately in
`final_test_completion.json`; audit replay never creates a new access record or overwrites
accepted artifacts.

Phase 2 strategy diagnostics reuse the approved Version 1 risk/backtest path for selected
model and executable long/cash comparator transitions. Generated strategy artifacts retain
proposed orders, risk decisions, fills, costs, slippage, portfolio states, ending cash, and
ending shares. Regime diagnostics are descriptive, use validation only during Stage A, and
report explicit undefined reasons for metrics that are not mathematically meaningful.

Normal automated tests use synthetic Phase 1 manifests and make no network request. The
accepted owner-run benchmark is recorded only as sanitized summary evidence. The final-test
result showed weak predictive discrimination and must not be used for Phase 3 tuning.

## Version 2 Phase 3 Research Reproducibility

**Accepted and released as `v2.0.0-alpha.3`:** Phase 3 defines walk-forward research rules
under `docs/V2_PHASE_03_WALK_FORWARD_RESEARCH_SPEC.md`. PR #24 merged the approved
framework and initial scaffolding; PR #25 merged a manual, offline, classification-first
development campaign under `src/spy_market_agent/research`; owner development testing
completed locally; and Alpha 3 release preparation was merged and tagged. Package/runtime
version is `2.0.0a3`. The scientific outcome was `NO CANDIDATE PROMOTION`.

The recommended primary protocol is expanding-window walk-forward validation:

- minimum initial training rows: 756;
- six supervised rows excluded after each training window for mandatory gap and label
  purge;
- 126 supervised-row assessment windows by default;
- 63 supervised-row step size by default;
- deterministic fold identities from dataset, feature, label, fold policy, boundaries, and
  code lineage.

Phase 3 experiment records must capture dataset ID and checksum, provider/feed/timeframe,
adjustment mode, feature schema, label schema, exact fold boundaries, model configuration,
hyperparameter search space, calibration policy, threshold policy, metric definitions,
selection rule, candidate-selection configuration, Git SHA, package/runtime version, Python
version, and dependency versions. Fold and experiment identities are deterministic from
stable dataset, feature, label, policy, boundary, configuration, selection-threshold, and
code-lineage inputs. Creation timestamps are metadata only and do not define experiment
identity.

Generated real-data research artifacts remain ignored under
`artifacts/research/<experiment_id>/`. Normal tests must remain offline and synthetic. The
already-opened Phase 2 final test is frozen baseline evidence and must not be loaded or used
to choose Phase 3 features, models, hyperparameters, calibration, thresholds, or report
framing.

For development campaigns derived from the accepted Phase 2 parent dataset, reproducibility
also includes the session-level final-test exclusion boundary. The runner reconstructs the
frozen Phase 2 split from the verified Phase 1 lineage, truncates the eligible market-data
slice before `build_forward_label_set(...)`, records the parent dataset ID/checksum, the
research-slice ID/checksum, eligible development session range, exclusion policy, and
excluded-session count, and makes those fields identity-defining. Any fold, calibration
split, diagnostic assessment, or model-selection surface that intersects Phase 2 final-test
prediction sessions fails closed.

The committed development campaign configuration is
`configs/research/phase3_development_campaign.json`. It predeclares the global 60-session
feature warm-up, fold policy, diagnostic threshold, reliability bin count, candidate
selection thresholds, calibration procedure, and regime/drift settings before execution.
Changing selection-sensitive values creates a different stable campaign or experiment
identity. The owner-run development campaign remains under ignored local artifacts and is
represented in tracked files only by sanitized aggregate release evidence. Alpha 3 did not
execute protected evaluation or strategy optimization and did not promote a model.

## Version 2 Phase 4 Shadow Reproducibility

**Accepted and released as `v2.0.0-beta.1`:** Phase 4 is governed by
`docs/V2_PHASE_04_REAL_TIME_SHADOW_MODE_SPEC.md`. The package/runtime version is
`2.0.0b1`, and the public beta tag points to
`1c8feae478c0f5536b2193eeb408e580f3f7e33c`.

The observation-only shadow pipeline records deterministic identity policy before
model-connected operation is authorized. It consumes verified local Phase 1 manifests only;
canonical bars are loaded after deep manifest verification succeeds. A shadow run identity is
derived from stable inputs:

- symbol `SPY`;
- signal session;
- mode, initially `observation_only_no_model`;
- data snapshot lineage and canonical checksum;
- feature schema;
- model metadata and checksum when a future model is approved;
- configuration version;
- provider finalization policy identifier.

Shadow run identity excludes clock time, usernames, hostnames, absolute local paths,
credentials, random UUIDs, and raw provider payloads. Duplicate logical runs must fail
closed. The dedicated shadow SQLite database uses schema version
`spy-v2-phase4-shadow-db-v1`, enforces unique `shadow_run_id`, records sanitized
`shadow_runs`, `shadow_input_snapshots`, `shadow_health_events`, and `shadow_alerts`, and
rejects completed, blocked, failed, reserved, incomplete, or unknown prior records rather
than silently overwriting them.

Scheduled Observation Operations V1 reuses the same identity as manual observation for
identical manifest, target session, `as_of`, provider-finalization policy, configuration,
and data lineage. `schedule-preview` derives the latest completed XNYS session from
explicit UTC `as_of`, validates local lineage and compatible history, and is read-only; it
does not create a missing shadow database or mutate existing run lifecycle.
`run-due-observation` resolves the same due session, treats a terminal existing run as an
`already_processed` no-op, fails closed on `reserved` recovery-required state, and delegates
to `run_observation(...)` at most once only when the current due session is eligible. Missed
shadow-operation sessions are reported as operational-history gaps and are never backfilled
automatically. The shadow DB schema version remains `spy-v2-phase4-shadow-db-v1`.

Current model-admission state is `NO APPROVED SHADOW MODEL`. Observation-only mode may
evaluate daily SPY/XNYS session readiness, provider finalization evidence, monitoring state,
and local alerts, but it cannot generate model predictions, real-data `LONG`/`CASH`
proposals, or operational `ShadowProposal` records. Model-connected mode requires a future
separately approved immutable model-admission record and otherwise raises
`blocked_no_approved_model`.

Owner acceptance evidence is recorded in
`docs/V2_PHASE_04_BETA1_RELEASE_EVIDENCE.md`. Normal tests for Phase 4 remain offline,
synthetic, credential-free, broker-free, and do not run real owner research, protected
evaluation, paper execution, live execution, unattended schedulers, daemons, background
loops, or market-data acquisition.

## Version 2 Phase 5 Paper-Operation Scaffold Reproducibility

**Active specification + non-submitting safety/recovery scaffold:** Phase 5 is governed by
`docs/V2_PHASE_05_PRODUCTION_PAPER_OPERATION_SPEC.md` for target future release
`v2.0.0-beta.2`. Package/runtime remains `2.0.0b1`, and no beta.2 tag exists.

The `spy_market_agent.paper_ops` package is deterministic and offline. Its gate decisions
are fixed by the current approved Phase 5 state:

- P5-A infrastructure entry: `AUTHORIZED`;
- P5-B Phase 5 broker submission: `BLOCKED PENDING SEPARATE OWNER AUTHORIZATION`;
- P5-C model-connected paper operation: `BLOCKED_NO_APPROVED_PAPER_MODEL`.

Recovery classification is a pure mapping from the existing paper-attempt status to an
operator disposition. It does not read SQLite files, environment variables, credentials,
market data, settings, broker state, or model artifacts. Unknown states fail closed as
`INVALID_STATE`. `reserved` and `submission_unknown` require reconciliation by
`client_order_id`; no state permits automatic resubmission.

## Checksums and Schema Versions

Validated data and downstream artifacts retain explicit schema and checksum lineage:

- market-data schema: `spy-daily-ohlcv-v1`
- feature schema: `spy-daily-features-v1`
- label schema: `spy-open-t1-to-open-t6-net-positive-v1`
- model schema: `spy-binary-models-v1`
- strategy schema: `spy-long-cash-strategy-v1`
- risk schema: `spy-long-only-risk-v1`
- backtest schema: `spy-daily-next-open-backtest-v1`
- paper-execution schema: `spy-paper-execution-v1`
- SQLite schema: `spy-sqlite-persistence-v2`
- benchmark artifact schema: `spy-v2-phase2-benchmark-artifacts-v1`
- shadow artifact schema: `spy-v2-phase4-shadow-v1`
- shadow SQLite schema: `spy-v2-phase4-shadow-db-v1`

Market-data checksums are deterministic over canonical rows, column order, session order,
prices, and volume. Backtest results also retain source market data and execution-price
lineage so persisted artifacts can be reconstructed and revalidated.

## Split Specifications

Chronological splits are explicit date-boundary objects. They do not shuffle observations.
Rows are included only when the feature session and the label exit session fit inside the
partition boundary. The final test partition is not used for model selection.

## Git and Dependency Lineage

Persisted model evaluations and backtests can record Git commit hash, Python version, and
dependency versions through `RuntimeSnapshot`. This metadata helps explain when an artifact
was produced and what package versions were present.

## SQLite Initialization

SQLite initialization is explicit and idempotent:

```bash
python -c "from spy_market_agent.persistence import initialize_database; initialize_database('./spy_market_agent.sqlite3')"
SQLITE_DATABASE_PATH=./spy_market_agent.sqlite3 \
python -m uvicorn "spy_market_agent.api.main:create_app" \
  --factory \
  --host 127.0.0.1 \
  --port 8000
```

Imports, API startup, and dashboard startup do not initialize or migrate SQLite files. Read
paths fail safely when a database is unavailable, uninitialized, corrupted, or on an unsupported
future schema.

## Reconstructing Persisted Artifacts

`SQLiteArtifactRepository` supports loading persisted market-data batches, final model
evaluations, and backtest results. Load paths reconstruct domain objects and rerun the
existing validation, checksum, schema, lineage, accounting, metric, and risk checks instead of
trusting stored rows blindly.

`SQLitePaperExecutionRepository` reconstructs local paper-execution control state, attempts,
and events from the durable ledger. It validates schema version, IDs, checksums, attempt
states, booleans, timestamps, and the required symbol/session uniqueness index.

## Limitations

- No lock file is committed, so dependency resolution can vary.
- No committed real SPY dataset exists.
- Historical adjusted-price backtests approximate return distributions and corporate-action
  effects; they do not guarantee executable fills.
- Classification metrics and backtests are diagnostics only and are not evidence of
  profitability.
