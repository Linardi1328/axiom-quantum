from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class AssetClass(StrEnum):
    EQUITY_INDEX_ETF = "equity_index_etf"
    EQUITY = "equity"
    FOREX = "forex"
    METAL = "metal"
    VOLATILITY_INDEX = "volatility_index"
    RATE = "rate"
    MACRO_SERIES = "macro_series"


class SessionModel(StrEnum):
    EXCHANGE_SESSIONS = "exchange_sessions"
    CONTINUOUS_WEEKDAYS = "continuous_weekdays"
    PUBLICATION_TIMESTAMPS = "publication_timestamps"


class HorizonUnit(StrEnum):
    SESSIONS = "sessions"
    CALENDAR_DAYS = "calendar_days"
    WEEKS = "weeks"
    MONTHS = "months"


class DataQualityStatus(StrEnum):
    VERIFIED = "verified"
    LOW_QUALITY = "low_quality"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


def _require_nonempty(value: str, *, field_name: str) -> str:
    trimmed = value.strip()
    if not trimmed:
        raise ValueError(f"{field_name} must be nonempty.")
    return trimmed


def _require_safe_identifier(value: str, *, field_name: str) -> str:
    trimmed = _require_nonempty(value, field_name=field_name)
    if "/" in trimmed or "\\" in trimmed or ".." in trimmed:
        raise ValueError(f"{field_name} must be path-safe.")
    return trimmed


def _require_sha256(value: str, *, field_name: str) -> str:
    allowed = set("0123456789abcdef")
    if len(value) != 64 or any(character not in allowed for character in value):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 hex digest.")
    return value


def _require_aware_utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware.")
    return value.astimezone(UTC)


def _require_timezone(value: str) -> str:
    timezone_name = _require_nonempty(value, field_name="market_timezone")
    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("market_timezone must be a valid IANA timezone.") from exc
    return timezone_name


def _require_currency(value: str | None) -> str | None:
    if value is None:
        return None
    parsed = value.strip().upper()
    if len(parsed) != 3 or not parsed.isalpha():
        raise ValueError("quote_currency must be a three-letter alphabetic code.")
    return parsed


def _normalized_identifier_tuple(values: tuple[str, ...], *, field_name: str) -> tuple[str, ...]:
    normalized = tuple(
        _require_safe_identifier(value, field_name=f"{field_name} item") for value in values
    )
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{field_name} must not contain duplicates.")
    return tuple(sorted(normalized))


@dataclass(frozen=True, slots=True)
class InstrumentProfile:
    instrument_id: str
    symbol: str
    asset_class: AssetClass
    market_timezone: str
    session_model: SessionModel
    quote_currency: str | None = None
    calendar_code: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.asset_class, AssetClass):
            raise ValueError("asset_class must be an AssetClass.")
        if not isinstance(self.session_model, SessionModel):
            raise ValueError("session_model must be a SessionModel.")
        object.__setattr__(
            self,
            "instrument_id",
            _require_safe_identifier(self.instrument_id, field_name="instrument_id"),
        )
        object.__setattr__(self, "symbol", _require_nonempty(self.symbol, field_name="symbol"))
        object.__setattr__(self, "market_timezone", _require_timezone(self.market_timezone))
        object.__setattr__(self, "quote_currency", _require_currency(self.quote_currency))
        if self.calendar_code is not None:
            object.__setattr__(
                self,
                "calendar_code",
                _require_safe_identifier(self.calendar_code, field_name="calendar_code"),
            )
        if self.session_model == SessionModel.EXCHANGE_SESSIONS and self.calendar_code is None:
            raise ValueError("exchange-session instruments require calendar_code.")


@dataclass(frozen=True, slots=True, order=True)
class AnalysisHorizon:
    unit: HorizonUnit
    length: int

    def __post_init__(self) -> None:
        if not isinstance(self.unit, HorizonUnit):
            raise ValueError("horizon unit must be a HorizonUnit.")
        if isinstance(self.length, bool) or not isinstance(self.length, int) or self.length <= 0:
            raise ValueError("horizon length must be a positive integer.")


@dataclass(frozen=True, slots=True)
class AnalysisProfile:
    profile_id: str
    profile_version: str
    target_instrument_id: str
    horizons: tuple[AnalysisHorizon, ...]
    feature_families: tuple[str, ...]
    context_series_ids: tuple[str, ...]
    scenario_schema_id: str

    def __post_init__(self) -> None:
        for field_name in (
            "profile_id",
            "profile_version",
            "target_instrument_id",
            "scenario_schema_id",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_safe_identifier(getattr(self, field_name), field_name=field_name),
            )
        if not self.horizons:
            raise ValueError("horizons must not be empty.")
        if len(set(self.horizons)) != len(self.horizons):
            raise ValueError("horizons must be unique.")
        object.__setattr__(
            self,
            "horizons",
            tuple(sorted(self.horizons, key=lambda value: (value.unit.value, value.length))),
        )
        object.__setattr__(
            self,
            "feature_families",
            _normalized_identifier_tuple(self.feature_families, field_name="feature_families"),
        )
        object.__setattr__(
            self,
            "context_series_ids",
            _normalized_identifier_tuple(
                self.context_series_ids,
                field_name="context_series_ids",
            ),
        )


