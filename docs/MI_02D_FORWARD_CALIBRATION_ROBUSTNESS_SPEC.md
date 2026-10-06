# Market Intelligence MI-2D Forward Calibration, Abstention, and Robustness Specification

Status: Owner-authorized final development slice before the planned development break

Implementation branch: `review/market-intelligence-mi2d-forward-calibration-robustness`

Base commit: `99ebe9cfa69d53f679235038d3c81d58ee6505a5`

Package/runtime version: `2.0.0b1`

## 1. Purpose

MI-2A established contextual readiness contracts, MI-2B froze causal context transforms, and
MI-2C created a like-for-like development-only ablation against the frozen SPY-only candidate.
MI-2D adds the final pre-break software layer for forward calibration, selective abstention,
and interpretable robustness analysis of an **explicitly nominated** MI-2C contextual variant.

MI-2D does not decide which MI-2C variant should be carried forward. A caller must supply one
already-constructed contextual evaluation. This prevents the calibration layer from silently
turning the best development result into an automatically selected winner.

The governing principle remains:

**AI interprets. Data measures. Statistics test. Risk controls constrain. Evidence decides.
Humans remain responsible.**

## 2. Scientific Boundary

MI-2D is development-only software and evidence infrastructure.

It may answer questions such as:

- whether a preselected contextual variant's out-of-fold probabilities benefit from
  chronologically fitted temperature scaling;
- whether a high-confidence subset selected using only prior observable development evidence
  reaches the frozen 80% precision stretch objective on later development folds;
- how calibrated probability quality varies across the existing interpretable SPY
  trend/volatility regimes.

It must not establish:

- protected out-of-sample edge;
- a statistically proven contextual winner;
- model promotion;
- paper-model approval;
- live-trading eligibility;
- profitability;
- permission to trade.

No MI-2C variant is automatically selected by MI-2D. A negative, inconclusive, or all-abstain
result is valid.

## 3. Authorized Input

MI-2D consumes one immutable `ContextAblationVariantEvaluation` produced under the frozen MI-2C
contracts plus the matching SPY `FeatureSet` used only for causal regime classification.

The supplied contextual evaluation must already bind:

- one of the four contextual MI-2C variants;
- MI-2B feature membership;
- chronological MI-1D retained folds;
- development-only assessment outcomes;
- raw three-class out-of-fold probabilities;
- source market-data checksum and schema;
- scenario schema and horizon.

MI-2D does not refit the contextual logistic-regression candidate. It operates only on the
already-generated MI-2C out-of-fold probability evidence.

## 4. Forward-Only Fold Policy

For each MI-2C assessment fold in chronological order, MI-2D defines the current fold start as
the fold's first assessment anchor.

Calibration/selectivity history consists only of rows from **earlier MI-2C folds** whose
recorded `outcome_session` is on or before the current fold start.

Therefore:

`history_row.outcome_session <= current_fold.first_assessment_anchor`

is mandatory.

The current fold's outcomes must never participate in the temperature or selectivity policy
used for that same fold.

A fold is eligible for MI-2D forward evaluation only when at least 63 prior observable
out-of-fold rows exist. Leading folds that do not meet this floor are skipped deterministically.
If no fold is eligible, evaluation fails closed as insufficient forward history.

## 5. Frozen Temperature Calibration

MI-2D reuses the already-frozen MI-1E temperature grid:

`(0.50, 0.75, 1.00, 1.25, 1.50, 2.00)`

For each eligible current fold:

1. collect prior observable raw out-of-fold probabilities;
2. apply every frozen temperature to that prior history;
3. compute multiclass log loss on prior-history outcomes;
4. choose the temperature with minimum prior-history log loss;
5. break an exact tie by choosing the numerically smaller temperature;
6. apply that fixed temperature to the current fold raw probabilities.

No current-fold outcome is used to choose its temperature. No temperature outside the frozen
grid may be searched.

MI-2D records raw and calibrated prior-history metrics/ECE plus calibrated current-fold
metrics/ECE.

## 6. Frozen Selective-Abstention Policy

MI-2D reuses the MI-1F threshold surface:

Top-probability thresholds:

`(0.50, 0.55, 0.60, 0.65, 0.70)`

Top-vs-second separation thresholds:

`(0.05, 0.10, 0.15, 0.20)`

The minimum selected-history count remains 63 and the stretch precision objective remains
0.80.

For each eligible current fold, threshold candidates are evaluated only on that fold's prior
observable history after applying the selected historical temperature.

A candidate qualifies only when:

- selected prior-history rows >= 63; and
- realized prior-history precision >= 0.80.

If multiple policies qualify, choose deterministically by:

1. highest prior-history coverage;
2. highest prior-history precision;
3. higher top-probability threshold;
4. higher separation threshold.

