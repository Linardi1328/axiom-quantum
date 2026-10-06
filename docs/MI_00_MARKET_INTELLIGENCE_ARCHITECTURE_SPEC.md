# Market Intelligence MI-0 Architecture Foundation Specification

Status: Owner-authorized architecture foundation

Implementation branch: `review/market-intelligence-mi0`

Repository package/runtime version: `2.0.0b1`

Release behavior: No version bump and no release tag are authorized by MI-0.

## 1. Purpose

MI-0 begins the approved strategic evolution from a primarily SPY prediction and
paper-trading research project toward a broader, uncertainty-aware Market Intelligence &
Research System.

MI-0 is architecture-only foundation work. It introduces generic intelligence contracts
beside the frozen SPY contracts so future Market Intelligence phases can support additional
instruments and contextual series without rewriting Version 1 or weakening Version 2 safety
boundaries.

The governing principle is:

**AI interprets. Data measures. Statistics test. Risk controls constrain. Evidence decides.
Humans remain responsible.**

## 2. Relationship to Existing Version 1 and Version 2 Work

MI-0 does not supersede `PROJECT_SPEC.md`, the released Version 1 baseline, or the active
Version 2 Phase 5 paper-operation work.

Version 1 remains SPY-only, daily, long-or-cash, and paper-only.

Version 2 Phase 5 remains unchanged:

- P5-A infrastructure entry remains authorized under its existing specification.
- P5-B broker paper submission remains blocked pending separate owner authorization.
- P5-C model-connected paper operation remains `BLOCKED_NO_APPROVED_PAPER_MODEL`.
- Phase 3 remains `NO CANDIDATE PROMOTION`.
- Protected evaluation remains unexecuted.
- Live trading remains prohibited.

No MI-0 object, field, profile, adapter, identifier, boolean, metadata value, or configuration
value may authorize P5-B, P5-C, paper submission, or live execution.

## 3. Authorized Scope

MI-0 may add:

- this governing specification;
- a new `spy_market_agent.intelligence` package;
- immutable generic `InstrumentProfile` contracts;
- generic analysis-horizon and `AnalysisProfile` contracts;
- immutable `SeriesSnapshot` lineage metadata;
- fail-closed `DataQualityDecision` contracts;
- deterministic intelligence-run identity contracts;
- deterministic series-snapshot identity helpers;
- a read-only compatibility adapter from the existing SPY `MarketDataBatch` contract into
  generic MI-0 snapshot metadata;
- synthetic/offline unit tests;
- static execution-isolation tests.

## 4. Explicit Non-Goals

MI-0 does not authorize or implement:

- model training;
- model inference;
- scenario probability generation;
- regime classification;
- historical-analogue search;
- cross-asset statistics;
- macro/event acquisition;
- news acquisition or LLM calls;
- automatic scheduling;
- API or dashboard write controls;
- broker clients;
- broker account, position, order, or reconciliation calls;
- paper-order submission;
- cancellation or resubmission;
- changes to Version 1 execution behavior;
- changes to Phase 5 paper-operation gates;
- real-money execution;
- live trading;
- new dependencies;
- network access from the intelligence package;
- a package/runtime version bump;
- release-tag creation.

In short:

- No broker calls.
- No paper-order submission.
- No live trading.
- No model inference.
- No network access.
- No version bump.

## 5. Architecture Strategy

MI-0 uses incremental compatibility rather than a rewrite.

```text
Existing frozen SPY contracts
        |
        v
Legacy SPY compatibility adapter
        |
        v
Generic MI-0 contracts
        |
        +--> future MI-1 market state
        +--> future MI-1 scenarios
        +--> future MI-1 analogues
        +--> future MI-1 reporting

---------------- execution boundary ----------------

Existing strategy/risk/execution/paper systems
```

The existing `spy_market_agent.market_data`, `features`, `datasets`, `research`, `shadow`,
`risk`, `execution`, and `paper_ops` packages remain authoritative for their existing
approved behavior.

MI-0 must not change those contracts merely to make them generic.

## 6. Contract Separation

### 6.1 InstrumentProfile

`InstrumentProfile` describes market identity and observation-session semantics only.

It may describe, for example, an equity ETF or a future FX instrument without carrying
execution permission.

It deliberately does not include:

- broker identity;
- broker credentials;
- order type;
- position sizing;
- risk approval;
- execution permission.

### 6.2 AnalysisProfile

`AnalysisProfile` describes an analysis configuration:

- target instrument;
- analysis horizons;
- feature-family identifiers;
- contextual series identifiers;
- scenario-schema identifier;
- explicit profile/version identity.

It does not represent a trading strategy or order policy.

### 6.3 SeriesSnapshot

`SeriesSnapshot` is lineage metadata for one immutable source-data state.

It records:

