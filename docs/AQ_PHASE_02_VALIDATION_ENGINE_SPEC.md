# Axiom Quantum Phase 2 - Validation Engine Specification

Status: Active - Slice 1 implementation

Linear issues: `RIC-25` through `RIC-29`

## Purpose

Phase 2 turns the completed Phase 1 Research Core into a governed validation engine. It does not
replace Phase 1 evidence generation and it does not authorize shadow, paper, broker, or live
execution. Its job is to determine whether a research candidate has enough consistent evidence to
remain a validated research candidate, must be rejected, or still lacks required evidence.

The Phase 2 sequence is:

`hypothesis -> backtest -> walk-forward -> out-of-sample -> cost/slippage stress -> regime stress -> parameter sensitivity -> bootstrap/resampling -> risk -> candidate decision`

Every step must remain evidence-linked and reproducible. Missing evidence must never be silently
interpreted as a pass.

## Existing Baseline Reused From Phase 1

Phase 2 must reuse rather than duplicate the following merged Phase 1 capabilities:

- canonical `ExperimentDefinition` and `ExperimentResult` contracts;
- deterministic experiment/result identities;
- walk-forward and out-of-sample Phase-3 adapters;
- canonical backtest evidence;
- canonical robustness evidence for cost, regime, parameter, and walk-forward perturbations;
- append-only Axiom Research Memory;
- reproducible research reports;
- research-memory migration and provenance preservation.

## Phase 2 Implementation Slices

1. **Validation Case and Evidence Contract (`RIC-25`)**
   - canonical Phase 2 validation stages;
   - deterministic validation case identity;
   - checksum-bound evidence references;
   - validation can start only from a Phase 1 `validation_candidate` result;
   - execution authority remains `none`.
2. **Deterministic Resampling Risk Evidence (`RIC-26`)**
   - IID and moving-block bootstrap research evidence;
   - deterministic seeded resampling;
   - loss and drawdown-threshold breach frequencies;
   - return/drawdown distribution summaries;
   - no claim that empirical resampling frequencies are guaranteed future probabilities.
3. **Validation Gate and Verdict Engine (`RIC-27`)**
   - explicit caller-defined validation policy;
   - structural stage-completeness gates;
   - result-metric, robustness, and resampling gates;
   - deterministic verdict: `validated_research_candidate`, `rejected`, or
     `insufficient_evidence`.
4. **Strategy Graveyard and Validation Memory (`RIC-28`)**
   - append-only validation decision records;
   - deterministic Strategy Graveyard entries for rejected candidates;
   - failure reasons and failed-gate references remain queryable evidence.
5. **Validation Reporting and Completion (`RIC-29`)**
   - deterministic human-auditable validation report;
   - end-to-end decision persistence workflow;
   - integration coverage for validated, rejected, and insufficient evidence paths;
   - final Phase 2 completion contract.

## Slice 1 Contract

### Validation stages

The canonical ordered evidence stages are:

1. `hypothesis`
2. `backtest`
3. `walk_forward`
4. `out_of_sample`
5. `cost_stress`
6. `regime_stress`
7. `parameter_sensitivity`
8. `resampling`
9. `risk`

`candidate_decision` is an output stage and therefore cannot be supplied as an input evidence
reference.

### Validation evidence references

Each evidence reference records:

- one canonical validation stage;
- a source kind;
- a path-safe evidence identifier;
- a path-safe source identifier;
- a lowercase SHA-256 checksum.

Multiple evidence references may exist for a stage, but the `(stage, evidence_id)` pair must be
unique. Evidence is stored in deterministic stage order.

### Validation case identity

A validation case is content-addressed as:

`aq-validation-<24 lowercase hex characters>`

Its identity includes the parent experiment, parent result, policy identifier, source research
state, evidence manifest, and the hard-coded execution-authority boundary. Reordering equivalent
input evidence cannot change the identity.

### Candidate admission

`build_validation_case` accepts only an `ExperimentResult` whose research state is
`validation_candidate`. A `research_only`, `rejected`, or `retired` result cannot enter Phase 2
through this contract.

A validation case may be created with incomplete evidence so the later gate engine can distinguish
`insufficient_evidence` from `rejected`. The case exposes deterministic `evidenced_stages` and
`missing_stages` views; missing stages are not passes.

## Safety Boundary

Phase 2 explicitly does **not** provide:

- protected final-test access;
- automatic paper/shadow/live promotion;
- signal-session scheduling;
- broker order submission or cancellation;
- position sizing or capital allocation;
- autonomous model/strategy self-promotion;
- permission to lower evidence thresholds to force a candidate through validation.

All Phase 2 contracts retain `execution_authority = "none"`.

## Slice 1 Acceptance Gates

Slice 1 is complete only when:

- validation case identity is deterministic and content-addressed;
- evidence references fail closed on unsafe identifiers, invalid checksums, duplicate keys, or
  decision-stage misuse;
- equivalent evidence supplied in different order produces the same canonical case;
- only `validation_candidate` source results can create a validation case;
- incomplete evidence remains explicitly discoverable through `missing_stages`;
- execution authority cannot be changed from `none`;
- public research-package exports are available;
- Ruff check and format check pass;
- Mypy passes for `src` and `tests`;
- full pytest coverage gate passes;
- diff whitespace checks pass;
- Betterleaks passes;
- CodeRabbit performs a genuine ready-for-review pass with no unresolved actionable findings.

Later slices remain unauthorized until their preceding slice is squash-merged into `main`.
