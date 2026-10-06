from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from spy_market_agent.research.constants import (
    BOUNDARY_EXCLUSION_SESSIONS,
    DEFAULT_ASSESSMENT_WINDOW_ROWS,
    DEFAULT_STEP_ROWS,
    DIAGNOSTIC_THRESHOLD_POLICY_ID,
    ENTRY_OFFSET_SESSIONS,
    EXIT_OFFSET_SESSIONS,
    FEATURE_WARMUP_ROWS,
    MANDATORY_GAP_SESSIONS,
    MINIMUM_FINAL_ASSESSMENT_ROWS,
    MINIMUM_INITIAL_TRAINING_ROWS,
    NO_CALIBRATION_POLICY_ID,
    PHASE3_ARTIFACT_SCHEMA_VERSION,
    PHASE3_CLASSIFICATION_SELECTION_RULE_ID,
    PHASE3_PHASE_ID,
    WALK_FORWARD_FOLD_POLICY_ID,
)

PrimitiveValue = str | int | float | bool | None
SearchSpace = dict[str, tuple[PrimitiveValue, ...]]


def _is_nonempty_safe_identifier(value: str) -> bool:
    blocked = ("/", "\\", "..")
    return bool(value.strip()) and not any(part in value for part in blocked)


class ResearchArtifactModel(BaseModel):
    model_config = ConfigDict(frozen=True)

    artifact_schema_version: str = PHASE3_ARTIFACT_SCHEMA_VERSION

    @field_validator("artifact_schema_version")
    @classmethod
    def _validate_artifact_schema_version(cls, value: str) -> str:
        if value != PHASE3_ARTIFACT_SCHEMA_VERSION:
            msg = f"artifact_schema_version must be {PHASE3_ARTIFACT_SCHEMA_VERSION!r}."
            raise ValueError(msg)
        return value


class RuntimeLineage(BaseModel):
    model_config = ConfigDict(frozen=True)

    git_commit_sha: str
    package_version: str
    python_version: str
    dependency_versions: dict[str, str]

    @field_validator("git_commit_sha", "package_version", "python_version")
    @classmethod
    def _nonempty_string(cls, value: str) -> str:
        if not value.strip():
            msg = "lineage strings must be nonempty."
            raise ValueError(msg)
        return value

    @field_validator("dependency_versions")
    @classmethod
    def _dependency_versions_nonempty(cls, value: dict[str, str]) -> dict[str, str]:
        if not value:
            msg = "dependency_versions must record at least one dependency."
            raise ValueError(msg)
        for package, version in value.items():
            if not package.strip() or not version.strip():
                msg = "dependency_versions keys and values must be nonempty."
                raise ValueError(msg)
        return dict(sorted(value.items()))


class DatasetLineage(BaseModel):
    model_config = ConfigDict(frozen=True)

    dataset_id: str
    canonical_dataset_checksum: str
    provider: str
    feed: str
    timeframe: str
    adjustment: str
    first_session: date
    last_session: date

    @field_validator("dataset_id", "provider", "feed", "timeframe", "adjustment")
    @classmethod
    def _safe_lineage_string(cls, value: str) -> str:
        if not _is_nonempty_safe_identifier(value):
            msg = "lineage identifiers must be nonempty and path-safe."
            raise ValueError(msg)
        return value

    @field_validator("canonical_dataset_checksum")
    @classmethod
    def _checksum(cls, value: str) -> str:
        allowed = set("0123456789abcdef")
        if len(value) != 64 or any(character not in allowed for character in value):
            msg = "canonical_dataset_checksum must be a lowercase SHA-256 hex digest."
            raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def _valid_range(self) -> DatasetLineage:
        if self.first_session > self.last_session:
            msg = "first_session must not be after last_session."
            raise ValueError(msg)
        return self


