from __future__ import annotations

from spy_market_agent.config.settings import load_settings
from spy_market_agent.dashboard.app import (
    create_default_client,
    load_dashboard_state,
    render_dashboard,
)


def main() -> None:
    import streamlit as st

    settings = load_settings()
    client = create_default_client(
        base_url=settings.dashboard_api_base_url,
        timeout_seconds=settings.api_timeout_seconds,
    )
    try:
        render_dashboard(st, load_dashboard_state(client))
    finally:
        client.close()


if __name__ == "__main__":
    main()


__all__ = ["main"]
