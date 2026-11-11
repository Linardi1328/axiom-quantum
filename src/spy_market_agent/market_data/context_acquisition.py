"""Explicit QQQ/IWM research context acquisition, isolated from frozen SPY datasets.

A completed historical download is NOT evidence of point-in-time observability:
its availability, adjustment revisions and usage rights remain unverified.
No CLI, scheduler, model, protected-test or broker integration is provided.
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Final, Literal, Protocol, Self, cast
from zoneinfo import ZoneInfo

import pandas as pd
from pydantic import (
    BaseModel,
    ConfigDict,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)

from spy_market_agent.market_data.acquisition import (
    Clock,
    MarketDataCredentials,
    RawAcquisitionSnapshot,
    StockBarsRequestProtocol,
    utc_now,
)
from spy_market_agent.market_data.calendar import MARKET_TIMEZONE, XNYSCalendar
from spy_market_agent.market_data.errors import (
    CanonicalizationFailure,
    ChecksumMismatch,
    InvalidAcquisitionRequest,
    ManifestValidationFailure,
    ProviderIncompleteResponse,
    ProviderMalformedResponse,
    UnsafeDataPath,
)
from spy_market_agent.market_data.manifest import canonical_json_bytes, sha256_bytes
from spy_market_agent.market_data.storage import (
    DatasetArtifactPaths,
    DatasetStore,
    raw_snapshot_json_bytes,
)

CONTEXT_ACQUISITION_SCHEMA: Final = "aq-qqq-iwm-context-acquisition-v1"
CONTEXT_SYMBOLS = ("QQQ", "IWM")
CONTEXT_BAR_HEADER = ("symbol", "session", "open", "high", "low", "close", "volume")
CONTEXT_PIT_STATUS: Final = "UNVERIFIED"
CONTEXT_RIGHTS_STATUS: Final = "UNVERIFIED"


class ContextAcquisitionRequest(BaseModel):
    """One explicitly acknowledged historical SIP/all-adjusted ETF request."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    start_date: date
    end_date: date
    timeframe: str = "1Day"
    provider: str = "alpaca"
    feed: str = "sip"
    adjustment_mode: str = "all"
    data_root: Path = Path("data")
    acknowledge_provider_terms: bool = False
    asof: date | None = None

    @field_validator("symbol")
    @classmethod
    def _validate_symbol(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in CONTEXT_SYMBOLS:
            raise InvalidAcquisitionRequest("context acquisition supports exactly QQQ or IWM.")
        return normalized

    @field_validator("timeframe", "provider", "feed", "adjustment_mode")
    @classmethod
    def _validate_policy(cls, value: str, info: ValidationInfo) -> str:
        normalized = value.strip().lower()
        required = {
            "timeframe": "1day",
            "provider": "alpaca",
            "feed": "sip",
            "adjustment_mode": "all",
        }[cast(str, info.field_name)]
        if normalized != required:
            raise InvalidAcquisitionRequest(f"context {info.field_name} must be {required}.")
        return "1Day" if info.field_name == "timeframe" else normalized

    @field_validator("data_root")
    @classmethod
    def _validate_root(cls, value: Path) -> Path:
        # The repository ignores data/raw, data/canonical and data/manifests.
        # Do not allow licensed context files under a committable directory.
        if value != Path("data"):
            raise UnsafeDataPath("context data_root must be exactly data/ (ignored).")
        return value

    @model_validator(mode="after")
    def _validate_dates_and_terms(self) -> Self:
        if self.start_date > self.end_date or self.end_date >= utc_now().date():
            raise InvalidAcquisitionRequest("context dates must be historical and ordered.")
        if self.asof is not None:
            raise InvalidAcquisitionRequest(
                "context acquisition cannot silently remap tickers via asof."
            )
        if not self.acknowledge_provider_terms:
            raise InvalidAcquisitionRequest("explicit provider-terms acknowledgement is required.")
        return self

    def sanitized_parameters(self) -> dict[str, str | None]:
        """Match the established credential-free Alpaca request audit format."""
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


class ContextStockProvider(Protocol):
    """Reuses the bounded, paginated Alpaca stock-bar provider interface."""

    def fetch_raw_snapshot(
        self,
        request: StockBarsRequestProtocol,
        *,
        credentials: MarketDataCredentials,
        clock: Clock,
    ) -> RawAcquisitionSnapshot:
        """Fetch one explicit market-data snapshot, not a paper-account action."""
        ...


class ContextBar(BaseModel):
    """One complete XNYS ETF session, with no inferred as-of rights."""

    model_config = ConfigDict(frozen=True)

    symbol: Literal["QQQ", "IWM"]
    session: date
    open: str
    high: str
    low: str
    close: str
    volume: int


class ContextAcquisitionManifest(BaseModel):
    """Artifact identity with explicit NOT-YET-ELIGIBLE research flags."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["aq-qqq-iwm-context-acquisition-v1"]
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
    retrieval_timestamp: datetime
    provider_api_version: str
    provider_sdk_version: str
    raw_sha256: str
    canonical_sha256: str
    manifest_sha256: str | None = None
    point_in_time_status: Literal["UNVERIFIED"]
    usage_rights_status: Literal["UNVERIFIED"]
    execution_authority: Literal["none"]
    generated_paths: dict[str, str]


class ContextAcquisitionArtifacts(BaseModel):
    """Saved context artifact manifest; not a market-data approval record."""

    model_config = ConfigDict(frozen=True)

    manifest: ContextAcquisitionManifest
    raw_artifact_created: bool
    canonical_artifact_created: bool
    manifest_artifact_created: bool


def _decimal_text(value: object, name: str) -> str:
    if isinstance(value, bool):
        raise CanonicalizationFailure(f"invalid {name} in context bar.")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise CanonicalizationFailure(f"invalid {name} in context bar.") from exc
    if not number.is_finite() or number <= 0:
        raise CanonicalizationFailure(f"nonpositive or nonfinite {name} in context bar.")
    return format(number.normalize(), "f")


def _validate_and_canonicalize(
    request: ContextAcquisitionRequest, snapshot: RawAcquisitionSnapshot, *, as_of: datetime
) -> tuple[ContextBar, ...]:
    if snapshot.sanitized_request != request.sanitized_parameters():
        raise ProviderMalformedResponse("context provider request identity mismatch.")
    identity = snapshot.provider_identity
    if (
        identity.provider_name.lower() != "alpaca"
        or identity.feed.lower() != request.feed
        or identity.adjustment_mode.lower() != request.adjustment_mode
    ):
        raise ProviderMalformedResponse("context provider identity mismatch.")
    pages = snapshot.provider_response_payload.get("pages")
    if not isinstance(pages, list) or not pages or len(pages) != snapshot.response_page_count:
        raise ProviderMalformedResponse("missing or inconsistent context provider pages.")

    rows: list[ContextBar] = []
    for page in pages:
        if not isinstance(page, dict) or not isinstance(page.get("bars"), dict):
            raise ProviderMalformedResponse("context page must contain bars by symbol.")
        bars_by_symbol = page["bars"]
        if set(bars_by_symbol) - {request.symbol}:
            raise ProviderMalformedResponse("context page contains an unexpected symbol.")
        bar_list = bars_by_symbol.get(request.symbol, [])
        if not isinstance(bar_list, list):
            raise ProviderMalformedResponse("context symbol bars must be a list.")
        for record in bar_list:
            if not isinstance(record, dict) or not {"t", "o", "h", "l", "c", "v"} <= record.keys():
                raise ProviderMalformedResponse("context OHLCV bar is incomplete.")
            try:
                stamp = pd.Timestamp(record["t"])
            except (ValueError, TypeError, OverflowError) as exc:
                raise CanonicalizationFailure("invalid context bar timestamp.") from exc
            if pd.isna(stamp) or stamp.tzinfo is None:
                raise CanonicalizationFailure("context bar timestamp must be timezone-aware.")
            market_stamp = stamp.tz_convert(ZoneInfo(MARKET_TIMEZONE))
            if market_stamp != market_stamp.normalize():
                raise CanonicalizationFailure(
                    "context daily-bar timestamp must start at New York midnight."
                )
            session = market_stamp.date()
            try:
                volume = record["v"]
                if isinstance(volume, bool) or not str(volume).isdigit():
                    raise ValueError("invalid volume")
                parsed_volume = int(volume)
            except (TypeError, ValueError, OverflowError) as exc:
                raise CanonicalizationFailure("invalid context bar volume.") from exc
            open_price = _decimal_text(record["o"], "open")
            high_price = _decimal_text(record["h"], "high")
            low_price = _decimal_text(record["l"], "low")
            close_price = _decimal_text(record["c"], "close")
            o, h, low, close = map(Decimal, (open_price, high_price, low_price, close_price))
            if low > min(o, close) or h < max(o, close) or low > h:
                raise CanonicalizationFailure("context OHLC prices are inconsistent.")
            rows.append(
                ContextBar(
                    symbol=cast(Literal["QQQ", "IWM"], request.symbol),
                    session=session,
                    open=open_price,
                    high=high_price,
                    low=low_price,
                    close=close_price,
                    volume=parsed_volume,
                )
            )
    if not rows:
        raise ProviderIncompleteResponse("no context bars were returned.")
    sessions = tuple(bar.session for bar in rows)
    if sessions != tuple(sorted(set(sessions))):
        raise CanonicalizationFailure("context sessions are duplicated or out of order.")
    calendar = XNYSCalendar()
    expected = calendar.sessions_between(request.start_date, request.end_date)
    if sessions != expected:
        raise CanonicalizationFailure("context response has missing, extra or non-XNYS sessions.")
    if not calendar.is_session_complete(sessions[-1], as_of=as_of):
        raise CanonicalizationFailure("context final session is not complete at retrieval.")
    return tuple(rows)


def _canonical_csv(bars: tuple[ContextBar, ...]) -> bytes:
    writer_buffer = io.StringIO(newline="")
    writer = csv.writer(writer_buffer, lineterminator="\n")
    writer.writerow(CONTEXT_BAR_HEADER)
    for bar in bars:
        writer.writerow(
            (
                bar.symbol,
                bar.session.isoformat(),
                bar.open,
                bar.high,
                bar.low,
                bar.close,
                bar.volume,
            )
        )
    return writer_buffer.getvalue().encode("utf-8")


def acquire_historical_context_data(
    request: ContextAcquisitionRequest,
    *,
    provider: ContextStockProvider,
    credentials: MarketDataCredentials,
    clock: Clock,
    repository_root: Path | None = None,
) -> ContextAcquisitionArtifacts:
    """Explicit owner-invoked acquisition; never automatic or paper-authorized.

    Store raw/canonical/manifest with matching checksums. Do NOT convert these
    artifacts into verified MI-2B source proofs without separate source audit.
    """
    captured = clock()
    if captured.tzinfo is None or captured.utcoffset() is None:
        raise InvalidAcquisitionRequest("context acquisition clock must be timezone-aware.")
    now = captured.astimezone(UTC)

    def acquisition_clock() -> datetime:
        return now

    snapshot = provider.fetch_raw_snapshot(
        request, credentials=credentials, clock=acquisition_clock
    )
    if snapshot.retrieval_timestamp != now:
        raise ProviderMalformedResponse(
            "context snapshot retrieval time differs from request clock."
        )
    bars = _validate_and_canonicalize(request, snapshot, as_of=now)
    raw_bytes = raw_snapshot_json_bytes(snapshot)
    csv_bytes = _canonical_csv(bars)
    raw_sha = sha256_bytes(raw_bytes)
    canonical_sha = sha256_bytes(csv_bytes)
    identity_data = {
        "schema": CONTEXT_ACQUISITION_SCHEMA,
        "symbol": request.symbol,
        "start": request.start_date,
        "end": request.end_date,
        "feed": request.feed,
        "adjustment": request.adjustment_mode,
        "canonical_sha256": canonical_sha,
    }
    dataset_id = f"aq-ctx-v1-{sha256_bytes(canonical_json_bytes(identity_data))[:24]}"
    store = DatasetStore(request.data_root, repository_root=repository_root)
    segments = ("alpaca", request.symbol, request.timeframe, request.feed, request.adjustment_mode)
    raw_path = store.data_root.joinpath("raw", *segments, f"{dataset_id}.raw.json")
    canonical_path = store.data_root.joinpath("canonical", *segments, f"{dataset_id}.canonical.csv")
    manifest_path = store.data_root.joinpath("manifests", *segments, f"{dataset_id}.manifest.json")
    paths = DatasetArtifactPaths(raw_path, canonical_path, manifest_path)
    manifest = ContextAcquisitionManifest(
        schema_version=CONTEXT_ACQUISITION_SCHEMA,
        dataset_id=dataset_id,
        symbol=cast(Literal["QQQ", "IWM"], request.symbol),
        provider="alpaca",
        feed="sip",
        adjustment_mode="all",
        timeframe="1Day",
        start_date=request.start_date,
        end_date=request.end_date,
        first_session=bars[0].session,
        last_session=bars[-1].session,
        row_count=len(bars),
        retrieval_timestamp=now,
        provider_api_version=snapshot.provider_identity.api_version,
        provider_sdk_version=snapshot.provider_identity.sdk_package_version,
        raw_sha256=raw_sha,
        canonical_sha256=canonical_sha,
        point_in_time_status=CONTEXT_PIT_STATUS,
        usage_rights_status=CONTEXT_RIGHTS_STATUS,
        execution_authority="none",
        generated_paths={
            "raw": store.relative_path(raw_path),
            "canonical": store.relative_path(canonical_path),
            "manifest": store.relative_path(manifest_path),
        },
    )
    manifest_bytes = canonical_json_bytes(manifest)
    raw_result, csv_result, manifest_result = store.write_dataset(
        paths=paths,
        raw_bytes=raw_bytes,
        canonical_bytes=csv_bytes,
        manifest_bytes=manifest_bytes,
        expected_raw_checksum=raw_sha,
        expected_canonical_checksum=canonical_sha,
        expected_manifest_checksum=sha256_bytes(manifest_bytes),
    )
    return ContextAcquisitionArtifacts(
        manifest=manifest,
        raw_artifact_created=raw_result.created,
        canonical_artifact_created=csv_result.created,
        manifest_artifact_created=manifest_result.created,
    )
