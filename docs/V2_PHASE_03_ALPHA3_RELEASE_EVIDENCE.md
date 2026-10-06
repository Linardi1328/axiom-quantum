# Version 2 Phase 3 Alpha 3 Release Evidence

Status: Accepted release evidence for `v2.0.0-alpha.3`.

Public release identifier: `v2.0.0-alpha.3` has been tagged and released.

Released package/runtime version: `2.0.0a3`.

## 1. Phase 3 Scope

Version 2 Phase 3 is the Walk-Forward Model Research phase. The completed development
substage is classification-first research on SPY daily OHLCV data using deterministic
walk-forward folds, predeclared feature ablations, finite scikit-learn model grids, the
predeclared calibration sub-study, classification metrics, and descriptive regime/drift
diagnostics.

The release-preparation branch recorded sanitized aggregate acceptance evidence only. It did
not run a new real-data experiment.

## 2. Governing Specification

The governing document is
[`docs/V2_PHASE_03_WALK_FORWARD_RESEARCH_SPEC.md`](V2_PHASE_03_WALK_FORWARD_RESEARCH_SPEC.md).
The specification preserves the frozen Phase 2 final-test boundary, requires chronological
walk-forward folds, separates classification diagnostics from strategy evaluation, and keeps
protected evaluation separately gated.

## 3. PR #24 Framework History

PR #24, `Version 2 Phase 3 - Specification`, merged the approved Phase 3 framework and
initial research scaffolding under `src/spy_market_agent/research/`.

## 4. PR #25 Development History

PR #25, `Version 2 Phase 3 - Development V1`, merged into `main` at
`cc6ee4ee404659143a1ba633d3faf7fddbc63f9f`. The owner-tested implementation head recorded
in the campaign manifest was `7ef437eed6f15b46ed28843d35e2a6df46367d2a` with package
version `2.0.0a2`.

## 5. Owner-Run Verification Summary

The owner-run development campaign reviewed for this release-preparation branch is:

- campaign ID: `spy-v2p3-dev-3741349b8aa34020b8425af5`
- artifact schema version: `spy-v2-phase3-research-artifacts-v1`
- runtime lineage: Python `3.12.13`, package `2.0.0a2`
- dependency lineage: `alpaca-py 0.43.5`, `exchange-calendars 4.13.2`, `pandas 2.3.3`,
  `pydantic 2.13.4`, `pydantic-settings 2.14.2`, `scikit-learn 1.9.0`
- artifact index verification: all indexed SHA-256 checksums were recomputed and matched
- generated artifacts: local and ignored under `artifacts/research/`

## 6. Accepted Phase 1 Parent Dataset Lineage

- parent Phase 1 dataset ID: `spy-v2p1-825930b0a2bcab20c733b867`
- parent Phase 1 canonical content checksum:
  `d1c62194a3e13a164bbe09edad8cb6b4aa8bbd17a621d34da17e3e8edc96a259`
- parent canonical market-data checksum:
  `a808554a1f572f62287066279c1b4733f378af2b0e42bed7d4a13c5b97458515`
- Phase 1 manifest artifact checksum:
  `9f80bf6d3d12762b4f818a9f798e2f408acfc6200f36fffb886c7323bcd86690`
- provider/feed/timeframe/adjustment: `alpaca` / `sip` / `1Day` / `all`
- parent session range: 2018-01-02 through 2025-12-31

## 7. Phase 3 Research-Slice Lineage

- research-slice dataset ID: `spy-v2p3-dev-slice-d026d0f30a0e378b7c0b6b9d`
- research-slice checksum:
  `1e644de15c36cb3621915ebabf288b3169044199fd32ac38f53f6ea5fc737a35`
- eligible source session range: 2018-01-02 through 2024-10-15
- eligible development prediction range: 2018-03-29 through 2024-10-07
- supervised development rows after global warm-up and label eligibility: `1642`

## 8. Phase 2 Final-Test Exclusion Evidence

