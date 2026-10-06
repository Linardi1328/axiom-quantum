from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from spy_market_agent.api import create_app
from spy_market_agent.persistence import (
    PERSISTENCE_SCHEMA_VERSION,
    PersistenceConflictError,
    PersistenceError,
    PersistenceInputError,
    PersistenceIntegrityError,
    PersistenceNotFoundError,
    PersistenceSchemaError,
    SQLiteArtifactRepository,
    connect_database,
    initialize_database,
)
from unit.modeling_helpers import (
    final_test_evaluation_with_pre_cleanup_parameter_snapshot,
    pre_cleanup_logistic_parameter_snapshot,
)
from unit.phase7_helpers import (
    BACKTEST_RUN_ID,
    MARKET_RUN_ID,
    MODEL_RUN_ID,
    initialized_repository,
    make_phase7_artifacts,
)


def test_database_initialization_is_explicit_idempotent_and_enables_foreign_keys(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "phase7.sqlite3"

    initialize_database(database_path)
    initialize_database(database_path)
    connection = connect_database(database_path)
    try:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        versions = connection.execute("SELECT version FROM schema_migrations").fetchall()
        assert [row["version"] for row in versions] == [PERSISTENCE_SCHEMA_VERSION]
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO model_predictions (
                    run_id, sequence_number, session, probability_positive,
                    predicted_class, target
                )
                VALUES ('missing', 0, '2025-01-02', 0.5, 1, 1)
                """
            )
    finally:
        connection.close()


def test_repository_requires_initialized_database(tmp_path: Path) -> None:
    repository = SQLiteArtifactRepository(tmp_path / "missing.sqlite3")

    with pytest.raises(PersistenceSchemaError, match="initialized"):
        repository.list_model_runs()


def test_market_data_round_trip_and_mutation_isolation(tmp_path: Path) -> None:
    artifacts = make_phase7_artifacts()
    repository = initialized_repository(tmp_path / "phase7.sqlite3")
    original_frame = artifacts.market_data.data.copy(deep=True)

    repository.save_market_data_batch(MARKET_RUN_ID, artifacts.market_data)
    artifacts.market_data.data.loc[0, "open"] = 999.0
    loaded = repository.load_market_data_batch(MARKET_RUN_ID)
    loaded.data.loc[0, "open"] = 888.0
    reloaded = repository.load_market_data_batch(MARKET_RUN_ID)

    pd.testing.assert_frame_equal(reloaded.data, original_frame)
    assert reloaded.metadata.dataset_checksum == artifacts.market_data.metadata.dataset_checksum


def test_final_evaluation_and_backtest_round_trips_preserve_values_and_order(
    tmp_path: Path,
) -> None:
    artifacts = make_phase7_artifacts()
    repository = initialized_repository(tmp_path / "phase7.sqlite3")

    repository.save_final_test_evaluation(MODEL_RUN_ID, artifacts.evaluation)
    repository.save_backtest_result(BACKTEST_RUN_ID, artifacts.backtest)
    loaded_evaluation = repository.load_final_test_evaluation(MODEL_RUN_ID)
    loaded_backtest = repository.load_backtest_result(BACKTEST_RUN_ID)

    pd.testing.assert_frame_equal(
        loaded_evaluation.prediction_set.data,
        artifacts.evaluation.prediction_set.data,
    )
    assert loaded_evaluation.locked_selection == artifacts.evaluation.locked_selection
    assert loaded_backtest.metrics == artifacts.backtest.metrics
    assert loaded_backtest.cost_assumptions.commission_bps_per_side == (
        artifacts.backtest.cost_assumptions.commission_bps_per_side
    )
    assert loaded_backtest.cost_assumptions.slippage_bps_per_side == (
        artifacts.backtest.cost_assumptions.slippage_bps_per_side
    )
    pd.testing.assert_frame_equal(
        loaded_backtest.proposed_orders,
        artifacts.backtest.proposed_orders,
    )
    pd.testing.assert_frame_equal(
        loaded_backtest.risk_decisions,
        artifacts.backtest.risk_decisions,
    )
    pd.testing.assert_frame_equal(loaded_backtest.fills, artifacts.backtest.fills)
    pd.testing.assert_frame_equal(loaded_backtest.portfolio, artifacts.backtest.portfolio)
    pd.testing.assert_frame_equal(
        loaded_backtest.execution_prices.data,
        artifacts.backtest.execution_prices.data,
    )


def test_phase8_model_run_with_pre_cleanup_parameter_snapshot_round_trips(
    tmp_path: Path,
) -> None:
    artifacts = make_phase7_artifacts()
    evaluation = final_test_evaluation_with_pre_cleanup_parameter_snapshot(artifacts.evaluation)
    database_path = tmp_path / "phase8-model-lineage.sqlite3"
    repository = initialized_repository(database_path)

    repository.save_final_test_evaluation(MODEL_RUN_ID, evaluation)
    loaded = repository.load_final_test_evaluation(MODEL_RUN_ID)

    connection = connect_database(database_path)
    try:
        versions = connection.execute("SELECT version FROM schema_migrations").fetchall()
        assert [row["version"] for row in versions] == [PERSISTENCE_SCHEMA_VERSION]
    finally:
        connection.close()
    expected_logistic_parameters = pre_cleanup_logistic_parameter_snapshot(evaluation.random_seed)
    assert loaded.model_schema_version == "spy-binary-models-v1"
    assert loaded.locked_selection.candidate_parameters[0] == expected_logistic_parameters
    assert ("classifier.penalty", "l2") in (
        loaded.locked_selection.candidate_parameters[0].parameters
    )
    assert all(
        name != "classifier.l1_ratio"
        for name, _value in loaded.locked_selection.candidate_parameters[0].parameters
    )
    pd.testing.assert_frame_equal(
        loaded.prediction_set.data,
        evaluation.prediction_set.data,
    )


def test_duplicate_run_ids_and_missing_records_raise_structured_errors(tmp_path: Path) -> None:
    artifacts = make_phase7_artifacts()
    repository = initialized_repository(tmp_path / "phase7.sqlite3")

    repository.save_final_test_evaluation(MODEL_RUN_ID, artifacts.evaluation)
    with pytest.raises(PersistenceConflictError, match=MODEL_RUN_ID):
        repository.save_final_test_evaluation(MODEL_RUN_ID, artifacts.evaluation)
    with pytest.raises(PersistenceNotFoundError, match="not found"):
        repository.load_backtest_result("missing-backtest")


def test_transaction_rolls_back_when_nested_insert_fails(tmp_path: Path) -> None:
    artifacts = make_phase7_artifacts()
    database_path = tmp_path / "phase7.sqlite3"
    repository = initialized_repository(database_path)
    connection = connect_database(database_path)
    try:
        connection.execute(
            """
            CREATE TRIGGER fail_model_prediction_insert
            BEFORE INSERT ON model_predictions
            BEGIN
                SELECT RAISE(ABORT, 'forced prediction failure');
            END
            """
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(PersistenceIntegrityError, match="integrity"):
        repository.save_final_test_evaluation(MODEL_RUN_ID, artifacts.evaluation)

    connection = connect_database(database_path)
    try:
        count = connection.execute("SELECT COUNT(*) FROM model_runs").fetchone()[0]
        assert count == 0
    finally:
        connection.close()


def test_tampered_rows_and_unsupported_schema_versions_fail_safely(tmp_path: Path) -> None:
    artifacts = make_phase7_artifacts()
    database_path = tmp_path / "phase7.sqlite3"
    repository = initialized_repository(database_path)
    repository.save_final_test_evaluation(MODEL_RUN_ID, artifacts.evaluation)

    connection = connect_database(database_path)
    try:
        connection.execute(
            "UPDATE model_predictions SET probability_positive = 2.0 WHERE run_id = ?",
            (MODEL_RUN_ID,),
        )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(PersistenceIntegrityError, match="reconstruction"):
        repository.load_final_test_evaluation(MODEL_RUN_ID)

    connection = connect_database(database_path)
    try:
        connection.execute(
            "INSERT INTO schema_migrations(version) VALUES ('spy-sqlite-persistence-v999')"
        )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(PersistenceSchemaError, match="unsupported"):
        repository.list_model_runs()


def test_backtest_source_checksum_tampering_and_independent_reads_fail_safely(
    tmp_path: Path,
) -> None:
    artifacts = make_phase7_artifacts()
    database_path = tmp_path / "phase7.sqlite3"
    first_repository = initialized_repository(database_path)
    first_repository.save_backtest_result(BACKTEST_RUN_ID, artifacts.backtest)
    second_repository = SQLiteArtifactRepository(database_path)
    first_load = first_repository.load_backtest_result(BACKTEST_RUN_ID)
    first_load.portfolio.loc[0, "equity"] = 1.0

    second_load = second_repository.load_backtest_result(BACKTEST_RUN_ID)
    assert second_load.portfolio.loc[0, "equity"] != 1.0

    connection = connect_database(database_path)
    try:
        connection.execute(
            """
            UPDATE backtest_source_market_rows
            SET open = open + 1.0
            WHERE backtest_run_id = ? AND sequence_number = 0
            """,
            (BACKTEST_RUN_ID,),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(PersistenceIntegrityError, match="market data"):
        first_repository.load_backtest_result(BACKTEST_RUN_ID)


@pytest.mark.parametrize(
    "run_id",
    [
        "A1",
        "run.01_test-02",
        "a" * 128,
    ],
)
def test_repository_save_and_load_accept_url_safe_run_ids(tmp_path: Path, run_id: str) -> None:
    artifacts = make_phase7_artifacts()
    repository = initialized_repository(tmp_path / "phase7.sqlite3")

    repository.save_market_data_batch(run_id, artifacts.market_data)

    assert repository.load_market_data_batch(run_id).metadata.symbol == "SPY"


@pytest.mark.parametrize(
    "run_id",
    [
        "",
        "   ",
        " leading",
        "trailing ",
        "internal space",
        "bad/slash",
        "bad\\slash",
        "bad%percent",
        "bad?query",
        "bad#hash",
        "bad&amp",
        "bad:colon",
        "%2Fencoded",
        "a" * 129,
    ],
)
def test_repository_save_and_load_reject_unsafe_run_ids(
    tmp_path: Path,
    run_id: str,
) -> None:
    artifacts = make_phase7_artifacts()
    repository = initialized_repository(tmp_path / "phase7.sqlite3")

    with pytest.raises(PersistenceInputError):
        repository.save_market_data_batch(run_id, artifacts.market_data)
    with pytest.raises(PersistenceInputError):
        repository.load_market_data_batch(run_id)
    with pytest.raises(PersistenceInputError):
        repository.save_final_test_evaluation(run_id, artifacts.evaluation)
    with pytest.raises(PersistenceInputError):
        repository.load_final_test_evaluation(run_id)
    with pytest.raises(PersistenceInputError):
        repository.save_backtest_result(run_id, artifacts.backtest)
    with pytest.raises(PersistenceInputError):
        repository.load_backtest_result(run_id)


@dataclass(frozen=True, slots=True)
class TamperCase:
    name: str
    sql: str
    params: tuple[object, ...]
    repository_read: Callable[[SQLiteArtifactRepository], object]
    api_path: str


def _load_market(repository: SQLiteArtifactRepository) -> object:
    return repository.load_market_data_batch(MARKET_RUN_ID)


def _latest_market(repository: SQLiteArtifactRepository) -> object:
    return repository.load_latest_market_data_batch()


def _load_model(repository: SQLiteArtifactRepository) -> object:
    return repository.load_final_test_evaluation(MODEL_RUN_ID)


def _list_models(repository: SQLiteArtifactRepository) -> object:
    return repository.list_model_runs()


def _load_backtest(repository: SQLiteArtifactRepository) -> object:
    return repository.load_backtest_result(BACKTEST_RUN_ID)


def _list_backtests(repository: SQLiteArtifactRepository) -> object:
    return repository.list_backtests()


TAMPER_CASES = (
    TamperCase(
        name="market_data_ohlc",
        sql="UPDATE market_data_rows SET open = ? WHERE batch_run_id = ? AND sequence_number = 0",
        params=("raw-bad-market-open", MARKET_RUN_ID),
        repository_read=_load_market,
        api_path="/api/v1/data/status",
    ),
    TamperCase(
        name="market_data_volume",
        sql="UPDATE market_data_rows SET volume = ? WHERE batch_run_id = ? AND sequence_number = 0",
        params=("raw-bad-volume", MARKET_RUN_ID),
        repository_read=_load_market,
        api_path="/api/v1/data/status",
    ),
    TamperCase(
        name="market_data_row_count",
        sql="UPDATE market_data_batches SET row_count = ? WHERE run_id = ?",
        params=("raw-bad-row-count", MARKET_RUN_ID),
        repository_read=_latest_market,
        api_path="/api/v1/data/status",
    ),
    TamperCase(
        name="market_data_metadata_timestamp",
        sql="UPDATE market_data_batches SET downloaded_at = ? WHERE run_id = ?",
        params=("raw-bad-timestamp", MARKET_RUN_ID),
        repository_read=_latest_market,
        api_path="/api/v1/data/status",
    ),
    TamperCase(
        name="model_prediction_probability",
        sql="UPDATE model_predictions SET probability_positive = ? WHERE run_id = ?",
        params=("raw-bad-probability", MODEL_RUN_ID),
        repository_read=_load_model,
        api_path=f"/api/v1/model-runs/{MODEL_RUN_ID}",
    ),
    TamperCase(
        name="model_prediction_class",
        sql="UPDATE model_predictions SET predicted_class = ? WHERE run_id = ?",
        params=("raw-bad-class", MODEL_RUN_ID),
        repository_read=_load_model,
        api_path=f"/api/v1/model-runs/{MODEL_RUN_ID}",
    ),
    TamperCase(
        name="model_prediction_target",
        sql="UPDATE model_predictions SET target = ? WHERE run_id = ?",
        params=("raw-bad-target", MODEL_RUN_ID),
        repository_read=_load_model,
        api_path=f"/api/v1/model-runs/{MODEL_RUN_ID}",
    ),
    TamperCase(
        name="candidate_validation_metric",
        sql="UPDATE model_validation_metric_snapshots SET log_loss = ? WHERE run_id = ?",
        params=("raw-bad-validation-metric", MODEL_RUN_ID),
        repository_read=_load_model,
        api_path=f"/api/v1/model-runs/{MODEL_RUN_ID}",
    ),
    TamperCase(
        name="final_classification_metric",
        sql="UPDATE model_final_metrics SET roc_auc = ? WHERE run_id = ?",
        params=("raw-bad-final-metric", MODEL_RUN_ID),
        repository_read=_load_model,
        api_path=f"/api/v1/model-runs/{MODEL_RUN_ID}",
    ),
    TamperCase(
        name="model_parameter_json_nan",
        sql="UPDATE model_candidate_parameters SET parameter_value_json = ? WHERE run_id = ?",
        params=("NaN", MODEL_RUN_ID),
        repository_read=_load_model,
        api_path=f"/api/v1/model-runs/{MODEL_RUN_ID}",
    ),
    TamperCase(
        name="model_parameter_json_infinity",
        sql="UPDATE model_candidate_parameters SET parameter_value_json = ? WHERE run_id = ?",
        params=("Infinity", MODEL_RUN_ID),
        repository_read=_load_model,
        api_path=f"/api/v1/model-runs/{MODEL_RUN_ID}",
    ),
    TamperCase(
        name="model_parameter_json_overflow",
        sql="UPDATE model_candidate_parameters SET parameter_value_json = ? WHERE run_id = ?",
        params=("1e999", MODEL_RUN_ID),
        repository_read=_load_model,
        api_path=f"/api/v1/model-runs/{MODEL_RUN_ID}",
    ),
    TamperCase(
        name="backtest_strategy_row",
        sql=(
            "UPDATE backtest_strategy_signals SET probability_positive = ? "
            "WHERE backtest_run_id = ?"
        ),
        params=("raw-bad-strategy-probability", BACKTEST_RUN_ID),
        repository_read=_load_backtest,
        api_path=f"/api/v1/backtests/{BACKTEST_RUN_ID}",
    ),
    TamperCase(
        name="backtest_execution_price_row",
        sql=("UPDATE backtest_execution_prices SET reference_open = ? WHERE backtest_run_id = ?"),
        params=("raw-bad-execution-open", BACKTEST_RUN_ID),
        repository_read=_load_backtest,
        api_path=f"/api/v1/backtests/{BACKTEST_RUN_ID}",
    ),
    TamperCase(
        name="backtest_proposed_order_row",
        sql=(
            "UPDATE backtest_proposed_orders SET estimated_execution_price = ? "
            "WHERE backtest_run_id = ?"
        ),
        params=("raw-bad-proposed-order", BACKTEST_RUN_ID),
        repository_read=_load_backtest,
        api_path=f"/api/v1/backtests/{BACKTEST_RUN_ID}",
    ),
    TamperCase(
        name="risk_decision_boolean",
        sql="UPDATE backtest_risk_decisions SET approved = ? WHERE backtest_run_id = ?",
        params=("raw-bad-approved", BACKTEST_RUN_ID),
        repository_read=_load_backtest,
        api_path=f"/api/v1/backtests/{BACKTEST_RUN_ID}",
    ),
    TamperCase(
        name="risk_decision_numeric",
        sql="UPDATE backtest_risk_decisions SET projected_cash = ? WHERE backtest_run_id = ?",
        params=("raw-bad-risk-cash", BACKTEST_RUN_ID),
        repository_read=_load_backtest,
        api_path=f"/api/v1/backtests/{BACKTEST_RUN_ID}",
    ),
    TamperCase(
        name="fill_row",
        sql="UPDATE backtest_fills SET execution_price = ? WHERE backtest_run_id = ?",
        params=("raw-bad-fill-price", BACKTEST_RUN_ID),
        repository_read=_load_backtest,
        api_path=f"/api/v1/backtests/{BACKTEST_RUN_ID}",
    ),
    TamperCase(
        name="portfolio_row",
        sql="UPDATE backtest_portfolio_rows SET equity = ? WHERE backtest_run_id = ?",
        params=("raw-bad-portfolio-equity", BACKTEST_RUN_ID),
        repository_read=_load_backtest,
        api_path=f"/api/v1/backtests/{BACKTEST_RUN_ID}",
    ),
    TamperCase(
        name="backtest_metric",
        sql="UPDATE backtest_metrics SET final_equity = ? WHERE backtest_run_id = ?",
        params=("raw-bad-backtest-metric", BACKTEST_RUN_ID),
        repository_read=_load_backtest,
        api_path=f"/api/v1/backtests/{BACKTEST_RUN_ID}",
    ),
    TamperCase(
        name="model_run_summary",
        sql="UPDATE model_runs SET test_row_count = ? WHERE run_id = ?",
        params=("raw-bad-model-summary", MODEL_RUN_ID),
        repository_read=_list_models,
        api_path="/api/v1/model-runs",
    ),
    TamperCase(
        name="backtest_summary",
        sql="UPDATE backtest_metrics SET total_return = ? WHERE backtest_run_id = ?",
        params=("raw-bad-backtest-summary", BACKTEST_RUN_ID),
        repository_read=_list_backtests,
        api_path="/api/v1/backtests",
    ),
)


@pytest.mark.parametrize("case", TAMPER_CASES, ids=lambda case: case.name)
def test_corrupted_storage_values_raise_project_errors_and_api_is_sanitized(
    tmp_path: Path,
    case: TamperCase,
) -> None:
    database_path = tmp_path / "phase7.sqlite3"
    repository = initialized_repository(database_path)
    artifacts = make_phase7_artifacts()
    repository.save_market_data_batch(MARKET_RUN_ID, artifacts.market_data)
    repository.save_final_test_evaluation(MODEL_RUN_ID, artifacts.evaluation)
    repository.save_backtest_result(BACKTEST_RUN_ID, artifacts.backtest)

    connection = connect_database(database_path)
    try:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(case.sql, case.params)
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(PersistenceError):
        case.repository_read(repository)

    response = TestClient(create_app(database_path=str(database_path))).get(case.api_path)

    assert response.status_code == 503
    assert response.json() == {
        "code": response.json()["code"],
        "message": "Persisted research data is unavailable or invalid.",
    }
    for forbidden in (
        "raw-bad",
        "could not convert",
        "invalid literal",
        "ValueError",
        "Traceback",
        "sqlite",
        str(database_path),
    ):
        assert forbidden not in response.text


def test_overflowing_model_parameter_json_fails_closed_and_api_is_sanitized(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "phase7.sqlite3"
    repository = initialized_repository(database_path)
    artifacts = make_phase7_artifacts()
    repository.save_final_test_evaluation(MODEL_RUN_ID, artifacts.evaluation)

    connection = connect_database(database_path)
    try:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            "UPDATE model_candidate_parameters SET parameter_value_json = ? WHERE run_id = ?",
            ("1e999", MODEL_RUN_ID),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(PersistenceIntegrityError) as exc_info:
        repository.load_final_test_evaluation(MODEL_RUN_ID)

    assert not isinstance(exc_info.value, ValueError | OverflowError)
    assert "1e999" not in str(exc_info.value)
    assert "inf" not in str(exc_info.value).lower()

    response = TestClient(create_app(database_path=str(database_path))).get(
        f"/api/v1/model-runs/{MODEL_RUN_ID}"
    )

    assert response.status_code == 503
    assert response.json() == {
        "code": response.json()["code"],
        "message": "Persisted research data is unavailable or invalid.",
    }
    for forbidden in (
        "1e999",
        "Infinity",
        "inf",
        "ValueError",
        "OverflowError",
        str(database_path),
    ):
        assert forbidden not in response.text
