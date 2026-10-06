# Phase 7 Review: Persistence, API, and Dashboard

## Phase Objective

Implement explicitly invoked local persistence and read-only presentation for completed SPY research artifacts. Phase 7 stores approved artifacts in SQLite, reloads them through existing validation paths, exposes them through a read-only FastAPI API, and displays them in a read-only Streamlit dashboard.

This phase does not train models, generate probabilities, create signals, run backtests, submit orders, download market data, schedule jobs, communicate with brokers, or deploy services.

## Git Workflow

- Approved stable branch: `main`
- Review branch: `review/phase-07-persistence-api-dashboard`
- Starting `main` commit SHA: `d773fbb3b86b73dccd1644ab885f8c6f79c58574`
- The starting worktree was clean before the branch was created.
- The branch was created from the latest `origin/main` after `git fetch origin`, `git switch main`, and `git pull --ff-only origin main`.

## Files Created

- `src/spy_market_agent/persistence/models.py`
- `src/spy_market_agent/persistence/database.py`
- `src/spy_market_agent/persistence/schema.py`
- `src/spy_market_agent/persistence/serialization.py`
- `src/spy_market_agent/persistence/repositories.py`
- `src/spy_market_agent/api/main.py`
- `src/spy_market_agent/api/schemas.py`
- `src/spy_market_agent/api/services.py`
- `src/spy_market_agent/dashboard/app.py`
- `src/spy_market_agent/dashboard/client.py`
- `src/spy_market_agent/dashboard/streamlit_app.py`
- `tests/unit/phase7_helpers.py`
- `tests/unit/test_persistence_serialization.py`
- `tests/unit/test_persistence_repositories.py`
- `tests/unit/test_api_phase7.py`
- `tests/unit/test_dashboard_phase7.py`
- `tests/unit/test_public_phase7_api.py`
- `tests/integration/test_phase7_persistence_api_dashboard_flow.py`
- `reviews/PHASE_07_REVIEW.md`

## Files Modified

- `.env.example`
- `README.md`
- `pyproject.toml`
- `src/spy_market_agent/config/settings.py`
- `src/spy_market_agent/persistence/__init__.py`
- `src/spy_market_agent/api/__init__.py`
- `src/spy_market_agent/dashboard/__init__.py`

## Dependencies Added

- `fastapi>=0.115,<1`: read-only API application and typed response validation.
- `httpx>=0.27,<1`: dashboard HTTP client and deterministic API client tests.
- `streamlit>=1.37,<2`: read-only dashboard rendering.

No ORM, migration framework, broker SDK, scheduler, authentication framework, charting framework, task queue, or unrelated dependency was added. MyPy has a narrow missing-type override for `streamlit` and `streamlit.*`.

## Configuration

- Added `SQLITE_DATABASE_PATH` with safe local default `./spy_market_agent.sqlite3`.
- Added `DASHBOARD_API_BASE_URL` with safe local default `http://127.0.0.1:8000`.
- Added bounded `API_TIMEOUT_SECONDS` with default `5.0`.
- Existing safety defaults remain unchanged:
  - `EXECUTION_MODE=paper`
  - `ENABLE_PAPER_EXECUTION=false`
  - `DRY_RUN=true`
- Settings construction does not create database directories, initialize databases, load credentials, or perform external actions.

## Persistence Schema

- Schema version: `spy-sqlite-persistence-v1`
- Version table: `schema_migrations`
- Initialization: `initialize_database(...)` is explicit and idempotent.
- Every connection enables `PRAGMA foreign_keys = ON`.
- Database creation is disabled for normal repository reads and happens only through explicit initialization.
- Connections are opened per operation and closed deterministically.
- Saves use complete transactions and roll back on nested insert failures.
- Duplicate run IDs fail through `PersistenceConflictError`.
- Unsupported future schema versions fail through `PersistenceSchemaError`.

## Tables and Foreign Keys

- `market_data_batches`
- `market_data_rows` -> `market_data_batches`
- `model_runs`
- `model_validation_metric_snapshots` -> `model_runs`
- `model_candidate_parameters` -> `model_runs`
- `model_predictions` -> `model_runs`
- `model_final_metrics` -> `model_runs`
- `backtest_runs`
- `backtest_source_market_metadata` -> `backtest_runs`
- `backtest_source_market_rows` -> `backtest_runs`
- `backtest_strategy_signals` -> `backtest_runs`
- `backtest_execution_prices` -> `backtest_runs`
- `backtest_proposed_orders` -> `backtest_runs`
- `backtest_risk_decisions` -> `backtest_runs` and `backtest_proposed_orders`
- `backtest_fills` -> `backtest_runs` and `backtest_risk_decisions`
- `backtest_portfolio_rows` -> `backtest_runs`
- `backtest_metrics` -> `backtest_runs`

