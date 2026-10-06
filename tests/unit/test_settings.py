from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

import spy_market_agent
from spy_market_agent.config.settings import REDACTED_DATABASE_URL, Settings, load_settings


def test_defaults_load_without_env_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)

    settings = load_settings()

    assert settings.environment == "development"
    assert settings.execution_mode == "paper"
    assert settings.enable_paper_execution is False
    assert settings.dry_run is True
    assert settings.initial_capital_usd > 0
    assert settings.database_url == "sqlite:///./spy_market_agent.db"
    assert settings.market_symbol == "SPY"
    assert settings.market_timeframe == "1Day"
    assert settings.exchange_calendar == "XNYS"
    assert settings.market_timezone == "America/New_York"
    assert settings.adjustment_policy == "adjusted"
    assert settings.alpaca_api_key is None
    assert settings.alpaca_secret_key is None
    assert not (tmp_path / ".env").exists()


def test_execution_mode_defaults_to_paper() -> None:
    assert Settings().execution_mode == "paper"


@pytest.mark.parametrize("execution_mode", ["live", "simulation", ""])
def test_unsafe_execution_modes_are_rejected(execution_mode: str) -> None:
    with pytest.raises(ValidationError, match="execution_mode"):
        Settings(execution_mode=execution_mode)


def test_spy_symbol_is_accepted() -> None:
    assert Settings(market_symbol="SPY").market_symbol == "SPY"


@pytest.mark.parametrize("symbol", ["AAPL", "spy", ""])
def test_non_spy_symbols_are_rejected(symbol: str) -> None:
    with pytest.raises(ValidationError, match="market_symbol"):
        Settings(market_symbol=symbol)


def test_daily_timeframe_is_accepted() -> None:
    assert Settings(market_timeframe="1Day").market_timeframe == "1Day"


@pytest.mark.parametrize("timeframe", ["1Hour", "1Min", ""])
def test_other_timeframes_are_rejected(timeframe: str) -> None:
    with pytest.raises(ValidationError, match="market_timeframe"):
        Settings(market_timeframe=timeframe)


def test_xnys_calendar_is_accepted() -> None:
    assert Settings(exchange_calendar="XNYS").exchange_calendar == "XNYS"


@pytest.mark.parametrize("calendar", ["NASDAQ", "NYSE", ""])
def test_other_calendars_are_rejected(calendar: str) -> None:
    with pytest.raises(ValidationError, match="exchange_calendar"):
        Settings(exchange_calendar=calendar)


def test_new_york_market_timezone_is_accepted() -> None:
    assert Settings(market_timezone="America/New_York").market_timezone == "America/New_York"


@pytest.mark.parametrize("timezone", ["UTC", "Asia/Kuala_Lumpur", ""])
def test_other_market_timezones_are_rejected(timezone: str) -> None:
    with pytest.raises(ValidationError, match="market_timezone"):
        Settings(market_timezone=timezone)


def test_adjusted_policy_is_accepted() -> None:
    assert Settings(adjustment_policy="adjusted").adjustment_policy == "adjusted"


@pytest.mark.parametrize("policy", ["raw", "mixed", ""])
def test_raw_or_mixed_adjustment_policies_are_rejected(policy: str) -> None:
    with pytest.raises(ValidationError, match="adjustment_policy"):
        Settings(adjustment_policy=policy)


@pytest.mark.parametrize("capital", [Decimal("0"), Decimal("-1")])
def test_initial_capital_must_be_positive(capital: Decimal) -> None:
    with pytest.raises(ValidationError, match="initial_capital_usd"):
        Settings(initial_capital_usd=capital)


def test_paper_execution_permission_defaults_to_false() -> None:
    settings = Settings()

    assert settings.enable_paper_execution is False
    assert settings.paper_order_submission_enabled is False


def test_dry_run_defaults_to_true() -> None:
    settings = Settings()

    assert settings.dry_run is True
    assert settings.paper_order_submission_enabled is False


@pytest.mark.parametrize(
    ("enable_paper_execution", "dry_run", "paper_execution_kill_switch", "expected"),
    [
        (False, True, True, False),
        (False, False, False, False),
        (True, True, False, False),
        (True, False, True, False),
        (True, False, False, True),
    ],
)
def test_paper_order_submission_helper_requires_explicit_non_dry_run_permission(
    enable_paper_execution: bool,
    dry_run: bool,
    paper_execution_kill_switch: bool,
    expected: bool,
) -> None:
    settings = Settings(
        execution_mode="paper",
        enable_paper_execution=enable_paper_execution,
        dry_run=dry_run,
        paper_execution_kill_switch=paper_execution_kill_switch,
    )

    assert settings.paper_order_submission_enabled is expected


