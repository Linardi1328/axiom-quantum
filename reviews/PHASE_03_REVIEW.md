# Phase 03 Review

## Phase overview

- Phase number and title: Phase 03, Configuration, Canonical SPY Data Schema, Provider Interface, and Data Validation.
- Objective: Implement typed settings, paper-only configuration validation, provider-independent daily SPY data models, XNYS calendar handling, deterministic canonical OHLCV validation, checksum generation, lineage metadata foundations, tests, and documentation updates.
- Completion status: Fully completed.
- UTC completion timestamp: 2026-07-24 19:05:58 UTC.

## Corrective review addendum

Corrective completion timestamp: 2026-07-24 19:27:02 UTC.

Correction status: Completed. This addendum records the limited Phase 3 corrective pass only. Phase 4 was not started.

Issues corrected:

- Settings immutability: `Settings` now uses Pydantic `frozen=True`, so validated settings cannot be mutated after construction. Tests now cover rejected mutation attempts for `execution_mode`, `market_symbol`, `enable_paper_execution`, and `dry_run`.
- Metadata creation time: `validate_daily_spy_data()` now accepts a separate timezone-aware `created_at`. `as_of` is used only for market-session completeness checks, `downloaded_at` records source download time, and `created_at` records batch creation time.
- Temporal metadata validation: `downloaded_at`, `created_at`, and `as_of` must all be `datetime` instances with timezone information. The validator now rejects `downloaded_at > created_at` with a structured issue.
- Runtime input validation: malformed public inputs now produce `MarketDataValidationError` issues instead of unhandled attribute, type, overflow, or pandas conversion errors where covered by the public validator boundary.
- Provider names: `provider_name` must be a non-blank string, and the trimmed provider name is stored in metadata.
- Column-name validation: mixed-type or non-string DataFrame column names are rejected before canonical schema checks proceed.
- Numeric conversion safety: values that cannot be safely converted to canonical numeric output dtypes are rejected, and volume values outside the signed 64-bit integer range fail validation.
- Boolean OHLCV rejection: boolean values in price or volume columns are explicitly rejected and are not treated as numeric values.
- Calendar identity: `TradingCalendar` exposes `calendar_code`, `XNYSCalendar.calendar_code` returns `XNYS`, and validation rejects non-XNYS calendar implementations even if they satisfy the rest of the protocol.
- Safe settings display: `display_safe_dict()` now redacts username/password information embedded in `database_url`, while Alpaca secrets remain redacted.

Files changed during the corrective pass:

- `src/spy_market_agent/config/settings.py`: Added settings immutability and database URL credential redaction in safe display output.
- `src/spy_market_agent/market_data/calendar.py`: Added the read-only `calendar_code` contract and XNYS implementation.
- `src/spy_market_agent/validation/market_data_checks.py`: Added `created_at`, stronger public input checks, non-XNYS rejection, provider-name trimming, boolean rejection, int64 volume bounds, and safer conversion error handling.
- `tests/unit/test_settings.py`: Added immutability and database URL redaction tests.
- `tests/unit/test_calendar.py`: Added XNYS calendar identifier coverage.
- `tests/unit/test_market_data_validation.py`: Updated validator call sites for `created_at` and added corrective tests for temporal metadata, provider names, mixed column names, boolean OHLCV values, int64 volume bounds, provider-name trimming, and non-XNYS calendars.
- `tests/integration/test_market_data_provider_flow.py`: Updated the deterministic provider flow to pass separate `created_at`.
- `reviews/PHASE_03_REVIEW.md`: Added this corrective-review addendum.

Tests added or updated:

- Settings safety tests now verify immutability for execution mode, market symbol, paper-execution enablement, and dry-run state.
- Settings display tests now verify that database usernames and passwords do not appear in display-safe output.
- Validation tests now verify non-datetime temporal inputs, naive `created_at`, `downloaded_at > created_at`, blank and non-string provider names, trimmed provider metadata, mixed-type columns, boolean price and volume rejection, signed int64 volume limits, and non-XNYS calendar rejection.
- Existing validator and integration tests were updated to use the corrected `created_at` API.

Final corrective command results:

- `pytest`: Passed. Result: 106 passed, 3 third-party warnings, 88% total coverage.
- `pytest tests/unit -q`: Passed. Result: all unit tests passed, with the same 3 third-party warnings and 88% total coverage shown by coverage reporting.
- `pytest tests/integration -q`: Passed. Result: 1 integration test passed, with the same 3 third-party warnings.
- `ruff check .`: Passed with `All checks passed!`.
- `ruff format --check .`: Passed with `32 files already formatted`.
- `mypy src tests`: Passed with `Success: no issues found in 27 source files`.
- `python -c "import spy_market_agent; print(spy_market_agent.__version__)"`: Passed and printed `0.1.0`.
- `git diff --check`: Passed with no whitespace errors.

Corrective virtual-environment note:

- The corrective pass did not delete, replace, recreate, or clean `.venv/` or `venv/`.
- No cleanup command used `rm -rf .venv` or `rm -rf venv`.
- Verification used `/private/tmp/spy-market-agent-phase3-corrective-venv`, outside the project tree.
- Generated caches, coverage output, and egg-info were cleaned without touching project virtual-environment directories.

Remaining warnings and limitations after correction:

- Three visible warnings remain from `exchange-calendars` / pandas / NumPy timedelta internals. They were not globally suppressed.
- No real market-data provider, download path, feature engineering, label generation, model, strategy, backtest, risk calculation, persistence table, API endpoint, dashboard page, broker integration, paper-order submission, or live-trading support was introduced.
- The validator still expects future provider adapters to supply canonical columns exactly; vendor-specific normalization remains deferred.

Corrective git summary:

- Current commit hash: `336eda1`.
- `git status --short --untracked-files=all` showed modified Phase 3 scaffold/documentation files and untracked Phase 3 implementation/test/review files, including this review report.
- `git diff --stat` for tracked files showed 5 tracked files changed with 67 insertions and 12 deletions before this untracked report addendum; untracked Phase 3 files are not included in ordinary `git diff --stat`.
- `git diff --check` passed with no output.