- exclusion policy: `phase2-final-test-session-exclusion-v1`
- split policy: frozen Phase 2 split policy
- Phase 2 final-test available for tuning: `false`
- excluded source sessions: `303`
- label construction boundary: eligible market data was truncated before Version 1 labels
  were built for Phase 3 development research

Phase 2 final-test rows were unavailable for Phase 3 tuning. The release-preparation review
did not load Phase 2 final-test row-level labels, predictions, strategy rows, fills, or
generated benchmark result artifacts.

## 9. Walk-Forward Protocol

- fold policy ID: `phase3-expanding-window-756-train-126-assess-63-step-6-purge-v1`
- fold count: `13`
- global feature warm-up: `60` source sessions
- minimum initial training rows: `756`
- boundary exclusion: `6` supervised rows
- mandatory gap: `5` sessions
- entry offset: `t + 1`
- label exit offset: `t + 6`
- assessment window: `126` supervised rows
- step size: `63` supervised rows
- minimum final assessment rows: `63`

All feature-set and model candidates used the same deterministic development folds.

## 10. Feature Research Summary

The development campaign evaluated the frozen baseline feature families and the approved
research-only OHLCV-derived families `drawdown_position`, `volatility_structure`, and
`dollar_volume`.

| Feature-set candidate | Median ROC AUC | Median log loss | Median Brier | Median ECE | Defined folds |
| --- | ---: | ---: | ---: | ---: | ---: |
| `baseline_feature_set` | 0.547543 | 0.663959 | 0.235519 | 0.074247 | 13 |
| `baseline_plus_drawdown_position` | 0.565173 | 0.662091 | 0.234648 | 0.119592 | 13 |
| `baseline_plus_volatility_structure` | 0.526053 | 0.671509 | 0.238655 | 0.123664 | 13 |
| `baseline_plus_dollar_volume` | 0.547276 | 0.664072 | 0.235574 | 0.072228 | 13 |
| `all_feature_set` | 0.525499 | 0.666433 | 0.236956 | 0.122083 | 13 |
| `all_minus_drawdown_position` | 0.526885 | 0.671667 | 0.238729 | 0.123623 | 13 |
| `all_minus_volatility_structure` | 0.565173 | 0.662201 | 0.234701 | 0.119552 | 13 |
| `all_minus_dollar_volume` | 0.525499 | 0.666610 | 0.237040 | 0.122054 | 13 |
| `simpler_subset_feature_set` | 0.523990 | 0.670700 | 0.238876 | 0.065335 | 13 |

Selected development feature set: `baseline_plus_drawdown_position`.

Selected feature families: `trailing_returns`, `price_gaps`, `intraday_price_action`,
`trend_distance`, `realized_volatility`, `volume`, `drawdown_position`.

## 11. Candidate-Model Summary

Fixed Phase 2 model baselines and all predeclared development model candidates were retained
in the generated evidence, including neutral or unfavorable results.

