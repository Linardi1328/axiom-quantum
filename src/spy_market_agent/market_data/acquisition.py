from __future__ import annotations

import os
import platform
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from spy_market_agent import __version__
from spy_market_agent.market_data.errors import (
    InvalidAcquisitionRequest,
    MissingMarketDataCredentials,
    UnsafeDataPath,
    UnsupportedMarketSymbol,
    UnsupportedTimeframe,
)

PHASE1_SCHEMA_VERSION = "spy-v2-phase1-canonical-daily-bars-v1"
PHASE1_MANIFEST_SCHEMA_VERSION = "spy-v2-phase1-dataset-manifest-v1"
PHASE1_DATASET_ID_VERSION = "spy-v2-phase1-dataset-id-v1"
PHASE1_SOURCE_CHECKSUM_VERSION = "spy-v2-phase1-source-v1-sha256"
PHASE1_CANONICAL_CHECKSUM_VERSION = "spy-v2-phase1-canonical-v1-sha256"
PHASE1_CORPORATE_ACTION_POLICY = "provider-adjustment-policy-recorded-no-separate-ca-snapshot-v1"
SUPPORTED_ACQUISITION_SYMBOL = "SPY"
SUPPORTED_ACQUISITION_TIMEFRAME = "1Day"
SUPPORTED_PROVIDER = "alpaca"
SUPPORTED_FEEDS = ("sip", "iex")
SUPPORTED_ADJUSTMENT_MODES = ("raw", "all")
DEFAULT_DATA_ROOT = Path("./data")

Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(tz=UTC)


def _require_aware_utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        msg = f"{field_name} must be timezone-aware."
        raise ValueError(msg)
    return value.astimezone(UTC)


def _normalize_adjustment(value: str) -> str:
    normalized = value.strip().lower()
    if normalized == "all-adjusted":
        return "all"
    return normalized


def _today_utc() -> date:
    return utc_now().date()