class FoldPolicy(ResearchArtifactModel):
    fold_policy_id: str = WALK_FORWARD_FOLD_POLICY_ID
    feature_warmup_rows: int = FEATURE_WARMUP_ROWS
    entry_offset_sessions: int = ENTRY_OFFSET_SESSIONS
    exit_offset_sessions: int = EXIT_OFFSET_SESSIONS
    mandatory_gap_sessions: int = MANDATORY_GAP_SESSIONS
    boundary_exclusion_sessions: int = BOUNDARY_EXCLUSION_SESSIONS
    minimum_initial_training_rows: int = MINIMUM_INITIAL_TRAINING_ROWS
    assessment_window_rows: int = DEFAULT_ASSESSMENT_WINDOW_ROWS
    step_rows: int = DEFAULT_STEP_ROWS
    minimum_final_assessment_rows: int = MINIMUM_FINAL_ASSESSMENT_ROWS
    require_two_classes_per_fold: bool = True

    @model_validator(mode="after")
    def _validate_defaults(self) -> FoldPolicy:
        if self.feature_warmup_rows < FEATURE_WARMUP_ROWS:
            msg = "feature_warmup_rows must be at least the approved Phase 3 baseline warm-up."
            raise ValueError(msg)
        expected = {
            "entry_offset_sessions": ENTRY_OFFSET_SESSIONS,
            "exit_offset_sessions": EXIT_OFFSET_SESSIONS,
            "mandatory_gap_sessions": MANDATORY_GAP_SESSIONS,
            "boundary_exclusion_sessions": BOUNDARY_EXCLUSION_SESSIONS,
        }
        for field_name, expected_value in expected.items():
            if getattr(self, field_name) != expected_value:
                msg = f"{field_name} must be {expected_value} for the approved Phase 3 policy."
                raise ValueError(msg)
        if self.fold_policy_id != WALK_FORWARD_FOLD_POLICY_ID:
            msg = f"fold_policy_id must be {WALK_FORWARD_FOLD_POLICY_ID!r}."
            raise ValueError(msg)
        if self.minimum_initial_training_rows < MINIMUM_INITIAL_TRAINING_ROWS:
            msg = "minimum_initial_training_rows must preserve the approved minimum."
            raise ValueError(msg)
        if self.assessment_window_rows < self.minimum_final_assessment_rows:
            msg = "assessment_window_rows must be at least minimum_final_assessment_rows."
            raise ValueError(msg)
        if self.step_rows <= 0:
            msg = "step_rows must be positive."
            raise ValueError(msg)
        return self


class SessionWindow(BaseModel):
    model_config = ConfigDict(frozen=True)

    prediction_sessions: tuple[date, ...]
    entry_sessions: tuple[date, ...]
    exit_sessions: tuple[date, ...]
    positive_count: int
    negative_count: int

    @model_validator(mode="after")
    def _validate_window(self) -> SessionWindow:
        row_count = len(self.prediction_sessions)
        if row_count == 0:
            msg = "session windows must not be empty."
            raise ValueError(msg)
        if (
            len(self.entry_sessions) != row_count
            or len(self.exit_sessions) != row_count
            or self.positive_count + self.negative_count != row_count
        ):
            msg = "session window counts must match row_count."
            raise ValueError(msg)
        for field_name in ("prediction_sessions", "entry_sessions", "exit_sessions"):
            sessions = getattr(self, field_name)
            if len(sessions) != len(set(sessions)) or sessions != tuple(sorted(sessions)):
                msg = f"{field_name} must be unique and strictly increasing."
                raise ValueError(msg)
        if self.positive_count < 0 or self.negative_count < 0:
            msg = "class counts must be non-negative."
            raise ValueError(msg)
        return self

    @property
    def row_count(self) -> int:
        return len(self.prediction_sessions)

    @property
    def first_prediction_session(self) -> date:
        return self.prediction_sessions[0]

    @property
    def last_prediction_session(self) -> date:
        return self.prediction_sessions[-1]

    @property
    def first_entry_session(self) -> date:
        return self.entry_sessions[0]

    @property
    def last_entry_session(self) -> date:
        return self.entry_sessions[-1]

    @property
    def first_exit_session(self) -> date:
        return self.exit_sessions[0]

    @property
    def last_exit_session(self) -> date:
        return self.exit_sessions[-1]


