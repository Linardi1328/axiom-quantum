# Version 2 Phase 3 - Walk-Forward Model Research Specification

Status: Accepted and released as `v2.0.0-alpha.3` after PR #24 framework merge, PR #25
development implementation merge, owner development testing, and Alpha 3 release
preparation

Target release identifier: `v2.0.0-alpha.3`

Release-preparation branch: `review/v2-phase-03-alpha3-release-preparation`

Released package/runtime version: `2.0.0a3`

PR #24 merged the approved Phase 3 framework and initial research scaffolding. PR #25
merged the manual, offline, classification-first development experimentation runner, and
owner development testing completed locally. Alpha 3 release preparation recorded sanitized
acceptance evidence, bumped package metadata to `2.0.0a3`, merged to `main`, and was tagged
as `v2.0.0-alpha.3`. The scientific outcome was `NO CANDIDATE PROMOTION`. Phase 3 did not
execute protected evaluation, strategy-threshold optimization, Phase 4 shadow mode, Phase 5
production paper operation, live trading, or broker communication.

Phase 3 begins after the completed `v2.0.0-alpha.2` release. Phase 2 established the first
real SPY historical benchmark on a verified Alpaca SIP, `1Day`, adjustment `all` dataset.
Its engineering workflow passed, but its selected classifier did not establish convincing
predictive discrimination. That weak scientific result is valid baseline evidence and is the
motivation for Phase 3 research.

This document governs Phase 3 only. It does not modify, invalidate, rerun, or reinterpret
the frozen Phase 2 benchmark. Phase 3 must not tune against the already-opened Phase 2 final
test. Phase 3 must not add live-money trading, production paper operation, API write routes,
dashboard execution controls, or automatic execution behavior.

For development experimentation on the accepted Phase 1 lineage, the Phase 3 runner must
mechanically reconstruct the frozen Phase 2 development/final-test split, truncate the
eligible market-data slice before label construction, and ensure every Phase 3 training,
boundary, assessment, calibration, diagnostic, and selection session precedes the Phase 2
final-test prediction partition. The boundary and research-slice identity are
lineage-defining campaign metadata.

## Table of Contents

