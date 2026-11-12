"""Offline fixture-only policy, lineage, chronology and atomic rejection regressions."""

from dataclasses import replace
from datetime import date, timedelta
from typing import Any, cast

import exchange_calendars as xcals
import pandas as pd
import pytest

from spy_market_agent.datasets.models import LABEL_SCHEMA_VERSION
from spy_market_agent.market_data.models import SCHEMA_VERSION
from spy_market_agent.research.features import (
    RESEARCH_FEATURE_COLUMNS,
    RESEARCH_FEATURE_SCHEMA_VERSION,
    ResearchSupervisedDataset,
    ResearchSupervisedDatasetMetadata,
)
from spy_market_agent.research.qqq_iwm_alignment import align_qqq_iwm_history
from spy_market_agent.research.qqq_iwm_policy import (
    BASELINE_COLUMNS,
    BUNDLE_CONTRACT_ID,
    CANDIDATE_COLUMNS,
    CLOSE_SCHEMA_ID,
    CONTEXT_FEATURE_IDS,
    FROZEN_PREDICTION_WINDOW,
    FROZEN_SOURCE_WINDOW,
    HISTORY_CONTRACT_ID,
    POLICY_ID,
    REQUIRED_SOURCE_IDS,
    QQQIWMFeatureBundle,
    QQQIWMFeatureHistory,
    SyntheticCloseSnapshot,
    SyntheticSourceProof,
    context_values,
)

CALENDAR = xcals.get_calendar("XNYS")
SESSIONS = tuple(s.date() for s in CALENDAR.sessions_in_range("2020-10-01", "2020-12-08"))
PREDICTIONS = (date(2020, 11, 25), date(2020, 11, 27))
PROTECTED = date(2020, 12, 9)
CHECKSUM = "a" * 64


def _snapshot(series_id: str, session: date) -> SyntheticCloseSnapshot:
    end = SESSIONS.index(session)
    sessions = SESSIONS[end - 20 : end + 1]
    scale = ("SPY", *REQUIRED_SOURCE_IDS).index(series_id) + 1
    return SyntheticCloseSnapshot(
        series_id, sessions, tuple(100 + scale * i for i in range(end - 20, end + 1))
    )


def _proof(snapshot: SyntheticCloseSnapshot) -> SyntheticSourceProof:
    return SyntheticSourceProof(
        series_id=snapshot.series_id,
        snapshot_id=snapshot.snapshot_id,
        checksum=snapshot.checksum,
        schema_id=CLOSE_SCHEMA_ID,
        available_as_of=CALENDAR.session_close(pd.Timestamp(snapshot.sessions[-1])).to_pydatetime()
        + timedelta(minutes=5),
        historical_availability_evidence=f"synthetic-pit-{snapshot.checksum}",
        quality_evidence=f"synthetic-quality-{snapshot.checksum}",
        permitted_usage_evidence=f"synthetic-usage-{snapshot.checksum}",
    )


def _bundle(session: date) -> QQQIWMFeatureBundle:
    target = _snapshot("SPY", session)
    sources = tuple(_snapshot(series, session) for series in REQUIRED_SOURCE_IDS)
    cutoff = CALENDAR.session_close(pd.Timestamp(session)).to_pydatetime() + timedelta(hours=1)
    return QQQIWMFeatureBundle(
        target,
        sources,
        tuple(_proof(s) for s in (target, *sources)),
        cutoff,
        CONTEXT_FEATURE_IDS,
        context_values(target, sources),
    )


def _history() -> QQQIWMFeatureHistory:
    return QQQIWMFeatureHistory(CHECKSUM, SCHEMA_VERSION, tuple(_bundle(s) for s in PREDICTIONS))


