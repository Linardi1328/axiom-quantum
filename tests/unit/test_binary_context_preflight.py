"""Synthetic provenance checks only. Never trains models or reads real datasets."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

from spy_market_agent.datasets.models import LABEL_SCHEMA_VERSION
from spy_market_agent.intelligence.context import MI2A_REQUIRED_CONTEXT_IDS
from spy_market_agent.intelligence.context_features import (
    MI2B_CONTEXT_FEATURE_POLICY_ID,
    MI2B_SPY_CONTEXT_FEATURE_DEFINITIONS,
    ContextFeatureValue,
    SPYContextFeatureBundle,
)
from spy_market_agent.intelligence.legacy_spy import LEGACY_SPY_SERIES_ID
from spy_market_agent.research.binary_context_preflight import (
    BinaryContextPreflight,
    BinaryContextStatus,
    ContextSourceProof,
    assess_binary_context_preflight,
)
from spy_market_agent.research.context_ablation import MI2C_QQQ_IWM_FEATURE_IDS

SESSION = date(2026, 1, 13)
CUTOFF = datetime(2026, 1, 13, 22, 0, tzinfo=UTC)
AS_OF = CUTOFF - timedelta(minutes=1)
SNAPSHOTS = tuple(f"synthetic-snapshot-{i}" for i in range(4))
SOURCE_BY_ID = dict(zip(MI2A_REQUIRED_CONTEXT_IDS, SNAPSHOTS, strict=True))


def _bundle() -> SPYContextFeatureBundle:
    features = tuple(
        ContextFeatureValue(
            policy_id=MI2B_CONTEXT_FEATURE_POLICY_ID,
            feature_id=definition.feature_id,
            methodology_id=definition.methodology_id,
            source_series_id=definition.source_series_id,
            source_snapshot_id=SOURCE_BY_ID[definition.source_series_id],
            target_snapshot_id="synthetic-spy",
            anchor_session=SESSION,
            as_of=AS_OF,
            lookback_sessions=definition.lookback_sessions,
            unit=definition.unit,
            value=0.1,
        )
        for definition in MI2B_SPY_CONTEXT_FEATURE_DEFINITIONS
    )
    return SPYContextFeatureBundle(
        policy_id=MI2B_CONTEXT_FEATURE_POLICY_ID,
        as_of=AS_OF,
        anchor_session=SESSION,
        target_series_id=LEGACY_SPY_SERIES_ID,
        target_snapshot_id="synthetic-spy",
        context_snapshot_ids=SNAPSHOTS,
        features=features,
    )


def _proofs() -> tuple[ContextSourceProof, ...]:
    return tuple(
        ContextSourceProof(
            series_id=series,
            snapshot_id=SOURCE_BY_ID[series],
            checksum_sha256="a" * 64,
            available_as_of=AS_OF,
            quality_verified=True,
            usage_rights_verified=True,
        )
        for series in MI2A_REQUIRED_CONTEXT_IDS
    )


def _assess(
    *,
    label_schema: str = LABEL_SCHEMA_VERSION,
    bundle: SPYContextFeatureBundle | None = None,
    cutoff: datetime = CUTOFF,
    proofs: tuple[ContextSourceProof, ...] | None = None,
) -> BinaryContextPreflight:
    return assess_binary_context_preflight(
        bundle=bundle or _bundle(),
        label_schema=label_schema,
        prediction_session=SESSION,
        signal_cutoff=cutoff,
        source_proofs=proofs if proofs is not None else _proofs(),
    )


def test_verified_synthetic_context_is_only_development_eligible() -> None:
    result = _assess()
    assert result.status == BinaryContextStatus.ELIGIBLE_FOR_DEVELOPMENT
    assert result.feature_ids == MI2C_QQQ_IWM_FEATURE_IDS
    assert len(result.values) == len(MI2C_QQQ_IWM_FEATURE_IDS)
    assert result.execution_authority == "none"
    assert not hasattr(result, "approved_for_shadow")


def test_mi_three_class_schema_cannot_be_reused_as_binary() -> None:
    result = _assess(label_schema="mi1-spy-scenario")
    assert result.status == BinaryContextStatus.INELIGIBLE
    assert "binary_label_schema_mismatch" in result.reasons
    assert result.values == ()


def test_late_context_or_wrong_signal_session_fails_closed() -> None:
    late = _assess(cutoff=AS_OF - timedelta(seconds=1))
    assert "context_available_after_signal_cutoff" in late.reasons
    assert late.values == ()
    mismatch = _assess(cutoff=CUTOFF + timedelta(days=1))
    assert "prediction_session_cutoff_mismatch" in mismatch.reasons


def test_missing_duplicate_or_tampered_source_proofs_fail_closed() -> None:
    proofs = _proofs()
    assert "incomplete_context_source_proofs" in _assess(proofs=proofs[:-1]).reasons
    assert "unknown_or_duplicate_context_source" in _assess(
        proofs=(*proofs[:-1], proofs[0])
    ).reasons
    assert "source_snapshot_mismatch" in _assess(
        proofs=(replace(proofs[0], snapshot_id="different"), *proofs[1:])
    ).reasons
    assert "source_checksum_invalid" in _assess(
        proofs=(replace(proofs[0], checksum_sha256="invalid"), *proofs[1:])
    ).reasons


def test_unverified_rights_quality_and_future_availability_fail_closed() -> None:
    proofs = _proofs()
    bad = replace(
        proofs[0],
        quality_verified=False,
        usage_rights_verified=False,
        available_as_of=CUTOFF + timedelta(seconds=1),
    )
    result = _assess(proofs=(bad, *proofs[1:]))
    assert {
        "unverified_context_quality",
        "unverified_context_usage_rights",
        "source_available_after_signal_cutoff",
    }.issubset(result.reasons)
    assert result.values == ()


def test_naive_timestamps_are_ineligible() -> None:
    naive = _assess(cutoff=CUTOFF.replace(tzinfo=None))
    assert "naive_signal_cutoff" in naive.reasons
    proofs = _proofs()
    result = _assess(
        proofs=(
            replace(proofs[0], available_as_of=AS_OF.replace(tzinfo=None)),
            *proofs[1:],
        )
    )
    assert "naive_context_availability" in result.reasons
