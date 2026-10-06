# Market Intelligence MI-2B Context Transform Specification

Status: Owner-authorized implementation slice

Implementation branch: `review/market-intelligence-mi2b-context-transforms`

Base commit: `f021b3700a360c175b7a98fa74087695e68dea9a`

Package/runtime version: `2.0.0b1`

## 1. Purpose

MI-2A established a fail-closed readiness contract for the fixed SPY contextual set. MI-2B
adds the first deterministic, transform-specific contextual measurements for that already
approved set. It does not fit or promote a model and does not claim that the measurements add
predictive value.

The fixed contextual set remains:

- QQQ daily context;
- IWM daily context;
- VIX daily context;
- U.S. 10-year yield daily context.

The governing principle remains:

**AI interprets. Data measures. Statistics test. Risk controls constrain. Evidence decides.
Humans remain responsible.**

## 2. Scientific Boundary

MI-2B answers only this implementation question:

> Can the approved contextual inputs be transformed into deterministic, unit-coherent,
> point-in-time measurements with explicit lineage and no look-ahead beyond the SPY anchor
> session?

MI-2B does not answer whether the measurements improve forecasting, calibration, trading
performance, or risk-adjusted outcomes. Those questions belong to later MI-2 ablation and
protected-comparison stages.

A measurement existing in MI-2B is not evidence that it is useful.

## 3. Preconditions

Context features may be derived only when the MI-2A readiness assessment is
`VERIFIED_COMPLETE` and eligible for complete-context analysis.

The derivation must therefore fail closed when:

- the target is not the approved legacy SPY daily series;
- any required context series is missing;
- any target or context snapshot is not `VERIFIED`;
- any target or context snapshot was not available by the requested `as_of` timestamp;
- duplicate or undeclared context series are supplied.

MI-2B additionally requires every context series to contain the SPY anchor session explicitly.
No forward-fill or invented value is permitted.

## 4. Fixed Transform Policy

### 4.1 QQQ and IWM price-level context

Price-context returns use only sessions shared with SPY and ending on the SPY anchor session.
Selected price levels must be strictly positive.

For each of QQQ and IWM:

- 5-session trailing return:
  `P_t / P_{t-5} - 1`;
- 20-session trailing return:
  `P_t / P_{t-20} - 1`;
- 5-session SPY-relative strength:
  `context_return_5 - SPY_return_5` over the same aligned sessions;
- 20-session SPY-relative strength:
  `context_return_20 - SPY_return_20` over the same aligned sessions.

Units are fractions. Relative strength is descriptive, not a stable causal claim.

### 4.2 VIX volatility-index context

VIX is treated as an index level, not an equity price.

Measurements are:

- current VIX level in index points;
- 5-session absolute VIX change in index points;
- 60-observation trailing empirical percentile in `[0, 1]`, calculated as the fraction of
  trailing observations less than or equal to the current value.

The percentile window includes the anchor observation and uses only observations at or before
the SPY anchor session.

### 4.3 U.S. 10-year yield context

The yield input contract for MI-2B is **percentage points**, for example `4.25` for 4.25%.
The yield series must not be transformed as an equity price return.

Measurements are:

- current yield level in percentage points;
- 5-session absolute yield change in basis points;
- 20-session absolute yield change in basis points.

Basis-point change is calculated as:

`(yield_t - yield_{t-k}) * 100`

because one percentage point equals 100 basis points.

Negative yield levels are not rejected solely for being negative; the unit contract, finiteness,
and chronology are the relevant invariants.

## 5. Fixed Feature Set

MI-2B freezes the following 14 measurements in canonical order:

1. `qqq_return_5`
2. `qqq_return_20`
3. `qqq_relative_strength_5`
4. `qqq_relative_strength_20`
5. `iwm_return_5`
6. `iwm_return_20`
7. `iwm_relative_strength_5`
8. `iwm_relative_strength_20`
9. `vix_level`
10. `vix_change_5`
11. `vix_percentile_60`
12. `us10y_yield_level`
13. `us10y_yield_change_5bp`
14. `us10y_yield_change_20bp`

Changing this set, formula, lookback, unit, or methodology identifier requires a separately
reviewed policy revision rather than silent mutation.

## 6. Point-in-Time and Look-Ahead Rules

- `as_of` must be timezone-aware.
- MI-2A snapshot-availability checks remain mandatory.
- The SPY target's final session is the MI-2B anchor session.
- The SPY anchor session date must not be after the `as_of` date.
- Every context must contain that anchor session exactly; stale carry-forward is prohibited.
- Observations after the anchor session must never enter a feature calculation, even if the
  supplied snapshot contains later rows.
- QQQ/IWM return and relative-strength windows use only SPY/context shared sessions.
- VIX and yield trailing windows use only their own observations at or before the anchor.
- Insufficient trailing history fails closed rather than shortening a declared lookback.

Provider-specific revision semantics remain a later provider concern. MI-2B creates no network
provider behavior.

## 7. Lineage Contract

Every derived feature must record at least:

- the MI-2B feature policy identifier;
- fixed feature and methodology identifiers;
- source context series identifier;
- source context snapshot identifier;
- SPY target snapshot identifier;
- anchor session;
- normalized `as_of` timestamp;
- declared lookback;
- declared unit;
- finite numeric value.

The bundle must expose all 14 features in canonical order and the four context snapshot IDs in
the MI-2A canonical context order. Each feature's source snapshot must match the corresponding
context snapshot recorded by the bundle.

## 8. Explicit Non-Goals

MI-2B does not authorize or implement:

- new market-data downloads;
- Alpaca acquisition changes;
- FRED, Treasury, CBOE, news, fundamentals, or other external provider calls;
- scheduler or daemon behavior;
- LLM or external AI API calls;
- contextual candidate fitting;
- model training or inference changes;
- calibration fitting;
- selectivity-threshold search;
- hyperparameter search;
- regime-model expansion;
- protected evaluation;
- individual-stock, FX, metal, or commodity expansion;
- broker communication;
- paper-order submission;
- risk-approval bypasses;
- live trading;
- package version bump;
- release tag;
- new third-party dependencies.

## 9. Testing Requirements

Tests must cover at least:

1. the fixed 14-feature definition set and canonical order;
2. exact transform kinds, lookbacks, and units;
3. deterministic QQQ/IWM trailing returns;
4. deterministic SPY-relative strength on aligned sessions;
5. VIX level, change, and empirical percentile;
6. yield level and basis-point changes;
7. observations after the SPY anchor do not affect values;
8. rejection of an anchor session after `as_of`;
9. rejection of a context missing the SPY anchor session;
10. rejection of insufficient declared lookback history;
11. rejection of non-positive selected price levels;
12. MI-2A readiness failures block feature derivation;
13. immutable feature and bundle lineage invariants, including source snapshot consistency;
14. static isolation from provider, broker, execution, paper-operation, credential, and
    scheduler paths;
15. the full repository quality gate, including `pytest --cov-fail-under=85`.

## 10. Acceptance Criteria

MI-2B is implementation-complete only when:

1. this specification exists on the implementation branch;
2. the 14-feature transform policy is deterministic and frozen;
3. feature lineage is explicit and immutable;
4. chronology and anchor-session rules fail closed;
5. formulas are unit-coherent for price, volatility-index, and yield inputs;
6. no provider, model, execution, scheduler, broker, or trading behavior is added;
7. package/runtime version remains `2.0.0b1`;
8. no release tag is created;
9. the full repository quality gate passes;
10. final review finds no blocking issue.

MI-2B acceptance authorizes only the next reviewable MI-2 slice. It does not establish
incremental predictive value and does not authorize any protected evaluation or trading.