def _supervised() -> ResearchSupervisedDataset:
    features = pd.DataFrame(
        {"session": PREDICTIONS, **{name: [0.1, 0.2] for name in RESEARCH_FEATURE_COLUMNS}}
    )
    for idx, session in enumerate(PREDICTIONS):
        snapshot = _snapshot("SPY", session)
        for n in (1, 5, 20):
            features.at[idx, f"close_return_{n}d"] = (
                snapshot.closes[-1] / snapshot.closes[-n - 1] - 1
            )
    labels = pd.DataFrame(
        {
            "session": PREDICTIONS,
            "entry_session": [SESSIONS[SESSIONS.index(s) + 1] for s in PREDICTIONS],
            "exit_session": [SESSIONS[SESSIONS.index(s) + 6] for s in PREDICTIONS],
            "gross_forward_return": [0.01, -0.01],
            "net_forward_return": [0.009, -0.011],
            "target": [1, 0],
        }
    )
    metadata = ResearchSupervisedDatasetMetadata(
        source_market_data_checksum=CHECKSUM,
        source_schema_version=SCHEMA_VERSION,
        feature_schema_version=RESEARCH_FEATURE_SCHEMA_VERSION,
        label_schema_version=LABEL_SCHEMA_VERSION,
        feature_columns=RESEARCH_FEATURE_COLUMNS,
        row_count=2,
        first_session=PREDICTIONS[0],
        last_session=PREDICTIONS[-1],
        global_feature_warmup_rows=60,
        created_at=_bundle(PREDICTIONS[-1]).signal_cutoff,
    )
    return ResearchSupervisedDataset(features, labels, metadata)


def _align(**kwargs: Any) -> Any:
    inputs: dict[str, Any] = {
        "supervised": _supervised(),
        "history": _history(),
        "source_sessions": SESSIONS,
        "protected_first_prediction_session": PROTECTED,
    }
    inputs.update(kwargs)
    return align_qqq_iwm_history(**inputs)


def _rejected(**kwargs: Any) -> None:
    result = _align(**kwargs)
    assert result.status == "ineligible"
    assert result.feature_rows == result.prediction_sessions == ()
    assert result.execution_authority == "none"
    assert result.reasons == ("invalid_or_ineligible_synthetic_history",)


def test_valid_synthetic_alignment_and_frozen_feature_math() -> None:
    result = _align()
    assert result.status == "eligible_for_synthetic_development"
    assert result.reasons == ()
    assert result.prediction_sessions == PREDICTIONS
    assert result.feature_columns == CANDIDATE_COLUMNS
    assert result.policy_id == POLICY_ID
    assert result.evidence_kind == "synthetic_fixture"
    assert result.execution_authority == "none"
    assert len(BASELINE_COLUMNS) == 16
    assert len(CANDIDATE_COLUMNS) == 24
    assert CONTEXT_FEATURE_IDS == (
        "qqq_return_5",
        "qqq_return_20",
        "qqq_relative_strength_5",
        "qqq_relative_strength_20",
        "iwm_return_5",
        "iwm_return_20",
        "iwm_relative_strength_5",
        "iwm_relative_strength_20",
    )
    for row, bundle in zip(result.feature_rows, _history().bundles, strict=True):
        assert row[16:] == bundle.values
        for source_idx, source in enumerate(bundle.sources):
            for offset, n in enumerate((5, 20)):
                expected = source.closes[-1] / source.closes[-n - 1] - 1
                spy_return = bundle.target.closes[-1] / bundle.target.closes[-n - 1] - 1
                assert row[16 + source_idx * 4 + offset] == expected
                assert row[18 + source_idx * 4 + offset] == expected - spy_return
    assert "realized_volatility_ratio_5_20" not in CANDIDATE_COLUMNS
    assert "log_dollar_volume_deviation_20" not in CANDIDATE_COLUMNS
    assert _align() == result


def test_policy_contracts_and_frozen_future_population() -> None:
    assert _history().contract_id == HISTORY_CONTRACT_ID
    assert _bundle(PREDICTIONS[0]).contract_id == BUNDLE_CONTRACT_ID
    assert (date(2018, 1, 2), date(2024, 10, 15), 1708) == FROZEN_SOURCE_WINDOW
    assert (date(2018, 3, 29), date(2024, 10, 7), 1642) == FROZEN_PREDICTION_WINDOW


