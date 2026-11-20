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
- A series is only a verified `(manifest, canonical bytes)` pair built by
  `bind_context_series`. It stores no derived values: sessions, closes and identities are
  re-derived from the pair on every access and again at the computation boundary, so
  `dataclasses.replace` or attribute tampering cannot substitute values under an unchanged
  artifact identity. Caller arrays, verification booleans and rights claims are not
  accepted. Validation mirrors the v2 acquisition verifier (reusing its pure helpers):
  manifest self-checksum, canonical byte checksum, canonical decimal text and numeric
  bounds, int64 volume bounds, byte and row bounds, strict canonical re-serialization,
  OHLC sanity, exact requested XNYS session coverage, final-session completeness at
  retrieval, the 120-second retrieval bound and the lineage-bound dataset ID. The
  experiment identity also binds a checksum of the effective sessions and closes.
- Context sessions must equal the eligible SPY source sessions **exactly**. Extra
  (protected) context rows may be parsed during artifact integrity validation, but they
  are rejected before any feature generation, label construction or fitting, so protected
  context never reaches modelling. The future adapter must validate the approved date
  scope from metadata (manifest start/end dates and row count) **before** reading context
  artifacts at all.
- `data_class = retrospective_corrected`; the manifest's `point_in_time_status` and
  `usage_rights_status` stay `UNVERIFIED` and are copied unchanged into lineage.
- Owner entitlement evidence is a separate `OwnerRightsEvidence` reference bound to both
  dataset IDs and canonical checksums. It never rewrites manifest flags.
- No availability timestamp is invented. Synthetic proofs/snapshots are rejected by this
  path, and Phase 3C-A/MI-2B still reject `retrospective_corrected` evidence.
- The binder checks internal consistency only. It does **not** replace
  `verify_context_artifacts` (exact code/runtime lineage), which the future local adapter
  must run first; that requirement is not weakened.

## Parent integrity and evidence classes

- Before isolation, the evaluator recomputes the full parent market-data checksum from the
  actual rows (eligible and protected) and requires equality with the batch metadata and
  the accepted boundary, then checks the exact source (2018-01-02 – 2024-10-15) and
  prediction (2018-03-29 – 2024-10-07) dates in addition to the frozen row counts.
  Protected rows are never used for features or labels.
- The only test seam replaces the parent identity strings (`_FixtureIdentity`). It cannot
  alter costs, seed, offsets, thresholds or fold policy: all configuration comes from one
  immutable frozen mapping that is both executed and recorded. Fixture runs use evidence
  class `synthetic_fixture` and experiment namespace `aq-p3cr-synth-`; observed
  retrospective runs use `retrospective_corrected` and `aq-p3cr-retro-`. Neither class can
  satisfy strict PIT eligibility (3C-A and the MI-2B binary preflight are unchanged).

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
Python/dependency versions, the configuration file checksum and checksums of every
in-package source file reachable from the study by static import analysis (calendar,
benchmark splits/locks, feature models, research constants, reporting, isolation and
criteria logic), together with the versions of the third-party packages they import. No wall-clock time,
absolute path, credential or host identity is included. Bitwise reproduction is tested.

## Implementation boundary

The evaluator `_evaluate_retrospective_study` is private. There is **no** public
real-input runner, loader, CLI, writer, scheduler or persisted artifact; output is
in-memory canonical JSON. Tests use independently generated synthetic fixtures only and
never real market data, credentials or the network.

## Verified-data adapter (`aq-p3cr-verified-data-adapter-v1`)

An offline adapter, `research/retrospective_adapter.py`, joins verified local inputs to the
evaluator. It acquires nothing and reads no credentials. It has two entry points, each
usable **once per process** (tracked separately): `eligibility_preflight` (never fits) and
`run_authorized_experiment` (evaluates once, only under an owner authorization).

- **Frozen parent evidence:** dataset `spy-v2p1-900ca75371bf05e25b1935b5`; canonical
  content checksum `3fbaed89…99df6c`; market-data checksum `09371762…bad6c2`; manifest
  self-checksum `de296295…fee6b6` (`manifest.manifest_artifact_checksum`, not the hash of
  the complete file); campaign `spy-v2p3-dev-1f9a68f3dce8df02e3645468`. The accepted
  campaign's `artifact_index.json` (`e1934f41…5f60f`) and `experiment_manifest.json`
  (`bdab6f06…3977f`) digests are **pinned**, so a self-consistently resealed campaign is
  refused even though the existing verifier would accept it. Existing verification is
  reused unchanged (`_verify_parent_campaign`, `_load_verified_research_market_data`,
  Phase 2 isolation with exact boundary equality, `verify_context_artifacts`,
  `ContextStore`, `bind_context_series`, the evaluator).
