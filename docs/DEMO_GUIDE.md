# Demo Guide

## Local Prerequisites

- Python 3.12.
- A clean virtual environment.
- Editable dev install with `python -m pip install -e ".[dev]"`.
- No real credentials are required for a safe demo.

## Safe Setup Without Credentials

Use the committed defaults or a local `.env` derived from `.env.example`. Leave Alpaca
credentials unset. The defaults keep paper execution disabled, dry-run enabled, and the
configuration kill switch engaged.

```bash
python -m pip install -e ".[dev]"
python -c "from spy_market_agent.persistence import initialize_database; initialize_database('./spy_market_agent.sqlite3')"
test -f spy_market_agent.sqlite3 && echo "Database initialized"
```

Do not commit `spy_market_agent.sqlite3`.

## Demonstrate Validation and Modeling

Run deterministic integration tests:

```bash
pytest tests/integration/test_market_data_provider_flow.py -q
pytest tests/integration/test_phase5_modeling_flow.py -q
pytest tests/integration/test_phase6_research_flow.py -q
```

These tests use synthetic data and temporary artifacts. They demonstrate provider-boundary
validation, feature/label alignment, chronological modeling, strategy conversion, independent
risk checks, and backtest accounting without external data or broker calls.

## Demonstrate Persistence, API, and Dashboard

Run the Phase 7 integration flow:

```bash
pytest tests/integration/test_phase7_persistence_api_dashboard_flow.py -q
```

For manual API and dashboard startup against an explicitly initialized local database, use two
separate terminals. Each server command stays active until stopped. Start FastAPI before
opening Streamlit.

### Terminal A - FastAPI

```bash
SQLITE_DATABASE_PATH=./spy_market_agent.sqlite3 \
python -m uvicorn "spy_market_agent.api.main:create_app" \
  --factory \
  --host 127.0.0.1 \
  --port 8000
```

The default API address is `http://127.0.0.1:8000`.

Verify health before starting Streamlit:

```bash
curl http://127.0.0.1:8000/health
```

### Terminal B - Streamlit

```bash
DASHBOARD_API_BASE_URL=http://127.0.0.1:8000 \
streamlit run src/spy_market_agent/dashboard/streamlit_app.py \
  --server.address 127.0.0.1 \
  --server.port 8501 \
  --server.headless true \
  --browser.gatherUsageStats false
```

The dashboard normally becomes available at `http://127.0.0.1:8501` and reads from the API
address configured by `DASHBOARD_API_BASE_URL`. Headless mode avoids Streamlit's first-run
email prompt.

An empty initialized database may correctly show no persisted model runs or backtests. This
is normal unless you have populated the database through supported repository APIs. The API
and dashboard are read-only and cannot approve, submit, reconcile, cancel, replace, or change
paper orders.

## Demonstrate Paper Status Without Submitting

Run:

```bash
curl http://127.0.0.1:8000/api/v1/paper-trading/status
curl "http://127.0.0.1:8000/api/v1/paper-orders?limit=100&offset=0"
```

Expected safe defaults include paper mode, paper execution disabled, dry-run enabled, the
configuration kill switch engaged, the durable kill switch engaged, and credential-presence
booleans set to false.

## Useful Screenshots

- API `/health` response: "Read-only service is up with educational warning."
- API `/api/v1/data/status` on an empty database: "No persisted market data yet."
- Dashboard Overview empty state: "Read API connected, no persisted research artifacts."
- Dashboard Paper Trading Status: "Dual kill switches engaged, credentials absent."
- Test output for `pytest --cov-fail-under=85`: "Full verification passes with required
  coverage."

Do not commit screenshots containing private paths, account identifiers, credentials, browser
profiles, or external market data.

## Expected Empty or Unavailable Behavior

- Missing or uninitialized SQLite database: API returns sanitized service errors for artifact
  reads.
- Empty initialized SQLite database: API returns empty lists or unavailable data status.
- Dashboard cannot reach API: dashboard shows a visible unavailable state.
- Paper status with no credentials: status remains read-only and shows credential presence as
  false.

## Troubleshooting

- If Python is not 3.12, create the environment with an explicit `python3.12`.
- If imports fail, rerun `python -m pip install -e ".[dev]"`.
- If the API reports the database is unavailable, run explicit SQLite initialization.
- If the dashboard is empty, confirm the API is running and `DASHBOARD_API_BASE_URL` points to
  it.
- If tests fail on warnings, inspect whether a new project warning or dependency warning was
  introduced instead of suppressing it broadly.

## Cleanup

Remove generated local artifacts before committing:

```bash
git status --short
```

Generated SQLite files such as `spy_market_agent.sqlite3` or `demo.sqlite3`, local virtual
environments such as `.venv-test/`, coverage HTML, private screenshots, downloaded market
data, and real credential files must not be committed.
