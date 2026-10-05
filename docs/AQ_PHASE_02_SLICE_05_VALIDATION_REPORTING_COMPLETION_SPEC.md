# Axiom Quantum Phase 2 Slice 5 - Validation Reporting and Completion

Status: Final Phase 2 implementation slice

Linear: `RIC-29`

## Purpose

Slice 5 closes the Axiom Quantum Phase 2 Validation Engine by joining the already-merged validation
case, resampling evidence, gate engine, validation memory, and Strategy Graveyard into one
reproducible research-only workflow. The slice adds reporting and orchestration only; it does not
create a new validation rule, strategy, model, execution environment, or protected-evaluation path.

## Canonical Workflow

The end-to-end workflow is:

`ValidationCase + source ExperimentResult + ValidationPolicy + bound robustness/resampling evidence`

`-> evaluate_validation_case`

`-> append-only ValidationDecisionRecord`

`-> rejected only: append-only StrategyGraveyardEntry`

`-> deterministic validation report`

Every object remains linked by content-addressed IDs and the validation case remains fixed at
`execution_authority = "none"`.

## Validation Report Contract

A validation report is a deterministic Markdown rendering over the exact inputs that reproduce one
`ValidationDecision`. Before rendering, the implementation must re-evaluate the supplied case,
source result, policy, robustness evidence, and resampling evidence and require exact equality with
the supplied decision.

The report records:

- validation, decision, experiment, and result identities;
- policy ID and exact policy digest;
- the canonical evidence manifest including source kinds, source IDs, and SHA-256 checksums;
- the source-result research state and metric snapshot;
- supplied robustness summaries and their declared metric directions;
- supplied resampling configuration, source checksum, empirical frequencies, and distributions;
- one ordered gate result for every required Phase 2 validation stage;
- the terminal research verdict;
- Strategy Graveyard state for rejected candidates only;
- the explicit research-only authority boundary.

A report cannot be rendered from a substituted/stale decision or from a graveyard entry that differs
from `build_strategy_graveyard_entry(decision)`.

## Append-Only Persistence

Validation reports are stored beneath the existing safe ignored research-artifact root using the
canonical filename:

`axiom_validation_report_<aq-decision-id>.md`

The report artifact exposes the linked validation and decision IDs, safe relative path, and SHA-256
checksum. Repeating the exact same workflow is idempotent. Existing conflicting content is rejected
by the append-only `ResearchArtifactStore`.

## Verdict Persistence Rules

### Validated research candidate

- decision is recorded in validation memory;
- no Strategy Graveyard entry is created;
- report states `validated_research_candidate`;
- execution authority remains `none`.

### Rejected

- decision is recorded first;
- one deterministic Strategy Graveyard entry is recorded and re-loaded;
- the report must contain the exact linked graveyard entry;
- failed-gate evidence remains queryable;
- execution authority remains `none`.

### Insufficient evidence

- decision is recorded in validation memory;
- no graveyard entry is created because missing evidence is not a rejection;
- the report explicitly retains `insufficient_evidence` and the missing gate path;
- missing evidence is never represented as a pass;
- execution authority remains `none`.

## Non-Goals and Safety Boundary

Slice 5 does **not** authorize or implement:

- protected final-test access;
- shadow or paper execution;
- broker order submission or cancellation;
- live capital allocation;
- position sizing;
- signal-session scheduling;
- autonomous strategy/model promotion;
- lowering validation thresholds to force a pass;
- new strategy or model research.

A `validated_research_candidate` is still a research classification only.

## Acceptance Gates

Slice 5 is complete only when:

- validated, rejected, and insufficient-evidence workflows are covered end-to-end;
- every workflow records a canonical append-only validation decision;
- rejected decisions alone receive a canonical graveyard entry;
- insufficient evidence remains explicitly non-passing;
- reports reproduce the full ordered validation gate path and evidence lineage;
- report input substitution fails closed;
- report filenames are content-addressed by validation decision identity;
- report checksums are verified after persistence;
- all outputs retain `execution_authority = "none"`;
- public research-package exports are available;
- Ruff check and format check pass;
- Mypy passes for `src` and `tests`;
- full pytest coverage gate passes;
- diff whitespace checks pass;
- Betterleaks passes;
- CodeRabbit performs a genuine ready-for-review pass with no unresolved actionable findings.
