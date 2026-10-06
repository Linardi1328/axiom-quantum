from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum

from spy_market_agent.intelligence.scenarios import ScenarioOutcome, ScenarioProbability
from spy_market_agent.research.scenario_calibration import (
    MI1E_CALIBRATION_POLICY_ID,
    MI1E_TEMPERATURE_GRID,
    calculate_multiclass_ece,
)
from spy_market_agent.research.scenario_candidate import (
    MI1D_CANDIDATE_ID,
    MI1D_FEATURE_POLICY_ID,
)
from spy_market_agent.research.scenario_evaluation import (
    ScenarioEvaluationMetrics,
    calculate_scenario_probability_metrics,
)
from spy_market_agent.research.scenario_selectivity import (
    MI1F_SELECTIVITY_POLICY_ID,
    ScenarioSelectivityPolicy,
    select_scenario_from_probabilities,
)

MI1I_PROTECTED_POLICY_ID = "mi1i-protected-evaluation-v1"


class MI1ProtectedScientificStatus(StrEnum):
    PROTECTED_EVALUATION_COMPLETED_NO_PROMOTION = "protected_evaluation_completed_no_promotion"
    ELIGIBLE_FOR_SEPARATE_PROMOTION_REVIEW = "eligible_for_separate_promotion_review"


@dataclass(frozen=True, slots=True)
class MI1FrozenPolicyBundle:
    candidate_id: str
    feature_policy_id: str
    calibration_policy_id: str
    calibration_temperature: float
    selectivity_policy: ScenarioSelectivityPolicy | None
    development_through_session: date
    protected_start_session: date
    frozen_at: datetime
    model_fingerprint: str

    def __post_init__(self) -> None:
        if (
            self.candidate_id != MI1D_CANDIDATE_ID
            or self.feature_policy_id != MI1D_FEATURE_POLICY_ID
        ):
            raise ValueError("frozen bundle must use the MI-1D candidate and feature policy.")
        if self.calibration_policy_id != MI1E_CALIBRATION_POLICY_ID:
            raise ValueError("frozen bundle must use the MI-1E calibration policy.")
        if self.calibration_temperature not in MI1E_TEMPERATURE_GRID:
            raise ValueError("calibration_temperature must belong to the frozen MI-1E grid.")
        if (
            self.selectivity_policy is not None
            and self.selectivity_policy.policy_id != MI1F_SELECTIVITY_POLICY_ID
        ):
            raise ValueError("selectivity policy must be the frozen MI-1F policy.")
        if self.development_through_session >= self.protected_start_session:
            raise ValueError("development period must end before protected evaluation begins.")
        object.__setattr__(
            self,
            "frozen_at",
            _aware_utc(self.frozen_at, field_name="frozen_at"),
        )
        _require_sha256(self.model_fingerprint, field_name="model_fingerprint")


@dataclass(frozen=True, slots=True)
class MI1ProtectedEvaluationPermit:
    permit_id: str
    authorized_at: datetime
    protected_start_session: date
    protected_end_session: date

    def __post_init__(self) -> None:
        if not self.permit_id.strip():
            raise ValueError("permit_id must be non-empty.")
        if self.protected_start_session > self.protected_end_session:
            raise ValueError("protected evaluation session bounds are invalid.")
        object.__setattr__(
            self,
            "authorized_at",
            _aware_utc(self.authorized_at, field_name="authorized_at"),
        )


@dataclass(frozen=True, slots=True)
class MI1ProtectedPrediction:
    anchor_session: date
    outcome_session: date
    outcome: ScenarioOutcome
    probabilities: tuple[ScenarioProbability, ...]
    model_fingerprint: str

    def __post_init__(self) -> None:
        if self.anchor_session >= self.outcome_session:
            raise ValueError("protected prediction anchor must precede outcome session.")
        by_outcome = {item.outcome: item for item in self.probabilities}
        if set(by_outcome) != set(ScenarioOutcome) or len(self.probabilities) != len(
            ScenarioOutcome
        ):
            raise ValueError("protected prediction must contain all three scenario probabilities.")
        total = sum(item.probability for item in self.probabilities)
        if not math.isclose(total, 1.0, abs_tol=1e-12):
            raise ValueError("protected probabilities must sum to one.")
        object.__setattr__(
            self,
            "probabilities",
            tuple(by_outcome[outcome] for outcome in ScenarioOutcome),
        )
        _require_sha256(self.model_fingerprint, field_name="model_fingerprint")


