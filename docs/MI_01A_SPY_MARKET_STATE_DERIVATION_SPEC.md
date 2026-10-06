# Market Intelligence MI-1A SPY Market-State Derivation Specification

Status: Owner-authorized MI-1 continuation

Implementation branch: `review/market-intelligence-mi1-spy-state-derivation`

Base commit: `964631d3f94dd02f8ba377bfd6ff25dff51bccf3`

Repository package/runtime version: `2.0.0b1`

Release behavior: No version bump and no release tag are authorized by this slice.

## 1. Purpose

MI-1A connects the already-approved historical SPY data and leakage-safe trailing feature
contracts to the MI-1 market-state and evidence contracts merged in PR #35.

This slice produces deterministic research/decision-support measurements only. It does not
produce a statistical scenario forecast, trading signal, risk approval, order approval, or
broker instruction.

The governing principle remains:

**AI interprets. Data measures. Statistics test. Risk controls constrain. Evidence decides.
Humans remain responsible.**

## 2. Relationship to Existing Work

MI-1A reuses, without changing, the accepted SPY contracts:

- validated adjusted daily `MarketDataBatch`;
- leakage-safe trailing `FeatureSet`;
- MI-0 `SeriesSnapshot` and `IntelligenceRunIdentity`;
- MI-1 `EvidenceItem`, `MarketStateDimension`, and `MarketStateSnapshot`.

MI-1A does not change the frozen Version 1 runtime or Version 2 execution posture.

- Phase 3 remains `NO CANDIDATE PROMOTION`.
- Protected evaluation remains unexecuted.
- P5-B broker paper submission remains blocked pending separate owner authorization.
- P5-C model-connected paper operation remains `BLOCKED_NO_APPROVED_PAPER_MODEL`.
- Live trading remains prohibited.

## 3. Authorized Scope

This slice may add:

- a deterministic SPY state-derivation function inside `spy_market_agent.intelligence`;
- lineage and as-of validation between `MarketDataBatch`, `FeatureSet`, the legacy SPY
  `SeriesSnapshot`, and `IntelligenceRunIdentity`;
- current/latest-row SPY trend measurements using the existing 5-session and 20-session
  trailing feature values;
- current/latest-row SPY realized-volatility measurements using the existing 5-session and
  20-session trailing feature values;
- current drawdown from the running peak of the validated adjusted close series;
- immutable evidence items for every available quantitative dimension;
- explicit `UNAVAILABLE` state dimensions for relative strength and rates until their
  separately reviewed contextual datasets exist;
- deterministic/offline tests for formulas, lineage, temporal isolation, and execution
  isolation.

## 4. Explicit Non-Goals

MI-1A does not authorize or implement:

- new market-data downloads or providers;
- QQQ, IWM, VIX, Treasury, FRED, macro, news, or fundamentals network access;
- cross-asset calculations;
- relative-strength calculation;
- rates-pressure calculation;
- scenario-label generation;
- scenario probability generation;
- model training or inference;
- probability calibration fitting;
- learned regime models;
- historical analogue search;
- threshold optimization;
- strategy optimization;
- protected evaluation;
- shadow-model admission;
- automatic scheduling;
- API/dashboard write controls;
- broker communication;
- paper-order submission;
- live trading;
- new dependencies;
- package/runtime version changes;
- release tags.

## 5. Input Contract

The state derivation accepts:

1. one validated adjusted daily SPY `MarketDataBatch`;
2. one leakage-safe trailing SPY `FeatureSet` derived from that same market-data checksum;
3. one `IntelligenceRunIdentity` for the fixed MI-1 SPY profile.

The function must fail closed when:

- the target instrument is not SPY;
- the analysis profile is not the fixed MI-1 SPY profile;
- the feature-set market-data checksum differs from the market-data checksum;
- the feature set and market data do not end on the same session;
- the legacy SPY snapshot implied by the market-data batch is absent from the run identity;
- the source snapshot became available after the run as-of timestamp;
- the feature artifact was created after the run as-of timestamp;
- the latest session is not complete at the run as-of timestamp;
- a required latest-row numeric value is non-finite.

The function must not mutate either input dataframe.

## 6. Deterministic State Dimensions

MI-1A emits the following stable dimensions for the latest feature session:

