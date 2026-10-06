# Risk-Controlled SPY Market Intelligence and Paper-Trading System

## Project Overview

This project is a portfolio-quality Python system for researching SPY market signals, evaluating machine-learning models, backtesting long-or-cash strategies, and later submitting simulated orders through a paper-trading account.

The system is educational and experimental. It must never claim, imply, or guarantee profitability. Its purpose is to demonstrate careful engineering, chronological market-data handling, model evaluation discipline, risk controls, and safe separation between research, decisioning, and execution.

Version 2 status is tracked through `FUTURE_ROADMAP.md` and approved phase
specifications rather than by changing this frozen Version 1 baseline. The current
package/runtime version is `2.0.0b1`; Version 2 Phase 4 is accepted and released as
`v2.0.0-beta.1`, and Version 2 Phase 5 specification plus non-submitting safety/recovery
scaffold is active for target future release `v2.0.0-beta.2`. Phase 5 infrastructure entry
is authorized, but Phase 5 broker submission remains blocked pending separate owner
authorization, no approved paper model exists, protected evaluation has not been executed,
unattended scheduling is not authorized, and live trading remains prohibited.

## Problem Statement

Market-intelligence projects often fail by mixing research, prediction, backtesting, and execution in ways that introduce lookahead bias, unrealistic fills, uncontrolled risk, or unsafe broker access. This project addresses that problem by building a constrained, auditable workflow for SPY daily data:

1. Collect and validate daily OHLCV data.
2. Engineer features using only information available at the time.
3. Train and evaluate models with chronological splits.
4. Convert model outputs into proposed long-or-cash signals.
5. Backtest those signals with realistic execution timing, transaction costs, and slippage.
6. Pass all proposed trades through an independent risk-management layer.
7. Submit only explicitly approved paper-trading orders after risk approval through an isolated paper-only execution layer.

## Intended Users

- Developers evaluating a portfolio project that demonstrates Python, data engineering, machine learning, FastAPI, Streamlit, testing, and safety-aware trading-system design.
- Students or researchers learning about financial time-series modeling, backtesting constraints, and paper-trading workflows.
- The project owner, who will use the system as an educational research environment rather than an investment advisory tool.

This project is not intended for live-money trading, investment advice, or automated production trading.

## Project Objectives

- Build a clean, typed Python 3.12 codebase with small, testable modules.
- Restrict Version 1 to SPY, daily OHLCV data, and long-or-cash positions.
- Implement leakage-resistant feature engineering and chronological model evaluation.
- Establish logistic regression as the first baseline model.
- Add gradient boosting as a comparison model.
- Provide historical backtesting with configurable transaction costs and slippage.
- Enforce risk limits in a dedicated risk-management layer.
- Persist data and run metadata in SQLite.
- Expose research and backtest results through a FastAPI backend.
- Provide an interactive Streamlit dashboard.
- Provide explicitly invoked Alpaca paper-only order submission while explicitly prohibiting live-money trading and automatic order submission.
- Maintain a test suite with Pytest, formatting and linting with Ruff, and static type checking with MyPy.

## Version 1 Scope

- Python 3.12.
- SPY ETF only.
- Daily OHLCV market data.
- Long-or-cash positions only.
- No short selling.
- No leverage.
- Historical backtesting.
- Initial simulated capital of USD 10,000.
- Logistic regression as the first machine-learning baseline.
- Gradient boosting as a comparison model.
- FastAPI backend.
- Streamlit dashboard.
- SQLite database.
- Pytest test suite.
- Ruff linting and formatting.
- MyPy static type checking.
- Explicitly invoked Alpaca paper-trading preparation for SPY whole-share market DAY orders only.

## Explicit Non-Goals

- No live-money trading.
- No support for any asset other than SPY in Version 1.
- No intraday, tick, options, futures, crypto, or multi-asset data in Version 1.
- No short selling.
- No leverage or margin.
- No portfolio optimization across multiple instruments.
- No claims of profitability or investment suitability.
- No broker communication from prediction-model modules.
- No discretionary override that allows model outputs to bypass risk limits.
- No random train-test split for market data.
- No centered rolling-window features.
- No backward filling that introduces future information.
- No signal execution on the same candle that generated the signal.
- No hard-coded credentials or API keys.
- No automatic paper-order submission when the application starts.

## Proposed Architecture

The system should use a layered architecture with strict separation between research, decisioning, risk checks, execution, persistence, API, and dashboard concerns.

