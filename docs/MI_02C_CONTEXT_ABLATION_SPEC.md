# Market Intelligence MI-2C Context Ablation Specification

Status: Owner-authorized implementation slice

Implementation branch: `review/market-intelligence-mi2c-context-ablation`

Base commit: `f3043d198685a6f3163d5cb078f6dbf88b1f9161`

Package/runtime version: `2.0.0b1`

## 1. Purpose

MI-2A established fail-closed contextual readiness contracts. MI-2B froze deterministic,
point-in-time transforms for QQQ, IWM, VIX, and the U.S. 10-year yield. MI-2C performs the
first development-only contextual ablation against the frozen MI-1D SPY-only scenario
candidate.

MI-2C asks one narrow research question:

> Holding the model family, model hyperparameters, scenario outcomes, chronological folds,
> fit rows, and assessment rows fixed, how do pre-declared contextual feature groups change
> development-only scenario probability metrics relative to the frozen SPY-only candidate?

The governing principle remains:

**AI interprets. Data measures. Statistics test. Risk controls constrain. Evidence decides.
Humans remain responsible.**

A negative or inconclusive contextual result is acceptable. MI-2C is not a model-promotion
or trading-authorization stage.

## 2. Scientific Boundary

MI-2C is a controlled feature ablation, not an unrestricted model search.

The following remain frozen from MI-1D and MI-1C:

- scenario outcomes and label definitions;
- development cutoff supplied by the MI-1C benchmark;
- expanding-window chronological fold policy;
- the MI-1D seven SPY feature columns;
- `StandardScaler` fitted on each training fold only;
- multinomial logistic regression;
- `C=1.0`;
- `solver="lbfgs"`;
- `max_iter=2000`;
- `tol=1e-8`;
- no class weights;
- canonical `DOWNSIDE / RANGE / UPSIDE` class order;
- probability metrics from the existing MI-1C evaluator.

MI-2C changes only the pre-declared contextual feature group appended to the frozen seven SPY
features.

MI-2C does not tune model hyperparameters, choose transforms after seeing outcomes, fit
calibration, optimize selectivity thresholds, run protected evaluation, or promote a model.

## 3. Fixed Ablation Variants

The MI-2C comparison surface is fixed before implementation to exactly five variants:

1. `SPY_ONLY`
2. `SPY_PLUS_QQQ_IWM`
3. `SPY_PLUS_VIX`
4. `SPY_PLUS_RATES`
5. `SPY_PLUS_FULL_CONTEXT`

The SPY-only reference must be the existing frozen MI-1D evaluation produced by
`evaluate_development_multinomial_candidate`. It must not be redefined or silently
reimplemented.

### 3.1 QQQ/IWM group

`SPY_PLUS_QQQ_IWM` appends exactly:

- `qqq_return_5`
- `qqq_return_20`
- `qqq_relative_strength_5`
- `qqq_relative_strength_20`
- `iwm_return_5`
- `iwm_return_20`
- `iwm_relative_strength_5`
- `iwm_relative_strength_20`

### 3.2 VIX group

`SPY_PLUS_VIX` appends exactly:

- `vix_level`
- `vix_change_5`
- `vix_percentile_60`

### 3.3 Rates group

`SPY_PLUS_RATES` appends exactly:

- `us10y_yield_level`
- `us10y_yield_change_5bp`
- `us10y_yield_change_20bp`

### 3.4 Full-context group

`SPY_PLUS_FULL_CONTEXT` appends all 14 MI-2B features in the frozen MI-2B canonical order.

Changing a group, feature, model setting, or comparison surface requires a separately reviewed
policy revision rather than an in-study choice.

## 4. Historical Context Input Contract

MI-2C must not back-cast one contemporary context snapshot into historical rows. Historical
context is supplied as already-derived MI-2B `SPYContextFeatureBundle` objects, one bundle per
historical anchor session used by the study.

A dedicated immutable historical-context contract must bind the bundle sequence to:

- the MI-2B context feature policy ID;
- the same SPY source market-data checksum used by the supplied `FeatureSet` and
  `ScenarioLabelSet`;
- the same source schema version used by the supplied SPY features and labels;
- unique, strictly increasing anchor sessions;
- an `as_of` calendar date equal to the bundle's own anchor session, so a later/revised
  snapshot cannot be back-cast into an earlier modeled row;
- valid MI-2B bundle lineage at every anchor.

The source checksum binding closes the research-lineage boundary between historical context
and the frozen SPY feature/label surface. It does not create a provider or download path.

## 5. Like-for-Like Fold Rule

Scientific comparison requires identical samples.

MI-2C must first obtain the frozen MI-1D SPY-only development evaluation. The contextual
variants may use only the retained MI-1D folds.

For every retained fold:

1. the assessment anchors, assessment outcome sessions, and assessment outcomes must exactly
   match the corresponding MI-1D fold;
2. the fit labels must be reconstructed using the frozen MI-1D observability rule;
3. the reconstructed fit row count and fit-session bounds must match the MI-1D model snapshot;
4. every fit and assessment anchor used by MI-1D must have an MI-2B historical context bundle;
5. a missing required context anchor fails closed;
6. contextual variants must not shrink, expand, forward-fill, impute, or otherwise alter the
   MI-1D training or assessment sample.

Extra historical context bundles outside the required MI-1D rows may exist but must not alter
fold membership or metrics.

## 6. Point-in-Time and Leakage Rules