- **Metadata gate:** the adapter first reads its own source files (runtime fingerprint and
  Git status) and loads the authorization file; neither touches context data. Both context
  manifest paths must then have the exact v2 shape
  `data/manifests/alpaca/<SYMBOL>/1Day/sip/all/aq-ctx-v2-<24 hex>.manifest.json` (right
  symbols, v2 namespace, suffix, no traversal) **before either manifest is opened**, so raw
  and canonical paths are never read. Both manifests are then parsed and checked (v2 schema,
  QQQ and IWM, Alpaca SIP 1Day all-adjusted, 2018-01-02 to 2024-10-15, 1,708 XNYS sessions,
  UNVERIFIED flags, canonical serialization, self-checksum, path identity), and **only
  after both pass** are any raw or canonical observations read (by the verifier and the
  binder).
- **Complete input set:** every file any verifier or consumer reads (parent manifest,
  canonical and raw; every file in the campaign directory; context manifest, raw and
  canonical for both symbols) must be a non-symlink owned by the current user and not
  group/other writable, and is hashed before verification, after verification, before
  fitting and after fitting. The preflight report binds a digest of that whole snapshot.
  The exact gated manifest bytes, re-read manifest and consumed canonical bytes must also
  equal what `verify_context_artifacts` verified. The Phase 3B configuration is part of the
  snapshot: real input may only use the verified default inside the pinned, clean checkout
  (an override path is refused); a fixture override is hashed and rechecked like any other
  input.
- **Runtime identity (not cryptographic attestation):** `python -I`, a clean checkout at the
  owner-pinned commit, and a fingerprint of the study and adapter source closure,
  dependency versions and Python version. The baseline is captured **when the adapter
  module is imported**; the fingerprint is recomputed at entry, at the end of preflight,
  before and after fitting, and is bound into the report digest (so into the
  authorization). Git probes ignore inherited `GIT_*` variables and force `core.fsmonitor=false` so no configured hook runs. This detects drift after
  import and version differences; it is **not** loaded-code attestation and gives no
  protection against an adversary using the same OS account, who could alter any private
  file, the interpreter or the checkout. The acquisition verifier additionally requires
  the artifacts' recorded code lineage to equal the current one, so capture must happen at
  the pinned commit.
- **Authorization and single use:** a run needs a private (0600), git-ignored, untracked,
  owner-authored JSON file binding the exact report digest, pinned commit, evidence class
  and destination, plus a rights reference. A Boolean cannot authorize. The rights
  reference is recorded as `owner_attested_reference_not_verified_by_system`; this code
  cannot verify a licence, and the file is not a cryptographic signature. Before any
  fitting the adapter (1) validates the destination, then (2) atomically creates an
  exclusive private marker `<output root>/executions/<key>.json` where the key is derived
  from study, evidence class and the **input digest** (inputs only; a new authorization ID,
  commit or runtime cannot bypass it). The marker file and its directory entry (and any newly created ancestor
  entries) are fsynced before fitting, and a sync failure stops the run without fitting. An
  existing marker, including one left by an interrupted run, is rejected: there is no automatic retry and any reset is a manual,
  separately authorized owner action.
- **Output** (`artifacts/research/phase3cr/<id>/`; fixtures `phase3cr_synthetic/`): the
  whole output directory must itself be git-ignored (Git cannot re-include files of an
  ignored directory), have no tracked descendants, and every final path and the marker are
  re-checked, all before the authorization is consumed or anything is fitted; "untracked"
  requires a successful `ls-files` query with an empty listing, so a Git error fails
  closed; ancestors are walked descriptor-relative without following symlinks and
  must be directories owned by the current user and not group/other writable; missing
  directories are created exclusively with mode 0700. The experiment directory is reserved
  by an exclusive 0700 `mkdir`, files are written with `O_EXCL|O_NOFOLLOW` mode 0600 (no
  replacement, no later chmod), and the integrity index is written last. An existing or
  partial directory is never reused. Persisted content is sanitized aggregate/fold metrics,
  configuration, lineage (fold membership and protected-session lists replaced by checksums
  and counts), findings, limitations and the authorization record; reliability bins are
  omitted. The real destination remains subject to owner approval before any real run.
- **Evidence separation:** the only test seam supplies fixture parent identities and pins
  and switches the evidence class to `synthetic_fixture`, namespace `aq-p3cr-synth-` and
  the synthetic output root.

Remaining prerequisites before any real run (each needs separate owner authorization):
bounded QQQ/IWM capture at the pinned commit with rights confirmation; approval of the
real output destination; and an owner-authored authorization bound to the preflight digest.

## Unresolved real-data prerequisites (separate owner authorization)

Execution environment (applies to every item below): the future real-data adapter must run
in a fresh isolated process, from a clean pinned checkout, in a verified environment, and
read only privately owned input snapshots. The lineage hashes the code actually imported,
so a dirty or unpinned checkout is not acceptable evidence. Internal checksum consistency
checked by the binder is not source authentication.

1. A reviewed local adapter that runs `verify_context_artifacts` (exact code/runtime
   lineage), verifies the Phase 3B campaign and obtains the accepted boundary.
2. Bounded one-time QQQ/IWM capture approval, with explicit local rights/entitlement
   confirmation, for exactly the 1,708 eligible sessions.
3. Approval of where sanitized local metrics may be written.
4. One offline exploratory fit, then stop.