@dataclass(frozen=True, slots=True)
class SeriesSnapshot:
    snapshot_id: str
    series_id: str
    provider: str
    schema_version: str
    retrieved_at: datetime
    available_as_of: datetime
    first_observation_id: str
    last_observation_id: str
    row_count: int
    canonical_checksum: str
    quality_status: DataQualityStatus

    def __post_init__(self) -> None:
        if not isinstance(self.quality_status, DataQualityStatus):
            raise ValueError("quality_status must be a DataQualityStatus.")
        for field_name in ("snapshot_id", "series_id", "provider", "schema_version"):
            object.__setattr__(
                self,
                field_name,
                _require_safe_identifier(getattr(self, field_name), field_name=field_name),
            )
        object.__setattr__(
            self,
            "first_observation_id",
            _require_nonempty(self.first_observation_id, field_name="first_observation_id"),
        )
        object.__setattr__(
            self,
            "last_observation_id",
            _require_nonempty(self.last_observation_id, field_name="last_observation_id"),
        )
        retrieved_at = _require_aware_utc(self.retrieved_at, field_name="retrieved_at")
        available_as_of = _require_aware_utc(self.available_as_of, field_name="available_as_of")
        if retrieved_at > available_as_of:
            raise ValueError("retrieved_at must not be after available_as_of.")
        object.__setattr__(self, "retrieved_at", retrieved_at)
        object.__setattr__(self, "available_as_of", available_as_of)
        if (
            isinstance(self.row_count, bool)
            or not isinstance(self.row_count, int)
            or self.row_count <= 0
        ):
            raise ValueError("row_count must be a positive integer.")
        object.__setattr__(
            self,
            "canonical_checksum",
            _require_sha256(self.canonical_checksum, field_name="canonical_checksum"),
        )


@dataclass(frozen=True, slots=True)
class DataQualityDecision:
    status: DataQualityStatus
    eligible: bool
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.status, DataQualityStatus):
            raise ValueError("status must be a DataQualityStatus.")
        normalized_reasons = tuple(
            _require_nonempty(reason, field_name="reason") for reason in self.reasons
        )
        object.__setattr__(self, "reasons", normalized_reasons)
        if self.status == DataQualityStatus.VERIFIED:
            if not self.eligible or self.reasons:
                raise ValueError("verified data quality must be eligible with no refusal reasons.")
        elif self.eligible:
            raise ValueError("non-verified data quality must not be analysis eligible.")
        elif not self.reasons:
            raise ValueError("ineligible data quality decisions must include a reason.")


@dataclass(frozen=True, slots=True)
class IntelligenceRunIdentity:
    run_id: str
    target_instrument_id: str
    as_of: datetime
    analysis_profile_id: str
    snapshot_ids: tuple[str, ...]
    code_revision: str
    configuration_hash: str

    def __post_init__(self) -> None:
        for field_name in (
            "run_id",
            "target_instrument_id",
            "analysis_profile_id",
            "code_revision",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_safe_identifier(getattr(self, field_name), field_name=field_name),
            )
        object.__setattr__(self, "as_of", _require_aware_utc(self.as_of, field_name="as_of"))
        object.__setattr__(
            self,
            "snapshot_ids",
            _normalized_identifier_tuple(self.snapshot_ids, field_name="snapshot_ids"),
        )
        if not self.snapshot_ids:
            raise ValueError("snapshot_ids must not be empty.")
        object.__setattr__(
            self,
            "configuration_hash",
            _require_sha256(self.configuration_hash, field_name="configuration_hash"),
        )


def derive_series_snapshot_id(
    *,
    series_id: str,
    provider: str,
    canonical_checksum: str,
    first_observation_id: str,
    last_observation_id: str,
    row_count: int,
) -> str:
    if isinstance(row_count, bool) or not isinstance(row_count, int) or row_count <= 0:
        raise ValueError("row_count must be a positive integer.")
    payload = {
        "series_id": _require_safe_identifier(series_id, field_name="series_id"),
        "provider": _require_safe_identifier(provider, field_name="provider"),
        "canonical_checksum": _require_sha256(
            canonical_checksum,
            field_name="canonical_checksum",
        ),
        "first_observation_id": _require_nonempty(
            first_observation_id,
            field_name="first_observation_id",
        ),
        "last_observation_id": _require_nonempty(
            last_observation_id,
            field_name="last_observation_id",
        ),
        "row_count": row_count,
    }
    return f"mi0-snapshot-{_canonical_digest(payload)[:32]}"


def derive_intelligence_run_identity(
    *,
    target_instrument_id: str,
    as_of: datetime,
    analysis_profile_id: str,
    snapshot_ids: tuple[str, ...],
    code_revision: str,
    configuration_hash: str,
) -> IntelligenceRunIdentity:
    target = _require_safe_identifier(target_instrument_id, field_name="target_instrument_id")
    profile_id = _require_safe_identifier(analysis_profile_id, field_name="analysis_profile_id")
    normalized_snapshot_ids = _normalized_identifier_tuple(snapshot_ids, field_name="snapshot_ids")
    if not normalized_snapshot_ids:
        raise ValueError("snapshot_ids must not be empty.")
    revision = _require_safe_identifier(code_revision, field_name="code_revision")
    config_hash = _require_sha256(configuration_hash, field_name="configuration_hash")
    normalized_as_of = _require_aware_utc(as_of, field_name="as_of")
    payload = {
        "target_instrument_id": target,
        "as_of": normalized_as_of.isoformat(),
        "analysis_profile_id": profile_id,
        "snapshot_ids": normalized_snapshot_ids,
        "code_revision": revision,
        "configuration_hash": config_hash,
    }
    return IntelligenceRunIdentity(
        run_id=f"mi0-run-{_canonical_digest(payload)[:32]}",
        target_instrument_id=target,
        as_of=normalized_as_of,
        analysis_profile_id=profile_id,
        snapshot_ids=normalized_snapshot_ids,
        code_revision=revision,
        configuration_hash=config_hash,
    )


def _canonical_digest(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()
