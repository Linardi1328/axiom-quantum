from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from spy_market_agent.features.models import FeatureSet
from spy_market_agent.intelligence.scenarios import ScenarioOutcome, ScenarioProbability
from spy_market_agent.research.context_ablation import (
    MI2C_POLICY_ID,
    ContextAblationVariant,
    ContextAblationVariantEvaluation,
)
from spy_market_agent.research.scenario_analogues import (
    MI1G_MINIMUM_REGIME_EVALUATION_ROWS,
    SPYRegime,
    classify_spy_regime,
)
from spy_market_agent.research.scenario_calibration import (
    MI1E_TEMPERATURE_GRID,
    apply_temperature_scaling,
    calculate_multiclass_ece,
)
from spy_market_agent.research.scenario_evaluation import (
    ScenarioEvaluationMetrics,
    calculate_scenario_probability_metrics,
)
from spy_market_agent.research.scenario_selectivity import (
    MI1F_MINIMUM_SELECTED_ROWS,
    MI1F_SELECTIVITY_POLICY_ID,
    MI1F_SEPARATION_GRID,
    MI1F_TARGET_PRECISION,
    MI1F_TOP_PROBABILITY_GRID,
    ScenarioSelectivityPolicy,
    select_scenario_from_probabilities,
)

MI2D_POLICY_ID = "mi2d-forward-context-calibration-selectivity-v1"
MI2D_MINIMUM_HISTORY_ROWS = 63


class ForwardSelectivityStatus(StrEnum):
    QUALIFYING_POLICY = "qualifying_policy"
    NO_QUALIFYING_POLICY = "no_qualifying_policy"


@dataclass(frozen=True, slots=True)
class ForwardSelectivityEvidence:
    status: ForwardSelectivityStatus
    policy: ScenarioSelectivityPolicy | None
    history_row_count: int
    selected_rows: int
    correct_selected_rows: int
    coverage: float
    precision: float | None

    def __post_init__(self) -> None:
        if not isinstance(self.status, ForwardSelectivityStatus):
            raise ValueError("status must be a ForwardSelectivityStatus.")
        if self.history_row_count < MI2D_MINIMUM_HISTORY_ROWS:
            raise ValueError("selectivity history must meet the MI-2D minimum.")
        if not 0 <= self.correct_selected_rows <= self.selected_rows <= self.history_row_count:
            raise ValueError("selectivity history counts are inconsistent.")
        expected_coverage = self.selected_rows / self.history_row_count
        if not math.isclose(self.coverage, expected_coverage, rel_tol=0.0, abs_tol=1e-12):
            raise ValueError("selectivity coverage must match selected history rows.")
        if self.status == ForwardSelectivityStatus.NO_QUALIFYING_POLICY:
            if self.policy is not None:
                raise ValueError("no-qualifying status must not expose a policy.")
            if any(value is not None for value in (self.precision,)):
                raise ValueError("no-qualifying status must not expose selected precision.")
            if self.selected_rows != 0 or self.correct_selected_rows != 0 or self.coverage != 0.0:
                raise ValueError("no-qualifying status must encode an all-abstain policy.")
            return
        if self.policy is None or self.precision is None:
            raise ValueError("qualifying status requires policy and precision.")
        if self.policy.policy_id != MI1F_SELECTIVITY_POLICY_ID:
            raise ValueError("selectivity policy must use the frozen MI-1F policy ID.")
        expected_precision = self.correct_selected_rows / self.selected_rows
        if not math.isclose(self.precision, expected_precision, rel_tol=0.0, abs_tol=1e-12):
            raise ValueError("selectivity precision must match selected history rows.")
        if self.selected_rows < MI1F_MINIMUM_SELECTED_ROWS:
            raise ValueError("qualifying policy must meet the frozen selected-row minimum.")
        if self.precision < MI1F_TARGET_PRECISION:
            raise ValueError("qualifying policy must meet the frozen precision objective.")


