# Version 2 Phase 1 Release Checklist

Package version: `2.0.0a1`

Release identifier: `v2.0.0-alpha.1`

Date: 2026-08-05

## Repository State

- [x] Correct starting main SHA recorded:
  `c66d9e5ae7c99eeb7ab01e00a3c3494b2da1a7b0`.
- [x] Release branch created from current main:
  `review/v2-phase-01-release-preparation`.
- [x] Working tree clean before edits.
- [x] No pre-existing `v2.0.0-alpha.1` tag.

## Implementation Acceptance

- [x] Provider decision documented.
- [x] Explicit SPY acquisition implemented.
- [x] Raw/canonical layers separated.
- [x] XNYS validation implemented.
- [x] Manifest and checksum verification implemented.
- [x] Deep semantic verification implemented.
- [x] Atomic rollback implemented.
- [x] Offline tests passed in the implementation review.
- [x] Owner-run provider smoke test passed.

## Security

- [x] No credentials tracked.
- [x] No real downloaded dataset tracked.
- [x] No account identifier tracked.
- [x] No authorization header tracked.
- [x] No unsafe serialization.
- [x] No network activity during imports or normal tests.
- [x] No `TradingClient` used by acquisition.
- [x] No order submission.
- [x] No live endpoint support.

## Versioning

- [x] `pyproject.toml` reports `2.0.0a1`.
- [x] `spy_market_agent.__version__` reports `2.0.0a1`.
- [x] Database/API/data schema versions unchanged.
- [x] No Git tag created on the review branch.

## Quality

- [x] Full Pytest suite passed: 975 tests.
- [x] Coverage at least 85%: 85.20%.
- [x] Unit tests passed: 929 tests collected and `pytest tests/unit -q` passed.
- [x] Integration tests passed: 46 tests.
- [x] Targeted Phase 1 and release-metadata tests passed: 94 tests.
- [x] FutureWarning gate passed: `pytest -W error::FutureWarning`.
- [x] Ruff passed: `ruff check .`.
- [x] Formatting passed: `ruff format --check .`.
- [x] MyPy passed: `mypy src tests`.
- [x] `git diff --check` passed.
- [x] Markdown links and tables checked by documentation tests and final diff review.

## Post-Merge Steps

These unchecked items are an operator procedure for creating the release tag after branch
approval. They are not claims that the post-merge actions have already been performed.

- [ ] Merge approved branch into `main`.
- [ ] Pull merged `main`.
- [ ] Run full verification again on `main`.
- [ ] Confirm package/runtime versions.
- [ ] Confirm clean working tree.
- [ ] Create annotated `v2.0.0-alpha.1` tag.
- [ ] Push the tag.
- [ ] Confirm the tag points to verified `main`.
- [ ] Begin Phase 2 only after tag confirmation.

Post-merge items are intentionally unchecked on this review branch.
