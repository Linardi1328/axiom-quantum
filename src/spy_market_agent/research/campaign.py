from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, ValidationError, model_validator

from spy_market_agent.benchmark.artifacts import sha256_json
from spy_market_agent.research.constants import (
    BOUNDARY_EXCLUSION_SESSIONS,
    DEFAULT_ASSESSMENT_WINDOW_ROWS,
    DEFAULT_STEP_ROWS,
    MINIMUM_FINAL_ASSESSMENT_ROWS,
    MINIMUM_INITIAL_TRAINING_ROWS,
    PHASE3_ARTIFACT_SCHEMA_VERSION,
)
from spy_market_agent.research.errors import ResearchRegistryError, raise_research_error
from spy_market_agent.research.features import GLOBAL_DEVELOPMENT_FEATURE_WARMUP_ROWS
from spy_market_agent.research.models import (
    CandidateSelectionConfig,
    FoldPolicy,
    ResearchArtifactModel,
)

CAMPAIGN_ID_VERSION = "spy-v2-phase3-development-campaign-id-v1"


class ResearchCampaignConfig(ResearchArtifactModel):
    campaign_config_id: str = "phase3-development-classification-campaign-v1"
    random_seed: int = 42
    minimum_valid_fold_count: int = 3
    material_roc_auc_delta: float = 0.01
    materially_different_tolerance: float = 0.005
    diagnostic_classification_threshold: float = 0.5
    reliability_bin_count: int = 10
    small_regime_cell_rows: int = 30
    global_feature_warmup_rows: int = GLOBAL_DEVELOPMENT_FEATURE_WARMUP_ROWS
    assessment_window_rows: int = DEFAULT_ASSESSMENT_WINDOW_ROWS
    step_rows: int = DEFAULT_STEP_ROWS
    minimum_initial_training_rows: int = MINIMUM_INITIAL_TRAINING_ROWS
    minimum_final_assessment_rows: int = MINIMUM_FINAL_ASSESSMENT_ROWS
    outer_boundary_exclusion_rows: int = BOUNDARY_EXCLUSION_SESSIONS
    psi_bin_count: int = 10
    psi_epsilon: float = 0.000001
    drawdown_bucket_thresholds: tuple[float, ...] = (-0.1, -0.05)
    development_procedure: tuple[str, ...] = (
        "run_feature_ablations",
        "select_development_feature_set",
        "run_uncalibrated_model_candidate_campaign",
        "rank_uncalibrated_development_candidates",
        "run_calibration_substudy_for_highest_ranked_uncalibrated_candidate",
        "report_probability_quality_and_candidate_promotion",
    )
    classification_first: bool = True
    strategy_optimization_authorized: bool = False
    protected_evaluation_authorized: bool = False
    phase2_final_test_available_for_tuning: bool = False
    allowed_symbol: Literal["SPY"] = "SPY"
    primary_timeframe: Literal["1Day"] = "1Day"
    primary_adjustment: Literal["all"] = "all"
    approved_provider_dependency: Literal["local_verified_phase1_manifest"] = (
        "local_verified_phase1_manifest"
    )
    approved_model_dependency: Literal["scikit-learn"] = "scikit-learn"
    notes: str = Field(
        default=(
            "Development-only classification campaign. Protected evaluation, strategy "
            "optimization, broker access, acquisition, and Phase 2 final-test tuning are "
            "not authorized."
        )
    )

    @model_validator(mode="after")
    def _validate_campaign(self) -> Self:
        if self.artifact_schema_version != PHASE3_ARTIFACT_SCHEMA_VERSION:
            msg = f"artifact_schema_version must be {PHASE3_ARTIFACT_SCHEMA_VERSION!r}."
            raise ValueError(msg)
        if self.random_seed != 42:
            msg = "Phase 3 development campaign random_seed must be predeclared as 42."
            raise ValueError(msg)
        if self.minimum_valid_fold_count != 3:
            msg = "minimum_valid_fold_count must be predeclared as 3."
            raise ValueError(msg)
        if self.material_roc_auc_delta != 0.01:
            msg = "material_roc_auc_delta must be predeclared as 0.01."
            raise ValueError(msg)
        if self.materially_different_tolerance != 0.005:
            msg = "materially_different_tolerance must be predeclared as 0.005."
            raise ValueError(msg)
        if self.diagnostic_classification_threshold != 0.5:
            msg = "diagnostic_classification_threshold must remain 0.5."
            raise ValueError(msg)
        if self.reliability_bin_count != 10:
            msg = "reliability_bin_count must be predeclared as 10."
            raise ValueError(msg)
        if self.small_regime_cell_rows != 30:
            msg = "small_regime_cell_rows must be predeclared as 30."
            raise ValueError(msg)
        if self.global_feature_warmup_rows != GLOBAL_DEVELOPMENT_FEATURE_WARMUP_ROWS:
            msg = "global_feature_warmup_rows must be predeclared as 60."
            raise ValueError(msg)
        if self.assessment_window_rows != DEFAULT_ASSESSMENT_WINDOW_ROWS:
            msg = "assessment_window_rows must preserve the approved default."
            raise ValueError(msg)
        if self.step_rows != DEFAULT_STEP_ROWS:
            msg = "step_rows must preserve the approved default."
            raise ValueError(msg)
        if self.minimum_initial_training_rows != MINIMUM_INITIAL_TRAINING_ROWS:
            msg = "minimum_initial_training_rows must preserve the approved default."
            raise ValueError(msg)
        if self.minimum_final_assessment_rows != MINIMUM_FINAL_ASSESSMENT_ROWS:
            msg = "minimum_final_assessment_rows must preserve the approved default."
            raise ValueError(msg)
        if self.outer_boundary_exclusion_rows != BOUNDARY_EXCLUSION_SESSIONS:
            msg = "outer_boundary_exclusion_rows must preserve the six-row purge."
            raise ValueError(msg)
        if self.psi_bin_count <= 0 or self.psi_bin_count > 10:
            msg = "psi_bin_count must be between one and ten."
            raise ValueError(msg)
        if self.psi_epsilon <= 0.0:
            msg = "psi_epsilon must be positive."
            raise ValueError(msg)
        if self.drawdown_bucket_thresholds != tuple(sorted(self.drawdown_bucket_thresholds)):
            msg = "drawdown_bucket_thresholds must be sorted from deeper to shallower drawdown."
            raise ValueError(msg)
        if not self.classification_first or self.strategy_optimization_authorized:
            msg = "this campaign must remain classification-first without strategy optimization."
            raise ValueError(msg)
        if self.protected_evaluation_authorized or self.phase2_final_test_available_for_tuning:
            msg = "protected evaluation and Phase 2 final-test tuning must remain unavailable."
            raise ValueError(msg)
        if any(not stage.strip() for stage in self.development_procedure):
            msg = "development_procedure stages must be nonempty."
            raise ValueError(msg)
        lowered_notes = self.notes.lower()
        if any(term in lowered_notes for term in ("secret", "password", "api_key", "account_id")):
            msg = "campaign notes must not contain secrets or account identifiers."
            raise ValueError(msg)
        return self

    def candidate_selection_config(self) -> CandidateSelectionConfig:
        return CandidateSelectionConfig(
            minimum_valid_fold_count=self.minimum_valid_fold_count,
            material_roc_auc_delta=self.material_roc_auc_delta,
            materially_different_tolerance=self.materially_different_tolerance,
        )

    def fold_policy(self) -> FoldPolicy:
        return FoldPolicy(
            feature_warmup_rows=self.global_feature_warmup_rows,
            minimum_initial_training_rows=self.minimum_initial_training_rows,
            assessment_window_rows=self.assessment_window_rows,
            step_rows=self.step_rows,
            minimum_final_assessment_rows=self.minimum_final_assessment_rows,
        )


def load_research_campaign_config(path: Path) -> ResearchCampaignConfig:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise_research_error(
            ResearchRegistryError,
            "research_campaign_config_load_failed",
            "Phase 3 development campaign config could not be loaded.",
        )
    try:
        return ResearchCampaignConfig.model_validate(payload)
    except ValidationError as exc:
        raise_research_error(
            ResearchRegistryError,
            "invalid_research_campaign_config",
            f"Phase 3 development campaign config failed validation: {exc.errors()[0]['msg']}",
        )


def campaign_config_identity(config: ResearchCampaignConfig) -> str:
    payload = config.model_dump(mode="python")
    payload["campaign_config_identity_version"] = CAMPAIGN_ID_VERSION
    return f"spy-v2p3-cfg-{sha256_json(payload)[:20]}"
