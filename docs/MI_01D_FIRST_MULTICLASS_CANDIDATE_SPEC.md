# Market Intelligence MI-1D First Multiclass Candidate Specification

Status: Owner-authorized development-only research implementation slice

Implementation branch: `review/market-intelligence-mi1d-first-candidate`

Base commit: `d13611362a0c502691e0a1a91d34a7d437321dcd`

Repository package/runtime version: `2.0.0b1`

Release behavior: No version bump and no release tag are authorized by MI-1D.

## 1. Purpose

MI-1D introduces the first statistical candidate for the Market Intelligence scenario program.
It answers one narrow development question:

> Can a fixed, simple multinomial logistic-regression model using only already-validated trailing
> SPY features improve development-only scenario probability metrics relative to the frozen MI-1C
> naive baselines?

MI-1D is a candidate-model evaluation phase. It is not a promotion phase, protected evaluation,
calibration phase, signal phase, or trading phase.

The governing principle remains:

**AI interprets. Data measures. Statistics test. Risk controls constrain. Evidence decides.
Humans remain responsible.**

## 2. Relationship to Existing Work

MI-1D builds on:

- MI-0 generic intelligence contracts;
- MI-1 SPY 5-session and 20-session scenario horizons;
- MI-1A deterministic SPY market-state derivation;
- MI-1B frozen `DOWNSIDE / RANGE / UPSIDE` labels;
- MI-1C development-only baseline evaluation and fold policy;
- the existing trailing `FeatureSet` pipeline and its source lineage.

MI-1D does not reuse the old binary trading-label candidate contracts. The scenario target is a
separate three-class market-outcome research problem.

## 3. Authorized Scope

MI-1D may add:

- one fixed multinomial logistic-regression candidate;
- a fixed seven-feature SPY input policy;
- training-only standardization;
- horizon-aware chronological candidate fitting;
- raw three-class probability prediction;
- fold-level candidate model snapshots;
- development-only comparison against all MI-1C naive baselines on identical retained folds;
- candidate-vs-baseline metric deltas;
- deterministic lineage and version metadata;
- tests for fit isolation, feature/label alignment, fold equality, class ordering, and execution
  isolation.

## 4. Explicit Non-Goals

MI-1D does not authorize or implement:

- protected evaluation;
- candidate promotion;
- model selection among multiple candidates;
- hyperparameter search;
- feature selection based on measured performance;
- calibration fitting;
- probability threshold tuning;
- high-confidence or 80%-precision claims;
- scenario actionability;
- `ABSTAIN` threshold tuning;
- historical analogue search;
- cross-asset data;
- new providers or network access;
- QQQ, IWM, VIX, Treasury, macro, news, or fundamentals inputs;
- API/dashboard writes;
- scheduling;
- broker communication;
- paper-order submission;
- live trading;
- new dependencies;
- package version changes;
- release tags.

## 5. Candidate Identity

Candidate identifier:

`mi1d-multinomial-logistic-regression-v1`

Feature-policy identifier:

`mi1d-spy-seven-feature-policy-v1`

The candidate specification is frozen before any real development result is interpreted.
Changing any model hyperparameter or feature membership requires a new candidate identifier.

## 6. Fixed Feature Policy

MI-1D uses exactly these existing trailing features in this order:

1. `close_return_1d`
2. `close_return_5d`
3. `close_return_20d`
4. `close_to_sma_20`
5. `realized_volatility_5`
6. `realized_volatility_20`
7. `log_volume_deviation_20`

These features are already produced by the deterministic trailing feature pipeline. MI-1D does
not add transformations that look forward, use scenario outcomes, or depend on cross-asset data.

The feature set intentionally spans short/medium momentum, medium trend, short/medium realized
volatility, and volume deviation while remaining small enough for an interpretable first linear
candidate.

## 7. Fixed Estimator Policy

Every fold uses:

1. `StandardScaler` fit on candidate training rows only;
2. `LogisticRegression` with:
   - solver: `lbfgs`
   - regularization: L2
   - `C = 1.0`
   - `max_iter = 2000`
   - `tol = 1e-8`
   - intercept enabled
   - no class weights

No hyperparameter is selected from assessment performance.

