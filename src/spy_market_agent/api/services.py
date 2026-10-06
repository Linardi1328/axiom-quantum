from __future__ import annotations

import math
from decimal import Decimal
from typing import Any, Protocol, TypeVar, cast

import pandas as pd

from spy_market_agent.api.schemas import (
    BacktestDetailResponse,
    BacktestMetricsResponse,
    BacktestRunListResponse,
    BacktestRunSummaryResponse,
    ClassificationMetricsResponse,
    CostAssumptionsResponse,
    DataStatusResponse,
    EquityPageResponse,
    EquityRowResponse,
    FillPageResponse,
    FillRowResponse,
    MetricSnapshotResponse,
    ModelRunDetailResponse,
    ModelRunListResponse,
    ModelRunSummaryResponse,
    OrderPageResponse,
    OrderRowResponse,
    PaperOrderAttemptResponse,
    PaperOrderListResponse,
    PaperTradingStatusResponse,
    PredictionPageResponse,
    PredictionRowResponse,
    RiskConfigResponse,
    RiskDecisionPageResponse,
    RiskDecisionRowResponse,
)
from spy_market_agent.backtesting.models import BacktestResult
from spy_market_agent.config import Settings
from spy_market_agent.execution.models import PaperExecutionAttempt, PaperExecutionStatus
from spy_market_agent.execution.repository import SQLitePaperExecutionRepository
from spy_market_agent.market_data.models import MarketDataBatch
from spy_market_agent.modeling.models import ClassificationMetrics, FinalTestEvaluation
from spy_market_agent.persistence.models import BacktestRunSummary, ModelRunSummary
from spy_market_agent.persistence.repositories import SQLiteArtifactRepository
from spy_market_agent.persistence.serialization import date_to_text, datetime_to_text
from spy_market_agent.risk.models import RiskConfig

MAX_PAGE_LIMIT = 500


class ReadRepository(Protocol):
    def load_latest_market_data_batch(self) -> MarketDataBatch | None: ...

    def list_model_runs(self) -> tuple[ModelRunSummary, ...]: ...

    def load_final_test_evaluation(self, run_id: str) -> FinalTestEvaluation: ...

    def list_backtests(self) -> tuple[BacktestRunSummary, ...]: ...

    def load_backtest_result(self, run_id: str) -> BacktestResult: ...


class ExecutionReadRepository(Protocol):
    def status(self, settings: Settings) -> PaperExecutionStatus: ...

    def list_attempts(self, *, limit: int, offset: int) -> tuple[PaperExecutionAttempt, ...]: ...

    def count_attempts(self) -> int: ...

    def get_attempt(self, client_order_id: str) -> PaperExecutionAttempt: ...


T = TypeVar("T")


