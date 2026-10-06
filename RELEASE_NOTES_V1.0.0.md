# Release Notes: Version 1.0.0

## Release Purpose

Version 1.0.0 is the final reviewed release candidate for the educational
`spy-market-agent` Version 1 system. It demonstrates a constrained SPY daily research
workflow with leakage-aware modeling, risk-controlled backtesting, SQLite audit persistence,
read-only presentation surfaces, and explicitly invoked paper-only execution preparation.

This release is not investment advice and is not real-money trading infrastructure.

## Implemented Capabilities

- SPY-only daily OHLCV data contracts, validation, NYSE session checks, and checksums.
- Leakage-safe trailing features and the approved open `t + 1` to open `t + 6` label.
- Chronological train, validation, and final-test model evaluation.
- Deterministic logistic-regression and gradient-boosting baselines.
- Validation-only model selection and locked final-test diagnostics.
- Long-or-cash signal policy with next-open execution mapping.
- Independent risk evaluation before every backtest fill.
- Risk-controlled backtests with transaction costs, slippage, cash, shares, fills, orders,
  equity curves, drawdown, turnover, and metrics.
- Explicit SQLite initialization and persistence with typed reconstruction and revalidation.
- Read-only FastAPI routes for persisted data, model evaluations, backtests, and local
  paper-execution status.
- Read-only Streamlit dashboard backed by the API client.
- Explicit paper-only Alpaca preparation through a broker-independent service and isolated
  adapter.

## Safety Guarantees

- No live trading support exists.
- `EXECUTION_MODE` may only be `paper`; live mode is rejected.
- Application imports, FastAPI GET routes, and dashboard rendering do not create broker
  clients or submit orders.
- API and dashboard surfaces are read-only and contain no approve, submit, reconcile,
  cancel, replace, or kill-switch controls.
- Paper execution requires deliberate configuration, dry-run disabled, dual kill switches
  disengaged, paper credentials, a paper endpoint, market-hours checks, execution-time risk
  approval, unique IDs, and a matching human approval bound to the instruction fingerprint.
- Duplicate signal IDs, client-order IDs, approval IDs, and same-symbol/session paper
  reservations are rejected.
- Submission uncertainty is recorded as `submission_unknown` and never retried
  automatically. Reconciliation is lookup-only by `client_order_id`.

## Setup And Verification

Use Python 3.12:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Release-candidate verification:

```bash
pytest --cov-fail-under=85
pytest tests/unit -q
pytest tests/integration -q
pytest -W error::FutureWarning
ruff check .
ruff format --check .
mypy src tests
```

The final release-candidate coverage gate reached `85.34%`, above the required 85% minimum.

## Expected Defaults

- `EXECUTION_MODE=paper`
- `ENABLE_PAPER_EXECUTION=false`
- `DRY_RUN=true`
- `PAPER_EXECUTION_KILL_SWITCH=true`
- durable SQLite paper-execution kill switch engaged by default
- `PAPER_EXECUTION_REQUIRE_MARKET_OPEN=true`
- `DASHBOARD_API_BASE_URL=http://127.0.0.1:8000`
- default SQLite path for API reads: `./spy_market_agent.sqlite3`

These defaults cannot submit a paper order.

## Known Limitations

- No real SPY dataset is committed.
- No market-data downloader is implemented.
- No dependency lock file is committed, so exact transitive dependency versions can differ.
- Backtests use adjusted daily bars and simplified transaction-cost and slippage assumptions.
- Classification metrics and backtest metrics are diagnostics only.
- Paper fills can differ from backtest assumptions and from live-market behavior.
- No authentication, deployment hardening, scheduler, worker, or production database exists.
- No order cancellation, replacement, stops, limits, brackets, OCO, or OTO exists.

## Paper-Trading Warning

Alpaca integration is paper-only and explicitly invoked. Paper trading can still create
orders in a simulated broker account when all gates are deliberately opened from code. Do not
use real credentials in committed files, logs, screenshots, or review records.

## No-Live-Trading Statement

Version 1.0.0 does not support live trading. It has no live-mode configuration path, no live
Alpaca endpoint support, no API write route, no dashboard execution control, no scheduler, and
no automatic broker communication.

## Migration Notes

- Package metadata and `spy_market_agent.__version__` are now `1.0.0`.
- Persisted data schema versions, model schema versions, strategy schema versions, risk
  schema versions, backtest schema versions, and paper-execution schema versions were not
  changed for the package version bump.
- Existing local SQLite files should still be validated by the current explicit schema checks.
- Generated SQLite databases, coverage output, private screenshots, and external market data
  should remain uncommitted.

## Warning Policy

Pytest treats unexpected warnings as errors. The only allowed warning filters are exact
third-party dependency warnings documented in `pyproject.toml`. Broad suppressions are not
part of the Version 1 release state.

## Future Work Excluded

Version 1.0.0 does not implement Version 2 roadmap work. Excluded work includes market-data
downloaders, real-time feeds, model artifact persistence, authentication, deployment,
scheduling, automation, live trading, multi-asset support, and dashboard/API execution
controls.