- snapshot identity;
- series identity;
- provider;
- schema version;
- retrieval timestamp;
- point-in-time availability cutoff;
- first and last observation identifiers;
- row count;
- canonical checksum;
- data-quality status.

MI-0 intentionally keeps observation identifiers opaque strings. Their semantics are owned
by the future series profile/data contract so the intelligence layer does not assume every
future series uses XNYS `date` keys.

### 6.4 DataQualityDecision

A data-quality decision fails closed.

`VERIFIED` is the only status that may be analysis eligible.

`LOW_QUALITY`, `UNAVAILABLE`, and `UNKNOWN` must all be ineligible and must carry an
explanatory reason.

### 6.5 IntelligenceRunIdentity

An intelligence-run identity is derived deterministically from stable inputs:

- target instrument;
- `as_of` timestamp;
- analysis profile identity;
- sorted immutable input snapshot identities;
- code revision;
- configuration hash.

Clock time not supplied as the explicit `as_of`, local filesystem paths, usernames,
hostnames, random UUIDs, and process IDs must not define run identity.

## 7. Point-in-Time Rule

MI-0 introduces the vocabulary needed for future point-in-time analysis but does not yet
acquire new data.

Every future intelligence input must distinguish at least:

- when the source observation describes;
- when the system retrieved it;
- when it was actually available for analysis.

Future macro/event work must additionally preserve release vintages and revisions where
required.

A value revised after a historical analysis cutoff must never silently replace the value
that was knowable at that cutoff.

## 8. Legacy SPY Adapter

The MI-0 compatibility adapter may read an already validated `MarketDataBatch` and expose
its metadata as a generic `SeriesSnapshot`.

The adapter must:

- preserve the existing canonical checksum;
- preserve provider identity;
- preserve first/last session identity;
- preserve row count;
- preserve existing retrieval/creation timestamps;
- avoid copying or modifying market-data rows;
- avoid market-data acquisition;
- avoid filesystem writes;
- avoid network access;
- avoid model, risk, shadow, execution, or paper imports.

The adapter does not make SPY generic internally. It creates a boundary around the frozen
SPY contract.

## 9. Safety and Isolation

`spy_market_agent.intelligence` must remain independent from broker and execution
capabilities.

MI-0 intelligence modules must not import or construct:

- Alpaca trading clients;
- Version 1 execution services;
- the Alpaca paper adapter;
- Phase 5 `paper_ops`;
- order-submission functions;
- trading settings or credentials.

Unknown or invalid contract state must raise rather than silently invent defaults.

## 10. Dependency Policy

MI-0 adds no third-party dependency.

The generic contract layer uses Python standard-library types.

The legacy adapter may import existing project-owned market-data contracts only.

## 11. Testing Requirements

MI-0 tests must cover:

- non-SPY instrument profiles;
- exchange-calendar requirements;
- analysis-horizon validation;
- deterministic profile ordering/normalization;
- point-in-time timestamp validation;
- SHA-256 validation;
- fail-closed data-quality decisions;
- deterministic snapshot identity;
- deterministic run identity independent of caller snapshot ordering;
- legacy SPY lineage preservation;
- static execution/broker isolation;
- specification safety boundaries.

The full existing quality gates remain required before merge:

```bash
python -m pip install -e ".[dev]"
pytest --cov-fail-under=85
pytest tests/unit -q
pytest tests/integration -q
pytest -W error::FutureWarning
ruff check .
ruff format --check .
mypy src tests
git diff --check
git status --short
```

## 12. Acceptance Criteria

MI-0 is complete when:

1. The governing MI-0 specification exists and records the strategic/safety boundary.
2. Generic immutable instrument, horizon, analysis, snapshot, quality, and run-identity
   contracts exist.
3. Generic contracts contain no SPY-only symbol or XNYS-only validation.
4. The legacy SPY adapter preserves existing lineage without modifying existing market data.
5. New intelligence modules contain no broker, order-submission, paper-operation, credential,
   network, scheduler, or live-trading capability.
6. Existing Version 1 and Version 2 runtime files do not need behavioral modification.
7. P5-B and P5-C remain blocked exactly as before.
8. Package/runtime version remains `2.0.0b1`.
9. No MI-0 release tag is created.
10. Full repository quality gates pass.

## 13. Future MI-1 Boundary

MI-0 does not itself authorize MI-1 implementation.

A future MI-1 specification should separately govern the first SPY Market Intelligence
runtime, expected to consider SPY as the analyzed asset with a deliberately small contextual
set such as QQQ, IWM, VIX, and selected U.S. rate series.

Candidate MI-1 features such as market-state dimensions, scenario probabilities,
calibration, abstention, historical analogues, cross-asset relationships, model-degradation
monitoring, and human-readable reporting remain future work until separately approved.
