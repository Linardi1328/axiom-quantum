from __future__ import annotations

import re

RUN_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
RUN_ID_MAX_LENGTH = 128
RUN_ID_ERROR_MESSAGE = (
    "run_id must be 1 to 128 ASCII characters, start with a letter or digit, "
    "and contain only letters, digits, period, underscore, and hyphen."
)

_RUN_ID_RE = re.compile(RUN_ID_PATTERN)


def validate_run_id(value: object) -> str:
    if type(value) is not str or _RUN_ID_RE.fullmatch(value) is None:
        raise ValueError(RUN_ID_ERROR_MESSAGE)
    return value


__all__ = [
    "RUN_ID_ERROR_MESSAGE",
    "RUN_ID_MAX_LENGTH",
    "RUN_ID_PATTERN",
    "validate_run_id",
]
