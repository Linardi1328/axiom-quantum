"""Disjoint Phase 3B assessment schedule over already isolated supervised rows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import cast

import pandas as pd

from spy_market_agent.research.errors import WalkForwardSplitError, raise_research_error


@dataclass(frozen=True, slots=True)
class DisjointFold:
    index: int
    training_rows: tuple[int, ...]
    purge_rows: tuple[int, ...]
    assessment_rows: tuple[int, ...]
    training_last_prediction: date
    training_last_exit: date
    purge_sessions: tuple[date, ...]
    assessment_first_prediction: date
    assessment_last_prediction: date
    assessment_last_exit: date


def construct_disjoint_folds(
    labels: pd.DataFrame,
    *,
    initial_training_rows: int = 756,
    purge_rows: int = 6,
    assessment_rows: int = 126,
    minimum_partial_rows: int = 63,
    protected_first_session: date,
    source_sessions: tuple[date, ...],
) -> tuple[tuple[DisjointFold, ...], int]:
    """Return folds and a count of terminal rows too short to assess.

    The input must already exclude protected source sessions before labels were built.
    Actual exit dates are checked in addition to the six-row purge.
    """
    if (initial_training_rows, purge_rows, assessment_rows, minimum_partial_rows) != (
        756,
        6,
        126,
        63,
    ):
        raise_research_error(
            WalkForwardSplitError, "phase3b_policy_changed", "frozen fold policy changed"
        )
    required = ("session", "entry_session", "exit_session", "target")
    if any(column not in labels for column in required) or labels.empty:
        raise_research_error(WalkForwardSplitError, "phase3b_labels_invalid", "labels are missing")
    sessions = tuple(cast(date, item) for item in labels["session"].to_list())
    entries = tuple(cast(date, item) for item in labels["entry_session"].to_list())
    exits = tuple(cast(date, item) for item in labels["exit_session"].to_list())
    if source_sessions != tuple(sorted(set(source_sessions))):
        raise_research_error(
            WalkForwardSplitError, "phase3b_source_sessions_invalid", "source sessions are invalid"
        )
    source_index = {session: index for index, session in enumerate(source_sessions)}
    if sessions != tuple(sorted(set(sessions))) or entries != tuple(sorted(set(entries))):
        raise_research_error(
            WalkForwardSplitError,
            "phase3b_sessions_invalid",
            "sessions are unordered or duplicated",
        )
    if exits != tuple(sorted(set(exits))) or any(
        not (prediction < entry < exit < protected_first_session)
        for prediction, entry, exit in zip(sessions, entries, exits, strict=True)
    ):
        raise_research_error(
            WalkForwardSplitError, "phase3b_exit_invalid", "label exit is invalid or protected"
        )
    for prediction, entry, exit in zip(sessions, entries, exits, strict=True):
        index = source_index.get(prediction)
        if (
            index is None
            or index + 6 >= len(source_sessions)
            or (entry != source_sessions[index + 1] or exit != source_sessions[index + 6])
        ):
            raise_research_error(
                WalkForwardSplitError,
                "phase3b_label_alignment_invalid",
                "entry or exit does not match actual XNYS source-session offset",
            )
    targets = labels["target"].to_list()
    if any(target not in (0, 1) for target in targets):
        raise_research_error(
            WalkForwardSplitError, "phase3b_targets_invalid", "binary targets required"
        )
    folds: list[DisjointFold] = []
    training_end = initial_training_rows
    previous_last_exit: date | None = None
    while len(labels) - (training_end + purge_rows) >= minimum_partial_rows:
        assessment_start = training_end + purge_rows
        assessment_end = min(assessment_start + assessment_rows, len(labels))
        if exits[training_end - 1] >= sessions[assessment_start] or (
            previous_last_exit is not None and previous_last_exit >= sessions[assessment_start]
        ):
            raise_research_error(
                WalkForwardSplitError,
                "phase3b_outcome_overlap",
                "training or previous assessment label outcome reaches the next assessment",
            )
        if exits[training_end - 1] != sessions[assessment_start - 1]:
            raise_research_error(
                WalkForwardSplitError,
                "phase3b_purge_mismatch",
                "six excluded prediction rows do not align with the final training label exit",
            )
        if len(set(targets[:training_end])) < 2:
            raise_research_error(
                WalkForwardSplitError,
                "phase3b_one_class_training",
                "training requires both classes",
            )
        folds.append(
            DisjointFold(
                index=len(folds),
                training_rows=tuple(range(training_end)),
                purge_rows=tuple(range(training_end, assessment_start)),
                assessment_rows=tuple(range(assessment_start, assessment_end)),
                training_last_prediction=sessions[training_end - 1],
                training_last_exit=exits[training_end - 1],
                purge_sessions=sessions[training_end:assessment_start],
                assessment_first_prediction=sessions[assessment_start],
                assessment_last_prediction=sessions[assessment_end - 1],
                assessment_last_exit=exits[assessment_end - 1],
            )
        )
        previous_last_exit = exits[assessment_end - 1]
        training_end = assessment_end
    if not folds:
        raise_research_error(
            WalkForwardSplitError, "phase3b_insufficient_rows", "no valid assessment window"
        )
    return tuple(folds), len(labels) - training_end
