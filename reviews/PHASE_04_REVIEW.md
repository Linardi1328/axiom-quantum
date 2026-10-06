# Phase 04 Review

## Phase objective

Phase 04 implements leakage-safe trailing feature engineering, the Version 1 forward open-to-open classification label, supervised feature/label alignment, and chronological train/validation/test split assignment.

Completion timestamp: 2026-07-25 10:30:32 UTC.

Phase 5 was not started. No model fitting, prediction, probability calibration, threshold selection, signals, backtesting, risk sizing, persistence, API, dashboard, broker communication, paper-order submission, or live trading was implemented.

## Files created

- `src/spy_market_agent/features/models.py`
- `src/spy_market_agent/features/engineering.py`
- `src/spy_market_agent/datasets/__init__.py`
- `src/spy_market_agent/datasets/models.py`
- `src/spy_market_agent/datasets/labels.py`
- `src/spy_market_agent/datasets/splits.py`
- `tests/unit/test_feature_engineering.py`
- `tests/unit/test_label_generation.py`
- `tests/unit/test_chronological_splits.py`
- `tests/integration/test_supervised_dataset_flow.py`
- `reviews/PHASE_04_REVIEW.md`

## Files modified

- `README.md`: Updated project status, repository structure, Phase 4 capabilities, and remaining unimplemented scope.
- `src/spy_market_agent/features/__init__.py`: Exported Phase 4 feature constants, models, errors, and builder.

## Feature definitions

Feature schema version: `spy-daily-features-v1`.

Ordered feature columns:

```text
close_return_1d
close_return_5d
close_return_20d
overnight_gap_1d
intraday_return_1d
range_pct_1d
close_to_sma_5
close_to_sma_20
realized_volatility_5
realized_volatility_20
log_volume_change_1d
log_volume_deviation_20
```

All features use only session `t` and earlier. Rolling windows are trailing, include `t`, use full-window `min_periods`, and are not centered. Realized volatility uses rolling standard deviation of `close_return_1d` with `ddof=0`. Volume features use `math.log1p`.

The first 20 source rows are excluded as deterministic warm-up. Post-warm-up non-finite features fail closed with `FeatureEngineeringError`.

## Label timeline and formula

Label schema version: `spy-open-t1-to-open-t6-net-positive-v1`.

For source session `t` at row `i`:

- `entry_session = source session at i + 1`
- `exit_session = source session at i + 6`
- `entry_open = source open at i + 1`
- `exit_open = source open at i + 6`

The final six source rows are excluded because their exit sessions are unavailable.

Formula:

```text
gross_forward_return = exit_open / entry_open - 1
effective_entry_price = entry_open * (1 + side_cost_rate)
effective_exit_price = exit_open * (1 - side_cost_rate)
net_forward_return = effective_exit_price / effective_entry_price - 1
target = 1 only when net_forward_return > 0, else 0
```

Entry and exit prices are not included in the model feature matrix.

## Cost assumptions

`TradingCostAssumptions` is frozen and requires explicit values:

- `commission_bps_per_side`
- `slippage_bps_per_side`

Both are validated as finite, non-negative `Decimal` values. There is no hidden nonzero default and no silent zero assumption. `side_cost_rate` is `(commission_bps_per_side + slippage_bps_per_side) / 10000`.

## Leakage protections

- Feature engineering copies input market data and does not mutate `MarketDataBatch.data`.
- Feature engineering uses positive lag shifts and trailing rolling windows only.
- No forward-fill, backward-fill, interpolation, centered windows, fitted scaler, full-dataset standardization, or malformed-value repair is performed.
- Supervised assembly keeps `features` and `labels` separate.
- `SupervisedDataset.X` returns only ordered numerical feature columns.
- `SupervisedDataset.y` returns only the binary target.
- Label audit columns are excluded from model features.
- Split assignment includes a row only when the feature session is inside the partition start and the label `exit_session` is inside the partition end.
- Boundary-crossing labels are purged from train and validation partitions.

## Split semantics

`ChronologicalSplitSpec` uses explicit session-date boundaries:

- `train_start_session`
- `train_end_session`
- `validation_start_session`
- `validation_end_session`
- `test_start_session`
- `test_end_session`

The spec rejects overlapping or inverted boundaries. No random split or shuffle is implemented.

A supervised row belongs to a partition only when:

