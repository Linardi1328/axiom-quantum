# Market Intelligence MI-1 Phase 1 Completion Specification

Status: Owner-authorized implementation

Implementation branch: `review/market-intelligence-mi1-phase1-completion`

Base commit: `3e92b9e8fd3933e1c4ac441f097080262fd34ae6`

Package/runtime version: `2.0.0b1`

## 1. Purpose

This specification completes Market Intelligence Phase 1 after MI-1A through MI-1D. The
completed Phase 1 is a SPY-first, research/decision-support intelligence layer. It measures
market state, evaluates three-way scenarios, calibrates probabilities, supports selective
abstention, retrieves historical analogues, measures supplied cross-asset relationships,
monitors degradation, and produces a deterministic market brief.

The governing principle remains:

**AI interprets. Data measures. Statistics test. Risk controls constrain. Evidence decides.
Humans remain responsible.**

Phase 1 completion is implementation approval, not a profitability claim, model promotion,
or trading authorization.

## 2. Binding Safety Boundary

Nothing in this phase may change Version 1 trading behavior or Version 2 execution gates.

- Phase 3 remains `NO CANDIDATE PROMOTION` unless a separate future evidence decision changes it.
- Existing Phase 3 protected evaluation remains untouched.
- P5-B remains blocked pending separate owner authorization.
- P5-C remains `BLOCKED_NO_APPROVED_PAPER_MODEL`.
- Live-money trading remains prohibited.
- No model or intelligence output may call a broker, execution service, risk bypass, or paper
  submission path.
- No release tag or package version bump is authorized.
- No new third-party dependency is authorized.

## 3. Remaining Phase 1 Stages

### MI-1E — Calibration and reliability

Add development-only multiclass probability calibration using a frozen chronological policy.
The last 126 eligible fit observations form a calibration tail; earlier observations fit the
candidate model. Temperature scaling is chosen only from the frozen grid
`(0.50, 0.75, 1.00, 1.25, 1.50, 2.00)` by minimum calibration-tail multiclass log loss, with
smaller temperature used only as a deterministic tie-break. Report raw and calibrated log
loss, Brier score, accuracy, mean true-class probability, and expected calibration error
(ECE). Calibration is descriptive development evidence and does not imply actionability.

### MI-1F — Selective confidence and abstention

Evaluate a frozen threshold grid over development-only calibrated predictions. Candidate
policies are combinations of top-probability thresholds `(0.50, 0.55, 0.60, 0.65, 0.70)` and
top-vs-second separation thresholds `(0.05, 0.10, 0.15, 0.20)`. A policy must select at least
63 observations. The stretch reliability objective is realized selected-set precision of at
least `0.80`. Choose the highest-coverage qualifying policy, then higher precision, then more
conservative thresholds. If no policy qualifies, return `NO_QUALIFYING_POLICY`; runtime
behavior remains abstention. This is not threshold optimization against protected data.

### MI-1G — Historical analogues and regime robustness

Add deterministic SPY-only historical analogue search using the frozen MI-1D feature vector.
Standardization statistics use only observations available before the query anchor. Candidate
analogues must precede the query by at least the requested scenario horizon and selected
analogues must be separated from one another by at least that horizon to reduce overlapping
outcome dependence. Euclidean distance in standardized feature space is the version-1
similarity metric.

Add a simple interpretable regime diagnostic, not a learned regime model: trend is positive or
negative from 20-session return; volatility is high or low relative to the trailing median of
20-session realized volatility using only prior observations. Report scenario outcome counts
and candidate probability metrics by regime when enough observations exist. No significance
claim is implied.

### MI-1H — Cross-asset relationship context

Add deterministic relationship calculations for caller-supplied point-in-time context series.
No new network/provider acquisition is authorized here. Supported measures are rolling
correlation and relative performance over an explicitly requested trailing window. Inputs
must be verified, aligned, finite, strictly ordered, and available no later than the analysis
as-of time. Missing context remains explicitly unavailable.

### MI-1I — MI-1 protected-evaluation gate

