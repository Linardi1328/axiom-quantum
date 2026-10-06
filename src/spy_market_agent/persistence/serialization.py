from __future__ import annotations

import json
import math
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, cast

from spy_market_agent.persistence.models import PersistenceInputError, PersistenceIntegrityError
from spy_market_agent.run_ids import RUN_ID_ERROR_MESSAGE, validate_run_id

type JsonValue = bool | int | float | str | list[JsonValue] | dict[str, JsonValue] | None


def require_run_id(value: object) -> str:
    try:
        return validate_run_id(value)
    except ValueError as exc:
        raise PersistenceInputError("invalid_run_id", RUN_ID_ERROR_MESSAGE) from exc


def stored_run_id(value: object, *, field_name: str = "run_id") -> str:
    try:
        return validate_run_id(value)
    except ValueError as exc:
        raise PersistenceIntegrityError(f"invalid_{field_name}", RUN_ID_ERROR_MESSAGE) from exc


def required_text(value: object, *, field_name: str) -> str:
    if type(value) is not str or value == "":
        raise PersistenceIntegrityError(
            f"invalid_{field_name}",
            f"{field_name} must be stored as non-empty text.",
        )
    return value


def optional_text(value: object, *, field_name: str) -> str | None:
    if value is None:
        return None
    if type(value) is not str:
        raise PersistenceIntegrityError(
            f"invalid_{field_name}",
            f"{field_name} must be stored as text or NULL.",
        )
    return value


def date_to_text(value: date) -> str:
    if isinstance(value, datetime) or type(value) is not date:
        raise PersistenceInputError("invalid_date", "date values must be plain dates.")
    return value.isoformat()


def text_to_date(value: object, *, field_name: str = "date") -> date:
    if type(value) is not str:
        raise PersistenceIntegrityError(
            f"invalid_{field_name}",
            f"{field_name} must be an ISO date string.",
        )
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise PersistenceIntegrityError(
            f"invalid_{field_name}",
            f"{field_name} must be an ISO date string.",
        ) from exc
    return parsed


