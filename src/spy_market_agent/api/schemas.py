from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

EDUCATIONAL_WARNING = (
    "Educational and experimental research output only. Not investment advice and not proof of "
    "profitability."
)


class ApiErrorResponse(BaseModel):
    code: str
    message: str


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str = "spy-market-agent-read-api"
    educational_warning: str = EDUCATIONAL_WARNING


class DataStatusResponse(BaseModel):
    available: bool
    symbol: str | None = None
    provider_name: str | None = None
    source_description: str | None = None
    timeframe: str | None = None
    adjustment_policy: str | None = None
    first_session: str | None = None
    last_session: str | None = None
    row_count: int | None = None
    dataset_checksum: str | None = None
    schema_version: str | None = None
    downloaded_at: str | None = None
    created_at: str | None = None
    educational_warning: str = EDUCATIONAL_WARNING


class MetricSnapshotResponse(BaseModel):
    model_name: str
    row_count: int
    positive_count: int
    negative_count: int
    log_loss: float
    brier_score: float
    roc_auc: float


class ClassificationMetricsResponse(BaseModel):
    model_name: str
    partition_name: str
    diagnostic_classification_threshold: float
    row_count: int
    positive_count: int
    negative_count: int
    positive_rate: float
    log_loss: float
    brier_score: float
    roc_auc: float
    average_precision: float
    accuracy_at_0_5: float
    precision_at_0_5: float
    recall_at_0_5: float
    f1_at_0_5: float
    true_negative_count: int
    false_positive_count: int
    false_negative_count: int
    true_positive_count: int
    created_at: str


class ModelRunSummaryResponse(BaseModel):
    run_id: str
    selected_model_name: str
    selection_reason: str
    created_at: str
    source_market_data_checksum: str
    test_row_count: int
    educational_warning: str = EDUCATIONAL_WARNING


class ModelRunListResponse(BaseModel):
    items: list[ModelRunSummaryResponse]
    count: int
    educational_warning: str = EDUCATIONAL_WARNING


class ModelRunDetailResponse(BaseModel):
    run_id: str
    selected_model_name: str
    selection_reason: str
    validation_metric_snapshots: list[MetricSnapshotResponse]
    candidate_parameters: dict[str, dict[str, str | int | float | bool | None]]
    final_test_metrics: ClassificationMetricsResponse
    split_spec: dict[str, str]
    feature_columns: list[str]
    source_market_data_checksum: str
    source_schema_version: str
    feature_schema_version: str
    label_schema_version: str
    model_schema_version: str
    sklearn_version: str
    created_at: str
    limitation: str = "Classification metrics do not establish profitability."
    educational_warning: str = EDUCATIONAL_WARNING


class PredictionRowResponse(BaseModel):
    sequence_number: int
    session: str
    probability_positive: float
    predicted_class: int
    target: int


class PredictionPageResponse(BaseModel):
    run_id: str
    total: int
    limit: int
    offset: int
    items: list[PredictionRowResponse]
    educational_warning: str = EDUCATIONAL_WARNING


class CostAssumptionsResponse(BaseModel):
    commission_bps_per_side: str
    slippage_bps_per_side: str


class RiskConfigResponse(BaseModel):
    supported_symbol: str
    allow_short_selling: bool
    allow_leverage: bool
    allow_fractional_shares: bool
    maximum_position_weight: float


class BacktestMetricsResponse(BaseModel):
    session_count: int
    initial_cash: str
    final_cash: str
    final_shares: int
    final_market_value: str
    final_equity: str
    total_return: float
    maximum_drawdown: float
    total_reference_notional: str
    total_execution_notional: str
    total_commission: str
    total_slippage_cost: str
    total_transaction_cost: str
    turnover_ratio: float
    exposure_fraction: float
    proposed_order_count: int
    approved_order_count: int
    rejected_order_count: int
    fill_count: int
    buy_fill_count: int
    sell_fill_count: int


class BacktestRunSummaryResponse(BaseModel):
    run_id: str
    selected_model_name: str
    created_at: str
    source_market_data_checksum: str
    final_equity: str
    total_return: float
    maximum_drawdown: float
    proposed_order_count: int
    fill_count: int
    educational_warning: str = EDUCATIONAL_WARNING


class BacktestRunListResponse(BaseModel):
    items: list[BacktestRunSummaryResponse]
    count: int
    educational_warning: str = EDUCATIONAL_WARNING


class BacktestDetailResponse(BaseModel):
    run_id: str
    selected_model_name: str
    metrics: BacktestMetricsResponse
    cost_assumptions: CostAssumptionsResponse
    risk_config: RiskConfigResponse
    source_market_data_checksum: str
    execution_price_checksum: str
    first_signal_session: str
    last_signal_session: str
    first_execution_session: str
    last_execution_session: str
    source_schema_version: str
    feature_schema_version: str
    label_schema_version: str
    model_schema_version: str
    strategy_schema_version: str
    risk_schema_version: str
    backtest_schema_version: str
    created_at: str
    limitation: str = "Historical backtests are approximations and do not guarantee future results."
    educational_warning: str = EDUCATIONAL_WARNING


