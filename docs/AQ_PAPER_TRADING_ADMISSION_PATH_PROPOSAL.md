# Axiom Quantum — Evidence-First Path to Supervised Paper Trading

**Status:** DRAFT — OWNER REVIEW REQUIRED. No research-run, protected-evaluation, model-admission, broker, paper-order, or live-trading authorization.
**Linear:** RIC-147 (AQ-PAPER-01).
**Baseline:** `main` at `069137ae2c274a999e10c4c6c1c42de4cccae63e`.
**Owner goal:** Reach an independently justified, human-confirmed Alpaca paper-trading pilot without weakening the scientific or execution gates.

## 1. Established state and what is actually blocked

- Phase 3B single owner-run diagnostic `aq-p3b-339f75453f2effdc66502b70`: 7 disjoint assessments, 6/6 SHA-256 indexed artifact checks passed, candidate median ROC AUC ~0.47367, latest three AUCs <0.5, predicted-positive rate 96.03–100%, negative recall 0–6%. It is **NO CANDIDATE PROMOTION** evidence. Its inputs/metrics must not be retuned, relabeled, replayed, or used as a fresh holdout.
- Phase 4 observation-only operation exists. **Gate B model-connected shadow inference is LOCKED**: no approved model or trusted admission record.
- Phase 5 P5-A infrastructure is permitted. **P5-B real paper-broker submission requires separate owner authorization**. **P5-C is `BLOCKED_NO_APPROVED_PAPER_MODEL`.**
- Phase 6 provides a human-confirmed paper-only execution bridge to an existing `PaperExecutionService` with approval, independent risk limits, kill switch, duplicate protection and lookup-only reconciliation. Software existence does not itself authorize an order.
- Phase 7 supplies synthetic safety testing and owner-attested evidence contracts; the operational pilot has **not passed**. Its 20-distinct-XNYS-session readiness-to-audit check, at least one independently verified broker outcome, unresolved-incident checks and an independent owner decision remain separate from software delivery. An attestation hash is **not** proof of broker authenticity.
- An authorized *human-confirmed manual paper pilot* and **model-generated paper trading** are different outcomes. A manual pilot can test the existing broker/approval safety path only after distinct owner and operational approvals, but does not establish predictive edge or unlock P5-C.

## 2. Do not duplicate the Market Intelligence research track

- MI-2A/B/C/D already specify point-in-time context from QQQ, IWM, VIX and US 10-year yield, controlled feature ablations, forward-only calibration and selective abstention.
- MI-2 concerns three-class `DOWNSIDE/RANGE/UPSIDE` scenarios. The rejected V2 Phase 3 candidate predicts a **binary SPY `t+1` to `t+6` outcome**. Different labels, clocks, artifacts and eligible folds **must not be silently connected**. No MI result grants paper admission.
- First implementation work is a **read-only compatibility and evidence inventory** of the existing modules. Reuse MI transforms only if feature semantics, source rights, point-in-time availability, adjustments, calendars and identity are demonstrably compatible with the binary research target. Otherwise abort the bridge rather than fabricate a data join.

## 3. Proposed narrow next research hypothesis (not yet authorized)

> Does adding one verified, point-in-time market-context feature group to the existing binary SPY `t+1` to `t+6` target improve leakage-safe, non-overlapping walk-forward ranking and probability quality against the original frozen SPY-only comparators?

- Investigate **one** predeclared context group at a time; a preferred initial candidate is the existing MI-2 `QQQ/IWM` relative-market-context group *if* its source/availability contract is verified. VIX/rates are separate hypotheses, not automatic extra searches.
- Keep the Phase 3B binary target, SPY signal timing, costs and original fixed logistic/boosting/training-prevalence comparators. Candidate family should initially remain a simple regularized logistic model with predeclared regularization; no AutoML, opaque ensemble or external AI model.
- Before implementation, independently inventory what historically available QQQ/IWM data actually exists. No provider data acquisition, credentials, external requests, or real-data training is authorized by this draft.
- If data is absent, mislabeled, stale, revision-contaminated, legally unusable, or knowable only after the signal decision cutoff, the result is **NO ELIGIBLE EXPERIMENT**.

## 4. Pre-registration before any new real-data campaign

An owner-approved amendment must freeze **all** of the following in a versioned config and immutable experiment identity *before* training or assessment:

1. Exact parent input identities and hashes; new source rights/available-as-of semantics; any exclusions; feature/label schemas and binary label horizon.
2. Exact finite candidate/model/feature grid (small by design), training-only transforms and seeds; baseline model definitions; evaluation period partitions; 756 initial eligible training rows, six-session true label-exit purge, 126 assessment rows and final 63+ partial fold as applicable, with explicit inter-window outcome-exit proofs.
3. Minimum valid fold count, same-window baseline comparisons, metrics and warnings; disjoint prediction/label-outcome boundaries, but a caveat that folds are **not independent trials**.
4. Material ROC-AUC improvement **strictly greater than zero**, numeric absolute ranking floor, baseline log-loss or Brier improvement, conservative later-fold/worst-quartile checks, negative-class recall, predicted-positive frequency and calibration. All thresholds must be set before the first assessment; they must not be relaxed after seeing results.
5. Ablation controls and trial-count budget, missing-data and unequal comparator coverage, finite input guards, XNYS calendar checks, train-only fitting, immutable artifacts and `NO CANDIDATE PROMOTION` by default.

