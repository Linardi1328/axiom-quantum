from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from spy_market_agent.market_data.calendar import (
    CALENDAR_END,
    CALENDAR_START,
    CalendarDataError,
    XNYSCalendar,
)


def test_calendar_identifies_valid_xnys_sessions() -> None:
    calendar = XNYSCalendar()

    assert calendar.calendar_code == "XNYS"
    assert calendar.supported_start == CALENDAR_START
    assert calendar.supported_end == CALENDAR_END
    assert calendar.is_session(date(2024, 1, 2))
    assert not calendar.is_session(date(2024, 1, 6))
    assert not calendar.is_session(date(2024, 1, 1))


def test_calendar_support_range_covers_spy_inception_session() -> None:
    calendar = XNYSCalendar()

    assert date(1993, 1, 22) == CALENDAR_START
    assert calendar.is_session(date(1993, 1, 22))


def test_calendar_support_range_includes_future_horizon_beyond_2027() -> None:
    calendar = XNYSCalendar()

    assert date(2050, 12, 30) == CALENDAR_END
    assert calendar.is_session(date(2035, 1, 2))


def test_calendar_out_of_range_date_fails_with_project_exception() -> None:
    calendar = XNYSCalendar()

    with pytest.raises(CalendarDataError) as exc_info:
        calendar.is_session(date(2051, 1, 3))

    assert exc_info.value.code == "calendar_session_out_of_range"
    assert "2051-01-03" in str(exc_info.value)


def test_calendar_extremely_old_date_fails_with_project_exception() -> None:
    calendar = XNYSCalendar()

    with pytest.raises(CalendarDataError) as exc_info:
        calendar.is_session(date(1, 1, 1))

    assert exc_info.value.code == "calendar_session_out_of_range"


def test_sessions_between_excludes_weekends_and_holidays() -> None:
    calendar = XNYSCalendar()

    sessions = calendar.sessions_between(date(2024, 1, 1), date(2024, 1, 8))

    assert sessions == (
        date(2024, 1, 2),
        date(2024, 1, 3),
        date(2024, 1, 4),
        date(2024, 1, 5),
        date(2024, 1, 8),
    )


def test_missing_sessions_reports_only_expected_exchange_sessions() -> None:
    calendar = XNYSCalendar()

    missing = calendar.missing_sessions((date(2024, 1, 2), date(2024, 1, 4)))

    assert missing == (date(2024, 1, 3),)


def test_session_completion_uses_injected_aware_timestamp() -> None:
    calendar = XNYSCalendar()

    assert not calendar.is_session_complete(
        date(2024, 1, 5),
        as_of=datetime(2024, 1, 5, 15, 0, tzinfo=UTC),
    )
    assert calendar.is_session_complete(
        date(2024, 1, 5),
        as_of=datetime(2024, 1, 5, 22, 0, tzinfo=UTC),
    )


def test_calendar_converts_aware_timestamps_to_utc() -> None:
    calendar = XNYSCalendar()

    new_york_time = datetime(2024, 1, 5, 16, 0, tzinfo=ZoneInfo("America/New_York"))

    assert calendar.to_utc(new_york_time) == datetime(2024, 1, 5, 21, 0, tzinfo=UTC)


def test_calendar_rejects_naive_timestamps() -> None:
    calendar = XNYSCalendar()

    with pytest.raises(ValueError, match="timezone-aware"):
        calendar.to_utc(datetime(2024, 1, 5, 16, 0))