class ReadService:
    """Read-only service layer for persisted research artifacts."""

    def __init__(
        self,
        repository: ReadRepository,
        *,
        execution_repository: ExecutionReadRepository | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._repository = repository
        self._execution_repository = execution_repository
        self._settings = settings or Settings()

    @classmethod
    def from_database_path(cls, database_path: str) -> ReadService:
        return cls(
            SQLiteArtifactRepository(database_path),
            execution_repository=SQLitePaperExecutionRepository(database_path),
        )

    def data_status(self) -> DataStatusResponse:
        batch = self._repository.load_latest_market_data_batch()
        if batch is None:
            return DataStatusResponse(available=False)
        metadata = batch.metadata
        return DataStatusResponse(
            available=True,
            symbol=metadata.symbol,
            provider_name=metadata.provider_name,
            source_description=metadata.source_description,
            timeframe=metadata.timeframe,
            adjustment_policy=metadata.adjustment_policy,
            first_session=date_to_text(metadata.first_session),
            last_session=date_to_text(metadata.last_session),
            row_count=metadata.row_count,
            dataset_checksum=metadata.dataset_checksum,
            schema_version=metadata.schema_version,
            downloaded_at=datetime_to_text(metadata.downloaded_at),
            created_at=datetime_to_text(metadata.created_at),
        )

    def model_runs(self) -> ModelRunListResponse:
        items = [_model_summary(summary) for summary in self._repository.list_model_runs()]
        return ModelRunListResponse(items=items, count=len(items))

    def model_run_detail(self, run_id: str) -> ModelRunDetailResponse:
        evaluation = self._repository.load_final_test_evaluation(run_id)
        locked = evaluation.locked_selection
        return ModelRunDetailResponse(
            run_id=run_id,
            selected_model_name=evaluation.selected_model_name,
            selection_reason=locked.selection_reason,
            validation_metric_snapshots=[
                MetricSnapshotResponse(
                    model_name=snapshot.model_name,
                    row_count=snapshot.row_count,
                    positive_count=snapshot.positive_count,
                    negative_count=snapshot.negative_count,
                    log_loss=_finite(snapshot.log_loss, "log_loss"),
                    brier_score=_finite(snapshot.brier_score, "brier_score"),
                    roc_auc=_finite(snapshot.roc_auc, "roc_auc"),
                )
                for snapshot in locked.validation_metric_snapshots
            ],
            candidate_parameters={
                parameter_set.model_name: dict(parameter_set.parameters)
                for parameter_set in locked.candidate_parameters
            },
            final_test_metrics=_classification_metrics(evaluation.metrics),
            split_spec=_split_spec_dict(evaluation.split_spec),
            feature_columns=list(evaluation.feature_columns),
            source_market_data_checksum=evaluation.source_market_data_checksum,
            source_schema_version=evaluation.source_schema_version,
            feature_schema_version=evaluation.feature_schema_version,
            label_schema_version=evaluation.label_schema_version,
            model_schema_version=evaluation.model_schema_version,
            sklearn_version=evaluation.sklearn_version,
            created_at=datetime_to_text(evaluation.created_at),
        )

    def model_predictions(
        self,
        run_id: str,
        *,
        limit: int,
        offset: int,
    ) -> PredictionPageResponse:
        limit, offset = validate_pagination(limit=limit, offset=offset)
        evaluation = self._repository.load_final_test_evaluation(run_id)
        frame = evaluation.prediction_set.data
        rows = [
            PredictionRowResponse(
                sequence_number=offset + index,
                session=date_to_text(row.session),
                probability_positive=_finite(row.probability_positive, "probability_positive"),
                predicted_class=int(row.predicted_class),
                target=int(row.target),
            )
            for index, row in enumerate(
                _page(frame, limit=limit, offset=offset).itertuples(index=False)
            )
        ]
        return PredictionPageResponse(
            run_id=run_id,
            total=len(frame),
            limit=limit,
            offset=offset,
            items=rows,
        )

    def backtests(self) -> BacktestRunListResponse:
        items = [_backtest_summary(summary) for summary in self._repository.list_backtests()]
        return BacktestRunListResponse(items=items, count=len(items))

    def backtest_detail(self, run_id: str) -> BacktestDetailResponse:
        result = self._repository.load_backtest_result(run_id)
        return BacktestDetailResponse(
            run_id=run_id,
            selected_model_name=result.selected_model_name,
            metrics=_backtest_metrics(result),
            cost_assumptions=CostAssumptionsResponse(
                commission_bps_per_side=str(result.cost_assumptions.commission_bps_per_side),
                slippage_bps_per_side=str(result.cost_assumptions.slippage_bps_per_side),
            ),
            risk_config=_risk_config(result.risk_config),
            source_market_data_checksum=result.source_market_data_checksum,
            execution_price_checksum=result.execution_prices.execution_price_checksum,
            first_signal_session=date_to_text(result.first_signal_session),
            last_signal_session=date_to_text(result.last_signal_session),
            first_execution_session=date_to_text(result.first_execution_session),
            last_execution_session=date_to_text(result.last_execution_session),
            source_schema_version=result.source_schema_version,
            feature_schema_version=result.feature_schema_version,
            label_schema_version=result.label_schema_version,
            model_schema_version=result.model_schema_version,
            strategy_schema_version=result.strategy_schema_version,
            risk_schema_version=result.risk_schema_version,
            backtest_schema_version=result.backtest_schema_version,
            created_at=datetime_to_text(result.created_at),
        )

    def equity(self, run_id: str, *, limit: int, offset: int) -> EquityPageResponse:
        limit, offset = validate_pagination(limit=limit, offset=offset)
        result = self._repository.load_backtest_result(run_id)
        frame = result.portfolio
        return EquityPageResponse(
            run_id=run_id,
            total=len(frame),
            limit=limit,
            offset=offset,
            items=[
                EquityRowResponse(
                    sequence_number=offset + index,
                    session=date_to_text(row.session),
                    signal_session=date_to_text(row.signal_session),
                    target_position=int(row.target_position),
                    cash=_money(row.cash),
                    shares=int(row.shares),
                    close_price=_money(row.close_price),
                    market_value=_money(row.market_value),
                    equity=_money(row.equity),
                    daily_return=_finite(row.daily_return, "daily_return"),
                    drawdown=_finite(row.drawdown, "drawdown"),
                )
                for index, row in enumerate(
                    _page(frame, limit=limit, offset=offset).itertuples(index=False)
                )
            ],
        )

    def orders(self, run_id: str, *, limit: int, offset: int) -> OrderPageResponse:
        limit, offset = validate_pagination(limit=limit, offset=offset)
        result = self._repository.load_backtest_result(run_id)
        frame = result.proposed_orders
        return OrderPageResponse(
            run_id=run_id,
            total=len(frame),
            limit=limit,
            offset=offset,
            items=[
                OrderRowResponse(
                    sequence_number=int(row.sequence_number),
                    symbol=str(row.symbol),
                    side=str(row.side),
                    quantity=int(row.quantity),
                    signal_session=date_to_text(row.signal_session),
                    execution_session=date_to_text(row.execution_session),
                    target_position=int(row.target_position),
                    reference_open=_money(row.reference_open),
                    estimated_execution_price=_money(row.estimated_execution_price),
                    estimated_commission=_money(row.estimated_commission),
                    estimated_cash_change=_money(row.estimated_cash_change),
                    current_cash=_money(row.current_cash),
                    current_shares=int(row.current_shares),
                )
                for row in _page(frame, limit=limit, offset=offset).itertuples(index=False)
            ],
        )

    def risk_decisions(
        self,
        run_id: str,
        *,
        limit: int,
        offset: int,
    ) -> RiskDecisionPageResponse:
        limit, offset = validate_pagination(limit=limit, offset=offset)
        result = self._repository.load_backtest_result(run_id)
        frame = result.risk_decisions
        return RiskDecisionPageResponse(
            run_id=run_id,
            total=len(frame),
            limit=limit,
            offset=offset,
            items=[
                RiskDecisionRowResponse(
                    order_sequence_number=int(row.order_sequence_number),
                    approved=bool(row.approved),
                    reason_codes=list(cast(tuple[str, ...], row.reason_codes)),
                    evaluated_session=date_to_text(row.evaluated_session),
                    projected_cash=_money(row.projected_cash),
                    projected_shares=int(row.projected_shares),
                    projected_market_value=_money(row.projected_market_value),
                    projected_equity=_money(row.projected_equity),
                )
                for row in _page(frame, limit=limit, offset=offset).itertuples(index=False)
            ],
        )

    def fills(self, run_id: str, *, limit: int, offset: int) -> FillPageResponse:
        limit, offset = validate_pagination(limit=limit, offset=offset)
        result = self._repository.load_backtest_result(run_id)
        frame = result.fills
        return FillPageResponse(
            run_id=run_id,
            total=len(frame),
            limit=limit,
            offset=offset,
            items=[
                FillRowResponse(
                    order_sequence_number=int(row.order_sequence_number),
                    symbol=str(row.symbol),
                    side=str(row.side),
                    quantity=int(row.quantity),
                    signal_session=date_to_text(row.signal_session),
                    execution_session=date_to_text(row.execution_session),
                    reference_open=_money(row.reference_open),
                    execution_price=_money(row.execution_price),
                    reference_notional=_money(row.reference_notional),
                    execution_notional=_money(row.execution_notional),
                    commission=_money(row.commission),
                    slippage_cost=_money(row.slippage_cost),
                    total_transaction_cost=_money(row.total_transaction_cost),
                    cash_change=_money(row.cash_change),
                    shares_before=int(row.shares_before),
                    shares_after=int(row.shares_after),
                    cash_before=_money(row.cash_before),
                    cash_after=_money(row.cash_after),
                    risk_approved=bool(row.risk_approved),
                )
                for row in _page(frame, limit=limit, offset=offset).itertuples(index=False)
            ],
        )

    def paper_trading_status(self) -> PaperTradingStatusResponse:
        repository = self._require_execution_repository()
        return _paper_status_response(repository.status(self._settings))

    def paper_orders(self, *, limit: int, offset: int) -> PaperOrderListResponse:
        limit, offset = validate_pagination(limit=limit, offset=offset)
        repository = self._require_execution_repository()
        return PaperOrderListResponse(
            items=[
                _paper_attempt_response(attempt)
                for attempt in repository.list_attempts(limit=limit, offset=offset)
            ],
            total=repository.count_attempts(),
            limit=limit,
            offset=offset,
        )

    def paper_order_detail(self, client_order_id: str) -> PaperOrderAttemptResponse:
        return _paper_attempt_response(
            self._require_execution_repository().get_attempt(client_order_id)
        )

    def _require_execution_repository(self) -> ExecutionReadRepository:
        if self._execution_repository is None:
            raise RuntimeError("paper-execution repository is not configured.")
        return self._execution_repository


def validate_pagination(*, limit: int, offset: int) -> tuple[int, int]:
    if isinstance(limit, bool) or limit < 1 or limit > MAX_PAGE_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_PAGE_LIMIT}.")
    if isinstance(offset, bool) or offset < 0:
        raise ValueError("offset must be greater than or equal to 0.")
    return limit, offset


