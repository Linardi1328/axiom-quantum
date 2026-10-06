# Market Intelligence MI-2 Contextual Market Intelligence Specification

Status: Owner-authorized implementation

Implementation branch: `review/market-intelligence-mi2-context-foundation`

Initial implementation base: `61c2d3d8226df15544b1fac342ec7f5eceb6242f`

Current validation base after the Phase 1 coverage-gate correction: `dade6d178b2f229298641887b06a287082862d25`

Package/runtime version: `2.0.0b1`

## 1. Purpose

MI-2 begins the first contextual expansion after approved Market Intelligence Phase 1.
SPY remains the analyzed instrument. The purpose of MI-2 is to determine whether a small,
pre-declared contextual set adds reliable decision value beyond SPY-only information without
weakening chronology, auditability, abstention, protected-evaluation, or execution-isolation
rules.

The initial contextual set is the one already declared by the MI-1 analysis profile:

- QQQ daily context;
- IWM daily context;
- VIX daily context;
- U.S. 10-year yield daily context.

The governing principle remains:

**AI interprets. Data measures. Statistics test. Risk controls constrain. Evidence decides.
Humans remain responsible.**

MI-2 is a research/decision-support phase. It does not authorize trading.

## 2. Relationship to Phase 1 and Existing Execution Work

Phase 1 implementation is approved on `main`, while its scientific status remains
`PENDING_PROTECTED_EVALUATION` until a separate eligible real protected evaluation is
explicitly authorized and executed.

MI-2 must not reinterpret Phase 1 implementation approval as model promotion.

The existing execution boundary remains unchanged:

- Phase 3 historical model status remains `NO CANDIDATE PROMOTION` unless a separate future
  evidence decision changes it;
- P5-B broker paper submission remains blocked pending separate owner authorization;
- P5-C model-connected paper operation remains `BLOCKED_NO_APPROVED_PAPER_MODEL`;
- live-money trading remains prohibited;
- MI-2 outputs cannot authorize risk, orders, paper submission, broker calls, or live trading.

## 3. MI-2 Research Question

The primary MI-2 question is:

> Does a small, point-in-time contextual set consisting of QQQ, IWM, VIX, and the U.S. 10-year
> yield add reproducible out-of-sample information to SPY scenario analysis beyond the frozen
> SPY-only baseline, after controlling for chronology, calibration, selectivity, and multiple
> comparisons?

A negative or inconclusive answer is an acceptable and useful result.

## 4. Phase Structure

### MI-2A — Context contracts and readiness

The first authorized implementation slice adds deterministic metadata and readiness contracts
for the pre-declared contextual set.

MI-2A may add:

- fixed context-series definitions and semantic roles;
- explicit transform-kind metadata distinguishing price levels, volatility-index levels, and
  yield levels;
- a deterministic SPY context-bundle readiness assessment;
- canonical ordering of the required contextual set;
- fail-closed completeness, quality, and point-in-time availability checks;
- synthetic/offline tests and static execution-isolation checks.

MI-2A does **not** calculate a predictive feature from VIX or yields merely because the data is
present. Different data types require separately reviewed transformation rules.

### MI-2B — Transform-specific contextual features

A later reviewable slice may define deterministic, causal contextual measurements. It must
avoid unit-incoherent calculations such as treating a Treasury yield level as though it were
an equity price series. Candidate measurements may include:

- QQQ and IWM trailing returns and SPY-relative strength;
- VIX level, change, percentile, and/or standardized state using only prior information;
- U.S. 10-year yield level and basis-point change using an explicitly declared unit contract;
- rolling relationships whose input transformations are appropriate to each series type.

Every transformation must have lineage, an availability cutoff, a frozen formula, and tests
against look-ahead.

### MI-2C — Contextual candidate and ablation

A later slice may compare a deliberately simple SPY+context candidate with the frozen SPY-only
candidate. The contextual candidate must be evaluated on identical chronological folds and
outcomes wherever possible.

Required comparisons include:

- SPY-only baseline candidate;
- SPY + QQQ/IWM only;
- SPY + VIX only;
- SPY + rates only;
- SPY + the full approved contextual set.

The purpose is to measure incremental information value, not to maximize an unrestricted
leaderboard.

### MI-2D — Calibration, abstention, and robustness