```text
Data Source
    |
    v
Market Data Ingestion
    |
    v
Data Validation
    |
    v
Feature Engineering
    |
    v
Strategy and Model Signal Generation
    |
    v
Risk Management
    |
    v
Backtesting Engine or Paper Execution Adapter
    |
    v
SQLite Persistence
    |
    +--> FastAPI Backend
    |
    +--> Streamlit Dashboard
    |
    v
Monitoring and Reports
```

Core design rules:

- Market data flows forward in chronological order.
- Feature engineering may only use observations available at or before the feature timestamp.
- A signal generated from candle `t` may execute no earlier than candle `t + 1`.
- Models produce predictions or scores only; they do not place orders.
- Strategies convert predictions into proposed trades only; they do not execute trades directly.
- Risk management independently validates every proposed trade.
- Execution modules can only submit risk-approved paper orders when paper execution is deliberately enabled, dry-run mode is deliberately disabled, and explicit approval has been supplied.
- Missing, stale, invalid, or uncertain required information must cause the system to refuse trading.

## Data Flow

1. Load configuration from environment variables and typed settings objects.
2. Ingest SPY daily OHLCV data through a market-data provider interface.
3. Preserve the original vendor response as an immutable raw snapshot.
4. Validate schema, NYSE trading-session order, missing values, duplicate trading-session dates, price sanity, and volume sanity.
5. Build one consistently adjusted OHLCV series for features, labels, backtesting, and benchmark calculations.
6. Store raw snapshots, adjusted datasets, dataset metadata, and validation results in SQLite.
7. Generate features from historical observations available only through the close of trading day `t`.
8. Generate the Version 1 label: whether entering SPY at the open of trading day `t + 1` and exiting at the open of trading day `t + 6` produces a positive return after estimated round-trip transaction costs and slippage.
9. Split data chronologically into train, validation, and final test periods with at least five trading observations of gap between adjacent periods.
10. Train logistic regression and gradient boosting models.
11. Evaluate models using time-aware metrics and store results.
12. Convert model outputs into proposed long-or-cash signals.
13. Shift signals so entry occurs no earlier than the open of trading day `t + 1`.
14. Run proposed trades through risk management.
15. Backtest approved trades with initial capital, cash accounting, transaction costs, and slippage.
16. Persist backtest results, orders, positions, trades, metrics, lineage, and run metadata.
17. Serve results through FastAPI and Streamlit.
18. In Phase 8, submit only explicitly approved, risk-approved simulated orders to Alpaca paper trading through the isolated paper-only adapter.

## Market-Data Provider and Adjusted-Price Policy

- The data provider may remain undecided through Phase 3.
- All market-data access must use a provider interface so vendor-specific behavior remains isolated.
- Preserve the original vendor response as an immutable raw snapshot.
- Use one consistently adjusted OHLCV series for features, labels, backtesting, and benchmark calculations.
- Never mix raw and adjusted fields within one calculation.
- Record the adjustment policy in dataset and model metadata.
- Clearly document that adjusted-price backtesting approximates return distributions and corporate-action effects; it does not guarantee executable historical fills.

## Exchange Calendar and Timezone Rules

- Use the NYSE trading calendar.
- Treat `America/New_York` as the market timezone.
- Store timestamps in UTC when full timestamps are required.
- Daily bars must correspond to valid exchange sessions.
- Weekends and exchange holidays must not be treated as missing observations.
- Reject duplicate trading-session dates.
- Exclude incomplete current-session candles.

## Data Lineage and Metadata Requirements

Processed datasets and model runs should record the following when available:

- Data provider.
- Download timestamp.
- Symbol.
- Timeframe.
- Adjustment policy.
- First and last trading session.
- Row count.
- Dataset checksum.
- Feature-schema version.
- Label definition.
- Git commit hash.
- Python version.
- Dependency versions.

## Module Responsibilities

### Configuration

- Load and validate runtime settings.
- Use environment variables for secrets and external service settings.
- Separate execution mode from execution permission.
- Permit `EXECUTION_MODE` to be only `paper`.
- Raise `RuntimeError` for any request for `live` execution.
- Default `ENABLE_PAPER_EXECUTION` to `false`.
- Default `DRY_RUN` to `true`.
- Ensure application startup never automatically submits paper orders.
- Require deliberate configuration and explicit approval before paper-order submission.
- Provide typed configuration objects.

### Market Data