class WalkForwardFold(ResearchArtifactModel):
    fold_id: str
    fold_index: int
    dataset_id: str
    canonical_dataset_checksum: str
    feature_schema: str
    label_schema: str
    fold_policy_id: str
    training: SessionWindow
    boundary_excluded_sessions: tuple[date, ...]
    assessment: SessionWindow
    runtime_lineage: RuntimeLineage

    @field_validator("fold_id", "dataset_id", "feature_schema", "label_schema", "fold_policy_id")
    @classmethod
    def _safe_ids(cls, value: str) -> str:
        if not _is_nonempty_safe_identifier(value):
            msg = "fold identifiers must be nonempty and path-safe."
            raise ValueError(msg)
        return value

    @field_validator("canonical_dataset_checksum")
    @classmethod
    def _checksum(cls, value: str) -> str:
        return DatasetLineage._checksum(value)

    @model_validator(mode="after")
    def _validate_fold_chronology(self) -> WalkForwardFold:
        if self.fold_index < 0:
            msg = "fold_index must be non-negative."
            raise ValueError(msg)
        if len(self.boundary_excluded_sessions) != BOUNDARY_EXCLUSION_SESSIONS:
            msg = f"boundary_excluded_sessions must contain {BOUNDARY_EXCLUSION_SESSIONS} rows."
            raise ValueError(msg)
        if len(self.boundary_excluded_sessions) != len(
            set(self.boundary_excluded_sessions)
        ) or self.boundary_excluded_sessions != tuple(sorted(self.boundary_excluded_sessions)):
            msg = "boundary_excluded_sessions must be unique and strictly increasing."
            raise ValueError(msg)
        if not (
            self.training.last_prediction_session
            < self.boundary_excluded_sessions[0]
            < self.boundary_excluded_sessions[-1]
            < self.assessment.first_prediction_session
        ):
            msg = "fold chronology must be training -> boundary exclusion -> assessment."
            raise ValueError(msg)
        if self.training.last_exit_session != self.boundary_excluded_sessions[-1]:
            msg = "last training exit must land on the final boundary-excluded session."
            raise ValueError(msg)
        if self.training.last_exit_session >= self.assessment.first_prediction_session:
            msg = "training labels must not cross into the assessment window."
            raise ValueError(msg)
        if self.fold_policy_id != WALK_FORWARD_FOLD_POLICY_ID:
            msg = f"fold_policy_id must be {WALK_FORWARD_FOLD_POLICY_ID!r}."
            raise ValueError(msg)
        return self


class WalkForwardManifest(ResearchArtifactModel):
    fold_manifest_id: str
    dataset_lineage: DatasetLineage
    feature_schema: str
    label_schema: str
    fold_policy: FoldPolicy
    supervised_row_count: int
    folds: tuple[WalkForwardFold, ...]

    @model_validator(mode="after")
    def _validate_manifest(self) -> WalkForwardManifest:
        if not self.folds:
            msg = "walk-forward manifest must contain at least one fold."
            raise ValueError(msg)
        if self.supervised_row_count <= 0:
            msg = "supervised_row_count must be positive."
            raise ValueError(msg)
        expected_indexes = tuple(range(len(self.folds)))
        observed_indexes = tuple(fold.fold_index for fold in self.folds)
        if observed_indexes != expected_indexes:
            msg = "fold indexes must be contiguous from zero."
            raise ValueError(msg)
        for fold in self.folds:
            if fold.dataset_id != self.dataset_lineage.dataset_id:
                msg = "fold dataset_id must match manifest dataset lineage."
                raise ValueError(msg)
            if fold.canonical_dataset_checksum != self.dataset_lineage.canonical_dataset_checksum:
                msg = "fold checksum must match manifest dataset lineage."
                raise ValueError(msg)
            if fold.feature_schema != self.feature_schema or fold.label_schema != self.label_schema:
                msg = "fold schemas must match manifest schemas."
                raise ValueError(msg)
        return self


