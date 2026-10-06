from __future__ import annotations

import math
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

import pandas as pd

from spy_market_agent.dashboard.client import DashboardApiClient, DashboardApiError
from spy_market_agent.run_ids import validate_run_id

DASHBOARD_WARNING = (
    "Educational and experimental research dashboard. Not investment advice. Historical "
    "classification metrics and backtests do not prove profitability."
)
DASHBOARD_PREVIEW_LIMIT = 250
DASHBOARD_MODEL_PREVIEW_LIMIT = 100
DASHBOARD_EQUITY_PAGE_LIMIT = 500
DASHBOARD_PAPER_ORDER_PREVIEW_LIMIT = 100


class DashboardClient(Protocol):
    def health(self) -> dict[str, Any]: ...

    def data_status(self) -> dict[str, Any]: ...

    def model_runs(self) -> dict[str, Any]: ...

    def model_run_detail(self, run_id: str) -> dict[str, Any]: ...

    def model_predictions(
        self,
        run_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]: ...

    def backtests(self) -> dict[str, Any]: ...

    def backtest_detail(self, run_id: str) -> dict[str, Any]: ...

    def equity(self, run_id: str, *, limit: int = 250, offset: int = 0) -> dict[str, Any]: ...

    def orders(self, run_id: str, *, limit: int = 250, offset: int = 0) -> dict[str, Any]: ...

    def risk_decisions(
        self,
        run_id: str,
        *,
        limit: int = 250,
        offset: int = 0,
    ) -> dict[str, Any]: ...

    def fills(self, run_id: str, *, limit: int = 250, offset: int = 0) -> dict[str, Any]: ...

    def paper_trading_status(self) -> dict[str, Any]: ...

    def paper_orders(self, *, limit: int = 100, offset: int = 0) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class PaginatedItems:
    items: list[dict[str, Any]]
    total: int
    limit: int
    offset: int

    @classmethod
    def empty(cls) -> PaginatedItems:
        return cls(items=[], total=0, limit=1, offset=0)

    @property
    def visible_count(self) -> int:
        return len(self.items)

    def __bool__(self) -> bool:
        return bool(self.items)

    def __len__(self) -> int:
        return len(self.items)

    def __iter__(self) -> Iterator[dict[str, Any]]:
        return iter(self.items)

    def __getitem__(self, index: int) -> dict[str, Any]:
        return self.items[index]


@dataclass(frozen=True, slots=True)
class DashboardState:
    api_available: bool
    health: dict[str, Any]
    data_status: dict[str, Any]
    model_runs: list[dict[str, Any]]
    selected_model_run: dict[str, Any] | None
    model_predictions: PaginatedItems
    backtests: list[dict[str, Any]]
    selected_backtest: dict[str, Any] | None
    equity_rows: PaginatedItems
    order_rows: PaginatedItems
    risk_decision_rows: PaginatedItems
    fill_rows: PaginatedItems
    paper_trading_status: dict[str, Any] | None
    paper_order_rows: PaginatedItems
    paper_trading_error: str | None = None
    error_message: str | None = None


