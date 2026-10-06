# Phase 6 Review

## Phase Objective

Implemented an in-memory Phase 6 research layer that converts locked final-test probabilities into fixed long-or-cash target positions, maps signals to the next validated market open, sends proposed orders through independent risk controls, simulates approved orders with explicit costs, and records deterministic audit frames and metrics.

Phase 7 was not started.

## Files Created

- `src/spy_market_agent/strategies/models.py`
- `src/spy_market_agent/strategies/signal_policy.py`
- `src/spy_market_agent/risk/models.py`
- `src/spy_market_agent/risk/rules.py`
- `src/spy_market_agent/backtesting/models.py`
- `src/spy_market_agent/backtesting/costs.py`
- `src/spy_market_agent/backtesting/engine.py`
- `src/spy_market_agent/backtesting/metrics.py`
- `tests/unit/phase6_helpers.py`
- `tests/unit/test_strategy_signal_policy.py`
- `tests/unit/test_risk_rules.py`
- `tests/unit/test_backtest_costs.py`
- `tests/unit/test_backtest_engine.py`
- `tests/unit/test_backtest_metrics.py`
- `tests/unit/test_public_phase6_api.py`
- `tests/integration/test_phase6_research_flow.py`
- `reviews/PHASE_06_REVIEW.md`

## Files Modified

- `README.md`
- `src/spy_market_agent/strategies/__init__.py`
- `src/spy_market_agent/risk/__init__.py`
- `src/spy_market_agent/backtesting/__init__.py`

## Dependencies

No dependencies were added or removed.

## Strategy Rule

- Strategy schema: `spy-long-cash-strategy-v1`.
- Fixed threshold: `0.5`.
- `target_position = 1` when `probability_positive >= 0.5`; otherwise `0`.
- Phase 6 uses `probability_positive` from the locked final-test prediction audit frame, not Phase 5 `predicted_class`.
- Signals include `signal_session`, `execution_session`, `probability_positive`, and `target_position`.

## Next-Candle Mapping

Execution sessions are selected by the immediate next row in the validated `MarketDataBatch`. Calendar-day arithmetic is not used. Same-candle and backward execution are rejected.

## Cost Formulas

Cost assumptions require explicit non-negative finite `Decimal` values:

- `commission_bps_per_side`
- `slippage_bps_per_side`

Buy execution uses `reference_open * (1 + slippage_rate)`. Sell execution uses `reference_open * (1 - slippage_rate)`. Commission is calculated on execution notional. Slippage cost is audited as `abs(execution_price - reference_open) * quantity`.

## Position Sizing

- Starts with exactly `Decimal("10000")` simulated cash and zero shares.
- Buys the maximum affordable whole-share quantity when the target changes from cash to long.
- If no share is affordable, proposes one share so independent risk rejects it.
- Repeated long targets do not rebalance or pyramid.
- Repeated cash targets do not create orders.
- Cash targets sell the entire current whole-share position.
- No final forced liquidation is performed.

## Risk Rules

Risk schema: `spy-long-only-risk-v1`.

Risk is independent of model scores and does not accept probability or confidence inputs. It rejects non-SPY symbols, short-selling attempts, leverage, fractional or invalid quantities, insufficient cash, sell quantity above holdings, invalid target transitions, same-session execution, invalid prices, and invalid portfolio state.

Every proposed order receives exactly one risk decision. Rejected orders do not create fills or alter portfolio state.

## Backtest Accounting

Backtest schema: `spy-daily-next-open-backtest-v1`.

The engine records proposed orders, risk decisions, fills, and one portfolio row per execution session. Portfolio rows track cash, shares, close price, market value, equity, daily return, and drawdown. Running peak starts at initial cash.

## Metrics

Implemented deterministic in-memory metrics:

- session count
- final cash, shares, market value, and equity
- total return
- maximum drawdown
- reference and execution notional totals
- commission, slippage, and total transaction costs
- turnover ratio
- exposure fraction
- proposed, approved, rejected, fill, buy-fill, and sell-fill counts

No benchmark, alpha, beta, VaR, Sharpe, Sortino, profit factor, significance claim, or profitability claim was added.

## Reproducibility

All Phase 6 APIs require explicit timestamps and use deterministic input DataFrames. No current time, environment value, network access, persistence, broker action, model fitting, or model probability generation is used by the Phase 6 backtest.