## Files created

- `src/spy_market_agent/config/settings.py`: Implementation. Defines the Pydantic Settings class, safe defaults, configuration validation, optional secret-aware future Alpaca fields, `load_settings()`, and the read-only `paper_order_submission_enabled` helper.
- `src/spy_market_agent/market_data/models.py`: Implementation. Defines canonical constants, `MarketDataRequest`, `MarketDataMetadata`, and `MarketDataBatch`.
- `src/spy_market_agent/market_data/providers.py`: Implementation/interface. Defines the provider-independent `MarketDataProvider` protocol only. It has no concrete vendor implementation and no credentials.
- `src/spy_market_agent/market_data/calendar.py`: Implementation. Defines the internal `TradingCalendar` protocol and `XNYSCalendar` adapter over `exchange-calendars`.
- `src/spy_market_agent/market_data/checksum.py`: Implementation. Defines deterministic SHA-256 checksum generation for canonical daily OHLCV data.
- `src/spy_market_agent/validation/market_data_checks.py`: Implementation. Defines `ValidationIssue`, `MarketDataValidationError`, and `validate_daily_spy_data()`.
- `tests/unit/test_settings.py`: Tests. Covers configuration defaults, safety validation, environment overrides, secret redaction, and import side-effect safety.
- `tests/unit/test_calendar.py`: Tests. Covers XNYS session detection, session ranges, missing sessions, session completion, UTC conversion, and naive timestamp rejection.
- `tests/unit/test_market_data_validation.py`: Tests. Covers canonical request rules, validation success, validation failures, metadata generation, checksum behavior, and original DataFrame ownership.
- `tests/integration/test_market_data_provider_flow.py`: Integration test. Uses a deterministic fake provider implementing the provider protocol and validates its returned synthetic data.
- `reviews/PHASE_03_REVIEW.md`: Documentation. Records this review report.

No persistent new directories were required; Phase 3 used the existing Phase 2 package, test, and review directories.

## Files modified

- `pyproject.toml`: Added approved runtime dependencies, kept existing tooling intact, and added narrow MyPy overrides for `pandas` and `exchange_calendars` because complete type information is not available from those third-party packages.
- `.env.example`: Added Phase 3 market configuration placeholders and renamed future Alpaca placeholders to `ALPACA_API_KEY` and `ALPACA_SECRET_KEY`. No real secrets were added.
- `README.md`: Updated project status to Phase 3 and documented implemented configuration, canonical schema, calendar, checksum, and validation capabilities. Also clarified that downloads, models, recommendations, backtests, broker communication, and order submission remain unimplemented.
- `PROJECT_SPEC.md`: Narrow documentation change only: clarified that the data provider may remain undecided through Phase 3.
- `tests/unit/test_scaffold.py`: Added the Phase 2 regression improvement verifying `spy_market_agent.__version__` matches `importlib.metadata.version("spy-market-agent")`.

## Directories created

- No persistent project directories were created during Phase 3.
- Temporary local `.venv/` was created for Python 3.12 verification and removed during cleanup.

## Implementation summary

Settings loading now lives in `src/spy_market_agent/config/settings.py`. The `Settings` class uses Pydantic Settings, so callers can pass explicit constructor values, operating-system environment variables can override defaults, and a local `.env` file is supported when present. The project does not require `.env`, and `load_settings()` is explicit; settings are not loaded during package import.

Configuration validation enforces the Version 1 safety boundary: execution mode must be `paper`, market symbol must be `SPY`, timeframe must be `1Day`, exchange calendar must be `XNYS`, timezone must be `America/New_York`, adjustment policy must be `adjusted`, and initial capital must be positive. `ENABLE_PAPER_EXECUTION` defaults to false and `DRY_RUN` defaults to true. The helper `paper_order_submission_enabled` is true only for paper mode with paper execution explicitly enabled and dry-run explicitly disabled. It is read-only and does not import execution adapters or submit orders.

Secret handling uses Pydantic `SecretStr` for future Alpaca key fields. Those fields are optional, default to `None`, and are excluded from `repr`. Display serialization uses Pydantic JSON mode, which redacts secret values as `**********`.

The market-data layer now has provider-independent data models. `MarketDataRequest` represents a request for adjusted daily SPY data by trading-session dates. `MarketDataMetadata` records provider name, symbol, timeframe, adjustment policy, UTC timestamps, first and last sessions, row count, checksum, schema version, and source description. `MarketDataBatch` bundles a canonical pandas DataFrame with metadata. Batch ownership is documented: the validator returns a copy, but pandas DataFrames are not treated as deeply immutable.

The provider interface is only a `Protocol`. Tests prove a deterministic fake provider can implement it. There is no concrete vendor, no network call, no credential field, and no broker dependency.

The canonical DataFrame schema is exactly `session`, `open`, `high`, `low`, `close`, `volume`. `session` is a trading-session date, not an arbitrary local-midnight timestamp. Full metadata timestamps are timezone-aware UTC. The canonical schema intentionally excludes duplicate raw and adjusted price columns.

The XNYS calendar adapter wraps `exchange-calendars`. It checks valid sessions, returns sessions between two dates, computes missing expected sessions, determines whether a session is complete at an injected timezone-aware `as_of` timestamp, and converts full timestamps to UTC. Core validation does not call `datetime.now()` and does not depend on the machine timezone.

Validation flows through `validate_daily_spy_data()`. It checks object type, columns, empty data, session parsing, duplicate and unordered sessions, XNYS sessions, missing sessions, incomplete or future latest session, OHLC numeric/finite/positive constraints, volume numeric/finite/non-negative/integer-compatible constraints, OHLC relationships, metadata consistency, and timezone-aware inputs. On success it returns a validated copy, metadata, and checksum. On failure it raises `MarketDataValidationError` with structured issue codes and non-secret messages. It never repairs data, forward-fills, backward-fills, interpolates, or fabricates prices or volume.

