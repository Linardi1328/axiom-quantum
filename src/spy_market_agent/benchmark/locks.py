from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

BENCHMARK_SCHEMA_VERSION = "spy-v2-phase2-benchmark-artifacts-v1"
BENCHMARK_ID_VERSION = "spy-v2-phase2-benchmark-id-v1"
SPLIT_POLICY_ID = "phase2-positional-70-15-remainder-with-6-session-purge-v1"
SELECTION_RULE_ID = "validation-roc-auc-log-loss-brier-logistic-tie-v1"
SIGNAL_POLICY_ID = "spy-long-cash-probability-threshold-0.5-v1"
RISK_POLICY_ID = "spy-long-only-risk-v1"
ROUNDING_POLICY_ID = "decimal-no-intermediate-cents-quantization-v1"


class BenchmarkRole(StrEnum):
    PRIMARY = "primary"
    DIAGNOSTIC = "diagnostic"


class ArtifactModel(BaseModel):
    model_config = ConfigDict(frozen=True)

    artifact_schema_version: str = BENCHMARK_SCHEMA_VERSION


class BenchmarkArtifact(ArtifactModel):
    benchmark_id: str
    dataset_id: str


class FeedAvailabilityRecord(ArtifactModel):
    provider: str
    requested_feed: str
    symbol: str
    timeframe: str
    adjustment_mode: str
    requested_start: date
    requested_end: date
    probe_timestamp: datetime
    success: bool
    entitlement_or_subscription_limitation: str | None = None
    sanitized_failure_category: str | None = None
    owner_acknowledgement: bool
    evidence_source_description: str
    contains_credentials: bool = False
    contains_raw_provider_payload: bool = False
    contains_account_identifier: bool = False

    @field_validator(
        "contains_credentials",
        "contains_raw_provider_payload",
        "contains_account_identifier",
    )
    @classmethod
    def _must_be_false(cls, value: bool) -> bool:
        if value:
            msg = (
                "benchmark records must not contain credentials, raw provider "
                "payloads, or account identifiers."
            )
            raise ValueError(msg)
        return value


class FeedLimitationDecision(ArtifactModel):
    feed: str
    limitation_label: str
    primary_allowed: bool
    diagnostic_allowed: bool
    reason: str


class CostScenario(ArtifactModel):
    name: Literal["idealized", "base", "adverse", "severe"]
    commission_bps_per_side: Decimal
    slippage_bps_per_side: Decimal
    role: str

    @property
    def side_cost_bps(self) -> Decimal:
        return self.commission_bps_per_side + self.slippage_bps_per_side

    @property
    def round_trip_cost_bps(self) -> Decimal:
        return self.side_cost_bps * Decimal("2")


class ClassificationBaselineDefinition(ArtifactModel):
    name: str
    definition: str
    probability_source: str
    threshold: Decimal | None = None


class StrategyBaselineDefinition(ArtifactModel):
    name: str
    definition: str
    execution_policy: str
    included_in_default_policy: bool = True


class RegimePolicy(ArtifactModel):
    trend_200: dict[str, str]
    realized_volatility_20: dict[str, str | Decimal]
    drawdown_10: dict[str, str | Decimal]
    calendar_year: dict[str, str]
    volatility_threshold: Decimal
    strategy_attribution_rule: str
    small_sample_warning_threshold: int = 40


class BenchmarkPolicy(ArtifactModel):
    split_policy_id: str
    selection_rule_id: str
    signal_policy_id: str
    risk_policy_id: str
    rounding_policy_id: str
    latest_complete_research_year: int
    owner_approved_assumptions: bool
    initial_cash: Decimal
    annualized_risk_free_rate: Decimal
    no_cash_yield: bool
    whole_shares_only: bool
    primary_cost_scenario: str
    cost_scenarios: tuple[CostScenario, ...]
    classification_baselines: tuple[ClassificationBaselineDefinition, ...]
    strategy_baselines: tuple[StrategyBaselineDefinition, ...]
    regime_policy: RegimePolicy