Ordered audit rows preserve explicit `sequence_number` values.

## Canonical Serialization Rules

- Dates are stored as ISO `YYYY-MM-DD`.
- Timezone-aware datetimes are stored as UTC ISO-8601 text.
- `Decimal` values are stored as exact decimal strings.
- Booleans are stored as canonical SQLite integers and validated on load.
- Tuples and structured metadata are stored as deterministic JSON with sorted keys.
- SHA-256 checksums are validated as lowercase 64-character hexadecimal strings.
- Schema versions and symbols are explicitly validated.
- NaN and infinity are rejected before persistence or JSON serialization.
- No pandas objects are stored as pickle blobs.
- No fitted estimator object is persisted.

## Persisted Artifact Coverage

- `MarketDataBatch`: metadata, canonical daily SPY rows, checksum, provider/source, adjustment policy, session bounds, row count, schema version, and timestamps. Loads reconstruct the existing validated domain object.
- `FinalTestEvaluation`: run metadata, selected model name, locked model-selection decision, selection reason, validation metric snapshots, candidate parameters, final-test predictions, final metrics, split spec, seed, threshold, source checksum, schema versions, feature-column order, scikit-learn version, timestamp, Git commit, Python version, and dependency snapshot. Fitted estimators are not persisted.
- `BacktestResult`: metadata, owned source `MarketDataBatch`, `StrategySignalSet`, `ExecutionPriceSet`, proposed orders, risk decisions, fills, portfolio rows, `BacktestMetrics`, `BacktestConfig`, cost assumptions, `RiskConfig`, selected-model lineage, schema lineage, split spec, session bounds, checksums, timestamp, Git commit, Python version, and dependency snapshot. Loads reconstruct `BacktestResult` and rerun Phase 6 audit validation.

## Read Service Layer

Routes call `ReadService` instead of containing SQL or research logic. The service supports deterministic reads for data status, model summaries/details/metrics/predictions, backtest summaries/details/metrics, equity/drawdown rows, orders, risk decisions, fills, risk configuration, and rejection summaries.

The service does not train models, generate predictions, generate signals, evaluate risk, run backtests, submit orders, or initialize databases.

## API Route Inventory

- `GET /health`
- `GET /api/v1/data/status`
- `GET /api/v1/model-runs`
- `GET /api/v1/model-runs/{run_id}`
- `GET /api/v1/model-runs/{run_id}/predictions`
- `GET /api/v1/backtests`
- `GET /api/v1/backtests/{run_id}`
- `GET /api/v1/backtests/{run_id}/equity`
- `GET /api/v1/backtests/{run_id}/orders`
- `GET /api/v1/backtests/{run_id}/risk-decisions`
- `GET /api/v1/backtests/{run_id}/fills`

All application routes are read-only. There are no `POST`, `PUT`, `PATCH`, or `DELETE` application routes. Health checks do not mutate or initialize the database. Unknown run IDs return structured 404 responses. Invalid or unavailable databases return sanitized service errors without raw SQL, stack traces, credentials, or absolute database paths.

## Dashboard View Inventory

- Overview: educational warning, API status, data availability, latest model run, latest backtest run.
- Data Quality: SPY symbol, provider/source, adjustment policy, session bounds, row count, checksum, schema version, timestamps.
- Model Evaluation: selected model, rationale, validation metrics, final-test metrics, prediction table, profitability limitation.
- Backtest Results: initial/final equity, total return, maximum drawdown, turnover, exposure, costs, order/fill counts, equity and drawdown rows.
- Risk and Audit: SPY-only long-only configuration, no leverage, no fractional shares, maximum position weight, approved/rejected counts, rejection reasons, orders, decisions, and fills.

The dashboard obtains data through the FastAPI HTTP client only. It does not import persistence repositories or query SQLite directly. It has no controls for training, backtesting, signal generation, order approval, broker settings, or order submission.

## Safety Warnings Shown