Checksum generation serializes canonical column order, row order, ISO session dates, stable OHLC strings, and integer volume strings into JSON and hashes that payload with SHA-256. Volatile timestamps, local paths, credentials, and metadata are excluded.

## Architecture decisions

- Decision: Use Pydantic Settings for configuration.
  - Reason: It was explicitly requested and gives typed settings, environment loading, `.env` support, and validation.
  - Alternatives: Manual `os.environ` parsing or dataclasses.
  - Trade-offs: Adds runtime dependencies and Pydantic validation semantics.
  - Approval status: Approved by Phase 3 prompt.

- Decision: Keep `load_settings()` explicit and avoid global settings initialization.
  - Reason: Package import must not load configuration or perform side effects.
  - Alternatives: Module-level singleton settings object.
  - Trade-offs: Callers must explicitly load settings.
  - Approval status: Approved by Phase 3 prompt and `AGENTS.md`.

- Decision: Use `SecretStr` and `repr=False` for future Alpaca credential fields.
  - Reason: Secrets must not appear in repr, display serialization, logs, or validation output.
  - Alternatives: Plain optional strings with manual redaction.
  - Trade-offs: Tests and call sites must handle secret-aware values.
  - Approval status: Approved by Phase 3 prompt.

- Decision: Represent daily sessions as `datetime.date`.
  - Reason: It avoids arbitrary local-midnight timestamps and matches daily exchange sessions.
  - Alternatives: pandas normalized session index.
  - Trade-offs: Date objects are simple and explicit but require conversion when using pandas calendars.
  - Approval status: Newly selected within approved Phase 3 choices.

- Decision: Use `exchange-calendars` only through a small internal adapter.
  - Reason: Calendar behavior is isolated and deterministic tests can inject `as_of`.
  - Alternatives: Hard-code holidays or use pandas business days.
  - Trade-offs: Adds dependency initialization cost and third-party warnings, but avoids maintaining exchange calendars manually.
  - Approval status: Approved by Phase 3 prompt.

- Decision: Use a pandas DataFrame as canonical daily OHLCV storage.
  - Reason: It was explicitly requested and is natural for later feature engineering.
  - Alternatives: Pydantic row models or dataclasses.
  - Trade-offs: DataFrames are mutable and not deeply type-checked, so the validator returns a copy and documents ownership.
  - Approval status: Approved by Phase 3 prompt.

- Decision: Reject invalid data instead of normalizing vendor columns or repairing values.
  - Reason: Vendor normalization belongs in future provider adapters, and financial data must not be silently fabricated or corrected.
  - Alternatives: Rename common vendor columns or fill missing data.
  - Trade-offs: Providers must adapt data into canonical form before validation.
  - Approval status: Approved by Phase 3 prompt and `PROJECT_SPEC.md`.

- Decision: Use deterministic JSON serialization for checksums.
  - Reason: It makes checksum inputs explicit and excludes volatile metadata.
  - Alternatives: CSV bytes or pandas hashing.
  - Trade-offs: Float formatting choices must remain stable and documented.
  - Approval status: Newly selected within approved checksum requirement.

- Decision: Add narrow MyPy overrides for `pandas` and `exchange_calendars`.
  - Reason: The approved dependencies do not provide complete strict type information in this environment, and adding `pandas-stubs` was not approved.
  - Alternatives: Add stubs, use broad ignores, or avoid type checking data modules.
  - Trade-offs: Third-party objects are less strictly typed, but project functions remain annotated and checked.
  - Approval status: Newly introduced narrow tooling decision; no broad type-check disablement.

No departure from `PROJECT_SPEC.md` or `AGENTS.md` was introduced.

## Dependencies

Added runtime dependencies:

- `pydantic>=2.8,<3`: Typed models and validation.
- `pydantic-settings>=2.4,<3`: Environment and `.env` settings loading.
- `pandas>=2.2,<3`: Canonical DataFrame schema and validation operations.
- `exchange-calendars>=4.5,<5`: XNYS session calendar, session ranges, and official session close handling.

Removed dependencies: None.

Existing development dependencies retained:

- `pytest>=8.2,<9`
- `pytest-cov>=5,<8`
- `ruff>=0.8,<1`
- `mypy>=1.10,<2`

Installed versions during verification:

```text
pydantic==2.13.4
pydantic-settings==2.14.2
pandas==2.3.3
exchange-calendars==4.13.2
pytest==8.4.2
pytest-cov==7.1.0
ruff==0.16.0
mypy==1.20.2
```

Relevant transitive dependency considerations:

- `pandas` installs `numpy`, `python-dateutil`, `pytz`, and `tzdata` transitively.
- `exchange-calendars` installs `numpy`, `pyluach`, `toolz`, `tzdata`, and `korean_lunar_calendar` transitively.
- `pydantic-settings` installs `python-dotenv` transitively. No separate dotenv package was added directly.
- No broker SDK, data vendor SDK, ML library, FastAPI, Streamlit, SQLAlchemy, or cloud SDK was added.

No dependency-lock file was introduced.

## Commands executed

Important commands executed:

```bash
sed -n '1,220p' PROJECT_SPEC.md
sed -n '221,440p' PROJECT_SPEC.md
sed -n '441,760p' PROJECT_SPEC.md
sed -n '1,220p' AGENTS.md
sed -n '1,220p' reviews/PHASE_02_REVIEW.md
sed -n '221,440p' reviews/PHASE_02_REVIEW.md
sed -n '441,760p' reviews/PHASE_02_REVIEW.md
git status --short --untracked-files=all
sed -n '1,240p' pyproject.toml
sed -n '1,260p' README.md
sed -n '1,120p' .env.example
sed -n '1,180p' tests/unit/test_scaffold.py
/opt/homebrew/bin/python3.12 --version
/opt/homebrew/bin/python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -c "import exchange_calendars as xc; cal=xc.get_calendar('XNYS'); print(type(cal)); print(hasattr(cal, 'is_session'), hasattr(cal, 'sessions_in_range'), hasattr(cal, 'session_close'), hasattr(cal, 'session_open')); print(cal.sessions_in_range('2024-01-02','2024-01-05')); print(cal.session_close('2024-01-05'))"
.venv/bin/python -c "import exchange_calendars as xc; cal=xc.get_calendar('XNYS'); print(cal.tz); print([name for name in dir(cal) if 'session' in name and ('close' in name or 'open' in name or 'minute' in name)][:80])"
.venv/bin/python -c "import exchange_calendars as xc; import pandas as pd; cal=xc.get_calendar('XNYS'); print(cal.is_session(pd.Timestamp('2024-01-06'))); print(cal.is_session(pd.Timestamp('2024-01-01'))); print(cal.is_session(pd.Timestamp('2024-01-02')))"
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy src tests
.venv/bin/ruff check --fix .
.venv/bin/ruff format .
.venv/bin/pytest tests/unit -q
.venv/bin/pytest tests/integration -q
rg -n "XNYSE|XNYSCalendar" src tests
.venv/bin/python -c "import spy_market_agent; print(spy_market_agent.__version__)"
git diff --check
.venv/bin/python -c "from importlib.metadata import version; names=['pydantic','pydantic-settings','pandas','exchange-calendars','pytest','pytest-cov','ruff','mypy']; print('\n'.join(f'{name}=={version(name)}' for name in names))"
date -u '+%Y-%m-%d %H:%M:%S UTC'
git diff --stat
git rev-parse --short HEAD
rm -rf .venv venv .pytest_cache .ruff_cache .mypy_cache src/spy_market_agent.egg-info src/spy_market_agent/__pycache__ tests/unit/__pycache__ .coverage .coverage.* htmlcov coverage.xml
rm -rf tests/integration/__pycache__ src/spy_market_agent/config/__pycache__ src/spy_market_agent/market_data/__pycache__ src/spy_market_agent/validation/__pycache__
find . -maxdepth 4 -type d -name __pycache__
find . -maxdepth 4 -type d -name '*.egg-info'
find . -maxdepth 2 -type d -name '.venv' -o -name 'venv' -o -name '.pytest_cache' -o -name '.mypy_cache' -o -name '.ruff_cache'
```

No command included credentials, tokens, passwords, or account numbers.

## Verification results

- Full Pytest suite:
  - Command: `.venv/bin/pytest`
  - Result: Passed.
  - Tests: 89 passed, 0 failed, 0 skipped.
  - Warnings: 3 warnings from `exchange-calendars` / pandas about NumPy generic timedelta deprecation.
  - Coverage: 89% total.
  - Corrective actions: Removed avoidable pandas FutureWarnings from tests by explicitly choosing compatible dtypes.

- Unit tests:
  - Command: `.venv/bin/pytest tests/unit -q`
  - Result: Passed.
  - Tests: all unit tests passed; the quiet output displayed pass dots and coverage with no failures.
  - Warnings: 3 third-party `exchange-calendars` / pandas deprecation warnings.

- Integration tests:
  - Command: `.venv/bin/pytest tests/integration -q`
  - Result: Passed.
  - Tests: 1 integration test passed.
  - Warnings: 3 third-party `exchange-calendars` / pandas deprecation warnings.

- Ruff linting:
  - Command: `.venv/bin/ruff check .`
  - Initial result: Failed on import sorting, Python 3.12 `UTC` modernization, line length, and one simplification suggestion.
  - Corrective action: Ran `.venv/bin/ruff check --fix .` and manually fixed remaining line length/simplification issues.
  - Final result: Passed with `All checks passed!`.

- Ruff formatting:
  - Command: `.venv/bin/ruff format --check .`
  - Initial result: Failed; 4 files needed formatting.
  - Corrective action: Ran `.venv/bin/ruff format .`.
  - Final result: Passed with `31 files already formatted`.

- MyPy:
  - Command: `.venv/bin/mypy src tests`
  - Initial result: Failed on strict type issues in pandas date conversion and test constructor arguments.
  - Corrective action: Added narrow casts/annotations and used type-compatible test inputs.
  - Final result: Passed with `Success: no issues found in 27 source files`.

- Import smoke test:
  - Command: `.venv/bin/python -c "import spy_market_agent; print(spy_market_agent.__version__)"`
  - Result: Passed.
  - Output: `0.1.0`.

- `git diff --check`:
  - Result: Passed.
  - Output: no whitespace errors.

## Tests created or modified

- `tests/unit/test_settings.py`:
  - Verifies settings defaults, paper-only execution mode, SPY-only configuration, daily timeframe, XNYS calendar, New York timezone, adjusted-only policy, positive capital, paper permission helper, optional missing credentials, secret redaction, environment overrides, unknown OS env tolerance, no file creation, and no package import side effects.
  - Protects against live-mode configuration, non-SPY expansion, accidental paper-order enablement, credential exposure, and import-time side effects.
  - Type: Unit and safety tests.

- `tests/unit/test_calendar.py`:
  - Verifies valid XNYS sessions, weekend and holiday exclusion, session ranges, missing exchange sessions, injected session completion checks, UTC conversion, and naive timestamp rejection.
  - Protects against calendar leakage, local-timezone dependence, treating weekends/holidays as missing observations, and incomplete-session acceptance.
  - Type: Unit tests.

- `tests/unit/test_market_data_validation.py`:
  - Verifies valid canonical datasets, no mutation of caller DataFrames, missing/extra/bad columns, duplicate sessions, unordered sessions, weekend and holiday rows, missing valid sessions, incomplete/future sessions, naive timestamps, missing/non-numeric/infinite/non-positive OHLC, invalid OHLC relationships, negative/non-numeric/infinite/fractional volume, metadata consistency, checksum determinism, and checksum sensitivity to OHLCV/session changes.
  - Protects against data-quality failures, silent repair, raw/adjusted mixing, invalid sessions, incomplete data, and lineage inconsistency.
  - Type: Unit, regression, and safety tests.

- `tests/integration/test_market_data_provider_flow.py`:
  - Verifies a deterministic fake provider can implement `MarketDataProvider` and return a validated batch without network access.
  - Protects the provider boundary and ensures tests do not require real vendor data.
  - Type: Integration test.