@dataclass(frozen=True, slots=True)
class ForwardContextFoldEvaluation:
    source_fold_index: int
    history_row_count: int
    history_last_outcome_session: date
    temperature: float
    history_raw_metrics: ScenarioEvaluationMetrics
    history_calibrated_metrics: ScenarioEvaluationMetrics
    history_raw_ece: float
    history_calibrated_ece: float
    selectivity: ForwardSelectivityEvidence
    assessment_anchor_sessions: tuple[date, ...]
    assessment_outcome_sessions: tuple[date, ...]
    assessment_outcomes: tuple[ScenarioOutcome, ...]
    calibrated_probability_rows: tuple[tuple[ScenarioProbability, ...], ...]
    assessment_metrics: ScenarioEvaluationMetrics
    assessment_ece: float
    assessment_selected_rows: int
    assessment_correct_selected_rows: int
    assessment_selected_coverage: float
    assessment_selected_precision: float | None

    def __post_init__(self) -> None:
        if self.source_fold_index < 0:
            raise ValueError("source_fold_index must be non-negative.")
        if self.history_row_count < MI2D_MINIMUM_HISTORY_ROWS:
            raise ValueError("forward fold must meet the MI-2D history minimum.")
        if self.temperature not in MI1E_TEMPERATURE_GRID:
            raise ValueError("temperature must belong to the frozen MI-1E grid.")
        if self.history_raw_metrics.row_count != self.history_row_count:
            raise ValueError("raw history metrics must cover every history row.")
        if self.history_calibrated_metrics.row_count != self.history_row_count:
            raise ValueError("calibrated history metrics must cover every history row.")
        row_count = len(self.assessment_anchor_sessions)
        if row_count == 0:
            raise ValueError("forward assessment fold must not be empty.")
        lengths = (
            len(self.assessment_outcome_sessions),
            len(self.assessment_outcomes),
            len(self.calibrated_probability_rows),
            self.assessment_metrics.row_count,
        )
        if any(length != row_count for length in lengths):
            raise ValueError("forward assessment fields must have matching row counts.")
        if self.assessment_anchor_sessions != tuple(sorted(self.assessment_anchor_sessions)):
            raise ValueError("assessment anchors must be increasing.")
        if len(set(self.assessment_anchor_sessions)) != row_count:
            raise ValueError("assessment anchors must be unique.")
        if self.history_last_outcome_session > self.assessment_anchor_sessions[0]:
            raise ValueError("history outcomes must be observable by assessment start.")
        if self.selectivity.history_row_count != self.history_row_count:
            raise ValueError("selectivity history must match calibration history.")
        for row in self.calibrated_probability_rows:
            _validate_probability_row(row)
        for field_name in ("history_raw_ece", "history_calibrated_ece", "assessment_ece"):
            value = float(getattr(self, field_name))
            if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError(f"{field_name} must lie in [0, 1].")
            object.__setattr__(self, field_name, value)
        if not (
            0 <= self.assessment_correct_selected_rows <= self.assessment_selected_rows <= row_count
        ):
            raise ValueError("assessment selected counts are inconsistent.")
        expected_coverage = self.assessment_selected_rows / row_count
        if not math.isclose(
            self.assessment_selected_coverage,
            expected_coverage,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise ValueError("assessment selected coverage must match selected rows.")
        if self.assessment_selected_rows == 0:
            if self.assessment_selected_precision is not None:
                raise ValueError("selected precision must be None when all rows abstain.")
        else:
            if self.assessment_selected_precision is None:
                raise ValueError(
                    "selected precision is required when assessment rows are selected."
                )
            expected_precision = (
                self.assessment_correct_selected_rows / self.assessment_selected_rows
            )
            if not math.isclose(
                self.assessment_selected_precision,
                expected_precision,
                rel_tol=0.0,
                abs_tol=1e-12,
            ):
                raise ValueError("assessment selected precision must match selected rows.")
        if self.selectivity.policy is None and self.assessment_selected_rows != 0:
            raise ValueError("no selectivity policy must produce all-abstain assessment behavior.")


@dataclass(frozen=True, slots=True)
class ContextRegimeRobustness:
    regime: SPYRegime
    row_count: int
    metrics: ScenarioEvaluationMetrics
    ece: float
    selected_rows: int
    selected_coverage: float
    selected_precision: float | None

    def __post_init__(self) -> None:
        if self.row_count != self.metrics.row_count or self.row_count <= 0:
            raise ValueError("regime row_count must match metrics.")
        if not 0 <= self.selected_rows <= self.row_count:
            raise ValueError("regime selected_rows is outside row_count.")
        expected_coverage = self.selected_rows / self.row_count
        if not math.isclose(self.selected_coverage, expected_coverage, abs_tol=1e-12):
            raise ValueError("regime selected coverage must match selected rows.")
        value = float(self.ece)
        if not math.isfinite(value) or not 0.0 <= value <= 1.0:
            raise ValueError("regime ECE must lie in [0, 1].")
        object.__setattr__(self, "ece", value)
        if self.selected_rows == 0:
            if self.selected_precision is not None:
                raise ValueError("empty regime selection must not expose precision.")
        elif self.selected_precision is None or not 0.0 <= self.selected_precision <= 1.0:
            raise ValueError("regime selected precision must lie in [0, 1].")


@dataclass(frozen=True, slots=True)
class ForwardContextEvaluation:
    policy_id: str
    source_policy_id: str
    variant: ContextAblationVariant
    horizon_length: int
    development_through_session: date
    source_market_data_checksum: str
    source_schema_version: str
    scenario_schema_id: str
    folds: tuple[ForwardContextFoldEvaluation, ...]
    pooled_calibrated_metrics: ScenarioEvaluationMetrics
    pooled_calibrated_ece: float
    pooled_selected_rows: int
    pooled_selected_coverage: float
    pooled_selected_precision: float | None
    regimes: tuple[ContextRegimeRobustness, ...]
    omitted_small_regimes: tuple[SPYRegime, ...]

    def __post_init__(self) -> None:
        if self.policy_id != MI2D_POLICY_ID:
            raise ValueError("policy_id must match the frozen MI-2D policy.")
        if self.source_policy_id != MI2C_POLICY_ID:
            raise ValueError("source_policy_id must match the frozen MI-2C policy.")
        if self.variant == ContextAblationVariant.SPY_ONLY:
            raise ValueError("MI-2D accepts only an explicitly nominated contextual variant.")
        if self.horizon_length not in {5, 20}:
            raise ValueError("horizon_length must be 5 or 20 sessions.")
        _require_sha256(
            self.source_market_data_checksum,
            field_name="source_market_data_checksum",
        )
        if not self.source_schema_version.strip() or not self.scenario_schema_id.strip():
            raise ValueError("source and scenario schema IDs must not be empty.")
        if not self.folds:
            raise ValueError("MI-2D evaluation must contain at least one forward fold.")
        indexes = tuple(fold.source_fold_index for fold in self.folds)
        if indexes != tuple(sorted(set(indexes))):
            raise ValueError("forward fold indexes must be unique and increasing.")
        pooled_rows = sum(len(fold.assessment_outcomes) for fold in self.folds)
        if self.pooled_calibrated_metrics.row_count != pooled_rows:
            raise ValueError("pooled metrics must cover all forward assessment rows.")
        if not math.isfinite(self.pooled_calibrated_ece) or not (
            0.0 <= self.pooled_calibrated_ece <= 1.0
        ):
            raise ValueError("pooled calibrated ECE must lie in [0, 1].")
        if not 0 <= self.pooled_selected_rows <= pooled_rows:
            raise ValueError("pooled selected rows are outside pooled assessment rows.")
        expected_coverage = self.pooled_selected_rows / pooled_rows
        if not math.isclose(self.pooled_selected_coverage, expected_coverage, abs_tol=1e-12):
            raise ValueError("pooled selected coverage must match selected rows.")
        if self.pooled_selected_rows == 0:
            if self.pooled_selected_precision is not None:
                raise ValueError("pooled selected precision must be None when all rows abstain.")
        elif self.pooled_selected_precision is None or not (
            0.0 <= self.pooled_selected_precision <= 1.0
        ):
            raise ValueError("pooled selected precision must lie in [0, 1].")
        represented = {item.regime for item in self.regimes}
        if len(represented) != len(self.regimes):
            raise ValueError("regime evaluations must be unique.")
        if represented.intersection(self.omitted_small_regimes):
            raise ValueError("a regime cannot be both evaluated and omitted.")
        if represented.union(self.omitted_small_regimes) != set(SPYRegime):
            raise ValueError("every SPY regime must be evaluated or explicitly omitted.")


def evaluate_forward_contextual_calibration_robustness(
    evaluation: ContextAblationVariantEvaluation,
    *,
    feature_set: FeatureSet,
) -> ForwardContextEvaluation:
    """Evaluate one preselected MI-2C contextual variant using prior OOF evidence only."""

    _validate_source_alignment(evaluation, feature_set)
    forward_folds: list[ForwardContextFoldEvaluation] = []
    for position, current_fold in enumerate(evaluation.folds):
        history = _observable_prior_rows(
            evaluation,
            before_position=position,
            cutoff=current_fold.assessment_anchor_sessions[0],
        )
        if len(history) < MI2D_MINIMUM_HISTORY_ROWS:
            continue
        history_outcomes = tuple(item[1] for item in history)
        history_rows = tuple(item[2] for item in history)
        temperature = _select_temperature(history_outcomes, history_rows)
        history_calibrated = apply_temperature_scaling(
            history_rows,
            temperature=temperature,
        )
        selectivity = _select_forward_policy(history_outcomes, history_calibrated)
        calibrated_current = apply_temperature_scaling(
            current_fold.probability_rows,
            temperature=temperature,
        )
        selected_rows, correct_rows = _selection_counts(
            current_fold.assessment_outcomes,
            calibrated_current,
            selectivity.policy,
        )
        forward_folds.append(
            ForwardContextFoldEvaluation(
                source_fold_index=current_fold.baseline_fold_index,
                history_row_count=len(history),
                history_last_outcome_session=max(item[0] for item in history),
                temperature=temperature,
                history_raw_metrics=calculate_scenario_probability_metrics(
                    history_outcomes,
                    history_rows,
                ),
                history_calibrated_metrics=calculate_scenario_probability_metrics(
                    history_outcomes,
                    history_calibrated,
                ),
                history_raw_ece=calculate_multiclass_ece(history_outcomes, history_rows),
                history_calibrated_ece=calculate_multiclass_ece(
                    history_outcomes,
                    history_calibrated,
                ),
                selectivity=selectivity,
                assessment_anchor_sessions=current_fold.assessment_anchor_sessions,
                assessment_outcome_sessions=current_fold.assessment_outcome_sessions,
                assessment_outcomes=current_fold.assessment_outcomes,
                calibrated_probability_rows=calibrated_current,
                assessment_metrics=calculate_scenario_probability_metrics(
                    current_fold.assessment_outcomes,
                    calibrated_current,
                ),
                assessment_ece=calculate_multiclass_ece(
                    current_fold.assessment_outcomes,
                    calibrated_current,
                ),
                assessment_selected_rows=selected_rows,
                assessment_correct_selected_rows=correct_rows,
                assessment_selected_coverage=(
                    selected_rows / len(current_fold.assessment_outcomes)
                ),
                assessment_selected_precision=(
                    correct_rows / selected_rows if selected_rows else None
                ),
            )
        )
    if not forward_folds:
        raise ValueError("MI-2D has no fold with 63 prior observable out-of-fold rows.")

    pooled_outcomes = tuple(
        outcome for fold in forward_folds for outcome in fold.assessment_outcomes
    )
    pooled_rows = tuple(row for fold in forward_folds for row in fold.calibrated_probability_rows)
    pooled_selected = sum(fold.assessment_selected_rows for fold in forward_folds)
    pooled_correct = sum(fold.assessment_correct_selected_rows for fold in forward_folds)
    regimes, omitted = _evaluate_regimes(feature_set, tuple(forward_folds))
    return ForwardContextEvaluation(
        policy_id=MI2D_POLICY_ID,
        source_policy_id=MI2C_POLICY_ID,
        variant=evaluation.variant,
        horizon_length=evaluation.horizon_length,
        development_through_session=evaluation.development_through_session,
        source_market_data_checksum=evaluation.source_market_data_checksum,
        source_schema_version=evaluation.source_schema_version,
        scenario_schema_id=evaluation.scenario_schema_id,
        folds=tuple(forward_folds),
        pooled_calibrated_metrics=calculate_scenario_probability_metrics(
            pooled_outcomes,
            pooled_rows,
        ),
        pooled_calibrated_ece=calculate_multiclass_ece(pooled_outcomes, pooled_rows),
        pooled_selected_rows=pooled_selected,
        pooled_selected_coverage=pooled_selected / len(pooled_outcomes),
        pooled_selected_precision=(pooled_correct / pooled_selected if pooled_selected else None),
        regimes=regimes,
        omitted_small_regimes=omitted,
    )


def _observable_prior_rows(
    evaluation: ContextAblationVariantEvaluation,
    *,
    before_position: int,
    cutoff: date,
) -> tuple[
    tuple[date, ScenarioOutcome, tuple[ScenarioProbability, ...]],
    ...,
]:
    rows: list[tuple[date, ScenarioOutcome, tuple[ScenarioProbability, ...]]] = []
    for fold in evaluation.folds[:before_position]:
        for outcome_session, outcome, probabilities in zip(
            fold.assessment_outcome_sessions,
            fold.assessment_outcomes,
            fold.probability_rows,
            strict=True,
        ):
            if outcome_session <= cutoff:
                _validate_probability_row(probabilities)
                rows.append((outcome_session, outcome, probabilities))
    return tuple(rows)


def _select_temperature(
    outcomes: tuple[ScenarioOutcome, ...],
    rows: tuple[tuple[ScenarioProbability, ...], ...],
) -> float:
    return min(
        MI1E_TEMPERATURE_GRID,
        key=lambda temperature: (
            calculate_scenario_probability_metrics(
                outcomes,
                apply_temperature_scaling(rows, temperature=temperature),
            ).multiclass_log_loss,
            temperature,
        ),
    )


def _select_forward_policy(
    outcomes: tuple[ScenarioOutcome, ...],
    rows: tuple[tuple[ScenarioProbability, ...], ...],
) -> ForwardSelectivityEvidence:
    qualifying: list[
        tuple[
            float,
            float,
            float,
            float,
            ScenarioSelectivityPolicy,
            int,
            int,
        ]
    ] = []
    for top_probability in MI1F_TOP_PROBABILITY_GRID:
        for separation in MI1F_SEPARATION_GRID:
            policy = ScenarioSelectivityPolicy(
                policy_id=MI1F_SELECTIVITY_POLICY_ID,
                min_top_probability=top_probability,
                min_separation=separation,
            )
            selected_rows, correct_rows = _selection_counts(outcomes, rows, policy)
            if selected_rows < MI1F_MINIMUM_SELECTED_ROWS:
                continue
            precision = correct_rows / selected_rows
            if precision < MI1F_TARGET_PRECISION:
                continue
            coverage = selected_rows / len(outcomes)
            qualifying.append(
                (
                    coverage,
                    precision,
                    top_probability,
                    separation,
                    policy,
                    selected_rows,
                    correct_rows,
                )
            )
    if not qualifying:
        return ForwardSelectivityEvidence(
            status=ForwardSelectivityStatus.NO_QUALIFYING_POLICY,
            policy=None,
            history_row_count=len(outcomes),
            selected_rows=0,
            correct_selected_rows=0,
            coverage=0.0,
            precision=None,
        )
    selected = max(qualifying, key=lambda item: item[:4])
    coverage, precision, _, _, policy, selected_rows, correct_rows = selected
    return ForwardSelectivityEvidence(
        status=ForwardSelectivityStatus.QUALIFYING_POLICY,
        policy=policy,
        history_row_count=len(outcomes),
        selected_rows=selected_rows,
        correct_selected_rows=correct_rows,
        coverage=coverage,
        precision=precision,
    )


def _selection_counts(
    outcomes: tuple[ScenarioOutcome, ...],
    rows: tuple[tuple[ScenarioProbability, ...], ...],
    policy: ScenarioSelectivityPolicy | None,
) -> tuple[int, int]:
    selected_rows = 0
    correct_rows = 0
    for outcome, row in zip(outcomes, rows, strict=True):
        selected = select_scenario_from_probabilities(row, policy)
        if selected is None:
            continue
        selected_rows += 1
        if selected == outcome:
            correct_rows += 1
    return selected_rows, correct_rows


def _evaluate_regimes(
    feature_set: FeatureSet,
    folds: tuple[ForwardContextFoldEvaluation, ...],
) -> tuple[tuple[ContextRegimeRobustness, ...], tuple[SPYRegime, ...]]:
    grouped_outcomes: dict[SPYRegime, list[ScenarioOutcome]] = {regime: [] for regime in SPYRegime}
    grouped_rows: dict[SPYRegime, list[tuple[ScenarioProbability, ...]]] = {
        regime: [] for regime in SPYRegime
    }
    grouped_selected: dict[SPYRegime, list[bool]] = {regime: [] for regime in SPYRegime}
    grouped_correct: dict[SPYRegime, list[bool]] = {regime: [] for regime in SPYRegime}

    for fold in folds:
        policy = fold.selectivity.policy
        for anchor, outcome, row in zip(
            fold.assessment_anchor_sessions,
            fold.assessment_outcomes,
            fold.calibrated_probability_rows,
            strict=True,
        ):
            regime = classify_spy_regime(feature_set, anchor_session=anchor)
            selected = select_scenario_from_probabilities(row, policy)
            grouped_outcomes[regime].append(outcome)
            grouped_rows[regime].append(row)
            grouped_selected[regime].append(selected is not None)
            grouped_correct[regime].append(selected == outcome if selected is not None else False)

    evaluations: list[ContextRegimeRobustness] = []
    omitted: list[SPYRegime] = []
    for regime in SPYRegime:
        outcomes = tuple(grouped_outcomes[regime])
        rows = tuple(grouped_rows[regime])
        if len(outcomes) < MI1G_MINIMUM_REGIME_EVALUATION_ROWS:
            omitted.append(regime)
            continue
        selected_rows = sum(grouped_selected[regime])
        correct_rows = sum(grouped_correct[regime])
        evaluations.append(
            ContextRegimeRobustness(
                regime=regime,
                row_count=len(outcomes),
                metrics=calculate_scenario_probability_metrics(outcomes, rows),
                ece=calculate_multiclass_ece(outcomes, rows),
                selected_rows=selected_rows,
                selected_coverage=selected_rows / len(outcomes),
                selected_precision=(correct_rows / selected_rows if selected_rows else None),
            )
        )
    return tuple(evaluations), tuple(omitted)


def _validate_source_alignment(
    evaluation: ContextAblationVariantEvaluation,
    feature_set: FeatureSet,
) -> None:
    if evaluation.variant == ContextAblationVariant.SPY_ONLY:
        raise ValueError("MI-2D requires a contextual MI-2C variant.")
    if evaluation.source_market_data_checksum != feature_set.source_market_data_checksum:
        raise ValueError("contextual evaluation and SPY feature checksums must match.")
    if evaluation.source_schema_version != feature_set.source_schema_version:
        raise ValueError("contextual evaluation and SPY source schemas must match.")


def _validate_probability_row(row: tuple[ScenarioProbability, ...]) -> None:
    if tuple(item.outcome for item in row) != tuple(ScenarioOutcome):
        raise ValueError("probability rows must use canonical scenario order.")
    if not math.isclose(
        sum(item.probability for item in row),
        1.0,
        rel_tol=0.0,
        abs_tol=1e-12,
    ):
        raise ValueError("probability rows must sum to one.")


def _require_sha256(value: str, *, field_name: str) -> None:
    is_valid = len(value) == 64 and all(character in "0123456789abcdef" for character in value)
    if not is_valid:
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest.")
