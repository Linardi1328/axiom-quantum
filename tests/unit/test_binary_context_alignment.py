"""Synthetic-only tests of exact session alignment and protected-label refusal."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pandas as pd

from spy_market_agent.datasets.models import LABEL_SCHEMA_VERSION
from spy_market_agent.intelligence.context import MI2A_REQUIRED_CONTEXT_IDS
from spy_market_agent.intelligence.context_features import (
    MI2B_CONTEXT_FEATURE_POLICY_ID,
    MI2B_SPY_CONTEXT_FEATURE_DEFINITIONS,
    ContextFeatureValue,
    SPYContextFeatureBundle,
)
from spy_market_agent.intelligence.legacy_spy import LEGACY_SPY_SERIES_ID
from spy_market_agent.market_data.models import SCHEMA_VERSION as MARKET_DATA_SCHEMA_VERSION
from spy_market_agent.research.binary_context_alignment import (
    BINARY_CONTEXT_ALIGNED_COLUMNS,
    BinaryContextHistoryAlignment,
    align_binary_context_history,
)
from spy_market_agent.research.binary_context_preflight import (
    BinaryContextStatus,
    ContextSourceProof,
)
from spy_market_agent.research.context_ablation import SPYContextFeatureHistory
from spy_market_agent.research.features import (
    RESEARCH_FEATURE_COLUMNS,
    RESEARCH_FEATURE_SCHEMA_VERSION,
    ResearchSupervisedDataset,
    ResearchSupervisedDatasetMetadata,
)

SESSIONS = (date(2026, 1, 12), date(2026, 1, 13))
SOURCE_SESSIONS = (
    date(2026, 1, 12),
    date(2026, 1, 13),
    date(2026, 1, 14),
    date(2026, 1, 15),
    date(2026, 1, 16),
    date(2026, 1, 20),
    date(2026, 1, 21),
    date(2026, 1, 22),
)
PROTECTED = date(2026, 2, 2)
CHECKSUM = "b" * 64


def _supervised() -> ResearchSupervisedDataset:
    """Construct a minimal synthetic binary target and feature-history fixture."""
    features = pd.DataFrame({"session": list(SESSIONS)})
    for name in RESEARCH_FEATURE_COLUMNS:
        features[name] = [0.10, 0.20]
    labels = pd.DataFrame(
        {
            "session": list(SESSIONS),
            "entry_session": [date(2026, 1, 13), date(2026, 1, 14)],
            "exit_session": [date(2026, 1, 21), date(2026, 1, 22)],
            "gross_forward_return": [0.01, -0.01],
            "net_forward_return": [0.009, -0.011],
            "target": [1, 0],
        }
    )
    metadata = ResearchSupervisedDatasetMetadata(
        source_market_data_checksum=CHECKSUM,
        source_schema_version=MARKET_DATA_SCHEMA_VERSION,
        feature_schema_version=RESEARCH_FEATURE_SCHEMA_VERSION,
        label_schema_version=LABEL_SCHEMA_VERSION,
        feature_columns=RESEARCH_FEATURE_COLUMNS,
        row_count=2,
        first_session=SESSIONS[0],
        last_session=SESSIONS[-1],
        global_feature_warmup_rows=60,
        created_at=datetime(2026, 1, 23, tzinfo=UTC),
    )
    return ResearchSupervisedDataset(features=features, labels=labels, metadata=metadata)


def _bundle(session: date) -> SPYContextFeatureBundle:
    """Produce timestamped MI context features for a synthetic market session."""
    as_of = datetime.combine(session, datetime.min.time(), tzinfo=UTC) + timedelta(hours=22)
    snapshots = tuple(f"s-{session.isoformat()}-{i}" for i in range(4))
    by_source = dict(zip(MI2A_REQUIRED_CONTEXT_IDS, snapshots, strict=True))
    features = tuple(
        ContextFeatureValue(
            policy_id=MI2B_CONTEXT_FEATURE_POLICY_ID,
            feature_id=definition.feature_id,
            methodology_id=definition.methodology_id,
            source_series_id=definition.source_series_id,
            source_snapshot_id=by_source[definition.source_series_id],
            target_snapshot_id=f"spy-{session.isoformat()}",
            anchor_session=session,
            as_of=as_of,
            lookback_sessions=definition.lookback_sessions,
            unit=definition.unit,
            value=0.2,
        )
        for definition in MI2B_SPY_CONTEXT_FEATURE_DEFINITIONS
    )
    return SPYContextFeatureBundle(
        policy_id=MI2B_CONTEXT_FEATURE_POLICY_ID,
        as_of=as_of,
        anchor_session=session,
        target_series_id=LEGACY_SPY_SERIES_ID,
        target_snapshot_id=f"spy-{session.isoformat()}",
        context_snapshot_ids=snapshots,
        features=features,
    )


def _history(
    bundles: tuple[SPYContextFeatureBundle, ...] | None = None,
) -> SPYContextFeatureHistory:
    """Build a synthetic MI context history with matching SPY lineage."""
    return SPYContextFeatureHistory(
        source_market_data_checksum=CHECKSUM,
        source_schema_version=MARKET_DATA_SCHEMA_VERSION,
        bundles=bundles if bundles is not None else tuple(_bundle(s) for s in SESSIONS),
    )


def _cutoffs() -> dict[date, datetime]:
    """Supply explicit per-session New York market-close cutoff instants."""
    return {
        session: datetime.combine(session, datetime.min.time(), tzinfo=UTC) + timedelta(hours=23)
        for session in SESSIONS
    }


def _proofs() -> dict[date, tuple[ContextSourceProof, ...]]:
    """Create synthetic declared provenance for each required context snapshot."""
    proofs: dict[date, tuple[ContextSourceProof, ...]] = {}
    for session in SESSIONS:
        bundle = _bundle(session)
        proofs[session] = tuple(
            ContextSourceProof(
                series_id=series,
                snapshot_id=snapshot,
                checksum_sha256="a" * 64,
                available_as_of=bundle.as_of,
                quality_verified=True,
                usage_rights_verified=True,
            )
            for series, snapshot in zip(
                MI2A_REQUIRED_CONTEXT_IDS, bundle.context_snapshot_ids, strict=True
            )
        )
    return proofs


def _align(
    *,
    supervised: ResearchSupervisedDataset | None = None,
    history: SPYContextFeatureHistory | None = None,
    cutoffs: dict[date, datetime] | None = None,
    proofs: dict[date, tuple[ContextSourceProof, ...]] | None = None,
    protected: date = PROTECTED,
    source_sessions: tuple[date, ...] = SOURCE_SESSIONS,
) -> BinaryContextHistoryAlignment:
    """Run the offline alignment with optional malformed synthetic inputs."""
    return align_binary_context_history(
        supervised=supervised if supervised is not None else _supervised(),
        history=history if history is not None else _history(),
        protected_first_prediction_session=protected,
        source_sessions=source_sessions,
        signal_cutoffs=cutoffs if cutoffs is not None else _cutoffs(),
        source_proofs_by_session=proofs if proofs is not None else _proofs(),
    )


def test_aligned_rows_are_deterministic_features_only() -> None:
    """Return the exact canonical, feature-only row layout on eligible inputs."""
    result = _align()
    assert result.status == BinaryContextStatus.ELIGIBLE_FOR_DEVELOPMENT
    assert result.reasons == ()
    assert result.execution_authority == "none"
    assert result.prediction_sessions == SESSIONS
    assert result.feature_columns == BINARY_CONTEXT_ALIGNED_COLUMNS
    assert len(result.feature_rows) == 2
    assert all(len(row) == len(BINARY_CONTEXT_ALIGNED_COLUMNS) for row in result.feature_rows)
    assert "target" not in result.feature_columns
    assert result.feature_rows[0][: len(RESEARCH_FEATURE_COLUMNS)] == (0.10,) * len(
        RESEARCH_FEATURE_COLUMNS
    )


def test_source_checksum_mismatch_fails_before_alignment() -> None:
    """Fail closed when contextual lineage differs from the binary SPY input."""
    history = replace(_history(), source_market_data_checksum="c" * 64)
    result = _align(history=history)
    assert result.status == BinaryContextStatus.INELIGIBLE
    assert result.reasons == ("binary_source_or_schema_mismatch",)
    assert result.feature_rows == ()


def test_protected_label_exits_are_rejected_without_partial_features() -> None:
    """Never build features if a label exit reaches the protected window."""
    supervised = _supervised()
    supervised.labels.loc[1, "exit_session"] = PROTECTED
    result = _align(supervised=supervised)
    assert result.reasons == ("protected_or_invalid_binary_label_boundary",)
    assert result.feature_rows == ()
    assert result.prediction_sessions == ()


def test_missing_or_extra_context_sessions_fail_closed() -> None:
    """Deny missing or excess history, source proof, and cutoff coverage."""
    missing = _history(bundles=(_bundle(SESSIONS[0]),))
    assert _align(history=missing).reasons == ("context_session_coverage_mismatch",)
    cutoffs = _cutoffs()
    cutoffs[date(2026, 1, 14)] = datetime(2026, 1, 14, 23, tzinfo=UTC)
    assert _align(cutoffs=cutoffs).reasons == ("context_session_coverage_mismatch",)
    proofs = _proofs()
    del proofs[SESSIONS[1]]
    assert _align(proofs=proofs).reasons == ("context_session_coverage_mismatch",)


def test_late_source_refuses_entire_history_not_just_bad_row() -> None:
    """Discard every row when one feature source arrived too late."""
    proofs = _proofs()
    late = replace(
        proofs[SESSIONS[1]][0],
        available_as_of=_cutoffs()[SESSIONS[1]] + timedelta(seconds=1),
    )
    proofs[SESSIONS[1]] = (late, *proofs[SESSIONS[1]][1:])
    result = _align(proofs=proofs)
    assert result.status == BinaryContextStatus.INELIGIBLE
    assert "source_available_after_signal_cutoff" in result.reasons
    assert result.feature_rows == ()
    assert result.prediction_sessions == ()


def test_context_after_signal_cutoff_is_rejected() -> None:
    """Reject context assembled after the prediction decision cutoff."""
    cutoffs = _cutoffs()
    cutoffs[SESSIONS[1]] = _bundle(SESSIONS[1]).as_of - timedelta(seconds=1)
    result = _align(cutoffs=cutoffs)
    assert "context_available_after_signal_cutoff" in result.reasons
    assert result.feature_rows == ()


def test_nonfinite_binary_feature_is_rejected() -> None:
    """Never expose non-finite baseline features to a candidate model."""
    supervised = _supervised()
    supervised.features.loc[0, RESEARCH_FEATURE_COLUMNS[0]] = float("nan")
    result = _align(supervised=supervised)
    assert result.reasons == ("nonfinite_binary_feature",)
    assert result.feature_rows == ()


def test_binary_entry_and_exit_must_match_exact_source_session_offsets() -> None:
    """Require actual next-session entry and six-session outcome exit."""
    supervised = _supervised()
    supervised.labels.loc[0, "entry_session"] = date(2026, 1, 14)
    result = _align(supervised=supervised)
    assert result.reasons == ("binary_label_horizon_mismatch",)
    assert result.feature_rows == ()


def test_protected_source_dates_or_truncated_calendar_fail_closed() -> None:
    """Reject protected sessions and calendars too short for the label."""
    protected_source = _align(source_sessions=(*SOURCE_SESSIONS, PROTECTED))
    assert protected_source.reasons == ("protected_source_session_present",)
    assert protected_source.feature_rows == ()
    truncated = _align(source_sessions=SOURCE_SESSIONS[:-2])
    assert truncated.reasons == ("binary_label_horizon_mismatch",)
    assert truncated.feature_rows == ()


def test_missing_xnys_source_session_is_not_a_six_session_horizon() -> None:
    """A gap in a nominally ordered calendar must not shorten target horizons."""
    incomplete = SOURCE_SESSIONS[:3] + SOURCE_SESSIONS[4:]
    result = _align(source_sessions=incomplete)
    assert result.reasons == ("missing_or_nontrading_xnys_source_session",)
    assert result.feature_rows == ()


def test_signal_before_spy_market_close_cannot_use_daily_close_features() -> None:
    """The binary baseline cannot see a future daily close before XNYS closes."""
    cutoffs = _cutoffs()
    cutoffs[SESSIONS[1]] = datetime(2026, 1, 13, 20, 59, tzinfo=UTC)
    result = _align(cutoffs=cutoffs)
    assert result.reasons == ("binary_features_unavailable_before_session_close",)
    assert result.feature_rows == ()
    assert result.prediction_sessions == ()


def test_naive_signal_cutoff_denied_before_market_close_comparison() -> None:
    """Reject a timezone-naive cutoff without accidentally treating it as UTC."""
    cutoffs = _cutoffs()
    cutoffs[SESSIONS[1]] = cutoffs[SESSIONS[1]].replace(tzinfo=None)
    result = _align(cutoffs=cutoffs)
    assert result.reasons == ("naive_signal_cutoff",)
    assert result.feature_rows == ()
