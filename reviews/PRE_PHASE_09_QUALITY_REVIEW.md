# Pre-Phase 9 Quality Review

## Scope

Perform a warning audit and coverage cleanup after Phase 8 was merged and before any Phase 9 work begins.

This pass did not add Phase 9 functionality and did not change Phase 8 execution behavior.

## Branch

- Starting main SHA: `846db31158723d43a2aa66004e2d31e0beb20378`
- Original cleanup branch: `review/pre-phase-09-quality-cleanup`
- Correction branch: `review/pre-phase-09-quality-corrections`
- Confirmed merged Phase 8 concurrency commit: `ae3d390` is an ancestor of `origin/main`.
- Cherry-picked cleanup SHA: `9a9ae2d3d2c81ca0235a40235d67a4fcfc3d34cd`
- Cherry-picked cleanup commit on correction branch: `fa8650c`

## Baseline Verification

- `python -m pip install -e ".[dev]"`: passed; editable `spy-market-agent 0.1.0` installed.
- `pytest`: `758 passed, 6 warnings`; full-suite coverage displayed as `82%`.
- `ruff check .`: `All checks passed!`
- `ruff format --check .`: `111 files already formatted`
- `mypy src tests`: `Success: no issues found in 101 source files`

## Warning Audit

Diagnostic commands:

- `pytest -W always -ra`: `758 passed, 7 warnings`.
- `pytest -W error::DeprecationWarning --maxfail=1`: failed as expected while locating an upstream `exchange_calendars` deprecation.
- `pytest -W error::PendingDeprecationWarning --maxfail=1`: `758 passed, 6 warnings`.
- `pytest -W error::FutureWarning --maxfail=1`: initially failed on project use of deprecated scikit-learn `LogisticRegression(penalty="l2")`; passed after the project code change.
- `pytest -W error::ResourceWarning --maxfail=1`: `758 passed, 6 warnings`.

Installed dependency versions recorded during audit:

- `exchange-calendars`: `4.13.2`
- `fastapi`: `0.141.1`
- `starlette`: `1.3.1`
- `httpx`: `0.28.1`
- `websockets`: `16.1.1`
- `scikit-learn`: `1.9.0`
- `pandas`: `2.3.3`
- `numpy`: `2.5.1`

Warnings found:

- Category: `DeprecationWarning`
  - Message: `The 'generic' unit for NumPy timedelta is deprecated, and will raise an error in the future. This includes implicit conversion of bare integers (e.g. `+ 1`).Please use a specific unit instead.`
  - Originating module: `exchange_calendars.exchange_calendar`
  - Triggering test path observed: import during market-calendar-backed tests, including `tests/integration/test_market_data_provider_flow.py`
  - Dependency: `exchange-calendars 4.13.2` with `numpy 2.5.1`
  - Resolution: exact category/message/module pytest filter because the warning is emitted by upstream import code before project code can avoid it.

- Category: `DeprecationWarning`
  - Message: `The 'generic' unit for NumPy timedelta is deprecated, and will raise an error in the future. This includes implicit conversion of bare integers (e.g. `+ 1`).Please use a specific unit instead.`
  - Originating module: `exchange_calendars.utils.pandas_utils`
  - Triggering test path observed: `tests/integration/test_market_data_provider_flow.py::test_fake_provider_can_implement_protocol_without_network_access`
  - Dependency: `exchange-calendars 4.13.2` with `numpy 2.5.1`
  - Resolution: exact category/message/module pytest filter because the warning is emitted by upstream calendar utility code.

- Category: `starlette.exceptions.StarletteDeprecationWarning`
  - Message: `Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.`
  - Originating module: `fastapi.testclient`
  - Triggering tests: FastAPI `TestClient` imports and Phase 7/8 API tests.
  - Dependencies: `fastapi 0.141.1`, `starlette 1.3.1`, `httpx 0.28.1`
  - Resolution: exact category/message/module pytest filter. Project tests use FastAPI's documented `TestClient` entry point, and the compatible migration path is an upstream FastAPI/Starlette/httpx2 transition.