```text
feature session >= partition start
exit_session <= partition end
```

Each partition records included count, feature-session bounds, exit-session bounds, boundary-crossing exclusions, split specification, source checksum, source schema, feature schema, label schema, and feature columns.

## Tests added

- Feature formula tests cover all ordered features, schema version, full 5- and 20-session windows, `ddof=0`, warm-up exclusion, plain-date sessions, float64 dtypes, and non-finite rejection after warm-up.
- Feature leakage tests modify future rows and prove past features are unchanged.
- Label tests cover trading-session row offsets, weekend/holiday behavior, final six-row exclusion, gross/net return formulas, strict positive target construction, flat price plus positive costs, and immutable cost validation.
- Split tests cover invalid boundaries, chronological partitions, no overlap, boundary-crossing purging, exit containment, empty partitions, rejected shuffled ordering, no mutation, and model-input safety.
- Integration test covers validated `MarketDataBatch` to `FeatureSet` to `LabelSet` to `SupervisedDataset` to chronological partitions using deterministic synthetic data only.

## Dependencies

Dependencies added: None.

Dependencies removed: None.

The implementation uses Python 3.12, pandas, Pydantic already present in the project, `Decimal`, and standard-library modules. No scikit-learn, TA-Lib, technical-analysis library, broker SDK, data-vendor SDK, database library, visualization library, API framework, or dashboard library was added.

## Commands executed

Required files were read before implementation, including:

```bash
sed -n '1,240p' PROJECT_SPEC.md
sed -n '241,520p' PROJECT_SPEC.md
sed -n '521,900p' PROJECT_SPEC.md
sed -n '1,260p' AGENTS.md
sed -n '1,260p' README.md
sed -n '1,260p' reviews/PHASE_03_REVIEW.md
sed -n '261,520p' reviews/PHASE_03_REVIEW.md
sed -n '521,837p' reviews/PHASE_03_REVIEW.md
```

Existing Phase 3 source and tests were read before changes, including market-data models, checksum, calendar, provider protocol, validation, settings, and existing unit/integration tests.

Preflight commands included targeted Phase 4 tests, Ruff, and MyPy. During preflight, four targeted pytest commands were run in parallel. Because pytest-cov uses shared coverage data, that preflight approach should not be repeated. The required final verification commands below were rerun sequentially and passed.

Final verification commands:

```bash
.venv/bin/pytest
.venv/bin/pytest tests/unit -q
.venv/bin/pytest tests/integration -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy src tests
.venv/bin/python -c "import spy_market_agent; print(spy_market_agent.__version__)"
git diff --check
```

## Verification results

- `.venv/bin/pytest`: Passed, `182 passed`, `4 warnings`, total coverage `81%`.
- `.venv/bin/pytest tests/unit -q`: Passed, all unit tests passed, `4 warnings`, total coverage `80%`.
- `.venv/bin/pytest tests/integration -q`: Passed, `2 passed`, `4 warnings`.
- `.venv/bin/ruff check .`: Passed with `All checks passed!`.
- `.venv/bin/ruff format --check .`: Passed with `43 files already formatted`.
- `.venv/bin/mypy src tests`: Passed with `Success: no issues found in 37 source files`.
- `.venv/bin/python -c "import spy_market_agent; print(spy_market_agent.__version__)"`: Passed and printed `0.1.0`.
- `git diff --check`: Passed with no output.

## Warnings

- Four visible third-party warnings remain from `exchange-calendars` / pandas / NumPy timedelta internals. They were not globally suppressed.
- The Phase 4 synthetic tests use deterministic generated OHLCV data; no real SPY data or provider download is included.
- A preflight targeted pytest batch was accidentally run in parallel. The final required pytest commands were rerun sequentially and passed.

## Known limitations

- No model fitting, prediction, probability calibration, threshold selection, strategy signals, backtesting, risk sizing, database persistence, APIs, dashboards, broker communication, order submission, or live trading exists.
- Split specs validate date order and non-overlap; callers still choose project-appropriate session-date boundaries.
- `MarketDataBatch`, `FeatureSet`, `LabelSet`, `SupervisedDataset`, and partition objects own copied DataFrames, but pandas DataFrames remain internally mutable by nature.
- Cost assumptions are explicit inputs; Phase 4 does not decide production transaction-cost values.