- `tests/unit/test_scaffold.py`:
  - Added package-version consistency test comparing `spy_market_agent.__version__` with installed package metadata.
  - Protects against independent version drift.
  - Type: Unit regression test.

No tests were removed or weakened.

## Manual checks required

Before approving Phase 3, manually inspect:

1. `src/spy_market_agent/config/settings.py` for safety defaults and secret handling.
2. `src/spy_market_agent/market_data/models.py` for canonical schema and metadata fields.
3. `src/spy_market_agent/validation/market_data_checks.py` for fail-closed validation behavior.
4. `.env.example` to confirm it contains placeholders only.
5. `README.md` to confirm the current status and not-implemented scope are clear.
6. `pyproject.toml` to confirm dependency constraints are acceptable.

Optional manual commands:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
pytest
ruff check .
ruff format --check .
mypy src tests
```

## Known limitations

- No external market-data provider is selected or implemented.
- No real SPY data is downloaded or included.
- Test datasets are synthetic and intentionally small.
- No feature engineering, label generation, model training, strategy logic, backtesting, risk calculation, persistence table, API endpoint, dashboard page, broker integration, or order execution exists.
- Checksum formatting uses stable strings for numeric values; future phases should preserve this format or version the checksum if serialization changes.
- Validation currently expects providers to supply canonical columns exactly; vendor normalization is deferred to future provider adapters.
- Third-party `exchange-calendars` emits deprecation warnings related to pandas / NumPy timedelta internals. They are visible and not suppressed globally.
- No dependency lock file exists.

## Problems encountered

- Problem: Initial editable install failed.
  - Root cause: Sandbox DNS/network restrictions prevented PyPI resolution.
  - Resolution: Reran the same install command with approved network access.
  - Future risk: Fresh environments still require PyPI access unless a later lock/offline strategy is added.

- Problem: `exchange-calendars` API details needed confirmation.
  - Root cause: The adapter depends on exact method names for session ranges and closes.
  - Resolution: Inspected the installed `XNYS` calendar object locally and used `is_session`, `sessions_in_range`, and `session_close`.
  - Future risk: A major `exchange-calendars` upgrade could change APIs; dependency is constrained to `<5`.

- Problem: Ruff initially failed.
  - Root cause: Import sorting, line length, Python 3.12 `UTC` modernization, and one simplification issue.
  - Resolution: Applied Ruff fixes and manual line/simplification edits.
  - Future risk: Low; tooling catches this.

- Problem: MyPy initially failed.
  - Root cause: Strict typing around pandas conversions and Pydantic runtime coercion in tests.
  - Resolution: Added casts/annotations and type-compatible test inputs.
  - Future risk: Pandas-heavy code will need careful annotations.

- Problem: Test code initially produced pandas FutureWarnings.
  - Root cause: Tests assigned incompatible values into typed pandas columns.
  - Resolution: Tests now explicitly cast columns before assigning invalid synthetic values.
  - Future risk: Low; remaining warnings are third-party deprecations.

## Security and safety review

- No credentials were committed.
- No credentials were logged.
- Secret values are redacted by `SecretStr`, excluded from settings `repr`, and redacted in display serialization.
- No broker SDK was added.
- No market-data network access was added.
- No paper-order submission was added.
- No live trading was added.
- No short selling was added.
- No leverage was added.
- No risk bypass was added.
- No feature or label leakage logic was introduced.
- No data was silently fabricated, forward-filled, backward-filled, interpolated, or repaired.
- `.env.example` contains placeholders only.
- `Settings` does not load globally at package import.
- The provider protocol contains no credentials and no concrete network provider.
- The validation layer fails closed on missing, invalid, incomplete, duplicate, unordered, or uncertain data.

## Scope exclusions

Intentionally not implemented in Phase 3:

- Real market-data downloads.
- Alpaca market-data clients.
- Alpaca trading clients.
- Broker communication.
- Live trading.
- Paper-order submission.
- Machine-learning models.
- Feature engineering.
- Label generation.
- Trading signals.
- Strategies.
- Backtesting.
- Risk calculations.
- Database persistence.
- FastAPI endpoints.
- Streamlit pages.
- Scheduled jobs.
- Cloud deployment.
- Docker.
- Hard-coded secrets.
- Real SPY data copied from any external source.

## Git summary

Current commit hash:

```text
336eda1
```

`git status --short`:

```text
 M .env.example
 M PROJECT_SPEC.md
 M README.md
 M pyproject.toml
 M tests/unit/test_scaffold.py
?? reviews/PHASE_03_REVIEW.md
?? src/spy_market_agent/config/settings.py
?? src/spy_market_agent/market_data/calendar.py
?? src/spy_market_agent/market_data/checksum.py
?? src/spy_market_agent/market_data/models.py
?? src/spy_market_agent/market_data/providers.py
?? src/spy_market_agent/validation/market_data_checks.py
?? tests/integration/test_market_data_provider_flow.py
?? tests/unit/test_calendar.py
?? tests/unit/test_market_data_validation.py
?? tests/unit/test_settings.py
```

`git diff --stat`:

```text
 .env.example                | 12 ++++++++----
 PROJECT_SPEC.md             |  2 +-
 README.md                   | 44 ++++++++++++++++++++++++++++++++++++++------
 pyproject.toml              | 16 +++++++++++++++-
 tests/unit/test_scaffold.py |  5 +++++
 5 files changed, 67 insertions(+), 12 deletions(-)
