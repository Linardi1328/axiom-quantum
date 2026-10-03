# Axiom Quantum Phase 1 - Legacy Research Evidence Bridge Specification

Status: Active - Slice 2

Linear issue: `RIC-8`

## 1. Purpose

This slice connects the repository's existing Version 2 Phase 3 development research evidence to
the canonical Axiom Quantum Research Core introduced in Phase 1 Slice 1.

The bridge is deliberately one-way and explicit. It does not change the legacy walk-forward
research algorithms, protected-evaluation controls, strategy behavior, paper systems, shadow
systems, broker adapters, or execution authority. It translates already validated development
research objects into canonical Axiom experiment and result records so old evidence can become part
of the new append-only research memory.

## 2. Source Evidence

The authorized source contracts are:

- `DatasetLineage`;
- `RuntimeLineage`;
- `ExperimentManifest`;
- `CandidateEvaluationSummary`;
- `CandidateSelectionResult`.

The bridge only accepts legacy Phase 3 development evidence. Protected evaluation evidence is
outside this slice and fails closed.

## 3. Canonical Experiment Mapping

A legacy `ExperimentManifest` maps to one Axiom `ExperimentDefinition`.

The canonical definition preserves:

- the source dataset ID and SHA-256 checksum;
- feature and label schema identities;
- source session bounds;
- enabled feature families;
- runtime Git/package/Python/dependency lineage;
- exact legacy model configuration through a content-addressed model ID;
- exact walk-forward and evaluation methodology through a content-addressed evaluation protocol;
- strategy assumptions through a content-addressed strategy ID when present;
- cost assumptions through a content-addressed cost-model ID when present;
- predeclared metric names;
- the source legacy experiment identity.

The bridge does not infer or grant any execution authority. Canonical
`execution_authority` remains `none`.

## 4. Scientific Identity

Legacy model, strategy, cost, and evaluation identities are derived from canonical JSON hashes
rather than human labels.

The evaluation-protocol hash includes:

- Phase identifier;
- dataset provenance;
- feature registry;
- forecast horizon;
- fold-policy ID;
- exact fold boundaries;
- hyperparameter-search definition;
- calibration policy;
- threshold policy;
- random seeds;
- baseline definitions;
- candidate-selection rule and configuration.

This prevents two materially different legacy experiments from collapsing into the same Axiom
identity merely because they share a model name or dataset.

Before mapping, the bridge recomputes the legacy Phase 3 experiment identity. A source manifest with
an ID that no longer matches its content is rejected.

## 5. Candidate Result Mapping

A legacy candidate summary and selection result map to one Axiom `ExperimentResult`.

A candidate is considered valid bridgeable development evidence only when:

- `valid` is true;
- `leaky` is false;
- `lineage_complete` is true.

State mapping is:

| Legacy evidence | Axiom lifecycle | Axiom outcome | Axiom strategy state |
| --- | --- | --- | --- |
| invalid, leaky, or incomplete | rejected | rejected | rejected |
| valid, not promoted | completed | inconclusive | research_only |
| valid, selected, promotion allowed | completed | completed | validation_candidate |

`validation_candidate` is the maximum state this bridge can produce. It is not shadow approval,
paper approval, live approval, or proof of profitability.

## 6. Metric Mapping

The result snapshot preserves:

- evidence-validity flags;
- valid-fold count;
- simplicity rank;
- whether this candidate was selected;
- whether legacy promotion was allowed;
- defined legacy summary metrics.

A legacy metric with an undefined value is omitted rather than replaced with zero, NaN, an invented
estimate, or another fabricated value.

## 7. Research Memory

`record_phase3_candidate_evidence` performs the explicit storage flow:

1. revalidate legacy source objects;
2. verify legacy experiment identity;
3. reject protected evidence;
4. construct the canonical Axiom experiment definition;
5. register the experiment through `ResearchMemoryRegistry`;
6. construct the canonical candidate result;
7. record the result through `ResearchMemoryRegistry`.

Existing append-only and idempotency rules from Slice 1 remain authoritative.

Historical Phase 3 artifacts are not moved, rewritten, or deleted.

## 8. Explicit Non-Goals

This slice does not authorize:

- protected-evaluation import;
- automatic runner side effects;
- automatic migration of every historical artifact;
- changes to walk-forward folds or candidate selection;
- strategy execution;
- broker submission or cancellation;
- paper or shadow promotion;
- live capital;
- multi-asset execution;
- dependency changes.

## 9. Acceptance Gates

This slice is acceptable only when:

1. equivalent valid legacy manifests generate the same canonical Axiom experiment identity;
2. scientific legacy changes generate a different canonical identity;
3. stale/tampered legacy experiment identities fail closed;
4. protected legacy evidence fails closed;
5. legacy promotion maps to no state above `validation_candidate`;
6. valid non-promoted candidates remain `research_only`;
7. invalid/leaky/incomplete evidence maps to `rejected`;
8. undefined metrics are omitted safely;
9. repeated recording of identical evidence is idempotent;
10. legacy research and execution behavior remains unchanged;
11. repository Ruff, format, Mypy, pytest/coverage, security, and review gates pass.

## 10. Follow-On Work

After this bridge is accepted, later Phase 1 slices can:

- connect additional legacy campaign artifacts and evidence references;
- standardize strategy/backtest evidence into the same canonical memory model;
- expand robustness and performance metrics;
- generate reproducible Axiom research reports;
- expose read-only research-memory inspection surfaces.

Those additions remain research-only until separately authorized roadmap gates are satisfied.