## Git status and diff summary

Current commit hash before Phase 4 changes:

```text
9206eb3
```

`git status --short --untracked-files=all` after implementation:

```text
 M README.md
 M src/spy_market_agent/features/__init__.py
?? reviews/PHASE_04_REVIEW.md
?? src/spy_market_agent/datasets/__init__.py
?? src/spy_market_agent/datasets/labels.py
?? src/spy_market_agent/datasets/models.py
?? src/spy_market_agent/datasets/splits.py
?? src/spy_market_agent/features/engineering.py
?? src/spy_market_agent/features/models.py
?? tests/integration/test_supervised_dataset_flow.py
?? tests/unit/test_chronological_splits.py
?? tests/unit/test_feature_engineering.py
?? tests/unit/test_label_generation.py
```

Tracked-file `git diff --stat` after implementation:

```text
 README.md                                 | 34 +++++++++++++++++++++++++------
 src/spy_market_agent/features/__init__.py | 18 ++++++++++++++++
 2 files changed, 46 insertions(+), 6 deletions(-)
```

Ordinary `git diff --stat` does not include untracked Phase 4 files. No commit or push was performed.

## Final checklist

- [x] Phase 4 feature engineering implemented.
- [x] Phase 4 label generation implemented.
- [x] Supervised dataset alignment implemented.
- [x] Chronological split assignment implemented.
- [x] Leakage and mutation tests added.
- [x] Integration flow test added.
- [x] README updated.
- [x] Required verification commands passed sequentially.
- [x] No dependencies added.
- [x] No secrets added.
- [x] No market-data downloading added.
- [x] No broker access added.
- [x] No paper-order submission added.
- [x] No live trading added.
- [x] Phase 5 not started.

## Integrity correction addendum

Correction timestamp: 2026-07-25 11:16:06 UTC.

Correction status: Completed. This addendum records a narrowly scoped Phase 4 integrity correction only. Phase 5 was not started.

Problems fixed:

- `LabelSet` validation now requires every row to satisfy `session < entry_session < exit_session`.
- `LabelSet` now verifies internal `entry_session[i] == session[i + 1]` where that label row is present.
- `LabelSet` now verifies internal `exit_session[i] == session[i + 6]` where that label row is present.
- `LabelSet` now verifies `cost_assumptions` is a `TradingCostAssumptions` instance.
- Label, supervised, and partition validation now reject missing targets before converting to Python integers.
- Label, supervised, and partition validation now require target consistency with `net_forward_return`.
- Nullable integer targets containing `pd.NA` now fail with project-owned structured errors.
- Phase 4 checksum validators now accept `object` at validation boundaries and reject non-string or malformed digests with structured project errors.
- Chronological split assignment now requires `session >= start`, `session <= end`, and `exit_session <= end`.
- Split selection now explicitly validates selected feature-session bounds, label chronology, and exit containment.
- Partition metadata now validates partition name, positive count, non-negative boundary-crossing count, checksums, schema versions, feature columns, plain date fields, and ordered bounds.
- Partition validation now checks exact feature and label schemas, row counts, session alignment, finite returns/features, binary non-missing targets, target consistency, metadata bounds, partition date bounds, and exit containment.
- `ChronologicalPartitions` now verifies correct partition names and shared split specification.
- Public Phase 4 package initializer exports are covered by tests.

Files modified during this correction:

- `src/spy_market_agent/features/models.py`
- `src/spy_market_agent/datasets/models.py`
- `src/spy_market_agent/datasets/splits.py`
- `tests/unit/test_feature_engineering.py`
- `tests/unit/test_label_generation.py`
- `tests/unit/test_chronological_splits.py`
- `reviews/PHASE_04_REVIEW.md`

Files created during this correction:

- `tests/unit/test_public_phase4_api.py`

Tests added:

- Backward entry session rejection.
- Backward exit session rejection.
- Entry equal to feature session rejection.
- Exit equal to entry session rejection.
- Internal entry-session alignment mismatch rejection.
- Internal exit-session alignment mismatch rejection.
- Positive net return with target zero rejection.
- Non-positive net return with target one rejection.
- Nullable `pd.NA` target rejection through structured errors.
- Non-string and malformed checksum rejection for feature, label, supervised, and partition metadata.
- Malformed feature session after partition end with earlier exit is not included in the earlier partition.
- Partition metadata mismatch rejection.
- Partition metadata invalid name and negative boundary-exclusion rejection.
- Chronological partition name and split-spec mismatch rejection.
- Public `spy_market_agent.features` and `spy_market_agent.datasets` imports and `__all__` coverage.