class LeakageReviewMetadata(BaseModel):
    model_config = ConfigDict(frozen=True)

    uses_only_information_through_prediction_close: bool
    uses_trailing_window_only: bool
    centered_window: bool = False
    backward_fill: bool = False
    future_timestamp_dependency: bool = False
    notes: str

    @model_validator(mode="after")
    def _validate_review(self) -> LeakageReviewMetadata:
        if not self.uses_only_information_through_prediction_close:
            msg = "feature review must confirm point-in-time availability."
            raise ValueError(msg)
        if not self.uses_trailing_window_only:
            msg = "feature review must confirm trailing-window-only construction."
            raise ValueError(msg)
        if self.centered_window or self.backward_fill or self.future_timestamp_dependency:
            msg = "leakage review cannot approve centered, backward-filled, or future data."
            raise ValueError(msg)
        if not self.notes.strip():
            msg = "leakage review notes must be nonempty."
            raise ValueError(msg)
        return self


class FeatureDefinition(ResearchArtifactModel):
    feature_name: str
    feature_family: str
    schema_version: str
    lookback: int | None
    input_fields: tuple[str, ...]
    adjustment_policy: str
    warm_up_rows: int
    missing_value_policy: str
    description: str
    leakage_review: LeakageReviewMetadata
    enabled: bool = True

    @field_validator("feature_name", "feature_family", "schema_version", "adjustment_policy")
    @classmethod
    def _safe_strings(cls, value: str) -> str:
        if not _is_nonempty_safe_identifier(value):
            msg = "feature definition strings must be nonempty and path-safe."
            raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def _validate_definition(self) -> FeatureDefinition:
        if self.lookback is not None and self.lookback <= 0:
            msg = "lookback must be positive when supplied."
            raise ValueError(msg)
        if not self.input_fields:
            msg = "input_fields must not be empty."
            raise ValueError(msg)
        if self.warm_up_rows < 0:
            msg = "warm_up_rows must be non-negative."
            raise ValueError(msg)
        if not self.missing_value_policy.strip() or not self.description.strip():
            msg = "feature missing-value policy and description must be nonempty."
            raise ValueError(msg)
        return self


class FeatureRegistry(ResearchArtifactModel):
    feature_schema: str
    features: tuple[FeatureDefinition, ...]

    @model_validator(mode="after")
    def _validate_registry(self) -> FeatureRegistry:
        if not self.features:
            msg = "feature registry must contain at least one feature."
            raise ValueError(msg)
        names = tuple(feature.feature_name for feature in self.features)
        if len(names) != len(set(names)):
            msg = "feature names must be unique."
            raise ValueError(msg)
        if any(feature.schema_version != self.feature_schema for feature in self.features):
            msg = "feature schema versions must match the registry schema."
            raise ValueError(msg)
        return self

    @property
    def enabled_feature_families(self) -> tuple[str, ...]:
        return tuple(
            sorted({feature.feature_family for feature in self.features if feature.enabled})
        )

    @property
    def enabled_feature_names(self) -> tuple[str, ...]:
        return tuple(feature.feature_name for feature in self.features if feature.enabled)


class AblationExperimentDefinition(ResearchArtifactModel):
    ablation_id: str
    mode: Literal[
        "baseline", "add_one_family", "remove_one_family", "all_features", "simpler_subset"
    ]
    baseline_feature_families: tuple[str, ...]
    candidate_feature_families: tuple[str, ...]
    fold_policy_id: str
    comparator_model_family: str
    status: Literal["planned", "passed", "failed", "neutral", "harmful"] = "planned"
    notes: str = ""

    @model_validator(mode="after")
    def _validate_ablation(self) -> AblationExperimentDefinition:
        if not _is_nonempty_safe_identifier(self.ablation_id):
            msg = "ablation_id must be path-safe."
            raise ValueError(msg)
        if self.fold_policy_id != WALK_FORWARD_FOLD_POLICY_ID:
            msg = "ablation comparator experiments must reuse the approved fold policy."
            raise ValueError(msg)
        if not self.baseline_feature_families or not self.candidate_feature_families:
            msg = "ablation feature-family sets must not be empty."
            raise ValueError(msg)
        return self