def test_missing_alpaca_credentials_do_not_block_research_settings() -> None:
    settings = Settings(alpaca_api_key=None, alpaca_secret_key=None)

    assert settings.alpaca_api_key is None
    assert settings.alpaca_secret_key is None


def test_secret_values_are_redacted_from_repr_and_display_serialization() -> None:
    settings = Settings(
        alpaca_api_key=SecretStr("paper-key"),
        alpaca_secret_key=SecretStr("paper-secret"),
    )

    assert "paper-key" not in repr(settings)
    assert "paper-secret" not in repr(settings)
    displayed = settings.display_safe_dict()
    assert "alpaca_api_key" not in displayed
    assert "alpaca_secret_key" not in displayed
    assert displayed["alpaca_api_key_present"] is True
    assert displayed["alpaca_secret_key_present"] is True


def test_database_url_is_excluded_from_settings_repr() -> None:
    settings = Settings(database_url="postgresql://db_user:db_password@localhost:5432/research")

    settings_repr = repr(settings)

    assert "database_url" not in settings_repr
    assert "db_user" not in settings_repr
    assert "db_password" not in settings_repr


@pytest.mark.parametrize(
    ("database_url", "forbidden_fragments"),
    [
        (
            "postgresql://db_user:db_password@localhost/research",
            ("db_user", "db_password"),
        ),
        (
            "postgresql:db_user:db_password@localhost/research",
            ("db_user", "db_password"),
        ),
        (
            "postgresql:///db_user:db_password@localhost/research",
            ("db_user", "db_password"),
        ),
        (
            "postgresql://localhost/research?password=db_password",
            ("password", "db_password"),
        ),
        (
            "postgresql://localhost/research?access_token=token_value",
            ("access_token", "token_value"),
        ),
        (
            "postgresql://db_user:db_password@[::1]:5432/research",
            ("db_user", "db_password"),
        ),
        (
            "postgresql://db_user:db_password@localhost:notaport/research",
            ("db_user", "db_password"),
        ),
        (
            "postgresql://db_user:db_password@[::1/research",
            ("db_user", "db_password"),
        ),
        (
            "sqlite:///./spy_market_agent.db?password=db_password",
            ("password", "db_password"),
        ),
    ],
)
def test_credential_bearing_database_urls_are_fully_redacted_from_display_serialization(
    database_url: str,
    forbidden_fragments: tuple[str, ...],
) -> None:
    settings = Settings(database_url=database_url)

    display_values = settings.display_safe_dict()
    display_text = str(display_values)

    assert display_values["database_url"] == REDACTED_DATABASE_URL
    for fragment in forbidden_fragments:
        assert fragment not in display_text


def test_sqlite_database_urls_remain_unchanged_in_display_serialization() -> None:
    settings = Settings(database_url="sqlite:///./spy_market_agent.db")

    assert settings.display_safe_dict()["database_url"] == "sqlite:///./spy_market_agent.db"


@pytest.mark.parametrize(
    ("field_name", "unsafe_value"),
    [
        ("execution_mode", "live"),
        ("market_symbol", "AAPL"),
        ("enable_paper_execution", True),
        ("dry_run", False),
    ],
)
def test_settings_are_immutable_after_construction(field_name: str, unsafe_value: object) -> None:
    settings = Settings()

    with pytest.raises(ValidationError, match="frozen"):
        setattr(settings, field_name, unsafe_value)


def test_environment_variables_can_override_safe_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("ENABLE_PAPER_EXECUTION", "true")
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("PAPER_EXECUTION_KILL_SWITCH", "false")

    settings = load_settings()

    assert settings.environment == "test"
    assert settings.enable_paper_execution is True
    assert settings.dry_run is False
    assert settings.paper_execution_kill_switch is False
    assert settings.paper_order_submission_enabled is True


def test_unknown_environment_variables_do_not_break_loading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("UNRELATED_OPERATING_SYSTEM_VALUE", "ignored")

    assert load_settings().execution_mode == "paper"


def test_loading_settings_does_not_create_files_or_external_actions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)

    settings = load_settings()

    assert settings.execution_mode == "paper"
    assert list(tmp_path.iterdir()) == []


def test_importing_package_does_not_load_settings_or_execute_side_effects() -> None:
    assert spy_market_agent.__version__ == "2.0.0b1"
    assert "settings" not in vars(spy_market_agent)