class DatasetEligibilityReport(BenchmarkArtifact):
    requested_start: date
    requested_end: date
    actual_start: date
    actual_end: date
    row_count: int
    expected_session_count: int
    complete_calendar_years: tuple[int, ...]
    included_required_periods: tuple[str, ...]
    missing_required_periods: tuple[str, ...]
    provider: str
    feed: str
    provider_feed_limitations: tuple[str, ...]
    adjustment_mode: str
    benchmark_role: BenchmarkRole
    passed: bool
    reasons: tuple[str, ...]
    latest_complete_research_year: int


class PartitionSummary(ArtifactModel):
    name: Literal["train", "validation", "final_test"]
    included_row_count: int
    positive_count: int
    negative_count: int
    first_prediction_session: date
    last_prediction_session: date
    first_entry_session: date
    last_entry_session: date
    first_exit_session: date
    last_exit_session: date


class SplitManifest(BenchmarkArtifact):
    split_policy_id: str
    feature_warmup_rows: int
    entry_offset_sessions: int
    exit_offset_sessions: int
    mandatory_gap_sessions: int
    boundary_exclusion_sessions: int
    supervised_row_count: int
    assignable_row_count: int
    feature_warmup_excluded_sessions: tuple[date, ...]
    label_horizon_excluded_sessions: tuple[date, ...]
    train_included_sessions: tuple[date, ...]
    train_validation_boundary_excluded_sessions: tuple[date, ...]
    validation_included_sessions: tuple[date, ...]
    validation_test_boundary_excluded_sessions: tuple[date, ...]
    final_test_included_sessions: tuple[date, ...]
    train: PartitionSummary
    validation: PartitionSummary
    final_test: PartitionSummary
    chronological_split_spec: dict[str, date]


class MetricValue(BaseModel):
    model_config = ConfigDict(frozen=True)

    value: float | None
    undefined_reason: str | None = None


class ClassificationMetricSet(BenchmarkArtifact):
    model_name: str
    partition_name: str
    row_count: int
    positive_count: int
    negative_count: int
    predicted_positive_count: int
    confusion_matrix: dict[str, int]
    metrics: dict[str, MetricValue]


class StrategyMetricSet(BenchmarkArtifact):
    strategy_name: str
    partition_name: str
    cost_scenario: str
    metrics: dict[str, Decimal | int | float | str | None]
    warnings: tuple[str, ...] = ()
    proposed_orders: tuple[dict[str, Any], ...] = ()
    risk_decisions: tuple[dict[str, Any], ...] = ()
    fills: tuple[dict[str, Any], ...] = ()
    portfolio_states: tuple[dict[str, Any], ...] = ()


class RegimeCellDiagnostics(ArtifactModel):
    sample_size: int
    positive_count: int
    negative_count: int
    small_sample: bool
    warnings: tuple[str, ...] = ()
    selected_model_classification: ClassificationMetricSet | None = None
    classification_baselines: dict[str, ClassificationMetricSet] = Field(default_factory=dict)
    selected_model_strategy: dict[str, StrategyMetricSet] = Field(default_factory=dict)
    strategy_comparators: dict[str, StrategyMetricSet] = Field(default_factory=dict)
    undefined_reasons: dict[str, str] = Field(default_factory=dict)


class RegimeDiagnostics(BenchmarkArtifact):
    partition_name: str
    volatility_threshold: Decimal
    strategy_attribution_rule: str
    small_sample_warning_threshold: int
    regimes: dict[str, dict[str, RegimeCellDiagnostics]]


class ValidationResult(BenchmarkArtifact):
    selected_model_name: str
    selection_reason: str
    model_metrics: dict[str, ClassificationMetricSet]
    classification_baselines: dict[str, ClassificationMetricSet]
    strategy_results: dict[str, StrategyMetricSet]
    regime_results: RegimeDiagnostics | None = None