```

`git diff --check`:

```text
```

Ordinary `git diff --stat` does not include untracked files; the status output above is the complete untracked Phase 3 file list.

No commit or push was performed.

## Recommended Phase 4

Phase 4 should implement leakage-safe feature engineering and chronological splits.

Phase 4 should depend on:

- `Settings` for approved symbol, timeframe, calendar, timezone, and adjustment policy.
- `MarketDataBatch` and canonical DataFrame schema.
- `XNYSCalendar` for session-aware assumptions.
- `validate_daily_spy_data()` for trusted canonical data inputs.
- Deterministic tests and synthetic fixtures from Phase 3.

Decisions needed before Phase 4:

- Exact feature-schema versioning approach.
- Initial trailing technical features.
- How to express label generation while keeping future returns out of model features.
- Chronological train/validation/test split boundaries and gap handling.

Risks Phase 4 must address:

- Lookahead leakage.
- Centered windows.
- Backward filling.
- Feature/label misalignment.
- Accidentally using final test data during feature, model, calibration, or threshold selection.

Do not begin Phase 4 until Phase 3 is reviewed and approved.

## Final checklist

- [x] Phase completed.
- [x] Approved scope only.
- [x] Dependencies reviewed.
- [x] Tests added.
- [x] Pytest run.
- [x] Unit tests run.
- [x] Integration tests run.
- [x] Ruff lint run.
- [x] Ruff format check run.
- [x] MyPy run.
- [x] Import smoke test run.
- [x] No secrets exposed.
- [x] No network access introduced.
- [x] No broker access introduced.
- [x] No live trading introduced.
- [x] Documentation updated.
- [x] Phase 4 not started.

## Second corrective review addendum

Corrective completion timestamp: 2026-07-24 19:49:28 UTC.

Correction status: Completed. This addendum records the final Phase 3 data-integrity corrective pass only. Phase 4 was not started.

Issues corrected:

- Checksum collision correction: checksum serialization now uses `float(value).hex()` for OHLC prices so distinct canonical float64 values are represented losslessly. The checksum serialization version was updated to `canonical-daily-ohlcv-v2-sha256`.
- Checksum session strictness: direct checksum generation now rejects full `datetime` session values and string session values. Checksum sessions must be plain `datetime.date` values.
- Exact volume validation correction: volume validation now parses values with `Decimal` before converting to signed int64. It rejects boolean, missing, non-numeric, infinite, negative, fractional, and out-of-range values without float rounding, truncation, or silent repair.
- Model immutability correction: `MarketDataRequest`, `MarketDataMetadata`, and `MarketDataBatch` are now frozen Pydantic models after construction. The DataFrame inside `MarketDataBatch` remains not deeply immutable by design and is documented/tested as such.
- Metadata invariant correction: `MarketDataMetadata` now directly rejects blank provider names, trims valid provider names, and rejects `downloaded_at > created_at`.
- Batch-integrity correction: `MarketDataBatch` now directly verifies canonical column order, non-empty data, metadata row count, first session, last session, and recomputed checksum.
- URL-redaction correction: `display_safe_dict()` now redacts ordinary credential-bearing database URLs, preserves IPv6 host brackets, handles malformed ports without failing, returns a safe placeholder for malformed credential-bearing URLs, and leaves SQLite URLs unchanged.
- Structured validation error correction: expected Pydantic construction failures from `MarketDataMetadata` or `MarketDataBatch` inside `validate_daily_spy_data()` are converted into `MarketDataValidationError` with non-secret structured issue codes.

Files changed during this corrective pass:

- `src/spy_market_agent/config/settings.py`: Hardened database URL redaction for IPv6, malformed ports, malformed credential-bearing URLs, and SQLite passthrough.
- `src/spy_market_agent/market_data/checksum.py`: Updated checksum serialization version, changed price serialization to `float.hex()`, and rejected non-plain-date session inputs.
- `src/spy_market_agent/market_data/models.py`: Froze market-data Pydantic models and added direct metadata/batch invariant validation.
- `src/spy_market_agent/validation/market_data_checks.py`: Replaced volume float-based integer checks with exact `Decimal` parsing and wrapped expected metadata/batch validation failures.
- `tests/unit/test_market_data_validation.py`: Added checksum, exact-volume, model immutability, metadata invariant, batch invariant, and structured-error regression tests.
- `tests/unit/test_settings.py`: Added deterministic database URL redaction tests.
- `reviews/PHASE_03_REVIEW.md`: Added this second corrective addendum.

Tests added or updated:

- Checksum tests now prove equivalent datasets still match, distinct prices `100.123456789012` and `100.123456789013` differ, changed sessions differ, changed volume differs, changed OHLC differs, and malformed session types fail.
- Volume tests now prove `"9007199254740992.1"` and `"1000.1"` are rejected, `"1000.0"` is accepted as `1000`, `0` is accepted, signed int64 maximum is accepted, and signed int64 maximum plus one is rejected.
- Model tests now prove frozen behavior for request symbol/timeframe, metadata row count/first session, and batch metadata replacement.
- Batch tests now prove direct construction rejects incorrect checksum, first session, last session, row count, and empty data.
- Metadata tests now prove blank provider names fail, valid provider names are trimmed, and `downloaded_at > created_at` fails.
- URL tests now prove credential redaction for ordinary URLs, IPv6 URLs, malformed ports, malformed credential-bearing URLs, and SQLite passthrough.
- Validator tests now prove expected internal metadata/batch construction failures are returned as `MarketDataValidationError` codes.

Final verification results:

- `pytest`: Passed. Result: 136 passed, 3 third-party warnings, 85% total coverage.
- `pytest tests/unit -q`: Passed. Result: all unit tests passed under quiet output, 3 third-party warnings, 85% total coverage shown by coverage reporting.
- `pytest tests/integration -q`: Passed. Result: 1 integration test passed, 3 third-party warnings.
- `ruff check .`: Passed with `All checks passed!`.
- `ruff format --check .`: Passed with `32 files already formatted`.
- `mypy src tests`: Passed with `Success: no issues found in 27 source files`.
- `python -c "import spy_market_agent; print(spy_market_agent.__version__)"`: Passed and printed `0.1.0`.
- `git diff --check`: Passed with no whitespace errors.

Remaining warnings and limitations:

- Three visible third-party warnings remain from `exchange-calendars` / pandas / NumPy timedelta internals. They were not globally suppressed.
- `MarketDataBatch` is a frozen Pydantic object, but pandas DataFrames are mutable internally; callers must still treat batch data as owned and avoid mutation after construction.
- No market-data downloads, provider adapters, feature engineering, labels, models, strategies, backtesting, persistence, API routes, dashboard code, broker access, paper-order submission, or execution functionality was added.
- The existing project `./.venv` directory was not deleted, recreated, replaced, or cleaned during this corrective pass.

Corrective git summary:

- Current commit hash: `a96bea5`.
- `git status --short --untracked-files=all` showed:

```text
 M src/spy_market_agent/config/settings.py
 M src/spy_market_agent/market_data/checksum.py
 M src/spy_market_agent/market_data/models.py
 M src/spy_market_agent/validation/market_data_checks.py
 M tests/unit/test_market_data_validation.py
 M tests/unit/test_settings.py