class ModelDefinition(ResearchArtifactModel):
    model_name: str
    model_family: str
    model_schema_version: str
    parameters: tuple[tuple[str, PrimitiveValue], ...]
    deterministic_probability_output: bool
    approved_dependency: str = "scikit-learn"
    baseline_role: str | None = None

    @field_validator("model_name", "model_family", "model_schema_version", "approved_dependency")
    @classmethod
    def _safe_model_strings(cls, value: str) -> str:
        if not _is_nonempty_safe_identifier(value):
            msg = "model definition strings must be nonempty and path-safe."
            raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def _validate_model(self) -> ModelDefinition:
        forbidden_families = {"deep_learning", "automl", "external_api", "gpu", "cloud_service"}
        if self.model_family in forbidden_families:
            msg = f"{self.model_family} is not authorized for Phase 3 scaffolding."
            raise ValueError(msg)
        if not self.parameters:
            msg = "model parameters must not be empty."
            raise ValueError(msg)
        if not self.deterministic_probability_output:
            msg = "model candidates must expose deterministic probability or score outputs."
            raise ValueError(msg)
        return self


class ModelRegistry(ResearchArtifactModel):
    model_schema_version: str
    models: tuple[ModelDefinition, ...]

    @model_validator(mode="after")
    def _validate_registry(self) -> ModelRegistry:
        if not self.models:
            msg = "model registry must contain at least one model."
            raise ValueError(msg)
        names = tuple(model.model_name for model in self.models)
        if len(names) != len(set(names)):
            msg = "model names must be unique."
            raise ValueError(msg)
        if any(model.model_schema_version != self.model_schema_version for model in self.models):
            msg = "model schema versions must match registry schema."
            raise ValueError(msg)
        return self


class HyperparameterSearchDefinition(ResearchArtifactModel):
    search_method: Literal["none", "grid", "fixed_seed_random", "inner_walk_forward"]
    search_space: SearchSpace = Field(default_factory=dict)
    random_seed: int | None = None
    trial_count: int = 0
    scoring_rule: str = "median_walk_forward_roc_auc"
    failure_policy: str = "record_and_continue"
    selection_scope: Literal[
        "inner_training_only",
        "outer_assessment",
        "protected_evaluation",
    ] = "inner_training_only"

    @model_validator(mode="after")
    def _validate_search(self) -> HyperparameterSearchDefinition:
        if self.search_method == "none":
            if self.search_space or self.trial_count != 0:
                msg = "none search must not define a search space or trials."
                raise ValueError(msg)
            return self
        if not self.search_space:
            msg = "search_space must be finite and predeclared."
            raise ValueError(msg)
        for parameter, values in self.search_space.items():
            if not _is_nonempty_safe_identifier(parameter) or not values:
                msg = "search_space parameters must be path-safe and have finite values."
                raise ValueError(msg)
        if self.search_method == "fixed_seed_random" and self.random_seed is None:
            msg = "fixed_seed_random search requires random_seed."
            raise ValueError(msg)
        if self.trial_count <= 0:
            msg = "search methods other than none require positive trial_count."
            raise ValueError(msg)
        return self


class HyperparameterTrialRecord(ResearchArtifactModel):
    trial_index: int
    configuration: dict[str, PrimitiveValue]
    status: Literal["planned", "completed", "failed"]
    failure_reason: str | None = None

    @model_validator(mode="after")
    def _validate_trial(self) -> HyperparameterTrialRecord:
        if self.trial_index < 0:
            msg = "trial_index must be non-negative."
            raise ValueError(msg)
        if self.status == "failed" and not self.failure_reason:
            msg = "failed trials must record a failure reason."
            raise ValueError(msg)
        if not self.configuration:
            msg = "trial configuration must not be empty."
            raise ValueError(msg)
        return self