- Fetch or load SPY daily OHLCV data.
- Access market data only through a provider interface.
- Keep provider-specific behavior isolated in provider adapters.
- Normalize columns and timestamp handling.
- Ensure all data is explicitly tied to SPY in Version 1.
- Preserve immutable raw vendor snapshots.
- Produce one consistently adjusted OHLCV series for downstream calculations.
- Avoid embedding vendor-specific assumptions outside adapter modules.

### Data Validation

- Validate required columns: date, open, high, low, close, adjusted close when available, and volume.
- Validate chronological ordering.
- Validate that daily bars correspond to valid NYSE trading sessions.
- Treat weekends and NYSE holidays as non-sessions, not missing observations.
- Detect duplicate trading-session dates.
- Exclude incomplete current-session candles.
- Detect impossible OHLC values, non-positive prices, and invalid volumes.
- Fail fast on missing data needed for modeling, backtesting, or trading decisions.

### Feature Engineering

- Compute time-series features using only present and past observations.
- Use trailing windows only.
- Avoid centered windows.
- Avoid backward filling that could introduce future information.
- Preserve feature-to-label alignment explicitly.
- Ensure future return and target columns never appear in model features.
- Document each feature's economic or statistical intuition.

### Strategies

- Convert model predictions or rule-based signals into proposed long-or-cash target positions.
- Apply signal lagging so a signal generated from candle `t` executes no earlier than candle `t + 1`.
- Never submit trades directly to any broker or execution adapter.

### Machine-Learning Models

- Provide logistic regression as the first baseline.
- Provide gradient boosting as a comparison model.
- Train only on chronologically valid training data.
- Evaluate on later chronological periods.
- Never randomly shuffle financial time-series observations.
- Use at least five trading observations of gap between training and validation and between validation and final testing.
- Apply the same minimum gap in time-series cross-validation.
- Keep the final test period untouched until feature selection, model selection, probability calibration, and signal-threshold selection are complete.
- Never communicate directly with broker or execution modules.

### Backtesting

- Simulate long-or-cash trading with initial capital of USD 10,000.
- Include configurable transaction costs.
- Include configurable estimated slippage.
- Account for cash, position size, fills, equity curve, drawdown, and turnover.
- Execute entries no earlier than the open of trading day `t + 1` for predictions generated after the close of day `t`.
- Use one consistently adjusted OHLCV series for features, labels, backtesting, and benchmarks.
- Persist input parameters, assumptions, and results for reproducibility.

### Risk Management

- Independently evaluate every proposed trade before execution or backtesting.
- Enforce no short selling.
- Enforce no leverage.
- Enforce a maximum of one open SPY position in Version 1.
- Enforce maximum position and available-cash constraints.
- Refuse trades when required price, cash, position, timestamp, or configuration information is missing or uncertain.
- Ensure no model output can override risk limits.

### Paper Execution

- Support Alpaca paper trading only through the isolated Phase 8 adapter.
- `EXECUTION_MODE` may only be `paper`.
- Raise `RuntimeError` for any request for `live` execution.
- Default `ENABLE_PAPER_EXECUTION` to `false`.
- Default `DRY_RUN` to `true`.
- Default the durable paper-execution kill switch to engaged.
- Starting the application must not automatically submit paper orders.
- Paper-order submission must require deliberate configuration and explicit approval.
- Refuse live-money endpoints, live account configuration, or any non-paper execution mode.
- Accept only immutable paper-order instructions tied to an approved `ProposedOrder` and `RiskDecision`.
- Require a matching human approval bound to the exact deterministic instruction fingerprint.
- Re-evaluate the order through the independent risk engine immediately before submission.
- Require unique signal identifiers.
- Require unique client-order identifiers.
- Require unique approval identifiers.
- Reject duplicate orders.
- Reject stale signals.
- Provide a global kill switch.
- Verify broker account type before order submission.
- Verify paper endpoint before order submission.
- Verify broker clock, account, account configuration, SPY asset, existing positions, and open orders before order submission.
- Permit at most one open SPY position in Version 1.
- Submit only SPY whole-share market DAY orders with `extended_hours=False`.
- Treat timeouts or uncertain submission outcomes as unresolved local audit states and never automatically resubmit.
- Provide explicit read-only reconciliation by `client_order_id`.
- Keep FastAPI routes and dashboard views read-only; they may inspect local execution status but must not approve, submit, cancel, replace, reconcile, or change the kill switch.

### Database Persistence

