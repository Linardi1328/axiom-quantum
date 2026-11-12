"""Internally generated Phase 3C-B fixtures; no paths, provider or credential input."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

import exchange_calendars as xcals
import numpy as np
import pandas as pd

from spy_market_agent.datasets.labels import build_forward_label_set
from spy_market_agent.datasets.models import TradingCostAssumptions
from spy_market_agent.market_data.checksum import compute_market_data_checksum
from spy_market_agent.market_data.models import (
    CANONICAL_COLUMNS,
    MarketDataBatch,
    MarketDataMetadata,
)
from spy_market_agent.research.features import (
    ResearchSupervisedDataset,
    build_research_feature_matrix,
    build_research_supervised_dataset,
)
from spy_market_agent.research.qqq_iwm_policy import (
    CLOSE_SCHEMA_ID,
    CONTEXT_FEATURE_IDS,
    REQUIRED_SOURCE_IDS,
    QQQIWMFeatureBundle,
    QQQIWMFeatureHistory,
    SyntheticCloseSnapshot,
    SyntheticSourceProof,
    context_values,
    require_sessions,
)

GENERATOR_ID = "aq-p3cb-synthetic-ohlcv-context-v1"
SEED = 42
SOURCE_ROWS = 1200
ELIGIBLE_SOURCE_ROWS = 1182
OHLCVRow = tuple[float, float, float, float, int]


@dataclass(frozen=True, slots=True)
class _SyntheticFixture:
    sessions: tuple[date, ...]
    spy_rows: tuple[OHLCVRow, ...]
    qqq_closes: tuple[float, ...]
    iwm_closes: tuple[float, ...]
    generator_id: str = GENERATOR_ID
    seed: int = SEED
    evidence_kind: str = "synthetic_fixture"

    def validate(self) -> None:
        if (self.generator_id, self.seed, self.evidence_kind) != (
            GENERATOR_ID,
            SEED,
            "synthetic_fixture",
        ):
            raise ValueError("invalid_synthetic_generator_identity")
        arrays = (self.sessions, self.spy_rows, self.qqq_closes, self.iwm_closes)
        if any(not isinstance(items, tuple) or len(items) != SOURCE_ROWS for items in arrays):
            raise ValueError("invalid_synthetic_fixture_dimensions")
        require_sessions(self.sessions)
        for row in self.spy_rows:
            if not isinstance(row, tuple) or len(row) != 5:
                raise ValueError("invalid_synthetic_ohlcv")
            open_, high, low, close, volume = row
            if (
                not all(np.isfinite(x) and x > 0 for x in row)
                or not low <= min(open_, close) <= max(open_, close) <= high
                or type(volume) is not int
            ):
                raise ValueError("invalid_synthetic_ohlcv")
        if any(not np.isfinite(x) or x <= 0 for x in (*self.qqq_closes, *self.iwm_closes)):
            raise ValueError("invalid_synthetic_context_close")


def _generate_fixture() -> _SyntheticFixture:
    calendar = xcals.get_calendar("XNYS")
    sessions = tuple(
        item.date() for item in calendar.sessions_in_range("2010-01-04", "2015-12-31")
    )[:SOURCE_ROWS]
    noise = np.random.default_rng(SEED).normal(size=(SOURCE_ROWS, 8))
    trend = np.sin(np.arange(SOURCE_ROWS) / 37.0) * 0.0007
    returns = 0.00015 + trend + 0.008 * noise[:, 0]
    closes = 100.0 * np.exp(np.cumsum(returns))
    qqq = 80.0 * np.exp(np.cumsum(0.6 * returns + 0.005 * noise[:, 1]))
    iwm = 60.0 * np.exp(np.cumsum(0.4 * returns + 0.006 * noise[:, 2]))
    opens = np.r_[100.0, closes[:-1]] * np.exp(0.002 * noise[:, 3])
    highs = np.maximum(opens, closes) * (1.002 + np.abs(noise[:, 4]) * 0.001)
    lows = np.minimum(opens, closes) / (1.002 + np.abs(noise[:, 5]) * 0.001)
    rows = tuple(
        (
            float(opens[i]),
            float(highs[i]),
            float(lows[i]),
            float(closes[i]),
            int(1_000_000 + abs(noise[i, 6]) * 200_000),
        )
        for i in range(SOURCE_ROWS)
    )
    fixture = _SyntheticFixture(
        sessions, rows, tuple(float(x) for x in qqq), tuple(float(x) for x in iwm)
    )
    fixture.validate()
    return fixture


def _prepare_fixture(
    fixture: _SyntheticFixture,
) -> tuple[MarketDataBatch, ResearchSupervisedDataset]:
    fixture.validate()
    # Exclude the synthetic protected source partition BEFORE any label building.
    sessions = fixture.sessions[:ELIGIBLE_SOURCE_ROWS]
    rows = fixture.spy_rows[:ELIGIBLE_SOURCE_ROWS]
    frame = pd.DataFrame(
        [(s, *row) for s, row in zip(sessions, rows, strict=True)], columns=list(CANONICAL_COLUMNS)
    )
    timestamp = xcals.get_calendar("XNYS").session_close(
        pd.Timestamp(sessions[-1])
    ).to_pydatetime() + timedelta(hours=2)
    metadata = MarketDataMetadata(
        provider_name="internally_generated_synthetic_fixture",
        downloaded_at=timestamp,
        created_at=timestamp,
        first_session=sessions[0],
        last_session=sessions[-1],
        row_count=len(frame),
        dataset_checksum=compute_market_data_checksum(frame),
        source_description=GENERATOR_ID,
    )
    batch = MarketDataBatch(data=frame, metadata=metadata)
    features = build_research_feature_matrix(
        batch, created_at=timestamp, global_feature_warmup_rows=60
    )
    labels = build_forward_label_set(
        batch,
        cost_assumptions=TradingCostAssumptions(
            commission_bps_per_side=Decimal("0.125"), slippage_bps_per_side=Decimal("0.25")
        ),
        created_at=timestamp,
    )
    return batch, build_research_supervised_dataset(features, labels, created_at=timestamp)


def _context_history(
    fixture: _SyntheticFixture, supervised: ResearchSupervisedDataset
) -> QQQIWMFeatureHistory:
    calendar = xcals.get_calendar("XNYS")
    all_values = (tuple(row[3] for row in fixture.spy_rows), fixture.qqq_closes, fixture.iwm_closes)
    position = {session: index for index, session in enumerate(fixture.sessions)}
    bundles: list[QQQIWMFeatureBundle] = []
    for session in supervised.features["session"].to_list():
        end = position[session] + 1
        snapshots = tuple(
            SyntheticCloseSnapshot(
                series_id, fixture.sessions[end - 21 : end], values[end - 21 : end]
            )
            for series_id, values in zip(("SPY", *REQUIRED_SOURCE_IDS), all_values, strict=True)
        )
        available = calendar.session_close(pd.Timestamp(session)).to_pydatetime() + timedelta(
            minutes=5
        )
        proofs = tuple(
            SyntheticSourceProof(
                snapshot.series_id,
                snapshot.snapshot_id,
                snapshot.checksum,
                CLOSE_SCHEMA_ID,
                available,
                f"synthetic-pit-{snapshot.checksum}",
                f"synthetic-quality-{snapshot.checksum}",
                f"synthetic-usage-{snapshot.checksum}",
            )
            for snapshot in snapshots
        )
        target, *sources = snapshots
        bundles.append(
            QQQIWMFeatureBundle(
                target,
                tuple(sources),
                proofs,
                available + timedelta(minutes=5),
                CONTEXT_FEATURE_IDS,
                context_values(target, tuple(sources)),
            )
        )
    return QQQIWMFeatureHistory(
        supervised.metadata.source_market_data_checksum,
        supervised.metadata.source_schema_version,
        tuple(bundles),
    )
