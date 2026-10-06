# Axiom Quantum Phase 2 Slice 2 - Deterministic Resampling Risk Evidence

Status: Active implementation

Linear: `RIC-26`

## Purpose

Slice 2 adds deterministic empirical bootstrap evidence over a finite simple-return path. The output
helps Phase 2 quantify sensitivity of cumulative return and maximum drawdown to return-path
resampling. It is research evidence only: empirical bootstrap frequencies are not guaranteed future
probabilities and do not authorize sizing, execution, or strategy promotion.

## Methods

Two methods are supported:

- `iid_bootstrap`: draw individual source returns with replacement;
- `moving_block_bootstrap`: draw contiguous source blocks with replacement and truncate the final
  sample to the original horizon.

Every study uses an explicit seed, sample count, and drawdown-breach threshold. Moving-block studies
also require an explicit block size. IID studies reject a block size rather than silently ignoring
it.

## Source Return Contract

The source path must:

- contain at least two simple returns;
- contain only finite numeric values;
- contain no return below `-1.0` because a non-levered simple return cannot lose more than 100%;
- use a block size no larger than the source length for moving-block studies.

The normalized source path is bound into evidence by a SHA-256 checksum over the source schema and
ordered returns.

## Canonical Evidence

`CanonicalResamplingEvidence` records:

- source return count and checksum;
- complete deterministic resampling configuration;
- min / 5th percentile / median / 95th percentile / max of compounded cumulative return;
- the same five-number percentile summary for maximum drawdown;
- empirical frequency of a resampled path ending with negative cumulative return;
- empirical frequency of maximum drawdown meeting or exceeding the configured threshold.

Percentiles use deterministic linear interpolation over the ordered finite sample. Frequencies are
bounded in `[0, 1]` and deliberately named `frequency`, not `probability`.

## Research-Only Adapter

`resampling_evidence_to_axiom_result` converts canonical evidence into an `ExperimentResult` fixed to
`StrategyResearchState.RESEARCH_ONLY`. It cannot create a `validation_candidate` and carries no
execution authority. The conclusion explicitly states that empirical resampling frequencies are not
guaranteed future probabilities.

## Non-Goals

Slice 2 does not provide:

- automatic validation verdicts;
- hidden/default financial thresholds;
- stochastic guarantees about future returns;
- position sizing or capital allocation;
- protected final-test access;
- shadow, paper, broker, or live execution;
- new dependencies.

## Acceptance Gates

Slice 2 is complete only when:

- identical source/config/seed inputs produce identical evidence;
- invalid source returns and method configurations fail closed;
- moving-block samples preserve contiguous source blocks;
- distribution summaries are finite and monotonically ordered;
- empirical frequencies remain in `[0, 1]`;
- the result adapter remains `research_only`;
- public research-package exports are available;
- Ruff, format, Mypy, full pytest/coverage, and whitespace checks pass;
- Betterleaks passes;
- a genuine ready-for-review CodeRabbit pass has no unresolved actionable finding.
