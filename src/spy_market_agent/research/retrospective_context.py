"""AQ-P3C-R retrospective QQQ/IWM close binding.

Retrospective data is never point-in-time evidence. A series can only be created from the
canonical CSV bytes of a v2 context acquisition plus its manifest, and every value is
re-derived from those bytes. Caller-supplied close arrays, verification booleans and
rights claims are not accepted. This binder checks internal consistency only; it does not
replace ``verify_context_artifacts`` (exact code/runtime lineage), which a future local
adapter must run first. No file, network, credential, broker or provider access occurs here.
"""

from __future__ import annotations

import csv
import io
import math
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Final, Literal

from spy_market_agent.market_data.context_contracts import (
    CONTEXT_ACQUISITION_SCHEMA,
    CONTEXT_BAR_HEADER,
    ContextAcquisitionManifest,
    ContextAcquisitionRequest,
    ContextBar,
)
from spy_market_agent.market_data.manifest import canonical_json_bytes, sha256_bytes
from spy_market_agent.research.qqq_iwm_policy import require_sessions

DATA_CLASS: Final = "retrospective_corrected"
SERIES_CONTRACT_ID: Final = "aq-p3cr-retrospective-context-series-v1"
_ORIGIN: Final = object()
_DECIMAL = re.compile(r"[0-9]{1,24}(\.[0-9]{1,18})?\Z")


class RetrospectiveInputError(ValueError):
    """Fixed-text rejection; never echoes input values."""


def _reject(code: str) -> RetrospectiveInputError:
    return RetrospectiveInputError(code)


@dataclass(frozen=True, slots=True)
class RetrospectiveContextSeries:
    """Immutable daily closes derived from canonical bytes. Use :func:`bind_context_series`."""

    symbol: Literal["QQQ", "IWM"]
    dataset_id: str
    canonical_sha256: str
    manifest_sha256: str
    sessions: tuple[date, ...]
    closes: tuple[float, ...]
    data_class: Literal["retrospective_corrected"] = DATA_CLASS
    point_in_time_status: Literal["UNVERIFIED"] = "UNVERIFIED"
    usage_rights_status: Literal["UNVERIFIED"] = "UNVERIFIED"
    execution_authority: Literal["none"] = "none"
    _origin: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._origin is not _ORIGIN:
            raise _reject("series_must_be_bound_from_canonical_bytes")


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


def _price(text: str) -> Decimal:
    if not isinstance(text, str) or not _DECIMAL.fullmatch(text):
        raise _reject("invalid_price_text")
    try:
        value = Decimal(text)
    except InvalidOperation:
        raise _reject("invalid_price_text") from None
    if not value.is_finite() or value <= 0:
        raise _reject("invalid_price_value")
    return value


def derive_context_dataset_id(manifest: ContextAcquisitionManifest, canonical_sha: str) -> str:
    """Mirror the v2 acquisition identity rule without importing provider code."""
    request = ContextAcquisitionRequest(
        symbol=manifest.symbol,
        start_date=manifest.start_date,
        end_date=manifest.end_date,
        acknowledge_provider_terms=True,
    )
    identity = {
        "schema": CONTEXT_ACQUISITION_SCHEMA,
        "request": request.sanitized_parameters(),
        "canonical_sha256": canonical_sha,
        "lineage": manifest.lineage,
    }
    return f"aq-ctx-v2-{sha256_bytes(canonical_json_bytes(identity))[:24]}"


def bind_context_series(
    manifest: ContextAcquisitionManifest, canonical_bytes: bytes
) -> RetrospectiveContextSeries:
    """Derive a series strictly from canonical bytes; fail closed on any inconsistency."""
    try:
        return _bind(manifest, canonical_bytes)
    except RetrospectiveInputError:
        raise
    except Exception:
        raise _reject("invalid_context_artifact") from None


def _bind(
    manifest: ContextAcquisitionManifest, canonical_bytes: bytes
) -> RetrospectiveContextSeries:
    if not isinstance(manifest, ContextAcquisitionManifest) or not isinstance(
        canonical_bytes, bytes
    ):
        raise _reject("invalid_context_artifact_types")
    # model_construct()/mutation cannot bypass strict validation.
    manifest = ContextAcquisitionManifest.model_validate(manifest.model_dump(mode="python"))
    if manifest.manifest_sha256 != sha256_bytes(
        canonical_json_bytes(manifest.model_copy(update={"manifest_sha256": None}))
    ):
        raise _reject("manifest_checksum_mismatch")
    if sha256_bytes(canonical_bytes) != manifest.canonical_sha256:
        raise _reject("canonical_checksum_mismatch")
    rows = list(csv.reader(io.StringIO(canonical_bytes.decode("utf-8"), newline="")))
    if not rows or tuple(rows[0]) != CONTEXT_BAR_HEADER or any(len(r) != 7 for r in rows):
        raise _reject("invalid_canonical_layout")
    bars: list[ContextBar] = []
    closes: list[float] = []
    for symbol, session, open_, high, low, close, volume in rows[1:]:
        if symbol != manifest.symbol or not volume.isascii() or not volume.isdigit():
            raise _reject("invalid_canonical_row")
        o, h, low_, c = (_price(x) for x in (open_, high, low, close))
        if low_ > min(o, c) or h < max(o, c):
            raise _reject("inconsistent_ohlc")
        bars.append(
            ContextBar(
                symbol=symbol,
                session=date.fromisoformat(session),
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=int(volume),
            )
        )
        value = float(c)
        if not math.isfinite(value) or value <= 0.0:
            raise _reject("invalid_close_value")
        closes.append(value)
    parsed = tuple(bars)
    if _canonical_csv(parsed) != canonical_bytes:
        raise _reject("noncanonical_bytes")
    sessions = tuple(bar.session for bar in parsed)
    require_sessions(sessions)
    if (
        not sessions
        or len(sessions) != manifest.row_count
        or (sessions[0], sessions[-1]) != (manifest.first_session, manifest.last_session)
        or (manifest.start_date > sessions[0] or manifest.end_date < sessions[-1])
    ):
        raise _reject("manifest_session_mismatch")
    if (
        manifest.provider != "alpaca"
        or manifest.feed != "sip"
        or manifest.adjustment_mode != "all"
        or manifest.timeframe != "1Day"
        or manifest.execution_authority != "none"
    ):
        raise _reject("source_policy_mismatch")
    if derive_context_dataset_id(manifest, manifest.canonical_sha256) != manifest.dataset_id:
        raise _reject("dataset_identity_mismatch")
    return RetrospectiveContextSeries(
        symbol=manifest.symbol,
        dataset_id=manifest.dataset_id,
        canonical_sha256=manifest.canonical_sha256,
        manifest_sha256=str(manifest.manifest_sha256),
        sessions=sessions,
        closes=tuple(closes),
        _origin=_ORIGIN,
    )
