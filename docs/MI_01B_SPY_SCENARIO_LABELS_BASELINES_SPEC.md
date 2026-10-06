# Market Intelligence MI-1B SPY Scenario Labels and Baselines Specification

Status: Owner-authorized research implementation slice

Implementation branch: `review/market-intelligence-mi1b-scenario-labels-baselines`

Base commit: `9581bda1ec918eb3bbc4e4045c92277e307536d9`

Repository package/runtime version: `2.0.0b1`

Release behavior: No version bump and no release tag are authorized by MI-1B.

## 1. Purpose

MI-1B defines the first objective historical outcome labels for the MI-1 SPY scenario schema
and establishes naive probability baselines that later statistical models must beat.

This slice is research-only. It intentionally uses future prices to label already-completed
historical observations and therefore must not be used as runtime market state, evidence, or
execution input.

The governing principle remains:

**AI interprets. Data measures. Statistics test. Risk controls constrain. Evidence decides.
Humans remain responsible.**

## 2. Relationship to Existing Work

MI-1B builds on:

- MI-0 generic intelligence contracts;
- MI-1 SPY analysis profile and three-outcome scenario schema;
- MI-1A deterministic SPY market-state derivation;
- existing validated historical SPY adjusted OHLCV data.

MI-1B does not reuse the older Phase 4 binary t+1-entry/t+6-exit net-profit label as scenario
ground truth. That label answers a trading-policy question. MI-1B labels answer a market
outcome question: how far SPY's adjusted close moved over the stated analysis horizon.

## 3. Authorized Scope

MI-1B may add:

- immutable research contracts for historical SPY scenario labels;
- deterministic 5-session and 20-session forward adjusted-close returns;
- fixed version-1 downside/range/upside categorization bands;
- deterministic uniform, empirical-prior, and majority-class naive baselines;
- explicit fit cutoffs that prevent baseline statistics from using labels whose outcomes were
  not yet observable by the fit cutoff;
- synthetic/offline tests for labeling, lineage, lookahead containment, baseline fitting, and
  execution isolation.

## 4. Explicit Non-Goals

MI-1B does not authorize or implement:

- model training or inference;
- feature selection or hyperparameter search;
- probability calibration fitting;
- performance threshold optimization;
- protected evaluation;
- candidate promotion;
- historical analogue search;
- new data providers or network access;
- QQQ, IWM, VIX, Treasury, FRED, macro, news, or fundamentals data;
- scenario actionability decisions;
- risk approval;
- scheduling;
- broker communication;
- paper-order submission;
- live trading;
- new dependencies;
- package version changes;
- release tags.

## 5. Scenario Label Definition

For a historical anchor session `t` and horizon `h` in `{5, 20}` trading sessions:

`forward_return(t, h) = adjusted_close(t + h) / adjusted_close(t) - 1`

The label describes the market outcome from the anchor close to the horizon close. It is not
an executable strategy return, does not assume an order at the anchor close, and does not
include transaction costs or slippage.

The existing validated adjusted-close series is the only price input required by MI-1B.

## 6. Version-1 Outcome Bands

The version-1 categorization policy is frozen as follows:

| Horizon | DOWNSIDE | RANGE | UPSIDE |
| --- | --- | --- | --- |
| 5 sessions | return < -1.00% | -1.00% <= return <= +1.00% | return > +1.00% |
| 20 sessions | return < -2.00% | -2.00% <= return <= +2.00% | return > +2.00% |

The bands are deliberately simple, symmetric, monotonic with horizon, and not selected by
maximizing model accuracy or trading performance. They are research categorization constants,
not claims about economically optimal move sizes.

Changing these bands requires a new scenario-label schema/version. Version-1 historical labels
must never be silently rewritten after later modeling or protected evaluation results are known.

## 7. Label Availability and Lookahead Boundary

A historical label is only knowable after its `outcome_session` has completed.

Therefore:

- the label builder may use future rows because it is an offline research transformation;
- the generated label must record both anchor session and outcome session;
- rows without a complete future horizon are excluded;
- label artifacts must never be inserted into runtime MI-1 evidence or market-state snapshots;
- a training/baseline fit cutoff may use only labels whose `outcome_session` is on or before the
  cutoff.

This distinction is mandatory. The anchor session is not the label's availability session.

## 8. Label Artifact Contract

A scenario label records at least:

- anchor session;
- outcome session;
- analysis horizon;
- finite forward adjusted-close return;
- one `ScenarioOutcome` value.

A scenario label set records at least:

- scenario schema identifier;
- source market-data checksum/schema version;
- analysis horizon;
- version-1 range band;
- ordered immutable labels;
- excluded trailing-row count equal to the horizon length;
- artifact creation timestamp.

The builder must not mutate the source `MarketDataBatch`.

## 9. Naive Baselines

MI-1B establishes three deliberately simple probability baselines.

### Uniform

Always assigns one-third probability to each outcome.

### Empirical prior

Uses the observed class frequencies from eligible fitting labels only.

### Majority class

Assigns probability 1.0 to the most frequent fitting class and 0.0 to the others. Ties are
resolved deterministically by the canonical `ScenarioOutcome` order.

These are baselines, not approved models and not actionable forecasts.

## 10. Baseline Fit Cutoff

Every fitted baseline receives an explicit `fit_through_session`.

Only labels satisfying:

`label.outcome_session <= fit_through_session`

may contribute to empirical frequencies or majority selection.

This prevents a common leakage error where a label anchored before the cutoff is included even
though its future outcome occurs after the cutoff.

The resulting baseline artifact records its fit row count and fitting-session boundary.

## 11. Execution Isolation

MI-1B research code must not import or invoke:

- Alpaca trading clients;
- execution services;
- `paper_ops` submission paths;
- risk-approval bypasses;
- broker credentials;
- schedulers or daemons.

`UPSIDE`, `RANGE`, and `DOWNSIDE` remain research outcomes. None is equivalent to BUY, SELL,
LONG, CASH, an order, or permission to trade.

## 12. Dependency Policy

No new third-party dependency is authorized. Use the standard library and already-approved
project dependencies/contracts.

## 13. Testing Requirements

Tests must cover at least:

- exact 5-session and 20-session horizon construction;
- fixed version-1 range bands and boundary behavior;
- deterministic close-to-close return calculation;
- exclusion of trailing rows without a complete future horizon;
- finite-return validation;
- source checksum propagation;
- source dataframe immutability;
- independence from open/high/low/volume changes when closes are unchanged;
- rejection of unsupported horizons;
- rejection of insufficient source history;
- artifact creation-time sanity;
- deterministic uniform baseline probabilities;
- empirical-prior probabilities from eligible fitting labels only;
- majority-class one-hot probabilities and deterministic tie handling;
- fit-cutoff exclusion of labels whose outcome session is after the cutoff;
- no execution/broker imports.

Full repository quality gates remain required before merge.

## 14. Existing Gates Preserved

- Phase 3 remains `NO CANDIDATE PROMOTION`.
- Protected evaluation remains unexecuted.
- P5-B broker paper submission remains blocked.
- P5-C remains `BLOCKED_NO_APPROVED_PAPER_MODEL`.
- Live trading remains prohibited.
- Package/runtime version remains `2.0.0b1`.

## 15. Acceptance Criteria

MI-1B is complete when:

1. this governing specification exists;
2. version-1 SPY 5/20-session scenario labels are deterministic and auditable;
3. label availability is tied to the future outcome session, not the anchor session;
4. uniform, empirical-prior, and majority-class baselines exist;
5. fitted baselines exclude unavailable future outcomes by construction;
6. no training, calibration fitting, protected evaluation, provider, execution, or live-trading
   capability is added;
7. existing safety gates remain unchanged;
8. full repository quality gates pass.

## 16. Next Slice

After MI-1B is reviewed and merged, the next MI-1 research slice may define development-only
multiclass evaluation metrics and walk-forward baseline evaluation for the frozen scenario
labels. Statistical candidate models must remain a later separately reviewed step.