- Use SQLite for local persistence.
- Store market data, features metadata, model runs, evaluations, backtest runs, orders, trades, positions, equity curves, risk decisions, and configuration snapshots.
- Avoid storing secrets.
- Use migrations or explicit schema-management scripts once persistence is implemented.

### Monitoring

- Track data freshness, validation results, model-run metadata, backtest summaries, risk rejections, and paper-trading status.
- Avoid logging credentials.
- Prefer structured, redacted logs.

### FastAPI

- Expose read-oriented endpoints for data status, model results, backtest results, risk settings, and system health.
- Avoid placing training, backtesting, or broker logic directly in API route handlers.
- Require explicit service-layer calls for state-changing operations.

### Streamlit

- Provide an interactive dashboard for data quality, model evaluation, backtest results, risk settings, and paper-trading status.
- Present educational warnings and limitations.
- Never present outputs as financial advice or profitability guarantees.

### Automated Tests

- Cover configuration safety behavior, data validation, leakage-sensitive feature alignment, chronological splitting, signal lagging, backtest accounting, risk rejections, and API behavior.
- Tests must not be removed or weakened merely to obtain a passing build.

## Machine-Learning Objective

The Version 1 supervised-learning task is exactly:

Using information available through the close of trading day `t`, predict whether entering SPY at the open of trading day `t + 1` and exiting at the open of trading day `t + 6` produces a positive return after estimated round-trip transaction costs and slippage.

Target construction rules:

- Features may use data only through the close of day `t`.
- Prediction is generated after day `t` is complete.
- Entry occurs no earlier than the open of `t + 1`.
- Exit for the five-day target occurs at the open of `t + 6`.
- The target is `1` only when the executable return after estimated costs is greater than zero.
- Future return and target columns must never appear in model features.
- The label definition must be recorded in dataset and model metadata.

Model evaluation must preserve chronological order. Candidate metrics may include accuracy, precision, recall, F1, ROC AUC, calibration diagnostics, confusion matrix, directional hit rate, and strategy-level metrics from backtesting. Classification metrics must not be treated as evidence of profitability.

## Chronological Splitting Requirements

- Preserve chronological order.
- Never randomly shuffle observations.
- Use a gap of at least five trading observations between training and validation and between validation and final testing.
- Apply the same minimum gap in time-series cross-validation.
- Keep the final test period untouched until feature selection, model selection, probability calibration, and signal-threshold selection are complete.

## Backtesting Requirements

- Use daily SPY OHLCV data only.
- Start with USD 10,000 simulated cash.
- Support long-or-cash positions only.
- Apply transaction costs on each trade.
- Apply configurable slippage assumptions.
- Use realistic execution timing: predictions generated after the close of day `t` may enter no earlier than the open of `t + 1`.
- For the Version 1 target, model the five-trading-day exit at the open of `t + 6`.
- Track cash, shares, market value, equity, returns, drawdown, orders, fills, and rejected trades.
- Ensure the backtest engine cannot enter short positions or use leverage.
- Keep backtest configuration and outputs reproducible.
- Report results with clear caveats and no profitability guarantees.

## Risk-Management Requirements

- Risk management must be independent from model and strategy modules.
- Every proposed trade must include enough information for risk validation.
- Required information should include symbol, side, target quantity or target weight, timestamp, estimated price, current cash, current position, and relevant risk configuration.
- Missing or uncertain required information must cause refusal to trade.
- Version 1 must reject:
  - Any symbol other than SPY.
  - Short positions.
  - Leverage.
  - More than one open SPY position.
  - Orders that exceed available simulated cash after costs and slippage.
  - Orders not produced through the approved signal and risk workflow.
  - Any attempt to bypass risk limits using model confidence or model score.

## Paper-Trading Restrictions

- Live-money trading must not be supported.
- Execution mode and execution permission are separate controls.
- `EXECUTION_MODE` may only be `paper`.
- Any request for `live` execution must raise `RuntimeError`.
- `ENABLE_PAPER_EXECUTION` must default to `false`.
- `DRY_RUN` must default to `true`.
- Starting the application must not automatically submit paper orders.
- Paper-order submission must require deliberate configuration and explicit approval.
- Alpaca integration is isolated to the Phase 8 paper-only adapter.
- Paper execution adapters must never be imported by model modules.
- Paper execution must accept only risk-approved order instructions.
- Paper execution must use deterministic instruction fingerprints and matching explicit approvals.
- Paper execution must reject duplicate orders and stale signals.
- Paper execution must require unique signal identifiers and unique client-order identifiers.
- Paper execution must include a global kill switch.
- Paper execution must verify broker account type and paper endpoint before order submission.
- Paper execution must permit at most one open SPY position in Version 1.
- Credentials must be read only from secure runtime configuration sources and never hard-coded.
- Credentials must never be logged, committed, or displayed in the dashboard.
- If account type, endpoint, credentials, clock, market status, order state, or risk approval cannot be verified, the system must refuse to trade.

