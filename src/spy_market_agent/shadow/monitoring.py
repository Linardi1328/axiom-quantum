from __future__ import annotations

from spy_market_agent.shadow.types import (
    ShadowHealthStatus,
    ShadowMonitoringEvent,
    ShadowMonitoringState,
)


def build_monitoring_state(
    events: tuple[ShadowMonitoringEvent, ...],
) -> ShadowMonitoringState:
    if any(event.status == ShadowHealthStatus.BLOCKED for event in events):
        status = ShadowHealthStatus.BLOCKED
    elif any(event.status == ShadowHealthStatus.DEGRADED for event in events):
        status = ShadowHealthStatus.DEGRADED
    else:
        status = ShadowHealthStatus.HEALTHY
    return ShadowMonitoringState(status=status, events=events)


def monitoring_event(
    code: str,
    message: str,
    status: ShadowHealthStatus,
) -> ShadowMonitoringEvent:
    return ShadowMonitoringEvent(code=code, message=message, status=status)