## Structured Errors

Added project-owned structured errors:

- `StrategyError`
- `StrategyInputError`
- `RiskError`
- `RiskInputError`
- `BacktestError`
- `BacktestInputError`
- `BacktestAccountingError`
- `BacktestMetricError`

Expected malformed Phase 6 inputs are converted to these structured errors.

## Tests Added

- Strategy threshold and next-session mapping tests.
- Strategy lineage, malformed prediction, same-candle, mutation, and future-row leakage tests.
- Cost formula and invalid assumption tests.
- Risk configuration, approval, rejection, and projection tests.
- Backtest engine chronology, sizing, rejection, mutation, and leakage tests.
- Backtest metric formula and invariant tests.
- Public API import and `__all__` tests.
- Full Phase 3-to-6 deterministic integration flow test.

## Verification Results

Interim verification before the final required sequence:

- `ruff check src/spy_market_agent/strategies src/spy_market_agent/risk src/spy_market_agent/backtesting tests/unit/phase6_helpers.py tests/unit/test_strategy_signal_policy.py tests/unit/test_backtest_costs.py tests/unit/test_risk_rules.py tests/unit/test_backtest_engine.py tests/unit/test_backtest_metrics.py tests/unit/test_public_phase6_api.py tests/integration/test_phase6_research_flow.py` passed.
- `mypy src/spy_market_agent/strategies src/spy_market_agent/risk src/spy_market_agent/backtesting tests/unit/phase6_helpers.py tests/unit/test_strategy_signal_policy.py tests/unit/test_backtest_costs.py tests/unit/test_risk_rules.py tests/unit/test_backtest_engine.py tests/unit/test_backtest_metrics.py tests/unit/test_public_phase6_api.py tests/integration/test_phase6_research_flow.py` passed.
- `pytest tests/unit/test_strategy_signal_policy.py tests/unit/test_backtest_costs.py tests/unit/test_risk_rules.py tests/unit/test_backtest_engine.py tests/unit/test_backtest_metrics.py tests/unit/test_public_phase6_api.py tests/integration/test_phase6_research_flow.py -q` passed: 41 passed.
- `pytest` passed: 380 passed.

Final required verification sequence:

- `pytest` passed: 380 passed, 4 warnings, total coverage 79%.
- `pytest tests/unit -q` passed: 360 passed, 4 warnings, total coverage 79%.
- `pytest tests/integration -q` passed: 4 passed, 4 warnings, integration-only coverage 69%.
- `ruff check .` passed.
- `ruff format --check .` passed: 73 files already formatted.
- `mypy src tests` passed: no issues found in 65 source files.
- `python -c "import spy_market_agent; print(spy_market_agent.__version__)"` passed and printed `0.1.0`.
- `python -c "import spy_market_agent.strategies as strategies; print(sorted(strategies.__all__))"` passed.
- `python -c "import spy_market_agent.risk as risk; print(sorted(risk.__all__))"` passed.
- `python -c "import spy_market_agent.backtesting as backtesting; print(sorted(backtesting.__all__))"` passed.
- `git diff --check` passed with no whitespace errors.

## Coverage

The full interim `pytest` run reported total coverage of 79%.

## Warnings

The test suite still reports third-party `exchange_calendars`/pandas `DeprecationWarning` messages about the generic NumPy timedelta unit. These warnings were not globally suppressed.

## Known Limitations

- No market-data downloading.
- No model refitting or reselection in Phase 6.
- No probability calibration or threshold optimization.
- No strategy optimization.
- No persistence, SQLite, API, dashboard, broker communication, Alpaca integration, paper order, live order, scheduling, or deployment.
- No short selling, leverage, margin, fractional shares, multi-asset portfolios, stops, limits, or intraday execution.

## Git Status And Diff Summary

Current git status shows modified tracked files:

- `README.md`
- `src/spy_market_agent/backtesting/__init__.py`
- `src/spy_market_agent/risk/__init__.py`
- `src/spy_market_agent/strategies/__init__.py`

Current git status shows untracked Phase 6 files:

- `reviews/PHASE_06_REVIEW.md`
- new `strategies`, `risk`, and `backtesting` implementation modules
- new Phase 6 unit tests and integration test

Tracked diff summary at final verification:

