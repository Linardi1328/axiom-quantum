from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from itertools import pairwise
from typing import Any, NoReturn, cast

import pandas as pd
import sklearn
from pydantic import ValidationError

from spy_market_agent.datasets.models import LABEL_SCHEMA_VERSION
from spy_market_agent.datasets.splits import ChronologicalSplitSpec
from spy_market_agent.features.models import FEATURE_COLUMNS, FEATURE_SCHEMA_VERSION
from spy_market_agent.market_data.checksum import compute_market_data_checksum
from spy_market_agent.market_data.models import (
    ADJUSTMENT_POLICY,
    CANONICAL_COLUMNS,
    MARKET_SYMBOL,
    MARKET_TIMEFRAME,
    MarketDataBatch,
    MarketDataMetadata,
)
from spy_market_agent.market_data.models import SCHEMA_VERSION as MARKET_DATA_SCHEMA_VERSION
from spy_market_agent.modeling.models import MODEL_NAMES, MODEL_SCHEMA_VERSION
from spy_market_agent.risk.models import (
    APPROVED_REASON,
    BUY_SIDE,
    RISK_SCHEMA_VERSION,
    SELL_SIDE,
    SUPPORTED_SYMBOL,
    PortfolioState,
    ProposedOrder,
    RiskConfig,
    RiskDecision,
    RiskError,
)
from spy_market_agent.strategies.models import (
    STRATEGY_LONG_PROBABILITY_THRESHOLD,
    STRATEGY_SCHEMA_VERSION,
    StrategySignalSet,
)

BACKTEST_SCHEMA_VERSION = "spy-daily-next-open-backtest-v1"
INITIAL_SIMULATED_CASH = Decimal("10000")
TRADING_SESSIONS_PER_YEAR = 252

PROPOSED_ORDER_COLUMNS = (
    "sequence_number",
    "symbol",
    "side",
    "quantity",
    "signal_session",
    "execution_session",
    "target_position",
    "reference_open",
    "estimated_execution_price",
    "estimated_commission",
    "estimated_cash_change",
    "current_cash",
    "current_shares",
)
RISK_DECISION_COLUMNS = (
    "order_sequence_number",
    "approved",
    "reason_codes",
    "evaluated_session",
    "projected_cash",
    "projected_shares",
    "projected_market_value",
    "projected_equity",
)
FILL_COLUMNS = (
    "order_sequence_number",
    "symbol",
    "side",
    "quantity",
    "signal_session",
    "execution_session",
    "reference_open",
    "execution_price",
    "reference_notional",
    "execution_notional",
    "commission",
    "slippage_cost",
    "total_transaction_cost",
    "cash_change",
    "shares_before",
    "shares_after",
    "cash_before",
    "cash_after",
    "risk_approved",
)
PORTFOLIO_COLUMNS = (
    "session",
    "signal_session",
    "target_position",
    "cash",
    "shares",
    "close_price",
    "market_value",
    "equity",
    "daily_return",
    "drawdown",
)
EXECUTION_PRICE_COLUMNS = (
    "execution_session",
    "reference_open",
    "close_price",
)


@dataclass(frozen=True, slots=True)
class BacktestIssue:
    code: str
    message: str


class BacktestError(ValueError):
    """Base class for Phase 6 backtesting failures."""

    def __init__(self, issues: list[BacktestIssue]) -> None:
        self.issues = tuple(issues)
        super().__init__("; ".join(f"{issue.code}: {issue.message}" for issue in self.issues))

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(issue.code for issue in self.issues)


class BacktestInputError(BacktestError):
    """Raised for malformed backtest inputs."""


class BacktestAccountingError(BacktestError):
    """Raised when portfolio accounting identities fail."""


class BacktestMetricError(BacktestError):
    """Raised when backtest metric invariants fail."""


def backtest_issue(code: str, message: str) -> BacktestIssue:
    return BacktestIssue(code=code, message=message)


def raise_backtest_error(
    error_type: type[BacktestError],
    code: str,
    message: str,
) -> NoReturn:
    raise error_type([backtest_issue(code, message)])


def require_aware_utc(
    value: object,
    *,
    field_name: str,
    error_type: type[BacktestError] = BacktestInputError,
) -> datetime:
    if not isinstance(value, datetime):
        raise_backtest_error(
            error_type,
            f"invalid_{field_name}",
            f"{field_name} must be a timezone-aware datetime.",
        )
    if value.tzinfo is None or value.utcoffset() is None:
        raise_backtest_error(
            error_type,
            f"invalid_{field_name}",
            f"{field_name} must be timezone-aware.",
        )
    return value.astimezone(UTC)


def require_plain_date(
    value: object,
    *,
    field_name: str,
    error_type: type[BacktestError] = BacktestInputError,
) -> date:
    if isinstance(value, datetime) or not isinstance(value, date):
        raise_backtest_error(
            error_type,
            f"invalid_{field_name}",
            f"{field_name} must be a plain datetime.date.",
        )
    return value


def require_decimal(
    value: object,
    *,
    field_name: str,
    allow_negative: bool = False,
    strictly_positive: bool = False,
    error_type: type[BacktestError] = BacktestInputError,
) -> Decimal:
    if isinstance(value, bool):
        raise_backtest_error(
            error_type,
            f"invalid_{field_name}",
            f"{field_name} must be a finite Decimal-compatible value.",
        )
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value).strip())
    except (InvalidOperation, ValueError, AttributeError):
        raise_backtest_error(
            error_type,
            f"invalid_{field_name}",
            f"{field_name} must be a finite Decimal-compatible value.",
        )
    if not parsed.is_finite():
        raise_backtest_error(
            error_type,
            f"non_finite_{field_name}",
            f"{field_name} must be finite.",
        )
    if strictly_positive and parsed <= 0:
        raise_backtest_error(
            error_type,
            f"non_positive_{field_name}",
            f"{field_name} must be greater than zero.",
        )
    if not allow_negative and not strictly_positive and parsed < 0:
        raise_backtest_error(
            error_type,
            f"negative_{field_name}",
            f"{field_name} must not be negative.",
        )
    return parsed


def require_int(
    value: object,
    *,
    field_name: str,
    minimum: int = 0,
    error_type: type[BacktestError] = BacktestInputError,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise_backtest_error(
            error_type,
            f"invalid_{field_name}",
            f"{field_name} must be an integer greater than or equal to {minimum}.",
        )
    return value


def require_finite_float(
    value: object,
    *,
    field_name: str,
    error_type: type[BacktestError] = BacktestMetricError,
) -> float:
    if isinstance(value, bool):
        raise_backtest_error(
            error_type,
            f"invalid_{field_name}",
            f"{field_name} must be a finite float.",
        )
    try:
        parsed = float(cast(Any, value))
    except (TypeError, ValueError, OverflowError):
        raise_backtest_error(
            error_type,
            f"invalid_{field_name}",
            f"{field_name} must be a finite float.",
        )
    if not math.isfinite(parsed):
        raise_backtest_error(
            error_type,
            f"non_finite_{field_name}",
            f"{field_name} must be finite.",
        )
    return parsed


def validate_backtest_checksum(
    value: object,
    *,
    field_name: str,
    error_type: type[BacktestError] = BacktestInputError,
) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise_backtest_error(
            error_type,
            f"invalid_{field_name}",
            f"{field_name} must be a lowercase SHA-256 hex digest.",
        )
    return value


def validate_feature_columns(
    value: object,
    *,
    error_type: type[BacktestError] = BacktestInputError,
) -> tuple[str, ...]:
    if not isinstance(value, tuple) or value != FEATURE_COLUMNS:
        raise_backtest_error(
            error_type,
            "invalid_feature_columns",
            "feature_columns must match the ordered Phase 4 feature schema.",
        )
    return value


def validate_model_name(
    value: object,
    *,
    error_type: type[BacktestError] = BacktestInputError,
) -> str:
    if not isinstance(value, str) or value not in MODEL_NAMES:
        raise_backtest_error(
            error_type,
            "invalid_selected_model_name",
            "selected_model_name must be an approved Phase 5 model name.",
        )
    return value


def calculate_execution_price_checksum(frame: pd.DataFrame) -> str:
    if not isinstance(frame, pd.DataFrame):
        raise_backtest_error(
            BacktestInputError,
            "invalid_execution_price_data",
            "execution price data must be a pandas DataFrame.",
        )
    if tuple(frame.columns) != EXECUTION_PRICE_COLUMNS:
        raise_backtest_error(
            BacktestInputError,
            "invalid_execution_price_columns",
            "execution price columns must match the approved Phase 6 schema.",
        )
    lines = ["execution_session,reference_open,close_price"]
    for row in frame.itertuples(index=False):
        session = require_plain_date(
            row.execution_session,
            field_name="execution_session",
            error_type=BacktestInputError,
        )
        reference_open = require_finite_float(
            row.reference_open,
            field_name="reference_open",
            error_type=BacktestInputError,
        )
        close_price = require_finite_float(
            row.close_price,
            field_name="close_price",
            error_type=BacktestInputError,
        )
        lines.append(
            f"{session.isoformat()},{reference_open:.17g},{close_price:.17g}",
        )
    payload = "\n".join(lines).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def decimal_to_float(value: Decimal) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise_backtest_error(
            BacktestAccountingError,
            "non_finite_float_conversion",
            "Decimal value cannot be represented as a finite float.",
        )
    return parsed


@dataclass(frozen=True, slots=True)
class BacktestCostAssumptions:
    commission_bps_per_side: Decimal
    slippage_bps_per_side: Decimal

    def __post_init__(self) -> None:
        commission = require_decimal(
            self.commission_bps_per_side,
            field_name="commission_bps_per_side",
        )
        slippage = require_decimal(
            self.slippage_bps_per_side,
            field_name="slippage_bps_per_side",
        )
        object.__setattr__(self, "commission_bps_per_side", commission)
        object.__setattr__(self, "slippage_bps_per_side", slippage)

    @property
    def commission_rate(self) -> Decimal:
        return self.commission_bps_per_side / Decimal("10000")

    @property
    def slippage_rate(self) -> Decimal:
        return self.slippage_bps_per_side / Decimal("10000")


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    cost_assumptions: BacktestCostAssumptions
    initial_cash: Decimal = INITIAL_SIMULATED_CASH

    def __post_init__(self) -> None:
        if not isinstance(cast(object, self.cost_assumptions), BacktestCostAssumptions):
            raise_backtest_error(
                BacktestInputError,
                "invalid_cost_assumptions",
                "cost_assumptions must be a BacktestCostAssumptions.",
            )
        costs = BacktestCostAssumptions(
            commission_bps_per_side=self.cost_assumptions.commission_bps_per_side,
            slippage_bps_per_side=self.cost_assumptions.slippage_bps_per_side,
        )
        initial_cash = require_decimal(
            self.initial_cash,
            field_name="initial_cash",
            strictly_positive=True,
        )
        if initial_cash != INITIAL_SIMULATED_CASH:
            raise_backtest_error(
                BacktestInputError,
                "invalid_initial_cash",
                "initial_cash must equal Decimal('10000') in Version 1.",
            )
        object.__setattr__(self, "cost_assumptions", costs)
        object.__setattr__(self, "initial_cash", initial_cash)


@dataclass(frozen=True, slots=True)
class ExecutionPriceSet:
    data: pd.DataFrame
    source_market_data_checksum: str
    source_schema_version: str
    first_execution_session: date
    last_execution_session: date
    row_count: int
    created_at: datetime
    execution_price_checksum: str

    def __post_init__(self) -> None:
        source_checksum = validate_backtest_checksum(
            self.source_market_data_checksum,
            field_name="source_market_data_checksum",
        )
        if self.source_schema_version != MARKET_DATA_SCHEMA_VERSION:
            raise_backtest_error(
                BacktestInputError,
                "invalid_source_schema_version",
                "execution-price source schema must match the approved market-data schema.",
            )
        first_execution = require_plain_date(
            self.first_execution_session,
            field_name="first_execution_session",
        )
        last_execution = require_plain_date(
            self.last_execution_session,
            field_name="last_execution_session",
        )
        if first_execution > last_execution:
            raise_backtest_error(
                BacktestInputError,
                "invalid_execution_session_bounds",
                "execution-price session bounds must be internally ordered.",
            )
        row_count = require_int(self.row_count, field_name="row_count", minimum=1)
        created_at = require_aware_utc(self.created_at, field_name="created_at")
        checksum = validate_backtest_checksum(
            self.execution_price_checksum,
            field_name="execution_price_checksum",
            error_type=BacktestAccountingError,
        )
        data = _validate_audit_frame(
            self.data,
            columns=EXECUTION_PRICE_COLUMNS,
            frame_name="execution_prices",
        )
        if len(data) != row_count:
            raise_backtest_error(
                BacktestAccountingError,
                "execution_price_row_count_mismatch",
                "execution-price row_count must match the frame length.",
            )
        _require_column_dtype(
            data,
            frame_name="execution_prices",
            columns=("reference_open", "close_price"),
            dtype_name="float64",
        )
        sessions = [
            require_plain_date(
                value,
                field_name="execution_session",
                error_type=BacktestAccountingError,
            )
            for value in data["execution_session"]
        ]
        if sessions != sorted(sessions) or len(sessions) != len(set(sessions)):
            raise_backtest_error(
                BacktestAccountingError,
                "unordered_execution_price_sessions",
                "execution-price sessions must be unique and strictly increasing.",
            )
        if sessions[0] != first_execution or sessions[-1] != last_execution:
            raise_backtest_error(
                BacktestAccountingError,
                "execution_price_session_bounds_mismatch",
                "execution-price session metadata must match the frame.",
            )
        for row in data.itertuples(index=False):
            reference_open = require_finite_float(
                row.reference_open,
                field_name="execution_price_reference_open",
                error_type=BacktestAccountingError,
            )
            close_price = require_finite_float(
                row.close_price,
                field_name="execution_price_close_price",
                error_type=BacktestAccountingError,
            )
            if reference_open <= 0.0 or close_price <= 0.0:
                raise_backtest_error(
                    BacktestAccountingError,
                    "non_positive_execution_price",
                    "execution-price opens and closes must be positive.",
                )
        recomputed_checksum = calculate_execution_price_checksum(data)
        if checksum != recomputed_checksum:
            raise_backtest_error(
                BacktestAccountingError,
                "execution_price_checksum_mismatch",
                "execution-price checksum must match the canonical price frame.",
            )
        object.__setattr__(self, "data", data)
        object.__setattr__(self, "source_market_data_checksum", source_checksum)
        object.__setattr__(self, "first_execution_session", first_execution)
        object.__setattr__(self, "last_execution_session", last_execution)
        object.__setattr__(self, "row_count", row_count)
        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "execution_price_checksum", checksum)


