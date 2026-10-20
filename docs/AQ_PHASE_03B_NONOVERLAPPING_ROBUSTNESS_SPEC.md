# Axiom Quantum — Phase 3B Non-Overlapping Walk-Forward Robustness Diagnostics

**Status:** PROPOSED — OWNER REVIEW REQUIRED; NOT APPROVED FOR IMPLEMENTATION OR REAL-DATA RUNS.
**Linear:** RIC-60 / AQ-P3B-01.
**Change type:** Narrow diagnostics-only research extension to the accepted V2 Phase 3, with no retroactive changes to accepted specifications or campaign settings.
**Parent baseline:** main at 375a5c7a9ca297d581671d301f0f7777fd3d491c.
**Admission decision:** NO CANDIDATE PROMOTION. Trading authority: none.

## 1. Purpose, scope, and explicit hold point

Explain whether the selected Phase 3 logistic classifier's poor later-period ranking and near-always-positive decisions persist under **non-overlapping prediction and label-outcome assessment windows**, using already-frozen model definitions and a research-only, descriptive report.

This document is the **specification approval artifact**, not authorization to implement, execute a real-data campaign, promote models, trade, deploy, or modify existing operational surfaces. OWNER HOLD POINT: obtain explicit acceptance of this exact spec before implementing Phase 3B or running any real-data diagnostics. Changes to this draft must be reviewed again.

No frontend/UI, new service, broker integration, cloud deployment, API write route, scheduler, background agent, paid provider call, or new framework is in scope. Codex owns research/backend implementation and tests if approved. Antigravity has no work in this backend-only issue.

## 2. Immutable evidence and dataset eligibility

- Accepted Phase 1 parent: verified Alpaca SIP SPY daily all-adjusted dataset spy-v2p1-900ca75371bf05e25b1935b5; canonical checksum 3fbaed8976d4aa1bc8d9029c955aa663b61acbea1be30bcf7a5e730a0b99df6c.
- Parent Phase 3 overlapping-fold campaign: spy-v2p3-dev-1f9a68f3dce8df02e3645468; 13 overlapping assessment windows; nine indexed artifacts previously verified. These artifacts are **read-only historical context**, not a tuning target for new research.
- Frozen development prediction boundary: no later than 2024-10-07. Derive the Phase 2 final-test partition mechanically from the repository's frozen Phase 2 split policy, not from a convenient calendar cut date or filename.
- Phase 2 final test was already opened in earlier research and is **not fresh independent holdout evidence**. The new diagnostics must not load its labels, predictions, trading outcomes, test result rows, or any unapproved protected evaluation surface.
- Reuse the existing verified local Phase 1 loader and apply_phase2_final_test_session_isolation before building any labels, folding, diagnostics, or model scores. Record exclusion policy/version, parent checksums, research slice ID/checksum and protected first prediction-session boundary. Require all training, assessment and label-exit sessions to predate that protected prediction-session boundary.
- Check correct SPY/1Day/SIP/all identity; monotonic unique XNYS sessions; completeness, finite and coherent OHLCV; no future observations; consistent adjustment; immutable input identities. Fail closed when lineage differs. No secrets, account IDs, provider rows or generated real-data artifacts in Git.
- The reconstructed development period is already explored; this is **retrospective diagnostic evidence only**, not a newly independent prospective validation.

## 3. Frozen experiment contract (no data-dependent optimization)

- Predeclare one Phase 3B policy/config file with its own versioned identity. Do not change configs/research/phase3_development_campaign.json or the released Phase 3 specification.
- Candidate under investigation: fixed Phase 3 logistic_regression_research_00 model **without calibration**, using already-selected baseline_plus_drawdown_position feature families. Reuse its existing registry hyperparameters and deterministic random seed; no candidate grid search, feature ablation or calibration fitting in this extension.
- Same-window comparators: (1) training-only prevalence probability baseline, (2) original fixed logistic_regression, (3) original fixed gradient_boosting, each refit on each fold's eligible training rows with the frozen model definitions and the same predeclared feature policy. Disclose differences in model features or preprocessing rather than silently compare unlike configurations.
- Classification threshold stays **0.5 only for descriptive predicted-positive/negative counts**. Never optimize thresholds or convert scores into trade signals.
- No choice of model, period, features, parameter, calibration method, regime policy or report framing may be driven by Phase 3B output. The result is always descriptive research; model-admission and trading states remain NO-GO.
- Use existing Python 3.12, pandas/scikit-learn, XNYS calendar, typed data contracts, research artifact store, and offline test stack. No new dependency unless separately justified and approved.