- API responses include: `Educational and experimental research output only. Not investment advice and not proof of profitability.`
- Dashboard warning: `Educational and experimental research output only. Not investment advice and not proof of profitability. Historical backtests and classification metrics are limited diagnostics.`
- Model detail responses state that classification metrics do not establish profitability.
- Backtest detail responses state that historical backtests are limited diagnostics.

## Tests Added

- Persistence serialization tests for dates, UTC datetimes, decimals, booleans, deterministic JSON, checksum validation, and non-finite rejection.
- Persistence repository tests for initialization, idempotence, foreign keys, save/load round trips, row order, mutation isolation, duplicate IDs, transaction rollback, missing records, tampered rows, checksum revalidation, and unsupported schema versions.
- API tests for app factory behavior, health, empty database responses, every read endpoint, structured 404s, sanitized database errors, pagination validation, chronological ordering, monetary serialization, and absence of state-changing routes.
- Dashboard tests for HTTP client parsing, API unavailable state, empty/populated state, warning text, import smoke behavior, no database access, no broker/write controls, and public exports.
- Public import/export tests for `spy_market_agent.persistence`, `spy_market_agent.api`, and `spy_market_agent.dashboard`.
- End-to-end Phase 3-7 integration test that persists and reloads deterministic artifacts, reconstructs domain objects, calls FastAPI through an in-process test client, and exercises dashboard state loading without external network or broker actions.

## Baseline Verification Results

Baseline was run before implementation on branch `review/phase-07-persistence-api-dashboard` at starting SHA `d773fbb3b86b73dccd1644ab885f8c6f79c58574`.

- `pytest`: `424 passed, 4 warnings`, coverage `79%`.
- `pytest tests/unit -q`: exited `0`; unit suite passed with the existing third-party warnings, coverage `79%`.
- `pytest tests/integration -q`: `4 passed, 4 warnings`, coverage `69%`.
- `ruff check .`: `All checks passed!`
- `ruff format --check .`: `73 files already formatted`
- `mypy src tests`: `Success: no issues found in 65 source files`
- `python -c "import spy_market_agent; print(spy_market_agent.__version__)"`: `0.1.0`
- `python -c "import spy_market_agent.strategies as strategies; print(sorted(strategies.__all__))"`: passed and printed the Phase 6 strategy exports.
- `python -c "import spy_market_agent.risk as risk; print(sorted(risk.__all__))"`: passed and printed the Phase 6 risk exports.
- `python -c "import spy_market_agent.backtesting as backtesting; print(sorted(backtesting.__all__))"`: passed and printed the Phase 6 backtesting exports.
- `git diff --check`: exited `0` with no output.

## Final Verification Results

