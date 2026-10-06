from __future__ import annotations

from typing import Any, cast

import httpx

from spy_market_agent.run_ids import validate_run_id


class DashboardApiError(RuntimeError):
    """Raised when the read API is unavailable or returns an invalid response."""


class DashboardApiClient:
    """Small read-only HTTP client for the Phase 7 dashboard."""

    def __init__(
        self,
        *,
        base_url: str = "http://127.0.0.1:8000",
        timeout_seconds: float = 5.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not base_url.strip():
            raise ValueError("base_url must not be blank.")
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def health(self) -> dict[str, Any]:
        return self._get("/health")

    def data_status(self) -> dict[str, Any]:
        return self._get("/api/v1/data/status")

    def model_runs(self) -> dict[str, Any]:
        return self._get("/api/v1/model-runs")

    def model_run_detail(self, run_id: str) -> dict[str, Any]:
        parsed_run_id = validate_run_id(run_id)
        return self._get(f"/api/v1/model-runs/{parsed_run_id}")

    def model_predictions(
        self,
        run_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        parsed_run_id = validate_run_id(run_id)
        return self._get(
            f"/api/v1/model-runs/{parsed_run_id}/predictions",
            params={"limit": limit, "offset": offset},
        )

    def backtests(self) -> dict[str, Any]:
        return self._get("/api/v1/backtests")

    def backtest_detail(self, run_id: str) -> dict[str, Any]:
        parsed_run_id = validate_run_id(run_id)
        return self._get(f"/api/v1/backtests/{parsed_run_id}")

    def equity(self, run_id: str, *, limit: int = 250, offset: int = 0) -> dict[str, Any]:
        parsed_run_id = validate_run_id(run_id)
        return self._get(
            f"/api/v1/backtests/{parsed_run_id}/equity",
            params={"limit": limit, "offset": offset},
        )

    def orders(self, run_id: str, *, limit: int = 250, offset: int = 0) -> dict[str, Any]:
        parsed_run_id = validate_run_id(run_id)
        return self._get(
            f"/api/v1/backtests/{parsed_run_id}/orders",
            params={"limit": limit, "offset": offset},
        )

    def risk_decisions(self, run_id: str, *, limit: int = 250, offset: int = 0) -> dict[str, Any]:
        parsed_run_id = validate_run_id(run_id)
        return self._get(
            f"/api/v1/backtests/{parsed_run_id}/risk-decisions",
            params={"limit": limit, "offset": offset},
        )

    def fills(self, run_id: str, *, limit: int = 250, offset: int = 0) -> dict[str, Any]:
        parsed_run_id = validate_run_id(run_id)
        return self._get(
            f"/api/v1/backtests/{parsed_run_id}/fills",
            params={"limit": limit, "offset": offset},
        )

    def paper_trading_status(self) -> dict[str, Any]:
        return self._get("/api/v1/paper-trading/status")

    def paper_orders(self, *, limit: int = 100, offset: int = 0) -> dict[str, Any]:
        return self._get(
            "/api/v1/paper-orders",
            params={"limit": limit, "offset": offset},
        )

    def paper_order_detail(self, client_order_id: str) -> dict[str, Any]:
        parsed_client_order_id = validate_run_id(client_order_id)
        return self._get(f"/api/v1/paper-orders/{parsed_client_order_id}")

    def _get(self, path: str, *, params: dict[str, int] | None = None) -> dict[str, Any]:
        try:
            response = self._client.get(path, params=params)
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as exc:
            raise DashboardApiError("Read API is unavailable.") from exc
        if not isinstance(payload, dict):
            raise DashboardApiError("Read API returned an invalid payload.")
        return cast(dict[str, Any], payload)


__all__ = ["DashboardApiClient", "DashboardApiError"]
