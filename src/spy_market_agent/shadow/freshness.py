from __future__ import annotations

from spy_market_agent.market_data.calendar import CalendarDataError, TradingCalendar, XNYSCalendar
from spy_market_agent.shadow.types import (
    DailyMarketDataStatus,
    FreshnessDecision,
    FreshnessStatus,
    ShadowHealthStatus,
)


def evaluate_market_data_freshness(
    status: DailyMarketDataStatus,
    *,
    expected_adjustment: str = "all",
    calendar: TradingCalendar | None = None,
) -> FreshnessDecision:
    calendar = calendar or XNYSCalendar()
    reasons: list[str] = []

    if status.adjustment != expected_adjustment:
        reasons.append("unsupported_adjustment")

    try:
        is_session = calendar.is_session(status.session)
        session_complete = calendar.is_session_complete(status.session, as_of=status.as_of)
    except CalendarDataError:
        reasons.append("calendar_uncertainty")
        is_session = False
        session_complete = False

    if not is_session:
        reasons.append("not_xnys_session")
    if not status.expected_session_present:
        reasons.append("missing_session")
    if status.duplicate_sessions_detected:
        reasons.append("duplicate_session")
    if status.out_of_order_sessions_detected:
        reasons.append("out_of_order_sessions")
    if not status.ohlcv_valid:
        reasons.append("invalid_ohlcv")
    if not status.session_complete or not session_complete:
        reasons.append("incomplete_session")
    if not status.provider_finalized:
        reasons.append("provider_not_finalized")
    if status.stale:
        reasons.append("stale_data")

    unique_reasons = tuple(dict.fromkeys(reasons))
    if unique_reasons:
        return FreshnessDecision(
            status=FreshnessStatus.BLOCKED,
            health_status=ShadowHealthStatus.BLOCKED,
            eligible=False,
            reasons=unique_reasons,
        )

    return FreshnessDecision(
        status=FreshnessStatus.FRESH,
        health_status=ShadowHealthStatus.HEALTHY,
        eligible=True,
        reasons=(),
    )