def _model_summary(summary: ModelRunSummary) -> ModelRunSummaryResponse:
    return ModelRunSummaryResponse(
        run_id=summary.run_id,
        selected_model_name=summary.selected_model_name,
        selection_reason=summary.selection_reason,
        created_at=summary.created_at,
        source_market_data_checksum=summary.source_market_data_checksum,
        test_row_count=summary.test_row_count,
    )


def _backtest_summary(summary: BacktestRunSummary) -> BacktestRunSummaryResponse:
    return BacktestRunSummaryResponse(
        run_id=summary.run_id,
        selected_model_name=summary.selected_model_name,
        created_at=summary.created_at,
        source_market_data_checksum=summary.source_market_data_checksum,
        final_equity=_money(summary.final_equity),
        total_return=_finite(summary.total_return, "total_return"),
        maximum_drawdown=_finite(summary.maximum_drawdown, "maximum_drawdown"),
        proposed_order_count=summary.proposed_order_count,
        fill_count=summary.fill_count,
    )


def _classification_metrics(metrics: ClassificationMetrics) -> ClassificationMetricsResponse:
    return ClassificationMetricsResponse(
        model_name=metrics.model_name,
        partition_name=metrics.partition_name,
        diagnostic_classification_threshold=metrics.diagnostic_classification_threshold,
        row_count=metrics.row_count,
        positive_count=metrics.positive_count,
        negative_count=metrics.negative_count,
        positive_rate=_finite(metrics.positive_rate, "positive_rate"),
        log_loss=_finite(metrics.log_loss, "log_loss"),
        brier_score=_finite(metrics.brier_score, "brier_score"),
        roc_auc=_finite(metrics.roc_auc, "roc_auc"),
        average_precision=_finite(metrics.average_precision, "average_precision"),
        accuracy_at_0_5=_finite(metrics.accuracy_at_0_5, "accuracy_at_0_5"),
        precision_at_0_5=_finite(metrics.precision_at_0_5, "precision_at_0_5"),
        recall_at_0_5=_finite(metrics.recall_at_0_5, "recall_at_0_5"),
        f1_at_0_5=_finite(metrics.f1_at_0_5, "f1_at_0_5"),
        true_negative_count=metrics.true_negative_count,
        false_positive_count=metrics.false_positive_count,
        false_negative_count=metrics.false_negative_count,
        true_positive_count=metrics.true_positive_count,
        created_at=datetime_to_text(metrics.created_at),
    )