- Category: `DeprecationWarning`
  - Message: `websockets.legacy is deprecated; see https://websockets.readthedocs.io/en/stable/howto/upgrade.html for upgrade instructions`
  - Originating module: `websockets.legacy`
  - Dependency: `websockets 16.1.1`
  - Resolution: exact category/module filter with message anchored to `websockets.legacy`; emitted by upstream import path, not project code.

- Category: `FutureWarning`
  - Message: scikit-learn deprecation for explicit `LogisticRegression(penalty='l2')`
  - Originating module: project modeling code constructing canonical logistic-regression estimators.
  - Triggering test: `tests/integration/test_phase5_modeling_flow.py`
  - Dependency: `scikit-learn 1.9.0`
  - Resolution: removed explicit `penalty="l2"` from estimator construction without changing the Version 1 semantic model-parameter snapshot.

Pytest warning policy after cleanup:

- All unexpected warnings now fail by default with `filterwarnings = ["error", ...]`.
- The only allowed warning filters are exact documented upstream exceptions by category, message, and module.
- No broad global suppression was added.

## Coverage

Coverage before cleanup:

- Baseline full-suite coverage displayed by `pytest`: `82%`.
- Baseline tests: `758 passed`.

Coverage diagnostics:

- `pytest --cov=src/spy_market_agent --cov-branch --cov-report=term-missing --cov-report=html --cov-report=json`: generated branch coverage reports.
- Intermediate targeted test additions raised full-suite coverage to `83.40261282660333%`.

Coverage after cleanup:

- `pytest --cov-report=json --cov-report=term-missing`: `878 passed`; exact combined coverage `85.3424386381631%`.
- Statement coverage: `88.81596219480178%`.
- Branch coverage: `74.69831053901851%`.
- Covered branches: `1857 / 2486`.
- Full-suite coverage gate target: `pytest --cov-fail-under=85`.

## Tests Added

Added focused regression and coverage tests for:

- Dashboard API client base URL validation, read-only path construction, query parameters, invalid run IDs, HTTP errors, and non-object JSON responses.
- Feature engineering scalar validators, metadata validation, frame-schema validation, dtype validation, non-finite feature rejection, session metadata mismatches, duplicate sessions, and unordered sessions.
- Market-data checksum rejection of noncanonical column order, boolean OHLC values, malformed OHLC values, non-finite OHLC values, boolean volumes, malformed volumes, and non-integral volumes.
- Modeling parameter-reading guards, fitted-state guards, learned array shape checks, finite numeric learned-state checks, estimator object-array checks, probability result conversion, probability row validation, and probability smoke-check failures.
- Persistence serialization readers and writers for required text, optional text, run IDs, dates, datetimes, decimals, booleans, finite floats, integer storage contracts, JSON string tuples, and checksum validation.
- Backtest cost and accounting guardrails for configuration values, scalar validators, execution-price checksum inputs, fill accounting replay, and metrics identity replay.
- Backtest metrics audit validation for invalid frames, malformed portfolio/fill/proposed-order/risk-decision rows, duplicate sequences, order-decision mismatches, and initial-cash mismatch.
- Correction tests proving logistic regression construction omits the deprecated `penalty` argument, training emits no `FutureWarning`, actual fitted public parameters preserve L2 behavior, `fixed_model_parameters()` keeps `classifier.penalty="l2"` and omits `classifier.l1_ratio`, old locked-selection snapshots reconstruct, old Phase 8-style persisted final evaluations save/load/validate, actual parameter tampering is still rejected, and `MODEL_SCHEMA_VERSION` remains `spy-binary-models-v1`.

## Dependency Changes

- No dependency was added, removed, or upgraded.
- Project-owned warning fix avoided a dependency change by removing deprecated scikit-learn parameter usage.
- The correction added no dependency and no persistence migration.

## Files Modified

- `README.md`
- `pyproject.toml`
- `reviews/PHASE_08_REVIEW.md`
- `reviews/PRE_PHASE_09_QUALITY_REVIEW.md`
- `src/spy_market_agent/execution/repository.py`
- `src/spy_market_agent/modeling/__init__.py`
- `src/spy_market_agent/modeling/models.py`
- `src/spy_market_agent/modeling/training.py`
- `tests/unit/modeling_helpers.py`
- `tests/unit/test_backtest_costs.py`
- `tests/unit/test_backtest_metrics.py`
- `tests/unit/test_dashboard_phase7.py`
- `tests/unit/test_feature_engineering.py`
- `tests/unit/test_market_data_validation.py`
- `tests/unit/test_model_training.py`
- `tests/unit/test_persistence_repositories.py`
- `tests/unit/test_persistence_serialization.py`
- `tests/unit/test_public_phase5_api.py`

