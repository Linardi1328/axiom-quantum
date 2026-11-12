"""Phase 3C-A contracts. Synthetic evidence can never admit real market data.

No file loader, acquisition adapter, model, or execution integration is provided.
Checksums establish integrity of supplied fixture values, not historical authenticity.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

import exchange_calendars as xcals
import pandas as pd

from spy_market_agent.benchmark.artifacts import sha256_json
from spy_market_agent.intelligence.context_features import (
    MI2B_SPY_CONTEXT_FEATURE_DEFINITIONS,
    ContextFeatureKind,
)
from spy_market_agent.intelligence.profiles import (
    MI1_IWM_DAILY_SERIES_ID,
    MI1_QQQ_DAILY_SERIES_ID,
)
from spy_market_agent.research.features import (
    BASELINE_FAMILY_ORDER,
    DRAWDOWN_POSITION_FAMILY,
    feature_columns_for_families,
)

POLICY_ID = "aq-p3ca-qqq-iwm-synthetic-eligibility-v1"
BUNDLE_CONTRACT_ID = "aq-p3ca-qqq-iwm-feature-bundle-v1"
HISTORY_CONTRACT_ID = "aq-p3ca-qqq-iwm-feature-history-v1"
CLOSE_SCHEMA_ID = "aq-p3ca-synthetic-aligned-close-v1"
REQUIRED_SOURCE_IDS = (MI1_QQQ_DAILY_SERIES_ID, MI1_IWM_DAILY_SERIES_ID)
TARGET_SERIES_ID = "SPY"
FEATURE_DEFINITIONS = tuple(
    item
    for item in MI2B_SPY_CONTEXT_FEATURE_DEFINITIONS
    if item.source_series_id in REQUIRED_SOURCE_IDS
)
CONTEXT_FEATURE_IDS = tuple(item.feature_id for item in FEATURE_DEFINITIONS)
BASELINE_COLUMNS = feature_columns_for_families((*BASELINE_FAMILY_ORDER, DRAWDOWN_POSITION_FAMILY))
CANDIDATE_COLUMNS = (*BASELINE_COLUMNS, *CONTEXT_FEATURE_IDS)

# Frozen future population, not permission to load it or to construct real labels.
FROZEN_SOURCE_WINDOW = (date(2018, 1, 2), date(2024, 10, 15), 1708)
FROZEN_PREDICTION_WINDOW = (date(2018, 3, 29), date(2024, 10, 7), 1642)


def require_sessions(sessions: tuple[date, ...]) -> None:
    if not sessions or any(type(item) is not date for item in sessions):
        raise ValueError("invalid_session_dates")
    if sessions != tuple(sorted(set(sessions))):
        raise ValueError("unordered_or_duplicate_sessions")
    calendar = xcals.get_calendar("XNYS")
    expected = tuple(
        item.date()
        for item in calendar.sessions_in_range(
            pd.Timestamp(sessions[0]), pd.Timestamp(sessions[-1])
        )
    )
    if sessions != expected:
        raise ValueError("missing_or_nontrading_xnys_session")


def require_cutoff(session: date, cutoff: datetime) -> None:
    if not isinstance(cutoff, datetime) or cutoff.tzinfo is None or cutoff.utcoffset() is None:
        raise ValueError("naive_or_invalid_signal_cutoff")
    calendar = xcals.get_calendar("XNYS")
    close = calendar.session_close(pd.Timestamp(session))
    next_session = calendar.next_session(pd.Timestamp(session))
    next_open = calendar.session_open(next_session)
    if not close < cutoff < next_open:
        raise ValueError("cutoff_outside_close_to_next_open")


@dataclass(frozen=True, slots=True)
class SyntheticCloseSnapshot:
    series_id: str
    sessions: tuple[date, ...]
    closes: tuple[float, ...]
    schema_id: str = CLOSE_SCHEMA_ID
    evidence_kind: Literal["synthetic_fixture"] = "synthetic_fixture"
    adjustment_policy: str = "synthetic_consistent_adjustment_v1"

    def __post_init__(self) -> None:
        if self.evidence_kind != "synthetic_fixture":
            raise ValueError("real_data_eligibility_blocked")
        if self.schema_id != CLOSE_SCHEMA_ID:
            raise ValueError("close_schema_mismatch")
        if self.adjustment_policy != "synthetic_consistent_adjustment_v1":
            raise ValueError("adjustment_policy_mismatch")
        if self.series_id not in (TARGET_SERIES_ID, *REQUIRED_SOURCE_IDS):
            raise ValueError("unknown_source_identity")
        require_sessions(self.sessions)
        if len(self.sessions) != len(self.closes) or len(self.closes) < 21:
            raise ValueError("insufficient_or_mismatched_close_history")
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value <= 0.0
            for value in self.closes
        ):
            raise ValueError("invalid_close_value")

    @property
    def checksum(self) -> str:
        return sha256_json(
            {
                "schema_id": self.schema_id,
                "evidence_kind": self.evidence_kind,
                "series_id": self.series_id,
                "adjustment_policy": self.adjustment_policy,
                "sessions": self.sessions,
                "closes": tuple(float(item) for item in self.closes),
            }
        )

    @property
    def snapshot_id(self) -> str:
        return f"aq-p3ca-synthetic-{self.checksum}"


@dataclass(frozen=True, slots=True)
class SyntheticSourceProof:
    """Explicit fixture evidence references; never assertions of real usage rights."""

    series_id: str
    snapshot_id: str
    checksum: str
    schema_id: str
    available_as_of: datetime
    historical_availability_evidence: str
    quality_evidence: str
    permitted_usage_evidence: str
    evidence_kind: Literal["synthetic_fixture"] = "synthetic_fixture"

    def validate(self, snapshot: SyntheticCloseSnapshot, cutoff: datetime) -> None:
        snapshot.__post_init__()
        if self.evidence_kind != "synthetic_fixture":
            raise ValueError("real_data_eligibility_blocked")
        if (self.series_id, self.snapshot_id, self.checksum, self.schema_id) != (
            snapshot.series_id,
            snapshot.snapshot_id,
            snapshot.checksum,
            snapshot.schema_id,
        ):
            raise ValueError("source_identity_checksum_or_schema_mismatch")
        # These exact names are a fixture protocol, not a real-data verification API.
        if (
            self.historical_availability_evidence != f"synthetic-pit-{snapshot.checksum}"
            or self.quality_evidence != f"synthetic-quality-{snapshot.checksum}"
            or self.permitted_usage_evidence != f"synthetic-usage-{snapshot.checksum}"
        ):
            raise ValueError("unverified_source_evidence")
        available = self.available_as_of
        if (
            not isinstance(available, datetime)
            or available.tzinfo is None
            or available.utcoffset() is None
        ):
            raise ValueError("naive_or_invalid_availability")
        close = xcals.get_calendar("XNYS").session_close(pd.Timestamp(snapshot.sessions[-1]))
        if not close < available <= cutoff:
            raise ValueError("historical_availability_outside_cutoff")


def context_values(
    target: SyntheticCloseSnapshot, sources: tuple[SyntheticCloseSnapshot, ...]
) -> tuple[float, ...]:
    """Reuse frozen MI-2B definitions and trailing-return/relative-strength math."""
    by_id = {source.series_id: source for source in sources}
    values: list[float] = []
    for definition in FEATURE_DEFINITIONS:
        lookback = definition.lookback_sessions
        assert lookback is not None
        source = by_id[definition.source_series_id]
        trailing_return = source.closes[-1] / source.closes[-lookback - 1] - 1.0
        spy_return = target.closes[-1] / target.closes[-lookback - 1] - 1.0
        values.append(
            trailing_return
            if definition.kind == ContextFeatureKind.TRAILING_RETURN
            else trailing_return - spy_return
        )
    return tuple(values)


@dataclass(frozen=True, slots=True)
class QQQIWMFeatureBundle:
    target: SyntheticCloseSnapshot
    sources: tuple[SyntheticCloseSnapshot, ...]
    proofs: tuple[SyntheticSourceProof, ...]
    signal_cutoff: datetime
    feature_ids: tuple[str, ...]
    values: tuple[float, ...]
    policy_id: str = POLICY_ID
    contract_id: str = BUNDLE_CONTRACT_ID
    execution_authority: Literal["none"] = "none"

    @property
    def anchor_session(self) -> date:
        return self.target.sessions[-1]

    def __post_init__(self) -> None:
        if (self.policy_id, self.contract_id, self.execution_authority) != (
            POLICY_ID,
            BUNDLE_CONTRACT_ID,
            "none",
        ):
            raise ValueError("bundle_policy_or_authority_mismatch")
        self.target.__post_init__()
        if self.target.series_id != TARGET_SERIES_ID:
            raise ValueError("target_identity_mismatch")
        if tuple(source.series_id for source in self.sources) != REQUIRED_SOURCE_IDS:
            raise ValueError("missing_duplicate_or_unknown_sources")
        if tuple(proof.series_id for proof in self.proofs) != (
            TARGET_SERIES_ID,
            *REQUIRED_SOURCE_IDS,
        ):
            raise ValueError("missing_duplicate_or_unknown_proofs")
        require_cutoff(self.anchor_session, self.signal_cutoff)
        for snapshot, proof in zip((self.target, *self.sources), self.proofs, strict=True):
            if snapshot.sessions != self.target.sessions:
                raise ValueError("snapshot_session_alignment_mismatch")
            proof.validate(snapshot, self.signal_cutoff)
        if self.feature_ids != CONTEXT_FEATURE_IDS:
            raise ValueError("context_feature_order_mismatch")
        if len(self.values) != 8 or any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            for value in self.values
        ):
            raise ValueError("malformed_context_values")
        if self.values != context_values(self.target, self.sources):
            raise ValueError("context_feature_math_mismatch")


@dataclass(frozen=True, slots=True)
class QQQIWMFeatureHistory:
    source_market_data_checksum: str
    source_schema_version: str
    bundles: tuple[QQQIWMFeatureBundle, ...]
    contract_id: str = HISTORY_CONTRACT_ID
    evidence_kind: Literal["synthetic_fixture"] = "synthetic_fixture"
    execution_authority: Literal["none"] = "none"

    def __post_init__(self) -> None:
        if self.contract_id != HISTORY_CONTRACT_ID or self.execution_authority != "none":
            raise ValueError("history_contract_or_authority_mismatch")
        if self.evidence_kind != "synthetic_fixture":
            raise ValueError("real_data_eligibility_blocked")
        if not self.bundles:
            raise ValueError("empty_context_history")
        for bundle in self.bundles:
            bundle.__post_init__()