def datetime_to_text(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PersistenceInputError(
            "invalid_datetime",
            "datetime values must be timezone-aware.",
        )
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def text_to_datetime(value: object, *, field_name: str = "datetime") -> datetime:
    if type(value) is not str:
        raise PersistenceIntegrityError(
            f"invalid_{field_name}",
            f"{field_name} must be a UTC ISO-8601 timestamp.",
        )
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise PersistenceIntegrityError(
            f"invalid_{field_name}",
            f"{field_name} must be a UTC ISO-8601 timestamp.",
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PersistenceIntegrityError(
            f"invalid_{field_name}",
            f"{field_name} must be timezone-aware.",
        )
    return parsed.astimezone(UTC)


def decimal_to_text(value: Decimal) -> str:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise PersistenceInputError("invalid_decimal", "Decimal values must be finite.")
    return format(value, "f")


def text_to_decimal(value: object, *, field_name: str = "decimal") -> Decimal:
    if type(value) is not str:
        raise PersistenceIntegrityError(
            f"invalid_{field_name}",
            f"{field_name} must be an exact decimal string.",
        )
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise PersistenceIntegrityError(
            f"invalid_{field_name}",
            f"{field_name} must be an exact decimal string.",
        ) from exc
    if not parsed.is_finite():
        raise PersistenceIntegrityError(
            f"non_finite_{field_name}",
            f"{field_name} must be finite.",
        )
    return parsed


def bool_to_int(value: bool) -> int:
    if type(value) is not bool:
        raise PersistenceInputError("invalid_boolean", "Boolean values must be true booleans.")
    return 1 if value else 0


def int_to_bool(value: object, *, field_name: str = "boolean") -> bool:
    if type(value) is not int or value not in (0, 1):
        raise PersistenceIntegrityError(
            f"invalid_{field_name}",
            f"{field_name} must be stored as canonical SQLite integer 0 or 1.",
        )
    return bool(value)


def finite_float(value: object, *, field_name: str) -> float:
    if isinstance(value, bool):
        raise PersistenceIntegrityError(
            f"invalid_{field_name}",
            f"{field_name} must be a finite float.",
        )
    try:
        parsed = float(cast(Any, value))
    except (TypeError, ValueError, OverflowError) as exc:
        raise PersistenceIntegrityError(
            f"invalid_{field_name}",
            f"{field_name} must be a finite float.",
        ) from exc
    if not math.isfinite(parsed):
        raise PersistenceIntegrityError(
            f"non_finite_{field_name}",
            f"{field_name} must be finite.",
        )
    return parsed


def finite_float_for_storage(value: object, *, field_name: str) -> float:
    parsed = finite_float(value, field_name=field_name)
    return parsed


def int_from_storage(
    value: object,
    *,
    field_name: str,
    minimum: int | None = None,
) -> int:
    """Read an integer stored as SQLite INTEGER, integral REAL, or exact integer text."""

    if isinstance(value, bool):
        raise PersistenceIntegrityError(
            f"invalid_{field_name}",
            f"{field_name} must be an integer.",
        )
    if type(value) is int:
        parsed = value
    elif type(value) is float:
        if not math.isfinite(value) or not value.is_integer():
            raise PersistenceIntegrityError(
                f"invalid_{field_name}",
                f"{field_name} must be an integer.",
            )
        parsed = int(value)
    elif type(value) is str:
        if not value or value.strip() != value:
            raise PersistenceIntegrityError(
                f"invalid_{field_name}",
                f"{field_name} must be exact integer text.",
            )
        digits = value[1:] if value[0] in "+-" else value
        if not digits or not digits.isdecimal():
            raise PersistenceIntegrityError(
                f"invalid_{field_name}",
                f"{field_name} must be exact integer text.",
            )
        parsed = int(value, 10)
    else:
        raise PersistenceIntegrityError(
            f"invalid_{field_name}",
            f"{field_name} must be an integer.",
        )
    if minimum is not None and parsed < minimum:
        raise PersistenceIntegrityError(
            f"invalid_{field_name}",
            f"{field_name} must be greater than or equal to {minimum}.",
        )
    return parsed


def int_for_storage(value: object, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise PersistenceInputError(
            f"invalid_{field_name}",
            f"{field_name} must be an integer.",
        )
    return value


def canonical_json_dumps(value: JsonValue) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise PersistenceInputError(
            "invalid_canonical_json",
            "value cannot be encoded as canonical JSON.",
        ) from exc


def canonical_json_loads(value: object, *, field_name: str = "json") -> JsonValue:
    if type(value) is not str:
        raise PersistenceIntegrityError(
            f"invalid_{field_name}",
            f"{field_name} must be canonical JSON text.",
        )
    try:
        parsed = json.loads(value, parse_constant=_reject_non_standard_json_constant)
    except (json.JSONDecodeError, ValueError) as exc:
        raise PersistenceIntegrityError(
            f"invalid_{field_name}",
            f"{field_name} must be canonical JSON text.",
        ) from exc
    _validate_json_numbers_are_finite(parsed, field_name=field_name)
    return cast(JsonValue, parsed)


def _reject_non_standard_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant {value!r} is not allowed")


def _validate_json_numbers_are_finite(value: object, *, field_name: str) -> None:
    if type(value) is float and not math.isfinite(value):
        raise PersistenceIntegrityError(
            f"non_finite_{field_name}",
            f"{field_name} must be canonical JSON text with finite numeric values.",
        )
    if isinstance(value, list):
        for item in value:
            _validate_json_numbers_are_finite(item, field_name=field_name)
    elif isinstance(value, dict):
        for item in value.values():
            _validate_json_numbers_are_finite(item, field_name=field_name)


def validate_checksum(value: object, *, field_name: str = "checksum") -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise PersistenceIntegrityError(
            f"invalid_{field_name}",
            f"{field_name} must be a lowercase 64-character SHA-256 hex digest.",
        )
    return value


def tuple_to_json(values: tuple[str, ...]) -> str:
    if not isinstance(values, tuple) or any(type(value) is not str for value in values):
        raise PersistenceInputError(
            "invalid_string_tuple",
            "value must be an immutable tuple of strings.",
        )
    return canonical_json_dumps(list(values))


def json_to_string_tuple(value: object, *, field_name: str) -> tuple[str, ...]:
    parsed = canonical_json_loads(value, field_name=field_name)
    if not isinstance(parsed, list) or any(type(item) is not str for item in parsed):
        raise PersistenceIntegrityError(
            f"invalid_{field_name}",
            f"{field_name} must encode an ordered list of strings.",
        )
    return tuple(cast(list[str], parsed))


__all__ = [
    "JsonValue",
    "bool_to_int",
    "canonical_json_dumps",
    "canonical_json_loads",
    "date_to_text",
    "datetime_to_text",
    "decimal_to_text",
    "finite_float",
    "finite_float_for_storage",
    "int_for_storage",
    "int_from_storage",
    "int_to_bool",
    "json_to_string_tuple",
    "optional_text",
    "require_run_id",
    "required_text",
    "stored_run_id",
    "text_to_date",
    "text_to_datetime",
    "text_to_decimal",
    "tuple_to_json",
    "validate_checksum",
]