def _backtest_metrics(result: BacktestResult) -> BacktestMetricsResponse:
    metrics = result.metrics
    return BacktestMetricsResponse(
        session_count=metrics.session_count,
        initial_cash=_money(metrics.initial_cash),
        final_cash=_money(metrics.final_cash),
        final_shares=metrics.final_shares,
        final_market_value=_money(metrics.final_market_value),
        final_equity=_money(metrics.final_equity),
        total_return=_finite(metrics.total_return, "total_return"),
        maximum_drawdown=_finite(metrics.maximum_drawdown, "maximum_drawdown"),
        total_reference_notional=_money(metrics.total_reference_notional),
        total_execution_notional=_money(metrics.total_execution_notional),
        total_commission=_money(metrics.total_commission),
        total_slippage_cost=_money(metrics.total_slippage_cost),
        total_transaction_cost=_money(metrics.total_transaction_cost),
        turnover_ratio=_finite(metrics.turnover_ratio, "turnover_ratio"),
        exposure_fraction=_finite(metrics.exposure_fraction, "exposure_fraction"),
        proposed_order_count=metrics.proposed_order_count,
        approved_order_count=metrics.approved_order_count,
        rejected_order_count=metrics.rejected_order_count,
        fill_count=metrics.fill_count,
        buy_fill_count=metrics.buy_fill_count,
        sell_fill_count=metrics.sell_fill_count,
    )


