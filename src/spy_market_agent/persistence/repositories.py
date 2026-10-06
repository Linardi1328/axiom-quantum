from __future__ import annotations

import platform
import sqlite3
import subprocess
from collections.abc import Iterable
from importlib import metadata as importlib_metadata
from typing import Any, cast

import pandas as pd
from pydantic import ValidationError

from spy_market_agent.backtesting.models import (
    BACKTEST_SCHEMA_VERSION,
    EXECUTION_PRICE_COLUMNS,
    FILL_COLUMNS,
    PORTFOLIO_COLUMNS,
    PROPOSED_ORDER_COLUMNS,
    RISK_DECISION_COLUMNS,
    BacktestConfig,
    BacktestCostAssumptions,
    BacktestError,
    BacktestMetrics,
    BacktestResult,
    ExecutionPriceSet,
)
from spy_market_agent.datasets.splits import ChronologicalSplitSpec
from spy_market_agent.market_data.models import (
    CANONICAL_COLUMNS,
    MarketDataBatch,
    MarketDataMetadata,
)
from spy_market_agent.modeling.models import (
    MODEL_NAMES,
    CandidateMetricSnapshot,
    ClassificationMetrics,
    FinalTestEvaluation,
    LockedModelSelection,
    ModelingError,
    ModelParameterSet,
    ModelParameterValue,
    PredictionSet,
)
from spy_market_agent.persistence.database import connect_database
from spy_market_agent.persistence.models import (
    BacktestRunSummary,
    DatabasePath,
    ModelRunSummary,
    PersistenceConflictError,
    PersistenceError,
    PersistenceInputError,
    PersistenceIntegrityError,
    PersistenceNotFoundError,
    RuntimeSnapshot,
)
from spy_market_agent.persistence.schema import validate_schema_version
from spy_market_agent.persistence.serialization import (
    JsonValue,
    bool_to_int,
    canonical_json_dumps,
    canonical_json_loads,
    date_to_text,
    datetime_to_text,
    decimal_to_text,
    finite_float,
    finite_float_for_storage,
    int_for_storage,
    int_from_storage,
    int_to_bool,
    json_to_string_tuple,
    optional_text,
    require_run_id,
    required_text,
    stored_run_id,
    text_to_date,
    text_to_datetime,
    text_to_decimal,
    tuple_to_json,
    validate_checksum,
)
from spy_market_agent.risk.models import RiskConfig, RiskError
from spy_market_agent.strategies.models import StrategyError, StrategySignalSet

_DEPENDENCY_SNAPSHOT_PACKAGES = (
    "fastapi",
    "httpx",
    "pandas",
    "pydantic",
    "scikit-learn",
    "streamlit",
)