- Only development labels admitted by the frozen MI-1C benchmark may be used.
- Protected labels or protected evaluation helpers must not be accessed.
- Context bundles are consumed only at their own historical anchor session.
- Each historical bundle's normalized `as_of` calendar date must equal its anchor session;
  delayed/revised context is rejected instead of being back-cast into an earlier row.
- No context bundle with an anchor after the relevant model row may be substituted.
- Scalers are fit only on each fold's training rows.
- Logistic regression is fit only on each fold's observable training outcomes.
- Assessment rows are transformed using the already-fitted fold scaler.
- No centered windows, backward fill, future context, or post-assessment outcomes are allowed.
- Mutating context after an earlier fold must not change that earlier fold's fitted model or
  probabilities.

## 7. Fixed Metrics and Comparisons

Each contextual variant must report the same existing `ScenarioEvaluationMetrics` used by
MI-1D:

- row count and class counts;
- accuracy;
- multiclass log loss;
- multiclass Brier score;
- mean true-class probability.

Each contextual variant must also report:

- median fold log loss;
- worst fold log loss;
- median fold Brier score;
- worst fold Brier score.

Each contextual variant is compared directly with SPY-only on identical retained rows using:

- `context_minus_spy_log_loss`;
- `context_minus_spy_brier_score`;
- `context_minus_spy_accuracy`;
- count of folds with lower log loss than SPY-only;
- count of folds with lower Brier score than SPY-only.

Lower log loss and lower Brier score are favorable. Accuracy is diagnostic only and is not a
profitability measure.

No single delta or fold-win count is an automatic promotion threshold.

## 8. Multiple-Comparison and Decision Rule

MI-2C pre-declares four contextual alternatives to reduce researcher degrees of freedom, but
it does not treat the best observed alternative as proven merely because it ranks first.

MI-2C must not:

- search additional context subsets;
- rank arbitrary feature combinations;
- tune a variant based on pooled outcomes;
- claim statistical significance without a separately specified procedure;
- automatically authorize MI-2D;
- automatically authorize protected evaluation.

The study output is descriptive development evidence. Whether any contextual variant merits a
separately reviewed MI-2D calibration/robustness slice remains an explicit scientific review
decision.

## 9. Lineage and Audit Requirements

Every contextual fold model snapshot must record at least:

- MI-2C policy ID;
- ablation variant;
- full ordered model feature columns;
- MI-2B context policy ID;
- active scikit-learn version;
- fit row count and fit session bounds;
- fitted scaler mean and scale;
- canonical class order;
- logistic-regression coefficients and intercepts;
- deterministic SHA-256 digest of the exact historical context bundle lineage used for the
  fold fit rows.

The study must record:

- SPY source market-data checksum;
- source schema version;
- scenario schema ID;
- MI-1C benchmark policy ID;
- MI-1D candidate and feature policy IDs;
- MI-2B context policy ID;
- MI-2C policy ID;
- horizon and development cutoff;
- the exact SPY-only evaluation plus all four contextual evaluations.

## 10. Explicit Non-Goals

MI-2C does not authorize or implement:

- new market-data downloads;
- provider integrations or network access;
- Alpaca acquisition changes;
- FRED, Treasury, CBOE, news, fundamentals, or other external calls;
- scheduler or daemon behavior;
- LLM or external AI API calls;
- new scenario labels or threshold changes;
- model-family or hyperparameter search;
- calibration fitting;
- selectivity-threshold search;
- protected evaluation;
- model promotion;
- shadow-model admission;
- broker communication;
- paper-order submission;
- risk-approval bypasses;
- live trading;
- package version bump;
- release tag;
- new third-party dependencies.

## 11. Testing Requirements

Tests must cover at least:

1. exact frozen ablation variants and context feature groups;
2. historical-context checksum/schema binding;
3. historical-context unique/increasing anchors and exact anchor-date `as_of` binding;
4. SPY-only output is the frozen MI-1D evaluation;
5. all contextual variants use exactly the MI-1D retained fold indexes;
6. all contextual variants use exactly the MI-1D fit and assessment row counts;
7. all contextual variants use exactly the MI-1D assessment anchors/outcomes;
8. contextual model columns equal the seven frozen SPY features plus only the declared group;
9. contextual probabilities contain all scenarios once and sum to one;
10. pooled and fold comparison deltas are arithmetically consistent;
11. missing required historical context fails closed instead of shrinking the sample;
12. source checksum/schema mismatch fails closed;
13. future context changes do not alter an earlier fold model or probabilities;
14. malformed or non-finite context values are rejected by upstream MI-2B contracts;
15. no protected-evaluation access;
16. static isolation from provider, broker, execution, paper-operation, credential, scheduler,
    and network paths;
17. full repository quality gate including `pytest --cov-fail-under=85`.

## 12. Acceptance Criteria

MI-2C is implementation-complete only when:

1. this specification exists on the implementation branch;
2. the five-variant comparison surface is frozen;
3. the frozen MI-1D candidate is reused as the SPY-only reference;
4. contextual variants hold model settings, fit rows, assessment rows, and outcomes constant;
5. historical context is checksum/schema bound and point-in-time auditable;
6. no automatic winner, promotion, calibration, selectivity, or protected-evaluation action is
   introduced;
7. no provider, scheduler, broker, execution, paper, or live-trading behavior is added;
8. package/runtime version remains `2.0.0b1`;
9. no release tag is created;
10. the full repository quality gate passes;
11. final review finds no blocking issue.

MI-2C acceptance authorizes only review of its development evidence and, if separately
approved, the next narrow MI-2 slice. It does not establish protected predictive value and
does not authorize trading.
