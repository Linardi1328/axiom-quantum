# Version 2 Phase 1 — Real SPY Data Foundation Specification

Status: Accepted for v2.0.0-alpha.1 release

Target release: `v2.0.0-alpha.1`

Planned implementation branch: `review/v2-phase-01-real-spy-data`

The Phase 1 implementation and owner-run real-provider smoke test have passed. Phase 1 uses
package version `2.0.0a1` and release identifier `v2.0.0-alpha.1`. The release tag must
point only to a successfully verified `main` commit after branch approval and merge.

This document does not authorize Version 2 Phase 2 implementation. Phase 2 requires its own
approved specification.

## A. Purpose

Version 2 Phase 1 establishes a reproducible, auditable, and legally usable historical SPY
market-data foundation for future research. The intended result is a controlled data layer
that can acquire historical SPY daily data only when explicitly requested, preserve raw
source records, produce canonical validated records, and record enough lineage to reproduce
or reject a dataset later.

Phase 1 must not change models, strategies, paper execution, broker integration, risk
limits, API submission behavior, dashboard submission behavior, persisted schema versions, or
Version 1 behavior outside the authorized Phase 1 data scope.

## Versioning Contract

The planning and repository-alignment branch kept package metadata and runtime `__version__`
at `1.0.0`.

Version 2 Phase 1 must not bump the package version at the beginning of implementation. Once
all Phase 1 acceptance criteria pass, its final release-preparation branch must set:

- `pyproject.toml` package version to `2.0.0a1`
- `spy_market_agent.__version__` to `2.0.0a1`

`2.0.0a1` is the PEP 440 Python package version. `v2.0.0-alpha.1` is the corresponding Git
tag and public release identifier.

Persisted database schema versions and API route versions must not be changed merely because
the package version changes.

The tag must be created only after:

1. Phase 1 release-preparation branch is approved.
2. The release-preparation branch is merged.
3. Main passes final verification.

If Phase 1 is rejected or incomplete, no alpha tag is created.

## B. Scope

Phase 1 planning covers:

- Historical SPY daily market data.
- Explicit user-triggered acquisition.
- Raw source records.
- Canonical validated records.
- Dataset manifests.
- Cryptographic checksums.
- Data lineage.
- Trading-session validation.
- Offline deterministic fixtures.
- Reproducible refresh behavior.
- Documentation and acceptance tests.

## C. Out of Scope

Phase 1 explicitly excludes:

- Model retraining or new models.
- Hyperparameter tuning.
- Performance or profitability claims.
- Walk-forward evaluation.
- Real-time market feeds.
- Shadow operation.
- New paper-order functionality.
- Live-money execution.
- Multi-asset support.
- Automated scheduling unless separately approved.

## D. Data-Provider Decision

No provider is approved by this planning document. The final implementation must identify the
selected provider and explain why it was chosen.

Provider approval criteria:

- SPY historical coverage.
- Adjusted and unadjusted OHLCV availability.
- Corporate-action information.
- Timestamp and timezone clarity.
- Exchange-session accuracy.
- Rate limits.
- Reliability.
- Licensing and redistribution terms.
- Authentication and secret requirements.
- Reproducibility.
- Cost implications.

Provider selection should be recorded with the evaluation date, access method, major
tradeoffs, and any rejected alternatives.

## E. Licensing and Redistribution

The implementation must record provider terms and access date before data acquisition is
accepted. Documentation must explain permitted local use and any retention, redistribution,
derived-data, screenshot, or publication limits.

Required rules:

- Do not commit restricted raw market data to Git.
- Do not redistribute provider data unless explicitly permitted.
- Keep source data separate from small synthetic or legally permitted fixtures.
- Redact credentials, account identifiers, tokens, and authentication headers.
- Treat unknown licensing status as not permitted for repository inclusion.

## F. Storage Layout

Planned layout:

```text
data/
  raw/
  canonical/
  manifests/
  fixtures/
```

Expected Git behavior:

- `data/raw/` should be ignored except for placeholder files.
- `data/canonical/` should be ignored except for placeholder files.
- `data/manifests/` should be ignored unless a manifest is synthetic or explicitly permitted
  for commit.
- `data/fixtures/` may contain small deterministic fixtures only when they are synthetic or
  legally permitted.
- Generated SQLite files and downloaded external market data must remain uncommitted.

This documentation task creates no market-data files and does not create these directories.

## G. Acquisition Contract

Downloads must be explicit and user initiated. Acquisition must not occur during import,
test collection, API startup, dashboard rendering, or package initialization.

Expected inputs:

- Symbol, initially `SPY` only.
- Start date.
- End date.
- Adjustment mode.
- Provider identifier.
- Destination directory.

Expected behavior:

- No acquisition at import time.
- No network request during tests unless explicitly marked as a network test.
- No silent overwrite.
- Atomic writes where practical.
- Clear failure messages.
- Bounded retries.
- No uncontrolled infinite retry.
- Safe handling of interrupted downloads.
- Idempotent repeat execution when inputs and provider responses are unchanged.

Unsupported symbols, ambiguous date ranges, missing configuration, or unsafe destinations
must fail before any write.

## H. Raw and Canonical Data

Raw data must preserve provider output as closely as practical, including original field
names, values, metadata, retrieval timestamp, provider identifier, request parameters, and
source timezone information when available.

Canonical data must use a documented schema including at minimum:

- `symbol`
- `session_date`
- `open`
- `high`
- `low`
- `close`
- `adjusted_close` where applicable
- `volume`
- `provider`
- `adjustment_mode`
- `retrieval_timestamp`
- `source_timezone`
- `canonical_timezone`
- `lineage_identifier`

Numeric precision and null-handling expectations:

- Prices should use deterministic decimal or string-preserving serialization where practical.
- Floating-point values must be finite before canonical acceptance.
- Nulls are allowed only for fields explicitly marked optional by the schema.
- Missing required price, volume, symbol, provider, session, adjustment, or lineage fields
  must reject the row or dataset.
- Canonical serialization must be deterministic.

## I. Adjustment and Corporate-Action Policy

Phase 1 must document:

- Adjusted versus unadjusted prices.
- Dividend treatment.
- Split treatment.
- Volume adjustments.
- Prevention of mixing adjustment modes.
- Dataset identity changes when policy changes.

The implementation must not make unsupported claims about any provider's exact adjustment
methodology before provider selection. If the provider methodology is incomplete or unclear,
that limitation must be recorded in the manifest and documentation.

## J. Exchange-Session Validation

Use the project's approved exchange-calendar conventions: XNYS sessions and
`America/New_York` as the market timezone, with UTC for full timestamps when required.

Validation must cover:

- Expected XNYS sessions.
- Weekends.
- Exchange holidays.
- Missing sessions.
- Duplicate sessions.
- Out-of-order records.
- Incomplete final sessions.
- Timezone conversion.
- Unexpected future-dated records.
- Zero or negative prices.
- Negative volume.
- OHLC consistency.

Weekends and exchange holidays are non-sessions, not missing observations.

## K. Dataset Manifests and Checksums

Each canonical dataset must have a manifest containing:

- Dataset identifier.
- Symbol.
- Provider.
- Requested range.
- Actual range.
- Retrieval timestamp.
- Adjustment mode.
- Schema version.
- Row count.
- Missing-session summary.
- Corporate-action policy.
- Source checksum.
- Canonical checksum.
- Tool/package version.
- Relevant configuration.
- Lineage references.

Use a strong deterministic checksum such as SHA-256. Source and canonical checksums must be
computed from deterministic serialization. Changes in row order, numeric representation,
schema version, adjustment mode, provider identity, or canonical field values must change the
appropriate checksum.

## L. Dataset Versioning

Material data changes create a new dataset identity. Material changes include:

- Provider changes.
- Requested or actual date-range changes.
- Schema changes.
- Adjustment-policy changes.
- Corporate-action-policy changes.
- Canonicalization logic changes.
- Source record changes.
- Missing-session or correction changes.

