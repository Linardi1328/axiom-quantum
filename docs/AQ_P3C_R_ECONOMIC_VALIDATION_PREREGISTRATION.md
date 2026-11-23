# AQ-P3C-R — Economic-validation preregistration proposal (RIC-152)

Status: **DRAFT proposal for owner review.** This document is documentation only. It is
not an approved protocol, not authorization to acquire data, fit a model, run a
simulation or evaluate any result, and not model promotion. Every output of any future
use of it retains:

```text
execution_authority = none
disposition = NO CANDIDATE PROMOTION
capture_status = NOT AUTHORIZED
```

No numerical decision threshold is approved here. Where a quantitative value is needed it
is marked **`[OWNER-LOCK]`**: a placeholder that must be reviewed by the owner and written
into a separate pre-experiment lock before any real-data run. Nothing marked
`[OWNER-LOCK]` has a default, and no value may be chosen after results are visible.

## 1. Purpose and relationship to existing contracts

`docs/AQ_P3C_R_RETROSPECTIVE_QQQ_IWM_SPEC.md` preregisters an **exploratory
classification screen** (`aq-p3cr-criteria-v1`) for the study `aq-p3cr-retrospective-study-v1`.
That screen only asks whether QQQ/IWM context improves out-of-fold classification of the
existing binary SPY target. It says explicitly that a favorable screen "only supports
future independent research". A classification screen cannot show that acting on the
predictions is economically plausible after costs, exposure limits and risk.

This proposal defines, *before any real-data run*, how a future, separately authorized
retrospective result would additionally be assessed for **economic plausibility**. It
is deliberately conservative: it adds descriptive diagnostics and abstention rules, and
leaves every acceptance threshold to the owner.

What this document does **not** do:

- It does not change `aq-p3cr-criteria-v1`, the frozen configuration
  `aq-p3cr-frozen-five-model-config-v1`, the five-model comparison, the frozen SPY parent,
  the fold construction, the target definition or the Phase 2 final-test exclusion.
- It does not add a strategy, backtest runner, feature, model, loader or CLI.
- It does not relax strict point-in-time requirements, source-rights requirements or the
  separate owner authorizations listed in the retrospective spec.

## 2. Frozen inputs (unchanged, restated for traceability)

| Item | Frozen value (source) |
|---|---|
| SPY parent dataset | `spy-v2p1-900ca75371bf05e25b1935b5`, canonical SHA-256 `3fbaed8976d4aa1bc8d9029c955aa663b61acbea1be30bcf7a5e730a0b99df6c` (`retrospective_study.py`, retrospective spec) |
| Parent campaign | `spy-v2p3-dev-1f9a68f3dce8df02e3645468` |
| Population | 1,708 source sessions 2018-01-02 – 2024-10-15; 1,642 predictions 2018-03-29 – 2024-10-07; seven disjoint folds, assessment sizes 126×6 and 88; 844 assessed predictions |
| Protected period | Phase 2 final-test exclusion applied before any feature or label code (`apply_phase2_final_test_session_isolation`); protected rows never used for features, labels, assessment or the economic evaluation proposed here |
| Target | enter `t+1` open, exit `t+6` open; positive only if net return > 0 |
| Costs | 0.125 bps commission + 0.25 bps slippage **per side** (`COMMISSION_BPS_PER_SIDE`, `SLIPPAGE_BPS_PER_SIDE`) |
| Models | training prevalence; `logistic_regression_research_00`; `logistic_regression`; `gradient_boosting`; 24-column candidate (research_00, C = 0.1, liblinear, seed 42) |
| Classification threshold | 0.5 (descriptive in the frozen study) |
| Classification screen | `aq-p3cr-criteria-v1`, unchanged |

The traded instrument in every hypothetical position below is **SPY only**, consistent
with the Version 1 rules. QQQ and IWM are never traded; they enter only as the eight
frozen context features.

## 3. Preconditions and the "one fit" constraint

1. The retrospective spec allows one offline exploratory fit and then stops. An economic
   evaluation that required a *second* fit would be a second trial and is therefore not
   permitted. Any economic evaluation must be computed **from the same single fit** that
   produces the classification screen, using the same out-of-fold predictions.
2. Consequently this proposal must be **approved and locked before** that fit, and the
   economic evaluation must be implemented and reviewed before that fit. It cannot be
   added afterward.
