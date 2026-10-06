from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, field_validator

from spy_market_agent.market_data.calendar import CalendarDataError, TradingCalendar, XNYSCalendar
from spy_market_agent.market_data.models import require_utc_datetime
from spy_market_agent.shadow.types import ShadowHealthStatus


class ShadowScheduleDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    target_session: date
    is_xnys_session: bool
    session_complete: bool
    provider_finalized: bool
    already_processed: bool
    eligible: bool
    status: ShadowHealthStatus
    refusal_reasons: tuple[str, ...] = ()


class ShadowScheduleInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    target_session: date
    as_of: datetime
    provider_finalized: bool
    already_processed: bool = False

    @field_validator("as_of")
    @classmethod
    def _utc_as_of(cls, value: datetime) -> datetime:
        return require_utc_datetime(value, field_name="as_of")


def evaluate_shadow_schedule(
    schedule_input: ShadowScheduleInput,
    *,
    calendar: TradingCalendar | None = None,
) -> ShadowScheduleDecision:
    calendar = calendar or XNYSCalendar()
    reasons: list[str] = []

    try:
        is_session = calendar.is_session(schedule_input.target_session)
        session_complete = calendar.is_session_complete(
            schedule_input.target_session,
            as_of=schedule_input.as_of,
        )
    except CalendarDataError:
        is_session = False
        session_complete = False
        reasons.append("calendar_uncertainty")

    if not is_session:
        reasons.append("not_xnys_session")
    if is_session and not session_complete:
        reasons.append("session_not_complete")
    if not schedule_input.provider_finalized:
        reasons.append("provider_not_finalized")
    if schedule_input.already_processed:
        reasons.append("duplicate_run")

    unique_reasons = tuple(dict.fromkeys(reasons))
    eligible = not unique_reasons
    return ShadowScheduleDecision(
        target_session=schedule_input.target_session,
        is_xnys_session=is_session,
        session_complete=session_complete,
        provider_finalized=schedule_input.provider_finalized,
        already_processed=schedule_input.already_processed,
        eligible=eligible,
        status=ShadowHealthStatus.HEALTHY if eligible else ShadowHealthStatus.BLOCKED,
        refusal_reasons=unique_reasons,
    )