class SQLiteArtifactRepository:
    """SQLite repository for completed Phase 3-7 research artifacts."""

    def __init__(
        self,
        database_path: DatabasePath,
        *,
        runtime_snapshot: RuntimeSnapshot | None = None,
    ) -> None:
        self._database_path = database_path
        self._runtime_snapshot = runtime_snapshot

    def save_market_data_batch(self, run_id: str, batch: MarketDataBatch) -> None:
        parsed_run_id = require_run_id(run_id)
        validated = _reconstruct_market_data_batch(batch)
        connection = self._connect()
        try:
            connection.execute("BEGIN")
            _insert_market_data_batch(
                connection,
                table_prefix="market_data",
                owner_column="batch_run_id",
                owner_id=parsed_run_id,
                batch=validated,
            )
            connection.commit()
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            _raise_integrity_error(exc, run_id=parsed_run_id)
        except sqlite3.Error as exc:
            connection.rollback()
            raise PersistenceError(
                "market_data_save_failed",
                "market-data artifact could not be saved.",
            ) from exc
        finally:
            connection.close()

    def load_market_data_batch(self, run_id: str) -> MarketDataBatch:
        parsed_run_id = require_run_id(run_id)
        connection = self._connect()
        try:
            return _load_market_data_batch(connection, parsed_run_id)
        finally:
            connection.close()

    def load_latest_market_data_batch(self) -> MarketDataBatch | None:
        connection = self._connect()
        try:
            row = connection.execute(
                """
                SELECT run_id FROM market_data_batches
                ORDER BY created_at DESC, run_id DESC
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                return None
            return _load_market_data_batch(connection, stored_run_id(row["run_id"]))
        finally:
            connection.close()

    def save_final_test_evaluation(self, run_id: str, evaluation: FinalTestEvaluation) -> None:
        parsed_run_id = require_run_id(run_id)
        validated = _reconstruct_final_test_evaluation(evaluation)
        runtime = self._runtime()
        connection = self._connect()
        try:
            connection.execute("BEGIN")
            _insert_model_run(connection, parsed_run_id, validated, runtime)
            connection.commit()
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            _raise_integrity_error(exc, run_id=parsed_run_id)
        except sqlite3.Error as exc:
            connection.rollback()
            raise PersistenceError(
                "model_run_save_failed",
                "model-run artifact could not be saved.",
            ) from exc
        finally:
            connection.close()

    def load_final_test_evaluation(self, run_id: str) -> FinalTestEvaluation:
        parsed_run_id = require_run_id(run_id)
        connection = self._connect()
        try:
            return _load_final_test_evaluation(connection, parsed_run_id)
        finally:
            connection.close()

    def list_model_runs(self) -> tuple[ModelRunSummary, ...]:
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT run_id, selected_model_name, selection_reason, created_at,
                       source_market_data_checksum, test_row_count
                FROM model_runs
                ORDER BY created_at DESC, run_id DESC
                """
            ).fetchall()
            return tuple(
                ModelRunSummary(
                    run_id=stored_run_id(row["run_id"]),
                    selected_model_name=required_text(
                        row["selected_model_name"],
                        field_name="selected_model_name",
                    ),
                    selection_reason=required_text(
                        row["selection_reason"],
                        field_name="selection_reason",
                    ),
                    created_at=required_text(row["created_at"], field_name="created_at"),
                    source_market_data_checksum=validate_checksum(
                        row["source_market_data_checksum"],
                        field_name="source_market_data_checksum",
                    ),
                    test_row_count=int_from_storage(
                        row["test_row_count"],
                        field_name="test_row_count",
                        minimum=0,
                    ),
                )
                for row in rows
            )
        finally:
            connection.close()

    def save_backtest_result(self, run_id: str, result: BacktestResult) -> None:
        parsed_run_id = require_run_id(run_id)
        validated = _reconstruct_backtest_result(result)
        runtime = self._runtime()
        connection = self._connect()
        try:
            connection.execute("BEGIN")
            _insert_backtest_run(connection, parsed_run_id, validated, runtime)
            connection.commit()
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            _raise_integrity_error(exc, run_id=parsed_run_id)
        except sqlite3.Error as exc:
            connection.rollback()
            raise PersistenceError(
                "backtest_save_failed",
                "backtest artifact could not be saved.",
            ) from exc
        finally:
            connection.close()

    def load_backtest_result(self, run_id: str) -> BacktestResult:
        parsed_run_id = require_run_id(run_id)
        connection = self._connect()
        try:
            return _load_backtest_result(connection, parsed_run_id)
        finally:
            connection.close()

    def list_backtests(self) -> tuple[BacktestRunSummary, ...]:
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT b.run_id, b.selected_model_name, b.created_at,
                       b.source_market_data_checksum, m.final_equity, m.total_return,
                       m.maximum_drawdown, m.proposed_order_count, m.fill_count
                FROM backtest_runs AS b
                JOIN backtest_metrics AS m ON m.backtest_run_id = b.run_id
                ORDER BY b.created_at DESC, b.run_id DESC
                """
            ).fetchall()
            return tuple(
                BacktestRunSummary(
                    run_id=stored_run_id(row["run_id"]),
                    selected_model_name=required_text(
                        row["selected_model_name"],
                        field_name="selected_model_name",
                    ),
                    created_at=required_text(row["created_at"], field_name="created_at"),
                    source_market_data_checksum=validate_checksum(
                        row["source_market_data_checksum"],
                        field_name="source_market_data_checksum",
                    ),
                    final_equity=finite_float(row["final_equity"], field_name="final_equity"),
                    total_return=finite_float(row["total_return"], field_name="total_return"),
                    maximum_drawdown=finite_float(
                        row["maximum_drawdown"],
                        field_name="maximum_drawdown",
                    ),
                    proposed_order_count=int_from_storage(
                        row["proposed_order_count"],
                        field_name="proposed_order_count",
                        minimum=0,
                    ),
                    fill_count=int_from_storage(
                        row["fill_count"],
                        field_name="fill_count",
                        minimum=0,
                    ),
                )
                for row in rows
            )
        finally:
            connection.close()

    def _connect(self) -> sqlite3.Connection:
        connection = connect_database(self._database_path, create=False)
        try:
            validate_schema_version(connection)
        except PersistenceError:
            connection.close()
            raise
        return connection

    def _runtime(self) -> RuntimeSnapshot:
        if self._runtime_snapshot is not None:
            return self._runtime_snapshot
        return RuntimeSnapshot(
            git_commit_hash=_current_git_commit_hash(),
            python_version=platform.python_version(),
            dependency_versions=tuple(
                (package_name, _package_version(package_name))
                for package_name in _DEPENDENCY_SNAPSHOT_PACKAGES
            ),
        )


def _insert_market_data_batch(
    connection: sqlite3.Connection,
    *,
    table_prefix: str,
    owner_column: str,
    owner_id: str,
    batch: MarketDataBatch,
) -> None:
    metadata = batch.metadata
    metadata_table = (
        "market_data_batches"
        if table_prefix == "market_data"
        else "backtest_source_market_metadata"
    )
    rows_table = (
        "market_data_rows" if table_prefix == "market_data" else "backtest_source_market_rows"
    )
    metadata_key_column = "run_id" if table_prefix == "market_data" else "backtest_run_id"
    connection.execute(
        f"""
        INSERT INTO {metadata_table} (
            {metadata_key_column}, provider_name, symbol, timeframe, adjustment_policy,
            downloaded_at, created_at, first_session, last_session, row_count,
            dataset_checksum, schema_version, source_description
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            owner_id,
            metadata.provider_name,
            metadata.symbol,
            metadata.timeframe,
            metadata.adjustment_policy,
            datetime_to_text(metadata.downloaded_at),
            datetime_to_text(metadata.created_at),
            date_to_text(metadata.first_session),
            date_to_text(metadata.last_session),
            metadata.row_count,
            metadata.dataset_checksum,
            metadata.schema_version,
            metadata.source_description,
        ),
    )
    for sequence_number, row in enumerate(batch.data.itertuples(index=False)):
        connection.execute(
            f"""
            INSERT INTO {rows_table} (
                {owner_column}, sequence_number, session, open, high, low, close, volume
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                owner_id,
                sequence_number,
                date_to_text(row.session),
                finite_float_for_storage(row.open, field_name="open"),
                finite_float_for_storage(row.high, field_name="high"),
                finite_float_for_storage(row.low, field_name="low"),
                finite_float_for_storage(row.close, field_name="close"),
                int_for_storage(row.volume, field_name="volume"),
            ),
        )


def _insert_model_run(
    connection: sqlite3.Connection,
    run_id: str,
    evaluation: FinalTestEvaluation,
    runtime: RuntimeSnapshot,
) -> None:
    locked = evaluation.locked_selection
    connection.execute(
        """
        INSERT INTO model_runs (
            run_id, selected_model_name, selection_rule_version, selection_reason,
            roc_auc_tie_break_required, log_loss_tie_break_required,
            brier_score_tie_break_required, source_market_data_checksum,
            source_schema_version, feature_schema_version, label_schema_version,
            feature_columns_json, split_spec_json, train_row_count, validation_row_count,
            train_first_session, train_last_session, validation_first_session,
            validation_last_session, test_row_count, test_first_session, test_last_session,
            random_seed, diagnostic_classification_threshold, sklearn_version,
            model_schema_version, created_at, git_commit_hash, python_version,
            dependency_versions_json
        )
        VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?
        )
        """,
        (
            run_id,
            evaluation.selected_model_name,
            locked.selection_rule_version,
            locked.selection_reason,
            bool_to_int(locked.roc_auc_tie_break_required),
            bool_to_int(locked.log_loss_tie_break_required),
            bool_to_int(locked.brier_score_tie_break_required),
            evaluation.source_market_data_checksum,
            evaluation.source_schema_version,
            evaluation.feature_schema_version,
            evaluation.label_schema_version,
            tuple_to_json(evaluation.feature_columns),
            _split_spec_to_json(evaluation.split_spec),
            locked.train_row_count,
            locked.validation_row_count,
            date_to_text(locked.train_first_session),
            date_to_text(locked.train_last_session),
            date_to_text(locked.validation_first_session),
            date_to_text(locked.validation_last_session),
            evaluation.test_row_count,
            date_to_text(evaluation.test_first_session),
            date_to_text(evaluation.test_last_session),
            evaluation.random_seed,
            finite_float_for_storage(
                evaluation.diagnostic_classification_threshold,
                field_name="diagnostic_classification_threshold",
            ),
            evaluation.sklearn_version,
            evaluation.model_schema_version,
            datetime_to_text(evaluation.created_at),
            runtime.git_commit_hash,
            runtime.python_version,
            _runtime_dependencies_to_json(runtime),
        ),
    )
    for sequence_number, snapshot in enumerate(locked.validation_metric_snapshots):
        connection.execute(
            """
            INSERT INTO model_validation_metric_snapshots (
                run_id, sequence_number, model_name, row_count, positive_count,
                negative_count, log_loss, brier_score, roc_auc
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                sequence_number,
                snapshot.model_name,
                snapshot.row_count,
                snapshot.positive_count,
                snapshot.negative_count,
                finite_float_for_storage(snapshot.log_loss, field_name="log_loss"),
                finite_float_for_storage(snapshot.brier_score, field_name="brier_score"),
                finite_float_for_storage(snapshot.roc_auc, field_name="roc_auc"),
            ),
        )
    for parameter_set in locked.candidate_parameters:
        for sequence_number, (parameter_name, parameter_value) in enumerate(
            parameter_set.parameters
        ):
            connection.execute(
                """
                INSERT INTO model_candidate_parameters (
                    run_id, model_name, sequence_number, parameter_name, parameter_value_json
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    parameter_set.model_name,
                    sequence_number,
                    parameter_name,
                    canonical_json_dumps(cast(JsonValue, parameter_value)),
                ),
            )
    for sequence_number, row in enumerate(evaluation.prediction_set.data.itertuples(index=False)):
        connection.execute(
            """
            INSERT INTO model_predictions (
                run_id, sequence_number, session, probability_positive, predicted_class, target
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                sequence_number,
                date_to_text(row.session),
                finite_float_for_storage(
                    row.probability_positive,
                    field_name="probability_positive",
                ),
                int_for_storage(row.predicted_class, field_name="predicted_class"),
                int_for_storage(row.target, field_name="target"),
            ),
        )
    _insert_classification_metrics(connection, run_id, evaluation.metrics)


