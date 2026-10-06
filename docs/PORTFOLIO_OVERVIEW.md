# Portfolio Overview

## Problem Statement

Financial research systems often mix data handling, modeling, backtesting, risk checks, and
execution in ways that create lookahead bias or unsafe broker access. This project implements
a constrained SPY-only Version 1 workflow that keeps those responsibilities separate and
auditable.

## Engineering Objectives

- Use a typed Python 3.12 `src` layout.
- Preserve chronological market-data handling.
- Keep model evaluation separate from strategy and execution.
- Enforce independent risk controls.
- Persist enough metadata for audit replay.
- Expose results through read-only presentation layers.
- Add paper-execution preparation without live trading or automatic submission.

## Architecture

The system is layered:

1. provider protocol and canonical SPY data validation
2. trailing feature engineering and forward labels
3. chronological splits and model diagnostics
4. fixed strategy signals
5. independent risk management
6. backtest accounting
7. SQLite persistence
8. read-only API and dashboard
9. explicit paper-only execution service and isolated Alpaca adapter

## Important Safety Decisions

- Version 1 supports only SPY daily long-or-cash research.
- Models cannot communicate with brokers.
- Risk checks do not accept model confidence as an override.
- API and dashboard routes are read-only.
- Paper execution requires explicit service calls, dual kill switches, human approval, and
  deterministic fingerprints.
- Live trading, scheduling, automation, and deployment are out of scope.

## Data-Leakage Prevention

Feature engineering uses trailing windows only. Labels use the approved timeline: information
through close of `t`, entry no earlier than open of `t + 1`, exit at open of `t + 6`, and a
positive target only after estimated costs. Future return and target columns are excluded from
model features.

Chronological splits preserve time order, apply gap-aware boundaries, and keep the final test
partition out of model selection.

## Model Evaluation Design

Version 1 trains deterministic logistic-regression and gradient-boosting candidates. Selection
uses validation metrics only. The selected model is refit on train plus validation and then
evaluated on the final test partition without changing parameters, preprocessing, or the fixed
diagnostic threshold.

Classification metrics are diagnostics only and are not evidence of profitability.

## Backtesting Design

The backtest is long-or-cash, starts with USD 10,000 simulated cash, maps signals to the next
validated open, uses whole shares, applies configurable commission and slippage, records all
orders and risk decisions, and rejects unsafe orders without changing state.

Backtest artifacts include source market data, execution-price lineage, fills, portfolio rows,
metrics, schema versions, checksums, and runtime metadata.

## Persistence and Audit Replay

SQLite persistence is explicit. Load paths reconstruct validated domain objects instead of
trusting stored rows. Revalidation covers checksums, schema versions, feature columns, model
lineage, risk decisions, fills, accounting identities, metrics, and paper-execution ledger
state.

## Paper-Execution Safeguards

The paper-execution layer is broker-independent until the isolated Alpaca paper adapter is
explicitly constructed. It enforces paper endpoint identity, human approval binding, dual kill
switches, regular market hours, execution-time risk evaluation, unique IDs, same-session SPY
reservation, no automatic retry, and lookup-only reconciliation.

## Testing Strategy

Tests cover configuration safety, market-data validation, feature leakage, labels, splits,
model training/evaluation/selection, strategy signals, risk rules, backtest accounting,
persistence reconstruction, API route boundaries, dashboard rendering, paper-execution gates,
and documentation consistency. The full suite must pass `pytest --cov-fail-under=85`, Ruff,
format checking, and MyPy.

## Technology Stack

- Python 3.12
- Pydantic and Pydantic Settings
- pandas
- exchange-calendars
- scikit-learn
- SQLite
- FastAPI
- Streamlit
- httpx
- alpaca-py for the isolated paper adapter
- Pytest, pytest-cov, Ruff, and MyPy

## Key Challenges Solved

- Preserving feature/label temporal alignment.
- Separating validation, modeling, strategy, risk, and execution boundaries.
- Reconstructing persisted artifacts through validation instead of trusting storage.
- Preventing broker access from model, API, dashboard, and import paths.
- Handling uncertain paper submissions without automatic resubmission.
- Preventing same-session duplicate SPY paper attempts even with different IDs.

## Current Limitations

- No real SPY dataset or market-data downloader is committed.
- Backtests use historical adjusted daily bars and simplified cost assumptions.
- Paper fills can differ from backtest assumptions.
- No authentication, deployment, scheduler, worker, live trading, or multi-asset support
  exists.
- No order cancellation or replacement is implemented.
- No dependency lock file is committed.

## Future Work

Future work is not implemented in Version 1. Possible later directions include a reviewed
market-data downloader, richer documentation examples, model artifact persistence, more
operational dashboards, or deployment packaging. Any such work must preserve the Version 1
safety boundaries unless the project specification is explicitly updated and approved.
