"""Strict v2 quarantine contracts; never evidence of rights or historical availability."""

from __future__ import annotations

import platform
import re
import subprocess
from datetime import UTC, date, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Final, Literal, Self, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    StrictBool,
    ValidationInfo,
    field_validator,
    model_validator,
)

from spy_market_agent.market_data.calendar import XNYSCalendar
from spy_market_agent.market_data.errors import InvalidAcquisitionRequest, UnsafeDataPath
from spy_market_agent.market_data.manifest import sha256_bytes

CONTEXT_ACQUISITION_SCHEMA: Final = "aq-qqq-iwm-context-acquisition-v2"
RAW_SCHEMA: Final = "aq-qqq-iwm-context-raw-v2"
ACCESS_METHOD: Final = (
    "alpaca-py StockHistoricalDataClient low-level get /v2/stocks/bars "
    "with bounded requests session timeout"
)
MAX_PAGES = 64
MAX_ROWS = 20_000
MAX_SECONDS = 120.0
MAX_BYTES = 32 * 1024 * 1024
CONTEXT_BAR_HEADER = ("symbol", "session", "open", "high", "low", "close", "volume")
DATASET_PATTERN = re.compile(r"aq-ctx-v2-[0-9a-f]{24}\Z")


class StrictContract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, validate_default=True)


class ContextAcquisitionRequest(StrictContract):
    symbol: str
    start_date: date
    end_date: date
    timeframe: str = "1Day"
    provider: str = "alpaca"
    feed: str = "sip"
    adjustment_mode: str = "all"
    data_root: Path = Path("data")
    acknowledge_provider_terms: StrictBool = False
    asof: date | None = None

    @field_validator("symbol", "timeframe", "provider", "feed", "adjustment_mode")
    @classmethod
    def _exact_policy(cls, value: str, info: ValidationInfo) -> str:
        required = {
            "symbol": ("QQQ", "IWM"),
            "timeframe": ("1Day",),
            "provider": ("alpaca",),
            "feed": ("sip",),
            "adjustment_mode": ("all",),
        }
        if value not in required[cast(str, info.field_name)]:
            raise InvalidAcquisitionRequest("unsupported context request policy.")
        return value

    @field_validator("data_root")
    @classmethod
    def _root(cls, value: Path) -> Path:
        if value != Path("data"):
            raise UnsafeDataPath("context data_root must be exactly data/ (ignored).")
        return value

    @model_validator(mode="after")
    def _policy(self) -> Self:
        if (
            self.start_date > self.end_date
            or not self.acknowledge_provider_terms
            or self.asof is not None
        ):
            raise InvalidAcquisitionRequest(
                "ordered dates and explicit terms acknowledgment required."
            )
        return self

    def sanitized_parameters(self) -> dict[str, str | None]:
        return {
            "symbol": self.symbol,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "timeframe": self.timeframe,
            "provider": self.provider,
            "feed": self.feed,
            "adjustment_mode": self.adjustment_mode,
            "asof": "-",
        }


def aware(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise InvalidAcquisitionRequest("context clock must be timezone-aware.")
    return value.astimezone(UTC)


def validate_request(
    request: ContextAcquisitionRequest, now: datetime
) -> ContextAcquisitionRequest:
    # model_validate(instance) can skip validation; reconstruct primitive fields.
    if type(request) is not ContextAcquisitionRequest or request.__pydantic_extra__:
        raise InvalidAcquisitionRequest("invalid context request type or fields.")
    checked = ContextAcquisitionRequest.model_validate(dict(request.__dict__))
    calendar = XNYSCalendar()
    sessions = calendar.sessions_between(checked.start_date, checked.end_date)
    if (
        checked.end_date > now.date()
        or not sessions
        or len(sessions) > MAX_ROWS
        or not calendar.is_session_complete(sessions[-1], as_of=now)
    ):
        raise InvalidAcquisitionRequest(
            "context range is empty, unsupported, future or incomplete."
        )
    return checked


class ContextLineage(StrictContract):
    git_commit: str
    python_version: str
    package_version: str
    dependency_versions: dict[str, str]
    source_checksums: dict[str, str]


def context_lineage() -> ContextLineage:
    package = Path(__file__).resolve().parents[1]
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=package.parent.parent,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    paths = (
        "market_data/context_acquisition.py",
        "market_data/context_contracts.py",
        "market_data/context_storage.py",
        "market_data/context_transport.py",
        "market_data/alpaca_provider.py",
        "market_data/acquisition.py",
        "market_data/calendar.py",
        "market_data/manifest.py",
        "market_data/errors.py",
    )
    return ContextLineage(
        git_commit=commit,
        python_version=platform.python_version(),
        package_version=version("spy-market-agent"),
        dependency_versions={
            key: version(key)
            for key in (
                "alpaca-py",
                "exchange-calendars",
                "pandas",
                "pydantic",
                "pydantic-settings",
            )
        },
        source_checksums={path: sha256_bytes((package / path).read_bytes()) for path in paths},
    )


class ContextBar(StrictContract):
    symbol: Literal["QQQ", "IWM"]
    session: date
    open: str
    high: str
    low: str
    close: str
    volume: int


class ContextAcquisitionManifest(StrictContract):
    schema_version: Literal["aq-qqq-iwm-context-acquisition-v2"]
    dataset_id: str
    symbol: Literal["QQQ", "IWM"]
    provider: Literal["alpaca"]
    feed: Literal["sip"]
    adjustment_mode: Literal["all"]
    timeframe: Literal["1Day"]
    start_date: date
    end_date: date
    first_session: date
    last_session: date
    row_count: int
    request_started_at: datetime
    retrieval_completed_at: datetime
    provider_api_version: Literal["v2"]
    provider_sdk_version: str
    lineage: ContextLineage
    raw_sha256: str
    canonical_sha256: str
    manifest_sha256: str | None = None
    point_in_time_status: Literal["UNVERIFIED"]
    usage_rights_status: Literal["UNVERIFIED"]
    execution_authority: Literal["none"]
    disposition: Literal["NO CANDIDATE PROMOTION"] = "NO CANDIDATE PROMOTION"
    generated_paths: dict[str, str]

    @field_validator("dataset_id")
    @classmethod
    def _id(cls, value: str) -> str:
        if not DATASET_PATTERN.fullmatch(value):
            raise ValueError("invalid context dataset ID syntax")
        return value

    @model_validator(mode="after")
    def _times(self) -> Self:
        if aware(self.request_started_at) > aware(self.retrieval_completed_at):
            raise ValueError("invalid context retrieval chronology")
        return self


class ContextAcquisitionArtifacts(StrictContract):
    manifest: ContextAcquisitionManifest
    raw_artifact_created: bool
    canonical_artifact_created: bool
    manifest_artifact_created: bool