- `pytest`: `447 passed, 5 warnings in 40.61s`, coverage `80%`.
- `pytest tests/unit -q`: exited `0`; unit suite passed, `5 warnings`, coverage `80%`.
- `pytest tests/integration -q`: `5 passed, 5 warnings`, coverage `71%`.
- `ruff check .`: `All checks passed!`
- `ruff format --check .`: `92 files already formatted`
- `mypy src tests`: `Success: no issues found in 83 source files`
- `python -c "import spy_market_agent; print(spy_market_agent.__version__)"`: `0.1.0`
- `python -c "import spy_market_agent.strategies as strategies; print(sorted(strategies.__all__))"`: `['SIGNAL_COLUMNS', 'STRATEGY_LONG_PROBABILITY_THRESHOLD', 'STRATEGY_SCHEMA_VERSION', 'StrategyError', 'StrategyInputError', 'StrategyIssue', 'StrategySignalSet', 'build_long_cash_strategy_signals']`
- `python -c "import spy_market_agent.risk as risk; print(sorted(risk.__all__))"`: `['APPROVED_REASON', 'BUY_SIDE', 'EXECUTION_NOT_AFTER_SIGNAL', 'FRACTIONAL_QUANTITY_FORBIDDEN', 'FULL_EXIT_REQUIRED', 'INSUFFICIENT_CASH', 'INVALID_PORTFOLIO_STATE', 'INVALID_PRICE', 'INVALID_TARGET_TRANSITION', 'LEVERAGE_FORBIDDEN', 'MAXIMUM_POSITION_EXCEEDED', 'MISSING_REQUIRED_INFORMATION', 'ORDER_COST_ESTIMATE_MISMATCH', 'PYRAMIDING_FORBIDDEN', 'PortfolioState', 'ProposedOrder', 'RISK_SCHEMA_VERSION', 'RiskConfig', 'RiskDecision', 'RiskError', 'RiskInputError', 'RiskIssue', 'SELL_QUANTITY_EXCEEDS_POSITION', 'SELL_SIDE', 'SHORT_SELLING_FORBIDDEN', 'SUPPORTED_SYMBOL', 'UNSUPPORTED_SYMBOL', 'evaluate_order_risk']`
- `python -c "import spy_market_agent.backtesting as backtesting; print(sorted(backtesting.__all__))"`: `['BACKTEST_SCHEMA_VERSION', 'BacktestAccountingError', 'BacktestConfig', 'BacktestCostAssumptions', 'BacktestError', 'BacktestInputError', 'BacktestIssue', 'BacktestMetricError', 'BacktestMetrics', 'BacktestResult', 'EXECUTION_PRICE_COLUMNS', 'ExecutionPriceSet', 'FILL_COLUMNS', 'FillRecord', 'INITIAL_SIMULATED_CASH', 'OrderCostEstimate', 'PORTFOLIO_COLUMNS', 'PROPOSED_ORDER_COLUMNS', 'RISK_DECISION_COLUMNS', 'TRADING_SESSIONS_PER_YEAR', 'calculate_backtest_metrics', 'estimate_order_cost', 'maximum_affordable_buy_quantity', 'run_long_or_cash_backtest']`
- `python -c "import spy_market_agent.persistence as persistence; print(sorted(persistence.__all__))"`: `['BacktestRunSummary', 'ModelRunSummary', 'PERSISTENCE_SCHEMA_VERSION', 'PersistenceConflictError', 'PersistenceError', 'PersistenceInputError', 'PersistenceIntegrityError', 'PersistenceNotFoundError', 'PersistenceSchemaError', 'RuntimeSnapshot', 'SQLiteArtifactRepository', 'connect_database', 'initialize_database']`
- `python -c "import spy_market_agent.api as api; print(sorted(api.__all__))"`: `['BacktestDetailResponse', 'BacktestRunListResponse', 'DEFAULT_SQLITE_DATABASE_PATH', 'DataStatusResponse', 'EDUCATIONAL_WARNING', 'HealthResponse', 'MAX_PAGE_LIMIT', 'ModelRunDetailResponse', 'ModelRunListResponse', 'ReadService', 'create_app']`
- `python -c "import spy_market_agent.dashboard as dashboard; print(sorted(dashboard.__all__))"`: `['DASHBOARD_WARNING', 'DashboardApiClient', 'DashboardApiError', 'DashboardState', 'create_default_client', 'load_dashboard_state', 'main', 'render_dashboard']`
- `git diff --check`: exited `0` with no output.

Additional smoke checks:

- FastAPI app factory smoke: emitted the existing Starlette/TestClient deprecation warning, then returned `200` for `/health` and `200` for `/api/v1/data/status` against an initialized empty temporary database.
- Dashboard import/state smoke: printed `True None None True`, confirming available API state, no selected model/backtest in an empty response, and required warning text.

## Coverage

- Baseline full-suite coverage: `79%`.
- Final full-suite coverage: `80%`.
- Final unit-suite coverage: `80%`.
- Final integration-suite coverage: `71%`.

## Warnings

Warnings were not suppressed or hidden.

- Existing third-party `exchange_calendars`/pandas/NumPy `DeprecationWarning` for generic timedeltas.
- New third-party `StarletteDeprecationWarning` from `fastapi.testclient` recommending `httpx2`.

## Known Limitations

- SQLite schema support is version 1 only; unsupported future versions fail closed.
- There is no CLI for persisting artifacts; persistence is explicit through the repository API.
- The API reads only already-initialized and already-populated SQLite databases.
- The dashboard requires a reachable local FastAPI API for populated views and handles unavailable or empty API responses gracefully.
- Phase 6 audit DataFrames contain float-valued price/accounting fields, so SQLite stores those existing fields as `REAL`; project `Decimal` configuration values are stored as exact decimal strings.
- There is no authentication, CORS, multi-user support, scheduler, background worker, deployment configuration, broker integration, or order submission.

## Git Status and Diff Summary

Pre-commit status consists of the modified files and new Phase 7 files listed above. `git diff --check` and `git diff --check --cached` are clean.

Staged diff summary before commit:

```text
26 files changed, 4911 insertions(+), 12 deletions(-)
```

## Phase Boundary Confirmation

Phase 8 was not started.

No broker communication, Alpaca integration, broker SDK, broker account check, paper-order submission, live-order submission, execution adapter, order approval interface, kill-switch execution behavior, scheduler, background worker, authentication system, deployment configuration, market-data downloading, model retraining endpoint, backtest-running endpoint, signal-generation endpoint, probability calibration, threshold optimization, hyperparameter tuning, model binary persistence, non-SPY asset support, short selling, leverage, margin, or fractional-share behavior was introduced.

## Phase 7 Correction Addendum

Correction branch: `review/phase-07-corrections`.

Starting main SHA: `d773fbb3b86b73dccd1644ab885f8c6f79c58574`.

Original Phase 7 implementation SHA: `0c0137177101400ae33756cf46b214121ede5a17`.

Cherry-picked Phase 7 commit SHA on the correction branch: `84da8c2fd95cb36503b5d0b35b8f1451cfcd1d8b`.

Prior correction commit SHA: `2b1cb5210fdf29c32a37173d4365aac85b5fc4c9`.

Final hardening branch: `review/phase-07-final-hardening`.

Final hardening commit SHA: reported in the final Codex response after the commit is created and pushed.

Findings corrected:

- Added plain `uvicorn>=0.30,<1` as a runtime dependency because README documents `python -m uvicorn "spy_market_agent.api.main:create_app" --factory --host 127.0.0.1 --port 8000`.
- Added a Uvicorn factory-resolution smoke test that loads `spy_market_agent.api.main:create_app` without creating SQLite files or starting a blocking server.
- Added a shared URL-safe run-id contract: `^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$`.
- Applied the run-id contract to persistence save/load boundaries, FastAPI path parameters, and dashboard path construction.
- Hardened public repository read paths so malformed stored values raise project-owned `PersistenceError` subclasses instead of raw `ValueError`, `TypeError`, `OverflowError`, pandas conversion errors, Pydantic `ValidationError`, `ModelingError`, `StrategyError`, `RiskError`, or `BacktestError`.
- Updated `canonical_json_loads` to reject non-standard `NaN`, `Infinity`, and `-Infinity` constants.
- Added storage-read helpers for required strings, optional strings, run IDs, finite floats, exact integer conversion, nonnegative integer constraints, canonical booleans, checksums, dates, timestamps, exact decimals, ordered string tuples, and pandas dtype conversions.
- Updated corrupted-data API behavior so corrupted persistence returns sanitized `503` JSON with the fixed message `Persisted research data is unavailable or invalid.`
- Updated API request-error behavior so invalid run IDs are client errors and generic `ValueError` messages are not echoed.
- Preserved dashboard pagination metadata for paginated API responses and added a typed `PaginatedItems` representation.
- Changed dashboard approved/rejected/order/fill totals to come from authoritative backtest detail metrics, not first-page risk decisions.
- Added visible `Showing X of Y` labels for bounded prediction, order, risk-decision, fill, and equity tables.
- Changed equity/drawdown chart loading to retrieve all equity pages using the maximum API page size and reject inconsistent totals, duplicate rows, and non-progressing pagination loops.
- Added chart-only finite numeric conversion for equity and drawdown values while preserving exact monetary strings in rendered dataframes.

Files modified or created in this correction:

- `README.md`
- `pyproject.toml`
- `reviews/PHASE_07_REVIEW.md`
- `src/spy_market_agent/run_ids.py`
- `src/spy_market_agent/api/main.py`
- `src/spy_market_agent/dashboard/app.py`
- `src/spy_market_agent/dashboard/client.py`
- `src/spy_market_agent/persistence/repositories.py`
- `src/spy_market_agent/persistence/serialization.py`
- `tests/unit/test_api_phase7.py`
- `tests/unit/test_dashboard_phase7.py`
- `tests/unit/test_persistence_repositories.py`
- `tests/unit/test_persistence_serialization.py`

Persistence conversion rules:

- Required text must be stored as actual non-empty SQLite text. `NULL` is not converted to `"None"`.
- Optional text accepts only SQLite `NULL` or actual text.
- Floats must convert successfully and must reject booleans, NaN, positive infinity, and negative infinity.
- Integers may be stored as SQLite integers, finite integral SQLite reals, or exact integer text without surrounding whitespace; booleans, non-integral floats, and malformed text are rejected. Nonnegative constraints are applied where required.
- Booleans remain canonical SQLite integers `0` or `1`.
- Pandas dtype conversion failures are translated to `PersistenceIntegrityError`.
- Checksums must be lowercase 64-character SHA-256 values.
- Canonical JSON loads reject non-standard/non-finite constants.