@dataclass(frozen=True, slots=True)
class FillRecord:
    order_sequence_number: int
    symbol: str
    side: str
    quantity: int
    signal_session: date
    execution_session: date
    reference_open: Decimal
    execution_price: Decimal
    reference_notional: Decimal
    execution_notional: Decimal
    commission: Decimal
    slippage_cost: Decimal
    total_transaction_cost: Decimal
    cash_change: Decimal
    shares_before: int
    shares_after: int
    cash_before: Decimal
    cash_after: Decimal
    risk_approved: bool

    def __post_init__(self) -> None:
        order_sequence_number = require_int(
            self.order_sequence_number,
            field_name="order_sequence_number",
            minimum=1,
            error_type=BacktestAccountingError,
        )
        if not isinstance(self.symbol, str) or self.symbol != SUPPORTED_SYMBOL:
            raise_backtest_error(
                BacktestAccountingError,
                "invalid_fill_symbol",
                "fill symbol must be SPY.",
            )
        symbol = self.symbol
        if not isinstance(self.side, str) or self.side not in (BUY_SIDE, SELL_SIDE):
            raise_backtest_error(
                BacktestAccountingError,
                "invalid_fill_side",
                "fill side must be buy or sell.",
            )
        side = self.side
        quantity = require_int(
            self.quantity,
            field_name="quantity",
            minimum=1,
            error_type=BacktestAccountingError,
        )
        signal_session = require_plain_date(
            self.signal_session,
            field_name="signal_session",
            error_type=BacktestAccountingError,
        )
        execution_session = require_plain_date(
            self.execution_session,
            field_name="execution_session",
            error_type=BacktestAccountingError,
        )
        if execution_session <= signal_session:
            raise_backtest_error(
                BacktestAccountingError,
                "same_candle_fill",
                "fill execution must occur after signal generation.",
            )
        reference_open = require_decimal(
            self.reference_open,
            field_name="reference_open",
            strictly_positive=True,
            error_type=BacktestAccountingError,
        )
        execution_price = require_decimal(
            self.execution_price,
            field_name="execution_price",
            strictly_positive=True,
            error_type=BacktestAccountingError,
        )
        reference_notional = require_decimal(
            self.reference_notional,
            field_name="reference_notional",
            error_type=BacktestAccountingError,
        )
        execution_notional = require_decimal(
            self.execution_notional,
            field_name="execution_notional",
            error_type=BacktestAccountingError,
        )
        commission = require_decimal(
            self.commission,
            field_name="commission",
            error_type=BacktestAccountingError,
        )
        slippage_cost = require_decimal(
            self.slippage_cost,
            field_name="slippage_cost",
            error_type=BacktestAccountingError,
        )
        total_transaction_cost = require_decimal(
            self.total_transaction_cost,
            field_name="total_transaction_cost",
            error_type=BacktestAccountingError,
        )
        cash_change = require_decimal(
            self.cash_change,
            field_name="cash_change",
            allow_negative=True,
            error_type=BacktestAccountingError,
        )
        shares_before = require_int(
            self.shares_before,
            field_name="shares_before",
            error_type=BacktestAccountingError,
        )
        shares_after = require_int(
            self.shares_after,
            field_name="shares_after",
            error_type=BacktestAccountingError,
        )
        cash_before = require_decimal(
            self.cash_before,
            field_name="cash_before",
            error_type=BacktestAccountingError,
        )
        cash_after = require_decimal(
            self.cash_after,
            field_name="cash_after",
            error_type=BacktestAccountingError,
        )
        if self.risk_approved is not True:
            raise_backtest_error(
                BacktestAccountingError,
                "fill_without_approved_risk_decision",
                "fills may only be created from approved risk decisions.",
            )
        if reference_notional != Decimal(quantity) * reference_open:
            raise_backtest_error(
                BacktestAccountingError,
                "fill_reference_notional_mismatch",
                "reference_notional must equal quantity times reference_open.",
            )
        if execution_notional != Decimal(quantity) * execution_price:
            raise_backtest_error(
                BacktestAccountingError,
                "fill_execution_notional_mismatch",
                "execution_notional must equal quantity times execution_price.",
            )
        if slippage_cost != abs(execution_price - reference_open) * Decimal(quantity):
            raise_backtest_error(
                BacktestAccountingError,
                "fill_slippage_cost_mismatch",
                "slippage_cost must match execution price slippage.",
            )
        if total_transaction_cost != commission + slippage_cost:
            raise_backtest_error(
                BacktestAccountingError,
                "fill_total_cost_mismatch",
                "total_transaction_cost must equal commission plus slippage_cost.",
            )
        if side == BUY_SIDE:
            if shares_after != shares_before + quantity:
                raise_backtest_error(
                    BacktestAccountingError,
                    "buy_share_transition_mismatch",
                    "buy fills must increase shares by quantity.",
                )
            if cash_change != -(execution_notional + commission):
                raise_backtest_error(
                    BacktestAccountingError,
                    "buy_cash_change_mismatch",
                    "buy cash_change must equal negative execution notional plus commission.",
                )
        else:
            if shares_after != shares_before - quantity:
                raise_backtest_error(
                    BacktestAccountingError,
                    "sell_share_transition_mismatch",
                    "sell fills must decrease shares by quantity.",
                )
            if cash_change != execution_notional - commission:
                raise_backtest_error(
                    BacktestAccountingError,
                    "sell_cash_change_mismatch",
                    "sell cash_change must equal execution notional less commission.",
                )
        if cash_after != cash_before + cash_change:
            raise_backtest_error(
                BacktestAccountingError,
                "fill_cash_after_mismatch",
                "cash_after must equal cash_before plus cash_change.",
            )
        if cash_after < 0:
            raise_backtest_error(
                BacktestAccountingError,
                "negative_cash_after_fill",
                "cash after a fill must remain non-negative.",
            )
        object.__setattr__(self, "order_sequence_number", order_sequence_number)
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "side", side)
        object.__setattr__(self, "quantity", quantity)
        object.__setattr__(self, "signal_session", signal_session)
        object.__setattr__(self, "execution_session", execution_session)
        object.__setattr__(self, "reference_open", reference_open)
        object.__setattr__(self, "execution_price", execution_price)
        object.__setattr__(self, "reference_notional", reference_notional)
        object.__setattr__(self, "execution_notional", execution_notional)
        object.__setattr__(self, "commission", commission)
        object.__setattr__(self, "slippage_cost", slippage_cost)
        object.__setattr__(self, "total_transaction_cost", total_transaction_cost)
        object.__setattr__(self, "cash_change", cash_change)
        object.__setattr__(self, "shares_before", shares_before)
        object.__setattr__(self, "shares_after", shares_after)
        object.__setattr__(self, "cash_before", cash_before)
        object.__setattr__(self, "cash_after", cash_after)
        object.__setattr__(self, "risk_approved", True)