3. The current evaluator persists only sanitized aggregate and fold metrics; per-row
   probabilities, labels and returns are neither retained nor written
   (`retrospective_study.py`, retrospective spec "Output"). Computing the quantities below
   needs per-prediction probabilities and forward returns **inside** the in-memory
   evaluation. That is **new implementation work** that is not authorized by this
   document and needs its own reviewed change and tests. Persisted output must remain
   aggregate-only, with no per-row probability, return or date list.
4. The existing backtest engine (`run_long_or_cash_backtest`,
   `run_strategy_signal_backtest`) is a Version 1 SPY long-or-cash engine bound to
   `FinalTestEvaluation` / `StrategySignalSet` (the *protected* final-test path) and a
   fixed `INITIAL_SIMULATED_CASH` of 10,000. It must **not** be pointed at the protected
   period, and it cannot consume development-fold predictions as it stands. This
   proposal therefore reuses its **metric definitions and accounting conventions**, not
   its runner.
5. The owner must lock this document's SHA-256 and the `[OWNER-LOCK]` values in a file
   whose checksum is bound into the experiment identity of any future economic
   evaluation, alongside the code SHA, parent checksums and context dataset checksums
   already required by the retrospective spec.

## 4. Portfolio construction (proposed policy `P-A`)

A signal is a hypothetical decision, not a funded trade. The policy below defines when a
signal results in a hypothetical position. The same policy is applied identically to
every model and to the model-based benchmarks.

| # | Rule |
|---|---|
| P-1 | **Signal.** At the close of prediction session `t`, a model emits a probability; the signal is *long* if `probability > 0.5`, otherwise *cash*. This reuses the frozen descriptive 0.5 threshold. No threshold search, calibration or tuning is permitted. (`[OWNER-LOCK]` D2: confirm the use of 0.5, or lock a different single value *before* the run.) |
| P-2 | **Instrument and direction.** SPY only; long or cash; no shorting, leverage, derivatives, other assets or margin. |
| P-3 | **One position at a time.** A long signal funds a position only if the account is flat when the entry order would be sent. While a position is open, new long signals are **ignored** (no pyramiding, no concurrent positions, no early exit, no signal-based exit). Signals ignored for this reason are counted and reported. |
| P-4 | **Holding period.** Enter at the open of `t+1`; exit at the open of `t+6` (the frozen label horizon, five sessions held). By the frozen fold construction (strict exit chronology and label-exit purge) every such exit for an assessed prediction lies inside that fold's `assessment_last_exit` window. |
| P-5 | **Capital.** Each fold starts flat with the fixed hypothetical starting cash `10,000` (the existing `INITIAL_SIMULATED_CASH`). No capital carries across folds and there is no compounding across folds. |
| P-6 | **Sizing.** All available cash is used: whole shares only, `floor(cash / effective entry price)` where the effective entry price includes the frozen per-side cost, matching the existing engine's affordability rule. No fractional shares. Unspent cash earns 0% (no interest assumption). If fewer than one share is affordable the signal is *unfunded* and is reported as such. |
| P-7 | **Cash availability.** Exit proceeds are available only after the exit fill. If a position exits at the open of session `s`, a long signal made at the close of `s−1` may enter at the same open, but only after the exit has filled and only with the cash then available; there is no use of unsettled or future proceeds and no borrowing. |
| P-8 | **Exposure limit.** At most one hypothetical position of at most 100% of fold equity; maximum concurrent positions = 1. |

Rationale: P-3 matches the Version 1 single-position rule and avoids the serial
dependence and cash-conflict ambiguity of overlapping five-session windows, at the price
of ignoring many signals and producing few trades per fold (at most one round trip per
five sessions, so at most 25 in a 126-prediction fold, and normally far fewer). The study
has no requirement to use more than one position.

**Alternative `P-B` (diagnostic only; owner decision D3).** Five equal "sleeves": each
long signal is funded with one fifth of fold equity, up to five concurrent overlapping
positions, no leverage. This uses every signal and mirrors the daily-signal structure of
the label but creates five-fold overlapping returns, higher turnover and correlated
positions. If included, it is reported **only** as a labelled sensitivity next to `P-A`,
never selected after the fact, and requires a second owner-reviewed implementation.
`P-A` is the proposed *primary* policy.

All of `P-A` and `P-B` are proposals. None is implemented, and none can be run under this
document.

## 5. Execution convention and costs