def _risk_config(config: RiskConfig) -> RiskConfigResponse:
    return RiskConfigResponse(
        supported_symbol=config.supported_symbol,
        allow_short_selling=config.allow_short_selling,
        allow_leverage=config.allow_leverage,
        allow_fractional_shares=config.allow_fractional_shares,
        maximum_position_weight=_finite(
            config.maximum_position_weight,
            "maximum_position_weight",
        ),
    )


def _paper_status_response(status: PaperExecutionStatus) -> PaperTradingStatusResponse:
    return PaperTradingStatusResponse(
        configuration_kill_switch_engaged=status.configuration_kill_switch_engaged,
        durable_kill_switch_engaged=status.durable_kill_switch_engaged,
        effective_kill_switch_engaged=status.effective_kill_switch_engaged,
        kill_switch_engaged=status.kill_switch_engaged,
        execution_mode=status.execution_mode,
        paper_execution_enabled=status.paper_execution_enabled,
        dry_run=status.dry_run,
        alpaca_api_key_present=status.alpaca_api_key_present,
        alpaca_secret_key_present=status.alpaca_secret_key_present,
        last_local_attempt_status=status.last_local_attempt_status,
        last_successful_submission_at_utc=None
        if status.last_successful_submission_at_utc is None
        else datetime_to_text(status.last_successful_submission_at_utc),
        unresolved_submission_count=status.unresolved_submission_count,
    )


def _paper_attempt_response(attempt: PaperExecutionAttempt) -> PaperOrderAttemptResponse:
    return PaperOrderAttemptResponse(
        signal_id=attempt.signal_id,
        client_order_id=attempt.client_order_id,
        approval_id=attempt.approval_id,
        instruction_fingerprint=attempt.instruction_fingerprint,
        execution_schema_version=attempt.execution_schema_version,
        symbol=attempt.symbol,
        side=attempt.side,
        quantity=attempt.quantity,
        signal_session=date_to_text(attempt.signal_session),
        execution_session=date_to_text(attempt.execution_session),
        instruction_created_at_utc=datetime_to_text(attempt.instruction_created_at_utc),
        expires_at_utc=datetime_to_text(attempt.expires_at_utc),
        approval_at_utc=datetime_to_text(attempt.approval_at_utc),
        approval_source=attempt.approval_source,
        original_risk_approved=attempt.original_risk_approved,
        execution_risk_approved=attempt.execution_risk_approved,
        attempt_status=attempt.attempt_status,
        broker_order_id=attempt.broker_order_id,
        broker_status=attempt.broker_status,
        broker_environment=attempt.broker_environment,
        account_id_fingerprint=attempt.account_id_fingerprint,
        sanitized_request_id=attempt.sanitized_request_id,
        created_at_utc=datetime_to_text(attempt.created_at_utc),
        updated_at_utc=datetime_to_text(attempt.updated_at_utc),
        failure_code=attempt.failure_code,
    )


def _split_spec_dict(spec: object) -> dict[str, str]:
    split = cast(Any, spec)
    return {
        "train_start_session": date_to_text(split.train_start_session),
        "train_end_session": date_to_text(split.train_end_session),
        "validation_start_session": date_to_text(split.validation_start_session),
        "validation_end_session": date_to_text(split.validation_end_session),
        "test_start_session": date_to_text(split.test_start_session),
        "test_end_session": date_to_text(split.test_end_session),
    }


def _page(frame: pd.DataFrame, *, limit: int, offset: int) -> pd.DataFrame:
    return frame.iloc[offset : offset + limit].copy(deep=True)


def _money(value: object) -> str:
    if isinstance(value, Decimal):
        return format(value, "f")
    parsed = _finite(value, "money")
    return format(parsed, ".17g")


def _finite(value: object, field_name: str) -> float:
    parsed = float(cast(Any, value))
    if not math.isfinite(parsed):
        raise ValueError(f"{field_name} must be finite.")
    return parsed


__all__ = [
    "MAX_PAGE_LIMIT",
    "ExecutionReadRepository",
    "ReadRepository",
    "ReadService",
    "validate_pagination",
]