## 4. Disjoint expanding-window fold schedule

On the eligible chronologically ordered supervised prediction rows only, after global feature warmup and label availability filtering:

- Initial training length: **exactly 756** supervised rows, beginning at the first eligible row.
- Training-to-assessment boundary: exactly **six** subsequent supervised prediction rows excluded. The last training label's exit at t+6 must be **strictly earlier** than the first assessment prediction session. Enforce with actual session/exit identities, not row counts alone.
- Assessment window: the next **126** eligible supervised prediction rows when available; **include one final partial window of 63–125 rows whenever available**. If fewer than 63 remain, record them as unassessed with reason; do not stretch, shuffle, duplicate, or silently drop a valid window.
- Advance sequentially: after assessment k completes, expand the next fold's eligible training history through the last prediction session of assessment k; exclude the **next six** supervised prediction rows before assessment k+1. No assessment prediction session may be reused in any other assessment.
- Extra **assessment-to-assessment outcome purge** is mandatory: assessment k's final label exit session must precede assessment k+1's first prediction session. The six excluded rows between windows provide a candidate implementation of this purge; always validate actual t+6 exits against next prediction sessions, even across holidays or missing rows. Fail closed if violated.
- Do not exclude old assessment rows from the *subsequent expanding training set* merely because they were assessed earlier. They become historical training data only once labels have fully matured before the next assessment starts. Require every training label exit to precede its own assessment start. Fit transforms, imputation, scalers, probability models and any training prevalence only on that fold's permitted training history.
- Reject empty, duplicate, unordered or overlapping assessment sessions; boundary crossings; any train/assessment shared prediction row within the same fold; no usable training history; and any protected-period/label-exit contact. Require **at least three valid folds** for descriptive cross-window aggregate summaries; otherwise report insufficient evidence, not a candidate verdict.
- Assessment prediction disjointness is not statistical independence. Five-day forward-return label windows share market outcomes inside each 126-row block, successive expanding training sets share history, and market regimes remain serially dependent. The report must never count these windows as independent trials or claim a fresh holdout.

## 5. Metrics, baselines and descriptive diagnostics

For each fold and fixed comparator, report training/assessment date bounds; training rows, purge sessions, assessment rows, positive and negative target counts and prevalence; probability mean, standard deviation and 5/25/50/75/95% quantiles; predicted-positive rate at the frozen 0.5 threshold; ROC AUC, log loss, Brier score and, where feasible, confusion matrix and negative recall. Show same-window differences from the training-prevalence comparator and original fixed-model comparators.

- One-class assessment windows: ROC AUC is undefined with explicit reason; retain other well-defined metrics, do not fabricate an AUC or quietly drop the window. Folds below the 63-row minimum must not contribute to normal aggregates. One-class training windows fail closed for classifiers requiring both classes.
- Report per-window performance and conservative descriptive aggregates (medians, dispersion, worst quartile and count of defined folds). Mark missing metric values, any class-imbalance/short-window caveat and unequal comparator coverage.
- Report descriptive regimes for above/below 200-session trailing SMA, high/lower realized volatility using the fold-training median, and trailing drawdown buckets using predeclared -10% and -5% boundaries. Each regime cell reports count, target prevalence and mean predicted probability; mark every cell below **30 rows** as small_sample and prohibit meaningful inference from that cell. No regime-based model selection or execution rule.
- Report each fixed feature's training-vs-assessment median and population stability index (PSI) computed with **10 training-derived quantile bins**, finite-value checks, deduplicated bin edges and epsilon 1e-6, reusing existing diagnostic semantics. Include missingness/finite-rate, and warnings that PSI is bin-dependent, can become extreme with sparse cells and does not measure predictive concept drift.
- Correlating PSI with ROC AUC across a few overlapping or dependent windows does not establish causation. Note sample size, class prevalence, shared observations and label-horizon dependence. Assessments are disjoint by prediction/label-outcome only, not independent experimental replicates.
- Explicitly compare the earlier explored 2022–2023 strong and 2023–2024 weak diagnostic periods descriptively, without reselecting boundaries, parameters or thresholds in response to results. Suppress or mark any unsupported comparison.

