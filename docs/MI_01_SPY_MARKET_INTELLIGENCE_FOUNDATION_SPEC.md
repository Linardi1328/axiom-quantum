# Market Intelligence MI-1 SPY Foundation Specification

Status: Owner-authorized implementation foundation

Implementation branch: `review/market-intelligence-mi1-foundation`

Base commit: `e66593b769647f6a12f8a72af6ecd3f561c6e75d`

Repository package/runtime version: `2.0.0b1`

Release behavior: No version bump and no release tag are authorized by MI-1 foundation work.

## 1. Purpose

MI-1 begins the first runtime-capable Market Intelligence layer on top of the accepted MI-0
contracts. The initial analyzed instrument remains SPY. The purpose of this foundation slice
is to establish deterministic, auditable representations for market state, evidence,
scenario probabilities, and actionability/abstention before any new statistical model or
external contextual data provider is introduced.

The governing principle remains:

**AI interprets. Data measures. Statistics test. Risk controls constrain. Evidence decides.
Humans remain responsible.**

## 2. Relationship to Existing Work

MI-1 does not modify the frozen Version 1 trading behavior and does not supersede Version 2
Phase 5 execution gates.

- Phase 3 remains `NO CANDIDATE PROMOTION`.
- Protected evaluation remains unexecuted.
- P5-B broker paper submission remains blocked pending separate owner authorization.
- P5-C model-connected paper operation remains `BLOCKED_NO_APPROVED_PAPER_MODEL`.
- Live trading remains prohibited.

MI-1 intelligence outputs are research/decision-support artifacts only. They are not trading
signals, risk approvals, order approvals, or broker instructions.

## 3. Authorized Foundation Scope

This MI-1 foundation may add:

- a fixed SPY Market Intelligence analysis profile;
- 5-session and 20-session analysis horizons;
- contextual-series identifiers for QQQ, IWM, VIX, and selected U.S. rate context;
- immutable market-state dimension and snapshot contracts;
- immutable evidence-item contracts with point-in-time availability metadata;
- immutable three-way scenario probability contracts: `DOWNSIDE`, `RANGE`, `UPSIDE`;
- explicit calibration-status metadata;
- a deterministic actionability policy that may return either a selected scenario or
  `ABSTAIN`;
- explicit abstention reason codes;
- read-only evidence-reference compatibility with the existing AI analyst boundary;
- synthetic/offline unit tests and execution-isolation tests.

## 4. Explicit Non-Goals

This foundation slice does not authorize or implement:

- new market-data downloads;
- QQQ, IWM, VIX, Treasury, FRED, macro, news, or fundamentals network access;
- model training;
- model inference;
- probability calibration fitting;
- historical analogue search;
- cross-asset statistical calculations;
- learned regime models;
- automatic scheduling;
- API/dashboard write controls;
- broker communication;
- paper-order submission;
- paper or live position sizing;
- any execution permission;
- any live-money trading support;
- a new dependency;
- a package/runtime version bump;
- a release tag.

## 5. SPY MI-1 Analysis Profile

The initial profile is deliberately narrow:

- target: SPY;
- horizons: 5 trading sessions and 20 trading sessions;
- feature-family identifiers: trend, volatility, drawdown, relative strength, rates;
- context-series identifiers: QQQ daily, IWM daily, VIX daily, U.S. 10-year yield daily;
- scenario schema: downside/range/upside version 1.

The profile records intended inputs only. It does not imply those contextual datasets have
already been acquired, validated, or approved for historical research.

## 6. Market-State Contract

A market-state snapshot is a deterministic collection of named dimensions for one
`IntelligenceRunIdentity`.

Each dimension records:

- stable dimension identifier;
- human-readable label;
- optional finite numeric value;
- optional unit;
- evidence references supporting the measurement.

Examples of future dimensions include trend, realized volatility, drawdown, participation,
relative strength, or rate pressure. MI-1 foundation does not hard-code a single semantic
market-regime label as ground truth.

Duplicate dimension identifiers are invalid. Unknown/unavailable measurements must be
represented explicitly rather than invented.

