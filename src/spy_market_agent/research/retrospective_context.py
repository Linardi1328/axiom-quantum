"""AQ-P3C-R retrospective QQQ/IWM close binding.

Retrospective data is never point-in-time evidence. A series is nothing more than a verified
pair of (v2 manifest, canonical CSV bytes). It stores no derived values: sessions, closes
and identities are re-derived from those two inputs on every access and again at the
computation boundary, so ``dataclasses.replace`` or attribute tampering cannot substitute
values while keeping an artifact identity. Validation mirrors the v2 acquisition verifier
(reusing its pure decimal/volume helpers). This binder checks internal consistency only; it
does not replace ``verify_context_artifacts`` (exact code/runtime lineage, raw snapshot,
paths), which a future local adapter must run first. No file, network, credential, broker or
provider access occurs here.
"""

from __future__ import annotations

import csv
import io
import math
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Final, Literal, NamedTuple

from spy_market_agent.benchmark.artifacts import sha256_json
from spy_market_agent.market_data.calendar import XNYSCalendar
from spy_market_agent.market_data.context_acquisition import _canonical_csv
from spy_market_agent.market_data.context_contracts import (
    CONTEXT_ACQUISITION_SCHEMA,
    CONTEXT_BAR_HEADER,
    MAX_BYTES,
    MAX_ROWS,
    ContextAcquisitionManifest,
    ContextAcquisitionRequest,
    ContextBar,
)
from spy_market_agent.market_data.context_transport import (
    decimal_price,
    decimal_text,
    integer_volume,
)
from spy_market_agent.market_data.errors import CanonicalizationFailure
from spy_market_agent.market_data.manifest import canonical_json_bytes, sha256_bytes
from spy_market_agent.research.qqq_iwm_policy import require_sessions

DATA_CLASS: Final = "retrospective_corrected"
SERIES_CONTRACT_ID: Final = "aq-p3cr-retrospective-context-series-v2"
MAX_RETRIEVAL_SECONDS: Final = 120.0  # same bound as the v2 verifier


class RetrospectiveInputError(ValueError):
    """Fixed-text rejection; never echoes input values."""


def _reject(code: str) -> RetrospectiveInputError:
    return RetrospectiveInputError(code)


class DerivedContext(NamedTuple):
    """Everything below is recomputed from manifest + canonical bytes, never stored."""

    manifest: ContextAcquisitionManifest
    sessions: tuple[date, ...]
    closes: tuple[float, ...]
    effective_data_checksum: str


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


def derive_context(manifest: ContextAcquisitionManifest, canonical_bytes: bytes) -> DerivedContext:
    """Re-derive and validate every value; fail closed on any inconsistency."""
    try:
        return _derive(manifest, canonical_bytes)
    except RetrospectiveInputError:
        raise
    except Exception:
        raise _reject("invalid_context_artifact") from None


def _canonical_price(text: str, name: str) -> Decimal:
    try:
        number = decimal_price(text, name)
        canonical = decimal_text(text, name)
    except CanonicalizationFailure:
        raise _reject("invalid_price_text") from None
    if canonical != text:
        raise _reject("noncanonical_price_text")
    return number


