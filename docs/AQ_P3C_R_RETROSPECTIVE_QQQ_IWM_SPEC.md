# AQ-P3C-R — Retrospective QQQ/IWM incremental-signal study (RIC-183)

Status: reviewed specification with owner amendments, plus the offline computation
core. This is **exploratory research only**. It is not strict point-in-time (PIT)
admission, not real-data acquisition or fit authorization, and not model promotion.
Every output retains:

```text
execution_authority = none
disposition = NO CANDIDATE PROMOTION
```

Phase 3A/3B/3C-A/3C-B, MI-2A/MI-2B, the v2 acquisition verifier and the Phase 2
exclusion contract are unchanged. A separate retrospective path is introduced beside
them; none of their eligibility requirements is relaxed.

## Question

Does QQQ/IWM context add predictive value beyond the SPY-only baseline, measured with
the unchanged Phase 3C-B comparison protocol on data retrieved retrospectively?

## Frozen SPY parent and development-only population

- Accepted Phase 3B parent: dataset `spy-v2p1-900ca75371bf05e25b1935b5`, canonical
  content SHA-256 `3fbaed8976d4aa1bc8d9029c955aa663b61acbea1be30bcf7a5e730a0b99df6c`,
  parent campaign `spy-v2p3-dev-1f9a68f3dce8df02e3645468`. The older Alpha 3 dataset
  `spy-v2p1-825930b0…` is **not interchangeable**.
- The parent *market-data* checksum is deliberately **not** hard-coded. It comes from the
  independently verified accepted local campaign boundary during a later metadata-only
  preflight. The mechanically derived boundary must equal the accepted campaign
  `phase2_final_test_exclusion_boundary` exactly (same rule as Phase 3B).
- `apply_phase2_final_test_session_isolation` runs with 60-session warm-up **before** any
  feature or label code. Protected source rows are only used for parent integrity checks
  and mechanical exclusion, never for features, labels or assessment.
- Asserted frozen population: 1,708 source sessions (2018-01-02 – 2024-10-15), 1,642
  predictions (2018-03-29 – 2024-10-07), seven disjoint folds with assessment sizes
  126, 126, 126, 126, 126, 126, 88; 844 assessed predictions; zero unassessed rows.

## Retrospective QQQ/IWM source eligibility (`aq-p3cr-retrospective-eligibility-v1`)

- Two series only (QQQ, IWM), v2 context acquisition (`alpaca`, SIP, `1Day`, `all`).
- A series exists only if built by `bind_context_series(manifest, canonical_bytes)`. The
  closes are parsed from the bytes; caller arrays, verification booleans and rights
  claims are not accepted. The binder re-checks the manifest self-checksum, canonical
  byte checksum, strict canonical re-serialization, OHLC sanity, XNYS session
  continuity, manifest/session agreement and the lineage-bound dataset ID.
- Context sessions must equal the eligible SPY source sessions **exactly**. Extra
  (protected) observations are rejected, so protected context is never processed.
- `data_class = retrospective_corrected`; the manifest's `point_in_time_status` and
  `usage_rights_status` stay `UNVERIFIED` and are copied unchanged into lineage.
- Owner entitlement evidence is a separate `OwnerRightsEvidence` reference bound to both
  dataset IDs and canonical checksums. It never rewrites manifest flags.
- No availability timestamp is invented. Synthetic proofs/snapshots are rejected by this
  path, and Phase 3C-A/MI-2B still reject `retrospective_corrected` evidence.
- The binder checks internal consistency only. It does **not** replace
  `verify_context_artifacts` (exact code/runtime lineage), which the future local adapter
  must run first; that requirement is not weakened.

## Features, models, folds, costs

- 16 SPY columns (`BASELINE_COLUMNS`) plus the eight existing columns
  `{qqq,iwm}_return_{5,20}` and `{qqq,iwm}_relative_strength_{5,20}`; values use
  closes at or before day *t* only (same arithmetic as Phase 3C-A).
- Five models with the exact Phase 3C-B definitions: training prevalence;
  `logistic_regression_research_00`; `logistic_regression`; `gradient_boosting`; and the
  24-column candidate (research_00: StandardScaler fit on training rows only, L2,
  C = 0.1, liblinear, no class weights, seed 42). A fresh estimator per model/fold.
  No extra trials, features, thresholds, calibration or weighting.
- Existing target (enter *t+1* open, exit *t+6* open, positive only if net return > 0),
  costs 0.125 bps commission + 0.25 bps slippage per side, `construct_disjoint_folds`
  (756 initial training rows, 6 purge, 126 assessment, final window ≥ 63), strict exit
  chronology and identical populations for all five models. Threshold 0.5 is descriptive.

## Preregistered exploratory screen (`aq-p3cr-criteria-v1`)

Unrounded finite metrics, no tolerance; all criteria must hold for `CRITERIA_MET`.

1. Candidate AUC defined in ≥ 5 folds.
2. Candidate median AUC ≥ 0.53 (over matched folds).
3. Strongest fitted comparator = highest median AUC among research_00, logistic and
   gradient boosting (ties: that registry order; training prevalence excluded).
   Median of per-fold paired differences (candidate − comparator), over folds where all
   four AUCs are defined, needs ≥ 5 pairs and must be ≥ 0.03.
4. All seven finite log-loss and Brier observations required for candidate and
   prevalence (otherwise *insufficient* → fail); median log loss **or** median Brier
   strictly lower than training prevalence.
5. At least two of the last three folds (indices 4–6) have candidate AUC > 0.5.
6. Predicted-positive rate at 0.5 within [0.05, 0.95] inclusive in each of those folds.

Recall is descriptive only. All undefined metrics and reasons are reported, together with
paired-AUC variants against every control and the raw per-fold metrics. A favorable screen
only supports future independent research; an unfavorable one retires this hypothesis.

## Limitations (carried in every result)

Data retrieved after the fact with all later adjustments; availability at each cutoff,
as-of corporate-action history, same-day extended-hours inclusion and usage rights are
not established. Folds are chronologically dependent, not independent trials. The SPY
population was already examined in Phase 3B. `drawdown_20d` and `distance_to_high_20d`
are the identical formula and each relative-strength column is a linear function of a
return column and SPY's return, so the candidate is linearly dependent and its larger,
redundant matrix alters L2 regularization geometry. **Improvement therefore cannot be
attributed purely to incremental information.**

## Identity and reproducibility

`aq-p3cr-retro-<sha256>` binds the study/criteria/configuration IDs, parent identity and
boundary, both context dataset IDs and canonical/manifest checksums, the owner rights
reference, schemas, exact model definitions and columns, exact fold membership, Git SHA,
Python/dependency versions and checksums of all transitive sources (including
`context_features.py`, reporting, isolation and criteria logic). No wall-clock time,
absolute path, credential or host identity is included. Bitwise reproduction is tested.

## Implementation boundary

The evaluator `_evaluate_retrospective_study` is private. There is **no** public
real-input runner, loader, CLI, writer, scheduler or persisted artifact; output is
in-memory canonical JSON. Tests use independently generated synthetic fixtures only and
never real market data, credentials or the network.

## Unresolved real-data prerequisites (separate owner authorization)

1. A reviewed local adapter that runs `verify_context_artifacts` (exact code/runtime
   lineage), verifies the Phase 3B campaign and obtains the accepted boundary.
2. Bounded one-time QQQ/IWM capture approval, with explicit local rights/entitlement
   confirmation, for exactly the 1,708 eligible sessions.
3. Approval of where sanitized local metrics may be written.
4. One offline exploratory fit, then stop.