@pytest.mark.parametrize(
    "field",
    [
        "snapshot_id",
        "checksum",
        "schema_id",
        "series_id",
        "historical_availability_evidence",
        "quality_evidence",
        "permitted_usage_evidence",
        "evidence_kind",
    ],
)
@pytest.mark.parametrize("source_index", [0, 1, 2])
def test_invalid_source_and_target_proofs(field: str, source_index: int) -> None:
    bundle = _bundle(PREDICTIONS[0])
    proofs = list(bundle.proofs)
    changes: dict[str, Any] = {field: "UNVERIFIED"}
    proofs[source_index] = replace(proofs[source_index], **changes)
    with pytest.raises(
        ValueError,
        match=r"missing_duplicate_or_unknown_proofs|source_identity_checksum_or_schema_mismatch|unverified_source_evidence|real_data_eligibility_blocked",
    ):
        replace(bundle, proofs=tuple(proofs))


@pytest.mark.parametrize("kind", ["missing", "duplicate", "unknown", "reordered"])
def test_exact_two_sources_and_three_proofs(kind: str) -> None:
    bundle = _bundle(PREDICTIONS[0])
    bad_proofs = {
        "missing": bundle.proofs[:-1],
        "duplicate": (bundle.proofs[0],) * 3,
        "unknown": (*bundle.proofs[:2], replace(bundle.proofs[2], series_id="VIX")),
        "reordered": tuple(reversed(bundle.proofs)),
    }
    with pytest.raises(ValueError, match=r"missing_duplicate_or_unknown_proofs"):
        replace(bundle, proofs=bad_proofs[kind])
    with pytest.raises(ValueError, match=r"missing_duplicate_or_unknown_sources"):
        replace(
            bundle, sources=bundle.sources[:-1] if kind == "missing" else (bundle.sources[0],) * 2
        )


@pytest.mark.parametrize("mode", ["naive", "future", "before_close"])
def test_availability_evidence_is_point_in_time(mode: str) -> None:
    bundle = _bundle(PREDICTIONS[0])
    available = {
        "naive": bundle.signal_cutoff.replace(tzinfo=None),
        "future": bundle.signal_cutoff + timedelta(seconds=1),
        "before_close": bundle.signal_cutoff - timedelta(hours=2),
    }[mode]
    with pytest.raises(
        ValueError, match=r"naive_or_invalid_availability|historical_availability_outside_cutoff"
    ):
        replace(
            bundle,
            proofs=(replace(bundle.proofs[0], available_as_of=available), *bundle.proofs[1:]),
        )


@pytest.mark.parametrize("mode", ["naive", "at_close", "next_open", "after_next_open"])
def test_actual_early_close_and_next_open_cutoffs(mode: str) -> None:
    bundle = _bundle(date(2020, 11, 27))  # Thanksgiving Friday early close.
    close = CALENDAR.session_close(pd.Timestamp(bundle.anchor_session)).to_pydatetime()
    next_open = CALENDAR.session_open(
        CALENDAR.next_session(pd.Timestamp(bundle.anchor_session))
    ).to_pydatetime()
    cutoff = {
        "naive": close.replace(tzinfo=None),
        "at_close": close,
        "next_open": next_open,
        "after_next_open": next_open + timedelta(seconds=1),
    }[mode]
    with pytest.raises(
        ValueError, match=r"naive_or_invalid_signal_cutoff|cutoff_outside_close_to_next_open"
    ):
        replace(bundle, signal_cutoff=cutoff)
    assert close.hour == 18