def _derive(manifest: ContextAcquisitionManifest, canonical_bytes: bytes) -> DerivedContext:
    if not isinstance(manifest, ContextAcquisitionManifest) or not isinstance(
        canonical_bytes, bytes
    ):
        raise _reject("invalid_context_artifact_types")
    if not 0 < len(canonical_bytes) <= MAX_BYTES:
        raise _reject("canonical_bytes_out_of_bounds")
    # model_construct()/mutation cannot bypass strict validation.
    manifest = ContextAcquisitionManifest.model_validate(manifest.model_dump(mode="python"))
    if manifest.manifest_sha256 != sha256_bytes(
        canonical_json_bytes(manifest.model_copy(update={"manifest_sha256": None}))
    ):
        raise _reject("manifest_checksum_mismatch")
    if sha256_bytes(canonical_bytes) != manifest.canonical_sha256:
        raise _reject("canonical_checksum_mismatch")
    try:
        text = canonical_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raise _reject("invalid_canonical_layout") from None
    rows = list(csv.reader(io.StringIO(text, newline="")))
    if (
        not rows
        or tuple(rows[0]) != CONTEXT_BAR_HEADER
        or any(len(r) != 7 for r in rows)
        or not 1 < len(rows) <= MAX_ROWS + 1
    ):
        raise _reject("invalid_canonical_layout")
    bars: list[ContextBar] = []
    closes: list[float] = []
    for symbol, session, open_, high, low, close, volume in rows[1:]:
        if symbol != manifest.symbol:
            raise _reject("invalid_canonical_row")
        parsed = date.fromisoformat(session)
        if parsed.isoformat() != session:
            raise _reject("noncanonical_session_text")
        o, h, low_, c = (
            _canonical_price(value, name)
            for value, name in ((open_, "open"), (high, "high"), (low, "low"), (close, "close"))
        )
        if low_ > min(o, c) or h < max(o, c) or low_ > h:
            raise _reject("inconsistent_ohlc")
        try:
            volume_value = integer_volume(volume)
        except CanonicalizationFailure:
            raise _reject("invalid_volume") from None
        if str(volume_value) != volume:
            raise _reject("noncanonical_volume_text")
        bars.append(
            ContextBar(
                symbol=manifest.symbol,
                session=parsed,
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=volume_value,
            )
        )
        value = float(close)
        if not math.isfinite(value) or value <= 0.0:
            raise _reject("invalid_close_value")
        closes.append(value)
    parsed_bars = tuple(bars)
    if _canonical_csv(parsed_bars) != canonical_bytes:
        raise _reject("noncanonical_bytes")
    sessions = tuple(bar.session for bar in parsed_bars)
    require_sessions(sessions)
    calendar = XNYSCalendar()
    # Exact requested XNYS coverage, as the acquisition verifier requires.
    if sessions != calendar.sessions_between(manifest.start_date, manifest.end_date):
        raise _reject("sessions_differ_from_requested_xnys_coverage")
    if not calendar.is_session_complete(sessions[-1], as_of=manifest.retrieval_completed_at):
        raise _reject("final_session_incomplete_at_retrieval")
    if (
        len(sessions) != manifest.row_count
        or (sessions[0], sessions[-1]) != (manifest.first_session, manifest.last_session)
        or (manifest.retrieval_completed_at - manifest.request_started_at).total_seconds()
        > MAX_RETRIEVAL_SECONDS
    ):
        raise _reject("manifest_session_mismatch")
    if (
        manifest.provider != "alpaca"
        or manifest.feed != "sip"
        or manifest.adjustment_mode != "all"
        or manifest.timeframe != "1Day"
        or manifest.execution_authority != "none"
        or manifest.point_in_time_status != "UNVERIFIED"
        or manifest.usage_rights_status != "UNVERIFIED"
    ):
        raise _reject("source_policy_mismatch")
    if derive_context_dataset_id(manifest, manifest.canonical_sha256) != manifest.dataset_id:
        raise _reject("dataset_identity_mismatch")
    effective = sha256_json(
        {
            "symbol": manifest.symbol,
            "sessions": [s.isoformat() for s in sessions],
            "closes": [value.hex() for value in closes],
        }
    )
    return DerivedContext(manifest, sessions, tuple(closes), effective)


@dataclass(frozen=True, slots=True)
class RetrospectiveContextSeries:
    """A verified (manifest, canonical bytes) pair; values are always re-derived.

    Construct with :func:`bind_context_series`. Constructing or ``replace``-ing validates the
    pair again, and the evaluator re-derives from the pair once more, so no field can carry a
    stale or substituted value under an unchanged artifact identity.
    """

    manifest: ContextAcquisitionManifest
    canonical_bytes: bytes

    def __post_init__(self) -> None:
        derive_context(self.manifest, self.canonical_bytes)

    def derived(self) -> DerivedContext:
        return derive_context(self.manifest, self.canonical_bytes)

    @property
    def symbol(self) -> Literal["QQQ", "IWM"]:
        return self.derived().manifest.symbol

    @property
    def dataset_id(self) -> str:
        return self.derived().manifest.dataset_id

    @property
    def canonical_sha256(self) -> str:
        return self.derived().manifest.canonical_sha256

    @property
    def manifest_sha256(self) -> str:
        return str(self.derived().manifest.manifest_sha256)

    @property
    def sessions(self) -> tuple[date, ...]:
        return self.derived().sessions

    @property
    def closes(self) -> tuple[float, ...]:
        return self.derived().closes

    @property
    def data_class(self) -> Literal["retrospective_corrected"]:
        return DATA_CLASS

    @property
    def point_in_time_status(self) -> Literal["UNVERIFIED"]:
        return "UNVERIFIED"

    @property
    def usage_rights_status(self) -> Literal["UNVERIFIED"]:
        return "UNVERIFIED"

    @property
    def execution_authority(self) -> Literal["none"]:
        return "none"


def bind_context_series(
    manifest: ContextAcquisitionManifest, canonical_bytes: bytes
) -> RetrospectiveContextSeries:
    """Validate a (manifest, canonical bytes) pair and wrap it; fail closed."""
    return RetrospectiveContextSeries(manifest, canonical_bytes)