- [1. Purpose](#1-purpose)
- [2. Scope](#2-scope)
- [3. Explicit Non-Goals](#3-explicit-non-goals)
- [4. Phase 2 Baseline Boundary](#4-phase-2-baseline-boundary)
- [5. Dataset Eligibility](#5-dataset-eligibility)
- [6. Primary Walk-Forward Protocol](#6-primary-walk-forward-protocol)
- [7. Fold Design and Chronology Rules](#7-fold-design-and-chronology-rules)
- [8. Leakage Protections](#8-leakage-protections)
- [9. Allowable Feature Research](#9-allowable-feature-research)
- [10. Feature Ablation Policy](#10-feature-ablation-policy)
- [11. Allowable Model Research](#11-allowable-model-research)
- [12. Hyperparameter Research Policy](#12-hyperparameter-research-policy)
- [13. Calibration Policy](#13-calibration-policy)
- [14. Threshold Research Policy](#14-threshold-research-policy)
- [15. Regime and Drift Analysis](#15-regime-and-drift-analysis)
- [16. Experiment Registry and Lineage](#16-experiment-registry-and-lineage)
- [17. Required Metrics](#17-required-metrics)
- [18. Baselines](#18-baselines)
- [19. Candidate Selection Rules](#19-candidate-selection-rules)
- [20. Protected Evaluation Policy](#20-protected-evaluation-policy)
- [21. Classification Versus Strategy Evaluation](#21-classification-versus-strategy-evaluation)
- [22. Research Artifacts](#22-research-artifacts)
- [23. Testing Plan](#23-testing-plan)
- [24. Acceptance Criteria for Alpha 3](#24-acceptance-criteria-for-alpha-3)
- [25. Rejection Criteria](#25-rejection-criteria)
- [26. Versioning Contract](#26-versioning-contract)
- [27. Approval Boundary](#27-approval-boundary)

## 1. Purpose

Phase 3 establishes a rigorous walk-forward research framework for improving SPY predictive
modeling while preserving the repository's chronological evaluation discipline, data
lineage, safety boundaries, and reproducibility.

The objective is to answer research questions that Phase 2 deliberately left out of scope:

- whether alternative trailing feature sets improve out-of-sample discrimination;
- whether feature groups are helpful, redundant, unstable, or harmful;
- whether model families beyond the fixed Phase 2 candidates improve walk-forward evidence;
- whether limited hyperparameter search improves validation evidence without leakage;
- whether probability calibration improves Brier score, log loss, and reliability;
- whether threshold policies can be researched without confusing strategy performance with
  classifier discrimination;
- whether results are stable across time, regimes, and market drift.

Phase 3 does not need to prove a profitable strategy. A finding that no researched candidate
is acceptable is a valid Phase 3 result when it is reproducible and honestly reported.

## 2. Scope

Phase 3 may include:

- this governing specification;
- repository status documentation for active Phase 3 development;
- deterministic walk-forward fold design;
- research-only experiment registry scaffolding;
- feature registry and feature-family documentation;
- feature ablation design;
- candidate model registry design;
- finite, predeclared hyperparameter search spaces;
- calibration research fitted only from eligible training data;
- threshold research fitted only from eligible development data;
- regime and drift diagnostics;
- classification and strategy metric definitions;
- synthetic offline tests for fold, lineage, registry, and leakage contracts;
- owner-run research reports that use verified local data without committing provider data.

Phase 3 implementation began with specification and scaffolding in PR #24. PR #25 merged
the authorized development substage for the predeclared classification-first walk-forward
campaign: verified Phase 1 manifest input, baseline and research OHLCV features, feature
ablations, fixed Phase 2 model baselines, finite scikit-learn model grids, the predeclared
calibration sub-study, classification metrics, regime/drift diagnostics, and candidate
selection. Alpha 3 release preparation recorded sanitized acceptance evidence and release
metadata. Strategy optimization and protected evaluation remain locked until a later
explicit approval. No model was promoted.

## 3. Explicit Non-Goals

Phase 3 does not authorize:

- modifying or invalidating Phase 2 benchmark evidence;
- tuning features, models, calibration, thresholds, or strategy choices against the Phase 2
  final-test rows or metrics;
- reopening the Phase 2 final test for non-audit research;
- changing the Version 1 label without a separate approved specification amendment;
- changing SPY-only, daily-bar, long-or-cash scope;
- adding assets other than SPY;
- adding intraday, tick, options, futures, crypto, or multi-asset data;
- adding live-money execution;
- adding new paper-execution behavior;
- adding real-time shadow operation;
- adding API write routes or dashboard execution controls;
- automatic scheduling, automatic model retraining, automatic order submission, or cloud
  deployment;
- committing raw provider data, canonical provider datasets, row-level real-data labels,
  generated research artifacts, credentials, account identifiers, or private screenshots;
- claiming profitability, investment suitability, trading readiness, or live readiness.

## 4. Phase 2 Baseline Boundary

Phase 2 remains frozen as `v2.0.0-alpha.2` baseline evidence.

Permitted Phase 3 uses of Phase 2 evidence:

- cite sanitized Phase 2 summary metrics already committed in release notes, review notes,
  README, roadmap, and the Phase 2 specification;
- compare Phase 3 development metrics against the Phase 2 fixed candidate definitions when
  those candidates are rerun on Phase 3 walk-forward development folds;
- use the weak Phase 2 result as motivation for research.

Prohibited Phase 3 uses:

- load Phase 2 final-test row-level labels, predictions, strategy rows, fills, or generated
  benchmark JSON for candidate research;
- choose features, models, hyperparameters, calibration, thresholds, cost assumptions, or
  report framing based on Phase 2 final-test performance;
- run a new non-audit final-test evaluation under the Phase 2 benchmark identity;
- treat the Phase 2 selected-model strategy result as proof of edge.

Phase 3 research reports must include a clear statement that the Phase 2 final test has
already been opened and is not available as a tuning or selection surface.

When the Phase 3 development runner consumes the accepted Phase 1 dataset lineage used by
Phase 2, it must derive the Phase 2 final-test prediction sessions from the frozen Phase 2
split policy rather than from filenames alone. The eligible Phase 3 development source data
must be truncated before `build_forward_label_set(...)`; the last eligible Phase 3
prediction row must have its `t + 6` exit before the first Phase 2 final-test prediction
session. Any attempted fold, calibration split, diagnostic assessment, or model-selection
surface that intersects Phase 2 final-test prediction sessions must fail closed.

## 5. Dataset Eligibility

The primary Phase 3 research dataset should use the same data-governance standard as Phase 2:

- symbol exactly `SPY`;
- timeframe exactly `1Day`;
- one provider and feed recorded consistently;
- adjustment mode recorded and applied consistently;
- one adjusted OHLCV series used for features, labels, backtests, and benchmarks;
- no mixing of raw and adjusted fields inside one calculation;
- Phase 1 manifest verification passes;
- canonical checksum and dataset ID are recorded;
- when the input lineage corresponds to the accepted Phase 2 parent dataset, the recorded
  Phase 3 research slice excludes the Phase 2 final-test prediction partition before labels
  are built;
- XNYS sessions validate;
- duplicate sessions, future sessions, incomplete current-session candles, and OHLCV
  inconsistencies fail closed.

The preferred primary research feed remains Alpaca SIP when available for the approved
range. IEX may be used only as a clearly labelled limited-coverage diagnostic unless a
future specification amendment explicitly approves otherwise.

Normal automated tests must remain offline and synthetic. Owner-run real-data research is
local and must not commit provider data or generated row-level artifacts.

## 6. Primary Walk-Forward Protocol

The recommended primary protocol is expanding-window walk-forward validation.

Default constants:

- `FEATURE_WARMUP_ROWS = 20` until the feature schema requires a larger recorded warm-up.
- `ENTRY_OFFSET_SESSIONS = 1`.
- `EXIT_OFFSET_SESSIONS = 6`.
- `MANDATORY_GAP_SESSIONS = 5`.
- `BOUNDARY_EXCLUSION_SESSIONS = 6`.
- Minimum initial training rows: `756`.
- Default assessment window: `126` supervised rows, approximately six trading months.
- Default step size: `63` supervised rows, approximately one trading quarter.
- Minimum assessment rows per fold: `63`.

For fold `k`:

```text
train_k = eligible supervised rows from research_start through train_end_k
gap_k = next 6 supervised rows after train_k
assessment_k = next 126 supervised rows after gap_k, or at least 63 rows for the final fold
next_train_end = train_end_k + 63 supervised rows
```

The training window expands over time. The assessment window follows training
chronologically and is never shuffled. The six-row boundary exclusion preserves the
five-session mandatory gap and the `t + 6` label-horizon purge.

Rolling-window training may be added as a secondary drift diagnostic only after the primary
expanding-window protocol is implemented. A rolling diagnostic must use a predeclared
training length, the same boundary exclusion rule, and the same metric definitions. It must
not replace the primary protocol without a specification amendment.

## 7. Fold Design and Chronology Rules

Walk-forward fold construction must:

- build features and labels before folds only when doing so does not use future rows;
- exclude feature warm-up rows;
- exclude rows whose labels are unavailable;
- preserve row order by validated XNYS session;
- reject duplicate, missing, unordered, or mismatched supervised rows;
- never use random row assignment;
- never rebalance classes by moving chronological boundaries;
- never move boundaries after seeing metrics;
- record every included training session, boundary-excluded session, and assessment session;
- ensure a row's feature session and label exit session fit within the fold's allowed
  chronology;
- fail closed when a fold has insufficient rows or only one target class and the selected
  metric cannot be computed honestly.

Fold identities must be deterministic from dataset identity, feature schema, label schema,
fold policy, fold boundaries, and code lineage. Clock time, usernames, hostnames, local
absolute paths, and random UUIDs must not define fold identity.

## 8. Leakage Protections

Phase 3 must preserve all repository leakage guardrails:

- features may use data only through the close of session `t`;
- prediction is generated after session `t` is complete;
- entry may occur no earlier than the open of session `t + 1`;
- the Version 1 target exits at the open of session `t + 6`;
- future-return and target columns must never appear in model features;
- centered rolling windows are prohibited;
- backward filling that introduces future information is prohibited;
- scalers, imputers, encoders, feature selectors, calibrators, and model parameters must be
  fitted only on eligible training data for the fold;
- validation and assessment rows must not determine training transformations except through
  explicitly recorded candidate-selection procedures;
- final protected rows must not influence feature, model, hyperparameter, calibration,
  threshold, regime, cost, or report-selection decisions;
- point-in-time availability must be documented for any external variable.

Any leakage defect invalidates the affected experiment identity. Fixing a leakage defect
requires a new experiment identity and a clear defect note.

## 9. Allowable Feature Research

Allowable feature families are limited to variables that can be computed from information
available through the close of session `t`:

- trailing returns and momentum over fixed lookbacks;
- trailing volatility, range, and realized-variance proxies;
- moving-average distance and trend features;
- drawdown and trailing high/low distance;
- open-close, high-low, and overnight gap features when all required values are known by the
  close of session `t`;
- trailing volume and dollar-volume features from the same adjusted-policy dataset;
- trailing regime labels such as trend, volatility, drawdown, and calendar-year diagnostics;
- calendar/session features known before trading, such as month, quarter, and trading-day
  position, when documented as non-performance-derived.

Feature research must record:

- feature family;
- feature name;
- feature schema version;
- lookback length;
- required input fields;
- adjustment policy;
- warm-up requirement;
- missing-value policy;
- economic or statistical intuition;
- leakage analysis;
- whether the feature is enabled for the candidate run.

New data sources, macroeconomic variables, sentiment feeds, fundamentals, options data, or
alternative data are out of scope unless a separate specification amendment records data
rights, point-in-time availability, revision policy, lineage, and tests.

## 10. Feature Ablation Policy

Phase 3 must use ablation to separate broad feature-set gains from isolated lucky results.

Required ablation views:

- Version 1 feature set as the frozen feature baseline;
- add-one-family experiments against the baseline;
- remove-one-family experiments from any candidate expanded set;
- all-feature candidate compared against simpler subsets;
- fold-by-fold feature availability and warm-up impact.

Ablation experiments must use the same walk-forward folds, model candidate family, metric
definitions, and cost assumptions as their comparator. Failed, neutral, and harmful
ablations must remain visible in the experiment registry. The final feature set must be
chosen from aggregate walk-forward evidence, stability, simplicity, and leakage review, not
from a single favorable fold.

## 11. Allowable Model Research

Phase 3 may research model families already available through approved dependencies,
especially scikit-learn. Examples include:

- regularized logistic regression variants;
- calibrated logistic regression;
- linear models with class weighting when predeclared;
- gradient boosting variants using existing scikit-learn estimators;
- random forest or extra-trees classifiers for diagnostic comparison;
- histogram gradient boosting when deterministic configuration and probability behavior are
  documented.

Every model must produce probabilities or scores for classification diagnostics. Models must
not contact brokers, submit orders, approve risk, size trades outside the strategy layer, or
override risk limits.

Deep learning, online learning services, AutoML frameworks, new major dependencies, GPU
tooling, cloud services, or external model APIs require explicit owner approval before they
enter the implementation plan.

## 12. Hyperparameter Research Policy

Hyperparameter research is allowed only when the search space is finite, recorded before the
run, and reproducible.

Allowed approaches:

- small predeclared grids;
- fixed-seed random search with a predeclared trial count and parameter distributions;
- inner walk-forward validation inside each fold's training rows;
- prior-fold-only adaptation when the adaptation rule is recorded before running.

Required controls:

- no parameter choice may use the assessment window for the same fold except as part of the
  explicitly declared research-selection surface;
- protected evaluation data must never be used for search;
- search spaces, random seeds, trial counts, scoring rules, and failure handling must be in
  the experiment manifest before execution;
- all tried configurations and failed configurations must be recorded;
- increasing the search space after seeing results creates a new experiment identity and
  must be disclosed.

The default candidate-selection score for hyperparameter research is median walk-forward
assessment ROC AUC, with log loss, Brier score, fold stability, and simplicity as
tie-breakers. Strategy metrics may be used for threshold or strategy research only when that
objective is declared separately before the run.

## 13. Calibration Policy

Probability calibration is research scope in Phase 3.

Allowed calibrators:

- Platt/sigmoid calibration through approved scikit-learn tooling;
- isotonic calibration only when each fitting fold has enough calibration observations and
  both classes are present;
- no-calibration baseline.

Calibration fitting must use only eligible training data for the fold. The preferred default
is to carve a trailing calibration window from the fold's training rows after an inner
six-row boundary exclusion. The main estimator fits on earlier training rows, the calibrator
fits on the trailing calibration rows, and the fold assessment window remains untouched until
evaluation.

Calibration reports must include log loss, Brier score, reliability by probability bin,
expected calibration error or a documented alternative, sample counts, and undefined reasons
when bins or classes are insufficient.

## 14. Threshold Research Policy

Phase 3 may research decision thresholds, but threshold research must be separated from
classifier discrimination.

Current implementation-stage note: the development-research branch preserves threshold
policy scaffolding but does not implement strategy-threshold optimization or use strategy
returns for candidate selection.

Rules:

- `0.5` remains the fixed diagnostic classification threshold unless a report explicitly
  labels an alternative threshold as a researched strategy threshold.
- Threshold candidates must be predeclared before running.
- Threshold choices may use only walk-forward development folds or prior folds, never the
  Phase 2 final test or any protected Phase 3 evaluation period.
- Threshold objectives must be declared before running, such as F1, balanced accuracy,
  expected utility, turnover-constrained return, drawdown-constrained return, or exposure
  constraints.
- Strategy threshold evaluation must include transaction costs, slippage, turnover,
  exposure, drawdown, and risk rejections.
- A threshold selected for strategy performance does not prove classifier discrimination.

Threshold reports must include the fixed `0.5` diagnostic baseline, all tried thresholds,
the selected threshold rule, fold-by-fold results, and the reason the rule was accepted or
rejected.

## 15. Regime and Drift Analysis

Phase 3 must report whether candidate behavior is stable across time and market regimes.

Required regime diagnostics:

- Phase 2 `trend_200` style bull/bear attribution where sufficient trailing history exists;
- Phase 2 `realized_volatility_20` style high/lower volatility attribution, with thresholds
  learned from training data only or fixed before the run;
- drawdown diagnostics using trailing running peaks through session `t`;
- calendar-year and fold-period summaries.

Required drift diagnostics:

- target prevalence by fold;
- feature missingness and finite-value rates by fold;
- feature distribution changes versus training rows;
- predicted-positive rate by fold;
- probability distribution by fold;
- calibration drift by fold;
- metric dispersion across folds;
- small-sample flags for regime cells.

Regime and drift diagnostics are descriptive unless an experiment explicitly declares a
regime-aware selection rule before running. Small regime cells must be labelled and not
over-interpreted.

## 16. Experiment Registry and Lineage

Phase 3 research must use an experiment registry before substantive real-data experiments
are accepted.

Each experiment record must include:

- experiment ID;
- phase identifier: `v2-phase-03`;
- dataset ID and canonical checksum;
- parent Phase 1 dataset ID and checksum when the campaign uses a derived development-only
  research slice;
- Phase 2 final-test exclusion policy, eligible Phase 3 development session range,
  research-slice checksum/identity, and excluded-session count for development campaigns
  derived from the accepted Phase 2 parent lineage;
- provider, feed, timeframe, adjustment mode, and session range;
- feature schema and enabled feature families;
- label schema and forecast horizon;
- fold policy ID and exact fold boundaries;
- model family and full model configuration;
- hyperparameter search space and tried configurations;
- calibration policy;
- threshold policy;
- strategy and cost assumptions when strategy metrics are reported;
- random seeds;
- baseline definitions;
- metric definitions;
- candidate-selection rule;
- candidate-selection configuration, including minimum valid fold count, material ROC-AUC
  delta, and materially different tolerance;
- protected-evaluation status;
- Git commit SHA;
- package/runtime version;
- Python version;
- dependency versions;
- artifact schema version;
- creation timestamp;
- non-sensitive owner/operator notes.

Experiment IDs must be deterministic from stable configuration and lineage inputs, including
the predeclared candidate-selection configuration. They must not be random UUIDs alone and
must exclude local absolute paths, usernames, hostnames, credentials, and raw provider data.

## 17. Required Metrics

Classification metrics:

- row count;
- positive and negative class counts;
- positive-class prevalence;
- predicted-positive rate;
- accuracy;
- balanced accuracy;
- precision;
- recall;
- F1 score;
- confusion matrix;
- ROC AUC where both classes exist;
- PR AUC / average precision where both classes exist;
- log loss;
- Brier score;
- calibration table or reliability bins;
- undefined-metric reasons.

Walk-forward aggregate metrics:

- per-fold value;
- mean;
- median;
- standard deviation where meaningful;
- interquartile range;
- worst fold;
- best fold;
- number of folds where the metric is defined;
- comparison against each baseline.

Strategy metrics, when strategy evaluation is included:

- initial cash;
- final equity;
- total return;
- annualized return where valid;
- annualized volatility where valid;
- maximum drawdown;
- Sharpe ratio with explicit risk-free-rate assumption;
- exposure percentage;
- turnover;
- number of orders;
- number of fills;
- number of completed trades;
- win rate where defined;
- average completed-trade return where defined;
- gross profit and loss;
- total transaction costs;
- total estimated slippage;
- rejected risk decisions;
- ending cash;
- ending shares.

Accuracy alone is never sufficient for candidate acceptance.

## 18. Baselines

Required classification baselines:

- Phase 2 fixed `logistic_regression` candidate rerun on Phase 3 walk-forward folds;
- Phase 2 fixed `gradient_boosting` candidate rerun on Phase 3 walk-forward folds;
- majority-class baseline derived from each fold's training data only;
- always-positive baseline;
- always-negative baseline;
- training-prevalence probability baseline derived from each fold's training data only.

Required strategy baselines when strategy metrics are reported:

- always cash;
- buy and hold over each assessment window using the same canonical adjusted series;
- fixed 20-session trailing momentum comparator;
- Phase 2 fixed selected-model signal policy rerun on the same development folds when
  legally and technically available without using Phase 2 final-test rows.

Baselines must not use assessment or protected prevalence to define probabilities or
classes. Buy-and-hold must not be described as equivalent to the model's overlapping
five-day label and turnover behavior.

## 19. Candidate Selection Rules

Phase 3 candidate selection happens on walk-forward development evidence only.

Default classification selection rule:

1. Reject candidates with leakage, invalid lineage, non-finite required metrics, or fewer
   than the minimum required valid folds.
2. Prefer higher median fold ROC AUC.
3. Break close ties with lower median log loss.
4. Break remaining ties with lower median Brier score.
5. Prefer the candidate with better worst-quartile ROC AUC when aggregate metrics conflict.
6. Prefer simpler models and smaller feature sets when evidence is not materially different.

A candidate should not be promoted to protected evaluation unless it beats the
training-prevalence probability baseline on median log loss or Brier score and shows
walk-forward discrimination that is materially better than the fixed Phase 2 model baselines.
The material ROC-AUC improvement threshold must be predeclared before execution, must be
greater than zero, and is part of the experiment identity. Changing the minimum valid fold
count, material ROC-AUC delta, or materially different tolerance after reviewing assessment
evidence creates a new experiment identity. A zero ROC-AUC delta is not materially better.
If no candidate qualifies, Phase 3 should report that no model is promoted.

Strategy selection, if performed, must use a separately declared strategy objective and must
not override classification evidence without explicit review. High strategy return from high
market exposure is not enough to establish predictive discrimination.

## 20. Protected Evaluation Policy

Phase 3 may define a later protected evaluation only after candidate features, model family,
hyperparameters, calibration, threshold, cost assumptions, metric definitions, and selection
rules are frozen.

Protected evaluation must:

- use a new Phase 3 evaluation lock;
- use a protected period or dataset that was not used to select the candidate;
- avoid the already-opened Phase 2 final test as a tuning surface;
- require explicit owner acknowledgement before any protected labels are loaded;
- write immutable access evidence before loading protected row-level labels;
- run once per protected evaluation identity unless audit replay verifies unchanged
  artifacts;
- refuse repeated non-audit evaluation after completion;
- record sanitized summary evidence only.

The preferred protected evaluation source is a newly acquired, verified, post-Phase-2 data
range that was not available to Phase 2 selection. If that is unavailable, Phase 3 may stop
at walk-forward research evidence without promoting a candidate. Stopping without a
protected candidate is an acceptable alpha 3 outcome.

## 21. Classification Versus Strategy Evaluation

Classification evaluation answers whether predicted probabilities or classes discriminate
between positive and negative labels.

Strategy evaluation answers how a selected signal policy behaves when mapped to
long-or-cash trades with costs, slippage, whole-share accounting, and risk checks.

Reports must keep these separate:

- ROC AUC, PR AUC, log loss, Brier score, calibration, and confusion matrices are
  classification diagnostics.
- return, drawdown, Sharpe ratio, exposure, turnover, orders, fills, and costs are strategy
  diagnostics.
- A strategy can perform well because it is highly exposed to a rising market even when the
  classifier has weak discrimination.
- A classifier can improve probability quality without producing an acceptable strategy
  after costs.

No Phase 3 report may present strategy performance alone as evidence of a reliable
predictive edge.

## 22. Research Artifacts

Generated Phase 3 research artifacts should remain ignored by default under:

```text
artifacts/research/<experiment_id>/
  experiment_manifest.json
  fold_manifest.json
  feature_registry.json
  model_registry.json
  hyperparameter_trials.json
  calibration_results.json
  threshold_results.json
  classification_results.json
  strategy_results.json
  regime_drift_results.json
  selection_report.md
  artifact_index.json
```

Small synthetic fixtures may be committed when they contain no provider data, credentials,
account identifiers, private paths, or row-level real-data labels. Generated owner-run
real-data artifacts remain local and ignored unless a separate review approves a sanitized
summary.

## 23. Testing Plan

Required tests or documentation checks for Phase 3 scaffolding:

- Phase 3 specification exists and is linked from status docs;
- Phase 3 active branch and target release are documented;
- Phase 2 final-test tuning prohibition is documented;
- no paper or live execution behavior is added;
- fold construction preserves chronological order;
- fold construction enforces the six-row boundary exclusion;
- fold identities are deterministic;
- features cannot include target or future-return columns;
- feature warm-up handling is explicit;
- scalers, selectors, calibrators, and models fit only on eligible training rows;
- experiment manifests record dataset, feature, label, fold, model, calibration, threshold,
  metric, code, package, Python, and dependency lineage;
- generated real-data research artifacts remain ignored;
- normal tests remain offline and synthetic.

Full model experimentation should add unit and integration tests for every implemented
feature family, model candidate, hyperparameter search path, calibration path, threshold
policy, metric, artifact schema, and CLI entry point.

## 24. Acceptance Criteria for Alpha 3

Version 2 Phase 3 alpha 3 may be accepted when:

- the governing Phase 3 specification is approved;
- roadmap, README, workflow, architecture, reproducibility, and safety documentation
  accurately describe Phase 3 active scope;
- Phase 2 frozen benchmark evidence remains unchanged and is not invalidated;
- no Phase 2 final-test row-level data is loaded or used for tuning;
- the primary walk-forward protocol is implemented or fully specified for implementation;
- fold chronology, gap, purge, warm-up, and leakage rules are covered by tests or
  documentation checks appropriate to the branch scope;
- experiment registry and lineage requirements are implemented or specified as required
  scaffolding for later model experiments;
- required metrics, baselines, and selection rules are documented;
- classification evaluation and strategy evaluation are clearly separated;
- no live trading, paper-execution behavior, API write route, dashboard execution control,
  scheduler, or automatic execution is added;
- no raw provider data, generated real-data artifacts, credentials, account identifiers, or
  private screenshots are committed;
- normal automated tests pass offline;
- coverage remains at least 85% when the coverage gate is run;
- Ruff, formatting, MyPy, `git diff --check`, and `git status --short` are clean before
  review.

Alpha 3 acceptance does not require a candidate model to beat baselines. It does require an
honest, reproducible framework that can reject weak candidates.

## 25. Rejection Criteria

Phase 3 must be rejected or held when:

- Phase 2 final-test rows or generated final-test artifacts are used for tuning;
- a final-test result is reopened outside approved audit replay;
- chronological order is broken;
- boundary gaps or label-horizon purges are missing;
- feature leakage is detected;
- training transformations are fitted on assessment or protected rows;
- hyperparameter search spaces are changed after seeing results without a new experiment
  identity;
- calibration or thresholds use protected data;
- strategy metrics are presented as classifier discrimination;
- baselines are missing or incorrectly constructed;
- experiment lineage is incomplete;
- metrics are non-finite or silently substituted;
- generated real provider data, row-level labels, credentials, or account identifiers are
  committed;
- live-money, production paper, shadow operation, API write, dashboard execution, or
  scheduling behavior is added without separate approval;
- profitability, investment-advice, trading-readiness, or live-readiness claims are made.

## 26. Versioning Contract

- Phase 3 begins from the completed `v2.0.0-alpha.2` release baseline.
- The implementation branch starts at package/runtime version `2.0.0a2`.
- Phase 3 specification and research-scaffolding work must not bump the package/runtime
  version at the start of implementation.
- The Alpha 3 release-preparation branch may set:
  - `pyproject.toml` package version to `2.0.0a3`;
  - `spy_market_agent.__version__` to `2.0.0a3`.
- Public Git release identifier: `v2.0.0-alpha.3`.
- No `v2.0.0-alpha.3` tag is created until specification approval, implementation review,
  required verification, merge to `main`, and owner approval are complete.
- API, database, market-data, and benchmark schema versions do not change merely because the
  package version changes.

## 27. Approval Boundary

This specification is the governing Phase 3 document. PR #24 delivered the approved
walk-forward model-research framework and initial research scaffolding. PR #25 delivered the
development-only, manual, offline classification experimentation runner under the same
leakage, lineage, fold, artifact, and selection controls. Alpha 3 release preparation
recorded sanitized acceptance evidence and release metadata, then was merged and tagged as
`v2.0.0-alpha.3`.

It does not authorize model promotion, Phase 3 protected evaluation execution, Phase 5
production paper operation, live trading, new assets, strategy optimization, or production
execution behavior. Phase 4 shadow infrastructure is now governed separately by
`docs/V2_PHASE_04_REAL_TIME_SHADOW_MODE_SPEC.md`; model-connected shadow inference remains
locked because Phase 3 produced `NO CANDIDATE PROMOTION`. Any expansion beyond the relevant
active specification requires explicit owner approval and a new or amended governing
specification.