Tampering regression coverage added for malformed values in:

- Market-data OHLC values.
- Market-data volume.
- Market-data row counts.
- Market-data metadata timestamps.
- Model prediction probabilities.
- Predicted classes and targets.
- Candidate validation metrics.
- Final classification metrics.
- Model parameter JSON containing `NaN` and `Infinity`.
- Backtest strategy rows.
- Execution-price rows.
- Proposed order rows.
- Risk-decision booleans and numeric values.
- Fill rows.
- Portfolio rows.
- Backtest metrics.
- Model-run summary rows.
- Backtest summary rows.

Run-id regression coverage added for:

- Valid alphanumeric IDs.
- Valid period, underscore, and hyphen.
- Empty IDs.
- Whitespace-only IDs.
- Leading whitespace.
- Trailing whitespace.
- Internal spaces.
- Slash.
- Backslash.
- Percent character.
- Question mark.
- Hash.
- Ampersand.
- Colon.
- Encoded path tricks.
- 128-character valid ID.
- 129-character ID.
- Direct repository save/load validation.
- FastAPI route validation.
- Dashboard path construction.

Dashboard regression coverage added for:

- More than 250 risk decisions with authoritative approved/rejected totals.
- More than 250 orders and fills with `Showing X of Y` labels.
- More than 100 model predictions with a preview label.
- More than 500 equity rows collected across pages in chronological order.
- Inconsistent equity pagination totals.
- Non-progressing equity pagination loops.
- Empty paginated responses.
- API unavailability during a later equity page request.
- Valid decimal chart strings converted only in a plotting copy.
- Exact-string table values preserved.
- Malformed, NaN, and infinite chart values rejected gracefully.

Baseline verification before corrections on the cherry-picked Phase 7 tree:

- `pytest`: passed, `447 passed`, `5 warnings`, total coverage `80%`.
- `pytest tests/unit -q`: exited `0`, unit suite passed with `5 warnings`, total coverage `80%`.
- `pytest tests/integration -q`: exited `0`, `5` integration tests passed, `5 warnings`, integration-only coverage `71%`.
- `ruff check .`: passed with `All checks passed!`.
- `ruff format --check .`: passed with `92 files already formatted`.
- `mypy src tests`: passed with `Success: no issues found in 83 source files`.
- Package version check printed `0.1.0`.
- Public `__all__` checks for `strategies`, `risk`, `backtesting`, `persistence`, `api`, and `dashboard` passed.
- `git diff --check`: exited `0`.

Final verification after corrections:

- `python -m pip install -e ".[dev]"`: initial sandboxed run failed because PyPI DNS resolution was blocked; the approved escalated rerun passed and installed the editable package with `uvicorn>=0.30,<1` already satisfied as `0.52.0`.
- `pytest`: passed, `531 passed`, `5 warnings in 59.89s`, total coverage `81%`.
- `pytest tests/unit -q`: exited `0`, unit suite passed with `5 warnings`, total coverage `81%`. The command combines repo `-q` and CLI `-q`, so pytest suppresses the final pass-count line; full minus integration corresponds to `526` unit tests.
- `pytest tests/integration -q`: exited `0`, `5` integration tests passed, `5 warnings`, integration-only coverage `71%`.
- `ruff check .`: passed with `All checks passed!`.
- `ruff format --check .`: passed with `93 files already formatted`.
- `mypy src tests`: passed with `Success: no issues found in 84 source files`.
- `python -m uvicorn --version`: printed `Running uvicorn 0.52.0 with CPython 3.12.13 on Darwin`.
- `python -c "import spy_market_agent; print(spy_market_agent.__version__)"`: printed `0.1.0`.
- Public `__all__` checks for `strategies`, `risk`, `backtesting`, `persistence`, `api`, and `dashboard` passed and the Phase 7 public exports remained unchanged.
- `git diff --check`: exited `0`.
- Smoke check loaded the FastAPI factory through TestClient, loaded the Uvicorn factory target, loaded dashboard state through an in-memory fake client, created no SQLite files, used no external network, and invoked no training, backtest, signal, risk, broker, paper-order, or live-order behavior.