class CalibrationPolicy(ResearchArtifactModel):
    calibration_policy_id: str = NO_CALIBRATION_POLICY_ID
    method: Literal["none", "sigmoid", "isotonic"] = "none"
    calibration_window_rows: int = 126
    inner_boundary_exclusion_rows: int = BOUNDARY_EXCLUSION_SESSIONS
    minimum_calibration_rows: int = MINIMUM_FINAL_ASSESSMENT_ROWS
    require_two_classes: bool = True

    @model_validator(mode="after")
    def _validate_policy(self) -> CalibrationPolicy:
        if self.method == "none":
            if self.calibration_policy_id != NO_CALIBRATION_POLICY_ID:
                msg = f"none calibration must use {NO_CALIBRATION_POLICY_ID!r}."
                raise ValueError(msg)
            return self
        if self.calibration_policy_id == NO_CALIBRATION_POLICY_ID:
            msg = "calibrated policies must use a non-baseline calibration_policy_id."
            raise ValueError(msg)
        if self.calibration_window_rows < self.minimum_calibration_rows:
            msg = "calibration_window_rows must meet the minimum calibration rows."
            raise ValueError(msg)
        if self.inner_boundary_exclusion_rows != BOUNDARY_EXCLUSION_SESSIONS:
            msg = "calibration inner boundary must preserve the six-row exclusion."
            raise ValueError(msg)
        return self


class CalibrationSplit(ResearchArtifactModel):
    fold_id: str
    estimator_training_sessions: tuple[date, ...]
    inner_boundary_excluded_sessions: tuple[date, ...]
    calibration_sessions: tuple[date, ...]
    outer_boundary_excluded_sessions: tuple[date, ...]

    @model_validator(mode="after")
    def _validate_split(self) -> CalibrationSplit:
        if not self.estimator_training_sessions or not self.calibration_sessions:
            msg = "calibration split must contain estimator and calibration rows."
            raise ValueError(msg)
        if len(self.inner_boundary_excluded_sessions) != BOUNDARY_EXCLUSION_SESSIONS:
            msg = "inner boundary exclusion must contain six sessions."
            raise ValueError(msg)
        if len(self.outer_boundary_excluded_sessions) != BOUNDARY_EXCLUSION_SESSIONS:
            msg = "outer boundary exclusion must contain six sessions."
            raise ValueError(msg)
        if not (
            self.estimator_training_sessions[-1]
            < self.inner_boundary_excluded_sessions[0]
            < self.inner_boundary_excluded_sessions[-1]
            < self.calibration_sessions[0]
            < self.calibration_sessions[-1]
            < self.outer_boundary_excluded_sessions[0]
        ):
            msg = (
                "calibration split chronology must be estimator -> inner gap "
                "-> calibration -> outer gap."
            )
            raise ValueError(msg)
        return self


class ThresholdPolicy(ResearchArtifactModel):
    threshold_policy_id: str = DIAGNOSTIC_THRESHOLD_POLICY_ID
    policy_role: Literal["diagnostic_classification", "strategy_research"] = (
        "diagnostic_classification"
    )
    fixed_diagnostic_threshold: float = 0.5
    candidate_thresholds: tuple[float, ...] = ()
    optimization_objective: str | None = None
    selection_rule: str = "fixed_0.5"
    exposure_constraint: float | None = None
    turnover_constraint: float | None = None

    @model_validator(mode="after")
    def _validate_thresholds(self) -> ThresholdPolicy:
        if self.fixed_diagnostic_threshold != 0.5:
            msg = "the fixed diagnostic classification threshold must remain 0.5."
            raise ValueError(msg)
        for threshold in self.candidate_thresholds:
            if not 0.0 < threshold < 1.0:
                msg = "candidate thresholds must be strictly between 0 and 1."
                raise ValueError(msg)
        if self.policy_role == "diagnostic_classification" and self.candidate_thresholds:
            msg = "diagnostic classification policy cannot include researched thresholds."
            raise ValueError(msg)
        if self.policy_role == "strategy_research" and (
            not self.candidate_thresholds or not self.optimization_objective
        ):
            msg = "strategy threshold research requires candidates and an objective."
            raise ValueError(msg)
        return self


class MetricValue(BaseModel):
    model_config = ConfigDict(frozen=True)

    value: float | None
    undefined_reason: str | None = None

    @model_validator(mode="after")
    def _validate_metric_value(self) -> MetricValue:
        if self.value is None and not self.undefined_reason:
            msg = "undefined metrics must record a reason."
            raise ValueError(msg)
        if self.value is not None and self.undefined_reason is not None:
            msg = "defined metrics must not record undefined_reason."
            raise ValueError(msg)
        return self


