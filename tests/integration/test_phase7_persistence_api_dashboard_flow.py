from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
from fastapi.testclient import TestClient

from spy_market_agent.api import create_app
from spy_market_agent.dashboard.app import load_dashboard_state
from spy_market_agent.persistence import SQLiteArtifactRepository
from unit.phase7_helpers import (
    BACKTEST_RUN_ID,
    MARKET_RUN_ID,
    MODEL_RUN_ID,
    persist_phase7_artifacts,
)


class ApiClientAdapter:
    def __init__(self, client: TestClient) -> None:
        self._client = client

    def health(self) -> dict[str, Any]:
        return self._json("/health")

    def data_status(self) -> dict[str, Any]:
        return self._json("/api/v1/data/status")

    def model_runs(self) -> dict[str, Any]:
        return self._json("/api/v1/model-runs")

    def model_run_detail(self, run_id: str) -> dict[str, Any]:
        return self._json(f"/api/v1/model-runs/{run_id}")

    def model_predictions(
        self,
        run_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        return self._json(
            f"/api/v1/model-runs/{run_id}/predictions",
            params={"limit": limit, "offset": offset},
        )

    def backtests(self) -> dict[str, Any]:
        return self._json("/api/v1/backtests")

    def backtest_detail(self, run_id: str) -> dict[str, Any]:
        return self._json(f"/api/v1/backtests/{run_id}")

    def equity(self, run_id: str, *, limit: int = 250, offset: int = 0) -> dict[str, Any]:
        return self._json(
            f"/api/v1/backtests/{run_id}/equity",
            params={"limit": limit, "offset": offset},
        )

    def orders(self, run_id: str, *, limit: int = 250, offset: int = 0) -> dict[str, Any]:
        return self._json(
            f"/api/v1/backtests/{run_id}/orders",
            params={"limit": limit, "offset": offset},
        )

    def risk_decisions(
        self,
        run_id: str,
        *,
        limit: int = 250,
        offset: int = 0,
    ) -> dict[str, Any]:
        return self._json(
            f"/api/v1/backtests/{run_id}/risk-decisions",
            params={"limit": limit, "offset": offset},
        )

    def fills(self, run_id: str, *, limit: int = 250, offset: int = 0) -> dict[str, Any]:
        return self._json(
            f"/api/v1/backtests/{run_id}/fills",
            params={"limit": limit, "offset": offset},
        )

    def paper_trading_status(self) -> dict[str, Any]:
        return self._json("/api/v1/paper-trading/status")

    def paper_orders(self, *, limit: int = 100, offset: int = 0) -> dict[str, Any]:
        return self._json(
            "/api/v1/paper-orders",
            params={"limit": limit, "offset": offset},
        )

    def _json(self, path: str, *, params: dict[str, int] | None = None) -> dict[str, Any]:
        response = self._client.get(path, params=params)
        assert response.status_code == 200
        payload = response.json()
        assert isinstance(payload, dict)
        return payload


def test_phase7_persistence_api_dashboard_flow_is_read_only_and_deterministic(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "phase7.sqlite3"
    artifacts = persist_phase7_artifacts(database_path)

    repository = SQLiteArtifactRepository(database_path)
    loaded_market_data = repository.load_market_data_batch(MARKET_RUN_ID)
    loaded_evaluation = repository.load_final_test_evaluation(MODEL_RUN_ID)
    loaded_backtest = repository.load_backtest_result(BACKTEST_RUN_ID)

    assert loaded_market_data.metadata.dataset_checksum == (
        artifacts.market_data.metadata.dataset_checksum
    )
    assert loaded_evaluation.source_market_data_checksum == (
        artifacts.evaluation.source_market_data_checksum
    )
    assert loaded_backtest.source_market_data_checksum == (
        artifacts.backtest.source_market_data_checksum
    )
    assert loaded_backtest.execution_prices.execution_price_checksum == (
        artifacts.backtest.execution_prices.execution_price_checksum
    )
    assert loaded_backtest.risk_config.supported_symbol == "SPY"
    assert loaded_backtest.risk_config.allow_short_selling is False
    pd.testing.assert_frame_equal(loaded_market_data.data, artifacts.market_data.data)
    pd.testing.assert_frame_equal(
        loaded_evaluation.prediction_set.data,
        artifacts.evaluation.prediction_set.data,
    )
    pd.testing.assert_frame_equal(
        loaded_backtest.source_market_data.data,
        artifacts.market_data.data,
    )
    pd.testing.assert_frame_equal(
        loaded_backtest.strategy_signal_set.data,
        artifacts.backtest.strategy_signal_set.data,
    )
    pd.testing.assert_frame_equal(
        loaded_backtest.proposed_orders,
        artifacts.backtest.proposed_orders,
    )
    pd.testing.assert_frame_equal(loaded_backtest.risk_decisions, artifacts.backtest.risk_decisions)
    pd.testing.assert_frame_equal(loaded_backtest.fills, artifacts.backtest.fills)
    pd.testing.assert_frame_equal(loaded_backtest.portfolio, artifacts.backtest.portfolio)

    app = create_app(database_path=str(database_path))
    client = TestClient(app)
    model_response = client.get(f"/api/v1/model-runs/{MODEL_RUN_ID}")
    backtest_response = client.get(f"/api/v1/backtests/{BACKTEST_RUN_ID}")
    equity_response = client.get(f"/api/v1/backtests/{BACKTEST_RUN_ID}/equity")

    assert model_response.status_code == 200
    assert model_response.json()["selected_model_name"] == artifacts.evaluation.selected_model_name
    assert backtest_response.status_code == 200
    assert backtest_response.json()["risk_config"]["supported_symbol"] == "SPY"
    assert equity_response.status_code == 200
    assert len(equity_response.json()["items"]) == len(artifacts.backtest.portfolio)
    state_changing = {"POST", "PUT", "PATCH", "DELETE"}
    for route in app.routes:
        methods: set[str] = set(getattr(route, "methods", set()) or set())
        assert not state_changing.intersection(methods)

    dashboard_state = load_dashboard_state(ApiClientAdapter(client))

    assert dashboard_state.api_available is True
    assert dashboard_state.selected_model_run is not None
    assert dashboard_state.selected_model_run["run_id"] == MODEL_RUN_ID
    assert dashboard_state.selected_backtest is not None
    assert dashboard_state.selected_backtest["run_id"] == BACKTEST_RUN_ID
    assert dashboard_state.order_rows[0]["symbol"] == "SPY"
