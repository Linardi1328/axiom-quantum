# Agent Instructions

These instructions are permanent guardrails for future Codex tasks in this repository.

## Required Reading Order

Before making changes, read the relevant repository guidance in this order:

1. `AGENTS.md`
2. `PROJECT_SPEC.md` - frozen Version 1 implementation baseline
3. `FUTURE_ROADMAP.md` - approved long-term planning sequence
4. The active phase specification - binding implementation authority for the current
   approved phase

`PROJECT_SPEC.md` remains the authoritative record for Version 1 scope, architecture,
safety requirements, and development sequence. `FUTURE_ROADMAP.md` is planning
documentation; it is not automatic permission to implement every stage. Each future
development phase requires an approved phase specification, and Codex must implement only the
currently authorized phase.

Version 2 Phase 1 is accepted under `docs/V2_PHASE_01_REAL_SPY_DATA_SPEC.md`.
Version 2 Phase 2 is accepted and released as `v2.0.0-alpha.2` under
`docs/V2_PHASE_02_REAL_HISTORICAL_BENCHMARK_SPEC.md`.
Version 2 Phase 3 PR #24 merged the approved walk-forward framework and initial research
scaffolding. PR #25 merged the manual, offline, classification-first development research
implementation. Owner development testing completed, and Version 2 Phase 3 is accepted and
released as `v2.0.0-alpha.3`. The Phase 3 scientific outcome was
`NO CANDIDATE PROMOTION`; no model is authorized for protected evaluation, shadow mode,
paper research, production paper operation, or live trading. Phase 3 development research
must derive and apply the Phase 2 final-test prediction-session exclusion boundary before
building labels from the accepted Phase 1 parent dataset.
Version 2 Phase 4 is accepted and released as `v2.0.0-beta.1` under
`docs/V2_PHASE_04_REAL_TIME_SHADOW_MODE_SPEC.md`. PR #27 merged the approved
specification and infrastructure-first scaffold. PR #28 merged Observation-Only Operational
Pipeline V1. PR #29 merged Scheduled Observation Operations V1. PR #30 merged Beta 1
release preparation, and the public `v2.0.0-beta.1` tag points to
`1c8feae478c0f5536b2193eeb408e580f3f7e33c`. Gate B model-connected shadow inference
remains locked because no approved shadow model exists.
Version 2 Phase 5 is active under
`docs/V2_PHASE_05_PRODUCTION_PAPER_OPERATION_SPEC.md`. The current authorized substage is
specification plus non-submitting safety/recovery scaffold on
`review/v2-phase-05-production-paper`. Gate P5-A infrastructure entry is authorized. Gate
P5-B broker paper submission by the Phase 5 workflow is blocked pending separate owner
authorization. Gate P5-C model-connected paper operation is
`BLOCKED_NO_APPROVED_PAPER_MODEL` because Phase 3 produced `NO CANDIDATE PROMOTION`.
The target future release is `v2.0.0-beta.2`, the package/runtime version remains
`2.0.0b1`, no beta.2 tag exists, protected evaluation remains unexecuted, no approved paper
model exists, unattended scheduling remains unauthorized, and live trading remains
prohibited.
Real-data-provider selection belongs to Version 2 Phase 1, not to an old Version 1 phase.

If a user request conflicts with `PROJECT_SPEC.md`, `FUTURE_ROADMAP.md`, or the active phase
specification, explain the conflict and ask for explicit approval before changing the
architecture, safety posture, or scope.

## Branch and Review Discipline

- Do not modify `main` directly.
- Every implementation, documentation, or maintenance change must occur on a review or
  maintenance branch.
- Branch work must be reviewed before merge.
- Do not push directly to `main`.
- Do not create release tags unless the repository owner explicitly asks for that tag.

## Engineering Standards

- Use Python 3.12.
- Use type hints for Python code.
- Prefer small, modular, reviewable changes.
- Keep implementation files aligned with the approved package boundaries.
- Add or update tests for every implementation feature.
- Clearly document assumptions and limitations.
- Ask for explicit approval before introducing a new major dependency.
- Ask for explicit approval before changing the agreed architecture.
- Version 2 or roadmap work must not change Version 1 runtime behavior unless the active
  approved phase specification explicitly authorizes that change.

## Trading Safety Rules

- Never introduce live-money trading support.
- Live-money execution remains prohibited.
- Separate execution mode from execution permission.
- `EXECUTION_MODE` may only be `paper`.
- Any request for `live` execution must raise `RuntimeError`.
- `ENABLE_PAPER_EXECUTION` must default to `false`.
- `DRY_RUN` must default to `true`.
- Starting the application must not automatically submit paper orders.
- Paper-order submission must require deliberate configuration and explicit approval.
- Paper execution already exists in Version 1 and must remain paper-only, explicitly invoked,
  and isolated from models, API write paths, dashboard controls, and imports.
