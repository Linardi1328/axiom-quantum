# Phase 05 Review

## Phase objective

Phase 05 implements deterministic in-memory binary-classification baselines for the approved Phase 4 supervised dataset and chronological partitions.

Completed at: 2026-07-25 12:59:49 UTC.

Implemented scope:

- Logistic-regression baseline.
- Gradient-boosting comparison model.
- Train-only candidate fitting.
- Validation-only candidate comparison and model selection.
- Locked fresh refit on train plus validation.
- One explicit final test evaluation path.
- Complete typed lineage, metrics, prediction audit frames, and structured Phase 5 errors.

Phase 6 was not started. No strategy signals, recommendations, backtesting, position sizing, risk calculations, model persistence, database persistence, APIs, dashboards, broker communication, paper-order submission, live trading, scheduled jobs, or deployment behavior was added.

## Files created

- `src/spy_market_agent/modeling/__init__.py`
- `src/spy_market_agent/modeling/models.py`
- `src/spy_market_agent/modeling/training.py`
- `src/spy_market_agent/modeling/evaluation.py`
- `src/spy_market_agent/modeling/selection.py`
- `tests/unit/modeling_helpers.py`
- `tests/unit/test_model_training.py`
- `tests/unit/test_model_evaluation.py`
- `tests/unit/test_model_selection.py`
- `tests/unit/test_public_phase5_api.py`
- `tests/integration/test_phase5_modeling_flow.py`
- `reviews/PHASE_05_REVIEW.md`

## Files modified

- `pyproject.toml`: Added the approved direct runtime dependency `scikit-learn>=1.7,<2` and a narrow MyPy missing-import override for `sklearn` / `sklearn.*`.
- `README.md`: Updated project status, implemented Phase 5 capabilities, and remaining unimplemented scope.

## Dependencies

Added direct runtime dependency:

- `scikit-learn>=1.7,<2`

No other direct dependency was added. Project code does not directly import NumPy, SciPy, joblib, plotting libraries, broker SDKs, data-vendor SDKs, database libraries, FastAPI, Streamlit, or model-serialization libraries.

Installed version observed during verification:

```text
scikit-learn==1.9.0
pandas==2.3.3
pytest==8.4.2
pytest-cov==7.1.0
ruff==0.16.0
mypy==1.20.2
```

Scikit-learn installed normal transitive runtime packages, including SciPy, joblib, narwhals, and threadpoolctl. They were not added as direct dependencies and are not directly imported by project code.

The narrow MyPy override for `sklearn` / `sklearn.*` was added because the installed scikit-learn package does not provide complete strict type information for this project configuration. MyPy remains enabled for project source and tests.

## Candidate model specifications

Model schema version: `spy-binary-models-v1`.

Selection rule version: `validation-roc-auc-log-loss-brier-v1`.

Logistic regression candidate:

```python
Pipeline(
    steps=[
        ("scaler", StandardScaler()),
        (
            "classifier",
            LogisticRegression(
                penalty="l2",
                C=1.0,
                solver="liblinear",
                max_iter=2000,
                class_weight=None,
                random_state=random_seed,
            ),
        ),
    ]
)
```

Gradient boosting candidate:

```python
GradientBoostingClassifier(
    n_estimators=100,
    learning_rate=0.05,
    max_depth=2,
    min_samples_leaf=5,
    subsample=1.0,
    random_state=random_seed,
    n_iter_no_change=None,
)
```

Gradient boosting does not use a scaler. Early stopping is disabled through `n_iter_no_change=None`; no validation fraction is used for fitting.

## Preprocessing behavior

- Logistic regression uses `StandardScaler` inside the scikit-learn pipeline.
- The scaler is fitted only on the train partition during candidate comparison.
- Validation and test rows never influence scaler statistics.
- No full-dataset standardization, external scaler fitting, imputation, class weighting, resampling, synthetic observations, feature selection, threshold tuning, or hyperparameter tuning was added.

## Training-data boundary

Candidate comparison uses:

- Train features and targets only for `.fit()`.
- Validation features and targets only for probabilities, diagnostics, and selection.
- No test partition parameter exists on `train_candidate_models()`.

Expected malformed inputs fail through project-owned structured errors:

- `ModelInputError`
- `ModelTrainingError`
- `ModelEvaluationError`
- `ModelSelectionError`
- `LockedModelError`

The modeling layer revalidates mutable Phase 4 partition DataFrames before fitting or evaluation.

Locked model metadata also revalidates immutable candidate metric snapshots, fixed parameter snapshots, schema lineage, checksum lineage, split lineage, feature-column order, class counts, and session bounds. Expected malformed Phase 5 metadata failures are raised as structured project-owned errors.

## Validation selection rule

Selection uses validation metrics only:

1. Higher validation ROC AUC.
2. If tied within `1e-12`, lower validation log loss.
3. If still tied within `1e-12`, lower validation Brier score.
4. If still tied, logistic regression wins as the simpler baseline.

Training metrics and test metrics are not accepted by the metric decision helper and are not used for selection.

## Test-lock mechanism

After selection:

- `fit_locked_model_on_train_validation()` verifies the locked selection belongs to the supplied train and validation partitions.
- It builds a fresh estimator for the selected fixed specification.
- It fits on train plus validation rows only.
- It does not accept a test partition.

Final test evaluation:

- Uses `evaluate_locked_model_on_test()` as a separate explicit API.
- Requires a `FinalModelBundle`.
- Verifies test lineage, schema, split spec, feature columns, and chronological order.
- Never calls `.fit()`.
- Preserves selected model name, candidate parameters, diagnostic threshold, and selection rationale unchanged.
- Does not compare candidates on test and does not feed test metrics back into selection.

## Evaluation metrics

Metrics calculated for train, validation, and final test diagnostics:

- `row_count`
- `positive_count`
- `negative_count`
- `positive_rate`
- `log_loss`
- `brier_score`
- `roc_auc`
- `average_precision`
- `accuracy_at_0_5`
- `precision_at_0_5`
- `recall_at_0_5`
- `f1_at_0_5`
- `true_negative_count`
- `false_positive_count`
- `false_negative_count`
- `true_positive_count`

Confusion matrices use explicit label order `[0, 1]`. Log loss supplies explicit labels `[0, 1]`. Precision, recall, and F1 use `zero_division=0`.

These are classification diagnostics only. No trading returns, Sharpe ratio, drawdown, profit factor, position-level performance, strategy performance, or backtest metric was added.

## Reproducibility controls

- Default random seed: `42`.
- Candidate and final estimators receive the configured seed directly.
- Creation timestamps are explicit caller inputs and normalized to UTC.
- No current time, environment variable, global random state, network source, current machine timezone, persistence artifact, or file output is used by model training or evaluation.
- Repeated runs with identical partitions, config, timestamp, and dependency versions produce identical selected model names, prediction frames, metric objects, and fixed parameter metadata in tests.

## Typed result objects

Created frozen typed wrappers:

- `ModelTrainingConfig`
- `ClassificationMetrics`
- `PredictionSet`
- `CandidateModelResult`
- `CandidateModelComparison`
- `LockedModelSelection`
- `FinalModelBundle`
- `FinalTestEvaluation`

Also created structured helper models:

- `ModelingIssue`
- `ModelParameterSet`
- `CandidateMetricSnapshot`
- `ModelSelectionDecision`

DataFrames stored in prediction wrappers are copied on construction. Scikit-learn estimator objects remain internally mutable by design; this limitation is documented here and should be considered when reviewing or persisting any future model artifacts.

## Tests added

Unit tests:

- `tests/unit/test_model_training.py`
  - Fixed estimator specs for logistic regression and gradient boosting.
  - Config immutability and validation.
  - Input-safety rejection for malformed features, targets, sessions, row counts, lineage, schema, and overlap-like mutable partition corruption.
  - Single-class train, validation, and test rejection.
  - Train-only scaler statistics.
  - Validation distribution changes do not alter logistic scaler means.
  - Gradient boosting fitting does not depend on validation rows.
  - Deterministic repeated fitting.
  - Candidate API has no test partition.
  - Mutated test data does not affect candidate selection.
  - Final refit excludes test rows and uses a fresh estimator.
  - Final test evaluation never calls `.fit()` and cannot change locked selection.
  - Phase 5 operations do not mutate partitions or prior prediction frames.

