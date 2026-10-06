# Version 2.0.0 Alpha 1 Release Notes

Release identity:

- Package: `2.0.0a1`
- Git release identifier: `v2.0.0-alpha.1`
- Date: 2026-08-05

Release policy: the `v2.0.0-alpha.1` tag must point only to a successfully verified `main`
commit after review approval and merge.

## Purpose

Version 2 Phase 1 establishes a reproducible, auditable historical SPY daily-data foundation
for future research. It adds explicit acquisition, local ignored storage, deterministic
canonicalization, manifests, checksums, and deep offline verification.

This release does not evaluate model accuracy, does not claim profitability, does not add
real-time operation, and does not add live-money execution.

## Main Capabilities

- Explicit Alpaca historical SPY daily acquisition through a CLI command.
- SPY-only, `1Day`-only acquisition request validation.
- Separate market-data credentials:
  `ALPACA_MARKET_DATA_API_KEY` and `ALPACA_MARKET_DATA_SECRET_KEY`.
- Sanitized raw JSON snapshots.
- Canonical validated CSV daily bars.
- Deterministic dataset manifests.
- SHA-256 source, canonical content, artifact, and manifest self-checksums.
- Deterministic dataset identity derived from stable content and policy inputs.
- XNYS session and OHLCV validation.
- Safe local storage under ignored `data/raw/`, `data/canonical/`, and `data/manifests/`.
- Atomic multi-artifact write behavior with rollback for newly created partial artifacts.
- Deep offline dataset verification.
- Synthetic fixtures and offline tests.

## Provider Decision

Phase 1 uses the Alpaca Market Data API through the installed official `alpaca-py` package.
The adapter isolates the low-level stock-bars page request because the public
`get_stock_bars(StockBarsRequest)` method in the installed SDK merges pages and does not
preserve page-level pagination metadata required by the raw snapshot audit.

Known provider limitations:

- The owner-run smoke test confirmed only a narrow IEX range.
- The project does not prove SPY coverage back to inception.
- SIP availability may depend on subscription.
- Corporate-action evidence is limited to the documented provider adjustment policy.
- Alpaca provider data must remain local unless redistribution is explicitly permitted.

## Owner-Run Smoke-Test Evidence

Codex did not execute the real-provider request. The repository owner reported a controlled
Alpaca smoke test on 2026-08-05:

- Provider: Alpaca Market Data API.
- Symbol: SPY.
- Timeframe: `1Day`.
- Feed: `iex`.
- Adjustment mode: `all`.
- Requested range: 2024-01-02 through 2024-01-05.
- Actual session range: 2024-01-02..2024-01-05.
- Row count: 4.
- Acquisition exit code: 0.
- Deep offline verification: passed.
- `git status --short` after acquisition: no output.
- Generated raw, canonical, and manifest artifacts remained in ignored local data
  directories.
- No trading request or order submission was performed.

No API keys, secret keys, authorization headers, account IDs, raw provider payloads,
screenshots, or generated dataset files are reproduced in these notes.

## Setup and Verification

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
pytest --cov-fail-under=85
pytest tests/unit -q
pytest tests/integration -q
pytest -W error::FutureWarning
ruff check .
ruff format --check .
mypy src tests
git diff --check
python -c "import importlib.metadata as m; import spy_market_agent; print(m.version('spy-market-agent'), spy_market_agent.__version__)"
```

Expected version output:

```text
2.0.0a1 2.0.0a1
```

Release-preparation verification on the review branch passed with:

- 975 full-suite tests.
- 929 unit tests collected and the unit suite passing.
- 46 integration tests.
- 94 targeted Phase 1 and release-metadata tests.
- 85.20% branch-aware coverage.
- Passing FutureWarning, Ruff, formatting, MyPy, and `git diff --check` gates.

Release-preparation verification results are recorded in
`reviews/V2_PHASE_01_REVIEW.md` and `VERSION_2_PHASE_01_RELEASE_CHECKLIST.md`.

## Security and Data Boundaries

- Credentials are read from environment variables only.
- Credentials must not be placed in command arguments, logs, manifests, fixtures, review
  files, screenshots, or Git.
- Provider data is local-only and ignored by Git.
- Normal automated tests remain offline and do not contact Alpaca.
- Imports, API startup, dashboard startup, and CLI help do not acquire data.
- The acquisition flow does not construct `TradingClient`.
- The release adds no API write route, dashboard submission control, paper-order behavior, or
  live-money execution path.

## Reproducibility

Artifact identity is based on deterministic raw snapshot serialization, canonical CSV
content, manifest fields, schema versions, provider/feed/adjustment policy, checksums, and
dataset identity. The package version is recorded in manifests for lineage, but dataset
identity is not derived solely from the package version.

## Known Limitations

- No real SPY dataset is distributed with the repository.
- No historical benchmark is included.
- No model retraining, walk-forward evaluation, or model-performance claim is included.
- No real-time feed, scheduler, shadow mode, or production paper operation is included.
- No live-money trading is supported or approved.
- No assets other than SPY are supported by Phase 1.
- Exact dependency resolution can differ because no lock file is committed.

## Next Milestone

Next planned milestone:

- V2 Phase 2 - Real Historical Benchmark.
- Target: `v2.0.0-alpha.2`.

Phase 2 has not begun and requires a separate approved specification.