@dataclass(frozen=True, slots=True)
class BacktestMetrics:
    session_count: int
    initial_cash: float
    final_cash: float
    final_shares: int
    final_market_value: float
    final_equity: float
    total_return: float
    maximum_drawdown: float
    total_reference_notional: float
    total_execution_notional: float
    total_commission: float
    total_slippage_cost: float
    total_transaction_cost: float
    turnover_ratio: float
    exposure_fraction: float
    proposed_order_count: int
    approved_order_count: int
    rejected_order_count: int
    fill_count: int
    buy_fill_count: int
    sell_fill_count: int

    def __post_init__(self) -> None:
        count_fields = (
            "session_count",
            "final_shares",
            "proposed_order_count",
            "approved_order_count",
            "rejected_order_count",
            "fill_count",
            "buy_fill_count",
            "sell_fill_count",
        )
        counts = {
            field_name: require_int(
                getattr(self, field_name),
                field_name=field_name,
                error_type=BacktestMetricError,
            )
            for field_name in count_fields
        }
        if counts["session_count"] <= 0:
            raise_backtest_error(
                BacktestMetricError,
                "invalid_session_count",
                "session_count must be positive.",
            )
        float_fields = (
            "initial_cash",
            "final_cash",
            "final_market_value",
            "final_equity",
            "total_return",
            "maximum_drawdown",
            "total_reference_notional",
            "total_execution_notional",
            "total_commission",
            "total_slippage_cost",
            "total_transaction_cost",
            "turnover_ratio",
            "exposure_fraction",
        )
        floats = {
            field_name: require_finite_float(getattr(self, field_name), field_name=field_name)
            for field_name in float_fields
        }
        for non_negative_field in (
            "initial_cash",
            "final_cash",
            "final_market_value",
            "final_equity",
            "maximum_drawdown",
            "total_reference_notional",
            "total_execution_notional",
            "total_commission",
            "total_slippage_cost",
            "total_transaction_cost",
            "turnover_ratio",
            "exposure_fraction",
        ):
            if floats[non_negative_field] < 0:
                raise_backtest_error(
                    BacktestMetricError,
                    f"negative_{non_negative_field}",
                    f"{non_negative_field} must not be negative.",
                )
        if not math.isclose(
            floats["initial_cash"],
            float(INITIAL_SIMULATED_CASH),
            rel_tol=0.0,
            abs_tol=0.0,
        ):
            raise_backtest_error(
                BacktestMetricError,
                "invalid_initial_cash",
                "initial_cash must equal 10000 for Phase 6 metrics.",
            )
        if not math.isclose(
            floats["final_equity"],
            floats["final_cash"] + floats["final_market_value"],
            rel_tol=1e-12,
            abs_tol=1e-9,
        ):
            raise_backtest_error(
                BacktestMetricError,
                "final_equity_identity_mismatch",
                "final_equity must equal final_cash plus final_market_value.",
            )
        expected_total_return = floats["final_equity"] / floats["initial_cash"] - 1.0
        if not math.isclose(
            floats["total_return"],
            expected_total_return,
            rel_tol=1e-12,
            abs_tol=1e-12,
        ):
            raise_backtest_error(
                BacktestMetricError,
                "total_return_mismatch",
                "total_return must equal final_equity divided by initial_cash minus one.",
            )
        if not math.isclose(
            floats["total_transaction_cost"],
            floats["total_commission"] + floats["total_slippage_cost"],
            rel_tol=1e-12,
            abs_tol=1e-9,
        ):
            raise_backtest_error(
                BacktestMetricError,
                "transaction_cost_identity_mismatch",
                "total_transaction_cost must equal total_commission plus total_slippage_cost.",
            )
        if floats["maximum_drawdown"] > 1.0 or floats["exposure_fraction"] > 1.0:
            raise_backtest_error(
                BacktestMetricError,
                "bounded_metric_out_of_range",
                "maximum_drawdown and exposure_fraction must lie within [0, 1].",
            )
        if (
            counts["approved_order_count"] + counts["rejected_order_count"]
            != counts["proposed_order_count"]
        ):
            raise_backtest_error(
                BacktestMetricError,
                "order_count_identity_mismatch",
                "approved plus rejected orders must equal proposed orders.",
            )
        if counts["fill_count"] != counts["approved_order_count"]:
            raise_backtest_error(
                BacktestMetricError,
                "fill_count_identity_mismatch",
                "fill_count must equal approved_order_count.",
            )
        if counts["buy_fill_count"] + counts["sell_fill_count"] != counts["fill_count"]:
            raise_backtest_error(
                BacktestMetricError,
                "fill_side_count_identity_mismatch",
                "buy plus sell fill counts must equal fill_count.",
            )


def _validate_audit_frame(
    frame: object,
    *,
    columns: tuple[str, ...],
    frame_name: str,
) -> pd.DataFrame:
    if not isinstance(frame, pd.DataFrame):
        raise_backtest_error(
            BacktestAccountingError,
            f"invalid_{frame_name}",
            f"{frame_name} must be a pandas DataFrame.",
        )
    data = frame.copy(deep=True)
    if tuple(data.columns) != columns:
        raise_backtest_error(
            BacktestAccountingError,
            f"invalid_{frame_name}_columns",
            f"{frame_name} columns must be {list(columns)}.",
        )
    return data


def _require_column_dtype(
    frame: pd.DataFrame,
    *,
    frame_name: str,
    columns: tuple[str, ...],
    dtype_name: str,
) -> None:
    for column in columns:
        if str(frame[column].dtype) != dtype_name:
            raise_backtest_error(
                BacktestAccountingError,
                f"invalid_{frame_name}_{column}_dtype",
                f"{frame_name}.{column} must use canonical {dtype_name} dtype.",
            )


def _audit_decimal(value: object, *, field_name: str, strictly_positive: bool = False) -> Decimal:
    parsed = require_finite_float(
        value,
        field_name=field_name,
        error_type=BacktestAccountingError,
    )
    decimal = Decimal(str(parsed))
    if strictly_positive and decimal <= 0:
        raise_backtest_error(
            BacktestAccountingError,
            f"non_positive_{field_name}",
            f"{field_name} must be greater than zero.",
        )
    if not strictly_positive and decimal < 0:
        raise_backtest_error(
            BacktestAccountingError,
            f"negative_{field_name}",
            f"{field_name} must not be negative.",
        )
    return decimal


def _audit_signed_decimal(value: object, *, field_name: str) -> Decimal:
    parsed = require_finite_float(
        value,
        field_name=field_name,
        error_type=BacktestAccountingError,
    )
    return Decimal(str(parsed))


def _assert_float_matches_decimal(
    observed: object,
    expected: Decimal,
    *,
    field_name: str,
) -> None:
    observed_float = require_finite_float(
        observed,
        field_name=field_name,
        error_type=BacktestAccountingError,
    )
    expected_float = decimal_to_float(expected)
    if not math.isclose(observed_float, expected_float, rel_tol=1e-12, abs_tol=1e-8):
        raise_backtest_error(
            BacktestAccountingError,
            f"{field_name}_mismatch",
            f"{field_name} does not match independently replayed accounting.",
        )


def _assert_decimal_matches_decimal(
    observed: Decimal,
    expected: Decimal,
    *,
    field_name: str,
) -> None:
    if not math.isclose(
        decimal_to_float(observed),
        decimal_to_float(expected),
        rel_tol=1e-12,
        abs_tol=1e-8,
    ):
        raise_backtest_error(
            BacktestAccountingError,
            f"{field_name}_mismatch",
            f"{field_name} does not match independently replayed accounting.",
        )


def _reconstruct_proposed_order(row: Any) -> ProposedOrder:
    try:
        return ProposedOrder(
            sequence_number=row.sequence_number,
            symbol=row.symbol,
            side=row.side,
            quantity=row.quantity,
            signal_session=row.signal_session,
            execution_session=row.execution_session,
            target_position=row.target_position,
            reference_open=_audit_decimal(
                row.reference_open,
                field_name="order_reference_open",
                strictly_positive=True,
            ),
            estimated_execution_price=_audit_decimal(
                row.estimated_execution_price,
                field_name="order_estimated_execution_price",
                strictly_positive=True,
            ),
            estimated_commission=_audit_decimal(
                row.estimated_commission,
                field_name="order_estimated_commission",
            ),
            estimated_cash_change=_audit_signed_decimal(
                row.estimated_cash_change,
                field_name="order_estimated_cash_change",
            ),
            current_cash=_audit_decimal(row.current_cash, field_name="order_current_cash"),
            current_shares=row.current_shares,
        )
    except RiskError:
        raise_backtest_error(
            BacktestAccountingError,
            "invalid_proposed_order_row",
            "proposed order rows must reconstruct to valid ProposedOrder objects.",
        )


def _reconstruct_risk_decision(row: Any) -> RiskDecision:
    try:
        projected_cash = _audit_decimal(row.projected_cash, field_name="decision_projected_cash")
        projected_market_value = _audit_decimal(
            row.projected_market_value,
            field_name="decision_projected_market_value",
        )
        observed_projected_equity = _audit_decimal(
            row.projected_equity,
            field_name="decision_projected_equity",
        )
        expected_projected_equity = projected_cash + projected_market_value
        _assert_decimal_matches_decimal(
            observed_projected_equity,
            expected_projected_equity,
            field_name="decision_projected_equity",
        )
        return RiskDecision(
            order_sequence_number=row.order_sequence_number,
            approved=row.approved,
            reason_codes=row.reason_codes,
            evaluated_session=row.evaluated_session,
            projected_cash=projected_cash,
            projected_shares=row.projected_shares,
            projected_market_value=projected_market_value,
            projected_equity=expected_projected_equity,
        )
    except RiskError:
        raise_backtest_error(
            BacktestAccountingError,
            "invalid_risk_decision_row",
            "risk decision rows must reconstruct to valid RiskDecision objects.",
        )


def _reconstruct_fill(row: Any) -> FillRecord:
    try:
        quantity = require_int(
            row.quantity,
            field_name="fill_quantity",
            minimum=1,
            error_type=BacktestAccountingError,
        )
        reference_open = _audit_decimal(
            row.reference_open,
            field_name="fill_reference_open",
            strictly_positive=True,
        )
        execution_price = _audit_decimal(
            row.execution_price,
            field_name="fill_execution_price",
            strictly_positive=True,
        )
        observed_reference_notional = _audit_decimal(
            row.reference_notional,
            field_name="fill_reference_notional",
        )
        observed_execution_notional = _audit_decimal(
            row.execution_notional,
            field_name="fill_execution_notional",
        )
        commission = _audit_decimal(row.commission, field_name="fill_commission")
        observed_slippage_cost = _audit_decimal(
            row.slippage_cost,
            field_name="fill_slippage_cost",
        )
        observed_total_cost = _audit_decimal(
            row.total_transaction_cost,
            field_name="fill_total_transaction_cost",
        )
        observed_cash_change = _audit_signed_decimal(
            row.cash_change,
            field_name="fill_cash_change",
        )
        cash_before = _audit_decimal(row.cash_before, field_name="fill_cash_before")
        observed_cash_after = _audit_decimal(row.cash_after, field_name="fill_cash_after")
        quantity_decimal = Decimal(quantity)
        expected_reference_notional = quantity_decimal * reference_open
        expected_execution_notional = quantity_decimal * execution_price
        expected_slippage_cost = abs(execution_price - reference_open) * quantity_decimal
        expected_total_cost = commission + expected_slippage_cost
        if not isinstance(row.side, str) or row.side not in (BUY_SIDE, SELL_SIDE):
            raise_backtest_error(
                BacktestAccountingError,
                "invalid_fill_side",
                "fill side must be buy or sell.",
            )
        expected_cash_change = (
            -(expected_execution_notional + commission)
            if row.side == BUY_SIDE
            else expected_execution_notional - commission
        )
        expected_cash_after = cash_before + expected_cash_change
        _assert_decimal_matches_decimal(
            observed_reference_notional,
            expected_reference_notional,
            field_name="fill_reference_notional",
        )
        _assert_decimal_matches_decimal(
            observed_execution_notional,
            expected_execution_notional,
            field_name="fill_execution_notional",
        )
        _assert_decimal_matches_decimal(
            observed_slippage_cost,
            expected_slippage_cost,
            field_name="fill_slippage_cost",
        )
        _assert_decimal_matches_decimal(
            observed_total_cost,
            expected_total_cost,
            field_name="fill_total_transaction_cost",
        )
        _assert_decimal_matches_decimal(
            observed_cash_change,
            expected_cash_change,
            field_name="fill_cash_change",
        )
        _assert_decimal_matches_decimal(
            observed_cash_after,
            expected_cash_after,
            field_name="fill_cash_after",
        )
        return FillRecord(
            order_sequence_number=row.order_sequence_number,
            symbol=row.symbol,
            side=row.side,
            quantity=quantity,
            signal_session=row.signal_session,
            execution_session=row.execution_session,
            reference_open=reference_open,
            execution_price=execution_price,
            reference_notional=expected_reference_notional,
            execution_notional=expected_execution_notional,
            commission=commission,
            slippage_cost=expected_slippage_cost,
            total_transaction_cost=expected_total_cost,
            cash_change=expected_cash_change,
            shares_before=row.shares_before,
            shares_after=row.shares_after,
            cash_before=cash_before,
            cash_after=expected_cash_after,
            risk_approved=row.risk_approved,
        )
    except BacktestError:
        raise_backtest_error(
            BacktestAccountingError,
            "invalid_fill_row",
            "fill rows must reconstruct to valid FillRecord objects.",
        )