## Testing Strategy

Use Pytest for automated tests, Ruff for linting and formatting, and MyPy for static type checking.

Test categories:

- Unit tests for configuration, validation, features, splitting, models, backtesting, risk, and persistence.
- Integration tests for end-to-end research and backtest flows using small fixture datasets.
- API tests for FastAPI endpoints.
- Dashboard smoke tests where practical.
- Regression tests for safety invariants.

Required safety tests:

- Requests for `live` execution raise `RuntimeError`.
- Live-trading configuration is rejected.
- `ENABLE_PAPER_EXECUTION` defaults to `false`.
- `DRY_RUN` defaults to `true`.
- Application startup does not submit paper orders.
- Paper-order submission requires deliberate configuration and explicit approval.
- Random train-test splitting is not used for market data.
- Feature rows do not use future observations.
- Future return and target columns are excluded from model features.
- Centered rolling windows are not used.
- Backward filling does not introduce future information.
- Predictions generated after the close of day `t` may enter no earlier than the open of `t + 1`.
- Chronological splits include at least five trading observations of gap between train, validation, and final test periods.
- Time-series cross-validation applies the same minimum gap.
- Final test data remains untouched until feature selection, model selection, probability calibration, and threshold selection are complete.
- Daily bars map to valid NYSE trading sessions.
- Duplicate trading-session dates are rejected.
- Incomplete current-session candles are excluded.
- Short selling and leverage are rejected.
- More than one open SPY position is rejected.
- Duplicate paper orders and stale paper signals are rejected.
- Model output cannot bypass risk management.
- Missing required trade information causes refusal to trade.

## Security Requirements

- Never hard-code API keys, account IDs, secrets, or credentials.
- Never commit secrets.
- Never log credentials.
- Use environment variables or local secret-management mechanisms for sensitive settings.
- Provide `.env.example` style documentation later, with placeholder values only.
- Redact sensitive values in errors, logs, API responses, and dashboard views.
- Keep broker integration isolated from model and research modules.
- Fail safely when security-sensitive configuration is missing or inconsistent.

## Reproducibility Requirements

- Pin or lock dependencies once the project is scaffolded.
- Record Python version and tool versions.
- Persist model parameters, feature configuration, split dates, backtest settings, transaction cost assumptions, slippage assumptions, and run timestamps.
- Persist dataset lineage when available: data provider, download timestamp, symbol, timeframe, adjustment policy, first and last trading session, row count, dataset checksum, feature-schema version, label definition, Git commit hash, Python version, and dependency versions.
- Use deterministic random seeds where applicable.
- Preserve chronological data splits.
- Store enough metadata to reproduce a model evaluation or backtest run from the same input data.
- Document external data-source limitations, adjustment behavior, and refresh dates.

## Expected Repository Structure

No implementation files should be created during Phase 1. The proposed Version 1 structure is:

```text
spy-market-agent/
  PROJECT_SPEC.md
  AGENTS.md
  README.md
  pyproject.toml
  .env.example
  src/
    spy_market_agent/
      __init__.py
      config/
        __init__.py
        settings.py
      market_data/
        __init__.py
        providers.py
        schema.py
      validation/
        __init__.py
        market_data_checks.py
      features/
        __init__.py
        technical.py
        labels.py
      strategies/
        __init__.py
        signal_policy.py
      models/
        __init__.py
        baseline_logistic.py
        gradient_boosting.py
        evaluation.py
        splits.py
      backtesting/
        __init__.py
        engine.py
        costs.py
        metrics.py
      risk/
        __init__.py
        rules.py
        decisions.py
      execution/
        __init__.py
        paper.py
        alpaca_paper.py
      persistence/
        __init__.py
        database.py
        repositories.py
      monitoring/
        __init__.py
        logging.py
        health.py
      api/
        __init__.py
        main.py
        routes.py
        schemas.py
      dashboard/
        __init__.py
        streamlit_app.py
  tests/
    unit/
      test_config.py
      test_validation.py
      test_features.py
      test_splits.py
      test_backtesting.py
      test_risk.py
    integration/
      test_research_flow.py
      test_api.py
    fixtures/
      spy_daily_sample.csv
```