**Proposed owner-review numerical policy (NOT APPROVED):** at least five valid windows; median ROC AUC >=0.53 **and** >=0.03 above the stronger same-window frozen fixed-model median; median candidate log loss **or** Brier below the training-prevalence baseline; a positive median AUC in a separately declared later-window subset and no misleading near-constant one-class prediction behavior. These are conservative *proposals*, not validated statistics or evidence of profitability; the owner must review the exact comparison and statistical uncertainty policy before lock. If the sample period cannot furnish five valid folds, **do not lower** the gate mid-run.

Comparisons on 2021–2024 explored development windows remain exploratory. A candidate selected on such evidence **cannot be called externally validated**. No protected window may be used for hyperparameters, context group choice, model selection, calibration or threshold tuning.

## 5. Fresh protected evaluation (separate hold point)

- After a candidate meets its **pre-registered development** gates, freeze its full identity, model, features, training sample, cost/threshold policy and evaluation procedure.
- Propose a genuinely **unexposed post-Phase-2 period** with verified source provenance and enough chronological observations; 2026 observations are only *potentially* eligible, contingent on an audit showing they have not been used in MI/V2 selection or other related research. Never assume calendar recency guarantees a fresh holdout.
- Require a new protected-evaluation lock, immutable access log before labels, and one-time owner consent for one controlled evaluation identity; prohibit repeat selection attempts and reuse of the previously opened Phase 2 final test.
- Inspect aggregate AUC, probability scoring, stability, calibration and separately specified net-of-cost/risk behavior. Protected evaluation can still fail. A negative result means **NO MODEL PROMOTION**.

## 6. Paper-trading critical path

| Gate | Required evidence | Current decision |
| --- | --- | --- |
| A. Research feasibility | Read-only V2/MI schema-and-source audit; signed-off pre-registration | Not yet reviewed |
| B. Development candidate | Predeclared leakage-safe walk-forward evidence beats frozen baselines | No approved candidate |
| C. Protected admission | Fresh, one-time owner-authorized protected evaluation, science review and immutable lineage | Not authorized |
| D. Model-connected shadow | Trusted model-admission record + explicit owner approval, fail-closed authorization checks | Phase 4 Gate B LOCKED |
| E. Supervised model-paper pilot | Shadow safety evidence, paper-only identity, explicit human approval of each order, independent risk gates and broker reconciliation | P5-B not authorized; P5-C blocked |
| F. Operational paper assessment | Genuine paper-broker receipts/audit, at least 20 distinct reviewed sessions, zero unresolved incidents/unknown submissions; independent owner verdict | Phase 7 operational NO_GO |

**Alternative bounded operational pilot:** If the owner wants to test *human-confirmed paper infrastructure before an ML candidate qualifies*, it must be separately proposed and explicitly labeled **manual/synthetic signal, not model-connected**. Run in a verified Alpaca paper-only environment with an explicit individual order approval and independent risk checks, and never misrepresent its results as a validated strategy. This is not authorized here.

## 7. Implementation, safety and finish lines

- **Codex:** backend/research compatibility audit; draft preregistration/config/contracts; focused deterministic synthetic tests. No Antigravity work and no UI, data downloads or additional framework by default.
- Preserve RIC-60 immutability, `execution_authority=none`, `NO CANDIDATE PROMOTION`, `NO APPROVED SHADOW MODEL`, and `BLOCKED_NO_APPROVED_PAPER_MODEL`. No caller-supplied `approved=true`, metadata, altered config, or synthetic trial may unlock operational gates.
- Review via new GitHub branch/PR, Ruff, Ruff format, MyPy, full Pytest and >=85% coverage, Betterleaks, CodeRabbit and owner exact-head review. No acceptance based solely on synthetic pass counts.
- **Next immediate deliverable:** owner-reviewed compatibility/evidence-gap report and a frozen small-scope Phase 3C development protocol **before** any real-data acquisition or run.
- **Do not claim success:** the state remains research-only until a real scientific candidate, fresh evaluation, approved shadow model, supervised broker pilot and independent operational review exist. An honest no-go/abstention is acceptable.

## 8. Decision requested on this draft

Owner approval is requested only to finalize this research-and-integration **specification** and assign the first read-only compatibility audit. It is **not** a blanket authorization to conduct real-data experimentation, protected evaluation, paper trading, broker calls, deploy, or enable autonomous agents.

Related: [RIC-60](https://linear.app/rbl-productions/issue/RIC-60/aq-p3b-01-non-overlapping-walk-forward-robustness-diagnostics), [V2 Phase 3 research contract](V2_PHASE_03_WALK_FORWARD_RESEARCH_SPEC.md), [MI-2 contextual research](MI_02_CONTEXTUAL_MARKET_INTELLIGENCE_SPEC.md), [Phase 6 human-confirmed execution](AQ_PHASE_06_HUMAN_CONFIRMED_PAPER_EXECUTION_SPEC.md), [Phase 7 completion](AQ_PHASE_07_COMPLETION_SPEC.md).
