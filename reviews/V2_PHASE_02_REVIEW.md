# Version 2 Phase 2 Review

Status: Engineering acceptance complete; release preparation in review

## Branch And Release State

- Implementation PR: `#20`
- Implementation branch: `review/v2-phase-02-real-benchmark`
- Release-preparation branch: `review/v2-phase-02-release-preparation`
- Release-preparation base: `main` at `1155c3c`
- Final implementation SHA, original implementation candidate: `61f1431...`
- Hardening SHA: `779567f55a353f9f6f806cbc2c50d2754146a770`
- Package/runtime version prepared for release: `2.0.0a2`
- Target public release identifier: `v2.0.0-alpha.2`
- Git tag status: `v2.0.0-alpha.2` not created on the review branch

## Scope Accepted

Version 2 Phase 2 implemented a dedicated `spy_market_agent.benchmark` package for
deterministic, auditable, file-based real historical benchmark infrastructure. The accepted
workflow covers verified Phase 1 manifests, offline feed evidence, dataset eligibility,
feature and label construction, chronological split construction, immutable benchmark locks,
validation-only model selection, final-test locks, controlled final-test access, locked
baseline and strategy evaluation, regime diagnostics, artifact indexes, runtime-lineage
checks, audit replay, and deep offline semantic verification.

The hardening commit strengthened the implementation before owner-run real-data acceptance by
adding deep semantic verification, frozen runtime-lineage enforcement, regime-performance
diagnostics, reuse of the approved Version 1 risk/backtest engine for executable long/cash
strategy paths, and immutable final-test access with separate completion evidence.

## Owner-Run Acceptance Evidence

- Dataset ID: `spy-v2p1-825930b0a2bcab20c733b867`
- Dataset configuration: SPY, Alpaca, SIP, `1Day`, adjustment `all`, 2018-01-02 through
  2025-12-31, 2011 sessions.
- Dataset canonical checksum:
  `d1c62194a3e13a164bbe09edad8cb6b4aa8bbd17a621d34da17e3e8edc96a259`
- Dataset verification: passed.
- Benchmark ID: `spy-v2p2-a065593e952e6a9d96f4be86`
- Benchmark role: primary.
- Chronological split: 1381 train rows, 295 validation rows, 297 final-test rows, 20
  feature warm-up sessions, six boundary-exclusion sessions, `t+1` open entry, and `t+6`
  open exit.
- Benchmark verification: passed.
- Runtime-lineage verification: passed.
- Final test: opened once under the controlled Stage B process after final-test lock and
  owner acknowledgement.
- Post-final-test tuning: none.

This review records sanitized summary evidence only. Raw Alpaca data, benchmark JSON
artifacts, row-level labels, raw provider payloads, credentials, account identifiers, and
authentication data remain uncommitted.

## Scientific Result

Validation selected `logistic_regression` because it had higher validation ROC AUC than the
locked `gradient_boosting` candidate:

| Model | ROC AUC | Log loss | Brier score |
| --- | ---: | ---: | ---: |
| Logistic Regression | 0.4524265002013693 | 0.6655731478455287 | 0.2362171314791596 |
| Gradient Boosting | 0.4438683044703987 | 0.6750181668327135 | 0.2406752163211499 |

Final Logistic Regression classification results were scientifically weak:

- Rows: 297.
- Positives: 177.
- Negatives: 120.
- Accuracy: 0.6127946127946128.
- Balanced accuracy: 0.5221751412429378.
- Precision: 0.6068965517241379.
- Recall: 0.9943502824858758.
- F1: 0.7537473233404711.
- ROC AUC: 0.4640772128060263.
- Average precision: 0.5768659559801369.
- Log loss: 0.6758076912128111.
- Brier score: 0.24133930491472247.
- Predicted-positive rate: 0.9764309764309764.

The classifier did not establish convincing directional predictive discrimination. Its final
ROC AUC remained below 0.5, probability metrics did not beat the training-prevalence
baseline, and the approximately 97.6% predicted-positive rate means the final-period signal
behaved close to almost-always-long. This is valid benchmark evidence and not an engineering
failure, but it must not be framed as a proven predictive edge, trading readiness, or
profitability.

## Final-Test Strategy Summary

Primary base-cost selected-model strategy:

- Initial cash: 10000 USD.
- Final equity: 12546.511708197906.
- Total return: 0.2546511708197906.
- Annualized return: approximately 0.21226.
- Annualized volatility: approximately 0.18171.
- Maximum drawdown: approximately -0.14852.
- Sharpe ratio: approximately 1.18062.
- Exposure: approximately 97.64%.
- Completed trades: 5.