def load_dashboard_state(client: DashboardClient) -> DashboardState:
    try:
        health = client.health()
        data_status = client.data_status()
        model_runs_payload = client.model_runs()
        backtests_payload = client.backtests()
        model_runs = _items(model_runs_payload)
        backtests = _items(backtests_payload)
        selected_model = None
        predictions = PaginatedItems.empty()
        if model_runs:
            run_id = _run_id_from_api_item(model_runs[0])
            selected_model = client.model_run_detail(run_id)
            predictions = _paginated(
                client.model_predictions(run_id, limit=DASHBOARD_MODEL_PREVIEW_LIMIT)
            )
        selected_backtest = None
        equity_rows = PaginatedItems.empty()
        order_rows = PaginatedItems.empty()
        risk_rows = PaginatedItems.empty()
        fill_rows = PaginatedItems.empty()
        if backtests:
            run_id = _run_id_from_api_item(backtests[0])
            selected_backtest = client.backtest_detail(run_id)
            equity_rows = _load_all_equity_pages(client, run_id)
            order_rows = _paginated(client.orders(run_id, limit=DASHBOARD_PREVIEW_LIMIT))
            risk_rows = _paginated(client.risk_decisions(run_id, limit=DASHBOARD_PREVIEW_LIMIT))
            fill_rows = _paginated(client.fills(run_id, limit=DASHBOARD_PREVIEW_LIMIT))
        paper_status: dict[str, Any] | None = None
        paper_orders = PaginatedItems.empty()
        paper_error: str | None = None
        try:
            paper_status = client.paper_trading_status()
            paper_orders = _paginated(
                client.paper_orders(limit=DASHBOARD_PAPER_ORDER_PREVIEW_LIMIT)
            )
        except (DashboardApiError, AttributeError, KeyError, TypeError, ValueError) as exc:
            paper_error = str(exc) or "Paper-trading status is unavailable."
        return DashboardState(
            api_available=True,
            health=health,
            data_status=data_status,
            model_runs=model_runs,
            selected_model_run=selected_model,
            model_predictions=predictions,
            backtests=backtests,
            selected_backtest=selected_backtest,
            equity_rows=equity_rows,
            order_rows=order_rows,
            risk_decision_rows=risk_rows,
            fill_rows=fill_rows,
            paper_trading_status=paper_status,
            paper_order_rows=paper_orders,
            paper_trading_error=paper_error,
        )
    except (DashboardApiError, KeyError, TypeError, ValueError) as exc:
        return DashboardState(
            api_available=False,
            health={},
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
            paper_trading_status=None,
            paper_order_rows=PaginatedItems.empty(),
            paper_trading_error=None,
            error_message=str(exc) or "Read API is unavailable.",
        )


def render_dashboard(st: Any, state: DashboardState) -> None:
    st.set_page_config(page_title="SPY Market Agent", layout="wide")
    st.title("SPY Market Agent")
    st.warning(DASHBOARD_WARNING)
    if not state.api_available:
        st.error(state.error_message or "Read API is unavailable.")
        return

    tabs = st.tabs(
        [
            "Overview",
            "Data Quality",
            "Model Evaluation",
            "Backtest Results",
            "Risk and Audit",
            "Paper Trading Status",
        ]
    )
    with tabs[0]:
        _render_overview(st, state)
    with tabs[1]:
        _render_data_quality(st, state)
    with tabs[2]:
        _render_model_evaluation(st, state)
    with tabs[3]:
        _render_backtest_results(st, state)
    with tabs[4]:
        _render_risk_audit(st, state)
    with tabs[5]:
        _render_paper_trading_status(st, state)


def _render_overview(st: Any, state: DashboardState) -> None:
    st.subheader("Overview")
    st.write("API status:", state.health.get("status", "unknown"))
    st.write("Data available:", state.data_status.get("available", False))
    st.write(
        "Latest model run:",
        state.model_runs[0]["run_id"] if state.model_runs else "No persisted model runs",
    )
    st.write(
        "Latest backtest run:",
        state.backtests[0]["run_id"] if state.backtests else "No persisted backtests",
    )


def _render_data_quality(st: Any, state: DashboardState) -> None:
    st.subheader("Data Quality")
    if not state.data_status.get("available", False):
        st.info("No persisted market-data status is available.")
        return
    fields = [
        "symbol",
        "provider_name",
        "source_description",
        "adjustment_policy",
        "first_session",
        "last_session",
        "row_count",
        "dataset_checksum",
        "schema_version",
        "downloaded_at",
    ]
    st.dataframe(pd.DataFrame([state.data_status]).reindex(columns=fields))


def _render_model_evaluation(st: Any, state: DashboardState) -> None:
    st.subheader("Model Evaluation")
    model = state.selected_model_run
    if model is None:
        st.info("No persisted model evaluation is available.")
        return
    st.write("Selected model:", model["selected_model_name"])
    st.write("Selection rationale:", model["selection_reason"])
    st.write("Classification metrics do not establish profitability.")
    st.dataframe(pd.DataFrame(model["validation_metric_snapshots"]))
    st.dataframe(pd.DataFrame([model["final_test_metrics"]]))
    if state.model_predictions:
        _write_page_status(st, "model predictions", state.model_predictions)
        prediction_frame = pd.DataFrame(state.model_predictions.items)
        st.line_chart(prediction_frame.set_index("session")["probability_positive"])
        st.dataframe(prediction_frame)