- `README.md`: 32 lines changed.
- `src/spy_market_agent/backtesting/__init__.py`: 50 insertions.
- `src/spy_market_agent/risk/__init__.py`: 54 insertions.
- `src/spy_market_agent/strategies/__init__.py`: 20 insertions.

The broader diff also includes the untracked Phase 6 source, test, and review files listed above.

## Phase 7 Confirmation

Phase 7 was not started. No persistence, broker integration, API, dashboard, paper execution, live execution, scheduling, or deployment behavior was implemented.

## Audit-Integrity Correction Addendum

Correction timestamp: 2026-07-26 UTC.

Correction status: Completed. This addendum records a narrowly scoped Phase 6 audit-integrity and independent-risk correction only. Phase 7 was not started.

Problems corrected:

- `StrategySignalSet` now records immutable source market-session lineage and verifies every execution session is exactly one validated market row after its signal session.
- Strategy signal metadata now rejects wrong label-schema lineage and wrong in-memory scikit-learn version.
- `evaluate_order_risk()` now rejects pyramiding buys, partial cash-target exits, and proposed orders whose stored estimates do not match independent cost recomputation.
- `RiskDecision` now accepts only known Version 1 reason codes, rejects duplicate reason codes, keeps approved decisions to exactly `("approved",)`, and rejects rejected decisions containing `"approved"`.
- Public side/symbol boundaries now reject pandas `Series`, pandas `Index`, lists, dictionaries, and non-string values through structured errors instead of ambiguous truth or raw Python errors.
- `OrderCostEstimate` now validates side, quantity, prices, notionals, slippage cost, total transaction cost, cash change, and stores normalized `Decimal` values.
- `FillRecord` now writes validated normalized values back onto the frozen object, so numeric strings do not remain unvalidated strings.
- `BacktestMetrics` now rejects false initial cash, false total return, final-equity identity mismatches, and transaction-cost identity mismatches.
- `BacktestResult` now deeply reconstructs proposed orders, risk decisions, fills, and portfolio rows; replays the full audit path from `$10,000` and zero shares; reruns independent risk; reconciles fills; validates rejected-order no-state-change behavior; and recomputes all metrics from the validated frames.
- `BacktestResult.created_at` must now match the nested strategy signal-set timestamp, preserving deterministic engine lineage.

Files modified during this correction:

- `src/spy_market_agent/strategies/models.py`
- `src/spy_market_agent/strategies/signal_policy.py`
- `src/spy_market_agent/risk/__init__.py`
- `src/spy_market_agent/risk/models.py`
- `src/spy_market_agent/risk/rules.py`
- `src/spy_market_agent/backtesting/costs.py`
- `src/spy_market_agent/backtesting/engine.py`
- `src/spy_market_agent/backtesting/models.py`
- `tests/unit/test_strategy_signal_policy.py`
- `tests/unit/test_risk_rules.py`
- `tests/unit/test_backtest_costs.py`
- `tests/unit/test_backtest_engine.py`
- `tests/unit/test_backtest_metrics.py`
- `reviews/PHASE_06_REVIEW.md`

Tests added or strengthened:

- False metric identities: initial cash, total return, final equity, and total transaction cost.
- Strategy label-schema mismatch, scikit-learn-version mismatch, and two-market-row execution mapping.
- Pyramiding buy rejection, partial sell rejection, false proposed-order cost estimate rejection, unknown risk reason code rejection, and duplicate reason code rejection.
- Structured pandas `Series`/`Index` boundary failures for cost side, order side, order symbol, and risk supported symbol.
- Impossible direct `OrderCostEstimate` construction.
- `FillRecord` numeric-string normalization.
- Full backtest audit tampering: flat portfolio with fills, target mismatch, shares mismatch, negative order quantity, false cost estimates, fabricated balanced risk projection, fill/order mismatch, fill state discontinuity, fill costs inconsistent with assumptions, rejected-decision projection change, rejected-order portfolio state change, and false stored metrics.

Verification commands run sequentially after the correction:

```bash
pytest
pytest tests/unit -q
pytest tests/integration -q
ruff check .
ruff format --check .
mypy src tests
python -c "import spy_market_agent; print(spy_market_agent.__version__)"
python -c "import spy_market_agent.strategies as strategies; print(sorted(strategies.__all__))"
python -c "import spy_market_agent.risk as risk; print(sorted(risk.__all__))"
python -c "import spy_market_agent.backtesting as backtesting; print(sorted(backtesting.__all__))"
git diff --check
```