class EquityRowResponse(BaseModel):
    sequence_number: int
    session: str
    signal_session: str
    target_position: int
    cash: str
    shares: int
    close_price: str
    market_value: str
    equity: str
    daily_return: float
    drawdown: float


class OrderRowResponse(BaseModel):
    sequence_number: int
    symbol: str
    side: str
    quantity: int
    signal_session: str
    execution_session: str
    target_position: int
    reference_open: str
    estimated_execution_price: str
    estimated_commission: str
    estimated_cash_change: str
    current_cash: str
    current_shares: int


class RiskDecisionRowResponse(BaseModel):
    order_sequence_number: int
    approved: bool
    reason_codes: list[str]
    evaluated_session: str
    projected_cash: str
    projected_shares: int
    projected_market_value: str
    projected_equity: str


class FillRowResponse(BaseModel):
    order_sequence_number: int
    symbol: str
    side: str
    quantity: int
    signal_session: str
    execution_session: str
    reference_open: str
    execution_price: str
    reference_notional: str
    execution_notional: str
    commission: str
    slippage_cost: str
    total_transaction_cost: str
    cash_change: str
    shares_before: int
    shares_after: int
    cash_before: str
    cash_after: str
    risk_approved: bool


class PaginatedResponse(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    run_id: str
    total: int
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)
    educational_warning: str = EDUCATIONAL_WARNING


class EquityPageResponse(PaginatedResponse):
    items: list[EquityRowResponse]


class OrderPageResponse(PaginatedResponse):
    items: list[OrderRowResponse]


class RiskDecisionPageResponse(PaginatedResponse):
    items: list[RiskDecisionRowResponse]


class FillPageResponse(PaginatedResponse):
    items: list[FillRowResponse]


class PaperTradingStatusResponse(BaseModel):
    configuration_kill_switch_engaged: bool
    durable_kill_switch_engaged: bool
    effective_kill_switch_engaged: bool
    kill_switch_engaged: bool
    execution_mode: str
    paper_execution_enabled: bool
    dry_run: bool
    alpaca_api_key_present: bool
    alpaca_secret_key_present: bool
    last_local_attempt_status: str | None
    last_successful_submission_at_utc: str | None
    unresolved_submission_count: int
    scope: str = "paper trading only; local persisted execution state only"
    limitation: str = (
        "Paper fills are experimental, may differ from historical backtests and live fills, "
        "and are not investment advice."
    )
    educational_warning: str = EDUCATIONAL_WARNING


class PaperOrderAttemptResponse(BaseModel):
    signal_id: str
    client_order_id: str
    approval_id: str
    instruction_fingerprint: str
    execution_schema_version: str
    symbol: str
    side: str
    quantity: int
    signal_session: str
    execution_session: str
    instruction_created_at_utc: str
    expires_at_utc: str
    approval_at_utc: str
    approval_source: str
    original_risk_approved: bool
    execution_risk_approved: bool
    attempt_status: str
    broker_order_id: str | None
    broker_status: str | None
    broker_environment: str | None
    account_id_fingerprint: str | None
    sanitized_request_id: str | None
    created_at_utc: str
    updated_at_utc: str
    failure_code: str | None
    limitation: str = "Local paper-order attempts are audit records, not recommendations."
    educational_warning: str = EDUCATIONAL_WARNING


class PaperOrderListResponse(BaseModel):
    items: list[PaperOrderAttemptResponse]
    total: int
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)
    educational_warning: str = EDUCATIONAL_WARNING


__all__ = [
    "EDUCATIONAL_WARNING",
    "ApiErrorResponse",
    "BacktestDetailResponse",
    "BacktestMetricsResponse",
    "BacktestRunListResponse",
    "BacktestRunSummaryResponse",
    "ClassificationMetricsResponse",
    "CostAssumptionsResponse",
    "DataStatusResponse",
    "EquityPageResponse",
    "EquityRowResponse",
    "FillPageResponse",
    "FillRowResponse",
    "HealthResponse",
    "MetricSnapshotResponse",
    "ModelRunDetailResponse",
    "ModelRunListResponse",
    "ModelRunSummaryResponse",
    "OrderPageResponse",
    "OrderRowResponse",
    "PaperOrderAttemptResponse",
    "PaperOrderListResponse",
    "PaperTradingStatusResponse",
    "PredictionPageResponse",
    "PredictionRowResponse",
    "RiskConfigResponse",
    "RiskDecisionPageResponse",
    "RiskDecisionRowResponse",
]