- **Price convention (frozen).** Hypothetical entry fill at the `t+1` open of the parent
  SPY series multiplied by `1 + side_cost_rate`; exit fill at the `t+6` open multiplied by
  `1 - side_cost_rate`, where `side_cost_rate = (0.125 + 0.25) / 10,000`, exactly as in
  `datasets/labels.py`. Gross return is `exit_open / entry_open - 1`; net return uses the
  cost-adjusted prices. Round-trip cost is therefore twice the per-side rate.
- **Series.** The parent SPY series with `adjustment=all` (split- and dividend-adjusted),
  one consistently adjusted series for features, labels and economics; never a mix of raw
  and adjusted fields. Hypothetical results are denominated in adjusted prices.
- **Turnover.** Reported with the existing `turnover_ratio` definition (total reference
  notional divided by mean equity), per fold, together with the number of completed round
  trips.
- **What the data cannot establish.** Daily bars contain no quotes, depth, trade-size
  or condition data. They cannot establish the actual bid–ask spread, market impact,
  fill probability, order-type behavior, queue position or the execution price of a real
  market-on-open order. The daily bar "open" is a historical *reported* print that need
  not equal an executable price at the opening auction or first consolidated trade, and
  whether extended-hours activity is included is itself documented as not established.
  Retrospective-corrected bars may also differ from what a trader observed at the time.
  **No result may be described as realized or achievable execution quality**, and the
  frozen 0.375 bps per side is an assumption, not an estimate of true trading cost.
- **Cost sensitivity (required reporting).** Because the frozen costs are an
  assumption, each economic metric is also reported with the per-side cost scaled by a
  set of multipliers `[OWNER-LOCK]` D5 (the multiplier set; no default is proposed), and a
  parameter-free **break-even per-side cost** (the per-side cost at which the policy's net
  return for the fold equals zero; undefined if the gross return is not positive). Cost
  sensitivity is a diagnostic only and never changes the primary cost assumption.

## 6. Benchmarks

All benchmarks are fixed here, before any evaluation. For each fold they use **the same
window, the same starting cash, the same whole-share rule, the same frozen costs and the
same equity-marking convention** as the candidate. The window of a fold is from the open
of the entry session of its first assessed prediction to the open of its
`assessment_last_exit` session.

| ID | Benchmark | Definition |
|---|---|---|
| B-0 | **No position** | Cash only: return 0, exposure 0, no costs. The abstention baseline. |
| B-1 | **Buy-and-hold SPY** | Buy at the window's first open, sell at its last open; one round trip; same costs. |
| B-2 | **Training-prevalence policy** | The frozen training-prevalence model's constant probability fed through the same `P-1`…`P-8` rules: always long if the training prevalence exceeds 0.5 (re-entering after each exit), otherwise always cash. This is the economic counterpart of the frozen prevalence control. |
| B-3 | **SPY-only fitted comparators** | The three fitted comparators of the frozen five-model design (`logistic_regression_research_00`, `logistic_regression`, `gradient_boosting`) under the identical policy and identical prediction rows, so the candidate is compared with the strongest SPY-only comparator, as in `aq-p3cr-criteria-v1`. |

Candidate-versus-benchmark comparisons are **paired by fold** and reported as per-fold
differences and fold counts, with no significance claim. An exposure-matched or
randomized-timing benchmark is a possible addition (decision D6) but would introduce a
random component and extra design choices; it is not proposed unless the owner requests
it and locks its construction first.

## 7. Economic and risk metrics

Status key: **E** = definition exists in `BacktestMetrics` / `canonical_backtest_evidence`
(`research/backtest_evidence.py`, `docs/AQ_PHASE_01_SLICE_03_BACKTEST_EVIDENCE_SPEC.md`)
and should be reused unchanged; **N** = requires additional implementation (not authorized
here); **X** = cannot be established from the available data.