class ClassificationMetricSet(ResearchArtifactModel):
    model_name: str
    fold_id: str
    row_count: int
    positive_count: int
    negative_count: int
    predicted_positive_count: int
    prevalence: float
    predicted_positive_rate: float
    confusion_matrix: dict[str, int]
    metrics: dict[str, MetricValue]
    reliability_bins: tuple[dict[str, float | int | str], ...] = ()


class MetricAggregate(ResearchArtifactModel):
    metric_name: str
    per_fold: tuple[MetricValue, ...]
    mean: MetricValue
    median: MetricValue
    standard_deviation: MetricValue
    interquartile_range: MetricValue
    worst_fold: MetricValue
    best_fold: MetricValue
    defined_fold_count: int
    baseline_comparison: MetricValue | None = None


class BaselineDefinition(ResearchArtifactModel):
    baseline_name: str
    baseline_type: Literal[
        "phase2_fixed_model",
        "majority_class",
        "always_positive",
        "always_negative",
        "training_prevalence_probability",
        "strategy",
    ]
    probability_source: str
    uses_training_data_only: bool
    threshold: float | None = 0.5

    @model_validator(mode="after")
    def _validate_baseline(self) -> BaselineDefinition:
        if (
            self.baseline_type
            in {
                "majority_class",
                "training_prevalence_probability",
            }
            and not self.uses_training_data_only
        ):
            msg = "training-derived baselines must use training data only."
            raise ValueError(msg)
        return self


class StrategyAssumptions(ResearchArtifactModel):
    signal_policy: str
    initial_cash: str
    whole_shares_only: bool
    risk_policy: str


class CostAssumptions(ResearchArtifactModel):
    cost_scenario: str
    commission_bps_per_side: str
    slippage_bps_per_side: str


class ProtectedEvaluationStatus(ResearchArtifactModel):
    state: Literal["not_configured", "scaffolded_locked_no_access", "accessed", "completed"] = (
        "not_configured"
    )
    owner_acknowledged: bool = False
    protected_labels_loaded: bool = False

    @model_validator(mode="after")
    def _validate_status(self) -> ProtectedEvaluationStatus:
        if self.protected_labels_loaded and self.state not in {"accessed", "completed"}:
            msg = "protected labels cannot be loaded before an accessed protected state."
            raise ValueError(msg)
        if self.protected_labels_loaded and not self.owner_acknowledged:
            msg = "protected label access requires owner acknowledgement."
            raise ValueError(msg)
        return self


class CandidateSelectionConfig(ResearchArtifactModel):
    minimum_valid_fold_count: int = 3
    material_roc_auc_delta: float
    materially_different_tolerance: float = 0.0

    @model_validator(mode="after")
    def _validate_config(self) -> CandidateSelectionConfig:
        if self.minimum_valid_fold_count <= 0:
            msg = "minimum_valid_fold_count must be positive."
            raise ValueError(msg)
        if self.material_roc_auc_delta <= 0.0:
            msg = "material_roc_auc_delta must be predeclared and greater than zero."
            raise ValueError(msg)
        if self.materially_different_tolerance < 0:
            msg = "selection deltas and tolerances must be non-negative."
            raise ValueError(msg)
        return self