A convergence warning or fit failure is a fail-closed research error. The implementation must not
silently change solver, iteration limit, class weights, regularization, or features to obtain a
better result.

## 8. Canonical Class Encoding

The candidate must use this fixed encoding:

- `DOWNSIDE -> 0`
- `RANGE -> 1`
- `UPSIDE -> 2`

The fitted estimator must expose exactly these three classes. Missing-class training folds fail
closed.

Predicted probabilities are converted back into canonical `ScenarioOutcome` order before metrics
are calculated or artifacts are stored.

## 9. Source Alignment

The `FeatureSet`, `ScenarioLabelSet`, and MI-1C `ScenarioBaselineBenchmark` must agree on:

- source market-data checksum;
- source market-data schema;
- horizon;
- scenario schema;
- development cutoff where applicable.

Every candidate feature row is joined by `anchor_session` to one scenario label. A missing feature
for an assessment anchor is an error.

Feature values are never joined by row position alone.

## 10. MI-1C Fold Authority

MI-1D does not invent a new assessment schedule.

The MI-1C empirical-prior baseline evaluation supplies the authoritative ordered fold boundaries.
All three MI-1C baseline evaluations are already required to share those boundaries.

For a candidate fold:

1. take the MI-1C assessment fold unchanged;
2. define candidate training labels as labels whose `outcome_session` is on or before the first
   assessment anchor;
3. require a feature row for each candidate training anchor;
4. require at least 756 feature-aligned candidate training rows;
5. fit the scaler and logistic regression only on those rows;
6. predict the unchanged MI-1C assessment anchors;
7. score raw probabilities using MI-1C metrics.

## 11. Feature Warm-Up and Retained Folds

The existing feature pipeline excludes its first 20 source rows. Therefore an early MI-1C fold can
meet the 756 observable-label requirement while containing fewer than 756 feature-aligned candidate
training rows.

MI-1D must not lower the evidence floor to compensate.

The rule is:

- leading MI-1C folds with fewer than 756 feature-aligned candidate training rows are skipped;
- after the first candidate-eligible fold, later folds must remain eligible;
- retained candidate folds preserve their original MI-1C fold indexes and assessment boundaries;
- candidate-vs-baseline comparisons use only those same retained fold indexes.

Fold retention is determined only by feature availability and the frozen minimum training count,
never by measured candidate or baseline performance.

## 12. Training Availability Boundary

For retained fold start `t`, a scenario label may be used for candidate training only when:

`label.outcome_session <= t`

and an MI-1D feature row exists for `label.anchor_session`.

This condition applies independently to the 5-session and 20-session horizons. No shared hard-coded
purge count is introduced.

## 13. Development Cutoff

MI-1D requires the existing MI-1C `development_through_session` through the supplied benchmark.

No candidate assessment outcome may resolve after that cutoff.

MI-1D has no API for protected labels or protected evaluation artifacts.

## 14. Fold Model Snapshot

Each retained fold records an immutable audit snapshot containing at least:

- candidate identifier;
- feature-policy identifier;
- ordered feature columns;
- source feature schema version;
- scikit-learn version;
- training row count and training session bounds;
- last training outcome session;
- scaler means;
- scaler scales;
- canonical class order;
- multinomial coefficient matrix;
- intercept vector.

The snapshot is evidence about the fitted development candidate. It is not an approved deployable
model bundle.

## 15. Metrics

MI-1D reuses the MI-1C deterministic multiclass metrics:

- accuracy;
- multiclass log loss;
- multiclass Brier score;
- mean probability assigned to the realized class.

Primary probability-quality comparisons are log loss and Brier score. Accuracy remains descriptive.

No significance test is authorized in MI-1D because horizon outcomes overlap and serial dependence
has not yet been modeled.

## 16. Baseline Comparison

For every naive baseline kind:

- uniform;
- empirical prior;
- majority class;

MI-1D reconstructs pooled baseline metrics using only the exact MI-1C folds retained by the
candidate.

It records candidate-minus-baseline deltas for:

- log loss;
- Brier score;
- accuracy.

For loss metrics, a negative delta favors the candidate. For accuracy, a positive delta favors the
candidate.

