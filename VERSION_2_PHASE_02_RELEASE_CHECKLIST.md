# Version 2 Phase 2 Release Checklist

Package version: `2.0.0a2`

Release identifier: `v2.0.0-alpha.2`

Date: 2026-08-09

## Repository State

- [x] Correct starting main SHA recorded: `1155c3c`.
- [x] Release branch created from current main:
  `review/v2-phase-02-release-preparation`.
- [x] Implementation PR #20 merged.
- [x] Working tree clean before release-preparation edits.
- [x] No pre-existing `v2.0.0-alpha.2` tag.

## Engineering Acceptance

- [x] Version 2 Phase 2 benchmark implementation merged.
- [x] Primary benchmark used SPY, Alpaca, SIP, `1Day`, adjustment `all`.
- [x] Phase 1 dataset verification passed.
- [x] Dataset ID recorded: `spy-v2p1-825930b0a2bcab20c733b867`.
- [x] Canonical checksum recorded:
  `d1c62194a3e13a164bbe09edad8cb6b4aa8bbd17a621d34da17e3e8edc96a259`.
- [x] Benchmark ID recorded: `spy-v2p2-a065593e952e6a9d96f4be86`.
- [x] Chronological split recorded: 1381 training rows, 295 validation rows, 297 final-test
  rows.
- [x] Validation-only model selection completed.
- [x] Final-test lock completed before final-test access.
- [x] Owner final-test acknowledgement recorded.
- [x] One controlled final-test execution completed.
- [x] Full completed benchmark verification passed.
- [x] Runtime-lineage verification passed.
- [x] No tuning was performed after final-test access.

## Scientific Acceptance Notes

- [x] Selected model recorded: `logistic_regression`.
- [x] Validation selection reason recorded: higher ROC AUC versus `gradient_boosting`.
- [x] Weak validation evidence documented honestly.
- [x] Final classification evidence documented honestly.
- [x] Training-prevalence baseline comparison documented.
- [x] Strategy result documented as a single final-test-period diagnostic, not proof of a
  predictive edge.
- [x] No profitability, investment-advice, trading-readiness, or live-readiness claim added.
- [x] Phase 3 research motivation recorded without implementing Phase 3.
- [x] Phase 3 warning recorded: do not tune against the already-opened Phase 2 final test.

## Security and Data Boundaries

- [x] No raw Alpaca data tracked.
- [x] No benchmark JSON artifacts tracked.
- [x] No row-level labels tracked.
- [x] No raw provider payloads tracked.
- [x] No credentials tracked.
- [x] No account identifiers tracked.
- [x] No authentication data tracked.
- [x] Generated benchmark artifacts confirmed ignored under `artifacts/benchmarks/*`.
- [x] No broker call or order submission added.
- [x] No API write route or dashboard execution control added.

## Versioning

- [x] `pyproject.toml` reports `2.0.0a2`.
- [x] `spy_market_agent.__version__` reports `2.0.0a2`.
- [x] Database/API/data/benchmark schema versions unchanged.
- [x] No Git tag created on the review branch.

## Quality

- [x] Owner-run final Pytest gate passed: 999 tests.
- [x] Owner-run coverage gate passed: 85%.
- [x] Owner-run Ruff formatting passed: 164 files already formatted.
- [x] Owner-run Ruff lint passed.
- [x] Owner-run MyPy passed: no issues in 132 source files.
- [x] Owner-run Git working tree after real benchmark was clean.
- [x] Release-preparation branch verification completed after documentation edits.
      - Pytest: 999 passed.
      - Coverage: 85%.
      - Ruff lint: passed.
      - Ruff formatting: 166 files already formatted.
      - MyPy: no issues found in 132 source files.
      - Working tree clean after verification.
      - Package/runtime version: 2.0.0a2.

## Post-Merge Steps

These unchecked items are an operator procedure for creating the release tag after branch
approval. They are not claims that the post-merge actions have already been performed.

- [ ] Merge approved branch into `main`.
- [ ] Pull merged `main`.
- [ ] Run full verification again on `main`.
- [ ] Confirm package/runtime versions.
- [ ] Confirm clean working tree.
- [ ] Create annotated `v2.0.0-alpha.2` tag.
- [ ] Push the tag.
- [ ] Confirm the tag points to verified `main`.
- [ ] Begin Phase 3 only after tag confirmation and a separate approved Phase 3
  specification.

Post-merge items are intentionally unchecked on this review branch.