def _render_backtest_results(st: Any, state: DashboardState) -> None:
    st.subheader("Backtest Results")
    backtest = state.selected_backtest
    if backtest is None:
        st.info("No persisted backtest is available.")
        return
    metrics = backtest["metrics"]
    st.metric("Initial Equity", metrics["initial_cash"])
    st.metric("Final Equity", metrics["final_equity"])
    st.metric("Total Return", metrics["total_return"])
    st.metric("Maximum Drawdown", metrics["maximum_drawdown"])
    st.metric("Turnover", metrics["turnover_ratio"])
    st.metric("Exposure", metrics["exposure_fraction"])
    st.metric("Costs", metrics["total_transaction_cost"])
    st.metric("Orders", metrics["proposed_order_count"])
    st.metric("Fills", metrics["fill_count"])
    st.write("Historical backtests are approximations and do not guarantee future results.")
    if state.equity_rows:
        _write_page_status(st, "equity rows", state.equity_rows)
        equity_frame = pd.DataFrame(state.equity_rows.items)
        try:
            chart_frame = _equity_chart_frame(equity_frame)
        except DashboardApiError:
            st.error("Chart data is unavailable or invalid.")
        else:
            st.line_chart(chart_frame.set_index("session")["equity"])
            st.line_chart(chart_frame.set_index("session")["drawdown"])
        st.dataframe(equity_frame)


def _render_risk_audit(st: Any, state: DashboardState) -> None:
    st.subheader("Risk and Audit")
    backtest = state.selected_backtest
    if backtest is None:
        st.info("No persisted risk audit is available.")
        return
    st.dataframe(pd.DataFrame([backtest["risk_config"]]))
    st.write("SPY-only, long-only, no leverage, no fractional shares.")
    metrics = backtest["metrics"]
    st.write("Approved orders:", metrics["approved_order_count"])
    st.write("Rejected orders:", metrics["rejected_order_count"])
    if state.risk_decision_rows:
        _write_page_status(st, "risk decisions", state.risk_decision_rows)
        risk_frame = pd.DataFrame(state.risk_decision_rows.items)
        st.dataframe(risk_frame)
    if state.order_rows:
        _write_page_status(st, "orders", state.order_rows)
        st.dataframe(pd.DataFrame(state.order_rows.items))
    if state.fill_rows:
        _write_page_status(st, "fills", state.fill_rows)
        st.dataframe(pd.DataFrame(state.fill_rows.items))


def _render_paper_trading_status(st: Any, state: DashboardState) -> None:
    st.subheader("Paper Trading Status")
    st.warning(
        "Paper trading only. Educational and experimental local execution state. It is "
        "not investment advice, and paper fills can differ from historical backtests and "
        "live fills."
    )
    if state.paper_trading_error is not None:
        st.error(state.paper_trading_error)
    status = state.paper_trading_status
    if status is None:
        st.info("No local paper-execution status is available.")
        return
    status_rows = [
        {
            "execution_mode": status.get("execution_mode", "unknown"),
            "paper_execution_enabled": status.get("paper_execution_enabled", False),
            "dry_run": status.get("dry_run", True),
            "configuration_kill_switch_engaged": status.get(
                "configuration_kill_switch_engaged",
                True,
            ),
            "durable_kill_switch_engaged": status.get("durable_kill_switch_engaged", True),
            "effective_kill_switch_engaged": status.get(
                "effective_kill_switch_engaged",
                status.get("kill_switch_engaged", True),
            ),
            "kill_switch_engaged": status.get(
                "kill_switch_engaged",
                status.get("effective_kill_switch_engaged", True),
            ),
            "alpaca_api_key_present": _bool_label(status.get("alpaca_api_key_present", False)),
            "alpaca_secret_key_present": _bool_label(
                status.get("alpaca_secret_key_present", False)
            ),
            "last_local_attempt_status": status.get("last_local_attempt_status"),
            "last_successful_submission_at_utc": status.get("last_successful_submission_at_utc"),
            "unresolved_submission_count": status.get("unresolved_submission_count", 0),
        }
    ]
    st.dataframe(pd.DataFrame(status_rows))
    if state.paper_order_rows:
        _write_page_status(st, "paper-order attempts", state.paper_order_rows)
        fields = [
            "client_order_id",
            "signal_id",
            "side",
            "quantity",
            "attempt_status",
            "broker_status",
            "updated_at_utc",
            "failure_code",
        ]
        st.dataframe(pd.DataFrame(state.paper_order_rows.items).reindex(columns=fields))
    else:
        st.info("No local paper-order attempts are persisted.")


def _items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    items = payload.get("items", [])
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise ValueError("API payload items must be a list of objects.")
    return [dict(item) for item in items]