| Candidate | Median ROC AUC | Median log loss | Median Brier | Median ECE | Defined folds |
| --- | ---: | ---: | ---: | ---: | ---: |
| `logistic_regression` | 0.565173 | 0.662091 | 0.234648 | 0.119592 | 13 |
| `gradient_boosting` | 0.488369 | 0.668272 | 0.237447 | 0.117544 | 13 |
| `logistic_regression_research_00` | 0.566197 | 0.660945 | 0.234120 | 0.110823 | 13 |
| `logistic_regression_research_01` | 0.556978 | 0.692642 | 0.249726 | 0.127643 | 13 |
| `logistic_regression_research_02` | 0.565173 | 0.662091 | 0.234648 | 0.119592 | 13 |
| `logistic_regression_research_03` | 0.554930 | 0.694048 | 0.250464 | 0.125533 | 13 |
| `logistic_regression_research_04` | 0.562356 | 0.662439 | 0.234801 | 0.099548 | 13 |
| `logistic_regression_research_05` | 0.554930 | 0.694705 | 0.250792 | 0.134422 | 13 |
| `hist_gradient_boosting_00` | 0.506339 | 0.728977 | 0.260813 | 0.160716 | 13 |
| `hist_gradient_boosting_01` | 0.511420 | 0.697841 | 0.248287 | 0.142254 | 13 |
| `hist_gradient_boosting_02` | 0.513298 | 0.778495 | 0.273271 | 0.213972 | 13 |
| `hist_gradient_boosting_03` | 0.501662 | 0.757179 | 0.273546 | 0.195042 | 13 |
| `hist_gradient_boosting_04` | 0.524457 | 0.911552 | 0.296168 | 0.231692 | 13 |
| `hist_gradient_boosting_05` | 0.514199 | 0.873329 | 0.294574 | 0.247618 | 13 |
| `hist_gradient_boosting_06` | 0.523098 | 1.123166 | 0.328198 | 0.294664 | 13 |
| `hist_gradient_boosting_07` | 0.520279 | 1.005551 | 0.330772 | 0.288911 | 13 |
| `extra_trees_00` | 0.537018 | 0.669331 | 0.238268 | 0.077975 | 13 |
| `extra_trees_01` | 0.543551 | 0.691441 | 0.249147 | 0.140414 | 13 |
| `extra_trees_02` | 0.548682 | 0.669639 | 0.238402 | 0.073471 | 13 |
| `extra_trees_03` | 0.555274 | 0.690856 | 0.248855 | 0.134997 | 13 |
| `extra_trees_04` | 0.521552 | 0.664158 | 0.235654 | 0.088099 | 13 |
| `extra_trees_05` | 0.534736 | 0.688397 | 0.247635 | 0.134152 | 13 |
| `extra_trees_06` | 0.527529 | 0.665114 | 0.236236 | 0.080430 | 13 |
| `extra_trees_07` | 0.524848 | 0.689896 | 0.248378 | 0.136519 | 13 |
| `extra_trees_08` | 0.511917 | 0.671651 | 0.239269 | 0.088276 | 13 |
| `extra_trees_09` | 0.511663 | 0.698164 | 0.252167 | 0.116701 | 13 |
| `extra_trees_10` | 0.529158 | 0.663984 | 0.235700 | 0.081545 | 13 |
| `extra_trees_11` | 0.526369 | 0.689987 | 0.248424 | 0.134428 | 13 |

Highest-ranked uncalibrated development candidate:
`logistic_regression_research_00`. It did not satisfy the predeclared promotion gates.

## 12. Calibration Summary

Calibration was evaluated only after the uncalibrated candidate campaign, using the
predeclared no-calibration, sigmoid, and isotonic policies. Calibration did not improve
median log loss or median Brier score in this owner-run development campaign.

| Calibration variant | Median ROC AUC | Median log loss | Median Brier | Median ECE | Defined folds |
| --- | ---: | ---: | ---: | ---: | ---: |
| `logistic_regression_research_00_calibration_none` | 0.566197 | 0.660945 | 0.234120 | 0.110823 | 13 |
| `logistic_regression_research_00_calibration_sigmoid` | 0.544900 | 0.696840 | 0.251755 | 0.126366 | 13 |
| `logistic_regression_research_00_calibration_isotonic` | 0.522312 | 1.211092 | 0.269587 | 0.201035 | 13 |

## 13. Drift/Regime Summary

Regime and drift diagnostics were descriptive only and were not used as a strategy-selection
surface.

- candidate diagnostic records: `44`
- candidate-fold diagnostic records: `572`
- PSI feature checks with defined values: `8723`
- undefined classification metric counts: `0`
- folds with feature missingness greater than zero: `0`
- folds with feature finite-value rate below one: `0`
- small-sample regime cells below 30 rows:
  - calendar-year cells: `132` of `836`
  - drawdown cells: `572` of `1364`
  - trend-200 cells: `264` of `924`
  - volatility cells: `220` of `924`

Small regime cells were labelled rather than suppressed. No regime-aware selection rule was
authorized or applied.

## 14. Promotion Decision

**NO CANDIDATE PROMOTION**

The Phase 3 research framework operated correctly, and the predeclared promotion rules
rejected all candidates. This is not an engineering failure. No predictive edge is claimed.
No candidate is authorized for protected evaluation, shadow mode, paper research, production
paper operation, or live trading.

