"""Atomic, synthetic-only Phase 3C-A feature alignment; no labels are constructed."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Literal, cast

import numpy as np
import pandas as pd

from spy_market_agent.datasets.models import LABEL_COLUMNS, LABEL_SCHEMA_VERSION
from spy_market_agent.research.features import (
    RESEARCH_FEATURE_COLUMNS,
    RESEARCH_FEATURE_FRAME_COLUMNS,
    RESEARCH_FEATURE_SCHEMA_VERSION,
    ResearchSupervisedDataset,
)
from spy_market_agent.research.qqq_iwm_policy import (
    BASELINE_COLUMNS,
    CANDIDATE_COLUMNS,
    POLICY_ID,
    QQQIWMFeatureHistory,
    require_sessions,
)


@dataclass(frozen=True, slots=True)
class QQQIWMHistoryAlignment:
    status: Literal["eligible_for_synthetic_development", "ineligible"]
    reasons: tuple[str, ...]
    prediction_sessions: tuple[date, ...]
    feature_rows: tuple[tuple[float, ...], ...]
    feature_columns: tuple[str, ...] = CANDIDATE_COLUMNS
    policy_id: str = POLICY_ID
    evidence_kind: Literal["synthetic_fixture"] = "synthetic_fixture"
    execution_authority: Literal["none"] = "none"


def align_qqq_iwm_history(
    *,
    supervised: ResearchSupervisedDataset,
    history: QQQIWMFeatureHistory,
    source_sessions: tuple[date, ...],
    protected_first_prediction_session: date,
) -> QQQIWMHistoryAlignment:
    """Validate the entire input before returning any rows.

    An eventual separately authorized real runner must derive/apply the existing
    Phase 2 exclusion contract BEFORE label construction. A caller-supplied date
    here is a synthetic test boundary only; it cannot authorize a real-data run.
    """
    try:
        predictions, rows = _align(
            supervised, history, source_sessions, protected_first_prediction_session
        )
    except (ValueError, TypeError, KeyError, IndexError, OverflowError):
        # Fixed reason: never expose provider payloads or malformed input values.
        return QQQIWMHistoryAlignment(
            "ineligible", ("invalid_or_ineligible_synthetic_history",), (), ()
        )
    return QQQIWMHistoryAlignment("eligible_for_synthetic_development", (), predictions, rows)


def _align(
    supervised: ResearchSupervisedDataset,
    history: QQQIWMFeatureHistory,
    source_sessions: tuple[date, ...],
    protected: date,
) -> tuple[tuple[date, ...], tuple[tuple[float, ...], ...]]:
    history.__post_init__()
    require_sessions(source_sessions)
    if type(protected) is not date or source_sessions[-1] >= protected:
        raise ValueError("protected_source_intersection")
    meta = supervised.metadata
    if (
        meta.source_market_data_checksum != history.source_market_data_checksum
        or meta.source_schema_version != history.source_schema_version
        or meta.label_schema_version != LABEL_SCHEMA_VERSION
        or meta.feature_schema_version != RESEARCH_FEATURE_SCHEMA_VERSION
        or meta.feature_columns != RESEARCH_FEATURE_COLUMNS
    ):
        raise ValueError("source_or_schema_lineage_mismatch")
    # Pandas frames remain mutable after construction; recheck shape and indexes.
    frame, labels = supervised.features, supervised.labels
    for data, columns in ((frame, RESEARCH_FEATURE_FRAME_COLUMNS), (labels, LABEL_COLUMNS)):
        if (
            tuple(data.columns) != columns
            or len(data) != meta.row_count
            or not isinstance(data.index, pd.RangeIndex)
            or not data.index.equals(pd.RangeIndex(meta.row_count))
        ):
            raise ValueError("invalid_frame_schema_or_index")
    # DataFrames can be mutated after construction. Check every research numeric
    # column, including unselected families, without repairing or coercing inputs.
    for data, columns, dtype in (
        (frame, RESEARCH_FEATURE_COLUMNS, np.dtype("float64")),
        (labels, ("gross_forward_return", "net_forward_return"), np.dtype("float64")),
        (labels, ("target",), np.dtype("int64")),
    ):
        if any(data[column].dtype != dtype for column in columns):
            raise ValueError("noncanonical_numeric_dtype")
    predictions = tuple(cast(date, value) for value in frame["session"].to_list())
    require_sessions(predictions)
    if (
        predictions != tuple(labels["session"].to_list())
        or (predictions[0], predictions[-1]) != (meta.first_session, meta.last_session)
        or predictions != tuple(bundle.anchor_session for bundle in history.bundles)
    ):
        raise ValueError("prediction_session_alignment_mismatch")
    positions = {session: idx for idx, session in enumerate(source_sessions)}
    rows: list[tuple[float, ...]] = []
    for idx, prediction in enumerate(predictions):
        position = positions.get(prediction)
        if position is None or position + 6 >= len(source_sessions):
            raise ValueError("insufficient_label_horizon")
        entry, exit_session = labels.at[idx, "entry_session"], labels.at[idx, "exit_session"]
        if (
            type(entry) is not date
            or type(exit_session) is not date
            or entry != source_sessions[position + 1]
            or exit_session != source_sessions[position + 6]
            or not prediction < entry < exit_session < protected
        ):
            raise ValueError("invalid_or_protected_label_chronology")
        gross = float(labels.at[idx, "gross_forward_return"])
        net = float(labels.at[idx, "net_forward_return"])
        target = labels.at[idx, "target"]
        if not math.isfinite(gross) or not math.isfinite(net) or target != int(net > 0):
            raise ValueError("invalid_label_value")
        bundle = history.bundles[idx]
        # Exact trailing source calendar: no forward observations, omissions or substitution.
        target_sessions = bundle.target.sessions
        if target_sessions != source_sessions[position - len(target_sessions) + 1 : position + 1]:
            raise ValueError("context_source_calendar_mismatch")
        for lookback in (1, 5, 20):
            expected_return = bundle.target.closes[-1] / bundle.target.closes[-lookback - 1] - 1
            if not math.isclose(
                float(frame.at[idx, f"close_return_{lookback}d"]),
                expected_return,
                rel_tol=1e-12,
                abs_tol=1e-12,
            ):
                raise ValueError("spy_snapshot_return_mismatch")
        baseline = tuple(float(frame.at[idx, name]) for name in BASELINE_COLUMNS)
        if not all(math.isfinite(value) for value in baseline):
            raise ValueError("invalid_baseline_value")
        rows.append((*baseline, *bundle.values))
    return predictions, tuple(rows)
