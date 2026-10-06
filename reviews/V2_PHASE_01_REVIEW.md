# Version 2 Phase 1 Review

Starting Phase 1 implementation main SHA:
`3f5e3612bbf78b44845f3dedc24da35185a171f7`

Hardened implementation commit:
`050364e` (`fix: harden version 2 data foundation`)

Phase 1 implementation merge commit:
`c66d9e5ae7c99eeb7ab01e00a3c3494b2da1a7b0`

Release-preparation branch:
`review/v2-phase-01-release-preparation`

Release-preparation commit:
reported externally in the final Codex response after commit.

Package target:
`2.0.0a1`

Public Git identifier:
`v2.0.0-alpha.1`

Phase status:
Phase 1 accepted; release metadata verified on the release-preparation branch.

## Scope Accepted

Version 2 Phase 1 implements a historical SPY daily-data foundation:

- Explicit acquisition request validation.
- Separate market-data credentials.
- Alpaca historical market-data adapter.
- Raw sanitized provider snapshot storage.
- Canonical SPY daily CSV storage.
- XNYS session validation.
- Deterministic dataset manifests.
- SHA-256 source, canonical content, artifact, and manifest self-checksums.
- Deterministic dataset identity.
- Safe repository-relative storage and atomic writes.
- Multi-artifact rollback when later writes fail.
- Deep offline dataset verification.
- Synthetic offline fixtures and tests.

## Hardening Accepted

The hardened implementation added these release-blocking corrections before the
real-provider smoke test:

- `MARKET_DATA_TIMEOUT_SECONDS` reaches the actual Alpaca SDK HTTP request boundary through
  a timeout-enforcing `requests.Session` wrapper installed only on the explicit market-data
  SDK client.
- Retry count and request timeout remain separate settings.
- The provider access path remains a small isolated adapter around
  `StockHistoricalDataClient.get(path="/stocks/bars", data=...)`.
- The documented public SDK method `get_stock_bars(StockBarsRequest)` was reviewed but not
  used for durable raw-page storage because the installed SDK merges paginated pages and
  does not preserve page-level `next_page_token` metadata.
- Alpaca's installed SDK docstring states that `raw_data` is not implemented, so Phase 1
  does not rely on that flag as a public guarantee.
- Acquisition captures one timestamp at operation start and reuses it for snapshot
  timestamping, incomplete-session validation, manifest reasoning, and lineage.
- Multi-artifact writes remove files newly created by the failed attempt while preserving
  valid matching artifacts that existed before the attempt.
- Offline verification reconstructs manifest, raw snapshot, canonical bars, checksums,
  dataset identity, filenames, generated paths, row counts, session ranges, lineage, and
  XNYS validation before accepting a dataset.

## Provider Decision

Selected provider:
Alpaca Market Data API.

SDK and access method:

- `alpaca-py`
- `StockHistoricalDataClient`
- `/v2/stocks/bars`
- explicit `feed`
- explicit `adjustment`
- ascending chronological sorting
- pagination token handling
- request-boundary timeout wrapper

Provider decision record:
`docs/V2_PHASE_01_PROVIDER_DECISION.md`.

## Owner-Run Smoke-Test Evidence

Codex did not execute a real-provider request and did not receive credentials, account
identifiers, authorization headers, raw provider payloads, screenshots, or generated dataset
files.

The owner executed the controlled real Alpaca market-data smoke test on 2026-08-05.
Owner-reported evidence:

- Alpaca authentication succeeded.
- Provider: Alpaca Market Data API.
- Symbol: SPY.
- Timeframe: `1Day`.
- Feed: `iex`.
- Adjustment mode: `all`.
- Requested range: 2024-01-02 through 2024-01-05.
- Actual session range: 2024-01-02..2024-01-05.
- Four valid XNYS sessions were returned.
- Acquisition exit code: 0.
- Deep offline verification passed.
- `git status --short` after acquisition had no output.
- Generated raw, canonical, and manifest artifacts remained in ignored local data
  directories.
- No trading endpoint was contacted.
- No order was submitted.
- No credential was recorded in Git, documentation, or this review report.

## Provider Limitations

- The owner-run smoke test confirmed only a narrow 2024-01-02 through 2024-01-05 IEX range.
- Official support documentation does not prove SPY inception coverage.
- Alpaca support documentation says data is not available further back than 2016 and notes
  some missing early data points.
- SIP access can be subscription-limited.
- Alpaca API data must not be redistributed.
- Corporate-action evidence remains limited to the provider adjustment policy; no separate
  corporate-action snapshot is acquired.

## Storage Formats

Raw snapshot:

- UTF-8 JSON.
- Sorted keys.
- Compact separators.
- No NaN or Infinity.
- Terminal newline.
- Sanitized request metadata, provider identity, raw bar pages, pagination metadata, and
  retrieval timestamp.
- No credentials or authorization headers.

Canonical data:

- UTF-8 CSV.
- Fixed column order.
- ISO session dates.
- Deterministic decimal text.
- Integer-compatible volume.
- Ascending XNYS sessions.
- Terminal newline.

Manifest:

- UTF-8 deterministic JSON.
- Dataset identity, provider identity, requested and actual ranges, row counts, session
  summary, checksums, lineage, dependency versions, licensing classification, and generated
  relative file locations.

## Checksum Definitions

- Source checksum: SHA-256 over stable sanitized raw provider content, request parameters,
  provider identity, pagination metadata, and corporate-action evidence; retrieval timestamp
  is excluded.
- Canonical content checksum: SHA-256 over canonical rows, schema version, provider, feed,
  timeframe, adjustment mode, and corporate-action policy; local paths and derived lineage
  identifiers are excluded.
- Artifact checksum: SHA-256 over complete written raw or canonical artifact bytes.
- Manifest self-checksum: SHA-256 over the manifest with `manifest_artifact_checksum`
  unset.

## Dataset Identity

Dataset IDs are deterministic and derived from:

- symbol
- provider
- feed
- timeframe
- adjustment mode
- requested date range
- canonical schema version
- canonical content checksum
- corporate-action policy identifier

They are not derived from wall-clock time, random UUIDs, local absolute paths, usernames, or
package version.

## Security Controls

- Market-data credentials are separate from paper-trading credentials.
- Credentials are not accepted in CLI arguments.
- Provider exceptions are redacted.
- Imports, `--help`, invalid CLI input, missing acknowledgement, and missing credentials do
  not construct an Alpaca client.
- API and dashboard startup do not acquire market data.
- Acquisition never constructs `TradingClient`.
- Storage rejects absolute roots, `..` traversal, source/doc/test/Git directories, symlink
  artifact paths, existing conflicts, and checksum mismatches.
- Generated provider data is ignored under `data/raw/`, `data/canonical/`, and
  `data/manifests/`.

## Test Categories

- Request validation.
- Credential separation.
- Settings redaction.
- Provider parameter mapping.
- SDK contract and timeout transport behavior.
- Pagination.
- Bounded retry behavior.
- Error mapping and redaction.
- Canonical schema conversion.
- Numeric normalization.
- XNYS session validation.
- Incomplete-session rejection.
- Adjustment-mode isolation.
- Manifest construction.
- Deterministic serialization and checksums.
- Dataset identity.
- Safe paths and symlink rejection.
- Atomic write and idempotent existing-data behavior.
- Multi-artifact rollback after later write failures.
- Existing conflict rejection.
- CLI help, invalid-input, missing-acknowledgement, and missing-credential behavior.
- Fake-provider integration flow.
- Corrupted artifact rejection.
- Deep verification tamper rejection after manifest self-checksum recomputation.
- Import/startup side-effect protection.
- Release metadata and documentation consistency.

## Remaining Acceptance Items

Completed:

- Owner review of the implementation branch.
- Owner-run controlled real-provider smoke test with market-data credentials only.
- Confirmation of account-specific Alpaca authentication for the narrow IEX smoke-test
  range.
- Release-preparation branch sets package/runtime version to `2.0.0a1`.

Operator actions after branch approval:

- Review and approval of the release-preparation branch.
- Merge into `main`.
- Full verification on merged `main`.
- Creation of `v2.0.0-alpha.1` only after merged-main verification passes.

## Verification Results

Release-preparation verification on 2026-08-05:

- Fresh Python 3.12 virtual environment: created with `python3.12 -m venv
  .venv-test`.
- Editable development install: `python -m pip install -e ".[dev]"` passed.
- Full test suite: `pytest --cov-fail-under=85` passed with 975 tests.
- Coverage: 85.20%, above the 85% gate.
- Unit tests: `pytest tests/unit -q` passed; 929 unit tests collected.
- Integration tests: `pytest tests/integration -q` passed with 46 tests.
- Targeted Phase 1/release-metadata tests: 94 tests passed.
- FutureWarning gate: `pytest -W error::FutureWarning` passed.
- Ruff: `ruff check .` passed.
- Formatting: `ruff format --check .` passed.
- MyPy: `mypy src tests` passed.
- Whitespace diff check: `git diff --check` passed.
- Package metadata/runtime version: `2.0.0a1 2.0.0a1`.
- `v2.0.0-alpha.1` tag remained absent on the review branch.
- Codex did not run a real Alpaca/provider network request.
- Owner-run smoke-test evidence is the only accepted real-provider evidence.
- No real provider data, credentials, account identifiers, authorization headers,
  generated SQLite database, coverage output, or private screenshots were committed.

## Explicit Confirmations

- No model changed.
- No model was trained.
- No benchmark was run.
- No API write route was added.
- No dashboard execution control was added.
- No paper-order behavior changed.
- No trading client was contacted by Codex.
- No live support was added.
- No real market dataset was committed.
- No secret was committed.
- Package version is prepared as `2.0.0a1`.
- No Git tag was created.