class ExperimentManifest(ResearchArtifactModel):
    experiment_id: str
    phase_identifier: str = PHASE3_PHASE_ID
    dataset_lineage: DatasetLineage
    feature_registry: FeatureRegistry
    enabled_feature_families: tuple[str, ...]
    label_schema: str
    forecast_horizon: str
    fold_policy_id: str
    fold_boundaries: tuple[WalkForwardFold, ...]
    model_family: str
    model_configuration: ModelDefinition
    hyperparameter_search: HyperparameterSearchDefinition
    tried_configurations: tuple[HyperparameterTrialRecord, ...] = ()
    calibration_policy: CalibrationPolicy
    threshold_policy: ThresholdPolicy
    strategy_assumptions: StrategyAssumptions | None = None
    cost_assumptions: CostAssumptions | None = None
    random_seeds: tuple[int, ...]
    baseline_definitions: tuple[BaselineDefinition, ...]
    metric_definitions: tuple[str, ...]
    candidate_selection_rule: str = PHASE3_CLASSIFICATION_SELECTION_RULE_ID
    candidate_selection_config: CandidateSelectionConfig
    protected_evaluation_status: ProtectedEvaluationStatus
    runtime_lineage: RuntimeLineage
    creation_timestamp: datetime
    owner_operator_notes: str = ""

    @model_validator(mode="after")
    def _validate_manifest(self) -> ExperimentManifest:
        if self.phase_identifier != PHASE3_PHASE_ID:
            msg = f"phase_identifier must be {PHASE3_PHASE_ID!r}."
            raise ValueError(msg)
        if not _is_nonempty_safe_identifier(self.experiment_id):
            msg = "experiment_id must be path-safe."
            raise ValueError(msg)
        if self.enabled_feature_families != self.feature_registry.enabled_feature_families:
            msg = "enabled_feature_families must match the feature registry."
            raise ValueError(msg)
        if self.fold_policy_id != WALK_FORWARD_FOLD_POLICY_ID:
            msg = "fold_policy_id must use the approved Phase 3 policy."
            raise ValueError(msg)
        if not self.fold_boundaries:
            msg = "experiment manifests must capture exact fold boundaries."
            raise ValueError(msg)
        for fold in self.fold_boundaries:
            if fold.dataset_id != self.dataset_lineage.dataset_id:
                msg = "fold dataset_id must match experiment dataset lineage."
                raise ValueError(msg)
            if fold.canonical_dataset_checksum != self.dataset_lineage.canonical_dataset_checksum:
                msg = "fold checksum must match experiment dataset lineage."
                raise ValueError(msg)
            if fold.feature_schema != self.feature_registry.feature_schema:
                msg = "fold feature schema must match experiment feature registry."
                raise ValueError(msg)
            if fold.label_schema != self.label_schema:
                msg = "fold label schema must match experiment label schema."
                raise ValueError(msg)
            if fold.fold_policy_id != self.fold_policy_id:
                msg = "fold policy IDs must match experiment fold policy."
                raise ValueError(msg)
            if fold.runtime_lineage != self.runtime_lineage:
                msg = "fold runtime lineage must match experiment runtime lineage."
                raise ValueError(msg)
        if self.model_configuration.model_family != self.model_family:
            msg = "model_family must match model_configuration."
            raise ValueError(msg)
        if self.hyperparameter_search.selection_scope != "inner_training_only":
            msg = "hyperparameter search must not use outer assessment or protected rows."
            raise ValueError(msg)
        if not self.random_seeds:
            msg = "random_seeds must not be empty."
            raise ValueError(msg)
        if not self.baseline_definitions:
            msg = "baseline_definitions must not be empty."
            raise ValueError(msg)
        if not self.metric_definitions:
            msg = "metric_definitions must not be empty."
            raise ValueError(msg)
        if self.candidate_selection_rule != PHASE3_CLASSIFICATION_SELECTION_RULE_ID:
            msg = "candidate_selection_rule must use the approved Phase 3 selection rule."
            raise ValueError(msg)
        forbidden_note_terms = ("secret", "password", "api_key", "account_id")
        lowered_notes = self.owner_operator_notes.lower()
        if any(term in lowered_notes for term in forbidden_note_terms):
            msg = "owner_operator_notes must not contain secrets or account identifiers."
            raise ValueError(msg)
        return self


class CandidateEvaluationSummary(ResearchArtifactModel):
    candidate_name: str
    valid: bool
    leaky: bool = False
    lineage_complete: bool = True
    simplicity_rank: int = 100
    valid_fold_count: int
    median_roc_auc: MetricValue
    median_log_loss: MetricValue
    median_brier_score: MetricValue
    worst_quartile_roc_auc: MetricValue
    median_training_prevalence_log_loss_delta: MetricValue
    median_training_prevalence_brier_delta: MetricValue
    phase2_baseline_roc_auc_delta: MetricValue


class CandidateSelectionResult(ResearchArtifactModel):
    selected_candidate_name: str | None
    promotion_allowed: bool
    reason: str
    ranked_candidates: tuple[str, ...]