def _validate_portfolio_frame(frame: pd.DataFrame, *, initial_cash: float) -> None:
    if frame.empty:
        raise_backtest_error(
            BacktestAccountingError,
            "empty_portfolio_frame",
            "portfolio frame must contain at least one execution-session row.",
        )
    sessions = [require_plain_date(value, field_name="session") for value in frame["session"]]
    if sessions != sorted(sessions) or len(sessions) != len(set(sessions)):
        raise_backtest_error(
            BacktestAccountingError,
            "unordered_portfolio_sessions",
            "portfolio sessions must be unique and strictly increasing.",
        )
    running_peak = initial_cash
    previous_equity = initial_cash
    for index, row in frame.iterrows():
        cash = require_finite_float(
            row["cash"],
            field_name="cash",
            error_type=BacktestAccountingError,
        )
        shares = require_int(row["shares"], field_name="shares", error_type=BacktestAccountingError)
        close_price = require_finite_float(
            row["close_price"],
            field_name="close_price",
            error_type=BacktestAccountingError,
        )
        market_value = require_finite_float(
            row["market_value"],
            field_name="market_value",
            error_type=BacktestAccountingError,
        )
        equity = require_finite_float(
            row["equity"],
            field_name="equity",
            error_type=BacktestAccountingError,
        )
        daily_return = require_finite_float(
            row["daily_return"],
            field_name="daily_return",
            error_type=BacktestAccountingError,
        )
        drawdown = require_finite_float(
            row["drawdown"],
            field_name="drawdown",
            error_type=BacktestAccountingError,
        )
        if cash < 0 or shares < 0 or close_price <= 0:
            raise_backtest_error(
                BacktestAccountingError,
                "invalid_portfolio_values",
                "portfolio cash, shares, and close prices must be valid.",
            )
        if not math.isclose(market_value, shares * close_price, rel_tol=1e-12, abs_tol=1e-9):
            raise_backtest_error(
                BacktestAccountingError,
                "portfolio_market_value_mismatch",
                "market_value must equal shares times close_price.",
            )
        if not math.isclose(equity, cash + market_value, rel_tol=1e-12, abs_tol=1e-9):
            raise_backtest_error(
                BacktestAccountingError,
                "portfolio_equity_mismatch",
                "equity must equal cash plus market_value.",
            )
        expected_return = equity / previous_equity - 1.0
        if index == 0:
            expected_return = equity / initial_cash - 1.0
        if not math.isclose(daily_return, expected_return, rel_tol=1e-12, abs_tol=1e-9):
            raise_backtest_error(
                BacktestAccountingError,
                "portfolio_daily_return_mismatch",
                "daily_return must match equity change.",
            )
        running_peak = max(running_peak, equity)
        expected_drawdown = equity / running_peak - 1.0
        if drawdown > 0 or not math.isclose(
            drawdown,
            expected_drawdown,
            rel_tol=1e-12,
            abs_tol=1e-9,
        ):
            raise_backtest_error(
                BacktestAccountingError,
                "portfolio_drawdown_mismatch",
                "drawdown must match equity divided by running peak minus one.",
            )
        previous_equity = equity