@pytest.mark.parametrize(
    "mode",
    [
        "warmup",
        "missing_session",
        "duplicate",
        "reordered",
        "unknown",
        "schema",
        "real",
        "nonfinite",
        "negative",
        "mismatched_length",
    ],
)
def test_invalid_close_snapshots(mode: str) -> None:
    snapshot = _snapshot("SPY", PREDICTIONS[0])
    changes_by_mode: dict[str, dict[str, Any]] = {
        "warmup": {"sessions": snapshot.sessions[1:], "closes": snapshot.closes[1:]},
        "missing_session": {
            "sessions": (*snapshot.sessions[:5], *snapshot.sessions[6:]),
            "closes": snapshot.closes[:-1],
        },
        "duplicate": {"sessions": (snapshot.sessions[0], *snapshot.sessions[:-1])},
        "reordered": {"sessions": tuple(reversed(snapshot.sessions))},
        "unknown": {"series_id": "VIX"},
        "schema": {"schema_id": "wrong"},
        "real": {"evidence_kind": "real"},
        "nonfinite": {"closes": (*snapshot.closes[:-1], float("inf"))},
        "negative": {"closes": (*snapshot.closes[:-1], -1)},
        "mismatched_length": {"closes": snapshot.closes[:-1]},
    }
    changes = changes_by_mode[mode]
    with pytest.raises(
        ValueError,
        match=r"insufficient_or_mismatched_close_history|missing_or_nontrading_xnys_session|unordered_or_duplicate_sessions|unknown_source_identity|close_schema_mismatch|real_data_eligibility_blocked|invalid_close_value",
    ):
        replace(snapshot, **changes)


@pytest.mark.parametrize(
    "mode",
    ["order", "missing", "nan", "string", "math", "policy", "authority", "target", "alignment"],
)
def test_invalid_feature_contract(mode: str) -> None:
    bundle = _bundle(PREDICTIONS[0])
    changes_by_mode: dict[str, dict[str, Any]] = {
        "order": {"feature_ids": tuple(reversed(CONTEXT_FEATURE_IDS))},
        "missing": {"values": bundle.values[:-1]},
        "nan": {"values": (*bundle.values[:-1], float("nan"))},
        "string": {"values": (*bundle.values[:-1], "invalid")},
        "math": {"values": (0.0,) * 8},
        "policy": {"policy_id": "wrong"},
        "authority": {"execution_authority": "paper"},
        "target": {"target": bundle.sources[0]},
        "alignment": {
            "sources": (_snapshot(REQUIRED_SOURCE_IDS[0], PREDICTIONS[1]), bundle.sources[1])
        },
    }
    changes = changes_by_mode[mode]
    with pytest.raises(
        ValueError,
        match=r"context_feature_order_mismatch|malformed_context_values|context_feature_math_mismatch|bundle_policy_or_authority_mismatch|target_identity_mismatch|snapshot_session_alignment_mismatch",
    ):
        replace(bundle, **changes)


@pytest.mark.parametrize(
    "mode",
    [
        "features_index",
        "labels_index",
        "duplicate_index",
        "columns",
        "label_columns",
        "nan",
        "string",
        "target",
        "entry",
        "exit",
        "protected",
        "return",
        "sessions",
        "metadata",
        "row_count",
        "snapshot_return",
    ],
)
def test_mutated_frame_rejection_is_atomic(mode: str) -> None:
    supervised = _supervised()
    if mode == "features_index":
        supervised.features.index = pd.Index([10, 11])
    elif mode == "labels_index":
        supervised.labels.index = pd.Index([1, 0])
    elif mode == "duplicate_index":
        supervised.features.index = pd.Index([0, 0])
    elif mode == "columns":
        supervised.features.rename(columns={BASELINE_COLUMNS[0]: "unknown"}, inplace=True)
    elif mode == "label_columns":
        supervised.labels.drop(columns="target", inplace=True)
    elif mode in ("nan", "string"):
        name = BASELINE_COLUMNS[0]
        supervised.features[name] = supervised.features[name].astype("object")
        supervised.features.at[1, name] = float("nan") if mode == "nan" else "invalid"
    elif mode == "target":
        supervised.labels.at[1, "target"] = 1
    elif mode in ("entry", "exit", "protected"):
        supervised.labels.at[1, "entry_session" if mode == "entry" else "exit_session"] = PROTECTED
    elif mode == "return":
        supervised.labels.at[1, "net_forward_return"] = float("inf")
    elif mode == "sessions":
        supervised.features.at[1, "session"] = PREDICTIONS[0]
    elif mode == "metadata":
        supervised = replace(
            supervised, metadata=replace(supervised.metadata, source_market_data_checksum="b" * 64)
        )
    elif mode == "row_count":
        supervised.features.drop(index=1, inplace=True)
    else:
        supervised.features.at[1, "close_return_20d"] = 100.0
    _rejected(supervised=supervised)