```

- `git diff --stat` before this report addendum showed:

```text
 src/spy_market_agent/config/settings.py            |  30 +--
 src/spy_market_agent/market_data/checksum.py       |  71 +++++--
 src/spy_market_agent/market_data/models.py         |  54 ++++-
 .../validation/market_data_checks.py               | 160 ++++++++++++--
 tests/unit/test_market_data_validation.py          | 235 ++++++++++++++++++++-
 tests/unit/test_settings.py                        |  36 ++++
 6 files changed, 522 insertions(+), 64 deletions(-)
```

No commit or push was performed.

Second corrective checklist:

- [x] Checksum collision correction completed.
- [x] Exact volume-validation correction completed.
- [x] Market-data model immutability correction completed.
- [x] Batch-integrity correction completed.
- [x] URL-redaction correction completed.
- [x] Tests added or updated.
- [x] Pytest run.
- [x] Unit tests run.
- [x] Integration tests run.
- [x] Ruff lint run.
- [x] Ruff format check run.
- [x] MyPy run.
- [x] Import smoke test run.
- [x] `git diff --check` run.
- [x] No secrets exposed.
- [x] No market-data network access introduced.
- [x] No broker access introduced.
- [x] No live trading introduced.
- [x] Phase 4 not started.

## Final Phase 3 security note

Security correction timestamp: 2026-07-24 20:19:56 UTC.

Correction status: Completed. This note records only the final Phase 3 settings security correction. Phase 4 was not started.

Issues corrected:

- `Settings.database_url` is now excluded from the Pydantic model representation with `repr=False`, preventing database URLs from appearing in `repr(settings)`.
- `display_safe_dict()` now uses a fail-closed Version 1 database URL display policy. The normal local SQLite URL `sqlite:///./spy_market_agent.db` remains visible, while every non-SQLite database URL is replaced with `<redacted-database-url>`.
- Suspicious SQLite URLs with user-information markers, query strings, fragments, or credential-like terms are also replaced with `<redacted-database-url>`.
- The display-safe policy no longer attempts to partially preserve usernames, passwords, hosts, ports, query strings, fragments, or tokens from non-SQLite database URLs.

Files changed during this security correction:

- `src/spy_market_agent/config/settings.py`: Hid `database_url` from model repr and replaced partial URL redaction with fail-closed display-safe database URL handling.
- `tests/unit/test_settings.py`: Added/updated tests proving database URL credentials are absent from `repr(settings)` and display output for credential-bearing or non-SQLite URLs.
- `reviews/PHASE_03_REVIEW.md`: Added this final security note.

Tests added or updated:

- Added a repr test proving `db_user` and `db_password` from `postgresql://db_user:db_password@localhost/research` do not appear in `repr(settings)`.
- Added display-safe tests proving full redaction for:
  - `postgresql://db_user:db_password@localhost/research`
  - `postgresql:db_user:db_password@localhost/research`
  - `postgresql:///db_user:db_password@localhost/research`
  - `postgresql://localhost/research?password=db_password`
  - a URL containing `access_token` in its query
  - additional non-SQLite and suspicious SQLite credential-bearing cases
- Confirmed the normal local SQLite URL remains visible.

Final verification results:

- `pytest`: Passed. Result: 152 passed, 4 third-party warnings, 84% total coverage.
- `ruff check .`: Passed with `All checks passed!`.
- `ruff format --check .`: Passed with `32 files already formatted`.
- `mypy src tests`: Passed with `Success: no issues found in 27 source files`.
- `python -c "import spy_market_agent; print(spy_market_agent.__version__)"`: Passed and printed `0.1.0`.
- `git diff --check`: Passed with no whitespace errors.

Remaining warnings and limitations:

- Four visible third-party warnings remain from `exchange-calendars` / pandas / NumPy timedelta internals. They were not globally suppressed.
- No market-data, calendar, checksum, model, validation, provider, or execution behavior was changed during this security correction.
- No market-data providers, downloads, feature engineering, labels, models, strategies, backtesting, persistence, API routes, dashboard code, broker access, paper-order submission, or execution functionality was added.
- The existing project `./.venv` directory was not deleted, recreated, replaced, or cleaned.

Security correction git summary:

- Current commit hash: `a96bea5`.
- `git status --short --untracked-files=all` before this note showed prior Phase 3 modified files plus unstaged security changes in `src/spy_market_agent/config/settings.py` and `tests/unit/test_settings.py`.
- `git diff --stat` for the unstaged security correction before this note showed:

```text
 src/spy_market_agent/config/settings.py | 48 +++++++++++-------
 tests/unit/test_settings.py             | 87 +++++++++++++++++++++------------
 2 files changed, 86 insertions(+), 49 deletions(-)
```

No commit or push was performed.

Final security checklist:

- [x] `database_url` hidden from `repr(settings)`.
- [x] Display-safe database URL handling fails closed.
- [x] Normal local SQLite URL remains visible.
- [x] Credential-bearing database URL tests added.
- [x] Pytest run.
- [x] Ruff lint run.
- [x] Ruff format check run.
- [x] MyPy run.
- [x] Import smoke test run.
- [x] `git diff --check` run.
- [x] No secrets exposed.
- [x] No market-data behavior changed.
- [x] No broker access introduced.
- [x] No live trading introduced.
- [x] Phase 4 not started.

## Final calendar-and-scalar corrective addendum

Corrective completion timestamp: 2026-07-24 20:06:44 UTC.