Actual verification results:

- `pytest`: Passed, `391 passed`, `4 warnings`, total coverage `79%`.
- `pytest tests/unit -q`: Passed, `387` unit tests, `4 warnings`, total coverage `79%`.
- `pytest tests/integration -q`: Passed, `4 passed`, `4 warnings`, integration-only coverage `69%`.
- `ruff check .`: Passed with `All checks passed!`.
- `ruff format --check .`: Passed with `73 files already formatted`.
- `mypy src tests`: Passed with `Success: no issues found in 65 source files`.
- `python -c "import spy_market_agent; print(spy_market_agent.__version__)"`: Passed and printed `0.1.0`.
- Phase 6 public `__all__` import checks for `strategies`, `risk`, and `backtesting`: Passed.
- `git diff --check`: Passed with no whitespace errors.

Coverage:

- Full-suite coverage remained `79%`.
- Integration-only coverage remained `69%`.

Remaining warnings:

- Four third-party deprecation warnings remain from `exchange_calendars` / pandas / NumPy timedelta internals. They were not globally suppressed.

Git status and diff summary:

- Existing Phase 6 files remain uncommitted.
- This correction modified the Phase 6 strategy, risk, and backtesting modules plus Phase 6 regression tests.
- Tracked diff summary for this correction showed 13 files changed with 1,704 insertions and 18 deletions before this review addendum was appended. The broader workspace still includes the original uncommitted Phase 6 source, test, README, and review files.

Confirmation:

- No dependencies were added, removed, or changed.
- The fixed `0.5` strategy threshold, next-open execution behavior, feature definitions, label definitions, model definitions, split semantics, cost formulas, initial simulated cash, and public package names were not changed.
- No `.venv` or `venv` directory was deleted, recreated, replaced, or cleaned.
- No commit or push was performed.
- Phase 7 was not started. No persistence, SQLite, FastAPI, Streamlit, dashboard, broker communication, Alpaca integration, paper-order submission, live trading, scheduling, or deployment behavior was implemented.

## Final Market-Price-Lineage Addendum

Correction timestamp: 2026-07-26 UTC.

Correction status: Completed. This addendum records one narrowly scoped final Phase 6 market-price-lineage and metric-boundary correction only. Phase 7 was not started.

Problems corrected:

- Added `ExecutionPriceSet`, a frozen audited execution-price object built from the revalidated `MarketDataBatch` inside `run_long_or_cash_backtest()`.
- The execution-price audit frame stores exactly `execution_session`, `reference_open`, and `close_price`, records source checksum/schema lineage, execution-session bounds, row count, UTC timestamp, and a deterministic checksum of the canonical price frame.
- `BacktestResult` now stores and reconstructs `ExecutionPriceSet`, verifies its checksum, binds it to strategy/result source lineage, and requires its sessions to exactly match strategy execution sessions.
- Backtest audit replay now treats `ExecutionPriceSet` as the authoritative source for execution opens and close marks. Proposed-order opens, fill reference opens, and portfolio close prices must match that market-price lineage.
- Coordinated false portfolio prices with internally recalculated metrics are now rejected because replay uses the independent execution-price frame rather than portfolio self-declared prices.
- `calculate_backtest_metrics()` now validates required portfolio, fill, proposed-order, and risk-decision values before reductions, casts, comparisons, and arithmetic.
- Malformed public metric-frame values such as strings, lists, pandas `Series`, pandas `Index`, `None`, NaN, infinity, and incorrect scalar dtypes now fail through `BacktestMetricError`.
- `ExecutionPriceSet` was added to the explicit public backtesting API exports because it is a stored Phase 6 result type.

Files modified during this correction:

- `src/spy_market_agent/backtesting/__init__.py`
- `src/spy_market_agent/backtesting/engine.py`
- `src/spy_market_agent/backtesting/metrics.py`
- `src/spy_market_agent/backtesting/models.py`
- `tests/integration/test_phase6_research_flow.py`
- `tests/unit/test_backtest_engine.py`
- `tests/unit/test_backtest_metrics.py`
- `tests/unit/test_public_phase6_api.py`
- `reviews/PHASE_06_REVIEW.md`