These deltas are development evidence only and do not authorize promotion.

## 17. Determinism and Reproducibility

Identical inputs under the same supported dependency versions must produce identical fold
boundaries, feature matrices, class encoding, and metric results within deterministic floating-point
behavior.

The implementation records the scikit-learn version used for the fitted fold snapshots.

No randomness-dependent solver is authorized.

## 18. Failure Conditions

MI-1D fails closed when any of the following occurs:

- feature/label/benchmark source checksum mismatch;
- source schema mismatch;
- unsupported horizon;
- scenario schema mismatch;
- benchmark policy mismatch;
- benchmark fold boundaries do not match labels;
- a retained assessment anchor lacks a feature row;
- fewer than 756 feature-aligned training rows exist for every available fold;
- an eligible training fold lacks one of the three scenario classes;
- training data contains non-finite MI-1D features;
- scaler or model fit fails;
- logistic regression emits a convergence warning;
- fitted class order differs from the canonical three-class encoding;
- predicted probabilities are non-finite or invalid;
- candidate and baseline comparison rows do not match.

## 19. Scientific Interpretation Boundary

MI-1D may establish development statements such as:

- the fixed candidate has lower pooled development log loss than the empirical-prior baseline on
  the retained folds;
- the candidate underperforms naive baselines;
- performance varies materially across folds;
- the linear candidate appears insufficient and should be rejected or studied further.

MI-1D must not establish:

- protected out-of-sample predictive edge;
- calibrated probabilities;
- 80% reliable predictions;
- an approved model;
- an approved paper model;
- an actionable market signal;
- permission to trade.

A poor result is a valid MI-1D outcome.

## 20. Execution Isolation

MI-1D research code must not import or invoke:

- Alpaca trading clients;
- execution services;
- `paper_ops` submission paths;
- broker credentials;
- risk-approval bypasses;
- schedulers or daemons.

No candidate probability is BUY, SELL, LONG, CASH, an order, or execution authorization.

## 21. Testing Requirements

Tests must cover at least:

- exact frozen candidate and feature-policy constants;
- feature/label joining by session;
- source-lineage mismatch rejection;
- first MI-1C fold skipped when feature warm-up leaves fewer than 756 training rows;
- retained fold assessment boundaries exactly match MI-1C;
- at least 756 feature-aligned training rows per retained fold;
- scaler fit uses candidate training rows only;
- outcomes after fold start do not enter training;
- raw probabilities contain all three canonical classes and sum to one;
- all three naive baseline comparisons use identical retained folds;
- candidate pooled row count equals the union of retained assessment rows;
- missing assessment features fail closed;
- insufficient feature-aligned history fails closed;
- input `FeatureSet`, label set, and benchmark are not mutated;
- future feature changes do not alter an earlier fold model snapshot;
- no protected-evaluation access;
- no forecast/actionability/trading output;
- no execution/broker imports.

Full repository quality gates remain required before merge.

## 22. Existing Gates Preserved

- Phase 3 remains `NO CANDIDATE PROMOTION`.
- Protected evaluation remains unexecuted.
- P5-B broker paper submission remains blocked.
- P5-C remains `BLOCKED_NO_APPROVED_PAPER_MODEL`.
- Live trading remains prohibited.
- Package/runtime version remains `2.0.0b1`.

## 23. Acceptance Criteria

MI-1D is complete when:

1. this governing specification exists;
2. one fixed multinomial logistic-regression candidate exists;
3. candidate training uses only feature-aligned labels observable at fold start;
4. candidate assessment uses retained MI-1C fold boundaries unchanged;
5. naive baselines are compared on the same retained folds;
6. immutable model/evaluation artifacts preserve lineage and dependency version;
7. no protected evaluation, calibration, promotion, provider, execution, or live-trading
   capability is added;
8. existing safety gates remain unchanged;
9. full repository quality gates pass.

## 24. Next Slice

After MI-1D is reviewed and merged, the next separately reviewed slice should analyze the actual
candidate-vs-baseline development evidence and define a pre-registered decision for whether the
linear candidate is worth carrying forward. Calibration, richer challengers, and any protected
evaluation remain separate later gates.