## Correction Baseline

Commands run on `review/pre-phase-09-quality-corrections` after cherry-picking cleanup commit `9a9ae2d3d2c81ca0235a40235d67a4fcfc3d34cd` and before correction edits:

- `python -m pip install -e ".[dev]"`: passed; editable `spy-market-agent 0.1.0` installed and all dependencies were already present.
- `pytest --cov-fail-under=85`: `878 passed in 82.12s`; required coverage reached with exact combined coverage `85.34%`.
- `pytest tests/unit -q`: passed; combined coverage display remained `85%`; no warning summary was emitted.
- `pytest tests/integration -q`: `23 passed`; integration-only coverage display was `71%`; no warning summary was emitted.
- `ruff check .`: `All checks passed!`
- `ruff format --check .`: `112 files already formatted`
- `mypy src tests`: `Success: no issues found in 101 source files`
- `git diff --check`: passed.

## Correction Note

`LogisticRegression(penalty="l2")` is no longer used because scikit-learn 1.9 deprecates the explicit `penalty` constructor argument and emits a `FutureWarning` when that explicit value is fit. The Version 1 persisted model lineage remains `("classifier.penalty", "l2")` because it is the stable Phase 5/7/8 semantic model specification recorded in model metadata, database rows, and review artifacts. The in-memory estimator is validated separately through its actual public scikit-learn parameters: omitted `penalty` remains the constructor default, `l1_ratio=0.0` maps to L2 behavior during fit, and tampering with actual public estimator parameters is still rejected.

The final `FutureWarning` verification also exposed a pre-existing SQLite deferred write-upgrade race in `record_receipt()` under the Phase 8 concurrent submission test. The correction now acquires the receipt transaction with `BEGIN IMMEDIATE`, matching the repository's other attempt-update methods and preserving the existing Phase 8 state machine without adding execution capability.

## Final Verification

- Editable installation: `python -m pip install -e ".[dev]"` passed; editable `spy-market-agent 0.1.0` installed and all dependencies were already present.
- Full pytest: `pytest --cov-fail-under=85` passed with `887 passed in 81.64s`; required coverage reached with exact combined coverage `85.34%`.
- Unit tests: `pytest tests/unit -q` passed; the unit-only coverage display reported total coverage `85%`; no warning summary was emitted.
- Integration tests: `pytest tests/integration -q` passed with 23 test dots completed; the integration-only coverage display reported total coverage `71%`; no warning summary was emitted.
- Coverage: combined coverage remained above the branch-aware gate at `85.34%`, statement coverage display `85%`, and branch coverage remained enabled.
- Warning policy: `filterwarnings = ["error", ...]` remains enabled with only exact documented upstream warning filters; `pytest -W error::FutureWarning` passed with `887 passed in 82.20s` and emitted no uncontrolled warning summary.
- Ruff: `ruff check .` reported `All checks passed!`
- Formatting: `ruff format --check .` reported `112 files already formatted`.
- MyPy: `mypy src tests` reported `Success: no issues found in 101 source files`.
- Import checks: `python -c "import spy_market_agent; print(spy_market_agent.__version__)"` printed `0.1.0`; `python -c "from spy_market_agent.modeling import fixed_model_parameters; print(fixed_model_parameters('logistic_regression', random_seed=42))"` printed `ModelParameterSet(model_name='logistic_regression', parameters=(('estimator', 'Pipeline'), ('scaler', 'StandardScaler'), ('classifier', 'LogisticRegression'), ('classifier.penalty', 'l2'), ('classifier.C', 1.0), ('classifier.solver', 'liblinear'), ('classifier.max_iter', 2000), ('classifier.class_weight', None), ('classifier.random_state', 42)))`.
- `git diff --check`: passed.

## Confirmations

- Phase 9 was not started.
- No live-trading support was added.
- No API write route was added.
- No dashboard execution control was added.
- Phase 8 paper-only and fail-closed execution boundaries remain unchanged.
