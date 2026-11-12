# Phase 3C-B — Synthetic experiment runner (RIC-152)

The owner's Phase 3C-B request authorizes this offline, deterministic machinery
check only. It supersedes Phase 3C-A's runner hold point for **internally generated
synthetic fixtures only**. It does not authorize real historical experimentation,
provider access, protected outcomes, operational inference or trading. Phase 3B
results, its fixed policy, and the four-source MI-2A/MI-2B contracts are unchanged.

## Identifiers and entry point

- Runner: `aq-p3cb-synthetic-comparison-v1`.
- Configuration: `aq-p3cb-frozen-five-model-config-v1`.
- Generator: `aq-p3cb-synthetic-ohlcv-context-v1`, seed 42.
- Dependency: Phase 3C-A, reviewed at
  `d5691b7d5540fdd8a13899f64ba254d920f6154c` in PR #99.

`research.phase3c.run_synthetic_experiment()` takes **no arguments**. It generates
all values internally and returns an immutable `SyntheticExperimentResult` with a
canonical JSON payload, deterministic experiment ID and result checksum. No input
path, user dataset, provider, credentials, model artifact, output directory, CLI,
scheduler or automatic invocation is exposed. Private fixture/evaluation helpers
exist for deterministic negative and mutation tests; they are not a real-data
entry point or evidence-verification interface.

The original package initializers eagerly import some legacy acquisition/research
runner declarations. This change does not rewrite those exports. The new modules
neither depend on their capabilities nor invoke them: a fresh-process test runs
the synthetic experiment with credential lookup, provider/legacy dataset loading,
CSV/Parquet loading and network connections forbidden. No new provider, broker or
production registry imports are introduced.

## Fixed fixture and isolation

The fixture uses 1,200 actual XNYS sessions beginning 2010-01-04. Its coherent SPY
OHLCV and QQQ/IWM closes are derived from fixed-seed synthetic shocks, trailing
price paths and correlated synthetic innovations. These are invented observations,
not a representation of historical market performance or point-in-time evidence.
The original frozen real SPY development population is not redefined.

The final 18 synthetic source sessions are protected. They are removed **before**
the existing research feature and forward-label builders run. The remaining
1,182 source sessions yield 1,116 supervised rows after 60-session global warm-up
and six-session label-horizon filtering. Labels enter at the next session's open,
exit at t+6 open, and are positive only for net return greater than zero. Costs
are 0.125 bps commission and 0.25 bps slippage per side.

Context snapshots use the same underlying synthetic SPY/QQQ/IWM closes. Every
prediction has exactly 21 aligned closes, checksum-bound fixture proofs, availability
five minutes after actual XNYS close, and a cutoff ten minutes after close. The
Phase 3C-A policy, bundle/history and alignment validators run before constructing
or fitting any model. Missing/invalid context, noncanonical dtypes, inconsistent
historical overlaps, or protected/incorrect label chronology fail closed.

The synthetic protected boundary is a fixture test, not the Phase 2 real-data
exclusion contract. Any eventual separately authorized real runner must still
derive/apply the existing Phase 2 exclusion contract before building real labels.

## Identical expanding-window experiment

The unmodified `construct_disjoint_folds()` supplies the complete schedule:

| Fold | Training rows | Purge rows | Assessment rows |
| --- | ---: | ---: | ---: |
| 0 | 756 | 6 | 126 |
| 1 | 888 | 6 | 126 |
| 2 | 1,020 | 6 | 90 |

There are zero unassessed terminal rows. Assessment prediction sessions are
unique across folds. All training label exits and the previous assessment's last
label exit precede the next assessment start. Exact training, purge and assessment
row indexes, prediction sessions, entries and exits are retained in the payload.

Exactly five fixed variants share these populations and outcomes:

1. `training_prevalence`: mean of permitted training targets only.
2. `logistic_regression_research_00`.
3. `logistic_regression`.
4. `gradient_boosting`.
5. `logistic_regression_research_00_qqq_iwm`.

The first four controls use the 16 SPY baseline-plus-drawdown-position columns.
The candidate uses those identical 16 columns followed by the eight Phase 3C-A
QQQ/IWM columns. It reuses the research_00 definition unchanged: StandardScaler,
L2, C=0.1, liblinear, no class weighting and seed 42. Each model/fold gets a fresh
estimator; scaling and fitting consume only that fold's training rows. No grid
search, calibration fitting, threshold search, candidate ranking or selection is
performed. Threshold 0.5 is descriptive only. Both training classes are mandatory.
A fit/prediction failure aborts the comparison; no successful partial experiment
or admission is returned.

## Output and reproducibility

Existing research classification metrics report ROC AUC, log loss, Brier score,
balanced accuracy, confusion counts and reliability bins/calibration error. Explicit
positive/negative recall, predicted-positive rate, training/assessment prevalence,
per-fold candidate-minus-control deltas, and aggregate medians, standard deviations,
interquartile ranges and defined/undefined fold counts are included. Undefined
one-class AUC, balanced accuracy and absent-class recall retain reasons; no AUC is
invented. Deltas are raw candidate minus control; positive loss deltas are worse.
These dependent folds are descriptive machinery checks, not independent trials.

Experiment identity binds generator/configuration/seed, all generated source-content
checksums (including the excluded synthetic partition), eligible SPY canonical
checksum, session identity, Phase 3C-A identifiers, feature/label schemas, exact
model definitions and columns, exact fold membership, metric/cost configuration,
Git commit, relevant source-file checksums, Python and dependency versions. Source
hashes also bind uncommitted implementation changes. No wall-clock time, absolute
user path, credentials, account identifier or host identity defines the result.
Bitwise reproduction is tested within the same recorded code/runtime; runtime or
code changes create a new experiment identity. Results contain aggregate model
metrics and exact membership, not fitted estimators or persisted row probabilities.

All output is in memory. The runner writes no artifacts and never serializes a
trained model. Tests may inspect temporary synthetic results, but no real source
rows or private datasets are used. Every result explicitly declares:

```text
evidence_kind = synthetic_fixture
execution_authority = none
disposition = NO CANDIDATE PROMOTION
```

Synthetic metrics do not establish real predictive edge or profitability. Alpaca
licensing and historical point-in-time/as-of adjustment evidence remain unresolved.
No real-data admission, historical experiment, protected evaluation, model-connected
shadow/paper operation, live trading or automatic promotion is authorized. PR #98
is outside this work. The main-target Phase 3C-B PR must wait for #99 to merge and
contain only new runner changes. Stop after Phase 3C-B.
