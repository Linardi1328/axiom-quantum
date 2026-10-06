from __future__ import annotations

import inspect
from datetime import date, timedelta
from typing import Any

import httpx
import pandas as pd
import pytest

import spy_market_agent.dashboard as dashboard
import spy_market_agent.dashboard.app as dashboard_app
import spy_market_agent.dashboard.client as dashboard_client
from spy_market_agent.dashboard import DASHBOARD_WARNING
from spy_market_agent.dashboard.app import (
    DashboardState,
    PaginatedItems,
    load_dashboard_state,
    render_dashboard,
)
from spy_market_agent.dashboard.client import DashboardApiClient, DashboardApiError


def _session(index: int) -> str:
    return (date(2025, 1, 2) + timedelta(days=index)).isoformat()


class FakeClient:
    def __init__(self, *, populated: bool = True, fail: bool = False) -> None:
        self.populated = populated
        self.fail = fail
        self.model_detail_called = False
        self.backtest_detail_called = False

    def health(self) -> dict[str, Any]:
        if self.fail:
            raise DashboardApiError("api unavailable")
        return {"status": "ok"}

    def data_status(self) -> dict[str, Any]:
        return {"available": self.populated, "symbol": "SPY"}

    def model_runs(self) -> dict[str, Any]:
        return {"items": [{"run_id": "model-run-1"}] if self.populated else []}

    def model_run_detail(self, run_id: str) -> dict[str, Any]:
        self.model_detail_called = True
        return {
            "run_id": run_id,
            "selected_model_name": "logistic_regression",
            "selection_reason": "Selected for test.",
            "validation_metric_snapshots": [],
            "final_test_metrics": {"row_count": 1},
        }

    def model_predictions(
        self,
        run_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        _ = (run_id, limit)
        items = [
            {
                "sequence_number": offset,
                "session": "2025-01-02",
                "probability_positive": 0.5,
            }
        ]
        return {
            "run_id": run_id,
            "total": len(items),
            "limit": limit,
            "offset": offset,
            "items": items,
        }

    def backtests(self) -> dict[str, Any]:
        return {"items": [{"run_id": "backtest-run-1"}] if self.populated else []}

    def backtest_detail(self, run_id: str) -> dict[str, Any]:
        self.backtest_detail_called = True
        return {
            "run_id": run_id,
            "metrics": {
                "initial_cash": "10000",
                "final_equity": "10050",
                "total_return": 0.005,
                "maximum_drawdown": 0.01,
                "turnover_ratio": 0.2,
                "exposure_fraction": 0.5,
                "total_transaction_cost": "1.25",
                "proposed_order_count": 1,
                "approved_order_count": 1,
                "rejected_order_count": 0,
                "fill_count": 1,
            },
            "risk_config": {
                "supported_symbol": "SPY",
                "allow_short_selling": False,
                "allow_leverage": False,
                "allow_fractional_shares": False,
                "maximum_position_weight": 1.0,
            },
        }

    def equity(self, run_id: str, *, limit: int = 250, offset: int = 0) -> dict[str, Any]:
        items = [{"session": "2025-01-03", "equity": "10050", "drawdown": 0.0}]
        return {
            "run_id": run_id,
            "total": len(items),
            "limit": limit,
            "offset": offset,
            "items": items,
        }

    def orders(self, run_id: str, *, limit: int = 250, offset: int = 0) -> dict[str, Any]:
        items = [{"sequence_number": 1, "symbol": "SPY"}]
        return {
            "run_id": run_id,
            "total": len(items),
            "limit": limit,
            "offset": offset,
            "items": items,
        }

    def risk_decisions(self, run_id: str, *, limit: int = 250, offset: int = 0) -> dict[str, Any]:
        items = [{"order_sequence_number": 1, "approved": True, "reason_codes": ["approved"]}]
        return {
            "run_id": run_id,
            "total": len(items),
            "limit": limit,
            "offset": offset,
            "items": items,
        }

    def fills(self, run_id: str, *, limit: int = 250, offset: int = 0) -> dict[str, Any]:
        items = [{"order_sequence_number": 1, "risk_approved": True}]
        return {
            "run_id": run_id,
            "total": len(items),
            "limit": limit,
            "offset": offset,
            "items": items,
        }

    def paper_trading_status(self) -> dict[str, Any]:
        return {
            "kill_switch_engaged": True,
            "configuration_kill_switch_engaged": True,
            "durable_kill_switch_engaged": True,
            "effective_kill_switch_engaged": True,
            "execution_mode": "paper",
            "paper_execution_enabled": False,
            "dry_run": True,
            "alpaca_api_key_present": False,
            "alpaca_secret_key_present": False,
            "last_local_attempt_status": None,
            "last_successful_submission_at_utc": None,
            "unresolved_submission_count": 0,
        }

    def paper_orders(self, *, limit: int = 100, offset: int = 0) -> dict[str, Any]:
        items = (
            [
                {
                    "client_order_id": "paper-order-1",
                    "signal_id": "signal-1",
                    "side": "buy",
                    "quantity": 10,
                    "attempt_status": "accepted",
                    "broker_status": "accepted",
                    "updated_at_utc": "2025-01-03T15:30:00Z",
                    "failure_code": None,
                }
            ]
            if self.populated
            else []
        )
        return {
            "total": len(items),
            "limit": limit,
            "offset": offset,
            "items": items,
        }


class PaginatedFakeClient(FakeClient):
    def __init__(
        self,
        *,
        prediction_total: int = 150,
        equity_total: int = 501,
        order_total: int = 1200,
        risk_total: int = 1200,
        fill_total: int = 900,
        approved_order_count: int = 700,
        rejected_order_count: int = 500,
        inconsistent_equity_total: bool = False,
        stalled_equity_page: bool = False,
        fail_equity_offset: int | None = None,
    ) -> None:
        super().__init__(populated=True)
        self.prediction_total = prediction_total
        self.equity_total = equity_total
        self.order_total = order_total
        self.risk_total = risk_total
        self.fill_total = fill_total
        self.approved_order_count = approved_order_count
        self.rejected_order_count = rejected_order_count
        self.inconsistent_equity_total = inconsistent_equity_total
        self.stalled_equity_page = stalled_equity_page
        self.fail_equity_offset = fail_equity_offset
        self.equity_offsets: list[int] = []

    def model_predictions(
        self,
        run_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        items = [
            {
                "sequence_number": index,
                "session": _session(index),
                "probability_positive": 0.5,
            }
            for index in range(offset, min(offset + limit, self.prediction_total))
        ]
        return _page(
            run_id=run_id, total=self.prediction_total, limit=limit, offset=offset, items=items
        )

    def backtest_detail(self, run_id: str) -> dict[str, Any]:
        detail = super().backtest_detail(run_id)
        detail["metrics"].update(
            {
                "proposed_order_count": self.order_total,
                "approved_order_count": self.approved_order_count,
                "rejected_order_count": self.rejected_order_count,
                "fill_count": self.fill_total,
            }
        )
        return detail

    def equity(self, run_id: str, *, limit: int = 250, offset: int = 0) -> dict[str, Any]:
        self.equity_offsets.append(offset)
        if self.fail_equity_offset is not None and offset >= self.fail_equity_offset:
            raise DashboardApiError("later equity page failed")
        total = (
            self.equity_total - 1
            if self.inconsistent_equity_total and offset
            else self.equity_total
        )
        if self.stalled_equity_page and offset:
            items: list[dict[str, Any]] = []
        else:
            items = [
                {
                    "sequence_number": index,
                    "session": _session(index),
                    "equity": str(10000 + index),
                    "drawdown": -0.001 * index,
                }
                for index in range(offset, min(offset + limit, self.equity_total))
            ]
        return _page(run_id=run_id, total=total, limit=limit, offset=offset, items=items)

    def orders(self, run_id: str, *, limit: int = 250, offset: int = 0) -> dict[str, Any]:
        items = [
            {"sequence_number": index + 1, "symbol": "SPY"}
            for index in range(offset, min(offset + limit, self.order_total))
        ]
        return _page(run_id=run_id, total=self.order_total, limit=limit, offset=offset, items=items)

    def risk_decisions(self, run_id: str, *, limit: int = 250, offset: int = 0) -> dict[str, Any]:
        items = [
            {
                "order_sequence_number": index + 1,
                "approved": True,
                "reason_codes": ["approved"],
            }
            for index in range(offset, min(offset + limit, self.risk_total))
        ]
        return _page(run_id=run_id, total=self.risk_total, limit=limit, offset=offset, items=items)

    def fills(self, run_id: str, *, limit: int = 250, offset: int = 0) -> dict[str, Any]:
        items = [
            {"order_sequence_number": index + 1, "risk_approved": True}
            for index in range(offset, min(offset + limit, self.fill_total))
        ]
        return _page(run_id=run_id, total=self.fill_total, limit=limit, offset=offset, items=items)

    def paper_orders(self, *, limit: int = 100, offset: int = 0) -> dict[str, Any]:
        items = [
            {
                "client_order_id": f"paper-order-{index}",
                "signal_id": f"signal-{index}",
                "side": "buy",
                "quantity": 10,
                "attempt_status": "accepted",
                "broker_status": "accepted",
                "updated_at_utc": _session(index),
                "failure_code": None,
            }
            for index in range(offset, min(offset + limit, 125))
        ]
        return {"total": 125, "limit": limit, "offset": offset, "items": items}


def _page(
    *,
    run_id: str,
    total: int,
    limit: int,
    offset: int,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    return {"run_id": run_id, "total": total, "limit": limit, "offset": offset, "items": items}


class FakeTab:
    def __enter__(self) -> FakeTab:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None


class FakeStreamlit:
    def __init__(self) -> None:
        self.text: list[str] = []
        self.line_charts: list[Any] = []
        self.dataframes: list[pd.DataFrame] = []

    def __getattr__(self, name: str) -> Any:
        def recorder(*args: object, **_kwargs: object) -> object:
            self.text.extend(str(arg) for arg in args)
            if name == "tabs":
                return [FakeTab(), FakeTab(), FakeTab(), FakeTab(), FakeTab(), FakeTab()]
            if name == "line_chart" and args:
                self.line_charts.append(args[0])
            if name == "dataframe" and args and isinstance(args[0], pd.DataFrame):
                self.dataframes.append(args[0].copy(deep=True))
            return None

        return recorder


def test_dashboard_http_client_parses_responses_and_handles_api_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(503, json={"code": "unavailable"})

    client = DashboardApiClient(
        base_url="http://testserver",
        transport=httpx.MockTransport(handler),
    )

    assert client.health() == {"status": "ok"}
    with pytest.raises(DashboardApiError, match="unavailable"):
        client.data_status()
    client.close()


def test_dashboard_state_handles_empty_populated_and_unavailable_api() -> None:
    empty = load_dashboard_state(FakeClient(populated=False))
    populated = load_dashboard_state(FakeClient(populated=True))
    unavailable = load_dashboard_state(FakeClient(fail=True))

    assert empty.api_available is True
    assert empty.selected_model_run is None
    assert populated.selected_model_run is not None
    assert populated.selected_backtest is not None
    assert populated.model_predictions.total == 1
    assert populated.equity_rows.total == 1
    assert unavailable.api_available is False


def test_dashboard_rendering_shows_required_warning_and_no_write_controls() -> None:
    streamlit = FakeStreamlit()
    state = DashboardState(
        api_available=True,
        health={"status": "ok"},
        data_status={"available": False},
        model_runs=[],
        selected_model_run=None,
        model_predictions=PaginatedItems.empty(),
        backtests=[],
        selected_backtest=None,
        equity_rows=PaginatedItems.empty(),
        order_rows=PaginatedItems.empty(),
        risk_decision_rows=PaginatedItems.empty(),
        fill_rows=PaginatedItems.empty(),
        paper_trading_status={
            "kill_switch_engaged": True,
            "configuration_kill_switch_engaged": True,
            "durable_kill_switch_engaged": True,
            "effective_kill_switch_engaged": True,
            "execution_mode": "paper",
            "paper_execution_enabled": False,
            "dry_run": True,
            "alpaca_api_key_present": False,
            "alpaca_secret_key_present": False,
            "last_local_attempt_status": None,
            "last_successful_submission_at_utc": None,
            "unresolved_submission_count": 0,
        },
        paper_order_rows=PaginatedItems.empty(),
    )

    render_dashboard(streamlit, state)
    rendered_text = " ".join(streamlit.text).lower()

    assert "not investment advice" in rendered_text
    assert "submit" not in rendered_text
    assert "approve order" not in rendered_text


def test_dashboard_import_smoke_public_exports_and_no_database_access() -> None:
    source = inspect.getsource(dashboard)
    app_source = inspect.getsource(dashboard_app)
    client_source = inspect.getsource(dashboard_client)

    assert "DashboardApiClient" in dashboard.__all__
    assert "persistence" not in source + app_source + client_source
    assert "alpaca_paper" not in (source + app_source + client_source).lower()
    assert "execution.repository" not in source + app_source + client_source
    assert "button" not in (source + app_source + client_source).lower()
    assert "not investment advice" in DASHBOARD_WARNING.lower()


def test_dashboard_validates_api_run_ids_before_building_detail_paths() -> None:
    client = FakeClient(populated=True)
    client.model_runs = lambda: {"items": [{"run_id": "bad/slash"}]}  # type: ignore[method-assign]

    state = load_dashboard_state(client)

    assert state.api_available is False
    assert client.model_detail_called is False


def test_dashboard_client_validates_run_ids_before_requesting_paths() -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        return httpx.Response(200, json={})

    client = DashboardApiClient(
        base_url="http://testserver",
        transport=httpx.MockTransport(handler),
    )

    client.model_run_detail("run.01_test-02")
    with pytest.raises(ValueError, match="run_id"):
        client.backtest_detail("bad/slash")
    client.close()

    assert requests == ["/api/v1/model-runs/run.01_test-02"]


def test_dashboard_api_client_rejects_blank_base_url() -> None:
    with pytest.raises(ValueError, match="base_url"):
        DashboardApiClient(base_url="   ")


def test_dashboard_api_client_builds_read_only_paths_and_params() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True})

    client = DashboardApiClient(
        base_url="http://read-api.local/",
        transport=httpx.MockTransport(handler),
    )
    try:
        assert client.health() == {"ok": True}
        assert client.data_status() == {"ok": True}
        assert client.model_runs() == {"ok": True}
        assert client.model_predictions("model.run_1-2", limit=17, offset=3) == {"ok": True}
        assert client.backtests() == {"ok": True}
        assert client.backtest_detail("backtest.run_1-2") == {"ok": True}
        assert client.equity("backtest.run_1-2", limit=19, offset=5) == {"ok": True}
        assert client.orders("backtest.run_1-2", limit=23, offset=7) == {"ok": True}
        assert client.risk_decisions("backtest.run_1-2", limit=29, offset=11) == {"ok": True}
        assert client.fills("backtest.run_1-2", limit=31, offset=13) == {"ok": True}
        assert client.paper_trading_status() == {"ok": True}
        assert client.paper_orders(limit=37, offset=17) == {"ok": True}
        assert client.paper_order_detail("client.order_1-2") == {"ok": True}
    finally:
        client.close()

    assert [request.method for request in requests] == ["GET"] * len(requests)
    assert [request.url.path for request in requests] == [
        "/health",
        "/api/v1/data/status",
        "/api/v1/model-runs",
        "/api/v1/model-runs/model.run_1-2/predictions",
        "/api/v1/backtests",
        "/api/v1/backtests/backtest.run_1-2",
        "/api/v1/backtests/backtest.run_1-2/equity",
        "/api/v1/backtests/backtest.run_1-2/orders",
        "/api/v1/backtests/backtest.run_1-2/risk-decisions",
        "/api/v1/backtests/backtest.run_1-2/fills",
        "/api/v1/paper-trading/status",
        "/api/v1/paper-orders",
        "/api/v1/paper-orders/client.order_1-2",
    ]
    assert requests[3].url.params == httpx.QueryParams({"limit": "17", "offset": "3"})
    assert requests[6].url.params == httpx.QueryParams({"limit": "19", "offset": "5"})
    assert requests[7].url.params == httpx.QueryParams({"limit": "23", "offset": "7"})
    assert requests[8].url.params == httpx.QueryParams({"limit": "29", "offset": "11"})
    assert requests[9].url.params == httpx.QueryParams({"limit": "31", "offset": "13"})
    assert requests[11].url.params == httpx.QueryParams({"limit": "37", "offset": "17"})


@pytest.mark.parametrize(
    ("method_name", "args"),
    [
        ("model_predictions", ("bad/run",)),
        ("equity", ("bad/run",)),
        ("orders", ("bad/run",)),
        ("risk_decisions", ("bad/run",)),
        ("fills", ("bad/run",)),
        ("paper_order_detail", ("bad/run",)),
    ],
)
def test_dashboard_api_client_rejects_invalid_paginated_path_ids(
    method_name: str,
    args: tuple[str],
) -> None:
    client = DashboardApiClient(
        base_url="http://read-api.local",
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json={"ok": True})),
    )
    try:
        method = getattr(client, method_name)
        with pytest.raises(ValueError, match="run_id"):
            method(*args)
    finally:
        client.close()