| Metric | Definition | Status |
|---|---|---|
| Net total return | `final_equity / initial_cash − 1` | E |
| Maximum drawdown | absolute minimum of daily equity / running peak − 1 | E |
| Exposure fraction | share of sessions with a position at session end | E |
| Turnover ratio | total reference notional / mean equity | E |
| Commissions, slippage cost, total transaction cost | sums over fills | E |
| Cost burden | `total_transaction_cost / initial_cash` and cost in bps of execution notional | E (omitted when the denominator is undefined, per the evidence spec) |
| Order and fill counts, fill rate | proposed/approved/rejected/filled; fill rate when proposed > 0 | E |
| Gross (pre-cost) return and cost drag | gross return and net − gross | N |
| Completed round trips; ignored and unfunded signals | counts | N |
| Per-trade net return distribution | mean, median, best, worst; hit rate | N |
| Daily-return volatility and downside deviation | from daily equity marks | N |
| Risk-adjusted return | return divided by volatility (Sharpe-like) and by downside deviation (Sortino-like) with a stated annualization convention and risk-free assumption (`[OWNER-LOCK]` D8); return divided by maximum drawdown | N (undefined when the denominator is zero) |
| Drawdown duration and time under water | sessions below the prior peak | N |
| Tail diagnostics | worst single trade, worst five-session loss, longest losing streak; expected shortfall of trade returns at a quantile `[OWNER-LOCK]` D9, only if the trade count is sufficient | N |
| Per-fold dispersion | minimum / median / maximum of each metric across folds; number of folds with net return above each benchmark | N |
| Break-even per-side cost and cost-sensitivity table | see §5 | N |
| Realized spread, market impact, fill quality, capacity, taxes, borrow | — | X |

Aggregation across folds uses the **median of per-fold values and the fold range**, as the
frozen screen does, not a stitched equity curve (folds are separated by purge gaps and
start flat). A pooled descriptive total may be shown but is not a decision input. Undefined
metrics are reported as undefined with a reason; they are never filled with zero, NaN or
another value.

## 8. Abstention and decision rules

Rules are deterministic functions of locked inputs. They produce a **categorical
descriptive outcome**, never an approval.

**8.1 Relationship to the classification screen.** The frozen classification screen is
evaluated exactly as already preregistered. The economic evaluation is a separate,
subsequent layer and cannot retroactively change that screen's outcome. Proposed
interpretation rule (decision D1): economic results are **evidence of plausibility only
if** the classification screen returned `CRITERIA_MET`; otherwise the hypothesis is
retired per the spec and the economic results are reported as non-decisional diagnostics.

**8.2 Abstention (a per-fold and overall outcome, not an error).**

| Condition | Deterministic handling |
|---|---|
| No eligible signal in a fold (no probability above the signal threshold, or every signal ignored/unfunded) | Fold economic result = abstained: net return 0, exposure 0, no trades; counted as an abstained fold. |
| No trades in any fold | Overall outcome `NO_TRADES`; economic evidence not evaluable. |
| Completed round trips below `N_min` `[OWNER-LOCK]` D4 (no default) | Overall outcome `INSUFFICIENT_TRADES`; per-trade and tail statistics reported as undefined. |
| Any required metric undefined for the candidate in a decisive fold | The fold is excluded from decisive counts and listed with its reason; fewer than five valid folds (the `MIN_VALID_FOLDS` value of the frozen classification screen, proposed for reuse; owner to confirm under D6) ⇒ `NOT_EVALUABLE`. |
| Cost-negated: gross return > 0 but net return ≤ 0 | Fold flagged `COST_NEGATED`; folds so flagged are counted. |
| Excessive costs: cost burden above a limit `[OWNER-LOCK]` D5 | Overall outcome `EXCESSIVE_COST_BURDEN`. |
| Unacceptable risk: maximum drawdown or tail metric beyond limits `[OWNER-LOCK]` D7 (relative to B-0 / B-1 and/or absolute) | Overall outcome `UNACCEPTABLE_RISK`. |

**8.3 Outcome labels.** Exactly one is assigned by evaluating, in order: `NOT_EVALUABLE`,
`NO_TRADES`, `INSUFFICIENT_TRADES`, `EXCESSIVE_COST_BURDEN`, `UNACCEPTABLE_RISK`; then, if
none applies, the candidate is compared with B-0, B-1, B-2 and the strongest B-3
comparator under owner-locked margins and fold-count rules `[OWNER-LOCK]` D6, giving
either `ECONOMICALLY_NOT_SUPPORTED` or `ECONOMICALLY_PLAUSIBLE_DESCRIPTIVE`. The latter
means only that locked descriptive rules were met in a retrospective-corrected
development population; it is **not** evidence of future performance, **not** admission,
and still carries `NO CANDIDATE PROMOTION` and `execution_authority = none`.

**8.4 What may not happen.** No numerical threshold may be chosen, tightened, relaxed or
added after results are visible; no metric may be removed; no additional policy, benchmark
or cost multiplier may be added post hoc; a failed outcome may not be re-run with
different settings. Every reported fold-level statistic is reported, not only favorable
ones.

## 9. Scientific limitations (carried in every economic result)

