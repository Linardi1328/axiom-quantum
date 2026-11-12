"""Explicit context quarantine v2. No research admission, CLI, scheduler or trading.

Only internal integrity is verified. Neither timestamps nor checksums establish
historical availability, licensing, or independent provider authenticity.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol, cast
from zoneinfo import ZoneInfo

import pandas as pd
from pydantic import ValidationError

from spy_market_agent.market_data.acquisition import (
    Clock,
    MarketDataCredentials,
    RawAcquisitionSnapshot,
    StockBarsRequestProtocol,
)
from spy_market_agent.market_data.calendar import MARKET_TIMEZONE, XNYSCalendar
from spy_market_agent.market_data.context_contracts import (
    CONTEXT_ACQUISITION_SCHEMA,
    CONTEXT_BAR_HEADER,
    RAW_SCHEMA,
    ContextBar,
    ContextLineage,
    aware,
    context_lineage,
    validate_request,
)
from spy_market_agent.market_data.context_contracts import (
    ContextAcquisitionArtifacts as ContextAcquisitionArtifacts,
)
from spy_market_agent.market_data.context_contracts import (
    ContextAcquisitionManifest as ContextAcquisitionManifest,
)
from spy_market_agent.market_data.context_contracts import (
    ContextAcquisitionRequest as ContextAcquisitionRequest,
)
from spy_market_agent.market_data.context_storage import ContextStore
from spy_market_agent.market_data.context_transport import (
    context_permit,
    decimal_price,
    decimal_text,
    integer_volume,
    validate_snapshot,
)
from spy_market_agent.market_data.errors import (
    AtomicWriteFailure,
    CanonicalizationFailure,
    ChecksumMismatch,
    InvalidAcquisitionRequest,
    ManifestValidationFailure,
    MarketDataAcquisitionError,
    ProviderIncompleteResponse,
    ProviderMalformedResponse,
)
from spy_market_agent.market_data.manifest import canonical_json_bytes, sha256_bytes

CONTEXT_SYMBOLS = ("QQQ", "IWM")
CONTEXT_PIT_STATUS = "UNVERIFIED"
CONTEXT_RIGHTS_STATUS = "UNVERIFIED"


class ContextStockProvider(Protocol):
    def fetch_raw_snapshot(
        self, request: StockBarsRequestProtocol, *, credentials: MarketDataCredentials, clock: Clock
    ) -> RawAcquisitionSnapshot: ...


def _validate_and_canonicalize(
    request: ContextAcquisitionRequest, snapshot: RawAcquisitionSnapshot, *, as_of: datetime
) -> tuple[ContextBar, ...]:
    validate_snapshot(snapshot, request)
    rows: list[ContextBar] = []
    for page in snapshot.provider_response_payload["pages"]:
        for record in page["bars"][request.symbol]:
            try:
                stamp = pd.Timestamp(record["t"])
            except (ValueError, TypeError, OverflowError):
                raise CanonicalizationFailure("invalid context bar timestamp.") from None
            if pd.isna(stamp) or stamp.tzinfo is None:
                raise CanonicalizationFailure("context bar timestamp must be timezone-aware.")
            market_stamp = stamp.tz_convert(ZoneInfo(MARKET_TIMEZONE))
            if market_stamp != market_stamp.normalize():
                raise CanonicalizationFailure(
                    "context daily-bar timestamp must start at New York midnight."
                )
            # Compare exact accepted Decimal values BEFORE any serialization.
            o, h, low, close = (decimal_price(record[key], key) for key in ("o", "h", "l", "c"))
            if low > min(o, close) or h < max(o, close) or low > h:
                raise CanonicalizationFailure("context OHLC prices are inconsistent.")
            rows.append(
                ContextBar(
                    symbol=cast(Literal["QQQ", "IWM"], request.symbol),
                    session=market_stamp.date(),
                    open=decimal_text(record["o"], "open"),
                    high=decimal_text(record["h"], "high"),
                    low=decimal_text(record["l"], "low"),
                    close=decimal_text(record["c"], "close"),
                    volume=integer_volume(record["v"]),
                )
            )
    if not rows:
        raise ProviderIncompleteResponse("no context bars were returned.")
    sessions = tuple(bar.session for bar in rows)
    if sessions != tuple(sorted(set(sessions))):
        raise CanonicalizationFailure("context sessions are duplicated or out of order.")
    calendar = XNYSCalendar()
    if sessions != calendar.sessions_between(request.start_date, request.end_date):
        raise CanonicalizationFailure("context response has missing, extra or non-XNYS sessions.")
    if not calendar.is_session_complete(sessions[-1], as_of=as_of):
        raise CanonicalizationFailure("context final session is not complete at retrieval.")
    return tuple(rows)


def _canonical_csv(bars: tuple[ContextBar, ...]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
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
    return buffer.getvalue().encode()


def _context_dataset_id(
    request: ContextAcquisitionRequest, canonical_sha: str, lineage: ContextLineage
) -> str:
    identity = {
        "schema": CONTEXT_ACQUISITION_SCHEMA,
        "request": request.sanitized_parameters(),
        "canonical_sha256": canonical_sha,
        "lineage": lineage,
    }
    return f"aq-ctx-v2-{sha256_bytes(canonical_json_bytes(identity))[:24]}"


def _fetch(
    provider: ContextStockProvider,
    request: ContextAcquisitionRequest,
    credentials: MarketDataCredentials,
    clock: Clock,
) -> RawAcquisitionSnapshot:
    failure: type[Exception] | None = None
    try:
        with context_permit(request):
            result = provider.fetch_raw_snapshot(request, credentials=credentials, clock=clock)
        return result
    except Exception as exc:
        failure = (
            type(exc) if isinstance(exc, MarketDataAcquisitionError) else ProviderMalformedResponse
        )
    # Outside the except block: no raw exception retained as __context__ or __cause__.
    raise failure("context provider failed with sanitized classification.") from None


def _acquire(
    request: ContextAcquisitionRequest,
    *,
    provider: ContextStockProvider,
    credentials: MarketDataCredentials,
    clock: Clock,
    repository_root: Path | None,
) -> ContextAcquisitionArtifacts:
    started = aware(clock())
    checked = validate_request(request, started)
    store = ContextStore(repository_root)
    store.preflight(checked.symbol)
    snapshot = _fetch(provider, checked, credentials, clock)
    completed = aware(clock())
    if type(snapshot) is not RawAcquisitionSnapshot:
        raise ProviderMalformedResponse("context snapshot must use the supported raw model.")
    retrieved = aware(snapshot.retrieval_timestamp)
    if not started <= retrieved <= completed or (completed - started).total_seconds() > 120:
        raise ProviderMalformedResponse("context retrieval timestamp or elapsed budget mismatch.")
    validate_snapshot(snapshot, checked, credentials)
    bars = _validate_and_canonicalize(checked, snapshot, as_of=completed)
    raw_bytes = canonical_json_bytes(
        {"schema_version": RAW_SCHEMA, **snapshot.model_dump(mode="python")}
    )
    csv_bytes = _canonical_csv(bars)
    lineage = context_lineage()
    canonical_sha = sha256_bytes(csv_bytes)
    dataset_id = _context_dataset_id(checked, canonical_sha, lineage)
    paths = store.paths(checked.symbol, dataset_id)
    manifest = ContextAcquisitionManifest(
        schema_version=CONTEXT_ACQUISITION_SCHEMA,
        dataset_id=dataset_id,
        symbol=cast(Literal["QQQ", "IWM"], checked.symbol),
        provider="alpaca",
        feed="sip",
        adjustment_mode="all",
        timeframe="1Day",
        start_date=checked.start_date,
        end_date=checked.end_date,
        first_session=bars[0].session,
        last_session=bars[-1].session,
        row_count=len(bars),
        request_started_at=started,
        retrieval_completed_at=completed,
        provider_api_version="v2",
        provider_sdk_version=snapshot.provider_identity.sdk_package_version,
        lineage=lineage,
        raw_sha256=sha256_bytes(raw_bytes),
        canonical_sha256=canonical_sha,
        point_in_time_status="UNVERIFIED",
        usage_rights_status="UNVERIFIED",
        execution_authority="none",
        generated_paths=dict(
            zip(("raw", "canonical", "manifest"), (p.as_posix() for p in paths), strict=True)
        ),
    )
    manifest = manifest.model_copy(
        update={"manifest_sha256": sha256_bytes(canonical_json_bytes(manifest))}
    )
    payloads = (raw_bytes, csv_bytes, canonical_json_bytes(manifest))
    if any(
        secret.encode() in data
        for data in payloads
        for secret in (credentials.api_key, credentials.secret_key)
    ):
        raise ProviderMalformedResponse("context artifacts contain supplied credential material.")
    store.preflight(checked.symbol)  # recheck AFTER provider access, before publication
    created = store.write(paths, payloads)
    return ContextAcquisitionArtifacts(
        manifest=manifest,
        raw_artifact_created=created[0],
        canonical_artifact_created=created[1],
        manifest_artifact_created=created[2],
    )


def acquire_historical_context_data(
    request: ContextAcquisitionRequest,
    *,
    provider: ContextStockProvider,
    credentials: MarketDataCredentials,
    clock: Clock,
    repository_root: Path | None = None,
) -> ContextAcquisitionArtifacts:
    """Explicit, preflight-validated quarantine acquisition; no implied download approval."""
    kind: type[Exception]
    message: str
    try:
        return _acquire(
            request,
            provider=provider,
            credentials=credentials,
            clock=clock,
            repository_root=repository_root,
        )
    except MarketDataAcquisitionError as exc:
        kind, message = type(exc), str(exc)
    except (ValidationError, ValueError, TypeError):
        kind, message = InvalidAcquisitionRequest, "invalid context contract or provenance."
    except Exception:
        kind, message = AtomicWriteFailure, "context acquisition failed safely."
    raise kind(message) from None


def _verify(manifest_path: Path, repository_root: Path | None) -> ContextAcquisitionManifest:
    store = ContextStore(repository_root)
    # Validate filename syntax BEFORE reading or deriving paths from untrusted IDs.
    from spy_market_agent.market_data.context_contracts import DATASET_PATTERN

    if not manifest_path.name.endswith(".manifest.json") or not DATASET_PATTERN.fullmatch(
        manifest_path.name.removesuffix(".manifest.json")
    ):
        raise ManifestValidationFailure("invalid context manifest filename or schema namespace.")
    with store.locked(create=False):
        manifest_bytes = store.read(manifest_path)
        manifest = ContextAcquisitionManifest.model_validate_json(manifest_bytes)
        if manifest_bytes != canonical_json_bytes(
            manifest
        ) or manifest.manifest_sha256 != sha256_bytes(
            canonical_json_bytes(manifest.model_copy(update={"manifest_sha256": None}))
        ):
            raise ChecksumMismatch("context manifest checksum or canonical serialization mismatch.")
        # This version admits verification only under its exact recorded code/runtime.
        if manifest.lineage != context_lineage():
            raise ManifestValidationFailure(
                "context code or runtime lineage differs from supported verifier."
            )
        request = validate_request(
            ContextAcquisitionRequest(
                symbol=manifest.symbol,
                start_date=manifest.start_date,
                end_date=manifest.end_date,
                acknowledge_provider_terms=True,
            ),
            aware(manifest.request_started_at),
        )
        paths = store.paths(request.symbol, manifest.dataset_id)
        relative = Path(*store._parts(manifest_path))
        if relative != paths[2] or manifest.generated_paths != dict(
            zip(("raw", "canonical", "manifest"), (p.as_posix() for p in paths), strict=True)
        ):
            raise ManifestValidationFailure("context artifact paths do not match identity.")
        raw_bytes, canonical_bytes = store.read(paths[0]), store.read(paths[1])
        if (
            sha256_bytes(raw_bytes) != manifest.raw_sha256
            or sha256_bytes(canonical_bytes) != manifest.canonical_sha256
        ):
            raise ChecksumMismatch("context raw or canonical artifact checksum mismatch.")
        import json

        raw = json.loads(raw_bytes)
        if not isinstance(raw, dict) or raw.pop("schema_version", None) != RAW_SCHEMA:
            raise ManifestValidationFailure("unsupported context raw schema.")
        if set(raw) != set(RawAcquisitionSnapshot.model_fields):
            raise ManifestValidationFailure("context raw snapshot has unexpected fields.")
        snapshot = RawAcquisitionSnapshot.model_validate_json(canonical_json_bytes(raw))
        validate_snapshot(snapshot, request)
        if (
            canonical_json_bytes(
                {"schema_version": RAW_SCHEMA, **snapshot.model_dump(mode="python")}
            )
            != raw_bytes
        ):
            raise ManifestValidationFailure("context raw snapshot is not canonical.")
        if (
            not aware(manifest.request_started_at)
            <= aware(snapshot.retrieval_timestamp)
            <= aware(manifest.retrieval_completed_at)
            or (manifest.retrieval_completed_at - manifest.request_started_at).total_seconds() > 120
            or snapshot.provider_identity.sdk_package_version != manifest.provider_sdk_version
        ):
            raise ManifestValidationFailure("context retrieval time or SDK mismatch.")
        bars = _validate_and_canonicalize(request, snapshot, as_of=manifest.retrieval_completed_at)
        if (
            _canonical_csv(bars) != canonical_bytes
            or manifest.row_count != len(bars)
            or manifest.first_session != bars[0].session
            or manifest.last_session != bars[-1].session
            or _context_dataset_id(request, sha256_bytes(canonical_bytes), manifest.lineage)
            != manifest.dataset_id
        ):
            raise ManifestValidationFailure("context canonical bars or dataset identity mismatch.")
        return manifest


def verify_context_artifacts(
    manifest_path: Path, *, repository_root: Path | None = None
) -> ContextAcquisitionManifest:
    """Check v2 internal integrity, never independent source authenticity or rights."""
    kind: type[Exception]
    message: str
    try:
        return _verify(manifest_path, repository_root)
    except MarketDataAcquisitionError as exc:
        kind, message = type(exc), str(exc)
    except Exception:
        kind, message = (
            ManifestValidationFailure,
            "invalid context artifact contract or provenance.",
        )
    raise kind(message) from None