def test_dashboard_api_client_translates_http_errors() -> None:
    client = DashboardApiClient(
        base_url="http://read-api.local",
        transport=httpx.MockTransport(lambda _request: httpx.Response(503, json={"error": "down"})),
    )
    try:
        with pytest.raises(DashboardApiError, match="Read API is unavailable"):
            client.health()
    finally:
        client.close()


def test_dashboard_api_client_rejects_non_object_json_payload() -> None:
    client = DashboardApiClient(
        base_url="http://read-api.local",
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=["not", "object"])),
    )
    try:
        with pytest.raises(DashboardApiError, match="invalid payload"):
            client.health()
    finally:
        client.close()


def test_dashboard_preserves_paginated_metadata_and_fetches_all_equity_pages() -> None:
    client = PaginatedFakeClient(equity_total=501)
    state = load_dashboard_state(client)

    assert state.api_available is True
    assert state.model_predictions.total == 150
    assert state.model_predictions.visible_count == 100
    assert state.order_rows.total == 1200
    assert state.order_rows.visible_count == 250
    assert state.risk_decision_rows.total == 1200
    assert state.fill_rows.total == 900
    assert state.equity_rows.total == 501
    assert state.equity_rows.visible_count == 501
    assert client.equity_offsets == [0, 500]
    assert [row["session"] for row in state.equity_rows.items] == [
        _session(index) for index in range(501)
    ]