Add a separate MI-1 protected-evaluation contract. It must not access or modify the older
Phase 3 protected-evaluation boundary. The MI-1 evaluator accepts only an explicitly supplied
protected feature/label artifact plus a frozen Phase 1 policy bundle whose candidate,
calibration, and selectivity decisions were fixed before the protected start. The evaluator
is one-shot by contract, records its protected interval and metrics, and never promotes a
model automatically. Repository tests use synthetic protected data only. Real protected SPY
evidence remains pending until an eligible point-in-time artifact is supplied and explicitly
run.

### MI-1J — Degradation monitoring

Add deterministic monitoring comparing a recent realized prediction window with an immutable
reference evaluation. Monitor multiclass log loss, Brier score, ECE, selected-policy
precision, and selected coverage when available. Status is `INSUFFICIENT_EVIDENCE`, `STABLE`,
`WARNING`, or `DEGRADED`. The version-1 rule is conservative and deterministic: fewer than 63
recent outcomes is insufficient; one breached metric is warning; two or more breached metrics
is degraded. Breaches are based on absolute limits frozen in code and/or deterioration from
reference values. Monitoring cannot authorize execution.

### MI-1K — Deterministic brief and Phase 1 acceptance

Add an immutable deterministic SPY intelligence brief that can contain:

- run identity and data-quality decision;
- market-state snapshot;
- 5-session and 20-session scenario forecasts where available;
- actionability/abstention decisions;
- calibration/reliability status;
- historical analogue summaries;
- supplied cross-asset relationship summaries;
- degradation status;
- explicit limitations and unavailable components.

The brief must never invent a number. It is generated entirely from verified input artifacts.
No LLM is required to construct it. A separate Phase 1 acceptance object records software
implementation status and scientific-evidence status independently.

## 4. Protected Evaluation and Scientific Status

The repository may complete Phase 1 software implementation without claiming a validated
predictive edge. `IMPLEMENTATION_APPROVED` means the contracts, deterministic calculations,
fail-closed policies, tests, lineage, and isolation gates are complete and green.

Scientific status remains one of:

- `PENDING_PROTECTED_EVALUATION`;
- `PROTECTED_EVALUATION_COMPLETED_NO_PROMOTION`;
- `ELIGIBLE_FOR_SEPARATE_PROMOTION_REVIEW`.

Only an explicit future review may move from protected evidence to promotion. Phase 1 itself
never authorizes paper or live trading.

## 5. Data and Chronology Rules

- No random shuffling.
- Every training/calibration/selection input must be chronologically ordered.
- Labels are eligible for fitting only when their `outcome_session` is observable at the
  relevant cutoff.
- Scaling and calibration are fit only on earlier data.
- Historical analogue normalization must not use data after the query anchor.
- Cross-asset observations must be point-in-time available by the analysis timestamp.
- Protected evaluation policy must be frozen before the protected interval begins.
- All numeric inputs must be finite.

## 6. Testing Requirements

Tests must cover at least:

1. calibration-tail chronology and frozen temperature-grid behavior;
2. probability normalization and ECE calculations;
3. selectivity coverage/precision and fail-closed no-qualifying-policy behavior;
4. analogue look-ahead exclusion and non-overlap spacing;
5. causal regime classification and regime diagnostics;
6. cross-asset ordering, availability, alignment, correlation, and relative performance;
7. protected-policy freeze and one-shot protected-evaluation artifact validation;
8. degradation status transitions;
9. deterministic brief construction with unavailable components preserved explicitly;
10. static isolation from execution, paper submission, broker clients, credentials, and live
   trading;
11. full repository Ruff, formatter, Mypy, pytest/coverage, warning, and whitespace gates.

## 7. Acceptance Criteria

Phase 1 implementation is complete when MI-1A through MI-1K are present on `main`, all new
artifacts are immutable and auditable, chronological/leakage invariants are enforced, no
unauthorized provider/execution path was added, full repository quality gates are green, and
a final review finds no blocking issue.

Phase 1 implementation approval does **not** mean the MI-1 candidate has passed real protected
evaluation. If no eligible real protected artifact has been executed, scientific status must
remain `PENDING_PROTECTED_EVALUATION` and all model-connected trading gates remain closed.