- Never permit short selling in Version 1.
- Never permit leverage in Version 1.
- Never permit assets other than SPY in Version 1.
- Never permit more than one open SPY position in Version 1.
- Never let models bypass the risk engine.
- No model output may override risk limits.
- The prediction model must not communicate directly with a broker or execution adapter.
- Every proposed trade must pass through an independent risk-management layer.
- The application must fail safely by refusing to trade when required information is missing
  or uncertain.
- Paper execution requires unique signal identifiers and unique client-order identifiers.
- Paper execution must reject duplicate orders and stale signals.
- Paper execution must include configuration and durable kill switches.
- Paper execution must verify broker account type and paper endpoint before order submission.

## Data and Modeling Rules

- Never randomly shuffle financial time-series observations.
- All model evaluation must preserve chronological order.
- Use a gap of at least five trading observations between training and validation and between
  validation and final testing.
- Apply the same minimum gap in time-series cross-validation.
- Keep the final test period untouched until feature selection, model selection, probability
  calibration, and signal-threshold selection are complete.
- Never use centered rolling windows.
- Never use backward filling that introduces future information.
- Feature engineering must not use future observations.
- The Version 1 target is: using information available through the close of trading day `t`,
  predict whether entering SPY at the open of `t + 1` and exiting at the open of `t + 6`
  produces a positive return after estimated round-trip transaction costs and slippage.
- Features may use data only through the close of day `t`.
- Prediction is generated after day `t` is complete.
- Entry may occur no earlier than the open of `t + 1`.
- Exit for the five-day target occurs at the open of `t + 6`.
- The target is `1` only when the executable return after estimated costs is greater than
  zero.
- Future return and target columns must never appear in model features.
- Backtests must include configurable transaction costs and estimated slippage.
- Maintain explicit alignment between raw data, features, labels, predictions, signals, and
  fills.
- Preserve the original vendor response as an immutable raw snapshot when data acquisition is
  implemented under an approved phase.
- Use one consistently adjusted OHLCV series for features, labels, backtesting, and benchmark
  calculations.
- Never mix raw and adjusted fields within one calculation.
- Record the adjustment policy in dataset and model metadata.
- Use the NYSE trading calendar and treat `America/New_York` as the market timezone.
- Store timestamps in UTC when full timestamps are required.
- Daily bars must correspond to valid exchange sessions.
- Weekends and exchange holidays must not be treated as missing observations.
- Reject duplicate trading-session dates.
- Exclude incomplete current-session candles.

## Data Lineage Rules

- Record data lineage and model-run metadata when available, including provider, download
  timestamp, symbol, timeframe, adjustment policy, first and last trading session, row count,
  dataset checksum, feature-schema version, label definition, Git commit hash, Python version,
  and dependency versions.
- Do not commit restricted market data, generated SQLite databases, credentials, private
  screenshots, or generated coverage artifacts.

## Security Rules

- Never hard-code API keys, account IDs, secrets, or credentials.
- Never expose credentials in logs, test output, API responses, dashboard views, manifests,
  fixtures, review files, or committed files.
- Use environment variables or approved local secret-management mechanisms for credentials.
- Commit only placeholder examples for environment configuration.
- Redact authentication headers and account identifiers in errors and documentation.
- Do not use unsafe pickle or arbitrary-code deserialization for market-data artifacts.

## Testing Rules

- Do not remove tests merely to obtain a passing build.
- Do not weaken assertions merely to obtain a passing build.
- Add regression tests for safety-critical fixes.
- Prefer deterministic fixtures for market-data, feature, model, backtest, and risk tests.
- Normal automated tests must remain network independent unless a future approved phase
  explicitly marks a network test separately.

Canonical verification commands:

```bash
python -m pip install -e ".[dev]"
pytest --cov-fail-under=85
pytest tests/unit -q
pytest tests/integration -q
pytest -W error::FutureWarning
ruff check .
ruff format --check .
mypy src tests
git diff --check
git status --short
```

If a command cannot be run, document the reason clearly in the final response.

## Documentation Rules

- Keep assumptions and limitations visible in user-facing documentation.
- Do not claim or imply profitability.
- Do not present model outputs, backtests, or dashboard results as investment advice.
- When adding or changing architecture, update the active approved specification after
  explicit approval.
- Keep `PROJECT_SPEC.md` focused on the frozen Version 1 baseline unless explicitly approved.
- Keep `FUTURE_ROADMAP.md` focused on planning and stage gates, not implementation claims.
