from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Protocol, cast

import exchange_calendars as xcals
import pandas as pd
from exchange_calendars import errors as xcal_errors

MARKET_CALENDAR = "XNYS"
MARKET_TIMEZONE = "America/New_York"
CALENDAR_START = date(1993, 1, 22)
CALENDAR_END = date(2050, 12, 30)

_EXPECTED_CALENDAR_EXCEPTIONS = (
    xcal_errors.DateOutOfBounds,
    xcal_errors.NoSessionsError,
    xcal_errors.NotSessionError,
    xcal_errors.RequestedSessionOutOfBounds,
    OverflowError,
    pd.errors.OutOfBoundsDatetime,
)


class CalendarDataError(ValueError):
    """Raised when a calendar query cannot be answered inside the supported range."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class TradingCalendar(Protocol):
    """Internal calendar interface used by validation code."""

    name: str
    timezone_name: str

    @property
    def calendar_code(self) -> str:
        """Canonical exchange-calendar identifier."""

    def is_session(self, session: date) -> bool:
        """Return whether the date is a valid exchange session."""

    def sessions_between(self, start_session: date, end_session: date) -> tuple[date, ...]:
        """Return valid exchange sessions between two dates, inclusive."""

    def missing_sessions(self, observed_sessions: tuple[date, ...]) -> tuple[date, ...]:
        """Return expected sessions absent from an observed ordered session sequence."""

    def is_session_complete(self, session: date, *, as_of: datetime) -> bool:
        """Return whether the daily session is complete at the injected timestamp."""

    def to_utc(self, timestamp: datetime) -> datetime:
        """Convert a timezone-aware timestamp to UTC."""


class XNYSCalendar:
    """Small adapter around `exchange-calendars` for NYSE daily sessions.

    Version 1 supports SPY research sessions from ``CALENDAR_START`` through
    ``CALENDAR_END``. The explicit range covers SPY's inception session and a
    substantial future horizon without relying on exchange-calendars' default
    rolling range.
    """

    name = MARKET_CALENDAR
    timezone_name = MARKET_TIMEZONE
    supported_start = CALENDAR_START
    supported_end = CALENDAR_END

    def __init__(self) -> None:
        self._calendar = xcals.get_calendar(
            MARKET_CALENDAR,
            start=CALENDAR_START,
            end=CALENDAR_END,
        )

    @property
    def calendar_code(self) -> str:
        return MARKET_CALENDAR

    @staticmethod
    def _timestamp_for_session(session: date, *, field_name: str) -> pd.Timestamp:
        try:
            return pd.Timestamp(session)
        except (TypeError, ValueError, OverflowError, pd.errors.OutOfBoundsDatetime) as exc:
            msg = f"{field_name} cannot be represented as an XNYS calendar session."
            raise CalendarDataError("calendar_session_unrepresentable", msg) from exc

    @staticmethod
    def _range_failure_message(session: date) -> str:
        return (
            f"{session.isoformat()} is outside the supported XNYS calendar range "
            f"{CALENDAR_START.isoformat()} through {CALENDAR_END.isoformat()}."
        )

    @classmethod
    def _wrap_calendar_exception(cls, session: date) -> CalendarDataError:
        return CalendarDataError(
            "calendar_session_out_of_range",
            cls._range_failure_message(session),
        )

    def is_session(self, session: date) -> bool:
        timestamp = self._timestamp_for_session(session, field_name="session")
        try:
            return bool(self._calendar.is_session(timestamp))
        except _EXPECTED_CALENDAR_EXCEPTIONS as exc:
            raise self._wrap_calendar_exception(session) from exc

    def sessions_between(self, start_session: date, end_session: date) -> tuple[date, ...]:
        start = self._timestamp_for_session(start_session, field_name="start_session")
        end = self._timestamp_for_session(end_session, field_name="end_session")
        try:
            sessions = self._calendar.sessions_in_range(start, end)
        except _EXPECTED_CALENDAR_EXCEPTIONS as exc:
            session = start_session if start_session < CALENDAR_START else end_session
            raise self._wrap_calendar_exception(session) from exc
        return tuple(pd.Timestamp(session).date() for session in sessions)

    def missing_sessions(self, observed_sessions: tuple[date, ...]) -> tuple[date, ...]:
        if not observed_sessions:
            return ()

        expected_sessions = self.sessions_between(observed_sessions[0], observed_sessions[-1])
        observed = set(observed_sessions)
        return tuple(session for session in expected_sessions if session not in observed)

    def is_session_complete(self, session: date, *, as_of: datetime) -> bool:
        as_of_utc = self.to_utc(as_of)
        if not self.is_session(session):
            return False

        timestamp = self._timestamp_for_session(session, field_name="session")
        try:
            close = pd.Timestamp(self._calendar.session_close(timestamp))
        except _EXPECTED_CALENDAR_EXCEPTIONS as exc:
            raise self._wrap_calendar_exception(session) from exc
        close = close.tz_localize(UTC) if close.tzinfo is None else close.tz_convert(UTC)
        close_time = cast(datetime, close.to_pydatetime())
        return as_of_utc >= close_time

    def to_utc(self, timestamp: datetime) -> datetime:
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            msg = "timestamp must be timezone-aware."
            raise ValueError(msg)
        return timestamp.astimezone(UTC)