- `tests/unit/test_model_evaluation.py`
  - Manual metric checks for log loss, Brier score, ROC AUC, average precision, accuracy, precision, recall, F1, and confusion counts.
  - Positive-class probability column located from learned `classes_`.
  - Unexpected classes and probability shape/value failures are structured.
  - Prediction frame schema, dtypes, threshold behavior, missing targets, non-binary predictions, and copy ownership.

- `tests/unit/test_model_selection.py`
  - Higher ROC AUC wins.
  - Lower log loss breaks ROC-AUC ties.
  - Lower Brier score breaks second-level ties.
  - Logistic regression wins complete ties.
  - Training metrics and test metrics are not accepted by selection APIs.
  - Locked selection is immutable and records lineage.

- `tests/unit/test_public_phase5_api.py`
  - Public modeling imports work.
  - Every `__all__` entry exists.
  - Importing `spy_market_agent.modeling` does not train models, load datasets, create files, or perform external actions.

Integration test:

- `tests/integration/test_phase5_modeling_flow.py`
  - Deterministic validated market-data flow through Phase 4 features, labels, supervised dataset, chronological partitions, candidate training, validation-only selection, locked train+validation refit, and explicit final test evaluation.
  - Asserts lineage, fixed parameters, deterministic predictions, selected model stability, test isolation, target integrity, no label fields in `X`, and no mutation of earlier Phase 4 objects.

`tests/unit/modeling_helpers.py` supplies deterministic Phase 5 unit-test partitions and is not itself a collected test module.

## Commands executed

Important implementation and verification commands included:

```bash
sed -n '1,220p' PROJECT_SPEC.md
sed -n '221,440p' PROJECT_SPEC.md
sed -n '441,700p' PROJECT_SPEC.md
sed -n '1,180p' AGENTS.md
sed -n '1,220p' README.md
sed -n '1,220p' reviews/PHASE_03_REVIEW.md
sed -n '221,520p' reviews/PHASE_03_REVIEW.md
sed -n '521,900p' reviews/PHASE_03_REVIEW.md
sed -n '1,180p' reviews/PHASE_04_REVIEW.md
sed -n '181,360p' reviews/PHASE_04_REVIEW.md
sed -n '361,560p' reviews/PHASE_04_REVIEW.md
rg --files src tests
sed -n '1,220p' pyproject.toml
.venv/bin/python -c "import sklearn; print(sklearn.__version__)"
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/ruff check src/spy_market_agent/modeling
.venv/bin/mypy src/spy_market_agent/modeling
.venv/bin/pytest tests/unit/test_model_evaluation.py tests/unit/test_model_selection.py tests/unit/test_model_training.py tests/unit/test_public_phase5_api.py tests/integration/test_phase5_modeling_flow.py -q
.venv/bin/ruff check src tests
.venv/bin/mypy src tests
.venv/bin/pytest
.venv/bin/ruff format .
```

Final required verification commands were run sequentially:

```bash
.venv/bin/pytest
.venv/bin/pytest tests/unit -q
.venv/bin/pytest tests/integration -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy src tests
.venv/bin/python -c "import spy_market_agent; print(spy_market_agent.__version__)"
.venv/bin/python -c "import spy_market_agent.modeling as modeling; print(sorted(modeling.__all__))"
git diff --check
```

## Actual verification results

- `.venv/bin/pytest`: Passed, `280 passed`, `4 warnings`, total coverage `79%`.
- `.venv/bin/pytest tests/unit -q`: Passed, all unit tests passed, `4 warnings`, total coverage `79%`.
- `.venv/bin/pytest tests/integration -q`: Passed, `3 passed`, `4 warnings`, integration-only coverage `67%`.
- `.venv/bin/ruff check .`: Passed with `All checks passed!`.
- `.venv/bin/ruff format --check .`: Passed with `56 files already formatted`.
- `.venv/bin/mypy src tests`: Passed with `Success: no issues found in 49 source files`.
- `.venv/bin/python -c "import spy_market_agent; print(spy_market_agent.__version__)"`: Passed and printed `0.1.0`.
- `.venv/bin/python -c "import spy_market_agent.modeling as modeling; print(sorted(modeling.__all__))"`: Passed and printed the explicit public Phase 5 API list.
- `git diff --check`: Passed with no output.