| Dimension | Numeric value | Supporting evidence |
| --- | --- | --- |
| `trend_5` | existing `close_return_5d` | 5-session close return and 5-session close-vs-SMA |
| `trend_20` | existing `close_return_20d` | 20-session close return and 20-session close-vs-SMA |
| `volatility_5` | existing `realized_volatility_5` | existing 5-session trailing realized volatility |
| `volatility_20` | existing `realized_volatility_20` | existing 20-session trailing realized volatility |
| `drawdown_from_peak` | latest adjusted close / running peak - 1 | running-peak drawdown calculation |
| `relative_strength` | unavailable | no approved contextual comparator dataset yet |
| `rates` | unavailable | no approved rates dataset yet |

All available numeric dimensions use fraction units. The existing realized-volatility
features are preserved exactly as calculated by the accepted feature pipeline; MI-1A does
not silently annualize or rescale them.

## 7. Evidence and Timing

Each available quantitative dimension is backed by one or more `EvidenceItem` records.

Evidence identifiers are deterministic from the latest session and methodology. Evidence
uses the legacy SPY series identifier as the source and a stable methodology identifier for
the specific measurement.

For this slice, `observed_at` and `available_at` record the MI derivation as-of timestamp. The
function separately verifies that the underlying market-data snapshot and feature artifact
were already available by that timestamp. This avoids pretending that the derivation layer
knows a more precise vendor-publication timestamp than its source contracts provide.

## 8. Point-in-Time and Leakage Rules

MI-1A must preserve the repository's chronological rules:

- use only the latest feature row supplied by the accepted trailing feature pipeline;
- compute drawdown only from closes at or before the latest session;
- never access a future row, future label, future return, protected result, or model output;
- modifying rows after a historical cutoff must not change a state derived for that cutoff
  when the inputs are truncated at the cutoff;
- no centered windows or backward fill are introduced.

## 9. Contextual Data Boundary

`relative_strength` and `rates` are deliberately emitted as `UNAVAILABLE` with no invented
numeric value or evidence reference.

The declared QQQ/IWM/VIX/U.S.-10Y identifiers in the MI-1 profile remain declarations only.
A future contextual-data slice must separately specify provider, licensing, point-in-time
availability, validation, and alignment rules before those dimensions can become available.

## 10. Execution Isolation

Nothing in this slice may import, authorize, or invoke:

- Alpaca trading clients;
- execution services;
- paper adapters;
- `paper_ops` submission paths;
- risk approval bypasses;
- broker credentials;
- schedulers or daemons.

State values are measurements. Positive trend or low drawdown is not `BUY`, `LONG`, an
order, or permission to trade.

## 11. Dependency Policy

No new third-party dependency is authorized. MI-1A may use dependencies already required by
the accepted SPY data/feature contracts.

## 12. Testing Requirements

Tests must cover at least:

- successful deterministic state derivation from validated SPY market data and features;
- exact 5/20-session trend and volatility value reuse;
- exact running-peak drawdown calculation;
- deterministic evidence identifiers and evidence-reference propagation;
- explicit unavailable relative-strength and rates dimensions;
- market-data/feature checksum mismatch refusal;
- latest-session mismatch refusal;
- run snapshot mismatch refusal;
- future source-artifact availability refusal;
- incomplete-session refusal;
- no input dataframe mutation;
- point-in-time/truncated-data invariance;
- static execution/broker isolation.

Full repository quality gates remain required before merge.

## 13. Acceptance Criteria

MI-1A is complete when:

1. this governing specification exists;
2. deterministic SPY state derivation exists and is exported through the intelligence
   package boundary;
3. every available state dimension is evidence-backed;
4. relative strength and rates remain explicitly unavailable;
5. lineage and source-availability mismatches fail closed;
6. no scenario/model/provider/execution capability is added;
7. existing P5-B/P5-C and live-trading prohibitions remain unchanged;
8. package/runtime version remains `2.0.0b1`;
9. no release tag is created;
10. full repository quality gates pass.

## 14. Next Slice

After MI-1A is reviewed and merged, the next MI-1 research slice may separately specify and
implement historical three-way scenario labels and naive baselines using development-safe
historical partitions. That work must define label thresholds before any probability model
or protected evaluation is considered.