class SelectedModelManifest(BenchmarkArtifact):
    selected_model_name: str
    selection_reason: str
    fixed_parameters: dict[str, Any]
    locked_selection: dict[str, Any]
    validation_results_checksum: str
    no_model_binary_persisted: bool = True


class FinalTestReadiness(BenchmarkArtifact):
    ready: bool
    reasons: tuple[str, ...]
    final_test_row_count: int
    aggregate_positive_count: int
    aggregate_negative_count: int
    row_level_final_labels_exposed: bool = False


class FinalTestLock(BenchmarkArtifact):
    benchmark_lock_checksum: str
    validation_results_checksum: str
    classification_baselines_checksum: str
    strategy_baselines_checksum: str
    selected_model_manifest_checksum: str
    final_test_readiness_checksum: str
    selected_model_name: str
    owner_acknowledgement: bool
    final_test_policy: str


class FinalTestAccessRecord(BenchmarkArtifact):
    final_test_lock_checksum: str
    access_timestamp: datetime
    code_commit_sha: str | None
    python_version: str
    package_version: str
    dependency_versions: dict[str, str]
    owner_acknowledgement: bool
    access_state: Literal["started"]
    contains_results: bool = False


class FinalTestCompletionRecord(BenchmarkArtifact):
    final_test_lock_checksum: str
    access_record_checksum: str
    final_test_results_checksum: str
    cost_sensitivity_checksum: str
    regime_results_checksum: str
    backtest_results_checksum: str
    completion_timestamp: datetime
    code_commit_sha: str | None
    python_version: str
    package_version: str
    dependency_versions: dict[str, str]
    completed_state: Literal["completed"]


class FinalTestResults(BenchmarkArtifact):
    selected_model_name: str
    classification_metrics: ClassificationMetricSet
    classification_baselines: dict[str, ClassificationMetricSet]
    strategy_results: dict[str, StrategyMetricSet]
    cost_sensitivity: dict[str, StrategyMetricSet]
    regime_results: RegimeDiagnostics
    no_tuning_performed: bool
    no_model_binary_persisted: bool


class BenchmarkIdentityInput(ArtifactModel):
    dataset_id: str
    canonical_checksum: str
    provider: str
    feed: str
    adjustment_mode: str
    benchmark_role: BenchmarkRole
    feature_schema_id: str
    label_id: str
    forecast_horizon: str
    split_policy: dict[str, Any]
    model_candidate_configurations: tuple[dict[str, Any], ...]
    random_seeds: tuple[int, ...]
    selection_rule: str
    signal_policy: str
    risk_configuration: dict[str, Any]
    classification_baseline_definitions: tuple[dict[str, Any], ...]
    strategy_comparator_definitions: tuple[dict[str, Any], ...]
    cost_matrix: tuple[dict[str, Any], ...]
    initial_cash: Decimal
    annualized_risk_free_rate: Decimal
    rounding_policy: str
    regime_definitions: dict[str, Any]
    frozen_volatility_threshold: Decimal
    code_commit_sha: str | None
    python_version: str
    package_version: str
    dependency_versions: dict[str, str]
    artifact_schema_version: str = BENCHMARK_SCHEMA_VERSION


class BenchmarkLock(BenchmarkArtifact):
    benchmark_role: BenchmarkRole
    manifest_reference: str
    canonical_checksum: str
    provider: str
    feed: str
    feed_availability: FeedAvailabilityRecord
    feed_limitation_decision: FeedLimitationDecision
    adjustment_mode: str
    dataset_range: dict[str, date]
    feature_schema_id: str
    label_id: str
    forecast_horizon: str
    split_manifest_checksum: str
    dataset_eligibility_checksum: str
    benchmark_policy: BenchmarkPolicy
    identity_input: BenchmarkIdentityInput
    code_commit_sha: str | None
    python_version: str
    package_version: str
    dependency_versions: dict[str, str]
    owner_acknowledgement: bool
    final_test_lock_status: Literal["not_created", "created"] = "not_created"