Tests added or changed:

- Execution-price lineage is deterministic across repeated backtest runs.
- Portfolio close-price tampering is rejected even when portfolio accounting and metrics are recalculated consistently.
- Proposed-order reference opens differing from execution-price lineage are rejected.
- Fill reference opens differing from execution-price lineage are rejected.
- Mutated execution-price frames with stale checksums are rejected.
- Execution-price source checksum mismatches are rejected.
- Public backtesting exports include `ExecutionPriceSet` and `EXECUTION_PRICE_COLUMNS`.
- Metric boundary tests now reject string portfolio shares, string drawdown, non-Boolean risk approvals, non-finite metric-frame values, pandas `Series`/`Index` audit cells, lists, `None`, and other incorrect public dtypes through structured errors.
- The Phase 6 integration flow now asserts deterministic execution-price frames and checksums.

Verification commands run sequentially after the correction and Ruff formatting:

```bash
pytest
pytest tests/unit -q
pytest tests/integration -q
ruff check .
ruff format --check .
mypy src tests
python -c "import spy_market_agent; print(spy_market_agent.__version__)"
python -c "import spy_market_agent.strategies as strategies; print(sorted(strategies.__all__))"
python -c "import spy_market_agent.risk as risk; print(sorted(risk.__all__))"
python -c "import spy_market_agent.backtesting as backtesting; print(sorted(backtesting.__all__))"
git diff --check
```

Actual verification results:

- `pytest`: Passed, `408 passed`, `4 warnings`, total coverage `79%`.
- `pytest tests/unit -q`: Passed, `404` unit tests passed, `4 warnings`, total coverage `79%`.
- `pytest tests/integration -q`: Passed, `4` integration tests passed, `4 warnings`, integration-only coverage `69%`.
- `ruff check .`: Passed with `All checks passed!`.
- `ruff format --check .`: Passed with `73 files already formatted`.
- `mypy src tests`: Passed with `Success: no issues found in 65 source files`.
- `python -c "import spy_market_agent; print(spy_market_agent.__version__)"`: Passed and printed `0.1.0`.
- Phase 6 public `__all__` import checks for `strategies`, `risk`, and `backtesting`: Passed. Backtesting exports now include `ExecutionPriceSet` and `EXECUTION_PRICE_COLUMNS`.
- `git diff --check`: Passed with no whitespace errors.

Coverage:

- Full-suite coverage remained `79%`.
- Integration-only coverage remained `69%`.

Remaining warnings:

- Four third-party deprecation warnings remain from `exchange_calendars` / pandas / NumPy timedelta internals. They were not globally suppressed.

Confirmation:

- No dependencies were added, removed, or changed.
- The fixed strategy threshold, next-open execution behavior, model probabilities, feature definitions, label definitions, model definitions, split semantics, position-sizing policy, implemented risk rules, transaction-cost formulas, initial simulated cash, and public package names were not changed.
- No `.venv` or `venv` directory was deleted, recreated, replaced, or cleaned.
- No commit or push was performed.
- Phase 7 was not started. No persistence, SQLite, FastAPI, Streamlit, dashboard, broker communication, Alpaca integration, paper-order submission, live trading, scheduling, or deployment behavior was implemented.

## Final Source-Market Provenance Addendum

Problem corrected:

- `ExecutionPriceSet` previously proved only the extracted execution-price frame checksum and retained the claimed source-market checksum. A coordinated alteration could replace execution opens or closes, recalculate the extracted price checksum, recalculate orders, fills, portfolio history, and metrics, while preserving the original claimed source checksum.

Source-market evidence retained:

- `BacktestResult` now stores `source_market_data: MarketDataBatch` as owned source evidence.
- `BacktestResult` reconstructs the supplied batch from its data and metadata, forcing the Phase 3 checksum validation path to run again.
- The stored source batch must be SPY, use the approved market-data schema, and have a recomputed dataset checksum matching the backtest result, strategy signal set, and execution-price lineage.

Execution-price re-derivation:

- During `BacktestResult` reconstruction, every strategy execution session is looked up in the stored canonical source market-data batch.
- The expected `execution_session`, `reference_open`, and `close_price` frame is rebuilt from source-market rows, then compared to the stored `ExecutionPriceSet`.
- Stored execution opens and closes that differ from the source batch are rejected with structured accounting errors.
- Missing source execution sessions are rejected through a structured error.