def test_dashboard_rendering_uses_authoritative_metrics_and_preview_labels() -> None:
    state = load_dashboard_state(PaginatedFakeClient())
    streamlit = FakeStreamlit()

    render_dashboard(streamlit, state)
    rendered_text = " ".join(streamlit.text)

    assert "Approved orders: 700" in rendered_text
    assert "Rejected orders: 500" in rendered_text
    assert "Showing 100 of 150 model predictions." in rendered_text
    assert "Showing 250 of 1200 orders." in rendered_text
    assert "Showing 250 of 1200 risk decisions." in rendered_text
    assert "Showing 250 of 900 fills." in rendered_text
    assert "Showing 100 of 125 paper-order attempts." in rendered_text


def test_dashboard_paper_status_view_is_read_only_and_redacted() -> None:
    state = load_dashboard_state(PaginatedFakeClient())
    streamlit = FakeStreamlit()

    render_dashboard(streamlit, state)
    rendered_text = " ".join(streamlit.text).lower()
    status_frames = [
        frame for frame in streamlit.dataframes if "alpaca_api_key_present" in frame.columns
    ]

    assert state.paper_trading_status is not None
    assert "paper trading status" in rendered_text
    assert "paper trading only" in rendered_text
    assert "not investment advice" in rendered_text
    assert "approve order" not in rendered_text
    assert "cancel" not in rendered_text
    assert "liquidate" not in rendered_text
    assert status_frames
    assert "configuration_kill_switch_engaged" in status_frames[0].columns
    assert "durable_kill_switch_engaged" in status_frames[0].columns
    assert "effective_kill_switch_engaged" in status_frames[0].columns
    assert bool(status_frames[0].loc[0, "configuration_kill_switch_engaged"]) is True
    assert bool(status_frames[0].loc[0, "durable_kill_switch_engaged"]) is True
    assert bool(status_frames[0].loc[0, "effective_kill_switch_engaged"]) is True
    assert status_frames[0].loc[0, "alpaca_api_key_present"] == "not present"
    assert status_frames[0].loc[0, "alpaca_secret_key_present"] == "not present"