class BenchmarkArtifactIndex(BenchmarkArtifact):
    artifacts: dict[str, dict[str, str | bool]]
    creation_stage: str


class VerificationResult(BenchmarkArtifact):
    passed: bool
    checked_artifacts: tuple[str, ...]
    reasons: tuple[str, ...]


def exact_phase2_cost_scenarios() -> tuple[CostScenario, ...]:
    return (
        CostScenario(
            name="idealized",
            commission_bps_per_side=Decimal("0"),
            slippage_bps_per_side=Decimal("0"),
            role="diagnostic",
        ),
        CostScenario(
            name="base",
            commission_bps_per_side=Decimal("0.125"),
            slippage_bps_per_side=Decimal("0.25"),
            role="primary",
        ),
        CostScenario(
            name="adverse",
            commission_bps_per_side=Decimal("1"),
            slippage_bps_per_side=Decimal("2"),
            role="diagnostic",
        ),
        CostScenario(
            name="severe",
            commission_bps_per_side=Decimal("10"),
            slippage_bps_per_side=Decimal("20"),
            role="diagnostic",
        ),
    )


def classification_baseline_definitions() -> tuple[ClassificationBaselineDefinition, ...]:
    return (
        ClassificationBaselineDefinition(
            name="majority_class",
            definition="Predict the training-only majority class; ties select class 0.",
            probability_source="training majority class only",
        ),
        ClassificationBaselineDefinition(
            name="always_positive",
            definition="Always predict class 1 with probability_positive = 1.0.",
            probability_source="fixed pre-lock constant",
        ),
        ClassificationBaselineDefinition(
            name="always_negative",
            definition="Always predict class 0 with probability_positive = 0.0.",
            probability_source="fixed pre-lock constant",
        ),
        ClassificationBaselineDefinition(
            name="training_prevalence",
            definition="Use training positive prevalence with fixed diagnostic threshold 0.5.",
            probability_source="training target prevalence only",
            threshold=Decimal("0.5"),
        ),
    )


def strategy_baseline_definitions() -> tuple[StrategyBaselineDefinition, ...]:
    return (
        StrategyBaselineDefinition(
            name="always_cash",
            definition="Hold cash with zero market exposure and zero cash yield.",
            execution_policy="no orders",
        ),
        StrategyBaselineDefinition(
            name="buy_and_hold",
            definition=(
                "Enter whole-share SPY at first eligible execution open and "
                "sell at final eligible exit."
            ),
            execution_policy="first entry open through final exit open",
        ),
        StrategyBaselineDefinition(
            name="fixed_20_session_momentum",
            definition="Long when adjusted close t / adjusted close t-20 - 1 is greater than 0.",
            execution_policy="target executes no earlier than next validated session open",
        ),
    )


def default_regime_policy(volatility_threshold: Decimal) -> RegimePolicy:
    attribution_rule = (
        "Regime strategy diagnostics are attributed by signal_session. Proposed orders, "
        "risk decisions, fills, and portfolio states caused by a signal are included in the "
        "regime cell for that signal session; non-contiguous regime subsets do not receive "
        "standalone annualized return, drawdown, or Sharpe interpretations."
    )
    return RegimePolicy(
        trend_200={
            "input": "canonical adjusted close through session t",
            "definition": "bull when adjusted_close_t >= trailing_200_mean_t, bear otherwise",
            "unavailable": "fewer than 200 closes",
        },
        realized_volatility_20={
            "input": "daily log returns through session t",
            "definition": (
                "sample standard deviation of most recent 20 log returns annualized with sqrt(252)"
            ),
            "threshold_source": "training partition median only",
            "threshold": volatility_threshold,
        },
        drawdown_10={
            "input": "canonical adjusted close through session t",
            "definition": "drawdown_t = close_t / running_peak_t - 1",
            "threshold": Decimal("-0.10"),
        },
        calendar_year={"definition": "XNYS session calendar year reported independently"},
        volatility_threshold=volatility_threshold,
        strategy_attribution_rule=attribution_rule,
    )