Files modified:

- `src/spy_market_agent/backtesting/engine.py`
- `src/spy_market_agent/backtesting/models.py`
- `tests/integration/test_phase6_research_flow.py`
- `tests/unit/test_backtest_engine.py`
- `reviews/PHASE_06_REVIEW.md`

Tests added or changed:

- Coordinated execution close-price replacement is rejected even when the execution-price checksum, portfolio history, and metrics are recomputed.
- Coordinated execution open-price replacement is rejected even when order estimates, risk projections, fills, portfolio history, execution-price checksum, and metrics are recomputed.
- Mutating the caller-owned source market DataFrame after a valid result is constructed does not alter the stored result.
- Reconstructing with a stale mutated source batch fails through a structured source-market error.
- A different valid source batch with its own checksum is rejected when strategy/final-test lineage still claims the original checksum.
- A valid source batch missing required execution sessions is rejected through a structured missing-session error.
- The Phase 6 integration flow now asserts the stored source market batch and execution-price source lineage agree.

Verification commands run sequentially:

```bash
pytest
pytest tests/unit -q
pytest tests/integration -q
ruff check .
ruff format --check .
mypy src tests
python -c "import spy_market_agent; print(spy_market_agent.__version__)"
python -c "import spy_market_agent.strategies as strategies; print(sorted(strategies.__all__))"
python -c "import spy_market_agent.risk as risk; print(sorted(risk.__all__))"
python -c "import spy_market_agent.backtesting as backtesting; print(sorted(backtesting.__all__))"
git diff --check
```

Actual verification results:

- `pytest`: Passed, `412 passed`, `4 warnings`, total coverage `79%`.
- `pytest tests/unit -q`: Passed, `408` unit tests passed, `4 warnings`, total coverage `79%`.
- `pytest tests/integration -q`: Passed, `4` integration tests passed, `4 warnings`, integration-only coverage `69%`.
- `ruff check .`: Passed with `All checks passed!`.
- `ruff format --check .`: Passed with `73 files already formatted`.
- `mypy src tests`: Passed with `Success: no issues found in 65 source files`.
- `python -c "import spy_market_agent; print(spy_market_agent.__version__)"`: Passed and printed `0.1.0`.
- `python -c "import spy_market_agent.strategies as strategies; print(sorted(strategies.__all__))"`: Passed and printed the explicit Phase 6 strategy exports.
- `python -c "import spy_market_agent.risk as risk; print(sorted(risk.__all__))"`: Passed and printed the explicit Phase 6 risk exports.
- `python -c "import spy_market_agent.backtesting as backtesting; print(sorted(backtesting.__all__))"`: Passed and printed the explicit Phase 6 backtesting exports.
- `git diff --check`: Passed with no whitespace errors.

Coverage and warnings:

- Full-suite coverage remained `79%`.
- Integration-only coverage remained `69%`.
- Four third-party deprecation warnings remain from `exchange_calendars` / pandas / NumPy timedelta internals. They were not globally suppressed.

Confirmation:

- No dependencies were added, removed, or changed.
- The fixed strategy threshold, signal generation, next-open execution, position-sizing policy, implemented risk rules, cost formulas, metric formulas, initial simulated capital, and public package names were not changed.
- No network access, persistence, API, dashboard, broker communication, paper-order submission, live trading, scheduling, deployment, or Phase 7 behavior was implemented.
- No `.venv` or `venv` directory was deleted, recreated, replaced, or cleaned.
- No commit or push was performed.
- Phase 7 was not started.

## Final Source-Checksum and Ownership Addendum

Problem corrected:

- Phase 6 retained a source `MarketDataBatch`, but the backtest source reconstruction path did not explicitly recompute the canonical Phase 3 checksum before accepting the retained source evidence. A caller could mutate the retained source DataFrame while leaving the original metadata checksum in place, including on a non-execution row that did not affect extracted execution prices, orders, fills, portfolio rows, or metrics.
- Metadata ownership at the engine boundary reused the caller-owned `MarketDataMetadata` object, so later caller mutation of checksum, provider, source description, or timestamp metadata could affect retained evidence by object identity.