@pytest.mark.parametrize(
    "client",
    [
        PaginatedFakeClient(inconsistent_equity_total=True),
        PaginatedFakeClient(stalled_equity_page=True),
        PaginatedFakeClient(fail_equity_offset=500),
    ],
)
def test_dashboard_rejects_inconsistent_or_non_progressing_equity_pagination(
    client: PaginatedFakeClient,
) -> None:
    state = load_dashboard_state(client)

    assert state.api_available is False
    assert state.error_message


def test_dashboard_handles_empty_paginated_response() -> None:
    state = load_dashboard_state(
        PaginatedFakeClient(
            prediction_total=0,
            equity_total=0,
            order_total=0,
            risk_total=0,
            fill_total=0,
            approved_order_count=0,
            rejected_order_count=0,
        )
    )

    assert state.api_available is True
    assert state.model_predictions.total == 0
    assert state.equity_rows.items == []
    assert state.order_rows.items == []


def test_dashboard_charts_convert_valid_exact_strings_without_mutating_tables() -> None:
    streamlit = FakeStreamlit()
    state = DashboardState(
        api_available=True,
        health={"status": "ok"},
        data_status={"available": False},
        model_runs=[],
        selected_model_run=None,
        model_predictions=PaginatedItems.empty(),
        backtests=[{"run_id": "backtest-run-1"}],
        selected_backtest=FakeClient().backtest_detail("backtest-run-1"),
        equity_rows=PaginatedItems(
            items=[
                {"session": "2025-01-02", "equity": "10000.50", "drawdown": "0"},
                {"session": "2025-01-03", "equity": "10001.75", "drawdown": "-0.01"},
            ],
            total=2,
            limit=500,
            offset=0,
        ),
        order_rows=PaginatedItems.empty(),
        risk_decision_rows=PaginatedItems.empty(),
        fill_rows=PaginatedItems.empty(),
        paper_trading_status=None,
        paper_order_rows=PaginatedItems.empty(),
    )

    render_dashboard(streamlit, state)

    assert list(streamlit.line_charts[0].index) == ["2025-01-02", "2025-01-03"]
    assert streamlit.line_charts[0].dtype == "float64"
    assert float(streamlit.line_charts[0].iloc[0]) == 10000.5
    equity_table = next(frame for frame in streamlit.dataframes if "equity" in frame.columns)
    assert isinstance(equity_table.loc[0, "equity"], str)
    assert equity_table.loc[0, "equity"] == "10000.50"


@pytest.mark.parametrize("bad_value", ["not-a-number", "NaN", "Infinity", "-Infinity"])
def test_dashboard_rejects_malformed_nan_and_infinite_chart_values_gracefully(
    bad_value: str,
) -> None:
    streamlit = FakeStreamlit()
    state = DashboardState(
        api_available=True,
        health={"status": "ok"},
        data_status={"available": False},
        model_runs=[],
        selected_model_run=None,
        model_predictions=PaginatedItems.empty(),
        backtests=[{"run_id": "backtest-run-1"}],
        selected_backtest=FakeClient().backtest_detail("backtest-run-1"),
        equity_rows=PaginatedItems(
            items=[{"session": "2025-01-02", "equity": bad_value, "drawdown": 0.0}],
            total=1,
            limit=500,
            offset=0,
        ),
        order_rows=PaginatedItems.empty(),
        risk_decision_rows=PaginatedItems.empty(),
        fill_rows=PaginatedItems.empty(),
        paper_trading_status=None,
        paper_order_rows=PaginatedItems.empty(),
    )

    render_dashboard(streamlit, state)

    assert "Chart data is unavailable or invalid." in " ".join(streamlit.text)
    assert streamlit.line_charts == []