def _insert_classification_metrics(
    connection: sqlite3.Connection,
    run_id: str,
    metrics: ClassificationMetrics,
) -> None:
    connection.execute(
        """
        INSERT INTO model_final_metrics (
            run_id, model_name, partition_name, diagnostic_classification_threshold,
            row_count, positive_count, negative_count, positive_rate, log_loss,
            brier_score, roc_auc, average_precision, accuracy_at_0_5, precision_at_0_5,
            recall_at_0_5, f1_at_0_5, true_negative_count, false_positive_count,
            false_negative_count, true_positive_count, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            metrics.model_name,
            metrics.partition_name,
            metrics.diagnostic_classification_threshold,
            metrics.row_count,
            metrics.positive_count,
            metrics.negative_count,
            metrics.positive_rate,
            metrics.log_loss,
            metrics.brier_score,
            metrics.roc_auc,
            metrics.average_precision,
            metrics.accuracy_at_0_5,
            metrics.precision_at_0_5,
            metrics.recall_at_0_5,
            metrics.f1_at_0_5,
            metrics.true_negative_count,
            metrics.false_positive_count,
            metrics.false_negative_count,
            metrics.true_positive_count,
            datetime_to_text(metrics.created_at),
        ),
    )


def _insert_backtest_run(
    connection: sqlite3.Connection,
    run_id: str,
    result: BacktestResult,
    runtime: RuntimeSnapshot,
) -> None:
    connection.execute(
        """
        INSERT INTO backtest_runs (
            run_id, selected_model_name, source_market_data_checksum, source_schema_version,
            feature_schema_version, label_schema_version, model_schema_version,
            strategy_schema_version, risk_schema_version, backtest_schema_version,
            feature_columns_json, split_spec_json, strategy_threshold, first_signal_session,
            last_signal_session, first_execution_session, last_execution_session, initial_cash,
            commission_bps_per_side, slippage_bps_per_side, risk_supported_symbol,
            risk_allow_short_selling, risk_allow_leverage, risk_allow_fractional_shares,
            risk_maximum_position_weight, sklearn_version, created_at,
            execution_price_checksum, git_commit_hash, python_version, dependency_versions_json
        )
        VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?
        )
        """,
        (
            run_id,
            result.selected_model_name,
            result.source_market_data_checksum,
            result.source_schema_version,
            result.feature_schema_version,
            result.label_schema_version,
            result.model_schema_version,
            result.strategy_schema_version,
            result.risk_schema_version,
            result.backtest_schema_version,
            tuple_to_json(result.feature_columns),
            _split_spec_to_json(result.split_spec),
            result.strategy_threshold,
            date_to_text(result.first_signal_session),
            date_to_text(result.last_signal_session),
            date_to_text(result.first_execution_session),
            date_to_text(result.last_execution_session),
            decimal_to_text(result.initial_cash),
            decimal_to_text(result.cost_assumptions.commission_bps_per_side),
            decimal_to_text(result.cost_assumptions.slippage_bps_per_side),
            result.risk_config.supported_symbol,
            bool_to_int(result.risk_config.allow_short_selling),
            bool_to_int(result.risk_config.allow_leverage),
            bool_to_int(result.risk_config.allow_fractional_shares),
            result.risk_config.maximum_position_weight,
            result.sklearn_version,
            datetime_to_text(result.created_at),
            result.execution_prices.execution_price_checksum,
            runtime.git_commit_hash,
            runtime.python_version,
            _runtime_dependencies_to_json(runtime),
        ),
    )
    _insert_market_data_batch(
        connection,
        table_prefix="backtest_source_market",
        owner_column="backtest_run_id",
        owner_id=run_id,
        batch=result.source_market_data,
    )
    _insert_frame_rows(
        connection,
        table_name="backtest_strategy_signals",
        owner_column="backtest_run_id",
        owner_id=run_id,
        frame=result.strategy_signal_set.data,
        columns=("signal_session", "execution_session", "probability_positive", "target_position"),
        date_columns=("signal_session", "execution_session"),
        bool_columns=(),
        json_tuple_columns=(),
    )
    _insert_frame_rows(
        connection,
        table_name="backtest_execution_prices",
        owner_column="backtest_run_id",
        owner_id=run_id,
        frame=result.execution_prices.data,
        columns=("execution_session", "reference_open", "close_price"),
        date_columns=("execution_session",),
        bool_columns=(),
        json_tuple_columns=(),
    )
    _insert_frame_rows(
        connection,
        table_name="backtest_proposed_orders",
        owner_column="backtest_run_id",
        owner_id=run_id,
        frame=result.proposed_orders,
        columns=PROPOSED_ORDER_COLUMNS,
        date_columns=("signal_session", "execution_session"),
        bool_columns=(),
        json_tuple_columns=(),
        include_sequence_column=False,
    )
    _insert_frame_rows(
        connection,
        table_name="backtest_risk_decisions",
        owner_column="backtest_run_id",
        owner_id=run_id,
        frame=result.risk_decisions,
        columns=RISK_DECISION_COLUMNS,
        date_columns=("evaluated_session",),
        bool_columns=("approved",),
        json_tuple_columns=("reason_codes",),
        include_sequence_column=False,
    )
    _insert_frame_rows(
        connection,
        table_name="backtest_fills",
        owner_column="backtest_run_id",
        owner_id=run_id,
        frame=result.fills,
        columns=FILL_COLUMNS,
        date_columns=("signal_session", "execution_session"),
        bool_columns=("risk_approved",),
        json_tuple_columns=(),
        include_sequence_column=False,
    )
    _insert_frame_rows(
        connection,
        table_name="backtest_portfolio_rows",
        owner_column="backtest_run_id",
        owner_id=run_id,
        frame=result.portfolio,
        columns=PORTFOLIO_COLUMNS,
        date_columns=("session", "signal_session"),
        bool_columns=(),
        json_tuple_columns=(),
    )
    _insert_backtest_metrics(connection, run_id, result.metrics)


def _insert_frame_rows(
    connection: sqlite3.Connection,
    *,
    table_name: str,
    owner_column: str,
    owner_id: str,
    frame: pd.DataFrame,
    columns: Iterable[str],
    date_columns: tuple[str, ...],
    bool_columns: tuple[str, ...],
    json_tuple_columns: tuple[str, ...],
    include_sequence_column: bool = True,
) -> None:
    frame_columns = tuple(columns)
    insert_columns = [owner_column]
    if include_sequence_column:
        insert_columns.append("sequence_number")
    stored_column_names = [
        "reason_codes_json" if column == "reason_codes" else column for column in frame_columns
    ]
    insert_columns.extend(stored_column_names)
    placeholders = ", ".join("?" for _ in insert_columns)
    sql = f"INSERT INTO {table_name} ({', '.join(insert_columns)}) VALUES ({placeholders})"
    for sequence_number, row in enumerate(frame.itertuples(index=False)):
        values: list[object] = [owner_id]
        if include_sequence_column:
            values.append(sequence_number)
        for column in frame_columns:
            raw_value = getattr(row, column)
            if column in date_columns:
                values.append(date_to_text(raw_value))
            elif column in bool_columns:
                values.append(bool_to_int(raw_value))
            elif column in json_tuple_columns:
                if not isinstance(raw_value, tuple):
                    raise PersistenceInputError(
                        f"invalid_{column}",
                        f"{column} must be an immutable tuple.",
                    )
                values.append(canonical_json_dumps(list(raw_value)))
            elif isinstance(raw_value, float):
                values.append(finite_float_for_storage(raw_value, field_name=column))
            else:
                values.append(raw_value)
        connection.execute(sql, tuple(values))


def _insert_backtest_metrics(
    connection: sqlite3.Connection,
    run_id: str,
    metrics: BacktestMetrics,
) -> None:
    connection.execute(
        """
        INSERT INTO backtest_metrics (
            backtest_run_id, session_count, initial_cash, final_cash, final_shares,
            final_market_value, final_equity, total_return, maximum_drawdown,
            total_reference_notional, total_execution_notional, total_commission,
            total_slippage_cost, total_transaction_cost, turnover_ratio, exposure_fraction,
            proposed_order_count, approved_order_count, rejected_order_count, fill_count,
            buy_fill_count, sell_fill_count
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            metrics.session_count,
            metrics.initial_cash,
            metrics.final_cash,
            metrics.final_shares,
            metrics.final_market_value,
            metrics.final_equity,
            metrics.total_return,
            metrics.maximum_drawdown,
            metrics.total_reference_notional,
            metrics.total_execution_notional,
            metrics.total_commission,
            metrics.total_slippage_cost,
            metrics.total_transaction_cost,
            metrics.turnover_ratio,
            metrics.exposure_fraction,
            metrics.proposed_order_count,
            metrics.approved_order_count,
            metrics.rejected_order_count,
            metrics.fill_count,
            metrics.buy_fill_count,
            metrics.sell_fill_count,
        ),
    )