@pytest.mark.parametrize("mode", ["missing", "protected", "horizon", "invalid_date"])
def test_source_calendar_and_protected_boundary(mode: str) -> None:
    changes_by_mode: dict[str, dict[str, Any]] = {
        "missing": {"source_sessions": (*SESSIONS[:5], *SESSIONS[6:])},
        "protected": {"protected_first_prediction_session": SESSIONS[-1]},
        "horizon": {"source_sessions": SESSIONS[:-5]},
        "invalid_date": {"protected_first_prediction_session": "wrong"},
    }
    changes = changes_by_mode[mode]
    _rejected(**changes)


@pytest.mark.parametrize(
    "mode", ["empty", "schema", "real", "authority", "extra_session", "reordered"]
)
def test_history_contract_cannot_admit_real_evidence(mode: str) -> None:
    history = _history()
    if mode in ("empty", "real", "authority"):
        changes_by_mode: dict[str, dict[str, Any]] = {
            "empty": {"bundles": ()},
            "real": {"evidence_kind": "real"},
            "authority": {"execution_authority": "paper"},
        }
        changes = changes_by_mode[mode]
        with pytest.raises(
            ValueError,
            match=r"empty_context_history|real_data_eligibility_blocked|history_contract_or_authority_mismatch",
        ):
            replace(history, **changes)
    elif mode == "schema":
        _rejected(history=replace(history, source_schema_version="wrong"))
    else:
        bundles = (
            (*history.bundles, history.bundles[0])
            if mode == "extra_session"
            else tuple(reversed(history.bundles))
        )
        with pytest.raises(ValueError, match="unordered_or_duplicate_bundle_anchors"):
            replace(history, bundles=bundles)


def test_no_real_manifest_or_boolean_can_be_used_as_fixture_evidence() -> None:
    bundle = _bundle(PREDICTIONS[0])
    for value in (True, "VERIFIED", "UNVERIFIED", "downloaded_today"):
        with pytest.raises(ValueError, match=r"unverified_source_evidence"):
            replace(
                bundle,
                proofs=(
                    replace(bundle.proofs[0], permitted_usage_evidence=cast(Any, value)),
                    *bundle.proofs[1:],
                ),
            )


@pytest.mark.parametrize("column", RESEARCH_FEATURE_COLUMNS)
@pytest.mark.parametrize(
    "dtype", ["float32", "int64", "object", "Float64", "Int64", "bool", "numeric_strings"]
)
def test_noncanonical_feature_dtypes_are_rejected(column: str, dtype: str) -> None:
    supervised = _supervised()
    values = supervised.features[column]
    if dtype in ("int64", "Int64"):
        values = values.round()
    supervised.features[column] = values.astype("str" if dtype == "numeric_strings" else dtype)
    original = supervised.features.copy(deep=True)
    _rejected(supervised=supervised)
    pd.testing.assert_frame_equal(supervised.features, original)


@pytest.mark.parametrize("column", ["gross_forward_return", "net_forward_return", "target"])
@pytest.mark.parametrize(
    "dtype", ["float32", "int32", "object", "Float64", "Int64", "bool", "numeric_strings"]
)
def test_noncanonical_label_dtypes_are_rejected(column: str, dtype: str) -> None:
    supervised = _supervised()
    values = supervised.labels[column]
    if dtype in ("int32", "Int64"):
        values = values.round()
    supervised.labels[column] = values.astype("str" if dtype == "numeric_strings" else dtype)
    original = supervised.labels.copy(deep=True)
    _rejected(supervised=supervised)
    pd.testing.assert_frame_equal(supervised.labels, original)


def test_float64_target_is_rejected_even_when_values_are_binary() -> None:
    supervised = _supervised()
    supervised.labels["target"] = supervised.labels["target"].astype("float64")
    _rejected(supervised=supervised)


def test_mismatched_one_session_spy_return_is_rejected() -> None:
    supervised = _supervised()
    supervised.features.at[1, "close_return_1d"] += 0.01
    _rejected(supervised=supervised)