Verification commands were run sequentially:

```bash
.venv/bin/pytest
.venv/bin/pytest tests/unit -q
.venv/bin/pytest tests/integration -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy src tests
.venv/bin/python -c "import spy_market_agent; print(spy_market_agent.__version__)"
git diff --check
```

Actual verification results:

- `.venv/bin/pytest`: Passed, `224 passed`, `4 warnings`, total coverage `81%`.
- `.venv/bin/pytest tests/unit -q`: Passed, all unit tests passed, `4 warnings`, total coverage `81%`.
- `.venv/bin/pytest tests/integration -q`: Passed, `2 passed`, `4 warnings`.
- `.venv/bin/ruff check .`: Passed with `All checks passed!`.
- `.venv/bin/ruff format --check .`: Passed with `44 files already formatted`.
- `.venv/bin/mypy src tests`: Passed with `Success: no issues found in 38 source files`.
- `.venv/bin/python -c "import spy_market_agent; print(spy_market_agent.__version__)"`: Passed and printed `0.1.0`.
- `git diff --check`: Passed with no output.

Remaining warnings:

- Four visible third-party warnings remain from `exchange-calendars` / pandas / NumPy timedelta internals. They were not globally suppressed.

Correction git snapshot before this addendum:

```text
M  README.md
A  reviews/PHASE_04_REVIEW.md
A  src/spy_market_agent/datasets/__init__.py
A  src/spy_market_agent/datasets/labels.py
AM src/spy_market_agent/datasets/models.py
AM src/spy_market_agent/datasets/splits.py
M  src/spy_market_agent/features/__init__.py
A  src/spy_market_agent/features/engineering.py
AM src/spy_market_agent/features/models.py
A  tests/integration/test_supervised_dataset_flow.py
AM tests/unit/test_chronological_splits.py
AM tests/unit/test_feature_engineering.py
AM tests/unit/test_label_generation.py
?? tests/unit/test_public_phase4_api.py
```

Tracked diff summary before this addendum:

```text
 src/spy_market_agent/datasets/models.py | 215 ++++++++++++++++++++++--------
 src/spy_market_agent/datasets/splits.py | 226 +++++++++++++++++++++++++++++---
 src/spy_market_agent/features/models.py |  11 +-
 tests/unit/test_chronological_splits.py | 218 ++++++++++++++++++++++++++++++
 tests/unit/test_feature_engineering.py  |  27 ++++
 tests/unit/test_label_generation.py     | 183 ++++++++++++++++++++++++++
 6 files changed, 810 insertions(+), 70 deletions(-)
```

No `.venv` or `venv` directory was deleted, recreated, replaced, or cleaned. No commit or push was performed.

Correction checklist:

- [x] Label timeline integrity strengthened.
- [x] Target consistency enforced.
- [x] Checksum validation hardened.
- [x] Chronological split assignment hardened.
- [x] Partition validation strengthened.
- [x] Public initializer tests added.
- [x] Regression tests added.
- [x] Required verification commands passed sequentially.
- [x] No dependencies added.
- [x] No model fitting or Phase 5 work started.
- [x] No predictions, calibration, thresholds, signals, backtesting, persistence, APIs, dashboards, broker access, execution behavior, paper-order submission, or live trading added.

## Final metadata-validation correction note

Correction timestamp: 2026-07-25 12:13:31 UTC.

Correction status: Completed. This note records one final narrowly scoped Phase 4 metadata-validation correction only. Phase 5 was not started.

Problems fixed:

- `SupervisedDatasetMetadata` now validates `first_session` and `last_session` as plain `datetime.date` values before comparing them.
- `SupervisedDatasetMetadata` now validates `row_count` as an integer, rejects booleans, and then enforces `row_count > 0`.
- `SupervisedDatasetMetadata` now validates `feature_columns` is a tuple exactly matching the ordered Phase 4 feature schema before comparing it, avoiding raw conversion failures for values such as `None`.
- Similar Phase 4 metadata paths were reviewed. Narrow type guards were added where malformed public values could raise raw Python exceptions:
  - `FeatureSet` now validates `feature_columns` as a tuple before schema comparison and validates `row_count` as a non-boolean integer.
  - `DatasetPartitionMetadata` now validates `included_row_count` and `rows_excluded_boundary_crossing` as non-boolean integers before numeric comparisons, and validates `feature_columns` as the exact ordered tuple.
