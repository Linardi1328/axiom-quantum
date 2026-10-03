# Axiom Quantum Phase 1 - Research Core Foundation Specification

Status: Active - first implementation slice

Linear issue: `RIC-7`

Implementation branch: `richblinardi/ric-7-axiom-quantum-phase-1-research-core-foundation`

Authorized on: 2026-10-04

## 1. Purpose

Axiom Quantum Phase 1 begins the transition from the historical SPY Market Agent roadmap to a
phase-agnostic quantitative research platform. This slice does not replace the existing SPY,
Market Intelligence, shadow, paper, risk, or execution systems. It creates a canonical research
contract above them so future experiments can accumulate as reproducible evidence rather than as
one-off scripts or phase-specific artifacts.

The Phase 1 objective is a trustworthy research core before broader assets, additional models, or
higher execution autonomy are considered.

## 2. Existing Baseline

The repository already contains substantial research infrastructure:

- verified SPY historical-data lineage and manifests;
- deterministic feature engineering and labels;
- chronological train/validation/final-test controls;
- walk-forward research and protected-evaluation isolation;
- transaction-cost and slippage-aware backtesting;
- Market Intelligence MI-1 and MI-2 research;
- phase-specific experiment/model registry scaffolding;
- shadow observation infrastructure;
- paper-operation safety and recovery scaffolding.

Phase 1 therefore consolidates existing capabilities instead of rebuilding them.

The frozen Version 1 behavior and all existing Version 2 safety boundaries remain unchanged.
The current Python package name `spy_market_agent` also remains unchanged in this slice to avoid a
large compatibility migration mixed with research-core work.

## 3. Authorized Scope For This Slice

This implementation may add:

- a phase-agnostic canonical experiment definition;
- deterministic Axiom experiment and result identities;
- explicit experiment lifecycle states;
- explicit research-only strategy states;
- immutable dataset and runtime-lineage references;
- immutable experiment-result records;
- evidence references with checksums;
- an append-only research-memory registry built on the existing ignored research artifact store;
- read/checksum/list helpers for that existing artifact store;
- focused unit tests and public research-package exports.

## 4. Explicit Non-Goals

This slice does not authorize or implement:

- live trading;
- broker submission or cancellation;
- additional paper-order authority;
- model-connected paper operation;
- shadow-model promotion;
- protected evaluation;
- model promotion beyond a research `validation_candidate` state;
- autonomous scheduling;
- market-data acquisition;
- new asset execution;
- portfolio allocation;
- API or dashboard write controls;
- new dependencies;
- repository or Python-package renaming;
- profitability claims.

The new canonical experiment contract has `execution_authority = "none"` as its only legal value.

## 5. Core Contract

A canonical `ExperimentDefinition` records the scientific identity of an experiment:

- human-readable name;
- falsifiable hypothesis and research question;
- asset universe;
- immutable dataset references and checksums;
- feature families;
- strategy and model identifiers;
- evaluation protocol;
- transaction-cost model identifier;
- predeclared primary metrics;
- Git/runtime/dependency lineage;
- optional non-scientific tags and operator notes.

Scientific identity is content-addressed. Changing scientific lineage or methodology changes the
experiment ID. Operator notes do not change scientific identity.

The first identity format is:

`aq-exp-<24 hex characters>`

## 6. Experiment And Strategy States

Experiment lifecycle vocabulary is:

- `planned`;
- `running`;
- `completed`;
- `rejected`;
- `archived`.

Strategy research vocabulary is deliberately capped at:

- `research_only`;
- `validation_candidate`;
- `rejected`;
- `retired`.

`validation_candidate` is not shadow approval, paper approval, live approval, or proof of edge.
Later roadmap phases must introduce separate admission artifacts and gates if those capabilities are
ever authorized.

## 7. Immutable Results

An `ExperimentResult` records a completed research conclusion with:

- parent experiment identity;
- terminal experiment state;
- outcome;
- research strategy state;
- summary and conclusion;
- finite metric snapshot;
- optional evidence references;
- UTC completion timestamp.

Result identity is content-addressed as:

`aq-result-<24 hex characters>`

A changed conclusion, metric snapshot, evidence set, or completion timestamp becomes a new result
record instead of overwriting the prior result.

## 8. Research Memory

`ResearchMemoryRegistry` stores canonical records beneath the existing ignored
`artifacts/research/` root.

Each Axiom experiment directory contains:

- `axiom_experiment.json` - canonical experiment record;
- zero or more `axiom_result_<result-id>.json` immutable result records.

Writes are append-only by default. Re-writing identical content is idempotent. Conflicting content
at an existing path fails closed. Corrupt JSON, identity mismatch, unknown parent experiments, and
unsafe paths fail closed.

Generated research memory remains local/ignored unless a later specification explicitly defines a
sanitized evidence-publication flow.

## 9. Compatibility Boundary

This slice must not change behavior in:

- `spy_market_agent.execution`;
- `spy_market_agent.paper_ops`;
- `spy_market_agent.shadow`;
- `spy_market_agent.risk`;
- existing MI-1/MI-2 research logic;
- existing Version 1 or Version 2 experiment manifests;
- broker adapters or credentials;
- protected final-test handling.

The new Axiom contracts coexist with the historical phase-specific research contracts. Migration of
old experiment artifacts may be designed later; old evidence must not be silently rewritten.

## 10. Acceptance Gates

This slice is acceptable only when:

1. canonical identities are deterministic;
2. scientific-lineage changes alter experiment identity;
3. non-scientific notes do not alter experiment identity;
4. execution authority cannot be enabled through the experiment contract;
5. duplicate/unsafe identifiers and secret-bearing notes are rejected;
6. non-finite metrics are rejected;
7. `validation_candidate` requires a completed research outcome;
8. research-memory writes are immutable and idempotent;
9. corrupt or mismatched stored records fail closed;
10. focused tests pass;
11. repository Ruff, format, Mypy, pytest/coverage, and diff checks pass in the real repository CI;
12. no existing execution, paper, shadow, broker, or protected-evaluation behavior changes.

## 11. Follow-On Phase 1 Slices

After this foundation is accepted, Phase 1 can incrementally standardize the existing strategy and
backtest interfaces, connect existing walk-forward/OOS evidence to the canonical experiment
contract, formalize richer performance/robustness metrics, generate reproducible research reports,
and migrate selected existing research outputs into the new memory format without rewriting their
historical evidence.

Those follow-on slices remain research-only until separate roadmap gates explicitly authorize a
higher environment.
