# Version 2.0.0 Alpha 2 Release Notes

Release identity:

- Package: `2.0.0a2`
- Git release identifier: `v2.0.0-alpha.2`
- Date: 2026-08-09

Release policy: the `v2.0.0-alpha.2` tag must point only to a successfully verified `main`
commit after review approval and merge.

## Purpose

Version 2 Phase 2 establishes the first accepted real historical benchmark workflow for the
existing SPY research system. It measures the already-approved Version 1 model, feature,
label, signal, risk, and backtest path on verified real SPY data.

This release is an engineering and evidence milestone. It does not claim profitability,
investment suitability, model superiority, live-money readiness, or trading readiness.

## Engineering Result

Phase 2 benchmark infrastructure and the controlled evaluation workflow passed acceptance:

- Implementation PR: #20.
- Merged main commit: `1155c3c`.
- Dataset verification: passed.
- Validation-only model selection: completed.
- Final-test lock: completed before final-test access.
- Owner final-test acknowledgement: true.
- Controlled final-test execution: opened once under Stage B.
- Completed benchmark verification: passed.
- Runtime-lineage verification: passed.
- No post-final-test tuning was performed.

## Owner-Run Benchmark Summary

Dataset:

- Dataset ID: `spy-v2p1-825930b0a2bcab20c733b867`.
- Symbol/provider/feed/timeframe/adjustment: SPY, Alpaca, SIP, `1Day`, `all`.
- Session range: 2018-01-02 through 2025-12-31.
- Sessions: 2011.
- Canonical checksum:
  `d1c62194a3e13a164bbe09edad8cb6b4aa8bbd17a621d34da17e3e8edc96a259`.

Benchmark:

- Benchmark ID: `spy-v2p2-a065593e952e6a9d96f4be86`.
- Benchmark role: primary.
- Split: 1381 train rows, 295 validation rows, 297 final-test rows.
- Feature warm-up: 20 sessions.
- Boundary exclusion: 6 sessions.
- Entry/exit: open `t + 1` to open `t + 6`.

No raw Alpaca data, benchmark JSON artifacts, row-level labels, raw provider payloads,
credentials, account identifiers, or authentication data are included in these notes or
committed to Git.

## Model Selection

`logistic_regression` was selected by higher validation ROC AUC versus `gradient_boosting`.

Validation metrics:

| Model | ROC AUC | Log loss | Brier score |
| --- | ---: | ---: | ---: |
| `logistic_regression` | 0.4524265002013693 | 0.6655731478455287 | 0.2362171314791596 |
| `gradient_boosting` | 0.4438683044703987 | 0.6750181668327135 | 0.2406752163211499 |

The validation evidence was scientifically weak and should not be framed as model success.

## Final Classification Evidence

Final `logistic_regression` classification metrics:

- Rows: 297.
- Positives: 177.
- Negatives: 120.
- Predicted positives: 290.
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

Training-prevalence baseline:

- ROC AUC: 0.5.
- Log loss: 0.6749320142955199.
- Brier score: 0.24094293014378945.

Scientific interpretation: the selected classifier did not establish convincing directional
predictive discrimination. ROC AUC remained below `0.5`, probability metrics did not beat
the training-prevalence baseline, and the approximately `97.6%` predicted-positive rate
shows that the classifier behaved close to an almost-always-long signal during the final
period.

## Final Strategy Evidence

Primary base-cost selected-model strategy:

- Initial cash: 10000 USD.
- Final equity: 12546.511708197906.
- Total return: 0.2546511708197906.
- Annualized return: about 0.21226.
- Annualized volatility: about 0.18171.
- Maximum drawdown: about -0.14852.
- Sharpe ratio: about 1.18062.
- Exposure: about 97.64%.
- Completed trades: 5.

Base comparators:

| Strategy | Final equity | Total return | Maximum drawdown | Sharpe ratio |
| --- | ---: | ---: | ---: | ---: |
| Selected model | 12546.511708197906 | 0.2546511708197906 | about -0.14852 | about 1.18062 |
| Buy and hold | 11858.828299706134 | 0.1858828299706134 | about -0.18326 | about 0.90744 |
| Fixed 20-session momentum | 11497.440235049025 | 0.1497440235049025 | about -0.06335 | about 1.32024 |

Selected-model cost sensitivity:

- Idealized return: 25.5052%.
- Base return: 25.4651%.
- Adverse return: 25.1845%.
- Severe return: 20.8255%.

The selected-model strategy outperformed buy-and-hold on return and drawdown in this single
untouched final-test period, but the classifier itself showed weak discrimination and very
high exposure. The strategy result must not be represented as proof of a predictive market
edge or guaranteed profitability.

## Quality Gates

Owner-run final quality gates:

- `pytest`: 999 passed.
- Coverage: 85%.
- Ruff formatting: 164 files already formatted.
- Ruff lint: all checks passed.
- MyPy: no issues in 132 source files.
- Git working tree after real benchmark: clean.
- Generated benchmark artifacts: confirmed ignored under `artifacts/benchmarks/*`.

## Security and Data Boundaries

- Normal tests remain offline and deterministic.
- Benchmark commands are explicit CLI workflows.
- Generated real benchmark artifacts remain ignored.
- No Alpaca credentials or account identifiers are committed.
- No raw provider payloads or row-level final-test labels are committed.
- No broker order submission is part of Phase 2.
- No API write route, dashboard execution control, paper-execution behavior, or live-money
  trading support is added.

## Phase 3 Motivation

The weak Phase 2 scientific result motivates a future, separately governed Phase 3
walk-forward research program. Phase 3 should investigate walk-forward evaluation, feature
research, ablations, training/validation-only hyperparameter research, probability
calibration, threshold research, regime stability, drift analysis, and experiment/model
registry lineage.

Phase 3 must not tune against the already-opened Phase 2 final test.