## Development Phases

### Phase 1: Specification and Agent Instructions

- Create `PROJECT_SPEC.md`.
- Create `AGENTS.md`.
- Do not create implementation files.
- Review and approve the architecture before scaffolding.

### Phase 2: Project Scaffold and Tooling

- Add package structure.
- Add `pyproject.toml`.
- Configure Python 3.12, Pytest, Ruff, and MyPy.
- Add initial README and `.env.example`.
- Add minimal import and tooling tests.

### Phase 3: Configuration, Data Schema, and Validation

- Implement typed settings.
- Enforce execution-mode, paper-permission, and dry-run safety defaults.
- Define SPY daily OHLCV schema.
- Add validation logic and fixture tests.

### Phase 4: Feature Engineering and Chronological Splits

- Implement leakage-safe trailing features.
- Implement label generation.
- Implement chronological train, validation, and test splitting.
- Add tests for alignment and leakage prevention.

### Phase 5: Machine-Learning Baselines

- Implement logistic regression baseline.
- Implement gradient boosting comparison.
- Add model evaluation and run metadata.
- Add tests using small deterministic datasets.

### Phase 6: Strategy and Backtesting

- Convert model outputs into long-or-cash target positions.
- Enforce next-candle execution.
- Implement transaction costs and slippage.
- Track portfolio accounting and metrics.
- Add risk-controlled backtest tests.

### Phase 7: Persistence, API, and Dashboard

- Add SQLite persistence.
- Add FastAPI read endpoints.
- Add Streamlit dashboard views.
- Add integration and smoke tests.

### Phase 8: Paper-Trading Preparation

- Add broker-independent paper execution interfaces.
- Add immutable paper-order instructions, matching approvals, deterministic fingerprints, and durable duplicate protection.
- Add a persistent global kill switch that defaults to engaged.
- Add the Alpaca paper adapter only after explicit approval.
- Enforce paper-only endpoint restrictions.
- Add read-only API and dashboard status views for local paper-execution state.
- Add tests proving live trading cannot be configured, imports/startup do not construct broker clients, and timeout paths cannot resubmit automatically.

### Phase 9: Documentation, Polish, and Portfolio Readiness

- Rewrite README for the completed Version 1 implementation and limitations.
- Add architecture, reproducibility, workflow, security/safety, demo, and portfolio overview documentation.
- Add a Version 1 changelog section and release-readiness documentation.
- Add documentation consistency tests for repository links, routes, module paths, and safety claims.
- Review security, execution, API, dashboard, warning, and coverage invariants without broadening Version 1 scope.

## Definition of Done

Version 1 is done when:

- The project runs on Python 3.12.
- Pytest passes.
- Ruff passes.
- MyPy passes.
- SPY daily OHLCV data can be loaded, validated, and persisted.
- Features are generated without future leakage.
- Models are trained and evaluated with chronological splits.
- Logistic regression and gradient boosting results are available.
- Backtests run with USD 10,000 initial simulated capital.
- Transaction costs and slippage are configurable and applied.
- Predictions generated after the close of day `t` enter no earlier than the open of `t + 1`.
- Risk management approves or rejects every proposed trade.
- Short selling, leverage, non-SPY symbols, and live trading are rejected.
- FastAPI exposes core read endpoints.
- Streamlit displays data status, model evaluation, backtest results, and risk state.
- No credentials are committed, logged, or displayed.
- Documentation clearly states assumptions, limitations, and educational purpose.

## Known Risks and Limitations

- Historical backtests can overstate real-world performance because of data quality issues, survivorship assumptions, slippage assumptions, and unmodeled execution details.
- SPY-only daily data is intentionally narrow and may not generalize to other assets or timeframes.
- Logistic regression and gradient boosting can overfit noisy market data.
- Classification metrics may not translate into profitable or robust trading behavior.
- Transaction cost and slippage estimates are approximations.
- Data vendor adjustments, missing rows, and corporate-action handling must be understood and documented.
- Paper-trading fills may differ materially from live-market fills.
- API or dashboard results may be misinterpreted as advice unless educational warnings are visible.
- The project may require careful dependency management to keep the portfolio build reproducible.
- Any future broker integration increases security and safety complexity.
