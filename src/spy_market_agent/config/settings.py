from __future__ import annotations

from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REDACTED_DATABASE_URL = "<redacted-database-url>"
_CREDENTIAL_MARKERS = (
    "access_token",
    "api_key",
    "apikey",
    "credential",
    "password",
    "passwd",
    "pwd",
    "secret",
    "token",
)


def _require_value(value: str, *, expected: str, field_name: str) -> str:
    if value != expected:
        msg = f"{field_name} must be {expected!r} for Version 1."
        raise ValueError(msg)
    return value


class Settings(BaseSettings):
    """Typed application settings.

    Settings may be supplied explicitly, through environment variables, or through a local
    `.env` file. Instantiating this class never submits orders, imports execution adapters,
    or requires broker credentials.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
        case_sensitive=False,
        frozen=True,
    )

    environment: str = "development"
    execution_mode: str = "paper"
    enable_paper_execution: bool = False
    dry_run: bool = True
    paper_execution_kill_switch: bool = True
    paper_execution_require_market_open: bool = True
    initial_capital_usd: Decimal = Field(default=Decimal("10000"), gt=Decimal("0"))
    database_url: str = Field(default="sqlite:///./spy_market_agent.db", repr=False)
    sqlite_database_path: str = "./spy_market_agent.sqlite3"
    dashboard_api_base_url: str = "http://127.0.0.1:8000"
    api_timeout_seconds: float = Field(default=5.0, gt=0.0, le=60.0)
    market_symbol: str = "SPY"
    market_timeframe: str = "1Day"
    exchange_calendar: str = "XNYS"
    market_timezone: str = "America/New_York"
    adjustment_policy: str = "adjusted"
    alpaca_api_key: SecretStr | None = Field(default=None, repr=False)
    alpaca_secret_key: SecretStr | None = Field(default=None, repr=False)
    alpaca_market_data_api_key: SecretStr | None = Field(default=None, repr=False)
    alpaca_market_data_secret_key: SecretStr | None = Field(default=None, repr=False)
    alpaca_market_data_feed: str = "sip"
    market_data_root: str = "./data"
    market_data_max_retries: int = Field(default=3, ge=0, le=10)
    market_data_timeout_seconds: float = Field(default=30.0, gt=0.0, le=300.0)

    @field_validator("execution_mode")
    @classmethod
    def _validate_execution_mode(cls, value: str) -> str:
        return _require_value(value, expected="paper", field_name="execution_mode")

    @field_validator("paper_execution_require_market_open")
    @classmethod
    def _validate_market_open_required(cls, value: bool) -> bool:
        if value is not True:
            msg = "paper_execution_require_market_open must be true for Phase 8."
            raise ValueError(msg)
        return value

    @field_validator("market_symbol")
    @classmethod
    def _validate_market_symbol(cls, value: str) -> str:
        return _require_value(value, expected="SPY", field_name="market_symbol")

    @field_validator("market_timeframe")
    @classmethod
    def _validate_market_timeframe(cls, value: str) -> str:
        return _require_value(value, expected="1Day", field_name="market_timeframe")

    @field_validator("exchange_calendar")
    @classmethod
    def _validate_exchange_calendar(cls, value: str) -> str:
        return _require_value(value, expected="XNYS", field_name="exchange_calendar")

    @field_validator("market_timezone")
    @classmethod
    def _validate_market_timezone(cls, value: str) -> str:
        return _require_value(
            value,
            expected="America/New_York",
            field_name="market_timezone",
        )

    @field_validator("adjustment_policy")
    @classmethod
    def _validate_adjustment_policy(cls, value: str) -> str:
        return _require_value(value, expected="adjusted", field_name="adjustment_policy")

    @field_validator("alpaca_market_data_feed")
    @classmethod
    def _validate_market_data_feed(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"sip", "iex"}:
            msg = "alpaca_market_data_feed must be 'sip' or 'iex'."
            raise ValueError(msg)
        return normalized

    @field_validator("sqlite_database_path", "dashboard_api_base_url")
    @classmethod
    def _validate_non_empty_text(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            msg = "setting value must not be blank."
            raise ValueError(msg)
        return trimmed

    @property
    def paper_order_submission_enabled(self) -> bool:
        """Whether future paper-order submission would be permitted by configuration only."""

        return (
            self.execution_mode == "paper"
            and self.enable_paper_execution
            and not self.dry_run
            and not self.paper_execution_kill_switch
        )

    def display_safe_dict(self) -> dict[str, Any]:
        """Return settings suitable for display without revealing secret values."""

        safe_values = self.model_dump(mode="json")
        safe_values["database_url"] = _display_safe_database_url(self.database_url)
        safe_values["alpaca_api_key_present"] = _secret_present(self.alpaca_api_key)
        safe_values["alpaca_secret_key_present"] = _secret_present(self.alpaca_secret_key)
        safe_values["alpaca_market_data_api_key_present"] = _secret_present(
            self.alpaca_market_data_api_key
        )
        safe_values["alpaca_market_data_secret_key_present"] = _secret_present(
            self.alpaca_market_data_secret_key
        )
        safe_values.pop("alpaca_api_key", None)
        safe_values.pop("alpaca_secret_key", None)
        safe_values.pop("alpaca_market_data_api_key", None)
        safe_values.pop("alpaca_market_data_secret_key", None)
        return safe_values


def load_settings() -> Settings:
    """Load settings without creating files or performing external actions."""

    return Settings()


def _display_safe_database_url(url: str) -> str:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return REDACTED_DATABASE_URL

    if parsed.scheme.lower() != "sqlite":
        return REDACTED_DATABASE_URL

    candidate = f"{parsed.netloc}{parsed.path}{parsed.query}{parsed.fragment}".lower()
    if "@" in candidate:
        return REDACTED_DATABASE_URL
    if parsed.query or parsed.fragment:
        return REDACTED_DATABASE_URL
    if any(marker in candidate for marker in _CREDENTIAL_MARKERS):
        return REDACTED_DATABASE_URL
    return url


def _secret_present(value: SecretStr | None) -> bool:
    if value is None:
        return False
    return bool(value.get_secret_value().strip())