## 6. Identities, persistence, reports and immutability

- Define a new namespaced Phase 3B schema and CLI entry separate from the accepted Phase 3 runner and all Phase 4/5/6/7 operational surfaces. Invoke manually; do not register a service, daemon, scheduler or import broker modules.
- Compute deterministic experiment ID from parent Phase 1 dataset ID/checksum, parent Phase 3 campaign/slice ID, explicit frozen Phase 3B config digest, feature/model/label schema versions, exact disjoint fold bounds and runtime/code/dependency identity. Wall-clock time, absolute user paths, randomness from operating environment, credentials, account IDs and host identifiers cannot define research identity.
- Emit versioned config snapshot, immutable fold manifest including training/assessment/purge sessions and exit checks, metrics/diagnostics JSON, sanitized human-readable report, and artifact index with SHA-256 checksums. Check all output hashes after writing.
- No overwrite: identical content under the same experiment ID is idempotent; a conflicting payload, missing expected file, symlink/path traversal, schema mismatch, invalid lineage or partial/interrupted write fails closed or reports recovery_required with a manual recovery runbook. Never silently revise previously accepted artifacts.
- Artifact location remains under ignored artifacts/research in a distinct Phase 3B directory; keep local backup separate. Git commit is limited to spec, code, deterministic synthetic fixtures/tests and sanitized summary after owner run—not real provider bars, derived row-level labels/probabilities, SQLite, secrets or account identifiers.
- Human report must declare: development-only descriptive research, previously opened Phase 2 test excluded, no fresh protected validation, dependence limitations, no approved signal/strategy, NO CANDIDATE PROMOTION, no permission for shadow model inference, paper orders, unattended execution or live trading.

## 7. Test and review gates

Offline/synthetic Python regression tests must cover: (a) deterministic fold identity and repeatability; (b) non-overlapping prediction sessions **and** non-overlapping inter-window label outcomes; (c) six-session purging with actual XNYS dates; (d) train-only fit, scaling and baseline prevalence; (e) final-test boundary excluded **before labels** and terminal t+6 exit protection; (f) one-class and too-short assessments, no misleading ROC AUC; (g) regime small_sample warnings and PSI bin sensitivity; (h) missing/nonfinite inputs and zero-variance features; (i) immutable conflict/tamper/symlink/path traversal handling; (j) no broker/credentials/order/model-connected shadow calls; (k) regression compatibility with accepted Phase 1–3 and Phase 4 observation-only code.

Before PR merge, verify: Ruff check/format, MyPy, unit/integration Pytest, coverage gate >=85%, focused leakage/adversarial tests, GitHub CI + Betterleaks, CodeRabbit review on exact current head, human review, clean diff and no unauthorized file changes. Do not mark Done based on documentation alone. Any failed step remains blocked and must not be bypassed by weakening checks.

Actual owner-run real-data diagnostics may occur **only after explicit spec approval and implementation review**, must use verified local data, no network/provider charges, must preserve existing evidence and issue NO-GO unless a separate future governance process authorizes another decision.

## 8. Handoff and acceptance

**Current action:** OWNER REVIEW OF THIS SPEC — no Phase 3B implementation work authorized by the mere existence of this draft. Once explicitly accepted, Codex implements the offline Python research/diagnostics module and tests on a new reviewed implementation branch; Antigravity is not assigned because no UI or API contract changes are needed. Linear RIC-60 tracks acceptance evidence, branch/PR and open blockers. Research output is not a production launch gate and cannot enable trading.

**Non-goals reiterated:** no final-test reopening, backtesting or strategy optimization, threshold fitting, model promotion, broker reads/writes, approval automation, orders, monitoring notifications, market expansion, live execution, or claims of profitability.