1. **Retrospective-corrected data are not strict point-in-time evidence.** The QQQ/IWM and
   SPY bars were retrieved after the fact with all later adjustments; availability at each
   cutoff, as-of corporate-action history, extended-hours inclusion and usage rights are
   not established. `point_in_time_status` stays `UNVERIFIED`.
2. **The SPY development population has already been explored** in Phase 3B and the
   earlier development work; results here cannot be treated as untouched out-of-sample
   evidence.
3. **Assessment folds are chronologically dependent**, not independent experiments; fold
   counts, medians and paired differences are descriptive, and no statistical-significance
   claim is made. The five models and several benchmarks are compared on the same data,
   so favorable comparisons may arise by chance (multiple comparisons).
4. **Classification improvement does not establish profitability or economic value.**
   Economic outcomes are sensitive to the policy, whole-share rounding on a small
   hypothetical account, idle cash earning 0%, few trades and serial dependence.
5. **Hypothetical results cannot establish broker execution quality.** They use reported
   daily opens, an assumed fixed cost and no market impact or fill uncertainty (§5).
6. **A favorable exploratory result cannot substitute for fresh, independently protected
   validation.** The previously explored development data (per RIC-152, 2021–2024) and the
   previously opened Phase 2 final test cannot qualify as untouched confirmation; any future confirmation needs a
   never-exposed protected period and separate owner approval.
7. The candidate's design caveat in the retrospective spec (linearly dependent features,
   different effective regularization) applies to economic results as well: improvement
   cannot be attributed purely to incremental information.

## 10. Identity and reproducibility requirements for a future implementation

A future economic evaluation must bind into its experiment identity, in addition to the
items in the retrospective spec: this document's SHA-256, the `[OWNER-LOCK]` file checksum,
the policy identifier (`P-A`, and `P-B` if included), the benchmark set, the metric set and
its definitions' versions, the cost-multiplier set, and the exact rule order of §8.3. It
must be deterministic and bitwise reproducible, contain no wall-clock time, path,
credential or host identity, produce aggregate-only output, and have synthetic-fixture
tests with negative cases for each rule in §8 (no signal, no trades, insufficient trades,
undefined denominators, cost-negation, excessive cost, unacceptable risk, protected-period
refusal and unchanged `aq-p3cr-criteria-v1` outcomes). It is outside this document's
scope.

## 11. Decisions requested from the owner (none is approved by this draft)

| ID | Decision | Draft proposal |
|---|---|---|
| D1 | Relationship to the classification screen | Economic results are evidence of plausibility only if `aq-p3cr-criteria-v1` = `CRITERIA_MET`; otherwise non-decisional diagnostics |
| D2 | Signal rule | Long if probability > 0.5 (the frozen descriptive threshold); no tuning |
| D3 | Primary portfolio policy | `P-A` single position, non-overlapping, all-in whole shares; `P-B` sleeves as optional labelled sensitivity |
| D4 | `N_min` minimum completed round trips | `[OWNER-LOCK]` — no default |
| D5 | Cost-sensitivity multipliers and excessive-cost limit | `[OWNER-LOCK]` — no default |
| D6 | Benchmark comparison margins and fold-count rule; whether to add an exposure-matched benchmark | `[OWNER-LOCK]` — no default; benchmark set B-0…B-3 as in §6 |
| D7 | Drawdown and tail-risk limits (absolute and relative) | `[OWNER-LOCK]` — no default |
| D8 | Annualization convention and risk-free assumption for risk-adjusted ratios | `[OWNER-LOCK]` — no default |
| D9 | Tail-risk quantile and minimum sample | `[OWNER-LOCK]` — no default |
| D10 | Starting cash, whole-share rule and 0% idle-cash assumption | Existing `INITIAL_SIMULATED_CASH` (10,000), whole shares, 0% |
| D11 | Authorize a *separate* reviewed implementation of the economic evaluation inside the single-fit evaluator, with synthetic tests, before the real-data fit | Not authorized by this draft |
| D12 | Approve and lock this document (SHA-256) and the `[OWNER-LOCK]` file before any real-data run | Not authorized by this draft |

## 12. Boundaries

This proposal grants no authority for historical capture, protected-label access, real
model fitting, trading signals, paper or live trading, broker submission, scheduling or
model promotion. Alpaca historical acquisition remains blocked pending its separate
entitlement and rights decisions, and every real-data prerequisite in the retrospective
spec still requires its own owner authorization.