If no policy qualifies, the fold's policy is `None` and **every row in the current fold
abstains**. MI-2D must not lower thresholds to force a prediction.

Current-fold selected precision/coverage are evaluation evidence only and are never execution
authorization.

## 7. Calibration and Selectivity Interaction

Temperature selection and threshold selection use the same prior observable out-of-fold
history for a current fold. Both choices are frozen before the current fold is scored.

This is a forward development evaluation, not an estimate derived from the current fold.

MI-2D does not claim the prior-history tuning surface is statistically independent. It records
the result as development evidence and leaves any future protected comparison to a separate,
explicitly authorized phase.

## 8. Regime Robustness

MI-2D reuses the existing causal Phase 1 SPY regime classifier:

- positive trend / low volatility;
- positive trend / high volatility;
- negative trend / low volatility;
- negative trend / high volatility.

Regime classification uses only the supplied SPY `FeatureSet` and the existing causal trailing
volatility rule. The feature-set source checksum/schema must match the contextual evaluation.

Across MI-2D eligible assessment rows, each regime with at least the existing 20-row minimum
reports:

- calibrated multiclass metrics;
- ECE;
- selected row count;
- selected coverage;
- selected precision when at least one row was selected.

Smaller regimes are reported explicitly as omitted for insufficient evidence. MI-2D makes no
significance claim from regime differences.

## 9. Lineage and Audit Requirements

The final MI-2D evaluation records at least:

- MI-2D policy ID;
- nominated contextual variant;
- source MI-2C policy ID;
- horizon and development cutoff;
- source checksum/schema/scenario schema;
- each eligible source fold index;
- prior-history row count and last observable outcome session;
- selected temperature;
- prior raw/calibrated metrics and ECE;
- selected threshold policy or explicit no-policy state;
- prior-history selected count/coverage/precision;
- calibrated current-fold probabilities and metrics;
- current-fold selected count/coverage/precision;
- pooled calibrated metrics/ECE;
- pooled selected coverage/precision;
- regime robustness results and explicitly omitted regimes.

## 10. Failure Conditions

MI-2D fails closed when, among other conditions:

- the supplied evaluation is not a contextual MI-2C variant;
- source checksum/schema does not match the SPY feature set;
- fold indexes or assessment rows are not chronologically ordered;
- a historical outcome used for calibration resolves after the current fold start;
- no fold has at least 63 prior observable out-of-fold rows;
- probability rows are malformed or do not sum to one;
- a selected temperature is outside the frozen MI-1E grid;
- a selectivity policy is outside the frozen MI-1F threshold surface;
- regime classification lacks the required SPY feature anchor/history.

No missing value, threshold, regime, or probability may be invented.

## 11. Explicit Non-Goals

MI-2D does not authorize or implement:

- automatic MI-2C winner selection;
- new model fitting or hyperparameter search;
- new context transforms or feature subsets;
- new market-data/provider/network integrations;
- calibration using the current fold or protected data;
- threshold search using the current fold or protected data;
- MI-2E protected comparison;
- candidate/model promotion;
- shadow-model admission;
- API/dashboard mutation;
- scheduler or daemon behavior;
- broker communication;
- paper-order submission;
- risk-approval bypasses;
- live trading;
- package version bump;
- release tag;
- new dependency.

## 12. Testing Requirements

Tests must cover at least:

1. the MI-1E temperature grid is reused exactly;
2. MI-1F threshold grids/minimum count/80% objective are reused exactly;
3. leading folds without 63 observable prior out-of-fold outcomes are skipped;
4. history excludes earlier-fold outcomes that are not observable by current-fold start;
5. current-fold outcomes cannot change that same fold's selected temperature/policy;
6. temperature is selected only from prior history and is applied to current raw probabilities;
7. no-qualifying-policy produces current-fold all-abstain behavior;
8. qualifying prior evidence produces deterministic policy selection;
9. pooled calibrated metrics cover exactly the eligible forward folds;
10. regime evaluation is causal and omits regimes below the minimum row count;
11. feature/evaluation lineage mismatch fails closed;
12. malformed probability and artifact contracts fail closed;
13. no protected-evaluation, provider, broker, execution, paper-operation, credential,
    scheduler, or network path is imported;
14. full repository quality gate including `pytest --cov-fail-under=85` passes.

## 13. Acceptance and Development Break Boundary

MI-2D is implementation-complete when this specification, deterministic forward evaluator,
and focused tests are present; the full repository gate is green; and final review finds no
scientific, leakage, scope, or safety blocker.

MI-2D completion does **not** authorize MI-2E protected evaluation. It only leaves the
repository with a reviewed development-only contextual calibration/abstention/robustness
framework.

After MI-2D is reviewed and merged, development intentionally pauses for the requested break.
No MI-2E, MI-2F, provider expansion, protected evaluation, promotion, paper submission, or live
trading work should begin as part of this development push.