def _load_market_data_batch(connection: sqlite3.Connection, run_id: str) -> MarketDataBatch:
    metadata_row = connection.execute(
        "SELECT * FROM market_data_batches WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    if metadata_row is None:
        raise PersistenceNotFoundError(
            "market_data_not_found",
            "market-data artifact was not found.",
        )
    return _load_market_data_from_rows(
        connection,
        metadata_row=metadata_row,
        rows_table="market_data_rows",
        owner_column="batch_run_id",
        owner_id=run_id,
    )


def _load_market_data_from_rows(
    connection: sqlite3.Connection,
    *,
    metadata_row: sqlite3.Row,
    rows_table: str,
    owner_column: str,
    owner_id: str,
) -> MarketDataBatch:
    rows = connection.execute(
        f"SELECT * FROM {rows_table} WHERE {owner_column} = ? ORDER BY sequence_number",
        (owner_id,),
    ).fetchall()
    try:
        data = pd.DataFrame(
            [
                {
                    "session": text_to_date(row["session"], field_name="session"),
                    "open": finite_float(row["open"], field_name="open"),
                    "high": finite_float(row["high"], field_name="high"),
                    "low": finite_float(row["low"], field_name="low"),
                    "close": finite_float(row["close"], field_name="close"),
                    "volume": int_from_storage(
                        row["volume"],
                        field_name="volume",
                        minimum=0,
                    ),
                }
                for row in rows
            ],
            columns=list(CANONICAL_COLUMNS),
        )
        _astype_columns(data, columns=("open", "high", "low", "close"), dtype_name="float64")
        _astype_columns(data, columns=("volume",), dtype_name="int64")
        metadata = MarketDataMetadata(
            provider_name=required_text(metadata_row["provider_name"], field_name="provider_name"),
            symbol=required_text(metadata_row["symbol"], field_name="symbol"),
            timeframe=required_text(metadata_row["timeframe"], field_name="timeframe"),
            adjustment_policy=required_text(
                metadata_row["adjustment_policy"],
                field_name="adjustment_policy",
            ),
            downloaded_at=text_to_datetime(metadata_row["downloaded_at"]),
            created_at=text_to_datetime(metadata_row["created_at"]),
            first_session=text_to_date(metadata_row["first_session"], field_name="first_session"),
            last_session=text_to_date(metadata_row["last_session"], field_name="last_session"),
            row_count=int_from_storage(
                metadata_row["row_count"],
                field_name="row_count",
                minimum=1,
            ),
            dataset_checksum=validate_checksum(
                metadata_row["dataset_checksum"],
                field_name="dataset_checksum",
            ),
            schema_version=required_text(
                metadata_row["schema_version"], field_name="schema_version"
            ),
            source_description=optional_text(
                metadata_row["source_description"],
                field_name="source_description",
            ),
        )
        return MarketDataBatch(data=data, metadata=metadata)
    except (ValidationError, ValueError, TypeError, OverflowError) as exc:
        raise PersistenceIntegrityError(
            "market_data_reconstruction_failed",
            "stored market data failed Phase 3 reconstruction.",
        ) from exc


def _load_final_test_evaluation(
    connection: sqlite3.Connection,
    run_id: str,
) -> FinalTestEvaluation:
    row = connection.execute("SELECT * FROM model_runs WHERE run_id = ?", (run_id,)).fetchone()
    if row is None:
        raise PersistenceNotFoundError("model_run_not_found", "model run was not found.")
    try:
        snapshots = _load_candidate_metric_snapshots(connection, run_id)
        parameters = _load_model_parameter_sets(connection, run_id)
        split_spec = _split_spec_from_json(row["split_spec_json"])
        created_at = text_to_datetime(row["created_at"])
        selected_model_name = required_text(
            row["selected_model_name"], field_name="selected_model_name"
        )
        source_checksum = validate_checksum(
            row["source_market_data_checksum"],
            field_name="source_market_data_checksum",
        )
        feature_columns = json_to_string_tuple(
            row["feature_columns_json"],
            field_name="feature_columns_json",
        )
        diagnostic_threshold = finite_float(
            row["diagnostic_classification_threshold"],
            field_name="diagnostic_classification_threshold",
        )
        random_seed = int_from_storage(row["random_seed"], field_name="random_seed", minimum=0)
        locked = LockedModelSelection(
            selected_model_name=cast(Any, selected_model_name),
            selection_rule_version=required_text(
                row["selection_rule_version"],
                field_name="selection_rule_version",
            ),
            selection_reason=required_text(row["selection_reason"], field_name="selection_reason"),
            roc_auc_tie_break_required=int_to_bool(row["roc_auc_tie_break_required"]),
            log_loss_tie_break_required=int_to_bool(row["log_loss_tie_break_required"]),
            brier_score_tie_break_required=int_to_bool(row["brier_score_tie_break_required"]),
            validation_metric_snapshots=snapshots,
            candidate_parameters=parameters,
            source_market_data_checksum=source_checksum,
            source_schema_version=required_text(
                row["source_schema_version"],
                field_name="source_schema_version",
            ),
            feature_schema_version=required_text(
                row["feature_schema_version"],
                field_name="feature_schema_version",
            ),
            label_schema_version=required_text(
                row["label_schema_version"],
                field_name="label_schema_version",
            ),
            feature_columns=feature_columns,
            split_spec=split_spec,
            train_row_count=int_from_storage(
                row["train_row_count"],
                field_name="train_row_count",
                minimum=0,
            ),
            validation_row_count=int_from_storage(
                row["validation_row_count"],
                field_name="validation_row_count",
                minimum=0,
            ),
            train_first_session=text_to_date(row["train_first_session"]),
            train_last_session=text_to_date(row["train_last_session"]),
            validation_first_session=text_to_date(row["validation_first_session"]),
            validation_last_session=text_to_date(row["validation_last_session"]),
            random_seed=random_seed,
            diagnostic_classification_threshold=diagnostic_threshold,
            sklearn_version=required_text(row["sklearn_version"], field_name="sklearn_version"),
            model_schema_version=required_text(
                row["model_schema_version"],
                field_name="model_schema_version",
            ),
            created_at=created_at,
        )
        prediction_set = _load_prediction_set(connection, run_id, row, created_at)
        metrics = _load_classification_metrics(connection, run_id)
        return FinalTestEvaluation(
            selected_model_name=cast(Any, selected_model_name),
            locked_selection=locked,
            prediction_set=prediction_set,
            metrics=metrics,
            source_market_data_checksum=source_checksum,
            source_schema_version=required_text(
                row["source_schema_version"],
                field_name="source_schema_version",
            ),
            feature_schema_version=required_text(
                row["feature_schema_version"],
                field_name="feature_schema_version",
            ),
            label_schema_version=required_text(
                row["label_schema_version"],
                field_name="label_schema_version",
            ),
            feature_columns=feature_columns,
            split_spec=split_spec,
            test_row_count=int_from_storage(
                row["test_row_count"],
                field_name="test_row_count",
                minimum=0,
            ),
            test_first_session=text_to_date(row["test_first_session"]),
            test_last_session=text_to_date(row["test_last_session"]),
            random_seed=random_seed,
            diagnostic_classification_threshold=diagnostic_threshold,
            sklearn_version=required_text(row["sklearn_version"], field_name="sklearn_version"),
            model_schema_version=required_text(
                row["model_schema_version"],
                field_name="model_schema_version",
            ),
            created_at=created_at,
        )
    except (ModelingError, ValueError, TypeError, OverflowError) as exc:
        raise PersistenceIntegrityError(
            "model_run_reconstruction_failed",
            "stored model run failed Phase 5 reconstruction.",
        ) from exc


def _load_candidate_metric_snapshots(
    connection: sqlite3.Connection,
    run_id: str,
) -> tuple[CandidateMetricSnapshot, ...]:
    rows = connection.execute(
        """
        SELECT * FROM model_validation_metric_snapshots
        WHERE run_id = ?
        ORDER BY sequence_number
        """,
        (run_id,),
    ).fetchall()
    try:
        return tuple(
            CandidateMetricSnapshot(
                model_name=cast(Any, required_text(row["model_name"], field_name="model_name")),
                row_count=int_from_storage(row["row_count"], field_name="row_count", minimum=0),
                positive_count=int_from_storage(
                    row["positive_count"],
                    field_name="positive_count",
                    minimum=0,
                ),
                negative_count=int_from_storage(
                    row["negative_count"],
                    field_name="negative_count",
                    minimum=0,
                ),
                log_loss=finite_float(row["log_loss"], field_name="log_loss"),
                brier_score=finite_float(row["brier_score"], field_name="brier_score"),
                roc_auc=finite_float(row["roc_auc"], field_name="roc_auc"),
            )
            for row in rows
        )
    except (ModelingError, ValueError, TypeError, OverflowError) as exc:
        raise PersistenceIntegrityError(
            "metric_snapshot_reconstruction_failed",
            "stored metric snapshots failed reconstruction.",
        ) from exc


def _load_model_parameter_sets(
    connection: sqlite3.Connection,
    run_id: str,
) -> tuple[ModelParameterSet, ...]:
    parameter_sets: list[ModelParameterSet] = []
    for model_name in MODEL_NAMES:
        rows = connection.execute(
            """
            SELECT parameter_name, parameter_value_json
            FROM model_candidate_parameters
            WHERE run_id = ? AND model_name = ?
            ORDER BY sequence_number
            """,
            (run_id, model_name),
        ).fetchall()
        parameters = tuple(
            (
                required_text(row["parameter_name"], field_name="parameter_name"),
                _json_to_model_parameter_value(row["parameter_value_json"]),
            )
            for row in rows
        )
        try:
            parameter_sets.append(
                ModelParameterSet(model_name=cast(Any, model_name), parameters=parameters)
            )
        except (ModelingError, ValueError, TypeError, OverflowError) as exc:
            raise PersistenceIntegrityError(
                "parameter_snapshot_reconstruction_failed",
                "stored model parameter snapshots failed reconstruction.",
            ) from exc
    return tuple(parameter_sets)


def _load_prediction_set(
    connection: sqlite3.Connection,
    run_id: str,
    model_row: sqlite3.Row,
    created_at: object,
) -> PredictionSet:
    rows = connection.execute(
        """
        SELECT * FROM model_predictions
        WHERE run_id = ?
        ORDER BY sequence_number
        """,
        (run_id,),
    ).fetchall()
    try:
        frame = pd.DataFrame(
            [
                {
                    "session": text_to_date(row["session"]),
                    "probability_positive": finite_float(
                        row["probability_positive"],
                        field_name="probability_positive",
                    ),
                    "predicted_class": int_from_storage(
                        row["predicted_class"],
                        field_name="predicted_class",
                        minimum=0,
                    ),
                    "target": int_from_storage(row["target"], field_name="target", minimum=0),
                }
                for row in rows
            ],
            columns=["session", "probability_positive", "predicted_class", "target"],
        )
        _astype_columns(frame, columns=("probability_positive",), dtype_name="float64")
        _astype_columns(frame, columns=("predicted_class", "target"), dtype_name="int64")
        return PredictionSet(
            model_name=cast(
                Any,
                required_text(model_row["selected_model_name"], field_name="selected_model_name"),
            ),
            partition_name="test",
            data=frame,
            diagnostic_classification_threshold=finite_float(
                model_row["diagnostic_classification_threshold"],
                field_name="diagnostic_classification_threshold",
            ),
            row_count=int_from_storage(
                model_row["test_row_count"],
                field_name="test_row_count",
                minimum=0,
            ),
            first_session=text_to_date(model_row["test_first_session"]),
            last_session=text_to_date(model_row["test_last_session"]),
            created_at=cast(Any, created_at),
        )
    except (ModelingError, ValueError, TypeError, OverflowError) as exc:
        raise PersistenceIntegrityError(
            "prediction_reconstruction_failed",
            "stored predictions failed reconstruction.",
        ) from exc


def _load_classification_metrics(
    connection: sqlite3.Connection,
    run_id: str,
) -> ClassificationMetrics:
    row = connection.execute(
        "SELECT * FROM model_final_metrics WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    if row is None:
        raise PersistenceIntegrityError(
            "missing_model_metrics",
            "model run is missing final metrics.",
        )
    try:
        return ClassificationMetrics(
            model_name=cast(Any, required_text(row["model_name"], field_name="model_name")),
            partition_name=cast(
                Any,
                required_text(row["partition_name"], field_name="partition_name"),
            ),
            diagnostic_classification_threshold=finite_float(
                row["diagnostic_classification_threshold"],
                field_name="diagnostic_classification_threshold",
            ),
            row_count=int_from_storage(row["row_count"], field_name="row_count", minimum=0),
            positive_count=int_from_storage(
                row["positive_count"],
                field_name="positive_count",
                minimum=0,
            ),
            negative_count=int_from_storage(
                row["negative_count"],
                field_name="negative_count",
                minimum=0,
            ),
            positive_rate=finite_float(row["positive_rate"], field_name="positive_rate"),
            log_loss=finite_float(row["log_loss"], field_name="log_loss"),
            brier_score=finite_float(row["brier_score"], field_name="brier_score"),
            roc_auc=finite_float(row["roc_auc"], field_name="roc_auc"),
            average_precision=finite_float(
                row["average_precision"],
                field_name="average_precision",
            ),
            accuracy_at_0_5=finite_float(row["accuracy_at_0_5"], field_name="accuracy_at_0_5"),
            precision_at_0_5=finite_float(
                row["precision_at_0_5"],
                field_name="precision_at_0_5",
            ),
            recall_at_0_5=finite_float(row["recall_at_0_5"], field_name="recall_at_0_5"),
            f1_at_0_5=finite_float(row["f1_at_0_5"], field_name="f1_at_0_5"),
            true_negative_count=int_from_storage(
                row["true_negative_count"],
                field_name="true_negative_count",
                minimum=0,
            ),
            false_positive_count=int_from_storage(
                row["false_positive_count"],
                field_name="false_positive_count",
                minimum=0,
            ),
            false_negative_count=int_from_storage(
                row["false_negative_count"],
                field_name="false_negative_count",
                minimum=0,
            ),
            true_positive_count=int_from_storage(
                row["true_positive_count"],
                field_name="true_positive_count",
                minimum=0,
            ),
            created_at=text_to_datetime(row["created_at"]),
        )
    except (ModelingError, ValueError, TypeError, OverflowError) as exc:
        raise PersistenceIntegrityError(
            "classification_metric_reconstruction_failed",
            "stored classification metrics failed reconstruction.",
        ) from exc


def _load_backtest_result(connection: sqlite3.Connection, run_id: str) -> BacktestResult:
    row = connection.execute("SELECT * FROM backtest_runs WHERE run_id = ?", (run_id,)).fetchone()
    if row is None:
        raise PersistenceNotFoundError("backtest_not_found", "backtest run was not found.")
    source_metadata = connection.execute(
        "SELECT * FROM backtest_source_market_metadata WHERE backtest_run_id = ?",
        (run_id,),
    ).fetchone()
    if source_metadata is None:
        raise PersistenceIntegrityError(
            "missing_backtest_source_market_data",
            "backtest run is missing source market-data metadata.",
        )
    source_batch = _load_market_data_from_rows(
        connection,
        metadata_row=source_metadata,
        rows_table="backtest_source_market_rows",
        owner_column="backtest_run_id",
        owner_id=run_id,
    )
    try:
        split_spec = _split_spec_from_json(row["split_spec_json"])
        created_at = text_to_datetime(row["created_at"])
        selected_model_name = required_text(
            row["selected_model_name"],
            field_name="selected_model_name",
        )
        source_checksum = validate_checksum(
            row["source_market_data_checksum"],
            field_name="source_market_data_checksum",
        )
        source_schema_version = required_text(
            row["source_schema_version"],
            field_name="source_schema_version",
        )
        feature_schema_version = required_text(
            row["feature_schema_version"],
            field_name="feature_schema_version",
        )
        label_schema_version = required_text(
            row["label_schema_version"],
            field_name="label_schema_version",
        )
        model_schema_version = required_text(
            row["model_schema_version"],
            field_name="model_schema_version",
        )
        strategy_schema_version = required_text(
            row["strategy_schema_version"],
            field_name="strategy_schema_version",
        )
        risk_schema_version = required_text(
            row["risk_schema_version"],
            field_name="risk_schema_version",
        )
        backtest_schema_version = required_text(
            row["backtest_schema_version"],
            field_name="backtest_schema_version",
        )
        feature_columns = json_to_string_tuple(
            row["feature_columns_json"],
            field_name="feature_columns_json",
        )
        strategy_threshold = finite_float(
            row["strategy_threshold"], field_name="strategy_threshold"
        )
        first_signal_session = text_to_date(row["first_signal_session"])
        last_signal_session = text_to_date(row["last_signal_session"])
        first_execution_session = text_to_date(row["first_execution_session"])
        last_execution_session = text_to_date(row["last_execution_session"])
        sklearn_version = required_text(row["sklearn_version"], field_name="sklearn_version")
        signals = StrategySignalSet(
            data=_load_frame(
                connection,
                table_name="backtest_strategy_signals",
                owner_column="backtest_run_id",
                owner_id=run_id,
                columns=(
                    "signal_session",
                    "execution_session",
                    "probability_positive",
                    "target_position",
                ),
                date_columns=("signal_session", "execution_session"),
                bool_columns=(),
                json_tuple_columns=(),
                int_columns=("target_position",),
                float_columns=("probability_positive",),
            ),
            selected_model_name=selected_model_name,
            strategy_threshold=strategy_threshold,
            source_market_data_checksum=source_checksum,
            source_schema_version=source_schema_version,
            feature_schema_version=feature_schema_version,
            label_schema_version=label_schema_version,
            model_schema_version=model_schema_version,
            strategy_schema_version=strategy_schema_version,
            feature_columns=feature_columns,
            split_spec=split_spec,
            market_sessions=tuple(source_batch.data["session"].to_list()),
            first_signal_session=first_signal_session,
            last_signal_session=last_signal_session,
            first_execution_session=first_execution_session,
            last_execution_session=last_execution_session,
            row_count=_count_rows(
                connection,
                "backtest_strategy_signals",
                "backtest_run_id",
                run_id,
            ),
            sklearn_version=sklearn_version,
            created_at=created_at,
        )
        execution_prices = ExecutionPriceSet(
            data=_load_frame(
                connection,
                table_name="backtest_execution_prices",
                owner_column="backtest_run_id",
                owner_id=run_id,
                columns=EXECUTION_PRICE_COLUMNS,
                date_columns=("execution_session",),
                bool_columns=(),
                json_tuple_columns=(),
                int_columns=(),
                float_columns=("reference_open", "close_price"),
            ),
            source_market_data_checksum=source_checksum,
            source_schema_version=source_schema_version,
            first_execution_session=first_execution_session,
            last_execution_session=last_execution_session,
            row_count=_count_rows(
                connection,
                "backtest_execution_prices",
                "backtest_run_id",
                run_id,
            ),
            created_at=created_at,
            execution_price_checksum=validate_checksum(
                row["execution_price_checksum"],
                field_name="execution_price_checksum",
            ),
        )
        backtest_config = BacktestConfig(
            cost_assumptions=BacktestCostAssumptions(
                commission_bps_per_side=text_to_decimal(row["commission_bps_per_side"]),
                slippage_bps_per_side=text_to_decimal(row["slippage_bps_per_side"]),
            ),
            initial_cash=text_to_decimal(row["initial_cash"]),
        )
        risk_config = RiskConfig(
            supported_symbol=required_text(
                row["risk_supported_symbol"],
                field_name="risk_supported_symbol",
            ),
            allow_short_selling=int_to_bool(row["risk_allow_short_selling"]),
            allow_leverage=int_to_bool(row["risk_allow_leverage"]),
            allow_fractional_shares=int_to_bool(row["risk_allow_fractional_shares"]),
            maximum_position_weight=finite_float(
                row["risk_maximum_position_weight"],
                field_name="risk_maximum_position_weight",
            ),
        )
        return BacktestResult(
            strategy_signal_set=signals,
            source_market_data=source_batch,
            execution_prices=execution_prices,
            proposed_orders=_load_frame(
                connection,
                table_name="backtest_proposed_orders",
                owner_column="backtest_run_id",
                owner_id=run_id,
                columns=PROPOSED_ORDER_COLUMNS,
                date_columns=("signal_session", "execution_session"),
                bool_columns=(),
                json_tuple_columns=(),
                int_columns=("sequence_number", "quantity", "target_position", "current_shares"),
                float_columns=(
                    "reference_open",
                    "estimated_execution_price",
                    "estimated_commission",
                    "estimated_cash_change",
                    "current_cash",
                ),
                order_by="sequence_number",
            ),
            risk_decisions=_load_frame(
                connection,
                table_name="backtest_risk_decisions",
                owner_column="backtest_run_id",
                owner_id=run_id,
                columns=RISK_DECISION_COLUMNS,
                date_columns=("evaluated_session",),
                bool_columns=("approved",),
                json_tuple_columns=("reason_codes",),
                int_columns=("order_sequence_number", "projected_shares"),
                float_columns=("projected_cash", "projected_market_value", "projected_equity"),
                order_by="order_sequence_number",
            ),
            fills=_load_frame(
                connection,
                table_name="backtest_fills",
                owner_column="backtest_run_id",
                owner_id=run_id,
                columns=FILL_COLUMNS,
                date_columns=("signal_session", "execution_session"),
                bool_columns=("risk_approved",),
                json_tuple_columns=(),
                int_columns=("order_sequence_number", "quantity", "shares_before", "shares_after"),
                float_columns=(
                    "reference_open",
                    "execution_price",
                    "reference_notional",
                    "execution_notional",
                    "commission",
                    "slippage_cost",
                    "total_transaction_cost",
                    "cash_change",
                    "cash_before",
                    "cash_after",
                ),
                order_by="order_sequence_number",
            ),
            portfolio=_load_frame(
                connection,
                table_name="backtest_portfolio_rows",
                owner_column="backtest_run_id",
                owner_id=run_id,
                columns=PORTFOLIO_COLUMNS,
                date_columns=("session", "signal_session"),
                bool_columns=(),
                json_tuple_columns=(),
                int_columns=("target_position", "shares"),
                float_columns=(
                    "cash",
                    "close_price",
                    "market_value",
                    "equity",
                    "daily_return",
                    "drawdown",
                ),
            ),
            metrics=_load_backtest_metrics(connection, run_id),
            backtest_config=backtest_config,
            risk_config=risk_config,
            selected_model_name=selected_model_name,
            source_market_data_checksum=source_checksum,
            source_schema_version=source_schema_version,
            feature_schema_version=feature_schema_version,
            label_schema_version=label_schema_version,
            model_schema_version=model_schema_version,
            strategy_schema_version=strategy_schema_version,
            risk_schema_version=risk_schema_version,
            backtest_schema_version=backtest_schema_version,
            feature_columns=feature_columns,
            split_spec=split_spec,
            strategy_threshold=strategy_threshold,
            first_signal_session=first_signal_session,
            last_signal_session=last_signal_session,
            first_execution_session=first_execution_session,
            last_execution_session=last_execution_session,
            initial_cash=text_to_decimal(row["initial_cash"]),
            cost_assumptions=backtest_config.cost_assumptions,
            sklearn_version=sklearn_version,
            created_at=created_at,
        )
    except (BacktestError, RiskError, StrategyError, ValueError, TypeError, OverflowError) as exc:
        raise PersistenceIntegrityError(
            "backtest_reconstruction_failed",
            "stored backtest failed Phase 6 audit reconstruction.",
        ) from exc


def _load_frame(
    connection: sqlite3.Connection,
    *,
    table_name: str,
    owner_column: str,
    owner_id: str,
    columns: tuple[str, ...],
    date_columns: tuple[str, ...],
    bool_columns: tuple[str, ...],
    json_tuple_columns: tuple[str, ...],
    int_columns: tuple[str, ...],
    float_columns: tuple[str, ...],
    order_by: str = "sequence_number",
) -> pd.DataFrame:
    selected_columns = [
        "reason_codes_json" if column == "reason_codes" else column for column in columns
    ]
    rows = connection.execute(
        f"""
        SELECT {", ".join(selected_columns)}
        FROM {table_name}
        WHERE {owner_column} = ?
        ORDER BY {order_by}
        """,
        (owner_id,),
    ).fetchall()
    records: list[dict[str, object]] = []
    for row in rows:
        record: dict[str, object] = {}
        for column in columns:
            source_column = "reason_codes_json" if column == "reason_codes" else column
            raw_value = row[source_column]
            if column in date_columns:
                record[column] = text_to_date(raw_value, field_name=column)
            elif column in bool_columns:
                record[column] = int_to_bool(raw_value, field_name=column)
            elif column in json_tuple_columns:
                parsed = canonical_json_loads(raw_value, field_name=column)
                if not isinstance(parsed, list) or any(type(item) is not str for item in parsed):
                    raise PersistenceIntegrityError(
                        f"invalid_{column}",
                        f"{column} must be an ordered JSON list of strings.",
                    )
                record[column] = tuple(parsed)
            elif column in int_columns:
                record[column] = int_from_storage(raw_value, field_name=column, minimum=0)
            elif column in float_columns:
                record[column] = finite_float(raw_value, field_name=column)
            else:
                record[column] = required_text(raw_value, field_name=column)
        records.append(record)
    frame = pd.DataFrame.from_records(records, columns=list(columns))
    _astype_columns(frame, columns=int_columns, dtype_name="int64")
    _astype_columns(frame, columns=float_columns, dtype_name="float64")
    _astype_columns(frame, columns=bool_columns, dtype_name="bool")
    return frame


def _load_backtest_metrics(connection: sqlite3.Connection, run_id: str) -> BacktestMetrics:
    row = connection.execute(
        "SELECT * FROM backtest_metrics WHERE backtest_run_id = ?",
        (run_id,),
    ).fetchone()
    if row is None:
        raise PersistenceIntegrityError("missing_backtest_metrics", "backtest metrics are missing.")
    try:
        return BacktestMetrics(
            session_count=int_from_storage(
                row["session_count"],
                field_name="session_count",
                minimum=0,
            ),
            initial_cash=finite_float(row["initial_cash"], field_name="initial_cash"),
            final_cash=finite_float(row["final_cash"], field_name="final_cash"),
            final_shares=int_from_storage(
                row["final_shares"],
                field_name="final_shares",
                minimum=0,
            ),
            final_market_value=finite_float(
                row["final_market_value"],
                field_name="final_market_value",
            ),
            final_equity=finite_float(row["final_equity"], field_name="final_equity"),
            total_return=finite_float(row["total_return"], field_name="total_return"),
            maximum_drawdown=finite_float(
                row["maximum_drawdown"],
                field_name="maximum_drawdown",
            ),
            total_reference_notional=finite_float(
                row["total_reference_notional"],
                field_name="total_reference_notional",
            ),
            total_execution_notional=finite_float(
                row["total_execution_notional"],
                field_name="total_execution_notional",
            ),
            total_commission=finite_float(row["total_commission"], field_name="total_commission"),
            total_slippage_cost=finite_float(
                row["total_slippage_cost"],
                field_name="total_slippage_cost",
            ),
            total_transaction_cost=finite_float(
                row["total_transaction_cost"],
                field_name="total_transaction_cost",
            ),
            turnover_ratio=finite_float(row["turnover_ratio"], field_name="turnover_ratio"),
            exposure_fraction=finite_float(
                row["exposure_fraction"],
                field_name="exposure_fraction",
            ),
            proposed_order_count=int_from_storage(
                row["proposed_order_count"],
                field_name="proposed_order_count",
                minimum=0,
            ),
            approved_order_count=int_from_storage(
                row["approved_order_count"],
                field_name="approved_order_count",
                minimum=0,
            ),
            rejected_order_count=int_from_storage(
                row["rejected_order_count"],
                field_name="rejected_order_count",
                minimum=0,
            ),
            fill_count=int_from_storage(row["fill_count"], field_name="fill_count", minimum=0),
            buy_fill_count=int_from_storage(
                row["buy_fill_count"],
                field_name="buy_fill_count",
                minimum=0,
            ),
            sell_fill_count=int_from_storage(
                row["sell_fill_count"],
                field_name="sell_fill_count",
                minimum=0,
            ),
        )
    except (BacktestError, ValueError, TypeError, OverflowError) as exc:
        raise PersistenceIntegrityError(
            "backtest_metric_reconstruction_failed",
            "stored backtest metrics failed reconstruction.",
        ) from exc


def _count_rows(
    connection: sqlite3.Connection,
    table_name: str,
    owner_column: str,
    owner_id: str,
) -> int:
    row = connection.execute(
        f"SELECT COUNT(*) AS row_count FROM {table_name} WHERE {owner_column} = ?",
        (owner_id,),
    ).fetchone()
    if row is None:
        return 0
    return int_from_storage(row["row_count"], field_name="row_count", minimum=0)


def _astype_columns(
    frame: pd.DataFrame,
    *,
    columns: tuple[str, ...],
    dtype_name: str,
) -> None:
    for column in columns:
        try:
            frame[column] = frame[column].astype(dtype_name)
        except (TypeError, ValueError, OverflowError) as exc:
            raise PersistenceIntegrityError(
                f"invalid_{column}_dtype",
                f"{column} could not be converted to {dtype_name}.",
            ) from exc


def _split_spec_to_json(spec: ChronologicalSplitSpec) -> str:
    return canonical_json_dumps(
        {
            "test_end_session": date_to_text(spec.test_end_session),
            "test_start_session": date_to_text(spec.test_start_session),
            "train_end_session": date_to_text(spec.train_end_session),
            "train_start_session": date_to_text(spec.train_start_session),
            "validation_end_session": date_to_text(spec.validation_end_session),
            "validation_start_session": date_to_text(spec.validation_start_session),
        }
    )


def _split_spec_from_json(value: object) -> ChronologicalSplitSpec:
    parsed = canonical_json_loads(value, field_name="split_spec_json")
    if not isinstance(parsed, dict):
        raise PersistenceIntegrityError(
            "invalid_split_spec_json",
            "split_spec_json must encode an object.",
        )
    expected_keys = {
        "test_end_session",
        "test_start_session",
        "train_end_session",
        "train_start_session",
        "validation_end_session",
        "validation_start_session",
    }
    if set(parsed) != expected_keys:
        raise PersistenceIntegrityError(
            "invalid_split_spec_json",
            "split_spec_json must contain the exact split-session keys.",
        )
    return ChronologicalSplitSpec(
        train_start_session=text_to_date(parsed.get("train_start_session")),
        train_end_session=text_to_date(parsed.get("train_end_session")),
        validation_start_session=text_to_date(parsed.get("validation_start_session")),
        validation_end_session=text_to_date(parsed.get("validation_end_session")),
        test_start_session=text_to_date(parsed.get("test_start_session")),
        test_end_session=text_to_date(parsed.get("test_end_session")),
    )


def _json_to_model_parameter_value(value: object) -> ModelParameterValue:
    parsed = canonical_json_loads(value, field_name="parameter_value_json")
    if parsed is None or type(parsed) in (str, int, float, bool):
        return cast(ModelParameterValue, parsed)
    raise PersistenceIntegrityError(
        "invalid_parameter_value",
        "model parameter values must be primitive JSON values.",
    )


def _runtime_dependencies_to_json(runtime: RuntimeSnapshot) -> str:
    return canonical_json_dumps(dict(runtime.dependency_versions))


def _current_git_commit_hash() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    value = result.stdout.strip()
    return value or None


def _package_version(package_name: str) -> str:
    try:
        return importlib_metadata.version(package_name)
    except importlib_metadata.PackageNotFoundError:
        return "unavailable"


def _reconstruct_market_data_batch(batch: object) -> MarketDataBatch:
    if not isinstance(batch, MarketDataBatch):
        raise PersistenceInputError(
            "invalid_market_data_batch",
            "batch must be a MarketDataBatch.",
        )
    try:
        return MarketDataBatch(
            data=batch.data.copy(deep=True),
            metadata=batch.metadata.model_copy(deep=True),
        )
    except (ValidationError, ValueError, TypeError) as exc:
        raise PersistenceInputError(
            "invalid_market_data_batch",
            "batch failed Phase 3 validation.",
        ) from exc


def _reconstruct_final_test_evaluation(evaluation: object) -> FinalTestEvaluation:
    if not isinstance(evaluation, FinalTestEvaluation):
        raise PersistenceInputError(
            "invalid_final_test_evaluation",
            "evaluation must be a FinalTestEvaluation.",
        )
    try:
        return FinalTestEvaluation(
            selected_model_name=evaluation.selected_model_name,
            locked_selection=evaluation.locked_selection,
            prediction_set=evaluation.prediction_set,
            metrics=evaluation.metrics,
            source_market_data_checksum=evaluation.source_market_data_checksum,
            source_schema_version=evaluation.source_schema_version,
            feature_schema_version=evaluation.feature_schema_version,
            label_schema_version=evaluation.label_schema_version,
            feature_columns=evaluation.feature_columns,
            split_spec=evaluation.split_spec,
            test_row_count=evaluation.test_row_count,
            test_first_session=evaluation.test_first_session,
            test_last_session=evaluation.test_last_session,
            random_seed=evaluation.random_seed,
            diagnostic_classification_threshold=evaluation.diagnostic_classification_threshold,
            sklearn_version=evaluation.sklearn_version,
            model_schema_version=evaluation.model_schema_version,
            created_at=evaluation.created_at,
        )
    except ModelingError as exc:
        raise PersistenceInputError(
            "invalid_final_test_evaluation",
            "evaluation failed Phase 5 validation.",
        ) from exc


def _reconstruct_backtest_result(result: object) -> BacktestResult:
    if not isinstance(result, BacktestResult):
        raise PersistenceInputError(
            "invalid_backtest_result",
            "result must be a BacktestResult.",
        )
    try:
        return BacktestResult(
            strategy_signal_set=result.strategy_signal_set,
            source_market_data=result.source_market_data,
            execution_prices=result.execution_prices,
            proposed_orders=result.proposed_orders,
            risk_decisions=result.risk_decisions,
            fills=result.fills,
            portfolio=result.portfolio,
            metrics=result.metrics,
            backtest_config=result.backtest_config,
            risk_config=result.risk_config,
            selected_model_name=result.selected_model_name,
            source_market_data_checksum=result.source_market_data_checksum,
            source_schema_version=result.source_schema_version,
            feature_schema_version=result.feature_schema_version,
            label_schema_version=result.label_schema_version,
            model_schema_version=result.model_schema_version,
            strategy_schema_version=result.strategy_schema_version,
            risk_schema_version=result.risk_schema_version,
            backtest_schema_version=BACKTEST_SCHEMA_VERSION,
            feature_columns=result.feature_columns,
            split_spec=result.split_spec,
            strategy_threshold=result.strategy_threshold,
            first_signal_session=result.first_signal_session,
            last_signal_session=result.last_signal_session,
            first_execution_session=result.first_execution_session,
            last_execution_session=result.last_execution_session,
            initial_cash=result.initial_cash,
            cost_assumptions=result.cost_assumptions,
            sklearn_version=result.sklearn_version,
            created_at=result.created_at,
        )
    except BacktestError as exc:
        raise PersistenceInputError(
            "invalid_backtest_result",
            "backtest result failed Phase 6 validation.",
        ) from exc


def _raise_integrity_error(exc: sqlite3.IntegrityError, *, run_id: str) -> None:
    message = str(exc).lower()
    if "unique" in message or "primary key" in message:
        raise PersistenceConflictError(
            "duplicate_run_id",
            f"run_id {run_id!r} already exists.",
        ) from exc
    if "foreign key" in message:
        raise PersistenceIntegrityError(
            "foreign_key_violation",
            "database foreign-key integrity was violated.",
        ) from exc
    raise PersistenceIntegrityError(
        "sqlite_integrity_error",
        "database integrity constraint was violated.",
    ) from exc


__all__ = ["SQLiteArtifactRepository"]