Correction to earlier wording:

- `MarketDataBatch` construction alone is no longer treated as the source-checksum proof in Phase 6.
- Phase 6 now calls `compute_market_data_checksum()` directly on the owned canonical source DataFrame during `BacktestResult` source reconstruction.
- The directly recomputed checksum must equal the source batch metadata checksum, the `BacktestResult` source checksum, the `StrategySignalSet` source checksum, and the `ExecutionPriceSet` source checksum.

Source-market evidence retained:

- The engine now deep-copies source market metadata with `model_copy(deep=True)` before rebuilding its internal `MarketDataBatch`.
- `BacktestResult` reconstructs source data from a deep-copied DataFrame and independently copied metadata.
- Source metadata is validated against the retained frame for row count, first session, and last session.
- The retained source frame must preserve canonical column order, unique strictly increasing plain-date sessions, `float64` OHLC columns, `int64` volume, finite positive OHLC values, non-negative volume, SPY symbol, and the approved market-data schema.
- Caller mutation of the original DataFrame or metadata after result construction cannot alter the retained source evidence.

Files modified:

- `src/spy_market_agent/backtesting/engine.py`
- `src/spy_market_agent/backtesting/models.py`
- `tests/unit/test_backtest_engine.py`
- `reviews/PHASE_06_REVIEW.md`

Tests added or changed:

- Stale checksum on a non-execution source OHLC row is rejected through `source_market_checksum_recomputation_mismatch`.
- Stale checksum on an execution source open row is rejected during checksum recomputation.
- Caller metadata mutation after construction does not affect retained result metadata, and retained metadata is a different object from caller metadata.
- Caller DataFrame mutation after construction does not affect retained source data or checksum.
- Incorrect source metadata first/last session bounds are rejected.
- Unordered sessions, duplicate sessions, wrong OHLC dtype, wrong volume dtype, NaN, infinity, and boolean numeric source values are rejected through structured Phase 6 errors.
- Existing source-derived execution-price, replay, risk, fill, portfolio, and metric protections were preserved.

Verification commands run sequentially:

```bash
pytest
pytest tests/unit -q
pytest tests/integration -q
ruff check .
ruff format --check .
mypy src tests
python -c "import spy_market_agent; print(spy_market_agent.__version__)"
python -c "import spy_market_agent.strategies as strategies; print(sorted(strategies.__all__))"
python -c "import spy_market_agent.risk as risk; print(sorted(risk.__all__))"
python -c "import spy_market_agent.backtesting as backtesting; print(sorted(backtesting.__all__))"
git diff --check
```

Actual verification results:

- `pytest`: Passed, `424 passed`, `4 warnings`, total coverage `79%`.
- `pytest tests/unit -q`: Passed with exit code `0`, unit-only coverage `79%`, `4 warnings`.
- `pytest tests/integration -q`: Passed, `4 passed`, `4 warnings`, integration-only coverage `69%`.
- `ruff check .`: Passed with `All checks passed!`.
- `ruff format --check .`: Passed with `73 files already formatted`.
- `mypy src tests`: Passed with `Success: no issues found in 65 source files`.
- `python -c "import spy_market_agent; print(spy_market_agent.__version__)"`: Passed and printed `0.1.0`.
- Strategy import check: Passed and printed the explicit `spy_market_agent.strategies.__all__` exports.
- Risk import check: Passed and printed the explicit `spy_market_agent.risk.__all__` exports.
- Backtesting import check: Passed and printed the explicit `spy_market_agent.backtesting.__all__` exports.
- `git diff --check`: Passed with no whitespace errors.

Coverage and warnings:

- Full-suite coverage remained `79%`.
- Integration-only coverage remained `69%`.
- Four third-party deprecation warnings remain from `exchange_calendars` / pandas / NumPy timedelta internals. They were not globally suppressed.

Confirmation:

- No dependencies were added, removed, or changed.
- Strategy behavior, next-open execution, position sizing, risk rules, cost formulas, metrics, initial simulated capital, and public package names were not changed.
- No persistence, SQLite, API, dashboard, broker communication, paper-order submission, live trading, deployment, or Phase 7 behavior was implemented.
- No `.venv` or `venv` directory was deleted, recreated, replaced, or cleaned.
- Phase 7 was not started.