def _rebuild_later_bundle(series_id: str, *, overlap: bool) -> QQQIWMFeatureBundle:
    """Recompute every identity/proof/feature after changing a valid close value."""
    bundle = _bundle(PREDICTIONS[1])
    snapshots = [bundle.target, *bundle.sources]
    position = ("SPY", *REQUIRED_SOURCE_IDS).index(series_id)
    snapshot = snapshots[position]
    closes = list(snapshot.closes)
    closes[-2 if overlap else -1] += 0.25
    snapshots[position] = replace(snapshot, closes=tuple(closes))
    target, *sources = snapshots
    return replace(
        bundle,
        target=target,
        sources=tuple(sources),
        proofs=tuple(_proof(item) for item in snapshots),
        values=context_values(target, tuple(sources)),
    )


@pytest.mark.parametrize("series_id", ["SPY", *REQUIRED_SOURCE_IDS])
def test_conflicting_overlapping_closes_fail_despite_valid_rebuilt_proofs(series_id: str) -> None:
    first = _bundle(PREDICTIONS[0])
    later = _rebuild_later_bundle(series_id, overlap=True)
    # Each bundle is independently valid, including recalculated hashes and features.
    later.__post_init__()
    changed = (later.target, *later.sources)[("SPY", *REQUIRED_SOURCE_IDS).index(series_id)]
    original = (first.target, *first.sources)[("SPY", *REQUIRED_SOURCE_IDS).index(series_id)]
    assert changed.checksum != _snapshot(series_id, PREDICTIONS[1]).checksum
    assert changed.sessions[-2] == original.sessions[-1]
    assert changed.closes[-2] != original.closes[-1]
    with pytest.raises(ValueError, match="conflicting_historical_close"):
        QQQIWMFeatureHistory(CHECKSUM, SCHEMA_VERSION, (first, later))


@pytest.mark.parametrize("series_id", ["SPY", *REQUIRED_SOURCE_IDS])
def test_genuinely_new_close_observations_remain_valid(series_id: str) -> None:
    later = _rebuild_later_bundle(series_id, overlap=False)
    history = QQQIWMFeatureHistory(CHECKSUM, SCHEMA_VERSION, (_bundle(PREDICTIONS[0]), later))
    supervised = _supervised()
    if series_id == "SPY":
        for n in (1, 5, 20):
            supervised.features.at[1, f"close_return_{n}d"] = (
                later.target.closes[-1] / later.target.closes[-n - 1] - 1
            )
    result = _align(supervised=supervised, history=history)
    assert result.status == "eligible_for_synthetic_development"
    assert result.prediction_sessions == PREDICTIONS


@pytest.mark.parametrize("mode", ["duplicate", "reordered"])
def test_history_anchors_must_be_unique_and_chronological(mode: str) -> None:
    history = _history()
    bundles = (history.bundles[0],) * 2 if mode == "duplicate" else tuple(reversed(history.bundles))
    with pytest.raises(ValueError, match="unordered_or_duplicate_bundle_anchors"):
        replace(history, bundles=bundles)


@pytest.mark.parametrize("field", ["sessions", "closes"])
def test_snapshot_sequences_must_be_immutable(field: str) -> None:
    snapshot = _snapshot("SPY", PREDICTIONS[0])
    changes: dict[str, Any] = {field: list(getattr(snapshot, field))}
    with pytest.raises(ValueError, match="mutable_or_invalid_sequence"):
        replace(snapshot, **changes)


@pytest.mark.parametrize("field", ["sources", "proofs", "feature_ids", "values"])
def test_bundle_sequences_must_be_immutable(field: str) -> None:
    bundle = _bundle(PREDICTIONS[0])
    changes: dict[str, Any] = {field: list(getattr(bundle, field))}
    with pytest.raises(ValueError, match="mutable_or_invalid_sequence"):
        replace(bundle, **changes)


def test_history_bundle_sequence_must_be_immutable() -> None:
    history = _history()
    with pytest.raises(ValueError, match="mutable_or_invalid_sequence"):
        replace(history, bundles=cast(Any, list(history.bundles)))