Dataset schema versions are distinct from the package version. Updating the package version
does not by itself change a dataset identity, and changing a dataset identity does not by
itself require a package version change.

## M. Offline Fixtures

Phase 1 must include small deterministic fixtures that:

- Are legal to commit.
- Do not contain credentials or restricted bulk market data.
- Cover normal records.
- Cover duplicates.
- Cover missing sessions.
- Cover malformed OHLC values.
- Cover invalid volume.
- Cover timezone and session boundaries.
- Permit the complete automated suite to run offline.

Synthetic fixtures should be preferred when licensing is uncertain. Fixtures must be visibly
marked as synthetic or legally permitted.

## N. Failure Handling

Expected explicit failures include:

- Missing credentials.
- Authentication failure.
- Provider outage.
- Rate limiting.
- Partial response.
- Malformed response.
- Empty date range.
- Unsupported symbol.
- Checksum mismatch.
- Manifest mismatch.
- Storage permission failure.
- Interrupted atomic write.

Failures must fail closed. The implementation must not silently accept damaged, incomplete,
unlicensed, out-of-session, malformed, or checksum-mismatched data.

## O. Security

Required security rules:

- Use environment-variable credentials or approved local secret-management mechanisms.
- Do not put secrets in logs, manifests, fixtures, exceptions, screenshots, review files, or
  Git.
- Redact authentication headers.
- Do not use unsafe pickle or arbitrary-code deserialization.
- Use safe path handling.
- Do not write outside approved data directories.
- Perform no network activity during module import.
- Keep provider credentials separate from broker credentials.

## P. Testing Plan

Required unit tests:

- Schema validation.
- Date-range validation.
- Session validation.
- OHLC consistency.
- Adjustment-mode isolation.
- Manifest construction.
- Deterministic checksums.
- Atomic-write behavior.
- Failure mapping.
- Secret redaction.

Required integration tests:

- Provider response to raw storage.
- Raw data to canonical data.
- Canonical data to manifest and checksum.
- Repeated acquisition producing identical canonical output when inputs are unchanged.
- Offline fixture flow.
- Corrupted-data rejection.
- Import/startup side-effect protection.

All normal automated tests must remain network independent. Any provider-network test must be
explicitly marked, skipped by default, and documented with credential, cost, and licensing
requirements.

## Q. Acceptance Criteria

Phase 1 may be approved only when:

- The provider decision is documented.
- Licensing restrictions are documented.
- Historical SPY acquisition is explicit.
- Raw and canonical layers are separate.
- Canonical schema is validated.
- XNYS sessions are validated.
- Adjustment policy is documented.
- Manifests and SHA-256 checksums are deterministic.
- Data lineage is reproducible.
- Restricted datasets are not committed.
- Offline fixtures exist.
- Tests pass without network access.
- Existing Version 1 behavior remains unchanged.
- Coverage remains at least 85%.
- Ruff, formatting, and MyPy pass.
- No live-trading capability is introduced.
- No model-performance claim is made.

## R. Planned Implementation Deliverables

Implemented Phase 1 deliverables:

- Provider adapter.
- Explicit acquisition command.
- Raw and canonical schema models.
- Validators.
- Manifest and checksum services.
- Storage layer.
- Offline fixtures.
- Unit and integration tests.
- Phase review report.
- Updated documentation.
- Package and release metadata for `2.0.0a1` as the final release-preparation commit after
  Phase 1 acceptance criteria pass and before merge.
- Git tag `v2.0.0-alpha.1` only after merge and successful verification on `main`.

The package and release metadata for `2.0.0a1` are prepared by the release-preparation
branch after Phase 1 acceptance criteria and the owner-run smoke test passed. The Git tag
`v2.0.0-alpha.1` is created only after merge and successful verification on `main`.

## S. Approval Boundary

Version 2 Phase 1 implementation is accepted for the `v2.0.0-alpha.1` release. The release
tag must point only to a successfully verified `main` commit after review approval and
merge.

This specification does not authorize Version 2 Phase 2. Phase 2 requires a separate
approved specification and review branch.