## Warnings

- Four visible third-party warnings remain from `exchange-calendars` / pandas / NumPy timedelta internals. They were not globally suppressed.
- The first post-implementation `ruff format --check .` found formatting changes. `ruff format .` was run, and the full required verification sequence was rerun from the beginning after formatting.

## Known limitations

- The estimator objects inside frozen wrappers remain internally mutable because scikit-learn estimators are mutable.
- Models are in-memory only. No serialization, artifact persistence, registry, database table, or model-loading path exists.
- Diagnostic probability outputs are not calibrated and are not trading signals.
- The diagnostic threshold defaults to `0.5` and is used only for classification metrics; it is not optimized and is not a trading threshold.
- No feature selection, hyperparameter tuning, cross-validation, walk-forward optimization, class weighting, resampling, synthetic observations, calibration, or threshold optimization was added.
- Synthetic tests are deterministic and intentionally small; no real SPY download or provider adapter is included.
- Classification metrics are not evidence of profitability or investment suitability.

## Git status

`git status --short --untracked-files=all` after Phase 5 implementation and this review report:

```text
 M README.md
 M pyproject.toml
?? reviews/PHASE_05_REVIEW.md
?? src/spy_market_agent/modeling/__init__.py
?? src/spy_market_agent/modeling/evaluation.py
?? src/spy_market_agent/modeling/models.py
?? src/spy_market_agent/modeling/selection.py
?? src/spy_market_agent/modeling/training.py
?? tests/integration/test_phase5_modeling_flow.py
?? tests/unit/modeling_helpers.py
?? tests/unit/test_model_evaluation.py
?? tests/unit/test_model_selection.py
?? tests/unit/test_model_training.py
?? tests/unit/test_public_phase5_api.py
```

Tracked-file `git diff --stat` before this review report:

```text
 README.md      | 36 +++++++++++++++++++++++++-----------
 pyproject.toml |  3 +++
 2 files changed, 28 insertions(+), 11 deletions(-)
```

Ordinary `git diff --stat` does not include untracked Phase 5 files.

No `.venv` or `venv` directory was deleted, recreated, replaced, or cleaned. No commit or push was performed.

## Final checklist

- [x] Phase 5 logistic-regression baseline implemented.
- [x] Phase 5 gradient-boosting comparison implemented.
- [x] Candidate training uses train only.
- [x] Candidate selection uses validation only.
- [x] Candidate training and selection APIs do not accept test data.
- [x] Locked model refit uses train plus validation only.
- [x] Final test evaluation is separate and cannot change selection.
- [x] Structured Phase 5 errors implemented.
- [x] Public modeling API exports are explicit.
- [x] README updated.
- [x] Required verification commands passed sequentially.
- [x] No secrets added.
- [x] No market-data downloading added.
- [x] No broker access added.
- [x] No paper-order submission added.
- [x] No live trading added.
- [x] Phase 6 not started.

## Integrity correction addendum

Correction timestamp: 2026-07-25 13:48:19 UTC.

Correction status: Completed. This addendum records a narrowly scoped Phase 5 integrity correction only. Phase 6 was not started.

Problems corrected:

- `select_locked_model()` now requires a `ModelTrainingConfig`, revalidates candidate results, binds candidate random seeds to the supplied config, verifies candidate prediction and metric thresholds match the config, and requires candidate fixed parameter snapshots to equal the canonical fixed specs for the configured seed.
- `LockedModelSelection` now reconstructs nested `CandidateMetricSnapshot` and `ModelParameterSet` objects from primitive fields during validation, verifies canonical parameters for the locked seed, checks validation row/class-count agreement, and recomputes the validation-only selection decision from reconstructed snapshots.
- Selection tie-break flags now mean that the corresponding selection level was actually needed: ROC-only selection sets all flags false, log-loss selection sets ROC and log-loss flags, Brier selection sets all three, and the complete tie also sets all three before the logistic simplicity rule.
- `ClassificationMetrics` now rejects single-class metric objects, inconsistent class/confusion counts, and negative log loss.
- `CandidateMetricSnapshot` now rejects single-class snapshots, negative log loss, and out-of-bounds Brier or ROC AUC values.
- `CandidateModelResult`, `CandidateModelComparison`, `FinalModelBundle`, and `FinalTestEvaluation` now deeply revalidate nested Phase 5 metadata and enforce stronger lineage, threshold, count, split, dependency-version, and canonical-parameter consistency.
- Final fitted model bundles now require learned estimator classes exactly `{0, 1}` before acceptance, without calling `.fit()` during validation.
- Public Phase 5 functions now reject malformed `None`, boolean seed, non-DataFrame feature, missing config, and wrong object-type inputs through project-owned modeling errors.

