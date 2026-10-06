from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime
from typing import Any

import pandas as pd

from spy_market_agent.market_data.models import CANONICAL_COLUMNS

CHECKSUM_VERSION = "canonical-daily-ohlcv-v2-sha256"


def _session_to_iso(value: Any) -> str:
    if isinstance(value, datetime):
        msg = "checksum session values must be plain datetime.date values, not datetimes."
        raise ValueError(msg)
    if type(value) is date:
        return value.isoformat()

    msg = "checksum session values must be plain datetime.date values."
    raise ValueError(msg)


def _is_boolean_value(value: object) -> bool:
    value_type = type(value)
    return isinstance(value, bool) or (
        value_type.__module__ == "numpy" and value_type.__name__ == "bool"
    )


def _price_to_lossless_string(value: Any) -> str:
    if _is_boolean_value(value):
        msg = "checksum price values must not be boolean."
        raise ValueError(msg)
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        msg = f"checksum price values must be canonical float64-compatible numbers: {exc}"
        raise ValueError(msg) from exc
    if not math.isfinite(numeric):
        msg = "checksum price values must be finite."
        raise ValueError(msg)
    return numeric.hex()


def _volume_to_stable_string(value: Any) -> str:
    if _is_boolean_value(value):
        msg = "checksum volume values must not be boolean."
        raise ValueError(msg)
    try:
        volume = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        msg = f"checksum volume values must be canonical int64-compatible integers: {exc}"
        raise ValueError(msg) from exc
    if value != volume:
        msg = "checksum volume values must be integer values."
        raise ValueError(msg)
    return str(volume)


def compute_market_data_checksum(frame: pd.DataFrame) -> str:
    """Compute a deterministic SHA-256 digest for canonical daily OHLCV data.

    Serialization uses canonical column order, plain ISO session dates, lossless
    ``float.hex()`` strings for canonical float64 OHLC values, integer strings for
    volume, and current row order. Volatile timestamps, local paths, provider
    credentials, and metadata are intentionally excluded.
    """

    if tuple(frame.columns) != CANONICAL_COLUMNS:
        msg = "checksum requires canonical column order."
        raise ValueError(msg)

    rows: list[list[str]] = []
    for record in frame.itertuples(index=False, name=None):
        rows.append(
            [
                _session_to_iso(record[0]),
                _price_to_lossless_string(record[1]),
                _price_to_lossless_string(record[2]),
                _price_to_lossless_string(record[3]),
                _price_to_lossless_string(record[4]),
                _volume_to_stable_string(record[5]),
            ]
        )

    payload = {
        "version": CHECKSUM_VERSION,
        "columns": list(CANONICAL_COLUMNS),
        "rows": rows,
    }
    serialized = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
