# Phase 3B implementation and owner-run procedure

This manual, offline diagnostic is retrospective development research. It cannot promote a
model or authorize a signal, shadow inference, paper order, schedule, or live trade. The
Phase 2 final test was opened in earlier research and provides no fresh holdout here.

## Inputs and controls

- The input is the verified local Phase 1 Alpaca SIP SPY 1Day all-adjusted dataset with
  approved ID and canonical checksum. The accepted Phase 3 campaign artifact index and
  model registry must be present and pass checksum verification.
- The runner reconstructs the frozen Phase 2 final-test boundary, compares it with the
  accepted Phase 3 boundary, removes protected source sessions, and only then builds
  features and t+6 labels. It does not read final-test results or prediction artifacts.
- The config is [phase3b_nonoverlapping.json](../configs/research/phase3b_nonoverlapping.json).
  The 756/6/126/63 schedule, fixed models, feature policy, seed, classification threshold,
  and PSI policy are immutable. A change requires a separate approved spec.
- Each assessment block is disjoint by prediction session. The last t+6 label exit of a
  block must precede the next block's first prediction session. Expanding training can
  include prior assessed rows after their labels mature. Disjoint blocks are not
  statistically independent: labels overlap within blocks and training histories overlap.
- Same-window comparisons use training-only prevalence, the frozen uncalibrated research
  logistic model, and original fixed logistic and gradient-boosting definitions. All
  models use the same frozen baseline-plus-drawdown features. Only the logistic models
  scale features; scaling is fitted within each training fold.
- AUC is undefined for a one-class assessment. Three valid folds are required for
  descriptive aggregates. PSI is bin-dependent, can be unstable in sparse bins, and
  does not establish predictive concept drift. Cells below 30 rows carry small-sample
  warnings. The explored 2022-2023 and 2023-2024 comparison uses only whole folds
  within those predeclared periods and is marked unsupported if unavailable.

## Artifact contract

The runner writes under ignored `artifacts/research/phase3b/<experiment-id>/`:

| File | Content |
| --- | --- |
| `config.json` | Frozen configuration snapshot. |
| `lineage.json` | Parent dataset/campaign, protected boundary, feature/model/label/runtime identity. |
| `fold_manifest.json` | Exact ordered training, six purged, and assessment prediction sessions; training/assessment exit boundaries; explicit training-to-assessment and inter-window outcome-purge proofs; unassessed terminal rows. |
| `metrics.json` | Per-fold fixed-model metrics, same-window differences and descriptive aggregates. |
| `diagnostics.json` | Regime cells, train-derived volatility threshold, feature medians and PSI. |
| `findings.md` | Sanitized development-only limitations and NO CANDIDATE PROMOTION status. |
| `artifact_index.json` | SHA-256 checksums for every preceding file. |

The experiment identity is content-addressed from frozen input lineage, configuration,
model/feature/label schemas, exact folds, and code/runtime/dependency lineage. It excludes
clock time and absolute paths. Identical reruns are idempotent. A missing, conflicting,
tampered or symlinked artifact requires manual recovery; no file is overwritten.

## Owner-run workflow after implementation review and separate approval

Do not run this procedure as part of implementation review. After approval, on an
offline machine with the accepted local Phase 1 dataset and Phase 3 campaign artifacts:

1. Confirm the reviewed implementation commit and frozen config are checked out.
2. Confirm the parent dataset manifest and its canonical files are in the local `data/`
   store, and the accepted campaign is in `artifacts/research/`.
3. Run:

   ```bash
   .venv/bin/python -m spy_market_agent.research.cli run-phase3b \
     --manifest data/<verified-phase1-manifest>.json \
     --data-root data \
     --config configs/research/phase3b_nonoverlapping.json \
     --artifact-root artifacts/research
   ```

4. Inspect `artifact_index.json`, fold boundaries, per-fold metrics, warnings and
   `findings.md`. Record the experiment ID and checksums in the governed research review.
5. Retain the scientific decision **NO CANDIDATE PROMOTION** unless a separate governance
   process authorizes a later change. Do not feed this output to operational systems.

If an interrupted write leaves a partial directory, stop. Preserve the directory and its
index for investigation, verify original checksums against backup, and choose a new
reviewed recovery procedure. Do not delete, replace or edit the directory merely to
make a rerun pass. Real provider bars, row-level labels/probabilities and raw artifacts
must remain local and uncommitted.