- `LabelSet` and `ChronologicalSplitSpec` were reviewed; their relevant malformed date or session inputs are already routed through structured project-owned validation paths.

Regression tests added:

- `SupervisedDatasetMetadata(first_session=None)` fails with `DatasetAlignmentError`.
- `SupervisedDatasetMetadata(last_session=None)` fails with `DatasetAlignmentError`.
- `datetime` supplied as either supervised metadata session bound fails with `DatasetAlignmentError`.
- `row_count="1"` fails with `DatasetAlignmentError`.
- `row_count=True` fails with `DatasetAlignmentError`.
- `feature_columns=None` fails with `DatasetAlignmentError`.

Files modified during this correction:

- `src/spy_market_agent/features/models.py`
- `src/spy_market_agent/datasets/models.py`
- `src/spy_market_agent/datasets/splits.py`
- `tests/unit/test_chronological_splits.py`
- `reviews/PHASE_04_REVIEW.md`

Verification commands were run sequentially:

```bash
.venv/bin/pytest
.venv/bin/pytest tests/unit -q
.venv/bin/pytest tests/integration -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy src tests
.venv/bin/python -c "import spy_market_agent; print(spy_market_agent.__version__)"
git diff --check
```

Actual verification results:

- `.venv/bin/pytest`: Passed, `231 passed`, `4 warnings`, total coverage `81%`.
- `.venv/bin/pytest tests/unit -q`: Passed, all unit tests passed, `4 warnings`, total coverage `81%`.
- `.venv/bin/pytest tests/integration -q`: Passed, `2 passed`, `4 warnings`.
- `.venv/bin/ruff check .`: Passed with `All checks passed!`.
- `.venv/bin/ruff format --check .`: Passed with `44 files already formatted`.
- `.venv/bin/mypy src tests`: Passed with `Success: no issues found in 38 source files`.
- `.venv/bin/python -c "import spy_market_agent; print(spy_market_agent.__version__)"`: Passed and printed `0.1.0`.
- `git diff --check`: Passed with no output.

Remaining warnings:

- Four visible third-party warnings remain from `exchange-calendars` / pandas / NumPy timedelta internals. They were not globally suppressed.

Correction git snapshot before this note:

```text
M  README.md
A  reviews/PHASE_04_REVIEW.md
A  src/spy_market_agent/datasets/__init__.py
A  src/spy_market_agent/datasets/labels.py
AM src/spy_market_agent/datasets/models.py
AM src/spy_market_agent/datasets/splits.py
M  src/spy_market_agent/features/__init__.py
A  src/spy_market_agent/features/engineering.py
AM src/spy_market_agent/features/models.py
A  tests/integration/test_supervised_dataset_flow.py
AM tests/unit/test_chronological_splits.py
A  tests/unit/test_feature_engineering.py
A  tests/unit/test_label_generation.py
A  tests/unit/test_public_phase4_api.py
```

Tracked diff summary before this note:

```text
 src/spy_market_agent/datasets/models.py | 72 +++++++++++++++++++++++++++++----
 src/spy_market_agent/datasets/splits.py | 42 +++++++++++++------
 src/spy_market_agent/features/models.py |  6 ++-
 tests/unit/test_chronological_splits.py | 45 +++++++++++++++++++++
 4 files changed, 143 insertions(+), 22 deletions(-)
```

No `.venv` or `venv` directory was deleted, recreated, replaced, or cleaned. No commit or push was performed.

Final metadata correction checklist:

- [x] Supervised metadata date bounds hardened.
- [x] Supervised metadata row count hardened.
- [x] Supervised metadata feature-column validation hardened.
- [x] Similar Phase 4 metadata fields reviewed.
- [x] Narrow runtime guards added where needed.
- [x] Regression tests added.
- [x] Required verification commands passed sequentially.
- [x] No feature formulas, label formulas, cost calculations, split semantics, model inputs, dependencies, or public package exports changed.
- [x] Phase 5 not started.
