"""Fail-closed, offline provenance gate for reusing MI-2B *features* in binary SPY research.

This module cannot fit a model, evaluate an outcome, or grant any trading authority.
It deliberately does not consume MI-2C scenario model outputs or labels.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from spy_market_agent.datasets.models import LABEL_SCHEMA_VERSION
from spy_market_agent.intelligence.context import MI2A_REQUIRED_CONTEXT_IDS
from spy_market_agent.intelligence.context_features import (
    MI2B_CONTEXT_FEATURE_POLICY_ID,
    MI2B_SPY_CONTEXT_FEATURE_DEFINITIONS,
    SPYContextFeatureBundle,
)
from spy_market_agent.research.context_ablation import MI2C_QQQ_IWM_FEATURE_IDS


class BinaryContextStatus(StrEnum):
    ELIGIBLE_FOR_DEVELOPMENT = "eligible_for_development"
    INELIGIBLE = "ineligible"


@dataclass(frozen=True, slots=True)
class ContextSourceProof:
    """Owner-verified snapshot facts, not a caller-supplied model approval."""

    series_id: str
    snapshot_id: str
    checksum_sha256: str
    available_as_of: datetime
    quality_verified: bool
    usage_rights_verified: bool


@dataclass(frozen=True, slots=True)
class BinaryContextPreflight:
    status: BinaryContextStatus
    reasons: tuple[str, ...]
    feature_ids: tuple[str, ...]
    values: tuple[float, ...]
    execution_authority: str = "none"


def assess_binary_context_preflight(
    *,
    bundle: SPYContextFeatureBundle,
    label_schema: str,
    prediction_session: date,
    signal_cutoff: datetime,
    source_proofs: tuple[ContextSourceProof, ...],
) -> BinaryContextPreflight:
    """Check point-in-time and provenance, never scientific model admission.

    A success here is merely permission to propose *development* feature alignment.
    A separate approved protocol must still verify the SPY binary dataset,
    same-session alignment, feature history and actual training labels.
    """
    reasons: list[str] = []
    feature_ids: tuple[str, ...] = MI2C_QQQ_IWM_FEATURE_IDS
    if label_schema != LABEL_SCHEMA_VERSION:
        reasons.append("binary_label_schema_mismatch")
    if not isinstance(prediction_session, date) or isinstance(prediction_session, datetime):
        reasons.append("invalid_prediction_session")
    if signal_cutoff.tzinfo is None or signal_cutoff.utcoffset() is None:
        reasons.append("naive_signal_cutoff")
    else:
        if bundle.as_of > signal_cutoff:
            reasons.append("context_available_after_signal_cutoff")
        if signal_cutoff.date() != prediction_session:
            reasons.append("prediction_session_cutoff_mismatch")
    if bundle.anchor_session != prediction_session:
        reasons.append("context_prediction_session_mismatch")
    if bundle.policy_id != MI2B_CONTEXT_FEATURE_POLICY_ID:
        reasons.append("context_feature_policy_mismatch")

    proof_by_id: dict[str, ContextSourceProof] = {}
    if len(source_proofs) != len(MI2A_REQUIRED_CONTEXT_IDS):
        reasons.append("incomplete_context_source_proofs")
    for proof in source_proofs:
        if proof.series_id not in MI2A_REQUIRED_CONTEXT_IDS or proof.series_id in proof_by_id:
            reasons.append("unknown_or_duplicate_context_source")
        else:
            proof_by_id[proof.series_id] = proof

    for series_id, snapshot_id in zip(
        MI2A_REQUIRED_CONTEXT_IDS, bundle.context_snapshot_ids, strict=True
    ):
        proof = proof_by_id.get(series_id)
        if proof is None:
            reasons.append("missing_source_proof")
            continue
        if proof.snapshot_id != snapshot_id:
            reasons.append("source_snapshot_mismatch")
        if len(proof.checksum_sha256) != 64 or any(
            ch not in "0123456789abcdef" for ch in proof.checksum_sha256
        ):
            reasons.append("source_checksum_invalid")
        if not proof.quality_verified:
            reasons.append("unverified_context_quality")
        if not proof.usage_rights_verified:
            reasons.append("unverified_context_usage_rights")
        if proof.available_as_of.tzinfo is None or proof.available_as_of.utcoffset() is None:
            reasons.append("naive_context_availability")
        elif signal_cutoff.tzinfo is not None and signal_cutoff.utcoffset() is not None:
            if proof.available_as_of > signal_cutoff:
                reasons.append("source_available_after_signal_cutoff")

    definitions = {
        definition.feature_id: definition
        for definition in MI2B_SPY_CONTEXT_FEATURE_DEFINITIONS
    }
    selected = {feature.feature_id: feature for feature in bundle.features}
    for feature_id in feature_ids:
        feature = selected.get(feature_id)
        if feature is None or definitions[feature_id].source_series_id not in proof_by_id:
            reasons.append("required_context_feature_missing")
        elif signal_cutoff.tzinfo is not None and signal_cutoff.utcoffset() is not None:
            if feature.as_of > signal_cutoff:
                reasons.append("feature_available_after_signal_cutoff")
    if reasons:
        return BinaryContextPreflight(
            BinaryContextStatus.INELIGIBLE, tuple(sorted(set(reasons))), feature_ids, ()
        )
    return BinaryContextPreflight(
        BinaryContextStatus.ELIGIBLE_FOR_DEVELOPMENT,
        (),
        feature_ids,
        tuple(selected[name].value for name in feature_ids),
    )