Base-cost comparators:

| Strategy | Final equity | Total return | Maximum drawdown | Sharpe ratio |
| --- | ---: | ---: | ---: | ---: |
| Selected model | 12546.511708197906 | 0.2546511708197906 | approximately -0.14852 | approximately 1.18062 |
| Buy and hold | 11858.828299706134 | 0.1858828299706134 | approximately -0.18326 | approximately 0.90744 |
| Fixed 20-session momentum | 11497.440235049025 | 0.1497440235049025 | approximately -0.06335 | approximately 1.32024 |

Cost sensitivity for the selected-model strategy:

- Idealized return: 25.5052%.
- Base return: 25.4651%.
- Adverse return: 25.1845%.
- Severe return: 20.8255%.

Although the selected-model strategy outperformed buy-and-hold on return and drawdown during
this single untouched final-test period, the classifier itself showed weak discrimination and
very high exposure. The strategy result must not be represented as evidence of a reliable
predictive market edge or guaranteed profitability.

## Controls Accepted

- `benchmark verify` performs deep offline semantic verification for the declared workflow
  stage and rejects semantic tampering even when `artifact_index.json` checksums are
  recomputed.
- Runtime lineage is frozen in the lock and enforced before validation, final locking,
  final-test execution, audit replay, and reproduction verification.
- Final-test row-level labels are guarded from Stage A services.
- `final_test_access.json` is immutable started-access evidence and is written before
  final-test labels are loaded.
- `final_test_completion.json` is written only after completed final-test artifacts are
  written and references all required final checksums.
- A completed non-audit final run cannot be silently repeated.
- Audit replay verifies existing final artifacts and does not overwrite accepted results.
- Selected-model and fixed-momentum strategy paths use the approved Version 1
  `StrategySignalSet` -> `ProposedOrder` -> `RiskConfig` / `evaluate_order_risk` -> fill ->
  backtest metric flow.
- Benchmark commands do not make network requests, require credentials, construct Alpaca
  clients, construct broker `TradingClient`, initialize execution databases, or submit
  orders.
- Safe path validation blocks path traversal, artifact-root escapes, source/test/doc/data/Git
  writes, symlink artifacts, and conflicting immutable writes.

## Owner-Run Quality Gates

- `pytest`: 999 passed.
- Coverage: 85% total.
- Ruff format: 164 files already formatted.
- Ruff lint: all checks passed.
- MyPy: success, no issues found in 132 source files.
- Git working tree after real benchmark: clean.
- Generated benchmark artifacts: confirmed ignored under `artifacts/benchmarks/*`.

## Release-Preparation Boundaries

- Documentation may record only sanitized evidence and release-preparation status.
- No benchmark algorithms, model behavior, feature engineering, label definitions, split
  logic, risk rules, execution behavior, APIs, dashboard behavior, market-data logic, or
  other runtime behavior are changed in this branch.
- No raw Alpaca data, benchmark JSON artifacts, row-level labels, provider payloads,
  credentials, account identifiers, authentication data, model binaries, SQLite artifacts, or
  temporary benchmark artifacts are committed.
- No real benchmark is rerun by Codex.
- No final test is reopened.
- No Phase 3 implementation begins.
- Phase 3 remains Version 2 Phase 3 / `v2.0.0-alpha.3`, Walk-Forward Model Research, under
  its own future governing protocol.
- Phase 3 research must not tune against the already-opened Phase 2 final test.

## Release-Preparation Verification

Release-preparation quality gates are rerun on
`review/v2-phase-02-release-preparation` after documentation and metadata edits:

- `python -m pytest`
- `python -m ruff check .`
- `python -m ruff format --check .`
- `python -m mypy`
- `git status --short`

Results are reported in the release-preparation branch final report.

## Explicit Confirmations

- Phase 2 benchmark infrastructure and controlled evaluation workflow passed engineering
  acceptance.
- The owner-run real SIP benchmark completed under controlled validation and one final-test
  execution.
- Benchmark verification and runtime-lineage verification passed.
- Quality gates passed in owner-run final acceptance.
- The scientific result did not establish a reliable predictive edge.
- No profitability, trading readiness, or model superiority claim is made.
- Package/runtime version is prepared as `2.0.0a2`.
- `v2.0.0-alpha.2` was not created on the review branch.
- No runtime behavior changes are part of release preparation.
- No raw real data, benchmark artifacts, final-test records, or secrets are committed.
- Phase 3 was not started.
