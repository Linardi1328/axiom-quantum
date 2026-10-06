from __future__ import annotations

import math
from datetime import UTC, datetime


def require_nonempty(value: str, *, field_name: str) -> str:
    trimmed = value.strip()
    if not trimmed:
        raise ValueError(f"{field_name} must be nonempty.")
    return trimmed


def require_safe_identifier(value: str, *, field_name: str) -> str:
    trimmed = require_nonempty(value, field_name=field_name)
    if "/" in trimmed or "\\" in trimmed or ".." in trimmed:
        raise ValueError(f"{field_name} must be path-safe.")
    return trimmed


def require_aware_utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware.")
    return value.astimezone(UTC)


def require_finite(value: float | int, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be numeric.")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"{field_name} must be finite.")
    return parsed


def require_optional_finite(value: float | int | None, *, field_name: str) -> float | None:
    if value is None:
        return None
    return require_finite(value, field_name=field_name)


def normalized_identifiers(
    values: tuple[str, ...],
    *,
    field_name: str,
    allow_empty: bool = True,
) -> tuple[str, ...]:
    normalized = tuple(
        require_safe_identifier(value, field_name=f"{field_name} item") for value in values
    )
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{field_name} must not contain duplicates.")
    if not allow_empty and not normalized:
        raise ValueError(f"{field_name} must not be empty.")
    return tuple(sorted(normalized))