@dataclass(frozen=True, slots=True)
class MI1ProtectedEvaluationResult:
    policy_id: str
    evaluation_id: str
    permit_id: str
    model_fingerprint: str
    protected_start_session: date
    protected_end_session: date
    row_count: int
    metrics: ScenarioEvaluationMetrics
    ece: float
    selected_rows: int
    selected_correct_rows: int
    selected_coverage: float
    selected_precision: float | None
    scientific_status: MI1ProtectedScientificStatus

    def __post_init__(self) -> None:
        if self.policy_id != MI1I_PROTECTED_POLICY_ID:
            raise ValueError("policy_id must match MI-1I protected policy.")
        _require_sha256(self.model_fingerprint, field_name="model_fingerprint")
        if self.row_count != self.metrics.row_count or self.row_count <= 0:
            raise ValueError("row_count must match protected metrics.")
        if not 0 <= self.selected_correct_rows <= self.selected_rows <= self.row_count:
            raise ValueError("selected protected row counts are inconsistent.")
        if not math.isclose(
            self.selected_coverage,
            self.selected_rows / self.row_count,
            abs_tol=1e-12,
        ):
            raise ValueError("selected_coverage must match selected rows.")
        if self.selected_rows == 0:
            if self.selected_precision is not None:
                raise ValueError("selected_precision must be None when no rows are selected.")
        elif self.selected_precision is None or not math.isclose(
            self.selected_precision,
            self.selected_correct_rows / self.selected_rows,
            abs_tol=1e-12,
        ):
            raise ValueError("selected_precision must match selected outcomes.")
        if not 0.0 <= float(self.ece) <= 1.0:
            raise ValueError("ece must lie in [0, 1].")


def evaluate_mi1_protected_predictions(
    predictions: tuple[MI1ProtectedPrediction, ...],
    *,
    frozen_policy: MI1FrozenPolicyBundle,
    permit: MI1ProtectedEvaluationPermit,
) -> MI1ProtectedEvaluationResult:
    if not predictions:
        raise ValueError("protected evaluation requires at least one prediction.")
    if frozen_policy.protected_start_session != permit.protected_start_session:
        raise ValueError("permit start must match the frozen protected start.")
    anchors = tuple(item.anchor_session for item in predictions)
    if anchors != tuple(sorted(set(anchors))):
        raise ValueError("protected predictions must have unique ordered anchors.")
    if anchors[0] < permit.protected_start_session or anchors[-1] > permit.protected_end_session:
        raise ValueError("protected prediction anchors must lie within the permit interval.")
    if any(item.outcome_session > permit.protected_end_session for item in predictions):
        raise ValueError("protected outcomes must lie within the permit interval.")
    if any(item.model_fingerprint != frozen_policy.model_fingerprint for item in predictions):
        raise ValueError("protected predictions must use the frozen model fingerprint.")

    outcomes = tuple(item.outcome for item in predictions)
    rows = tuple(item.probabilities for item in predictions)
    metrics = calculate_scenario_probability_metrics(outcomes, rows)
    selected_rows = 0
    selected_correct = 0
    for prediction in predictions:
        selected = select_scenario_from_probabilities(
            prediction.probabilities,
            frozen_policy.selectivity_policy,
        )
        if selected is None:
            continue
        selected_rows += 1
        if selected == prediction.outcome:
            selected_correct += 1
    selected_precision = selected_correct / selected_rows if selected_rows else None
    status = MI1ProtectedScientificStatus.PROTECTED_EVALUATION_COMPLETED_NO_PROMOTION
    if (
        selected_rows >= 63
        and selected_precision is not None
        and selected_precision >= 0.80
        and metrics.multiclass_log_loss < math.log(3.0)
    ):
        status = MI1ProtectedScientificStatus.ELIGIBLE_FOR_SEPARATE_PROMOTION_REVIEW
    evaluation_id = _evaluation_id(frozen_policy, permit, predictions)
    return MI1ProtectedEvaluationResult(
        policy_id=MI1I_PROTECTED_POLICY_ID,
        evaluation_id=evaluation_id,
        permit_id=permit.permit_id,
        model_fingerprint=frozen_policy.model_fingerprint,
        protected_start_session=permit.protected_start_session,
        protected_end_session=permit.protected_end_session,
        row_count=len(predictions),
        metrics=metrics,
        ece=calculate_multiclass_ece(outcomes, rows),
        selected_rows=selected_rows,
        selected_correct_rows=selected_correct,
        selected_coverage=selected_rows / len(predictions),
        selected_precision=selected_precision,
        scientific_status=status,
    )


def _evaluation_id(
    policy: MI1FrozenPolicyBundle,
    permit: MI1ProtectedEvaluationPermit,
    predictions: tuple[MI1ProtectedPrediction, ...],
) -> str:
    payload = {
        "policy": MI1I_PROTECTED_POLICY_ID,
        "permit": permit.permit_id,
        "model": policy.model_fingerprint,
        "anchors": [item.anchor_session.isoformat() for item in predictions],
        "outcomes": [item.outcome.value for item in predictions],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"mi1-protected-{hashlib.sha256(encoded).hexdigest()[:32]}"


def _aware_utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware.")
    return value.astimezone(UTC)


def _require_sha256(value: str, *, field_name: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest.")
