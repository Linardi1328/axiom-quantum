from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from spy_market_agent.config import Settings


def test_phase8_execution_defaults_are_paper_disabled_dry_run_and_kill_switch_engaged() -> None:
    settings = Settings()

    assert settings.execution_mode == "paper"
    assert settings.enable_paper_execution is False
    assert settings.dry_run is True
    assert settings.paper_execution_kill_switch is True
    assert settings.paper_execution_require_market_open is True
    assert settings.paper_order_submission_enabled is False


def test_live_execution_mode_remains_rejected() -> None:
    with pytest.raises(ValidationError, match="execution_mode"):
        Settings(execution_mode="live")


def test_regular_market_hours_requirement_cannot_be_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValidationError, match="paper_execution_require_market_open"):
        Settings(paper_execution_require_market_open=False)

    monkeypatch.setenv("PAPER_EXECUTION_REQUIRE_MARKET_OPEN", "false")
    with pytest.raises(ValidationError, match="paper_execution_require_market_open"):
        Settings()


def test_missing_credentials_are_allowed_but_displayed_only_as_presence_flags() -> None:
    settings = Settings()
    displayed = settings.display_safe_dict()

    assert displayed["alpaca_api_key_present"] is False
    assert displayed["alpaca_secret_key_present"] is False
    assert "alpaca_api_key" not in displayed
    assert "alpaca_secret_key" not in displayed


def test_secret_values_are_not_exposed_in_repr_or_safe_display() -> None:
    settings = Settings(
        alpaca_api_key=SecretStr("AKSECRET"),
        alpaca_secret_key=SecretStr("SKSECRET"),
    )

    assert "AKSECRET" not in repr(settings)
    assert "SKSECRET" not in repr(settings)
    displayed = settings.display_safe_dict()
    rendered = repr(displayed)

    assert displayed["alpaca_api_key_present"] is True
    assert displayed["alpaca_secret_key_present"] is True
    assert "AKSECRET" not in rendered
    assert "SKSECRET" not in rendered