def _validate_audit_dtypes(
    *,
    proposed_orders: pd.DataFrame,
    risk_decisions: pd.DataFrame,
    fills: pd.DataFrame,
    portfolio: pd.DataFrame,
) -> None:
    _require_column_dtype(
        proposed_orders,
        frame_name="proposed_orders",
        columns=("sequence_number", "quantity", "target_position", "current_shares"),
        dtype_name="int64",
    )
    _require_column_dtype(
        proposed_orders,
        frame_name="proposed_orders",
        columns=(
            "reference_open",
            "estimated_execution_price",
            "estimated_commission",
            "estimated_cash_change",
            "current_cash",
        ),
        dtype_name="float64",
    )
    _require_column_dtype(
        risk_decisions,
        frame_name="risk_decisions",
        columns=("order_sequence_number", "projected_shares"),
        dtype_name="int64",
    )
    _require_column_dtype(
        risk_decisions,
        frame_name="risk_decisions",
        columns=("projected_cash", "projected_market_value", "projected_equity"),
        dtype_name="float64",
    )
    _require_column_dtype(
        risk_decisions,
        frame_name="risk_decisions",
        columns=("approved",),
        dtype_name="bool",
    )
    _require_column_dtype(
        fills,
        frame_name="fills",
        columns=("order_sequence_number", "quantity", "shares_before", "shares_after"),
        dtype_name="int64",
    )
    _require_column_dtype(
        fills,
        frame_name="fills",
        columns=(
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
        dtype_name="float64",
    )
    _require_column_dtype(fills, frame_name="fills", columns=("risk_approved",), dtype_name="bool")
    _require_column_dtype(
        portfolio,
        frame_name="portfolio",
        columns=("target_position", "shares"),
        dtype_name="int64",
    )
    _require_column_dtype(
        portfolio,
        frame_name="portfolio",
        columns=(
            "cash",
            "close_price",
            "market_value",
            "equity",
            "daily_return",
            "drawdown",
        ),
        dtype_name="float64",
    )


def _require_source_column_dtype(
    frame: pd.DataFrame,
    *,
    column: str,
    dtype_name: str,
) -> None:
    if str(frame[column].dtype) != dtype_name:
        raise_backtest_error(
            BacktestInputError,
            f"invalid_source_market_{column}_dtype",
            f"source_market_data.{column} must use canonical {dtype_name} dtype.",
        )


def _validate_source_market_metadata(
    metadata: MarketDataMetadata,
    *,
    source_data: pd.DataFrame,
) -> str:
    if not isinstance(metadata.symbol, str) or metadata.symbol != MARKET_SYMBOL:
        raise_backtest_error(
            BacktestInputError,
            "source_market_data_mismatch",
            "source_market_data must contain canonical SPY market data.",
        )
    if not isinstance(metadata.timeframe, str) or metadata.timeframe != MARKET_TIMEFRAME:
        raise_backtest_error(
            BacktestInputError,
            "source_market_data_mismatch",
            "source_market_data must contain daily market data.",
        )
    if (
        not isinstance(metadata.adjustment_policy, str)
        or metadata.adjustment_policy != ADJUSTMENT_POLICY
    ):
        raise_backtest_error(
            BacktestInputError,
            "source_market_data_mismatch",
            "source_market_data must use the approved adjustment policy.",
        )
    if not isinstance(metadata.schema_version, str) or metadata.schema_version != (
        MARKET_DATA_SCHEMA_VERSION
    ):
        raise_backtest_error(
            BacktestInputError,
            "invalid_source_schema_version",
            "source_market_data must use the approved market-data schema.",
        )
    row_count = metadata.row_count
    if isinstance(row_count, bool) or not isinstance(row_count, int) or row_count <= 0:
        raise_backtest_error(
            BacktestInputError,
            "invalid_source_market_row_count",
            "source_market_data row_count metadata must be a positive integer.",
        )
    if row_count != len(source_data):
        raise_backtest_error(
            BacktestInputError,
            "source_market_metadata_row_count_mismatch",
            "source_market_data row_count metadata must match the retained frame.",
        )
    first_session = require_plain_date(
        metadata.first_session,
        field_name="source_market_first_session",
        error_type=BacktestInputError,
    )
    last_session = require_plain_date(
        metadata.last_session,
        field_name="source_market_last_session",
        error_type=BacktestInputError,
    )
    data_first_session = require_plain_date(
        source_data.iloc[0]["session"],
        field_name="source_market_first_data_session",
        error_type=BacktestInputError,
    )
    data_last_session = require_plain_date(
        source_data.iloc[-1]["session"],
        field_name="source_market_last_data_session",
        error_type=BacktestInputError,
    )
    if first_session != data_first_session:
        raise_backtest_error(
            BacktestInputError,
            "source_market_metadata_first_session_mismatch",
            "source_market_data first_session metadata must match the first data row.",
        )
    if last_session != data_last_session:
        raise_backtest_error(
            BacktestInputError,
            "source_market_metadata_last_session_mismatch",
            "source_market_data last_session metadata must match the final data row.",
        )
    return validate_backtest_checksum(
        metadata.dataset_checksum,
        field_name="source_market_data_checksum",
    )


def _validate_source_market_frame(source_data: object) -> pd.DataFrame:
    if not isinstance(source_data, pd.DataFrame):
        raise_backtest_error(
            BacktestInputError,
            "invalid_source_market_data_frame",
            "source_market_data.data must be a pandas DataFrame.",
        )
    data = source_data.copy(deep=True)
    if tuple(data.columns) != CANONICAL_COLUMNS:
        raise_backtest_error(
            BacktestInputError,
            "invalid_source_market_columns",
            "source_market_data must use canonical market-data columns.",
        )
    if data.empty:
        raise_backtest_error(
            BacktestInputError,
            "empty_source_market_data",
            "source_market_data must contain at least one row.",
        )
    _require_source_column_dtype(data, column="open", dtype_name="float64")
    _require_source_column_dtype(data, column="high", dtype_name="float64")
    _require_source_column_dtype(data, column="low", dtype_name="float64")
    _require_source_column_dtype(data, column="close", dtype_name="float64")
    _require_source_column_dtype(data, column="volume", dtype_name="int64")
    sessions = [
        require_plain_date(
            session,
            field_name="source_market_session",
            error_type=BacktestInputError,
        )
        for session in data["session"]
    ]
    if len(sessions) != len(set(sessions)):
        raise_backtest_error(
            BacktestInputError,
            "duplicate_source_market_sessions",
            "source_market_data sessions must be unique.",
        )
    if any(current <= previous for previous, current in pairwise(sessions)):
        raise_backtest_error(
            BacktestInputError,
            "unordered_source_market_sessions",
            "source_market_data sessions must be strictly increasing.",
        )
    for column in ("open", "high", "low", "close"):
        for value in data[column]:
            parsed = require_finite_float(
                value,
                field_name=f"source_market_{column}",
                error_type=BacktestInputError,
            )
            if parsed <= 0.0:
                raise_backtest_error(
                    BacktestInputError,
                    "non_positive_source_market_ohlc",
                    "source_market_data OHLC values must be positive.",
                )
    for value in data["volume"]:
        volume = require_int(
            value,
            field_name="source_market_volume",
            error_type=BacktestInputError,
        )
        if volume < 0:
            raise_backtest_error(
                BacktestInputError,
                "negative_source_market_volume",
                "source_market_data volume must be non-negative.",
            )
    return data


def _reconstruct_source_market_data(value: object) -> MarketDataBatch:
    if not isinstance(value, MarketDataBatch):
        raise_backtest_error(
            BacktestInputError,
            "invalid_source_market_data",
            "source_market_data must be a MarketDataBatch.",
        )
    try:
        source_data = _validate_source_market_frame(value.data)
        metadata_copy = value.metadata.model_copy(deep=True)
        metadata_copy = MarketDataMetadata(**metadata_copy.model_dump())
    except BacktestError:
        raise
    except (ValidationError, AttributeError, TypeError, ValueError):
        raise_backtest_error(
            BacktestInputError,
            "source_market_data_mismatch",
            "source_market_data metadata must pass Phase 3 validation.",
        )
    metadata_checksum = _validate_source_market_metadata(
        metadata_copy,
        source_data=source_data,
    )
    try:
        recomputed_checksum = compute_market_data_checksum(source_data)
    except (AttributeError, TypeError, ValueError, OverflowError):
        raise_backtest_error(
            BacktestInputError,
            "source_market_checksum_generation_failed",
            "source_market_data checksum could not be recomputed from retained data.",
        )
    if recomputed_checksum != metadata_checksum:
        raise_backtest_error(
            BacktestInputError,
            "source_market_checksum_recomputation_mismatch",
            "source_market_data metadata checksum must match the recomputed Phase 3 checksum.",
        )
    try:
        batch = MarketDataBatch(
            data=source_data.copy(deep=True),
            metadata=metadata_copy.model_copy(deep=True),
        )
    except (ValidationError, AttributeError, TypeError, ValueError):
        raise_backtest_error(
            BacktestInputError,
            "source_market_data_mismatch",
            "source_market_data must pass Phase 3 checksum revalidation.",
        )
    return batch


def _derive_execution_prices_from_source(
    *,
    source_market_data: MarketDataBatch,
    signals: StrategySignalSet,
    created_at: datetime,
) -> ExecutionPriceSet:
    records: list[dict[str, object]] = []
    source_data = source_market_data.data
    for execution_session in signals.data["execution_session"]:
        session = require_plain_date(
            execution_session,
            field_name="execution_session",
            error_type=BacktestAccountingError,
        )
        matches = source_data.loc[source_data["session"] == session]
        if len(matches) != 1:
            raise_backtest_error(
                BacktestAccountingError,
                "execution_session_missing_from_source",
                "each strategy execution session must exist exactly once in source market data.",
            )
        source_row = matches.iloc[0]
        records.append(
            {
                "execution_session": session,
                "reference_open": require_finite_float(
                    source_row["open"],
                    field_name="source_market_open",
                    error_type=BacktestAccountingError,
                ),
                "close_price": require_finite_float(
                    source_row["close"],
                    field_name="source_market_close",
                    error_type=BacktestAccountingError,
                ),
            }
        )
    frame = pd.DataFrame.from_records(records, columns=list(EXECUTION_PRICE_COLUMNS))
    frame["reference_open"] = frame["reference_open"].astype("float64")
    frame["close_price"] = frame["close_price"].astype("float64")
    checksum = calculate_execution_price_checksum(frame)
    return ExecutionPriceSet(
        data=frame,
        source_market_data_checksum=source_market_data.metadata.dataset_checksum,
        source_schema_version=source_market_data.metadata.schema_version,
        first_execution_session=signals.first_execution_session,
        last_execution_session=signals.last_execution_session,
        row_count=signals.row_count,
        created_at=created_at,
        execution_price_checksum=checksum,
    )


def _validate_execution_prices_match_source(
    *,
    stored: ExecutionPriceSet,
    expected: ExecutionPriceSet,
) -> None:
    if stored.execution_price_checksum != calculate_execution_price_checksum(stored.data):
        raise_backtest_error(
            BacktestAccountingError,
            "execution_price_checksum_mismatch",
            "stored execution-price checksum must match the stored frame.",
        )
    if stored.data["execution_session"].to_list() != expected.data["execution_session"].to_list():
        raise_backtest_error(
            BacktestAccountingError,
            "execution_price_signal_session_mismatch",
            "stored execution sessions must match source-derived execution sessions.",
        )
    for stored_row, expected_row in zip(
        stored.data.itertuples(index=False),
        expected.data.itertuples(index=False),
        strict=True,
    ):
        if not math.isclose(
            float(stored_row.reference_open),
            float(expected_row.reference_open),
            rel_tol=0.0,
            abs_tol=0.0,
        ):
            raise_backtest_error(
                BacktestAccountingError,
                "execution_price_source_open_mismatch",
                "stored execution reference_open must match source market open.",
            )
        if not math.isclose(
            float(stored_row.close_price),
            float(expected_row.close_price),
            rel_tol=0.0,
            abs_tol=0.0,
        ):
            raise_backtest_error(
                BacktestAccountingError,
                "execution_price_source_close_mismatch",
                "stored execution close_price must match source market close.",
            )
    if stored.execution_price_checksum != expected.execution_price_checksum:
        raise_backtest_error(
            BacktestAccountingError,
            "execution_price_checksum_mismatch",
            "stored execution-price checksum must match the source-derived price frame.",
        )


def _assert_decision_matches_recomputed(
    recorded: RiskDecision,
    recomputed: RiskDecision,
) -> None:
    if recorded.approved is not recomputed.approved:
        raise_backtest_error(
            BacktestAccountingError,
            "risk_approval_mismatch",
            "recorded risk approval must match independent risk recomputation.",
        )
    if recorded.reason_codes != recomputed.reason_codes:
        raise_backtest_error(
            BacktestAccountingError,
            "risk_reason_codes_mismatch",
            "recorded risk reason codes must match independent risk recomputation.",
        )
    if recorded.evaluated_session != recomputed.evaluated_session:
        raise_backtest_error(
            BacktestAccountingError,
            "risk_evaluated_session_mismatch",
            "recorded risk evaluated_session must match independent risk recomputation.",
        )
    if recorded.projected_shares != recomputed.projected_shares:
        raise_backtest_error(
            BacktestAccountingError,
            "risk_projected_shares_mismatch",
            "recorded risk projected_shares must match independent risk recomputation.",
        )
    _assert_decimal_matches_decimal(
        recorded.projected_cash,
        recomputed.projected_cash,
        field_name="risk_projected_cash",
    )
    _assert_decimal_matches_decimal(
        recorded.projected_market_value,
        recomputed.projected_market_value,
        field_name="risk_projected_market_value",
    )
    _assert_decimal_matches_decimal(
        recorded.projected_equity,
        recomputed.projected_equity,
        field_name="risk_projected_equity",
    )


def _validate_order_estimate(order: ProposedOrder, costs: BacktestCostAssumptions) -> None:
    from spy_market_agent.backtesting.costs import estimate_order_cost

    estimate = estimate_order_cost(
        side=order.side,
        quantity=order.quantity,
        reference_open=order.reference_open,
        cost_assumptions=costs,
    )
    _assert_decimal_matches_decimal(
        order.estimated_execution_price,
        estimate.execution_price,
        field_name="order_estimated_execution_price",
    )
    _assert_decimal_matches_decimal(
        order.estimated_commission,
        estimate.commission,
        field_name="order_estimated_commission",
    )
    _assert_decimal_matches_decimal(
        order.estimated_cash_change,
        estimate.cash_change,
        field_name="order_estimated_cash_change",
    )


def _canonical_order_for_replay(
    *,
    order: ProposedOrder,
    cash: Decimal,
    shares: int,
    reference_open: Decimal,
    costs: BacktestCostAssumptions,
) -> ProposedOrder:
    from spy_market_agent.backtesting.costs import estimate_order_cost

    estimate = estimate_order_cost(
        side=order.side,
        quantity=order.quantity,
        reference_open=reference_open,
        cost_assumptions=costs,
    )
    return ProposedOrder(
        sequence_number=order.sequence_number,
        symbol=order.symbol,
        side=order.side,
        quantity=order.quantity,
        signal_session=order.signal_session,
        execution_session=order.execution_session,
        target_position=order.target_position,
        reference_open=reference_open,
        estimated_execution_price=estimate.execution_price,
        estimated_commission=estimate.commission,
        estimated_cash_change=estimate.cash_change,
        current_cash=cash,
        current_shares=shares,
    )


def _validate_fill_matches_order_and_cost(
    *,
    fill: FillRecord,
    order: ProposedOrder,
    cash_before: Decimal,
    shares_before: int,
    decision: RiskDecision,
    costs: BacktestCostAssumptions,
) -> None:
    from spy_market_agent.backtesting.costs import estimate_order_cost

    if (
        fill.symbol != order.symbol
        or fill.side != order.side
        or fill.quantity != order.quantity
        or fill.signal_session != order.signal_session
        or fill.execution_session != order.execution_session
    ):
        raise_backtest_error(
            BacktestAccountingError,
            "fill_order_identity_mismatch",
            "fill identity fields must match the approved proposed order.",
        )
    _assert_decimal_matches_decimal(
        fill.reference_open,
        order.reference_open,
        field_name="fill_reference_open",
    )
    estimate = estimate_order_cost(
        side=order.side,
        quantity=order.quantity,
        reference_open=order.reference_open,
        cost_assumptions=costs,
    )
    _assert_decimal_matches_decimal(
        fill.execution_price,
        estimate.execution_price,
        field_name="fill_execution_price",
    )
    _assert_decimal_matches_decimal(
        fill.reference_notional,
        estimate.reference_notional,
        field_name="fill_reference_notional",
    )
    _assert_decimal_matches_decimal(
        fill.execution_notional,
        estimate.execution_notional,
        field_name="fill_execution_notional",
    )
    _assert_decimal_matches_decimal(
        fill.commission,
        estimate.commission,
        field_name="fill_commission",
    )
    _assert_decimal_matches_decimal(
        fill.slippage_cost,
        estimate.slippage_cost,
        field_name="fill_slippage_cost",
    )
    _assert_decimal_matches_decimal(
        fill.total_transaction_cost,
        estimate.total_transaction_cost,
        field_name="fill_total_transaction_cost",
    )
    _assert_decimal_matches_decimal(
        fill.cash_change,
        estimate.cash_change,
        field_name="fill_cash_change",
    )
    _assert_decimal_matches_decimal(fill.cash_before, cash_before, field_name="fill_cash_before")
    if fill.shares_before != shares_before:
        raise_backtest_error(
            BacktestAccountingError,
            "fill_shares_before_mismatch",
            "fill shares_before must match replayed pre-fill shares.",
        )
    expected_shares_after = (
        shares_before + order.quantity if order.side == BUY_SIDE else shares_before - order.quantity
    )
    if fill.shares_after != expected_shares_after:
        raise_backtest_error(
            BacktestAccountingError,
            "fill_shares_after_mismatch",
            "fill shares_after must match replayed post-fill shares.",
        )
    _assert_decimal_matches_decimal(
        fill.cash_after,
        cash_before + estimate.cash_change,
        field_name="fill_cash_after",
    )
    _assert_decimal_matches_decimal(
        fill.cash_after,
        decision.projected_cash,
        field_name="fill_decision_projected_cash",
    )
    if fill.shares_after != decision.projected_shares:
        raise_backtest_error(
            BacktestAccountingError,
            "fill_decision_projected_shares_mismatch",
            "fill shares_after must match the approved risk projection.",
        )


def _validate_backtest_audit_replay(
    *,
    signals: StrategySignalSet,
    execution_prices: ExecutionPriceSet,
    proposed_orders: pd.DataFrame,
    risk_decisions: pd.DataFrame,
    fills: pd.DataFrame,
    portfolio: pd.DataFrame,
    backtest_config: BacktestConfig,
    risk_config: RiskConfig,
) -> None:
    from spy_market_agent.backtesting.costs import maximum_affordable_buy_quantity
    from spy_market_agent.risk.rules import evaluate_order_risk

    _validate_audit_dtypes(
        proposed_orders=proposed_orders,
        risk_decisions=risk_decisions,
        fills=fills,
        portfolio=portfolio,
    )
    if (
        execution_prices.data["execution_session"].to_list()
        != signals.data["execution_session"].to_list()
    ):
        raise_backtest_error(
            BacktestAccountingError,
            "execution_price_signal_session_mismatch",
            "execution-price sessions must exactly match strategy execution sessions.",
        )

    proposed_by_sequence: dict[int, ProposedOrder] = {}
    proposed_by_signal: dict[tuple[date, date], list[ProposedOrder]] = {}
    sequence_numbers: list[int] = []
    for row in proposed_orders.itertuples(index=False):
        order = _reconstruct_proposed_order(row)
        sequence_numbers.append(order.sequence_number)
        if order.sequence_number in proposed_by_sequence:
            raise_backtest_error(
                BacktestAccountingError,
                "duplicate_proposed_orders",
                "each proposed order must have a unique sequence number.",
            )
        proposed_by_sequence[order.sequence_number] = order
        proposed_by_signal.setdefault((order.signal_session, order.execution_session), []).append(
            order
        )
        _validate_order_estimate(order, backtest_config.cost_assumptions)
    if sequence_numbers != list(range(1, len(sequence_numbers) + 1)):
        raise_backtest_error(
            BacktestAccountingError,
            "non_contiguous_order_sequences",
            "proposed order sequence numbers must be contiguous and start at 1.",
        )

    decisions_by_sequence: dict[int, RiskDecision] = {}
    for row in risk_decisions.itertuples(index=False):
        decision = _reconstruct_risk_decision(row)
        if decision.order_sequence_number in decisions_by_sequence:
            raise_backtest_error(
                BacktestAccountingError,
                "duplicate_risk_decisions",
                "each proposed order may receive only one risk decision.",
            )
        decisions_by_sequence[decision.order_sequence_number] = decision
    if set(decisions_by_sequence) != set(proposed_by_sequence):
        raise_backtest_error(
            BacktestAccountingError,
            "risk_decision_order_mismatch",
            "every proposed order must receive exactly one risk decision.",
        )

    fills_by_sequence: dict[int, FillRecord] = {}
    for row in fills.itertuples(index=False):
        fill = _reconstruct_fill(row)
        if fill.order_sequence_number in fills_by_sequence:
            raise_backtest_error(
                BacktestAccountingError,
                "duplicate_fills",
                "each approved order may create only one fill.",
            )
        fills_by_sequence[fill.order_sequence_number] = fill

    signal_targets = {
        (row.signal_session, row.execution_session): int(row.target_position)
        for row in signals.data.itertuples(index=False)
    }
    for key, orders in proposed_by_signal.items():
        if key not in signal_targets:
            raise_backtest_error(
                BacktestAccountingError,
                "order_signal_mismatch",
                "each proposed order must match exactly one strategy signal row.",
            )
        if len(orders) != 1:
            raise_backtest_error(
                BacktestAccountingError,
                "multiple_orders_for_signal",
                "each strategy signal may produce at most one proposed order.",
            )
        if orders[0].target_position != signal_targets[key]:
            raise_backtest_error(
                BacktestAccountingError,
                "order_target_signal_mismatch",
                "proposed order target_position must match the strategy signal target.",
            )

    cash = backtest_config.initial_cash
    shares = 0
    previous_equity = backtest_config.initial_cash
    running_peak = backtest_config.initial_cash
    consumed_fills: set[int] = set()

    for index, signal in enumerate(signals.data.itertuples(index=False)):
        signal_session = require_plain_date(
            signal.signal_session,
            field_name="signal_session",
            error_type=BacktestAccountingError,
        )
        execution_session = require_plain_date(
            signal.execution_session,
            field_name="execution_session",
            error_type=BacktestAccountingError,
        )
        target_position = require_int(
            signal.target_position,
            field_name="signal_target_position",
            error_type=BacktestAccountingError,
        )
        execution_price_row = execution_prices.data.iloc[index]
        if execution_price_row["execution_session"] != execution_session:
            raise_backtest_error(
                BacktestAccountingError,
                "execution_price_session_mismatch",
                "execution-price rows must align with strategy execution sessions.",
            )
        reference_open = _audit_decimal(
            execution_price_row["reference_open"],
            field_name="execution_price_reference_open",
            strictly_positive=True,
        )
        close_price = _audit_decimal(
            execution_price_row["close_price"],
            field_name="execution_price_close_price",
            strictly_positive=True,
        )
        portfolio_row = portfolio.iloc[index]
        if portfolio_row["session"] != execution_session:
            raise_backtest_error(
                BacktestAccountingError,
                "portfolio_execution_session_mismatch",
                "portfolio session must match strategy execution session during replay.",
            )
        if portfolio_row["signal_session"] != signal_session:
            raise_backtest_error(
                BacktestAccountingError,
                "portfolio_signal_session_mismatch",
                "portfolio signal_session must match strategy signal during replay.",
            )
        if int(portfolio_row["target_position"]) != target_position:
            raise_backtest_error(
                BacktestAccountingError,
                "portfolio_target_signal_mismatch",
                "portfolio target_position must match the strategy signal target.",
            )

        key = (signal_session, execution_session)
        orders_for_signal = proposed_by_signal.get(key, [])
        matched_order = orders_for_signal[0] if orders_for_signal else None
        if target_position == 1:
            if shares > 0 and matched_order is not None:
                raise_backtest_error(
                    BacktestAccountingError,
                    "repeated_long_rebalance_order",
                    "repeated long targets must not rebalance or pyramid.",
                )
            if shares == 0 and matched_order is None:
                raise_backtest_error(
                    BacktestAccountingError,
                    "missing_long_entry_order",
                    "cash-to-long target transitions must propose one buy order.",
                )
        else:
            if shares == 0 and matched_order is not None:
                raise_backtest_error(
                    BacktestAccountingError,
                    "repeated_cash_order",
                    "repeated cash targets must not generate orders.",
                )
            if shares > 0 and matched_order is None:
                raise_backtest_error(
                    BacktestAccountingError,
                    "missing_cash_exit_order",
                    "long-to-cash target transitions must propose one sell order.",
                )

        if matched_order is not None:
            _assert_decimal_matches_decimal(
                matched_order.reference_open,
                reference_open,
                field_name="order_market_reference_open",
            )
            _assert_decimal_matches_decimal(
                matched_order.current_cash,
                cash,
                field_name="order_current_cash",
            )
            if matched_order.current_shares != shares:
                raise_backtest_error(
                    BacktestAccountingError,
                    "order_current_shares_mismatch",
                    "proposed order current_shares must match replayed pre-order shares.",
                )
            if target_position == 1:
                if matched_order.side != BUY_SIDE:
                    raise_backtest_error(
                        BacktestAccountingError,
                        "invalid_long_entry_order_side",
                        "long targets from cash must propose a buy order.",
                    )
                expected_quantity = maximum_affordable_buy_quantity(
                    available_cash=cash,
                    reference_open=reference_open,
                    cost_assumptions=backtest_config.cost_assumptions,
                )
                if expected_quantity <= 0:
                    expected_quantity = 1
                if matched_order.quantity != expected_quantity:
                    raise_backtest_error(
                        BacktestAccountingError,
                        "buy_quantity_sizing_mismatch",
                        "buy quantity must match the deterministic whole-share sizing policy.",
                    )
            elif matched_order.side != SELL_SIDE or matched_order.quantity != shares:
                raise_backtest_error(
                    BacktestAccountingError,
                    "full_exit_order_mismatch",
                    "cash targets while long must sell the entire current position.",
                )

            replay_order = _canonical_order_for_replay(
                order=matched_order,
                cash=cash,
                shares=shares,
                reference_open=reference_open,
                costs=backtest_config.cost_assumptions,
            )
            decision = decisions_by_sequence[matched_order.sequence_number]
            open_state = PortfolioState(
                session=execution_session,
                cash=cash,
                shares=shares,
                reference_price=replay_order.reference_open,
                market_value=Decimal(shares) * replay_order.reference_open,
                equity=cash + Decimal(shares) * replay_order.reference_open,
            )
            recomputed_decision = evaluate_order_risk(
                replay_order,
                open_state,
                risk_config=risk_config,
                cost_assumptions=backtest_config.cost_assumptions,
            )
            _assert_decision_matches_recomputed(decision, recomputed_decision)
            matched_fill = fills_by_sequence.get(matched_order.sequence_number)
            if decision.approved:
                if matched_fill is None:
                    raise_backtest_error(
                        BacktestAccountingError,
                        "missing_approved_fill",
                        "every approved risk decision must produce exactly one fill.",
                    )
                _validate_fill_matches_order_and_cost(
                    fill=matched_fill,
                    order=replay_order,
                    cash_before=cash,
                    shares_before=shares,
                    decision=decision,
                    costs=backtest_config.cost_assumptions,
                )
                cash = matched_fill.cash_after
                shares = matched_fill.shares_after
                consumed_fills.add(matched_fill.order_sequence_number)
            else:
                if matched_fill is not None:
                    raise_backtest_error(
                        BacktestAccountingError,
                        "fill_for_rejected_decision",
                        "rejected risk decisions must not create fills.",
                    )

        portfolio_close = _audit_decimal(
            portfolio_row["close_price"],
            field_name="portfolio_close_price",
            strictly_positive=True,
        )
        _assert_decimal_matches_decimal(
            portfolio_close,
            close_price,
            field_name="portfolio_market_close_price",
        )
        expected_market_value = Decimal(shares) * close_price
        expected_equity = cash + expected_market_value
        expected_daily_return = expected_equity / backtest_config.initial_cash - Decimal("1")
        if index > 0:
            expected_daily_return = expected_equity / previous_equity - Decimal("1")
        running_peak = max(running_peak, expected_equity)
        expected_drawdown = expected_equity / running_peak - Decimal("1")
        _assert_float_matches_decimal(portfolio_row["cash"], cash, field_name="portfolio_cash")
        if int(portfolio_row["shares"]) != shares:
            raise_backtest_error(
                BacktestAccountingError,
                "portfolio_shares_replay_mismatch",
                "portfolio shares must match replayed post-execution shares.",
            )
        _assert_float_matches_decimal(
            portfolio_row["market_value"],
            expected_market_value,
            field_name="portfolio_market_value",
        )
        _assert_float_matches_decimal(
            portfolio_row["equity"],
            expected_equity,
            field_name="portfolio_equity",
        )
        _assert_float_matches_decimal(
            portfolio_row["daily_return"],
            expected_daily_return,
            field_name="portfolio_daily_return",
        )
        _assert_float_matches_decimal(
            portfolio_row["drawdown"],
            expected_drawdown,
            field_name="portfolio_drawdown",
        )
        previous_equity = expected_equity

    if set(fills_by_sequence) != consumed_fills:
        raise_backtest_error(
            BacktestAccountingError,
            "unconsumed_fill_records",
            "all fill records must be consumed by chronological audit replay.",
        )


@dataclass(frozen=True, slots=True)
class BacktestResult:
    strategy_signal_set: StrategySignalSet
    source_market_data: MarketDataBatch
    execution_prices: ExecutionPriceSet
    proposed_orders: pd.DataFrame
    risk_decisions: pd.DataFrame
    fills: pd.DataFrame
    portfolio: pd.DataFrame
    metrics: BacktestMetrics
    backtest_config: BacktestConfig
    risk_config: RiskConfig
    selected_model_name: str
    source_market_data_checksum: str
    source_schema_version: str
    feature_schema_version: str
    label_schema_version: str
    model_schema_version: str
    strategy_schema_version: str
    risk_schema_version: str
    backtest_schema_version: str
    feature_columns: tuple[str, ...]
    split_spec: ChronologicalSplitSpec
    strategy_threshold: float
    first_signal_session: date
    last_signal_session: date
    first_execution_session: date
    last_execution_session: date
    initial_cash: Decimal
    cost_assumptions: BacktestCostAssumptions
    sklearn_version: str
    created_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(cast(object, self.strategy_signal_set), StrategySignalSet):
            raise_backtest_error(
                BacktestInputError,
                "invalid_strategy_signal_set",
                "strategy_signal_set must be a StrategySignalSet.",
            )
        signals = StrategySignalSet(
            data=self.strategy_signal_set.data,
            selected_model_name=self.strategy_signal_set.selected_model_name,
            strategy_threshold=self.strategy_signal_set.strategy_threshold,
            source_market_data_checksum=self.strategy_signal_set.source_market_data_checksum,
            source_schema_version=self.strategy_signal_set.source_schema_version,
            feature_schema_version=self.strategy_signal_set.feature_schema_version,
            label_schema_version=self.strategy_signal_set.label_schema_version,
            model_schema_version=self.strategy_signal_set.model_schema_version,
            strategy_schema_version=self.strategy_signal_set.strategy_schema_version,
            feature_columns=self.strategy_signal_set.feature_columns,
            split_spec=self.strategy_signal_set.split_spec,
            market_sessions=self.strategy_signal_set.market_sessions,
            first_signal_session=self.strategy_signal_set.first_signal_session,
            last_signal_session=self.strategy_signal_set.last_signal_session,
            first_execution_session=self.strategy_signal_set.first_execution_session,
            last_execution_session=self.strategy_signal_set.last_execution_session,
            row_count=self.strategy_signal_set.row_count,
            sklearn_version=self.strategy_signal_set.sklearn_version,
            created_at=self.strategy_signal_set.created_at,
        )
        source_market_data = _reconstruct_source_market_data(self.source_market_data)
        if not isinstance(cast(object, self.execution_prices), ExecutionPriceSet):
            raise_backtest_error(
                BacktestInputError,
                "invalid_execution_prices",
                "execution_prices must be an ExecutionPriceSet.",
            )
        execution_prices = ExecutionPriceSet(
            data=self.execution_prices.data,
            source_market_data_checksum=self.execution_prices.source_market_data_checksum,
            source_schema_version=self.execution_prices.source_schema_version,
            first_execution_session=self.execution_prices.first_execution_session,
            last_execution_session=self.execution_prices.last_execution_session,
            row_count=self.execution_prices.row_count,
            created_at=self.execution_prices.created_at,
            execution_price_checksum=self.execution_prices.execution_price_checksum,
        )
        if (
            execution_prices.data["execution_session"].to_list()
            != signals.data["execution_session"].to_list()
        ):
            raise_backtest_error(
                BacktestAccountingError,
                "execution_price_signal_session_mismatch",
                "execution-price sessions must match strategy execution sessions.",
            )
        if (
            execution_prices.first_execution_session != signals.first_execution_session
            or execution_prices.last_execution_session != signals.last_execution_session
        ):
            raise_backtest_error(
                BacktestAccountingError,
                "execution_price_bounds_mismatch",
                "execution-price bounds must match strategy execution bounds.",
            )
        if not isinstance(cast(object, self.backtest_config), BacktestConfig):
            raise_backtest_error(
                BacktestInputError,
                "invalid_backtest_config",
                "backtest_config must be a BacktestConfig.",
            )
        backtest_config = BacktestConfig(
            cost_assumptions=self.backtest_config.cost_assumptions,
            initial_cash=self.backtest_config.initial_cash,
        )
        if not isinstance(cast(object, self.risk_config), RiskConfig):
            raise_backtest_error(
                BacktestInputError,
                "invalid_risk_config",
                "risk_config must be a RiskConfig.",
            )
        risk_config = RiskConfig(
            supported_symbol=self.risk_config.supported_symbol,
            allow_short_selling=self.risk_config.allow_short_selling,
            allow_leverage=self.risk_config.allow_leverage,
            allow_fractional_shares=self.risk_config.allow_fractional_shares,
            maximum_position_weight=self.risk_config.maximum_position_weight,
        )
        selected_model_name = validate_model_name(self.selected_model_name)
        if selected_model_name != signals.selected_model_name:
            raise_backtest_error(
                BacktestInputError,
                "selected_model_mismatch",
                "backtest selected model must match strategy lineage.",
            )
        source_checksum = validate_backtest_checksum(
            self.source_market_data_checksum,
            field_name="source_market_data_checksum",
        )
        lineage_values = (
            ("source_schema_version", self.source_schema_version, MARKET_DATA_SCHEMA_VERSION),
            ("feature_schema_version", self.feature_schema_version, FEATURE_SCHEMA_VERSION),
            ("label_schema_version", self.label_schema_version, LABEL_SCHEMA_VERSION),
            ("model_schema_version", self.model_schema_version, MODEL_SCHEMA_VERSION),
            ("strategy_schema_version", self.strategy_schema_version, STRATEGY_SCHEMA_VERSION),
            ("risk_schema_version", self.risk_schema_version, RISK_SCHEMA_VERSION),
            ("backtest_schema_version", self.backtest_schema_version, BACKTEST_SCHEMA_VERSION),
        )
        for field_name, value, expected in lineage_values:
            if value != expected:
                raise_backtest_error(
                    BacktestInputError,
                    f"invalid_{field_name}",
                    f"{field_name} must be {expected!r}.",
                )
        if source_checksum != signals.source_market_data_checksum:
            raise_backtest_error(
                BacktestInputError,
                "strategy_source_checksum_mismatch",
                "backtest source checksum must match strategy lineage.",
            )
        if source_market_data.metadata.schema_version != self.source_schema_version:
            raise_backtest_error(
                BacktestInputError,
                "source_market_data_mismatch",
                "source_market_data schema must match backtest lineage.",
            )
        source_derived_execution_prices = _derive_execution_prices_from_source(
            source_market_data=source_market_data,
            signals=signals,
            created_at=signals.created_at,
        )
        _validate_execution_prices_match_source(
            stored=execution_prices,
            expected=source_derived_execution_prices,
        )
        if source_market_data.metadata.dataset_checksum != source_checksum:
            raise_backtest_error(
                BacktestInputError,
                "source_market_checksum_mismatch",
                "source_market_data checksum must match backtest lineage.",
            )
        if source_market_data.metadata.dataset_checksum != signals.source_market_data_checksum:
            raise_backtest_error(
                BacktestInputError,
                "source_market_checksum_mismatch",
                "source_market_data checksum must match strategy lineage.",
            )
        if source_market_data.metadata.dataset_checksum != (
            execution_prices.source_market_data_checksum
        ):
            raise_backtest_error(
                BacktestInputError,
                "source_market_checksum_mismatch",
                "source_market_data checksum must match execution-price lineage.",
            )
        if execution_prices.source_market_data_checksum != source_checksum:
            raise_backtest_error(
                BacktestInputError,
                "execution_price_source_checksum_mismatch",
                "execution-price source checksum must match backtest lineage.",
            )
        if signals.source_schema_version != self.source_schema_version:
            raise_backtest_error(
                BacktestInputError,
                "strategy_source_schema_mismatch",
                "backtest source schema must match strategy lineage.",
            )
        if execution_prices.source_schema_version != self.source_schema_version:
            raise_backtest_error(
                BacktestInputError,
                "execution_price_source_schema_mismatch",
                "execution-price source schema must match backtest lineage.",
            )
        for field_name in (
            "feature_schema_version",
            "label_schema_version",
            "model_schema_version",
            "strategy_schema_version",
        ):
            if getattr(signals, field_name) != getattr(self, field_name):
                raise_backtest_error(
                    BacktestInputError,
                    f"strategy_{field_name}_mismatch",
                    f"backtest {field_name} must match strategy lineage.",
                )
        feature_columns = validate_feature_columns(self.feature_columns)
        if signals.feature_columns != feature_columns:
            raise_backtest_error(
                BacktestInputError,
                "strategy_feature_columns_mismatch",
                "feature columns must match strategy lineage.",
            )
        if not isinstance(cast(object, self.split_spec), ChronologicalSplitSpec):
            raise_backtest_error(
                BacktestInputError,
                "invalid_split_spec",
                "split_spec must be a ChronologicalSplitSpec.",
            )
        if self.split_spec != signals.split_spec:
            raise_backtest_error(
                BacktestInputError,
                "strategy_split_spec_mismatch",
                "split spec must match strategy lineage.",
            )
        threshold = require_finite_float(
            self.strategy_threshold,
            field_name="strategy_threshold",
            error_type=BacktestInputError,
        )
        if (
            threshold != STRATEGY_LONG_PROBABILITY_THRESHOLD
            or threshold != signals.strategy_threshold
        ):
            raise_backtest_error(
                BacktestInputError,
                "strategy_threshold_mismatch",
                "strategy threshold must match the fixed Phase 6 threshold.",
            )
        first_signal = require_plain_date(
            self.first_signal_session,
            field_name="first_signal_session",
        )
        last_signal = require_plain_date(
            self.last_signal_session,
            field_name="last_signal_session",
        )
        first_execution = require_plain_date(
            self.first_execution_session,
            field_name="first_execution_session",
        )
        last_execution = require_plain_date(
            self.last_execution_session,
            field_name="last_execution_session",
        )
        if (
            first_signal != signals.first_signal_session
            or last_signal != signals.last_signal_session
            or first_execution != signals.first_execution_session
            or last_execution != signals.last_execution_session
        ):
            raise_backtest_error(
                BacktestInputError,
                "strategy_session_bounds_mismatch",
                "backtest session bounds must match strategy signal bounds.",
            )
        initial_cash = require_decimal(
            self.initial_cash,
            field_name="initial_cash",
            strictly_positive=True,
        )
        if initial_cash != INITIAL_SIMULATED_CASH or initial_cash != backtest_config.initial_cash:
            raise_backtest_error(
                BacktestInputError,
                "initial_cash_mismatch",
                "initial cash must match the fixed Phase 6 backtest configuration.",
            )
        if not isinstance(cast(object, self.cost_assumptions), BacktestCostAssumptions):
            raise_backtest_error(
                BacktestInputError,
                "invalid_cost_assumptions",
                "cost_assumptions must be a BacktestCostAssumptions.",
            )
        costs = BacktestCostAssumptions(
            commission_bps_per_side=self.cost_assumptions.commission_bps_per_side,
            slippage_bps_per_side=self.cost_assumptions.slippage_bps_per_side,
        )
        if costs != backtest_config.cost_assumptions:
            raise_backtest_error(
                BacktestInputError,
                "cost_assumptions_mismatch",
                "result cost assumptions must match backtest configuration.",
            )
        if (
            self.sklearn_version != signals.sklearn_version
            or self.sklearn_version != sklearn.__version__
        ):
            raise_backtest_error(
                BacktestInputError,
                "sklearn_version_mismatch",
                "backtest sklearn lineage must match strategy lineage and runtime.",
            )
        created_at = require_aware_utc(self.created_at, field_name="created_at")
        if created_at != signals.created_at:
            raise_backtest_error(
                BacktestInputError,
                "created_at_strategy_mismatch",
                "backtest created_at must match the strategy signal-set timestamp.",
            )
        if created_at != execution_prices.created_at:
            raise_backtest_error(
                BacktestInputError,
                "created_at_execution_prices_mismatch",
                "backtest created_at must match the execution-price timestamp.",
            )
        proposed_orders = _validate_audit_frame(
            self.proposed_orders,
            columns=PROPOSED_ORDER_COLUMNS,
            frame_name="proposed_orders",
        )
        risk_decisions = _validate_audit_frame(
            self.risk_decisions,
            columns=RISK_DECISION_COLUMNS,
            frame_name="risk_decisions",
        )
        fills = _validate_audit_frame(self.fills, columns=FILL_COLUMNS, frame_name="fills")
        portfolio = _validate_audit_frame(
            self.portfolio,
            columns=PORTFOLIO_COLUMNS,
            frame_name="portfolio",
        )
        _validate_portfolio_frame(portfolio, initial_cash=decimal_to_float(initial_cash))
        _validate_backtest_audit_replay(
            signals=signals,
            execution_prices=execution_prices,
            proposed_orders=proposed_orders,
            risk_decisions=risk_decisions,
            fills=fills,
            portfolio=portfolio,
            backtest_config=backtest_config,
            risk_config=risk_config,
        )
        if len(portfolio) != signals.row_count:
            raise_backtest_error(
                BacktestAccountingError,
                "portfolio_signal_row_count_mismatch",
                "portfolio must contain one row per execution session.",
            )
        if portfolio["session"].to_list() != signals.data["execution_session"].to_list():
            raise_backtest_error(
                BacktestAccountingError,
                "portfolio_execution_session_mismatch",
                "portfolio sessions must match strategy execution sessions.",
            )
        if portfolio["session"].to_list() != execution_prices.data["execution_session"].to_list():
            raise_backtest_error(
                BacktestAccountingError,
                "portfolio_execution_price_session_mismatch",
                "portfolio sessions must match execution-price sessions.",
            )
        if portfolio["signal_session"].to_list() != signals.data["signal_session"].to_list():
            raise_backtest_error(
                BacktestAccountingError,
                "portfolio_signal_session_mismatch",
                "portfolio signal sessions must match strategy signals.",
            )
        proposed_sequences = proposed_orders["sequence_number"].to_list()
        decision_sequences = risk_decisions["order_sequence_number"].to_list()
        fill_sequences = fills["order_sequence_number"].to_list()
        if len(proposed_sequences) != len(set(proposed_sequences)):
            raise_backtest_error(
                BacktestAccountingError,
                "duplicate_proposed_orders",
                "each proposed order must have a unique sequence number.",
            )
        if sorted(proposed_sequences) != sorted(decision_sequences):
            raise_backtest_error(
                BacktestAccountingError,
                "risk_decision_order_mismatch",
                "every proposed order must receive exactly one risk decision.",
            )
        if len(decision_sequences) != len(set(decision_sequences)):
            raise_backtest_error(
                BacktestAccountingError,
                "duplicate_risk_decisions",
                "each proposed order may receive only one risk decision.",
            )
        approved_sequences = risk_decisions.loc[
            risk_decisions["approved"] == True,  # noqa: E712
            "order_sequence_number",
        ].to_list()
        if sorted(fill_sequences) != sorted(approved_sequences):
            raise_backtest_error(
                BacktestAccountingError,
                "fill_risk_decision_mismatch",
                "every fill must reference exactly one approved risk decision.",
            )
        if len(fill_sequences) != len(set(fill_sequences)):
            raise_backtest_error(
                BacktestAccountingError,
                "duplicate_fills",
                "each approved order may create only one fill.",
            )
        if not fills.empty:
            if (fills["risk_approved"] != True).any():  # noqa: E712
                raise_backtest_error(
                    BacktestAccountingError,
                    "fill_without_approved_risk_decision",
                    "fill rows must record risk_approved as True.",
                )
            for row in fills.itertuples(index=False):
                require_plain_date(
                    row.signal_session,
                    field_name="fill_signal_session",
                    error_type=BacktestAccountingError,
                )
                require_plain_date(
                    row.execution_session,
                    field_name="fill_execution_session",
                    error_type=BacktestAccountingError,
                )
                if row.execution_session <= row.signal_session:
                    raise_backtest_error(
                        BacktestAccountingError,
                        "same_candle_fill",
                        "fill execution must occur after signal generation.",
                    )
                quantity = require_int(
                    row.quantity,
                    field_name="fill_quantity",
                    minimum=1,
                    error_type=BacktestAccountingError,
                )
                if row.side not in (BUY_SIDE, SELL_SIDE):
                    raise_backtest_error(
                        BacktestAccountingError,
                        "invalid_fill_side",
                        "fill side must be buy or sell.",
                    )
                for field_name in (
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
                ):
                    require_finite_float(
                        getattr(row, field_name),
                        field_name=f"fill_{field_name}",
                        error_type=BacktestAccountingError,
                    )
                if not math.isclose(
                    row.reference_notional,
                    quantity * row.reference_open,
                    rel_tol=1e-12,
                    abs_tol=1e-8,
                ):
                    raise_backtest_error(
                        BacktestAccountingError,
                        "fill_reference_notional_mismatch",
                        "reference_notional must equal quantity times reference_open.",
                    )
                if not math.isclose(
                    row.execution_notional,
                    quantity * row.execution_price,
                    rel_tol=1e-12,
                    abs_tol=1e-8,
                ):
                    raise_backtest_error(
                        BacktestAccountingError,
                        "fill_execution_notional_mismatch",
                        "execution_notional must equal quantity times execution_price.",
                    )
                if not math.isclose(
                    row.slippage_cost,
                    abs(row.execution_price - row.reference_open) * quantity,
                    rel_tol=1e-12,
                    abs_tol=1e-8,
                ):
                    raise_backtest_error(
                        BacktestAccountingError,
                        "fill_slippage_cost_mismatch",
                        "slippage_cost must match execution price slippage.",
                    )
                if not math.isclose(
                    row.total_transaction_cost,
                    row.commission + row.slippage_cost,
                    rel_tol=1e-12,
                    abs_tol=1e-8,
                ):
                    raise_backtest_error(
                        BacktestAccountingError,
                        "fill_total_cost_mismatch",
                        "total_transaction_cost must equal commission plus slippage_cost.",
                    )
                if row.side == BUY_SIDE:
                    expected_cash_change = -(row.execution_notional + row.commission)
                    expected_shares_after = row.shares_before + quantity
                else:
                    expected_cash_change = row.execution_notional - row.commission
                    expected_shares_after = row.shares_before - quantity
                if row.shares_after != expected_shares_after:
                    raise_backtest_error(
                        BacktestAccountingError,
                        "fill_share_transition_mismatch",
                        "fill share transition must match side and quantity.",
                    )
                if not math.isclose(
                    row.cash_change,
                    expected_cash_change,
                    rel_tol=1e-12,
                    abs_tol=1e-8,
                ):
                    raise_backtest_error(
                        BacktestAccountingError,
                        "fill_cash_change_mismatch",
                        "fill cash_change must match side, notional, and commission.",
                    )
                if not math.isclose(
                    row.cash_after,
                    row.cash_before + row.cash_change,
                    rel_tol=1e-12,
                    abs_tol=1e-8,
                ):
                    raise_backtest_error(
                        BacktestAccountingError,
                        "fill_cash_after_mismatch",
                        "cash_after must equal cash_before plus cash_change.",
                    )
        if not risk_decisions.empty:
            approved_codes = risk_decisions.loc[
                risk_decisions["approved"] == True,  # noqa: E712
                "reason_codes",
            ].to_list()
            if any(tuple(codes) != (APPROVED_REASON,) for codes in approved_codes):
                raise_backtest_error(
                    BacktestAccountingError,
                    "approved_reason_code_mismatch",
                    "approved risk decisions must use the approved reason code.",
                )
        if not isinstance(cast(object, self.metrics), BacktestMetrics):
            raise_backtest_error(
                BacktestMetricError,
                "invalid_backtest_metrics",
                "metrics must be a BacktestMetrics object.",
            )
        metrics = BacktestMetrics(
            session_count=self.metrics.session_count,
            initial_cash=self.metrics.initial_cash,
            final_cash=self.metrics.final_cash,
            final_shares=self.metrics.final_shares,
            final_market_value=self.metrics.final_market_value,
            final_equity=self.metrics.final_equity,
            total_return=self.metrics.total_return,
            maximum_drawdown=self.metrics.maximum_drawdown,
            total_reference_notional=self.metrics.total_reference_notional,
            total_execution_notional=self.metrics.total_execution_notional,
            total_commission=self.metrics.total_commission,
            total_slippage_cost=self.metrics.total_slippage_cost,
            total_transaction_cost=self.metrics.total_transaction_cost,
            turnover_ratio=self.metrics.turnover_ratio,
            exposure_fraction=self.metrics.exposure_fraction,
            proposed_order_count=self.metrics.proposed_order_count,
            approved_order_count=self.metrics.approved_order_count,
            rejected_order_count=self.metrics.rejected_order_count,
            fill_count=self.metrics.fill_count,
            buy_fill_count=self.metrics.buy_fill_count,
            sell_fill_count=self.metrics.sell_fill_count,
        )
        if metrics.session_count != len(portfolio):
            raise_backtest_error(
                BacktestMetricError,
                "metric_session_count_mismatch",
                "metric session_count must match portfolio rows.",
            )
        if metrics.proposed_order_count != len(proposed_orders):
            raise_backtest_error(
                BacktestMetricError,
                "metric_proposed_order_count_mismatch",
                "metric proposed_order_count must match proposed order rows.",
            )
        if metrics.approved_order_count != len(approved_sequences):
            raise_backtest_error(
                BacktestMetricError,
                "metric_approved_order_count_mismatch",
                "metric approved_order_count must match approved risk decisions.",
            )
        if metrics.fill_count != len(fills):
            raise_backtest_error(
                BacktestMetricError,
                "metric_fill_count_mismatch",
                "metric fill_count must match fill rows.",
            )
        if not fills.empty:
            sums = {
                "total_reference_notional": float(fills["reference_notional"].sum()),
                "total_execution_notional": float(fills["execution_notional"].sum()),
                "total_commission": float(fills["commission"].sum()),
                "total_slippage_cost": float(fills["slippage_cost"].sum()),
                "total_transaction_cost": float(fills["total_transaction_cost"].sum()),
            }
        else:
            sums = {
                "total_reference_notional": 0.0,
                "total_execution_notional": 0.0,
                "total_commission": 0.0,
                "total_slippage_cost": 0.0,
                "total_transaction_cost": 0.0,
            }
        for field_name, expected_sum in sums.items():
            if not math.isclose(
                getattr(metrics, field_name),
                expected_sum,
                rel_tol=1e-12,
                abs_tol=1e-9,
            ):
                raise_backtest_error(
                    BacktestMetricError,
                    f"metric_{field_name}_mismatch",
                    f"{field_name} must match fill audit sums.",
                )
        from spy_market_agent.backtesting.metrics import calculate_backtest_metrics

        recomputed_metrics = calculate_backtest_metrics(
            portfolio,
            fills,
            proposed_orders,
            risk_decisions,
            initial_cash=initial_cash,
        )
        if metrics != recomputed_metrics:
            raise_backtest_error(
                BacktestMetricError,
                "backtest_metrics_recomputation_mismatch",
                "stored metrics must equal metrics recomputed from validated audit frames.",
            )
        object.__setattr__(self, "strategy_signal_set", signals)
        object.__setattr__(self, "source_market_data", source_market_data)
        object.__setattr__(self, "execution_prices", execution_prices)
        object.__setattr__(self, "proposed_orders", proposed_orders)
        object.__setattr__(self, "risk_decisions", risk_decisions)
        object.__setattr__(self, "fills", fills)
        object.__setattr__(self, "portfolio", portfolio)
        object.__setattr__(self, "metrics", metrics)
        object.__setattr__(self, "backtest_config", backtest_config)
        object.__setattr__(self, "risk_config", risk_config)
        object.__setattr__(self, "selected_model_name", selected_model_name)
        object.__setattr__(self, "source_market_data_checksum", source_checksum)
        object.__setattr__(self, "feature_columns", feature_columns)
        object.__setattr__(self, "strategy_threshold", threshold)
        object.__setattr__(self, "first_signal_session", first_signal)
        object.__setattr__(self, "last_signal_session", last_signal)
        object.__setattr__(self, "first_execution_session", first_execution)
        object.__setattr__(self, "last_execution_session", last_execution)
        object.__setattr__(self, "initial_cash", initial_cash)
        object.__setattr__(self, "cost_assumptions", costs)
        object.__setattr__(self, "created_at", created_at)
