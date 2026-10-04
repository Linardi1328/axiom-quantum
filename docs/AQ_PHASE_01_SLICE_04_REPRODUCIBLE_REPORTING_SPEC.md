# Axiom Quantum Phase 1 Slice 4 - Reproducible Research Reporting

Status: Active implementation slice

Linear: `RIC-21`

## Purpose

Turn canonical Axiom experiment definitions and immutable experiment results into a deterministic,
human-auditable Markdown artifact. The report is a view over already validated research evidence;
it does not create new evidence, change a result, or grant any execution authority.

## Scope

- define report schema version `axiom-research-report-v1`;
- deterministically render one `ExperimentDefinition` and one matching `ExperimentResult`;
- include experiment/result identities, scientific definition, dataset checksums, runtime lineage,
  result state, metrics, and evidence references;
- derive report filenames from immutable result identity;
- persist reports beneath the existing ignored research-artifact root;
- use append-only/idempotent artifact-store semantics;
- reject mismatched experiment/result pairs;
- expose the reporting API through `spy_market_agent.research`.

## Report Identity

The report filename is:

`axiom_report_<result-id>.md`

where `<result-id>` is the canonical `aq-result-*` identity produced from the immutable
`ExperimentResult`. Equivalent canonical inputs therefore map to the same report path and content.
A changed result maps to a different result identity and a different report filename.

The writer returns a dedicated `ResearchReportArtifact`, not a `ResearchEvidenceRef`. A report is
derived from the immutable result, so linking that report back into the same result's evidence set
would create a recursive identity cycle. The report artifact can be indexed or referenced by later
systems without mutating its source result.

## Determinism

The renderer:

- uses canonical experiment/result models as its inputs;
- renders datasets in their canonical order;
- sorts dependency versions, metric names, and evidence references;
- formats metric values through deterministic JSON scalar formatting;
- emits a final newline;
- excludes operator notes because they are not part of scientific experiment identity.

The same canonical input pair must produce byte-identical UTF-8 Markdown.

## Artifact Semantics

Reports are written through `ResearchArtifactStore.write_bytes` with replacement disabled.

- writing identical report bytes to the same result-derived path is idempotent;
- conflicting existing bytes at that path fail closed;
- reports remain beneath the ignored `artifacts/research/` root;
- the writer returns a checksum-bearing `ResearchReportArtifact` for downstream indexing.

## Authority Boundary

A report is research evidence only. It cannot:

- promote a strategy;
- alter `StrategyResearchState`;
- authorize shadow or paper operation;
- submit or cancel broker orders;
- enable live trading;
- access protected evaluation data.

The report must explicitly state that it grants no execution authority.

## Non-Goals

This slice does not add:

- new models or strategies;
- new robustness statistics;
- new backtest accounting;
- new promotion logic;
- new market data;
- new dependencies;
- package or repository renaming.

## Acceptance

- equivalent canonical input pairs render byte-identical Markdown;
- report filenames are derived from canonical result identity;
- report artifact references do not create result-identity recursion;
- mismatched experiment/result pairs fail closed;
- operator notes are not rendered;
- metrics and evidence references have deterministic ordering;
- the report explicitly records the research-only authority boundary;
- append-only/idempotent artifact semantics are preserved;
- existing research, backtest, shadow, paper, risk, broker, and protected-evaluation behavior is
  unchanged;
- repository quality gates and review gates pass.
