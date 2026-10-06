# Market Intelligence MI-1C Scenario Baseline Evaluation Specification

Status: Owner-authorized development-only research implementation slice

Implementation branch: `review/market-intelligence-mi1c-baseline-evaluation`

Base commit: `9359eb38a72ea66380766db63d73d1acd973df27`

Repository package/runtime version: `2.0.0b1`

Release behavior: No version bump and no release tag are authorized by MI-1C.

## 1. Purpose

MI-1C defines deterministic development-only multiclass evaluation for the MI-1B SPY
`DOWNSIDE / RANGE / UPSIDE` labels and establishes walk-forward performance records for the
three MI-1B naive baselines.

This phase answers a narrow question: can the repository evaluate the frozen scenario labels and
naive probability baselines chronologically, reproducibly, and without allowing unavailable
future outcomes into a fold fit?

MI-1C does not train or evaluate a statistical candidate model.

The governing principle remains:

**AI interprets. Data measures. Statistics test. Risk controls constrain. Evidence decides.
Humans remain responsible.**

## 2. Relationship to Existing Work

MI-1C builds on:

- MI-0 generic intelligence contracts;
- MI-1 SPY 5-session and 20-session analysis horizons;
- MI-1A deterministic SPY market-state derivation;
- MI-1B frozen scenario labels and naive baselines;
- the repository's existing chronological-research discipline.

The existing Phase 3 binary `WalkForwardManifest` is not reused directly. Its fixed six-row purge,
entry-session fields, exit-session fields, and binary-class requirements belong to the older
`t+1` entry / `t+6` exit trading label. MI-1C instead uses each scenario label's own recorded
`outcome_session` as the availability boundary.

## 3. Authorized Scope

MI-1C may add:

- immutable scenario-evaluation contracts;
- deterministic multiclass accuracy;
- deterministic multiclass log loss;
- deterministic multiclass Brier score;
- mean probability assigned to the realized class;
- chronological expanding-window baseline evaluation;
- non-overlapping outer assessment windows;
- fold-level and pooled baseline metrics;
- an explicit development cutoff;
- tests for chronological fitting, horizon-aware outcome availability, metric correctness,
  deterministic ordering, and execution isolation.

## 4. Explicit Non-Goals

MI-1C does not authorize or implement:

- statistical candidate-model training or inference;
- feature fitting or feature selection;
- hyperparameter search;
- calibration fitting;
- threshold optimization;
- protected evaluation;
- candidate promotion;
- scenario actionability;
- historical analogue search;
- new data providers or network access;
- QQQ, IWM, VIX, Treasury, macro, news, or fundamentals data;
- API/dashboard mutation;
- scheduling;
- broker communication;
- paper-order submission;
- live trading;
- new dependencies;
- package version changes;
- release tags.

## 5. Development-Only Boundary

Every MI-1C evaluation requires an explicit `development_through_session`.

Only labels satisfying:

`label.outcome_session <= development_through_session`

may be used as assessment outcomes or fitting observations.

This is deliberately stricter than filtering by anchor session. A label anchored before the
cutoff but resolved after the cutoff is unavailable and must be excluded.

MI-1C does not authorize access to or execution of the repository's protected evaluation. The
protected evaluation remains separately gated and unexecuted.

## 6. Version-1 Walk-Forward Policy

The MI-1C version-1 policy is frozen:

| Setting | Value |
| --- | ---: |
| minimum observable fit rows | 756 |
| standard assessment rows | 126 |
| assessment step | 126 |
| minimum final assessment rows | 63 |

Policy identifier:

`mi1c-expanding-window-756-fit-126-assess-126-step-v1`

The 756-row minimum preserves the approximate three-year evidence floor already used by the
repository's prior research program.

Assessment windows do not overlap each other. This avoids counting the same scenario anchor in
multiple outer folds.

## 7. Horizon-Aware Fit Boundary

For each outer fold:

1. identify the first assessment anchor session;
2. fit a naive baseline using only labels whose `outcome_session` is on or before that first
   assessment anchor session;
3. require at least 756 such observable fitting labels;
4. freeze the fitted baseline probabilities for the entire assessment fold;
5. score the frozen probabilities against the assessment labels;
6. advance by 126 assessment anchors.

No fixed purge-row count is used.

A 20-session label naturally remains unavailable longer than a 5-session label because its
`outcome_session` is later. The recorded outcome timestamp, not a guessed gap constant, controls
eligibility.

## 8. Final Assessment Window

A normal fold contains 126 assessment rows.

If fewer than 126 rows remain before the development cutoff, one final partial fold is allowed
only when at least 63 rows remain. Fewer than 63 trailing rows are left unused.

This rule is deterministic and must not depend on measured baseline performance.

## 9. Baselines Evaluated

MI-1C evaluates all three MI-1B baselines independently under the same folds:

- uniform;
- empirical prior;
- majority class.

The baseline probabilities are fitted once at fold start and remain frozen through the fold.

These are comparison references, not approved prediction models.

