"""Offline, fail-closed session alignment for *development-only* binary SPY research.

No model fitting, provider ingestion, protected-label loading, inference or order paths.
This adapter deliberately reuses MI-2B feature *definitions*, not MI scenario labels
or an MI trained model. Input source-proof assertions are not independent verification.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal, cast

from spy_market_agent.datasets.models import LABEL_SCHEMA_VERSION
from spy_market_agent.research.binary_context_preflight import (
    BinaryContextStatus,
    ContextSourceProof,
    assess_binary_context_preflight,
)
from spy_market_agent.research.context_ablation import (
    MI2C_QQQ_IWM_FEATURE_IDS,
    SPYContextFeatureHistory,
)
from spy_market_agent.research.features import (
    RESEARCH_FEATURE_COLUMNS,
    RESEARCH_FEATURE_FRAME_COLUMNS,
    RESEARCH_FEATURE_SCHEMA_VERSION,
    ResearchSupervisedDataset,
)

BINARY_CONTEXT_ALIGNED_COLUMNS = (*RESEARCH_FEATURE_COLUMNS, *MI2C_QQQ_IWM_FEATURE_IDS)


@dataclass(frozen=True, slots=True)
class BinaryContextHistoryAlignment:
    """Deterministic rows with NO class labels and NO executable decisions."""

    status: BinaryContextStatus
    reasons: tuple[str, ...]
    feature_columns: tuple[str, ...]
    prediction_sessions: tuple[date, ...]
    feature_rows: tuple[tuple[float, ...], ...]
    execution_authority: Literal["none"] = "none"


def _reject(*reasons: str) -> BinaryContextHistoryAlignment:
    """Produce an atomic refusal with no partially aligned rows or sessions."""
    return BinaryContextHistoryAlignment(
        status=BinaryContextStatus.INELIGIBLE,
        reasons=tuple(sorted(set(reasons))),
        feature_columns=BINARY_CONTEXT_ALIGNED_COLUMNS,
        prediction_sessions=(),
        feature_rows=(),
    )


def align_binary_context_history(
    *,
    supervised: ResearchSupervisedDataset,
    history: SPYContextFeatureHistory,
    protected_first_prediction_session: date,
    source_sessions: tuple[date, ...],
    signal_cutoffs: dict[date, datetime],
    source_proofs_by_session: dict[date, tuple[ContextSourceProof, ...]],
) -> BinaryContextHistoryAlignment:
    """Align exactly matching development sessions, denying any incomplete evidence.

    Caller must separately verify the original market-data manifests, the actual
    source bytes behind the snapshots, and chronology of historical availability.
    This function cannot certify a candidate or an approved paper signal.
    """
    meta = supervised.metadata
    if (
        meta.label_schema_version != LABEL_SCHEMA_VERSION
        or meta.feature_schema_version != RESEARCH_FEATURE_SCHEMA_VERSION
        or history.source_market_data_checksum != meta.source_market_data_checksum
        or history.source_schema_version != meta.source_schema_version
    ):
        return _reject("binary_source_or_schema_mismatch")
    if not isinstance(protected_first_prediction_session, date) or isinstance(
        protected_first_prediction_session, datetime
    ):
        return _reject("invalid_protected_boundary")
    if (
        not source_sessions
        or source_sessions != tuple(sorted(set(source_sessions)))
        or any(isinstance(session, datetime) for session in source_sessions)
    ):
        return _reject("invalid_source_session_calendar")
    if source_sessions[-1] >= protected_first_prediction_session:
        return _reject("protected_source_session_present")
    source_index = {session: idx for idx, session in enumerate(source_sessions)}

    frame = supervised.features.copy(deep=True)
    labels = supervised.labels.copy(deep=True)
    if tuple(frame.columns) != RESEARCH_FEATURE_FRAME_COLUMNS:
        return _reject("feature_columns_mismatch")
    predictions = tuple(cast(date, item) for item in frame["session"].to_list())
    label_sessions = tuple(cast(date, item) for item in labels["session"].to_list())
    if (
        not predictions
        or predictions != tuple(sorted(set(predictions)))
        or predictions != label_sessions
        or len(predictions) != meta.row_count
    ):
        return _reject("binary_prediction_sessions_invalid")

    # Explicitly exclude the formerly opened Phase 2 final-test range before
    # reading any context row, and forbid forward label exits into that range.
    for prediction, entry, exit_session, target in zip(
        predictions,
        labels["entry_session"].to_list(),
        labels["exit_session"].to_list(),
        labels["target"].to_list(),
        strict=True,
    ):
        if not (
            isinstance(prediction, date)
            and isinstance(entry, date)
            and isinstance(exit_session, date)
            and not isinstance(prediction, datetime)
            and not isinstance(entry, datetime)
            and not isinstance(exit_session, datetime)
            and prediction < entry < exit_session < protected_first_prediction_session
            and target in (0, 1)
        ):
            return _reject("protected_or_invalid_binary_label_boundary")
        source_position = source_index.get(prediction)
        if (
            source_position is None
            or source_position + 6 >= len(source_sessions)
            or entry != source_sessions[source_position + 1]
            or exit_session != source_sessions[source_position + 6]
        ):
            return _reject("binary_label_horizon_mismatch")

    expected = set(predictions)
    context_sessions = tuple(bundle.anchor_session for bundle in history.bundles)
    if (
        context_sessions != predictions
        or set(signal_cutoffs) != expected
        or set(source_proofs_by_session) != expected
    ):
        return _reject("context_session_coverage_mismatch")

    rows: list[tuple[float, ...]] = []
    for idx, prediction in enumerate(predictions):
        baseline_values = tuple(float(frame.at[idx, name]) for name in RESEARCH_FEATURE_COLUMNS)
        if not all(math.isfinite(value) for value in baseline_values):
            return _reject("nonfinite_binary_feature")
        check = assess_binary_context_preflight(
            bundle=history.bundles[idx],
            label_schema=meta.label_schema_version,
            prediction_session=prediction,
            signal_cutoff=signal_cutoffs[prediction],
            source_proofs=source_proofs_by_session[prediction],
        )
        if check.status != BinaryContextStatus.ELIGIBLE_FOR_DEVELOPMENT:
            return _reject(*check.reasons)
        rows.append((*baseline_values, *check.values))

    return BinaryContextHistoryAlignment(
        status=BinaryContextStatus.ELIGIBLE_FOR_DEVELOPMENT,
        reasons=(),
        feature_columns=BINARY_CONTEXT_ALIGNED_COLUMNS,
        prediction_sessions=predictions,
        feature_rows=tuple(rows),
    )