If a contextual candidate survives MI-2C, calibration and selectivity must be re-estimated on
development data only. Thresholds must be frozen before any MI-2 protected comparison.
Robustness should include the interpretable Phase 1 trend/volatility regimes and context
availability stress tests.

### MI-2E — Protected comparison

Any protected MI-2 comparison is a separate scientific event requiring a frozen policy bundle
and explicit authorization. Protected data must never be used to choose contextual features,
thresholds, transforms, or model hyperparameters.

### MI-2F — Contextual brief and acceptance

A final MI-2 brief may expose contextual measurements, material changes, missing inputs,
conflicting evidence, calibration, selectivity, degradation, and explicit limitations.
Implementation acceptance and scientific-evidence status remain separate.

## 5. Fixed MI-2A Context Definitions

The MI-2A contextual set is fixed to the existing MI-1 profile identifiers:

| Series | Semantic role | Transform kind |
| --- | --- | --- |
| `qqq-daily` | large-cap growth context | price level |
| `iwm-daily` | small-cap participation context | price level |
| `vix-daily` | equity-volatility context | volatility-index level |
| `us-10y-yield-daily` | U.S. long-rate context | yield level |

These roles are analysis metadata, not claims of stable causal relationships.

MI-2A must reject undeclared context identifiers rather than silently expanding scope.

## 6. Readiness Rule

A context bundle is eligible for complete-context analysis only when:

1. the target series is the approved legacy SPY daily series;
2. the target snapshot is `VERIFIED`;
3. the target snapshot was available by the requested analysis `as_of` timestamp;
4. all four required context identifiers are present exactly once;
5. every context snapshot is `VERIFIED`;
6. every context snapshot was available by `as_of`.

Missing context is represented explicitly as `INCOMPLETE`. Unverified or future-available data
is `INELIGIBLE`. No missing or invalid series may be filled with an invented value.

## 7. Point-in-Time and Data Semantics

MI-2 preserves the Phase 1 chronology rules.

- `as_of` must be timezone-aware.
- A snapshot with `available_as_of > as_of` is not eligible.
- Revisions must not silently replace historical values that were not knowable at the
  historical cutoff.
- Context-series ordering is canonical and independent of caller input ordering.
- Unknown context identifiers fail closed.
- Duplicate context identifiers fail closed.

Future provider work must separately specify retrieval timestamps, observation timestamps,
availability timestamps, provider identity, unit conventions, revisions, and stale-data rules.

## 8. Explicit Non-Goals for MI-2A

MI-2A does not authorize or implement:

- new market-data downloads;
- Alpaca acquisition changes;
- FRED, Treasury, CBOE, news, fundamentals, or other external provider calls;
- automatic scheduling;
- LLM or external AI API calls;
- model training or inference changes;
- contextual feature fitting;
- hyperparameter search;
- learned regime models;
- individual-stock expansion;
- gold, commodity, or FX expansion;
- broker communication;
- paper-order submission;
- risk-approval bypasses;
- live trading;
- package version bump;
- release tag;
- new third-party dependencies.

## 9. Testing Requirements for MI-2A

Tests must cover at least:

1. fixed context definitions match the existing MI-1 analysis profile;
2. deterministic canonical context ordering;
3. complete verified bundle eligibility;
4. explicit incomplete status for missing context;
5. fail-closed handling of unverified context;
6. fail-closed handling of future-available snapshots;
7. rejection of unknown context identifiers;
8. rejection of duplicate context identifiers;
9. rejection of a non-SPY target series;
10. timezone-aware `as_of` enforcement;
11. static isolation from broker, paper-operation, execution, credentials, scheduler, and
    network-provider paths;
12. full repository quality gates before merge.

## 10. MI-2A Acceptance Criteria

MI-2A is complete when:

1. this specification exists on the implementation branch;
2. fixed context-series definitions exist and exactly match the MI-1 profile;
3. the readiness assessment is deterministic, immutable, point-in-time aware, and fail-closed;
4. no provider, model, execution, scheduler, or trading behavior is added;
5. package/runtime version remains `2.0.0b1`;
6. no release tag is created;
7. full repository quality gates pass;
8. final review finds no blocking issue.

MI-2A completion authorizes only the next reviewable MI-2 slice. It does not claim contextual
predictive value and does not authorize protected evaluation or trading.