def _paginated(payload: dict[str, Any]) -> PaginatedItems:
    items = _items(payload)
    total = _non_negative_int(payload.get("total"), field_name="total")
    limit = _positive_int(payload.get("limit"), field_name="limit")
    offset = _non_negative_int(payload.get("offset"), field_name="offset")
    if offset + len(items) > total:
        raise DashboardApiError("Read API returned inconsistent pagination metadata.")
    return PaginatedItems(items=items, total=total, limit=limit, offset=offset)


def _load_all_equity_pages(client: DashboardClient, run_id: str) -> PaginatedItems:
    collected: list[dict[str, Any]] = []
    expected_total: int | None = None
    seen_sequences: set[int] = set()
    offset = 0
    while True:
        page = _paginated(client.equity(run_id, limit=DASHBOARD_EQUITY_PAGE_LIMIT, offset=offset))
        if page.limit != DASHBOARD_EQUITY_PAGE_LIMIT or page.offset != offset:
            raise DashboardApiError("Read API returned inconsistent equity pagination metadata.")
        if expected_total is None:
            expected_total = page.total
        elif page.total != expected_total:
            raise DashboardApiError("Read API returned inconsistent equity pagination totals.")
        if expected_total == 0:
            return PaginatedItems(items=[], total=0, limit=page.limit, offset=0)
        previous_count = len(collected)
        for item in page.items:
            sequence_number = item.get("sequence_number")
            if isinstance(sequence_number, int):
                if sequence_number in seen_sequences:
                    raise DashboardApiError("Read API returned duplicate equity pagination rows.")
                seen_sequences.add(sequence_number)
            collected.append(item)
        if len(collected) == expected_total:
            return PaginatedItems(
                items=collected,
                total=expected_total,
                limit=page.limit,
                offset=0,
            )
        if len(collected) > expected_total or len(collected) == previous_count:
            raise DashboardApiError("Read API equity pagination did not make progress.")
        offset = len(collected)


def _run_id_from_api_item(item: dict[str, Any]) -> str:
    return validate_run_id(item.get("run_id"))


def _non_negative_int(value: object, *, field_name: str) -> int:
    if isinstance(value, bool) or type(value) is not int or value < 0:
        raise DashboardApiError(f"Read API returned invalid {field_name} pagination metadata.")
    return value


def _positive_int(value: object, *, field_name: str) -> int:
    if isinstance(value, bool) or type(value) is not int or value <= 0:
        raise DashboardApiError(f"Read API returned invalid {field_name} pagination metadata.")
    return value


def _write_page_status(st: Any, label: str, page: PaginatedItems) -> None:
    st.write(f"Showing {page.visible_count} of {page.total} {label}.")


def _bool_label(value: object) -> str:
    return "present" if value is True else "not present"


def _equity_chart_frame(equity_frame: pd.DataFrame) -> pd.DataFrame:
    chart_frame = equity_frame.copy(deep=True)
    for column in ("equity", "drawdown"):
        if column not in chart_frame.columns:
            raise DashboardApiError("Chart data is unavailable or invalid.")
        chart_frame[column] = [
            _finite_chart_number(value) for value in chart_frame[column].to_list()
        ]
        chart_frame[column] = chart_frame[column].astype("float64")
    return chart_frame


def _finite_chart_number(value: object) -> float:
    if isinstance(value, bool):
        raise DashboardApiError("Chart data is unavailable or invalid.")
    if type(value) is str and value.strip() != value:
        raise DashboardApiError("Chart data is unavailable or invalid.")
    try:
        parsed_decimal = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise DashboardApiError("Chart data is unavailable or invalid.") from None
    if not parsed_decimal.is_finite():
        raise DashboardApiError("Chart data is unavailable or invalid.")
    parsed = float(parsed_decimal)
    if not math.isfinite(parsed):
        raise DashboardApiError("Chart data is unavailable or invalid.")
    return parsed


def create_default_client(
    *,
    base_url: str = "http://127.0.0.1:8000",
    timeout_seconds: float = 5.0,
) -> DashboardApiClient:
    return DashboardApiClient(base_url=base_url, timeout_seconds=timeout_seconds)


__all__ = [
    "DASHBOARD_WARNING",
    "DashboardClient",
    "DashboardState",
    "create_default_client",
    "load_dashboard_state",
    "render_dashboard",
]