Correction status: Completed. This addendum records only the final Phase 3 calendar-range and scalar-validation corrective pass. Phase 4 was not started.

Issues corrected:

- Explicit calendar range: `XNYSCalendar` now constructs the exchange calendar with explicit `CALENDAR_START = 1993-01-22` and `CALENDAR_END = 2050-12-30`. This covers SPY's inception session and a substantial future research horizon without relying on the library's default rolling range.
- Calendar exception handling: the calendar adapter now raises project-owned `CalendarDataError` for expected out-of-range or unrepresentable calendar-query failures. The validator converts those failures into `MarketDataValidationError` with structured non-secret issue code `calendar_session_out_of_range`.
- Non-scalar volume handling: `_is_missing_scalar()` now treats array-like/list-like missing checks as non-scalar and returns `False`, allowing exact numeric parsing to reject the value as `non_numeric_volume` instead of raising raw pandas or Python errors.

Files changed during this corrective pass:

- `src/spy_market_agent/market_data/calendar.py`: Added `CALENDAR_START`, `CALENDAR_END`, `CalendarDataError`, explicit `get_calendar(..., start=..., end=...)`, supported-range documentation, and narrow wrapping of expected calendar exceptions.
- `src/spy_market_agent/validation/market_data_checks.py`: Converted expected `CalendarDataError` failures into structured validation issues and hardened `_is_missing_scalar()`.
- `tests/unit/test_calendar.py`: Added explicit range, historical start, future horizon, out-of-range, extremely old date, weekend, and holiday coverage.
- `tests/unit/test_market_data_validation.py`: Added validation coverage for a pre-2006 supported SPY session, safe out-of-range calendar failures, safe extremely old date failures, and non-scalar volume rejection.
- `reviews/PHASE_03_REVIEW.md`: Added this final corrective addendum.

Tests added or updated:

- Calendar adapter tests now prove the documented range starts at `1993-01-22`, extends through `2050-12-30`, supports a future session beyond 2027, wraps dates outside the range in `CalendarDataError`, and still handles weekends and NYSE holidays correctly.
- Validation tests now prove a supported historical XNYS session before 2006 validates successfully, out-of-range dates fail as `MarketDataValidationError`, extremely old dates do not expose raw third-party exceptions, and volume values `[1, 2]`, `{"unexpected": "object"}`, and an arbitrary object instance are rejected as validation errors.

Final verification results:

- `pytest`: Passed. Result: 146 passed, 4 third-party warnings, 84% total coverage.
- `pytest tests/unit -q`: Passed. Result: quiet output completed successfully with all unit tests passing, 4 third-party warnings, 83% total coverage.
- `pytest tests/integration -q`: Passed. Result: 1 integration test passed, 4 third-party warnings.
- `ruff check .`: Passed with `All checks passed!`.
- `ruff format --check .`: Passed with `32 files already formatted`.
- `mypy src tests`: Passed with `Success: no issues found in 27 source files`.
- `python -c "import spy_market_agent; print(spy_market_agent.__version__)"`: Passed and printed `0.1.0`.
- `git diff --check`: Passed with no whitespace errors.

Verification note:

- An earlier attempt ran multiple pytest commands concurrently. Because pytest-cov writes to a shared coverage SQLite file by default, the full run hit a coverage combine internal error after the tests themselves reached `146 passed`. That concurrent run is not counted as the final verification result. Generated coverage data was cleared with `coverage erase`, and the required pytest commands were rerun sequentially in their normal command form and passed.

Remaining warnings and limitations:

- Four visible third-party warnings remain from `exchange-calendars` / pandas / NumPy timedelta internals. They were not globally suppressed.
- The explicit calendar support range is intentionally finite: `1993-01-22` through `2050-12-30`. Data outside that range fails safely and must be handled by an approved future change if Version 1 research needs a wider horizon.
- No market-data providers, downloads, feature engineering, labels, models, strategies, backtesting, persistence, API routes, dashboard code, broker access, paper-order submission, or execution functionality was added.
- The existing project `./.venv` directory was not deleted, recreated, replaced, or cleaned during this corrective pass.

Corrective git summary:

- Current commit hash: `a96bea5`.
- `git status --short --untracked-files=all` before this addendum showed:

```text
 M reviews/PHASE_03_REVIEW.md
 M src/spy_market_agent/config/settings.py
 M src/spy_market_agent/market_data/calendar.py
 M src/spy_market_agent/market_data/checksum.py
 M src/spy_market_agent/market_data/models.py
 M src/spy_market_agent/validation/market_data_checks.py
 M tests/unit/test_calendar.py
 M tests/unit/test_market_data_validation.py
 M tests/unit/test_settings.py
```

- `git diff --stat` before this addendum showed:

```text
 reviews/PHASE_03_REVIEW.md                         | 105 ++++++++
 src/spy_market_agent/config/settings.py            |  30 +--
 src/spy_market_agent/market_data/calendar.py       |  81 +++++-
 src/spy_market_agent/market_data/checksum.py       |  71 +++--
 src/spy_market_agent/market_data/models.py         |  54 +++-
 .../validation/market_data_checks.py               | 201 +++++++++++---
 tests/unit/test_calendar.py                        |  42 ++-
 tests/unit/test_market_data_validation.py          | 292 ++++++++++++++++++++-
 tests/unit/test_settings.py                        |  36 +++
 9 files changed, 830 insertions(+), 82 deletions(-)
```

No commit or push was performed.

Final calendar-and-scalar checklist:

- [x] Explicit XNYS calendar support range added.
- [x] Calendar range failures handled safely.
- [x] Non-scalar volume handling hardened.
- [x] Tests added or updated.
- [x] Pytest run.
- [x] Unit tests run.
- [x] Integration tests run.
- [x] Ruff lint run.
- [x] Ruff format check run.
- [x] MyPy run.
- [x] Import smoke test run.
- [x] `git diff --check` run.
- [x] No secrets exposed.
- [x] No market-data network access introduced.
- [x] No broker access introduced.
- [x] No live trading introduced.
- [x] Phase 4 not started.
