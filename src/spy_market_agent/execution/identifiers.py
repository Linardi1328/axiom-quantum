from __future__ import annotations

import hashlib

from spy_market_agent.execution.errors import PaperExecutionInputError
from spy_market_agent.run_ids import RUN_ID_ERROR_MESSAGE, RUN_ID_PATTERN, validate_run_id

EXECUTION_ID_PATTERN = RUN_ID_PATTERN


def require_execution_id(value: object, *, field_name: str) -> str:
    try:
        return validate_run_id(value)
    except ValueError as exc:
        raise PaperExecutionInputError(f"invalid_{field_name}", RUN_ID_ERROR_MESSAGE) from exc


def sha256_hexdigest(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def require_sha256(value: object, *, field_name: str) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise PaperExecutionInputError(
            f"invalid_{field_name}",
            f"{field_name} must be a lowercase SHA-256 hex digest.",
        )
    return value


__all__ = [
    "EXECUTION_ID_PATTERN",
    "require_execution_id",
    "require_sha256",
    "sha256_hexdigest",
]
