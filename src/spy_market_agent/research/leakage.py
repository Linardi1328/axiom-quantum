from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from spy_market_agent.datasets.models import FORBIDDEN_MODEL_FEATURE_COLUMNS
from spy_market_agent.research.errors import LeakageValidationError, raise_research_error
from spy_market_agent.research.models import WalkForwardFold
from spy_market_agent.research.types import ResearchSupervisedDatasetLike

FUTURE_COLUMN_TOKENS = (
    "future",
    "forward_return",
    "gross_forward_return",
    "net_forward_return",
    "target",
    "label",
    "exit_session",
    "entry_session",
)

PHASE2_FINAL_TEST_ARTIFACT_TOKENS = (
    "final_test_results.json",
    "final_test_access.json",
    "final_test_completion.json",
    "final_test_predictions.json",
    "final_test_prediction_rows.json",
    "final_test_model_predictions.json",
    "phase2_final_test_predictions.json",
    "benchmark_strategy_rows.json",
    "final_test_strategy_rows.json",
    "phase2_final_test_strategy_rows.json",
    "backtest_results.json",
    "cost_sensitivity.json",
    "regime_results.json",
)

PHASE2_FINAL_TEST_ARTIFACT_NAMES = frozenset(PHASE2_FINAL_TEST_ARTIFACT_TOKENS)


class FeatureGenerationPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    uses_trailing_windows_only: bool = True
    uses_centered_windows: bool = False
    uses_backward_fill: bool = False
    uses_future_timestamps: bool = False

    @model_validator(mode="after")
    def _validate_policy(self) -> FeatureGenerationPolicy:
        if not self.uses_trailing_windows_only:
            msg = "Phase 3 feature generation must use trailing windows only."
            raise ValueError(msg)
        if self.uses_centered_windows:
            msg = "centered rolling windows are prohibited."
            raise ValueError(msg)
        if self.uses_backward_fill:
            msg = "future-informed backward filling is prohibited."
            raise ValueError(msg)
        if self.uses_future_timestamps:
            msg = "future timestamps are prohibited in feature generation."
            raise ValueError(msg)
        return self


class TransformationFitRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    record_name: str
    transformer_type: Literal[
        "scaler",
        "imputer",
        "encoder",
        "feature_selector",
        "model",
        "calibrator",
        "threshold",
        "class_weighting",
        "regime_threshold",
    ]
    fitted_sessions: tuple[date, ...]
    fitted_on_outer_assessment: bool = False
    fitted_on_protected_rows: bool = False

    @model_validator(mode="after")
    def _validate_record(self) -> TransformationFitRecord:
        if not self.record_name.strip():
            msg = "record_name must be nonempty."
            raise ValueError(msg)
        if not self.fitted_sessions:
            msg = "fitted_sessions must not be empty."
            raise ValueError(msg)
        if len(self.fitted_sessions) != len(set(self.fitted_sessions)):
            msg = "fitted_sessions must not contain duplicates."
            raise ValueError(msg)
        if self.fitted_sessions != tuple(sorted(self.fitted_sessions)):
            msg = "fitted_sessions must be chronological."
            raise ValueError(msg)
        return self


def validate_no_forbidden_feature_columns(columns: tuple[str, ...] | list[str]) -> None:
    observed = tuple(columns)
    forbidden = sorted(FORBIDDEN_MODEL_FEATURE_COLUMNS & set(observed))
    token_hits = sorted(
        column
        for column in observed
        if any(token in column.lower() for token in FUTURE_COLUMN_TOKENS)
    )
    if forbidden or token_hits:
        combined = tuple(sorted(set(forbidden + token_hits)))
        raise_research_error(
            LeakageValidationError,
            "forbidden_model_feature_columns",
            f"model input columns contain target or future-return fields: {combined}.",
        )


def validate_supervised_leakage_contract(supervised: ResearchSupervisedDatasetLike) -> None:
    validate_no_forbidden_feature_columns(tuple(supervised.features.columns))
    labels = supervised.labels.reset_index(drop=True)
    for row_number, row in enumerate(labels.itertuples(index=False), start=1):
        session = row.session
        entry_session = row.entry_session
        exit_session = row.exit_session
        if not session < entry_session < exit_session:
            raise_research_error(
                LeakageValidationError,
                "invalid_prediction_entry_exit_timeline",
                f"row {row_number} must satisfy prediction < entry < exit.",
            )


def validate_no_future_sessions(
    sessions: tuple[date, ...],
    *,
    latest_allowed_session: date,
    scope_name: str,
) -> None:
    future_sessions = tuple(session for session in sessions if session > latest_allowed_session)
    if future_sessions:
        raise_research_error(
            LeakageValidationError,
            "future_session_in_scope",
            f"{scope_name} contains sessions after the latest allowed session.",
        )


def validate_feature_generation_policy(policy: FeatureGenerationPolicy) -> None:
    try:
        FeatureGenerationPolicy(
            uses_trailing_windows_only=policy.uses_trailing_windows_only,
            uses_centered_windows=policy.uses_centered_windows,
            uses_backward_fill=policy.uses_backward_fill,
            uses_future_timestamps=policy.uses_future_timestamps,
        )
    except ValueError as exc:
        raise_research_error(
            LeakageValidationError,
            "invalid_feature_generation_policy",
            str(exc),
        )


def validate_training_only_fit_scope(
    fold: WalkForwardFold,
    fit_record: TransformationFitRecord,
    *,
    protected_sessions: tuple[date, ...] = (),
) -> None:
    training_sessions = set(fold.training.prediction_sessions)
    assessment_sessions = set(fold.assessment.prediction_sessions)
    boundary_sessions = set(fold.boundary_excluded_sessions)
    fitted_sessions = set(fit_record.fitted_sessions)
    if fit_record.fitted_on_outer_assessment or fitted_sessions & assessment_sessions:
        raise_research_error(
            LeakageValidationError,
            "outer_assessment_used_for_fit",
            f"{fit_record.record_name} used outer assessment rows for fitting.",
        )
    if fitted_sessions & boundary_sessions:
        raise_research_error(
            LeakageValidationError,
            "boundary_rows_used_for_fit",
            f"{fit_record.record_name} used purged boundary rows for fitting.",
        )
    if fit_record.fitted_on_protected_rows or fitted_sessions & set(protected_sessions):
        raise_research_error(
            LeakageValidationError,
            "protected_rows_used_for_fit",
            f"{fit_record.record_name} used protected evaluation rows for fitting.",
        )
    if not fitted_sessions.issubset(training_sessions):
        raise_research_error(
            LeakageValidationError,
            "non_training_rows_used_for_fit",
            f"{fit_record.record_name} must fit only on eligible training sessions.",
        )


def validate_phase2_final_test_isolation(paths_or_names: tuple[str, ...] | list[str]) -> None:
    unsafe = tuple(
        value for value in paths_or_names if _is_phase2_final_test_artifact_reference(value)
    )
    if unsafe:
        raise_research_error(
            LeakageValidationError,
            "phase2_final_test_artifact_rejected",
            "Phase 2 final-test artifacts must not be loaded for Phase 3 research.",
        )


def _is_phase2_final_test_artifact_reference(value: str) -> bool:
    normalized = value.replace("\\", "/").lower()
    artifact_name = normalized.rsplit("/", maxsplit=1)[-1]
    if artifact_name in PHASE2_FINAL_TEST_ARTIFACT_NAMES:
        return True
    return "artifacts/benchmarks" in normalized and any(
        token in normalized for token in PHASE2_FINAL_TEST_ARTIFACT_TOKENS
    )