class AcquisitionRequest(BaseModel):
    """Validated explicit request for Phase 1 historical SPY daily acquisition."""

    model_config = ConfigDict(frozen=True)

    symbol: str = SUPPORTED_ACQUISITION_SYMBOL
    start_date: date
    end_date: date
    timeframe: str = SUPPORTED_ACQUISITION_TIMEFRAME
    provider: str = SUPPORTED_PROVIDER
    feed: str
    adjustment_mode: str
    data_root: Path = DEFAULT_DATA_ROOT
    acknowledge_provider_terms: bool = False
    asof: date | None = None

    @field_validator("symbol")
    @classmethod
    def _validate_symbol(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized != SUPPORTED_ACQUISITION_SYMBOL:
            raise UnsupportedMarketSymbol("symbol must be exactly 'SPY' for Version 2 Phase 1.")
        return normalized

    @field_validator("timeframe")
    @classmethod
    def _validate_timeframe(cls, value: str) -> str:
        if value.strip() != SUPPORTED_ACQUISITION_TIMEFRAME:
            raise UnsupportedTimeframe("timeframe must be exactly '1Day' for Version 2 Phase 1.")
        return SUPPORTED_ACQUISITION_TIMEFRAME

    @field_validator("provider")
    @classmethod
    def _validate_provider(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized != SUPPORTED_PROVIDER:
            raise InvalidAcquisitionRequest("provider must be exactly 'alpaca'.")
        return normalized

    @field_validator("feed")
    @classmethod
    def _validate_feed(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in SUPPORTED_FEEDS:
            allowed = ", ".join(SUPPORTED_FEEDS)
            raise InvalidAcquisitionRequest(f"feed must be explicit and one of: {allowed}.")
        return normalized

    @field_validator("adjustment_mode")
    @classmethod
    def _validate_adjustment_mode(cls, value: str) -> str:
        normalized = _normalize_adjustment(value)
        if normalized not in SUPPORTED_ADJUSTMENT_MODES:
            raise InvalidAcquisitionRequest("adjustment_mode must be 'raw' or 'all-adjusted'.")
        return normalized

    @field_validator("data_root")
    @classmethod
    def _validate_data_root(cls, value: Path) -> Path:
        if value.is_absolute():
            raise UnsafeDataPath("data_root must be a repository-relative path.")
        if any(part == ".." for part in value.parts):
            raise UnsafeDataPath("data_root must not contain '..' path traversal.")
        if not str(value).strip():
            raise UnsafeDataPath("data_root must not be blank.")
        return value

    @model_validator(mode="after")
    def _validate_request(self) -> Self:
        if self.start_date > self.end_date:
            raise InvalidAcquisitionRequest("start_date must not be after end_date.")
        today = _today_utc()
        if self.start_date > today or self.end_date > today:
            raise InvalidAcquisitionRequest("future acquisition ranges are rejected.")
        if not self.acknowledge_provider_terms:
            raise InvalidAcquisitionRequest(
                "provider terms must be acknowledged explicitly before acquisition."
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
            "asof": self.asof.isoformat() if self.asof else None,
        }


class MarketDataCredentials(BaseModel):
    """Market-data credentials kept separate from paper-trading credentials."""

    model_config = ConfigDict(frozen=True)

    api_key: str = Field(repr=False)
    secret_key: str = Field(repr=False)

    @field_validator("api_key", "secret_key")
    @classmethod
    def _validate_secret_text(cls, value: str) -> str:
        if not value.strip():
            raise MissingMarketDataCredentials("market-data credentials are required.")
        return value

    @classmethod
    def from_environment(cls) -> Self:
        api_key = os.environ.get("ALPACA_MARKET_DATA_API_KEY", "")
        secret_key = os.environ.get("ALPACA_MARKET_DATA_SECRET_KEY", "")
        if not api_key.strip() or not secret_key.strip():
            raise MissingMarketDataCredentials(
                "set ALPACA_MARKET_DATA_API_KEY and ALPACA_MARKET_DATA_SECRET_KEY."
            )
        return cls(api_key=api_key, secret_key=secret_key)


class ProviderIdentity(BaseModel):
    """Provider identity captured in snapshots and manifests."""

    model_config = ConfigDict(frozen=True)

    provider_name: str
    api_version: str
    sdk_package_name: str
    sdk_package_version: str
    feed: str
    adjustment_mode: str
    access_method: str


class PaginationMetadata(BaseModel):
    """Sanitized pagination metadata for one provider page."""

    model_config = ConfigDict(frozen=True)

    page_number: int = Field(ge=1)
    request_page_token: str | None = None
    next_page_token: str | None = None
    row_count: int = Field(ge=0)


class RawAcquisitionSnapshot(BaseModel):
    """Credential-free raw provider response snapshot."""

    model_config = ConfigDict(frozen=True)

    sanitized_request: dict[str, str | None]
    provider_identity: ProviderIdentity
    retrieval_timestamp: datetime
    source_timezone: str
    provider_response_payload: dict[str, Any]
    pagination: tuple[PaginationMetadata, ...]
    response_page_count: int = Field(ge=1)
    corporate_actions_payload: dict[str, Any] | None = None

    @field_validator("retrieval_timestamp")
    @classmethod
    def _validate_retrieval_timestamp(cls, value: datetime) -> datetime:
        return _require_aware_utc(value, field_name="retrieval_timestamp")


class CanonicalDailyBar(BaseModel):
    """Deterministic canonical SPY daily bar used for Phase 1 storage."""

    model_config = ConfigDict(frozen=True)

    symbol: Literal["SPY"]
    session_date: date
    open: str
    high: str
    low: str
    close: str
    adjusted_close: str | None = None
    volume: int = Field(ge=0)
    provider: str
    feed: str
    adjustment_mode: str
    source_timezone: str
    canonical_timezone: str
    lineage_identifier: str


class MissingSessionSummary(BaseModel):
    """Summary of expected sessions absent from the canonical dataset."""

    model_config = ConfigDict(frozen=True)

    count: int = Field(ge=0)
    sessions: tuple[str, ...] = ()


class GeneratedFileLocations(BaseModel):
    """Relative generated artifact paths recorded in the manifest."""

    model_config = ConfigDict(frozen=True)

    raw_snapshot_path: str
    canonical_path: str
    manifest_path: str


class DatasetManifest(BaseModel):
    """Deterministic manifest for a canonical Phase 1 dataset."""

    model_config = ConfigDict(frozen=True)

    dataset_id: str
    symbol: Literal["SPY"]
    provider: str
    provider_api_version: str
    sdk_package_name: str
    sdk_package_version: str
    feed: str
    timeframe: Literal["1Day"]
    requested_start_date: date
    requested_end_date: date
    actual_first_session: date
    actual_last_session: date
    retrieval_timestamp: datetime
    adjustment_mode: str
    canonical_schema_version: str
    manifest_schema_version: str
    row_count: int = Field(gt=0)
    expected_session_count: int = Field(gt=0)
    missing_session_summary: MissingSessionSummary
    duplicate_session_count: int = Field(ge=0)
    incomplete_session_policy: str
    corporate_action_policy: str
    corporate_action_evidence: str
    source_checksum: str
    canonical_content_checksum: str
    artifact_checksum: str
    raw_artifact_checksum: str
    manifest_artifact_checksum: str | None = None
    relevant_configuration: dict[str, str | int | bool | None]
    lineage_identifier: str
    git_commit_sha: str | None
    python_version: str
    package_version: str
    dependency_versions: dict[str, str]
    licensing_classification: str
    generated_file_locations: GeneratedFileLocations

    @field_validator("retrieval_timestamp")
    @classmethod
    def _validate_retrieval_timestamp(cls, value: datetime) -> datetime:
        return _require_aware_utc(value, field_name="retrieval_timestamp")


@dataclass(frozen=True)
class AcquisitionArtifacts:
    """Result returned by explicit acquisition after durable artifact handling."""

    manifest: DatasetManifest
    raw_snapshot: RawAcquisitionSnapshot
    canonical_bars: tuple[CanonicalDailyBar, ...]
    created_files: tuple[Path, ...]
    reused_existing: bool


class HistoricalMarketDataProviderProtocol(Protocol):
    """Structural protocol without importing typing.Protocol in runtime checks."""

    def fetch_raw_snapshot(
        self,
        request: AcquisitionRequest,
        *,
        credentials: MarketDataCredentials,
        clock: Clock,
    ) -> RawAcquisitionSnapshot: ...


def dependency_versions() -> dict[str, str]:
    package_names = (
        "alpaca-py",
        "exchange-calendars",
        "pandas",
        "pydantic",
        "pydantic-settings",
    )
    versions: dict[str, str] = {}
    for package_name in package_names:
        try:
            versions[package_name] = version(package_name)
        except PackageNotFoundError:
            versions[package_name] = "not-installed"
    return versions


def current_git_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    commit = result.stdout.strip()
    return commit or None


def runtime_lineage() -> tuple[str | None, str, str, dict[str, str]]:
    return (
        current_git_commit(),
        platform.python_version(),
        __version__,
        dependency_versions(),
    )


def require_market_data_credentials() -> MarketDataCredentials:
    return MarketDataCredentials.from_environment()


def validate_request_without_client_construction(
    *,
    symbol: str,
    start_date: date,
    end_date: date,
    timeframe: str,
    provider: str,
    feed: str,
    adjustment_mode: str,
    data_root: Path,
    acknowledge_provider_terms: bool,
    asof: date | None = None,
) -> AcquisitionRequest:
    return AcquisitionRequest(
        symbol=symbol,
        start_date=start_date,
        end_date=end_date,
        timeframe=timeframe,
        provider=provider,
        feed=feed,
        adjustment_mode=adjustment_mode,
        data_root=data_root,
        acknowledge_provider_terms=acknowledge_provider_terms,
        asof=asof,
    )


def provider_identity_from_mapping(mapping: Mapping[str, str]) -> ProviderIdentity:
    return ProviderIdentity(
        provider_name=mapping["provider_name"],
        api_version=mapping["api_version"],
        sdk_package_name=mapping["sdk_package_name"],
        sdk_package_version=mapping["sdk_package_version"],
        feed=mapping["feed"],
        adjustment_mode=mapping["adjustment_mode"],
        access_method=mapping["access_method"],
    )


def acquisition_report_lines(artifacts: AcquisitionArtifacts) -> Sequence[str]:
    manifest = artifacts.manifest
    return (
        f"dataset_id={manifest.dataset_id}",
        (
            "actual_session_range="
            f"{manifest.actual_first_session.isoformat()}..{manifest.actual_last_session.isoformat()}"
        ),
        f"row_count={manifest.row_count}",
        f"source_checksum={manifest.source_checksum}",
        f"canonical_checksum={manifest.canonical_content_checksum}",
        f"manifest_path={manifest.generated_file_locations.manifest_path}",
    )