Files changed during this correction:

- `src/spy_market_agent/modeling/models.py`
- `src/spy_market_agent/modeling/training.py`
- `src/spy_market_agent/modeling/evaluation.py`
- `src/spy_market_agent/modeling/selection.py`
- `tests/unit/test_model_training.py`
- `tests/unit/test_model_evaluation.py`
- `tests/unit/test_model_selection.py`
- `reviews/PHASE_05_REVIEW.md`

Tests added or updated:

- Mismatched candidate/config seeds fail during locking.
- Candidate prediction and metric thresholds must match the selection config.
- Mutated locked metric snapshots fail final refit.
- Mutated locked parameter snapshots fail final refit.
- Locked selected model must match the recomputed validation snapshot decision.
- Inconsistent confusion/class counts fail direct metric construction.
- Negative log loss fails direct metric and snapshot construction.
- Wrong final schema lineage and wrong scikit-learn versions are rejected.
- Public malformed `None` inputs produce structured modeling errors.
- Boolean estimator seed fails with `ModelTrainingError`.
- Tie-break flag expectations were updated to match the corrected semantics.

Verification commands run sequentially after the correction:

```bash
.venv/bin/pytest
.venv/bin/pytest tests/unit -q
.venv/bin/pytest tests/integration -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy src tests
.venv/bin/python -c "import spy_market_agent; print(spy_market_agent.__version__)"
.venv/bin/python -c "import spy_market_agent.modeling as modeling; print(sorted(modeling.__all__))"
git diff --check
```

Actual verification results:

- `.venv/bin/pytest`: Passed, `294 passed`, `4 warnings`, total coverage `78%`.
- `.venv/bin/pytest tests/unit -q`: Passed, all unit tests passed, `4 warnings`, total coverage `78%`.
- `.venv/bin/pytest tests/integration -q`: Passed, `3` integration tests passed, `4 warnings`, integration-only coverage `66%`.
- `.venv/bin/ruff check .`: Passed with `All checks passed!`.
- `.venv/bin/ruff format --check .`: Passed with `56 files already formatted`.
- `.venv/bin/mypy src tests`: Passed with `Success: no issues found in 49 source files`.
- `.venv/bin/python -c "import spy_market_agent; print(spy_market_agent.__version__)"`: Passed and printed `0.1.0`.
- `.venv/bin/python -c "import spy_market_agent.modeling as modeling; print(sorted(modeling.__all__))"`: Passed and printed the explicit public Phase 5 API list.
- `git diff --check`: Passed with no output.

Remaining warnings:

- Four visible third-party warnings remain from `exchange-calendars` / pandas / NumPy timedelta internals. They were not globally suppressed.
- The first post-correction `ruff format --check .` found four Python files requiring formatting. `ruff format .` was run, and the full required verification sequence was rerun from the beginning.

No dependencies were added, removed, or changed during this correction. No feature definitions, label definitions, chronological split behavior, model specifications, selection priority, diagnostic calculations on valid inputs, or train/validation/test boundaries were changed. No `.venv` or `venv` directory was deleted, recreated, replaced, or cleaned. No commit or push was performed.

Confirmation: Phase 6 was not started. No strategy signals, recommendations, backtesting, position sizing, risk calculations, persistence, APIs, dashboards, broker communication, paper-order submission, live trading, scheduled jobs, or deployment behavior was added.

## Final gradient learned-state addendum

Correction timestamp: 2026-07-25 15:13:53 UTC.