## 7. Evidence Contract

Every quantitative or qualitative evidence item must be traceable.

An evidence item records:

- stable evidence identifier;
- source-series or source-method identifier;
- methodology identifier;
- observation timestamp;
- point-in-time availability timestamp;
- concise summary;
- optional finite numeric value;
- optional finite standardized value.

An item cannot be available before it was observed. Future macro/event work may require a
more specific release/vintage contract; this foundation does not pretend otherwise.

The existing read-only AI analyst may reference evidence identifiers, but it remains unable
to originate authoritative measurements or change execution/risk state.

## 8. Scenario Contract

A scenario forecast contains exactly three mutually exclusive outcomes:

- `DOWNSIDE`;
- `RANGE`;
- `UPSIDE`.

Probabilities must be finite, individually lie in `[0, 1]`, and sum to one within a strict
floating-point tolerance. A forecast also records its analysis horizon, calibration status,
and supporting evidence references.

MI-1 foundation validates probabilities but does not generate them from a statistical model.

## 9. Actionability and Abstention

Actionability is not a trading decision.

The deterministic policy may select the highest-probability scenario only when all required
conditions pass. Otherwise it returns `ABSTAIN` with explicit reasons.

Foundation checks include:

1. data quality must be `VERIFIED` and eligible;
2. calibration status must be acceptable when calibration is required;
3. the top scenario probability must meet a configured minimum;
4. the gap between the highest and second-highest probabilities must meet a configured
   minimum separation.

Default foundation thresholds are intentionally conservative examples for contract testing,
not statistically validated production thresholds:

- minimum top probability: `0.60`;
- minimum top-vs-second probability separation: `0.15`.

These values must not be marketed as optimized or historically validated. Future research
must select/freeze thresholds using development-only evidence before protected evaluation.

Supported abstention reasons include at least:

- low data quality;
- calibration not acceptable;
- low scenario confidence;
- low scenario separation.

## 10. Execution Isolation

Nothing in `spy_market_agent.intelligence` may authorize or invoke execution.

MI-1 foundation modules must not import or construct:

- Alpaca trading clients;
- execution services;
- paper adapters;
- `paper_ops` submission paths;
- risk approval bypasses;
- broker credentials;
- schedulers or daemons.

The output `UPSIDE` is a scenario label. It is not equivalent to `BUY`, `LONG`, an order,
or permission to trade.

## 11. Dependency Policy

No new third-party dependency is authorized in this slice.

Use Python standard-library contracts and already-approved project-owned MI-0 contracts.

## 12. Testing Requirements

Tests must cover at least:

- the fixed SPY MI-1 profile and 5/20-session horizons;
- deterministic normalization and duplicate rejection for market-state dimensions;
- finite numeric-value validation;
- point-in-time evidence availability validation;
- exact three-scenario membership;
- probability range and sum-to-one validation;
- deterministic scenario ordering;
- verified data-quality requirement;
- calibration refusal;
- low-confidence abstention;
- low-separation abstention;
- actionable high-evidence scenario selection;
- evidence-reference propagation;
- static execution/broker isolation.

Full repository quality gates remain required before merge.

## 13. Acceptance Criteria

MI-1 foundation is complete when:

1. this governing specification exists;
2. SPY has a fixed 5/20-session MI analysis profile;
3. immutable state, evidence, scenario, and actionability contracts exist;
4. scenario actionability fails closed to `ABSTAIN` when any required gate fails;
5. no new model, provider, network, scheduler, risk-approval, or execution path is added;
6. the existing AI analyst remains read-only and evidence-bound;
7. P5-B/P5-C and live-trading prohibitions remain unchanged;
8. package/runtime version remains `2.0.0b1`;
9. no release tag is created;
10. full repository quality gates pass.

## 14. Next MI-1 Slice

After this foundation is reviewed, the next MI-1 slice may connect deterministic SPY
measurements from already-approved historical data to market-state dimensions and begin a
separately specified research implementation for scenario labels/baselines. Contextual data
providers and cross-asset calculations remain separate reviewable additions.