## 15. Protected-Evaluation Status

Protected evaluation status: `scaffolded_locked_no_access`.

Protected evaluation was not executed. Protected labels were not loaded. No protected access
record, protected results file, protected evaluation period, or one-shot evaluation identity
was created by this release-preparation branch.

## 16. Strategy-Evaluation Status

Strategy optimization was not authorized and was not executed. The campaign manifest records
`strategy_results_artifact` as `not_generated_classification_first_branch`. The reviewed
artifact directory did not contain `strategy_results.json`, `threshold_results.json`, or
return-based candidate-selection evidence.

## 17. Safety Boundary

This release evidence makes no claim of profitability, investment suitability, paper
readiness, live readiness, or production readiness. The branch does not add broker
communication, scheduler behavior, API write routes, dashboard execution controls, paper
order changes, live-money support, new assets, intraday data, or external model services.

Generated real-data campaign artifacts remain local and ignored. This tracked document
contains only sanitized aggregate evidence and checksums.

## 18. Test/Quality-Gate Evidence

The owner-run campaign manifest records tested implementation SHA
`7ef437eed6f15b46ed28843d35e2a6df46367d2a` and package version `2.0.0a2`. This
release-preparation branch bumps package metadata to `2.0.0a3`; final branch quality-gate
results are recorded in the pull-request review and Codex completion report rather than by
embedding local command logs here.

Required release-preparation quality gates remain:

- `python -m pip install -e ".[dev]"`
- `pytest --cov-fail-under=85`
- `pytest tests/unit -q`
- `pytest tests/integration -q`
- `pytest -W error::FutureWarning`
- `ruff check .`
- `ruff format --check .`
- `mypy src tests`
- `git diff --check`
- `git status --short`

## 19. Artifact-Integrity Evidence

`artifact_index.json` checksum:
`f15c74beb7f637b590ea7048eb240d8c437c115da19410b3015f5dc465d1ca8e`.

All indexed artifacts were recomputed and matched the recorded SHA-256 checksums:

| Artifact | SHA-256 |
| --- | --- |
| `calibration_results.json` | `bc0c0e35f359864fa8d6c5dbec0f599079fccec62aa234949c6b714068052e90` |
| `classification_results.json` | `ddbd031bff0cd0e85512a6f4e36c0dca80708fc45b9facbb7e17e34f9c85cb41` |
| `experiment_manifest.json` | `45345ddefebeca862c545a2141b936000e3bf93d8ec62ff70fcbac993603f6e8` |
| `feature_registry.json` | `0323706eac0a1da691c6fc3009eddedcc21d4f4d6be028a02def926632b06f0d` |
| `fold_manifest.json` | `968cf2b3b4a2171dbbd0819007ed9fad0fb70df611e728106ebb017d649d55dc` |
| `hyperparameter_trials.json` | `24fb132512f8632c3c60d15c6848481afd3257c15c2afc5c53fcd09143157f3d` |
| `model_registry.json` | `d162989fe9af9b646bf337fa330ad934e35de7724fd72ab1125f2f60e91611f8` |
| `regime_drift_results.json` | `27fc8331447c7653919a441f8d4c6178a3895d7c7ad9c2dd709cb5b601f5c468` |
| `selection_report.md` | `db18cc5bb87fd7da6ed1b4facd765a14ac4afb386e4860f7f433d03f2b61ae40` |

## 20. Release Decision and Limitations

Alpha 3 was accepted as a research-framework and development-evidence release because the
system can reject weak candidates under predeclared scientific gates. It does not promote a
model and does not authorize a protected evaluation. The public `v2.0.0-alpha.3` tag was
created only after release-preparation merge and final verification on `main`.

Limitations:

- Development walk-forward evidence is not protected-evaluation evidence.
- Classification metrics are not profitability evidence.
- Calibration did not improve the reviewed probability-quality metrics.
- Small-sample regime cells were present and should not be over-interpreted.
- Any future protected evaluation requires separate owner authorization and a new controlled
  evaluation identity.