Correction status: Completed. This addendum records one narrowly scoped Phase 5 gradient learned-state correction only. Phase 6 was not started.

Problems corrected:

- Gradient-boosting `estimators_` validation now inspects every fitted stage estimator instead of accepting object-array shape alone.
- Each gradient stage must be a fitted `DecisionTreeRegressor`, expose learned `tree_`, and report the Phase 4 feature count.
- Gradient-boosting `init_` validation now requires fitted behavior, binary learned classes where exposed, matching feature count where exposed, and a valid non-mutating `predict_proba` smoke check.
- `validate_fitted_estimator_spec()` now performs one deterministic non-mutating `predict_proba` smoke check for accepted estimators, verifying probability row count, class-column count, finite bounded values, and row probability normalization.
- Expected smoke-check failures are converted into the supplied project-owned modeling error type, preserving `ModelTrainingError` for candidate results and `LockedModelError` for final bundles.

Files modified during this correction:

- `src/spy_market_agent/modeling/models.py`
- `tests/unit/test_model_training.py`
- `reviews/PHASE_05_REVIEW.md`

Tests added or updated:

- Candidate gradient results reject `estimators_` containing plain `object()` instances.
- Candidate gradient results reject `estimators_` containing unfitted `DecisionTreeRegressor` objects.
- Candidate gradient results reject invalid `init_`.
- Candidate gradient results reject fitted-state metadata that passes shape checks but fails `predict_proba`.
- Final model bundles reject the same invalid gradient stage, initializer, and probability-smoke states.
- Genuine fitted logistic and gradient candidate estimators remain accepted.
- Genuine fitted final model bundles remain accepted.
- Fitted-state validation is covered by a no-`.fit()` regression.

Verification commands run sequentially after the correction:

```bash
pytest
pytest tests/unit -q
pytest tests/integration -q
ruff check .
ruff format --check .
mypy src tests
python -c "import spy_market_agent; print(spy_market_agent.__version__)"
python -c "import spy_market_agent.modeling as modeling; print(sorted(modeling.__all__))"
git diff --check
```

Actual verification results:

- `pytest`: Passed, `339 passed`, `4 warnings`, total coverage `78%`.
- `pytest tests/unit -q`: Passed, `336` unit tests passed, `4 warnings`, total coverage `78%`.
- `pytest tests/integration -q`: Passed, `3` integration tests passed, `4 warnings`, integration-only coverage `66%`.
- `ruff check .`: Passed with `All checks passed!`.
- `ruff format --check .`: Passed with `56 files already formatted`.
- `mypy src tests`: Passed with `Success: no issues found in 49 source files`.
- `python -c "import spy_market_agent; print(spy_market_agent.__version__)"`: Passed and printed `0.1.0`.
- `python -c "import spy_market_agent.modeling as modeling; print(sorted(modeling.__all__))"`: Passed and printed the explicit Phase 5 public modeling API list.
- `git diff --check`: Passed with no output.

Remaining warnings:

- Four visible third-party deprecation warnings remain from `exchange-calendars` / pandas / NumPy timedelta internals. They were not globally suppressed.

No dependencies were added, removed, or changed during this correction. No model specifications, selection logic, metrics, dataset boundaries, or public API names were changed. No `.venv` or `venv` directory was deleted, recreated, replaced, or cleaned. No commit or push was performed.

Confirmation: Phase 6 was not started. No strategy signals, recommendations, backtesting, position sizing, risk calculations, persistence, APIs, dashboards, broker communication, paper-order submission, live trading, scheduled jobs, or deployment behavior was added.

## Final fitted-state and public-boundary addendum

Correction timestamp: 2026-07-25 14:52:58 UTC.

Correction status: Completed. This addendum records one narrowly scoped final Phase 5 fitted-state and public-boundary correction only. Phase 6 was not started.

Problems corrected:

- Fitted-estimator validation now requires genuine learned state beyond manually injected `classes_`, `n_features_in_`, or `feature_names_in_` metadata.
- Logistic pipeline validation now checks the fitted pipeline, fitted `StandardScaler` learned attributes, and fitted `LogisticRegression` learned attributes without calling `.fit()`.
- Gradient-boosting validation now checks fitted boosting learned attributes, fitted stage shape, fitted stage count, and learned-score shape without calling `.fit()`.
- Candidate result, comparison, final-bundle, and reconstruction paths now reject incomplete, metadata-only, or partially stripped estimators before downstream use.
- Model-name and partition-name validators now require plain strings before membership checks, so pandas array-like values cannot raise ambiguous-truth exceptions.
- `PredictionSet` now verifies that `data` is a pandas `DataFrame` before copying, so non-DataFrame public inputs fail through `ModelEvaluationError`.

Files modified during this correction:

- `src/spy_market_agent/modeling/models.py`
- `tests/unit/test_model_training.py`
- `tests/unit/test_model_evaluation.py`
- `reviews/PHASE_05_REVIEW.md`

Tests added or updated:

- Candidate results reject fresh unfitted logistic pipelines and gradient-boosting estimators.
- Candidate results reject metadata-only logistic and gradient estimators with only manually inserted class and feature metadata.
- Candidate results reject logistic pipelines missing scaler `mean_`, classifier `coef_`, or classifier `intercept_`.
- Candidate results reject gradient estimators missing `estimators_` or `train_score_`.
- Final bundles reject incomplete logistic and gradient estimators.
- Genuine fitted logistic and gradient candidate estimators remain accepted.
- Genuine fitted final model bundles remain accepted.
- pandas `Series` and `Index` model names fail through `ModelTrainingError`.
- pandas `Series` and `Index` partition names fail through `ModelEvaluationError`.
- `PredictionSet(data=None)`, lists, dictionaries, series, and arbitrary objects fail through `ModelEvaluationError`.

Verification commands run sequentially after the correction:

```bash
pytest
pytest tests/unit -q
pytest tests/integration -q
ruff check .
ruff format --check .
mypy src tests
python -c "import spy_market_agent; print(spy_market_agent.__version__)"
python -c "import spy_market_agent.modeling as modeling; print(sorted(modeling.__all__))"
git diff --check
```

Actual verification results:

- `pytest`: Passed, `330 passed`, `4 warnings`, total coverage `79%`.
- `pytest tests/unit -q`: Passed, `327` unit tests passed, `4 warnings`, total coverage `78%`.
- `pytest tests/integration -q`: Passed, `3` integration tests passed, `4 warnings`, integration-only coverage `66%`.
- `ruff check .`: Passed with `All checks passed!`.
- `ruff format --check .`: Passed with `56 files already formatted`.
- `mypy src tests`: Passed with `Success: no issues found in 49 source files`.
- `python -c "import spy_market_agent; print(spy_market_agent.__version__)"`: Passed and printed `0.1.0`.
- `python -c "import spy_market_agent.modeling as modeling; print(sorted(modeling.__all__))"`: Passed and printed the explicit Phase 5 public modeling API list.
- `git diff --check`: Passed with no output.

Remaining warnings:

- Four visible third-party deprecation warnings remain from `exchange-calendars` / pandas / NumPy timedelta internals. They were not globally suppressed.

No dependencies were added, removed, or changed during this correction. No model specifications, feature or label definitions, selection priority or tolerance, metric formulas, train/validation/test boundaries, or public API names were changed. No `.venv` or `venv` directory was deleted, recreated, replaced, or cleaned. No commit or push was performed.

Confirmation: Phase 6 was not started. No strategy signals, recommendations, backtesting, position sizing, risk calculations, persistence, APIs, dashboards, broker communication, paper-order submission, live trading, scheduled jobs, or deployment behavior was added.

## Final estimator-integrity addendum

Correction timestamp: 2026-07-25 14:21:00 UTC.

Correction status: Completed. This addendum records one narrowly scoped final Phase 5 estimator and split-lineage correction only. Phase 6 was not started.

Problems corrected:

- Fitted candidate and final estimators are now validated against the actual approved scikit-learn estimator specification without calling `.fit()`.
- Logistic candidates and final logistic bundles must be a two-step `Pipeline` with ordered `scaler` and `classifier` steps, a canonical `StandardScaler`, and a canonical fixed-parameter `LogisticRegression`.
- Gradient-boosting candidates and final gradient bundles must be a direct canonical fixed-parameter `GradientBoostingClassifier`, not a pipeline.
- Candidate and final estimator validation now rejects missing, swapped, unfitted, or mutated estimators, including changed public parameters, missing or unexpected learned classes, wrong feature counts, and mismatched learned feature names when scikit-learn exposes them.
- Candidate result validation now binds train and validation prediction session bounds to the appropriate `ChronologicalSplitSpec` train and validation windows.
- Locked selection validation now binds recorded train and validation session bounds to the same split specification.
- Final test evaluation validation now binds test metadata and prediction session bounds to the test split window.
- Candidate comparisons now require the comparison timestamp to match both candidate result timestamps and the locked-selection timestamp.
- Final test evaluations now require the evaluation timestamp to match the nested test prediction-set and metric timestamps.
- Candidate results, locked selections, candidate comparisons, final bundles, and final test evaluations now require recorded `sklearn_version` to equal the current in-memory `sklearn.__version__`.

Files changed during this correction:

- `src/spy_market_agent/modeling/models.py`
- `tests/unit/test_model_training.py`
- `reviews/PHASE_05_REVIEW.md`

Tests added or updated:

- `CandidateModelResult` rejects `estimator=None`.
- Logistic candidate metadata rejects a gradient-boosting estimator.
- Gradient candidate metadata rejects a logistic pipeline.
- Logistic candidate metadata rejects post-fit changes to `C` and `random_state`.
- Gradient candidate metadata rejects post-fit changes to `n_estimators` and `learning_rate`.
- Candidate metadata rejects missing or incorrect learned classes and incorrect fitted feature counts.
- Final bundles reject wrong estimator types and mutated final estimators before test evaluation.
- Candidate results and locked selections reject train/validation sessions outside their split windows.
- Final test evaluations reject test sessions before the test start or after the test end.
- Runtime dependency lineage rejects a consistently tampered `"WRONG"` scikit-learn version across candidates, locked selection, and comparison.
- Candidate comparisons reject mismatched nested timestamps.
- Final test evaluations reject mismatched nested prediction/metric timestamps.
- The final test no-fit regression now patches the real fitted estimator’s `fit` method instead of substituting a noncanonical proxy, preserving estimator-spec validation.

Verification commands run sequentially after the correction:

```bash
pytest
pytest tests/unit -q
pytest tests/integration -q
ruff check .
ruff format --check .
mypy src tests
python -c "import spy_market_agent; print(spy_market_agent.__version__)"
python -c "import spy_market_agent.modeling as modeling; print(sorted(modeling.__all__))"
git diff --check
```

Actual verification results:

- `pytest`: Passed, `309 passed`, `4 warnings`, total coverage `79%`.
- `pytest tests/unit -q`: Passed, `306` unit tests passed, `4 warnings`, total coverage `79%`.
- `pytest tests/integration -q`: Passed, `3` integration tests passed, `4 warnings`, integration-only coverage `66%`.
- `ruff check .`: Passed with `All checks passed!`.
- `ruff format --check .`: Passed with `56 files already formatted`.
- `mypy src tests`: Passed with `Success: no issues found in 49 source files`.
- `python -c "import spy_market_agent; print(spy_market_agent.__version__)"`: Passed and printed `0.1.0`.
- `python -c "import spy_market_agent.modeling as modeling; print(sorted(modeling.__all__))"`: Passed and printed the explicit Phase 5 public modeling API list.
- `git diff --check`: Passed with no output.

Remaining warnings:

- Four visible third-party deprecation warnings remain from `exchange-calendars` / pandas / NumPy timedelta internals. They were not globally suppressed.

No dependencies were added, removed, or changed during this correction. No candidate model specifications, model-selection priority or tolerance, feature or label definitions, chronological split assignment, metric formulas on valid inputs, train/validation/test fitting boundaries, or public API names were changed. No `.venv` or `venv` directory was deleted, recreated, replaced, or cleaned. No commit or push was performed.

Confirmation: Phase 6 was not started. No strategy signals, recommendations, backtesting, position sizing, risk calculations, persistence, APIs, dashboards, broker communication, paper-order submission, live trading, scheduled jobs, or deployment behavior was added.