Coverage:

- Final full-suite coverage: `81%`.
- Final unit-suite coverage: `81%`.
- Final integration-suite coverage: `71%`.

Remaining warnings:

- Existing third-party `exchange_calendars`/pandas/NumPy `DeprecationWarning` for generic timedeltas.
- Existing third-party `StarletteDeprecationWarning` from `fastapi.testclient` recommending `httpx2`.

Known limitations:

- SQLite schema support remains version 1 only.
- The API remains read-only and reads only initialized, already-populated SQLite databases.
- The dashboard remains read-only and requires a reachable read API for populated views.
- Prediction, order, risk-decision, and fill tables remain bounded previews with explicit counts.
- No authentication, CORS policy, deployment configuration, scheduler, background worker, model binary persistence, write route, training route, backtest-running route, signal-generation route, market-data downloader, broker integration, or order submission is implemented.

Confirmations:

- `main` remained at `d773fbb3b86b73dccd1644ab885f8c6f79c58574` during correction work.
- The original Phase 7 branch `review/phase-07-persistence-api-dashboard` was not modified.
- Phase 8 was not started.
- No Alpaca, broker SDK, broker communication, paper-order submission, live-order submission, execution adapter, order approval control, scheduler, background worker, deployment configuration, authentication, API write route, training route, backtest-running route, signal-generation route, market-data downloading, model binary persistence, additional assets, short selling, leverage, margin, fractional shares, or non-SPY asset behavior was introduced.

## Phase 7 Final Hardening Addendum

Final hardening branch: `review/phase-07-final-hardening`.

Prior correction commit SHA: `2b1cb5210fdf29c32a37173d4365aac85b5fc4c9`.

Final hardening commit SHA: reported in the final Codex response after the commit is created and pushed.

Issue hardened:

- `canonical_json_loads()` already rejected literal `NaN`, `Infinity`, and `-Infinity` through `json.loads(parse_constant=...)`, but JSON numeric overflow such as `1e999` could decode to a non-finite Python float without invoking `parse_constant`.

Correction:

- Added recursive post-decode JSON validation for scalar floats, list elements, and dictionary values.
- Any decoded float where `math.isfinite(value)` is false now raises `PersistenceIntegrityError` with a stable project-owned code and a sanitized message that does not echo the corrupted value.
- The JSON storage format, schema version, deterministic serialization, later domain reconstruction, and checksum validation remain unchanged.

Regression coverage added:

- Direct serializer coverage for `1e999`, `-1e999`, `[1e999]`, `{"value": 1e999}`, and deeply nested overflowing values.
- Direct serializer coverage proving ordinary finite exponent values such as `1e10` and existing valid JSON objects/lists still load.
- Repository tampering coverage for model parameter JSON stored as `1e999`, including `PersistenceIntegrityError` reconstruction failure and sanitized FastAPI `503` behavior.
- Existing `NaN` and `Infinity` tests were retained.

Final verification:

- `python -m pip install -e ".[dev]"`: passed in the existing Python 3.12 environment; `uvicorn>=0.30,<1` was already satisfied as `0.52.0`.
- `pytest`: passed, `539 passed`, `5 warnings in 62.10s`, total coverage `81%`.
- `pytest tests/unit -q`: exited `0`, unit suite passed with `5 warnings`, total coverage `81%`. The command combines repo `-q` and CLI `-q`, so pytest suppresses the final pass-count line; full minus integration corresponds to `534` unit tests.
- `pytest tests/integration -q`: exited `0`, `5` integration tests passed, `5 warnings`, integration-only coverage `71%`.
- `ruff check .`: passed with `All checks passed!`.
- `ruff format --check .`: passed with `93 files already formatted`.
- `mypy src tests`: passed with `Success: no issues found in 84 source files`.
- `python -m uvicorn --version`: printed `Running uvicorn 0.52.0 with CPython 3.12.13 on Darwin`.
- `python -c "import spy_market_agent; print(spy_market_agent.__version__)"`: printed `0.1.0`.
- Public `__all__` checks for `persistence`, `api`, and `dashboard` passed and remained unchanged.
- `git diff --check`: exited `0`.

Confirmation:

- Phase 8 was not started.
- No broker, Alpaca, execution, order-submission, scheduling, authentication, deployment, or API write functionality was introduced.
