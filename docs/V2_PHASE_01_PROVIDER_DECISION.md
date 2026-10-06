# Version 2 Phase 1 Provider Decision

Review date: 2026-08-05

This document is a technical provider-selection record, not legal advice. The implementation
uses Alpaca only for explicitly invoked historical market-data acquisition. It does not use
Alpaca trading endpoints, does not submit orders, and does not enable live-money trading.

## Official Sources Reviewed

- Alpaca Historical bars API reference:
  <https://docs.alpaca.markets/us/reference/stockbars>
- Alpaca Historical API overview:
  <https://docs.alpaca.markets/us/docs/historical-api>
- Alpaca Market Data FAQ:
  <https://docs.alpaca.markets/us/docs/market-data-faq>
- Alpaca Corporate actions API reference:
  <https://docs.alpaca.markets/us/reference/corporateactions-1>
- Alpaca SDKs and Tools:
  <https://docs.alpaca.markets/us/docs/sdks-and-tools>
- Alpaca redistribution support article:
  <https://alpaca.markets/support/redistribute-alpaca-api>
- Alpaca data timeline support article:
  <https://alpaca.markets/support/alpaca-data-timeline>
- Alpaca data provider support article:
  <https://alpaca.markets/support/data-provider-alpaca>

## Decision

Selected provisional Phase 1 provider: Alpaca Market Data API.

SDK and access method:

- Python SDK: `alpaca-py`, already present in the repository dependencies.
- Client: `alpaca.data.historical.StockHistoricalDataClient`.
- Endpoint path used by the adapter: `/v2/stocks/bars`.
- Base service documented by Alpaca for historical data: `https://data.alpaca.markets/v2`.

The documented public SDK method is
`StockHistoricalDataClient.get_stock_bars(StockBarsRequest)`. The implementation inspects
and tests the installed `alpaca-py==0.43.5` contract, but the public method delegates to the
SDK's paginator and returns merged bar data rather than the exact raw response pages and
`next_page_token` values needed for Phase 1 raw snapshot auditing. Alpaca's installed SDK
docstring also states that `raw_data` is not implemented. For that reason, Phase 1 isolates a
small page adapter around the SDK client's lower-level `get(path="/stocks/bars", data=...)`
request boundary. This is not documented as a stable public Alpaca SDK guarantee; it is a
small, tested compatibility layer for the pinned installed SDK version.

Request timeout:

- The adapter installs a timeout-enforcing `requests.Session` wrapper on the SDK client used
  for this explicit acquisition path.
- `MARKET_DATA_TIMEOUT_SECONDS` is passed to the actual SDK HTTP request as the per-request
  timeout.
- Retry count and request timeout remain separate settings.
- Timeout exceptions are redacted and mapped to `ProviderTimeoutFailure`.

Why Alpaca was selected:

- The repository already depends on `alpaca-py`, so no new major dependency is required.
- The official historical bars endpoint supports daily stock bars, explicit `feed`,
  explicit `adjustment`, ascending sort, and pagination.
- Using the official SDK keeps provider-specific behavior isolated from research, modeling,
  backtesting, API, dashboard, and execution packages.
- Existing paper execution already uses Alpaca in an isolated paper-only path, but Phase 1
  uses separate market-data credential settings and does not reuse paper-trading settings.

## Provider Contract Summary

Authentication:

- Regular-user market-data requests use Alpaca API key and secret headers.
- This project reads them only from `ALPACA_MARKET_DATA_API_KEY` and
  `ALPACA_MARKET_DATA_SECRET_KEY`.
- The acquisition CLI never accepts credentials as command-line arguments.

Historical SPY coverage:

- Alpaca support documentation says the Data API does not have data further back than 2016
  and notes a few missing data points toward the beginning.
- This implementation does not claim SPY inception coverage and does not claim that any
  specific crisis period is available until an authorized provider query proves it.

Daily-bar availability:

- The stock bars endpoint supports `1Day` bars.
- Alpaca documents that daily bars are aggregated from trades and that the daily timestamp is
  based on the New York trading day.

Feed behavior:

- Alpaca documents `sip` and `iex` stock feeds for historical endpoints.
- `sip` is the default project setting, but the request must still be explicit.
- SIP access can be subscription-limited, especially for recent data.

Adjustment modes:

- Alpaca documents `raw`, `split`, `dividend`, `spin-off`, and `all` adjustments, with
  combinations possible.
- Phase 1 supports only `raw` and `all` as isolated acquisition modes.
- `all` is recorded as the provider value for the project’s all-adjusted mode.

Corporate actions:

- Alpaca documents a corporate-actions endpoint with supported action types and a warning
  that corporate-action availability can be delayed.
- This implementation records the provider adjustment policy and the limitation in the
  manifest. It does not acquire a separate corporate-action snapshot because the current
  Phase 1 acceptance path does not require mixing corporate actions into provider-adjusted
  bars.

Pagination:

- Alpaca documents `next_page_token` and warns that callers should check it for additional
  pages.
- The adapter requests ascending order, follows all pages, and rejects repeated pagination
  tokens.

Rate limits and reliability:

- Alpaca documents HTTP 429 for rate limits and recommends checking rate-limit headers.
- The adapter applies bounded retries for timeouts, connection-like failures, 429 responses,
  and selected server-side failures. It does not retry authentication, authorization,
  malformed data, invalid requests, checksum mismatch, or validation failures.
- Retry backoff does not use the request timeout value as sleep duration. A stalled provider
  request is bounded at the request transport boundary before retry policy is considered.

Cost and subscription implications:

- Basic access may be limited by feed and recency.
- SIP data can require a paid subscription depending on the requested data.
- Optional real-provider smoke testing must be owner-run with explicit credentials and a
  narrow historical range.

## Owner-Run Smoke-Test Evidence

The repository owner reported a controlled Alpaca Market Data API smoke test on 2026-08-05.
Codex did not execute the request and did not receive credentials, account identifiers,
authorization headers, raw provider payloads, screenshots, or generated dataset files.

Owner-reported evidence:

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
- Git status after acquisition: clean.
- Generated raw, canonical, and manifest artifacts remained in ignored local data
  directories.
- No trading request or order submission was performed.

## Licensing and Redistribution

Alpaca’s support article states that Alpaca API data cannot be redistributed. The technical
project rule is therefore:

- Restricted provider data is local-only.
- Downloaded raw snapshots, canonical datasets, and real-data manifests must not be committed
  to Git.
- `data/raw/`, `data/canonical/`, and `data/manifests/` are ignored except for `.gitkeep`.
- Only small synthetic fixtures may be committed under `data/fixtures/`.
- Provider terms, access date, source, and licensing classification are recorded in
  documentation and manifests.

## Alternatives Considered

- `yfinance`: rejected because the Phase 1 instruction forbids adding it and it is not an
  official Alpaca access path.
- Direct HTTP through `requests`: rejected because the repository already has the official
  SDK and the instruction forbids adding or using a separate requests-based provider path.
- New paid data vendor: rejected for this phase because no owner-approved provider contract
  was supplied and adding a major dependency/provider would exceed the current scope.
- Existing synthetic fixtures only: insufficient because Phase 1 must establish a real
  provider acquisition foundation, even though normal tests remain offline.

## Known Phase 2 Limitations

- The owner-run smoke test confirmed only a narrow 2024-01-02 through 2024-01-05 IEX range.
  Broader real historical coverage, missing early bars, and subscription limits remain Phase
  2 research inputs.
- The implementation does not prove that SPY data covers pre-2016 regimes.
- The implementation does not run a historical benchmark, model retraining, or performance
  evaluation.
- Corporate-action evidence is limited to the provider adjustment policy until separate
  provider access and licensing are reviewed.
- Licensing conclusions should be reviewed by the owner before any publication,
  redistribution, screenshots, or report sharing that includes provider data.