## 10. Multiclass Metrics

### Accuracy

The predicted class is the highest-probability outcome. Probability ties use canonical
`ScenarioOutcome` order for deterministic behavior.

Accuracy is descriptive only.

### Multiclass log loss

For realized outcome `y_i` and predicted probability `p_i(y_i)`:

`log_loss = -mean(log(max(p_i(y_i), 1e-15)))`

The fixed floor prevents `log(0)` for one-hot majority-class baselines without silently changing
stored baseline probabilities.

Lower is better.

### Multiclass Brier score

For three outcomes:

`brier = mean(sum_k (p_ik - 1[y_i = k])^2)`

This unscaled three-class form lies in `[0, 2]`.

Lower is better.

### Mean realized-class probability

`mean_true_class_probability = mean(p_i(y_i))`

Higher is better. This is a diagnostic, not a promotion criterion.

## 11. Pooled and Fold Metrics

Every baseline evaluation records:

- all fold definitions;
- fit row count and fit boundary per fold;
- assessment anchor bounds;
- assessment outcome bounds;
- fold class counts;
- fold accuracy;
- fold log loss;
- fold Brier score;
- fold mean realized-class probability;
- pooled metrics over the non-overlapping assessment rows;
- median fold log loss;
- worst fold log loss;
- median fold Brier score;
- worst fold Brier score.

Because the scenario outcomes themselves overlap in time for 5-session and 20-session horizons,
these metrics must not be interpreted as independent observations or used for naive significance
tests. Serial-dependence-aware uncertainty is deferred to a later research phase.

## 12. Determinism and Lineage

Every evaluation artifact must retain:

- source market-data checksum from the label set;
- source schema version;
- scenario schema identifier;
- horizon;
- development cutoff;
- MI-1C policy identifier;
- baseline kind;
- ordered fold records.

The evaluator must not mutate the MI-1B label set.

Identical inputs must produce identical quantitative output.

## 13. Scientific Interpretation Boundary

MI-1C may establish statements such as:

- empirical prior has lower development log loss than uniform;
- majority class is unstable across folds;
- one horizon has materially different class balance than another.

MI-1C must not establish statements such as:

- the system has predictive edge;
- a baseline is an approved model;
- a scenario probability is calibrated;
- a high-confidence subset has achieved an accuracy target;
- the protected evaluation has passed;
- a result should influence a trade.

## 14. Execution Isolation

MI-1C research code must not import or invoke:

- Alpaca trading clients;
- execution services;
- `paper_ops` submission paths;
- broker credentials;
- risk-approval bypasses;
- schedulers or daemons.

No MI-1C outcome is BUY, SELL, LONG, CASH, an order, or permission to trade.

## 15. Dependency Policy

No new third-party dependency is authorized.

The metric implementation should remain deterministic and dependency-light. Existing project
dependencies may remain installed, but MI-1C does not require a new library.

## 16. Testing Requirements

Tests must cover at least:

- exact multiclass metric calculations on known probabilities;
- fixed probability-floor behavior for zero-probability realized classes;
- deterministic probability-tie class selection;
- explicit development-cutoff exclusion by outcome session;
- minimum 756 observable fitting rows;
- fold fitting uses only outcomes observable by the first assessment anchor;
- fitted baseline remains frozen throughout an assessment fold;
- non-overlapping assessment anchors across folds;
- 126-row standard assessment windows;
- 63-row minimum partial final fold;
- fewer than 63 residual rows are not evaluated;
- 5-session and 20-session availability differ naturally through `outcome_session`;
- all three naive baselines are evaluated under identical fold boundaries;
- pooled row counts equal the union of non-overlapping assessment rows;
- source lineage propagation;
- source label-set immutability;
- unsupported or insufficient development history fails closed;
- no forecast/actionability/trading objects are produced;
- no execution/broker imports.

Full repository quality gates remain required before merge.

## 17. Existing Gates Preserved

- Phase 3 remains `NO CANDIDATE PROMOTION`.
- Protected evaluation remains unexecuted.
- P5-B broker paper submission remains blocked.
- P5-C remains `BLOCKED_NO_APPROVED_PAPER_MODEL`.
- Live trading remains prohibited.
- Package/runtime version remains `2.0.0b1`.

## 18. Acceptance Criteria

MI-1C is complete when:

1. this governing specification exists;
2. deterministic multiclass metrics exist;
3. all MI-1B naive baselines can be assessed chronologically on development-only labels;
4. fold fits are controlled by label outcome availability;
5. outer assessment windows do not duplicate anchors;
6. the evaluator records auditable lineage and deterministic fold boundaries;
7. no candidate model, calibration fit, protected evaluation, provider, execution, or live-trading
   capability is added;
8. existing safety gates remain unchanged;
9. full repository quality gates pass.

## 19. Next Slice

After MI-1C is reviewed and merged, the next separately reviewed slice may define the first
simple multiclass candidate model and its development-only comparison against the frozen naive
baseline benchmark. Candidate promotion and protected evaluation remain later gates.
