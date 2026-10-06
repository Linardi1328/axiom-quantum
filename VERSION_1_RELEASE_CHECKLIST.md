# Version 1 Release Checklist

All checked items below are release-candidate evidence for branch
`review/version-1-release-candidate`.

- [x] Python 3.12: `python --version` reports Python 3.12 in the project environment.
- [x] Installation: `python -m pip install -e ".[dev]"` passes.
- [x] Full tests: `pytest --cov-fail-under=85` passes.
- [x] Unit tests: `pytest tests/unit -q` passes.
- [x] Integration tests: `pytest tests/integration -q` passes.
- [x] Coverage >=85%: full-suite coverage is `85.34%`, satisfying the 85% gate.
- [x] Warnings controlled: `pytest -W error::FutureWarning` passes and Pytest treats
  unexpected warnings as errors through `pyproject.toml`.
- [x] Ruff: `ruff check .` passes.
- [x] Formatting: `ruff format --check .` passes.
- [x] MyPy: `mypy src tests` passes.
- [x] Import safety: existing tests confirm package/API/dashboard imports do not load
  settings, create files, initialize SQLite, construct broker clients, or submit orders.
- [x] Secret scan: repository audit found no real-looking Alpaca credentials, authorization
  headers, account identifiers, private screenshots, generated databases, or generated
  coverage artifacts staged for commit.
- [x] Read-only API/dashboard: route inventory contains only GET application routes, and
  dashboard tests confirm no approve/submit controls.
- [x] Paper-only execution: Alpaca integration is isolated to the paper adapter and constructs
  `TradingClient(..., paper=True)` only when explicitly instantiated.
- [x] Live-mode rejection: settings and execution tests reject `live` mode and non-paper broker
  environments.
- [x] No automatic submission: tests confirm imports, startup, API GETs, dashboard rendering,
  dry-run defaults, kill switches, and blocked preflights do not submit paper orders.
- [x] Duplicate and concurrency controls: execution repository and service tests cover unique
  signal/client-order/approval IDs and same-symbol/session reservation protection.
- [x] Documentation completeness: README, architecture, reproducibility, workflows,
  security/safety, demo, portfolio, changelog, release notes, checklist, and review files are
  present.
- [x] Reproducibility: docs and persisted artifacts record Python/dependency lineage,
  deterministic seeds, schema versions, checksums, split specifications, and explicit SQLite
  initialization.
- [x] Known limitations: release notes and README state no real SPY dataset, no downloader, no
  live trading, no scheduler, no automatic submission, no API/dashboard execution controls,
  simplified backtest assumptions, and no profitability claims.
- [x] Clean Git diff: `git diff --check` passes.
- [x] No generated artifacts committed: generated SQLite files, coverage XML, `htmlcov`,
  private screenshots, credentials, and external market data are absent from the staged
  release-candidate diff.
