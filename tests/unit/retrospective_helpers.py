"""Independent synthetic fixtures for AQ-P3C-R. No real market data, no network."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np

from spy_market_agent.benchmark.artifacts import sha256_bytes
from spy_market_agent.market_data.calendar import XNYSCalendar
from spy_market_agent.market_data.context_contracts import (
    ContextAcquisitionManifest,
    ContextBar,
    ContextLineage,
)
from spy_market_agent.market_data.manifest import canonical_json_bytes
from spy_market_agent.research.retrospective_context import (
    RetrospectiveContextSeries,
    _canonical_csv,
    bind_context_series,
    derive_context_dataset_id,
)

CONTEXT_SEED = 20_260_719  # unrelated to the Phase 3C-B generator (seed 42)
LINEAGE = ContextLineage(
    git_commit="a" * 40,
    python_version="3.12.0",
    package_version="0.0.0",
    dependency_versions={"pandas": "0"},
    source_checksums={"market_data/context_contracts.py": "b" * 64},
)


def synthetic_context_bars(symbol: str, sessions: tuple[date, ...], seed: int) -> list[ContextBar]:
    rng = np.random.default_rng(seed)
    close = 50.0 * np.exp(np.cumsum(0.0002 + 0.011 * rng.standard_normal(len(sessions))))
    bars = []
    for session, value in zip(sessions, close, strict=True):
        c = Decimal(f"{value:.4f}")
        bars.append(
            ContextBar(
                symbol=symbol,  # type: ignore[arg-type]
                session=session,
                open=str(c),
                high=str(c + Decimal("0.5")),
                low=str(c - Decimal("0.5")),
                close=str(c),
                volume=1_000_000,
            )
        )
    return bars


def sealed_manifest(
    symbol: str,
    canonical: bytes,
    first: date,
    last: date,
    rows: int,
    **overrides: Any,
) -> ContextAcquisitionManifest:
    """Build a self-consistent manifest, then apply deliberate (re-sealed) overrides."""
    checksum = overrides.pop("canonical_sha256", sha256_bytes(canonical))
    base: dict[str, Any] = {
        "schema_version": "aq-qqq-iwm-context-acquisition-v2",
        "dataset_id": "aq-ctx-v2-" + "0" * 24,
        "symbol": symbol,
        "provider": "alpaca",
        "feed": "sip",
        "adjustment_mode": "all",
        "timeframe": "1Day",
        "start_date": first,
        "end_date": last,
        "first_session": first,
        "last_session": last,
        "row_count": rows,
        "request_started_at": datetime(2026, 1, 5, 22, 0, tzinfo=UTC),
        "retrieval_completed_at": datetime(2026, 1, 5, 22, 1, tzinfo=UTC),
        "provider_api_version": "v2",
        "provider_sdk_version": "0.0",
        "lineage": LINEAGE,
        "raw_sha256": "c" * 64,
        "canonical_sha256": checksum,
        "manifest_sha256": None,
        "point_in_time_status": "UNVERIFIED",
        "usage_rights_status": "UNVERIFIED",
        "execution_authority": "none",
        "generated_paths": {"raw": "r", "canonical": "c", "manifest": "m"},
    }
    base.update(overrides)
    draft = ContextAcquisitionManifest.model_validate(base)
    if "dataset_id" not in overrides:
        draft = draft.model_copy(update={"dataset_id": derive_context_dataset_id(draft, checksum)})
    sealed = sha256_bytes(canonical_json_bytes(draft.model_copy(update={"manifest_sha256": None})))
    return draft.model_copy(update={"manifest_sha256": sealed})


def make_artifact(
    symbol: str, sessions: tuple[date, ...], seed: int, **overrides: Any
) -> tuple[ContextAcquisitionManifest, bytes]:
    canonical = _canonical_csv(tuple(synthetic_context_bars(symbol, sessions, seed)))
    manifest = sealed_manifest(
        symbol, canonical, sessions[0], sessions[-1], len(sessions), **overrides
    )
    return manifest, canonical


def bound_series(sessions: tuple[date, ...]) -> tuple[RetrospectiveContextSeries, ...]:
    return tuple(
        bind_context_series(*make_artifact(symbol, sessions, CONTEXT_SEED + offset))
        for offset, symbol in enumerate(("QQQ", "IWM"))
    )


def xnys_sessions(start: date, end: date) -> tuple[date, ...]:
    return tuple(XNYSCalendar().sessions_between(start, end))


def parent_root(tmp_path: Path) -> Path:
    return tmp_path
