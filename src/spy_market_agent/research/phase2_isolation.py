from __future__ import annotations

from datetime import date
from typing import NoReturn, cast

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from spy_market_agent.benchmark.artifacts import sha256_json
from spy_market_agent.benchmark.errors import BenchmarkSplitError
from spy_market_agent.benchmark.locks import SPLIT_POLICY_ID
from spy_market_agent.benchmark.splits import (
    EXIT_OFFSET_SESSIONS as PHASE2_EXIT_OFFSET_SESSIONS,
)
from spy_market_agent.benchmark.splits import (
    FEATURE_WARMUP_ROWS as PHASE2_FEATURE_WARMUP_ROWS,
)
from spy_market_agent.benchmark.splits import phase2_split_layout_from_prediction_sessions
from spy_market_agent.features.models import (
    FeatureEngineeringError,
    validate_strictly_increasing_sessions,
)
from spy_market_agent.market_data.acquisition import (
    PHASE1_MANIFEST_SCHEMA_VERSION,
    PHASE1_SCHEMA_VERSION,
    DatasetManifest,
)
from spy_market_agent.market_data.checksum import compute_market_data_checksum
from spy_market_agent.market_data.models import MarketDataBatch, MarketDataMetadata
from spy_market_agent.research.constants import PHASE3_ARTIFACT_SCHEMA_VERSION
from spy_market_agent.research.errors import ResearchRegistryError, raise_research_error
from spy_market_agent.research.models import CalibrationSplit, WalkForwardManifest
from spy_market_agent.research.types import ResearchSupervisedDatasetLike

PHASE2_FINAL_TEST_EXCLUSION_POLICY_ID = "phase2-final-test-session-exclusion-v1"
RESEARCH_SLICE_ID_VERSION = "phase3-development-research-slice-v1"


class Phase2FinalTestExclusionBoundary(BaseModel):
    model_config = ConfigDict(frozen=True)

    artifact_schema_version: str = PHASE3_ARTIFACT_SCHEMA_VERSION
    exclusion_policy_id: str = PHASE2_FINAL_TEST_EXCLUSION_POLICY_ID
    split_policy_id: str = SPLIT_POLICY_ID
    research_slice_id: str
    research_slice_checksum: str
    parent_phase1_dataset_id: str
    parent_phase1_canonical_content_checksum: str
    parent_canonical_market_data_checksum: str
    parent_first_session: date
    parent_last_session: date
    parent_source_row_count: int
    phase2_feature_warmup_rows: int = PHASE2_FEATURE_WARMUP_ROWS
    phase2_exit_offset_sessions: int = PHASE2_EXIT_OFFSET_SESSIONS
    phase2_supervised_row_count: int
    phase2_final_test_prediction_sessions: tuple[date, ...]
    phase2_final_test_first_prediction_session: date
    phase2_final_test_last_prediction_session: date
    phase2_final_test_prediction_session_count: int
    eligible_source_first_session: date
    eligible_source_last_session: date
    eligible_source_row_count: int
    eligible_development_first_prediction_session: date
    eligible_development_last_prediction_session: date
    eligible_development_prediction_row_count: int
    global_feature_warmup_rows: int
    excluded_source_session_count: int

    @field_validator(
        "research_slice_id",
        "parent_phase1_dataset_id",
        "exclusion_policy_id",
        "split_policy_id",
    )
    @classmethod
    def _nonempty_identifier(cls, value: str) -> str:
        if not value.strip():
            msg = "Phase 2 exclusion identifiers must be nonempty."
            raise ValueError(msg)
        return value

    @field_validator(
        "research_slice_checksum",
        "parent_phase1_canonical_content_checksum",
        "parent_canonical_market_data_checksum",
    )
    @classmethod
    def _checksum(cls, value: str) -> str:
        allowed = set("0123456789abcdef")
        if len(value) != 64 or any(character not in allowed for character in value):
            msg = "Phase 2 exclusion checksums must be lowercase SHA-256 hex digests."
            raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def _validate_boundary(self) -> Phase2FinalTestExclusionBoundary:
        final_sessions = self.phase2_final_test_prediction_sessions
        if not final_sessions:
            msg = "Phase 2 final-test prediction sessions must not be empty."
            raise ValueError(msg)
        if final_sessions != tuple(sorted(final_sessions)) or len(final_sessions) != len(
            set(final_sessions)
        ):
            msg = "Phase 2 final-test prediction sessions must be unique and increasing."
            raise ValueError(msg)
        if final_sessions[0] != self.phase2_final_test_first_prediction_session:
            msg = "first protected prediction session mismatch."
            raise ValueError(msg)
        if final_sessions[-1] != self.phase2_final_test_last_prediction_session:
            msg = "last protected prediction session mismatch."
            raise ValueError(msg)
        if len(final_sessions) != self.phase2_final_test_prediction_session_count:
            msg = "protected prediction session count mismatch."
            raise ValueError(msg)
        if self.parent_first_session > self.parent_last_session:
            msg = "parent session range must be increasing."
            raise ValueError(msg)
        if self.eligible_source_first_session > self.eligible_source_last_session:
            msg = "eligible source range must be increasing."
            raise ValueError(msg)
        if (
            self.eligible_development_first_prediction_session
            > self.eligible_development_last_prediction_session
        ):
            msg = "eligible development prediction range must be increasing."
            raise ValueError(msg)
        if (
            self.eligible_development_last_prediction_session
            >= self.phase2_final_test_first_prediction_session
        ):
            msg = "eligible development predictions must precede Phase 2 final-test predictions."
            raise ValueError(msg)
        if self.eligible_source_row_count + self.excluded_source_session_count != (
            self.parent_source_row_count
        ):
            msg = "eligible and excluded source rows must sum to parent source row count."
            raise ValueError(msg)
        return self


def apply_phase2_final_test_session_isolation(
    *,
    manifest: DatasetManifest,
    market_data: MarketDataBatch,
    global_feature_warmup_rows: int,
) -> tuple[MarketDataBatch, Phase2FinalTestExclusionBoundary]:
    boundary = derive_phase2_final_test_exclusion_boundary(
        manifest=manifest,
        market_data=market_data,
        global_feature_warmup_rows=global_feature_warmup_rows,
    )
    sliced = _slice_market_data(market_data, boundary=boundary)
    return sliced, boundary


def derive_phase2_final_test_exclusion_boundary(
    *,
    manifest: DatasetManifest,
    market_data: MarketDataBatch,
    global_feature_warmup_rows: int,
) -> Phase2FinalTestExclusionBoundary:
    _validate_parent_lineage(manifest=manifest, market_data=market_data)
    source = market_data.data.copy(deep=True).reset_index(drop=True)
    source_sessions = tuple(cast(date, value) for value in source["session"].to_list())
    try:
        validated_source_sessions = validate_strictly_increasing_sessions(source["session"])
    except FeatureEngineeringError as exc:
        _raise_isolation_error(
            "invalid_parent_source_sessions",
            f"parent source sessions must be unique and strictly increasing: {exc}",
        )
    if source_sessions != validated_source_sessions:
        _raise_isolation_error(
            "invalid_parent_source_sessions",
            "parent source sessions must be unique and strictly increasing.",
        )
    if len(source_sessions) <= PHASE2_FEATURE_WARMUP_ROWS + PHASE2_EXIT_OFFSET_SESSIONS:
        _raise_isolation_error(
            "phase2_boundary_unavailable",
            "parent dataset is too short to reconstruct the Phase 2 prediction universe.",
        )
    phase2_prediction_sessions = source_sessions[
        PHASE2_FEATURE_WARMUP_ROWS : len(source_sessions) - PHASE2_EXIT_OFFSET_SESSIONS
    ]
    try:
        layout = phase2_split_layout_from_prediction_sessions(phase2_prediction_sessions)
    except BenchmarkSplitError as exc:
        _raise_isolation_error(
            "phase2_boundary_unavailable",
            f"could not reconstruct the frozen Phase 2 split boundary: {exc}",
        )
    if layout.split_policy_id != SPLIT_POLICY_ID:
        _raise_isolation_error(
            "phase2_split_policy_mismatch",
            "reconstructed Phase 2 split policy does not match the accepted policy.",
        )
    final_sessions = layout.final_test_included_sessions
    final_start_session = final_sessions[0]
    try:
        final_start_source_index = source_sessions.index(final_start_session)
    except ValueError:
        _raise_isolation_error(
            "phase2_boundary_session_missing",
            "first Phase 2 final-test prediction session is absent from parent source data.",
        )
    eligible_source_row_count = final_start_source_index
    if eligible_source_row_count <= global_feature_warmup_rows + PHASE2_EXIT_OFFSET_SESSIONS:
        _raise_isolation_error(
            "insufficient_pre_final_test_research_rows",
            (
                "Phase 3 development slice would not have labelable rows before "
                "the Phase 2 final test."
            ),
        )
    eligible_source_sessions = source_sessions[:eligible_source_row_count]
    eligible_prediction_sessions = eligible_source_sessions[
        global_feature_warmup_rows : len(eligible_source_sessions) - PHASE2_EXIT_OFFSET_SESSIONS
    ]
    if not eligible_prediction_sessions:
        _raise_isolation_error(
            "empty_phase3_development_prediction_universe",
            "Phase 3 development slice produced no eligible prediction sessions.",
        )
    if eligible_prediction_sessions[-1] >= final_start_session:
        _raise_isolation_error(
            "phase3_development_prediction_crosses_phase2_final_test",
            "Phase 3 development predictions must stop before the Phase 2 final test.",
        )
    research_slice = source.iloc[:eligible_source_row_count].copy(deep=True).reset_index(drop=True)
    research_slice_checksum = compute_market_data_checksum(research_slice)
    slice_id_payload = {
        "version": RESEARCH_SLICE_ID_VERSION,
        "parent_phase1_dataset_id": manifest.dataset_id,
        "parent_phase1_canonical_content_checksum": manifest.canonical_content_checksum,
        "parent_canonical_market_data_checksum": market_data.metadata.dataset_checksum,
        "split_policy_id": SPLIT_POLICY_ID,
        "exclusion_policy_id": PHASE2_FINAL_TEST_EXCLUSION_POLICY_ID,
        "phase2_final_test_first_prediction_session": final_start_session,
        "phase2_final_test_last_prediction_session": final_sessions[-1],
        "phase2_final_test_prediction_session_count": len(final_sessions),
        "eligible_source_first_session": eligible_source_sessions[0],
        "eligible_source_last_session": eligible_source_sessions[-1],
        "eligible_source_row_count": len(eligible_source_sessions),
        "eligible_development_first_prediction_session": eligible_prediction_sessions[0],
        "eligible_development_last_prediction_session": eligible_prediction_sessions[-1],
        "eligible_development_prediction_row_count": len(eligible_prediction_sessions),
        "global_feature_warmup_rows": global_feature_warmup_rows,
        "research_slice_checksum": research_slice_checksum,
    }
    return Phase2FinalTestExclusionBoundary(
        research_slice_id=f"spy-v2p3-dev-slice-{sha256_json(slice_id_payload)[:24]}",
        research_slice_checksum=research_slice_checksum,
        parent_phase1_dataset_id=manifest.dataset_id,
        parent_phase1_canonical_content_checksum=manifest.canonical_content_checksum,
        parent_canonical_market_data_checksum=market_data.metadata.dataset_checksum,
        parent_first_session=source_sessions[0],
        parent_last_session=source_sessions[-1],
        parent_source_row_count=len(source_sessions),
        phase2_supervised_row_count=layout.supervised_row_count,
        phase2_final_test_prediction_sessions=final_sessions,
        phase2_final_test_first_prediction_session=final_start_session,
        phase2_final_test_last_prediction_session=final_sessions[-1],
        phase2_final_test_prediction_session_count=len(final_sessions),
        eligible_source_first_session=eligible_source_sessions[0],
        eligible_source_last_session=eligible_source_sessions[-1],
        eligible_source_row_count=len(eligible_source_sessions),
        eligible_development_first_prediction_session=eligible_prediction_sessions[0],
        eligible_development_last_prediction_session=eligible_prediction_sessions[-1],
        eligible_development_prediction_row_count=len(eligible_prediction_sessions),
        global_feature_warmup_rows=global_feature_warmup_rows,
        excluded_source_session_count=len(source_sessions) - len(eligible_source_sessions),
    )


def validate_phase2_session_isolation(
    boundary: Phase2FinalTestExclusionBoundary,
    *,
    supervised: ResearchSupervisedDatasetLike | None = None,
    fold_manifest: WalkForwardManifest | None = None,
    calibration_splits: tuple[CalibrationSplit, ...] = (),
    diagnostic_assessment_sessions: tuple[date, ...] = (),
) -> None:
    protected_sessions = set(boundary.phase2_final_test_prediction_sessions)
    if supervised is not None:
        labels = supervised.labels.reset_index(drop=True)
        prediction_sessions = tuple(cast(date, value) for value in labels["session"].to_list())
        entry_sessions = tuple(cast(date, value) for value in labels["entry_session"].to_list())
        exit_sessions = tuple(cast(date, value) for value in labels["exit_session"].to_list())
        _reject_intersection(
            protected_sessions,
            prediction_sessions,
            scope="research_label_prediction_sessions",
        )
        _reject_intersection(
            protected_sessions,
            entry_sessions,
            scope="research_label_entry_sessions",
        )
        _reject_intersection(
            protected_sessions,
            exit_sessions,
            scope="research_label_exit_sessions",
        )
        crossing_exits = tuple(
            session
            for session in exit_sessions
            if session >= boundary.phase2_final_test_first_prediction_session
        )
        if crossing_exits:
            _raise_isolation_error(
                "phase3_label_exit_crosses_phase2_final_test_boundary",
                (
                    "Phase 3 development labels must exit before the Phase 2 final-test "
                    f"prediction partition begins; first crossing exit={crossing_exits[0]}."
                ),
            )
    if fold_manifest is not None:
        if fold_manifest.dataset_lineage.dataset_id != boundary.research_slice_id:
            _raise_isolation_error(
                "phase3_fold_dataset_lineage_mismatch",
                "fold manifest must use the Phase 3 development research slice identity.",
            )
        if (
            fold_manifest.dataset_lineage.canonical_dataset_checksum
            != boundary.research_slice_checksum
        ):
            _raise_isolation_error(
                "phase3_fold_checksum_lineage_mismatch",
                "fold manifest checksum must match the Phase 3 development research slice.",
            )
        for fold in fold_manifest.folds:
            for scope, sessions in (
                ("fold_training_prediction_sessions", fold.training.prediction_sessions),
                ("fold_training_entry_sessions", fold.training.entry_sessions),
                ("fold_training_exit_sessions", fold.training.exit_sessions),
                ("fold_boundary_excluded_sessions", fold.boundary_excluded_sessions),
                ("fold_assessment_prediction_sessions", fold.assessment.prediction_sessions),
                ("fold_assessment_entry_sessions", fold.assessment.entry_sessions),
                ("fold_assessment_exit_sessions", fold.assessment.exit_sessions),
            ):
                _reject_intersection(protected_sessions, sessions, scope=scope)
            if (
                fold.assessment.last_exit_session
                >= boundary.phase2_final_test_first_prediction_session
            ):
                _raise_isolation_error(
                    "phase3_fold_exit_crosses_phase2_final_test_boundary",
                    (
                        "Phase 3 fold assessment exits must precede the Phase 2 final-test "
                        f"prediction partition; fold_id={fold.fold_id}."
                    ),
                )
    for split in calibration_splits:
        for scope, sessions in (
            ("calibration_estimator_training_sessions", split.estimator_training_sessions),
            (
                "calibration_inner_boundary_excluded_sessions",
                split.inner_boundary_excluded_sessions,
            ),
            ("calibration_sessions", split.calibration_sessions),
            (
                "calibration_outer_boundary_excluded_sessions",
                split.outer_boundary_excluded_sessions,
            ),
        ):
            _reject_intersection(protected_sessions, sessions, scope=scope)
        if split.calibration_sessions[-1] >= boundary.phase2_final_test_first_prediction_session:
            _raise_isolation_error(
                "phase3_calibration_crosses_phase2_final_test_boundary",
                (
                    "Phase 3 calibration rows must precede the Phase 2 final-test "
                    "prediction partition."
                ),
            )
    _reject_intersection(
        protected_sessions,
        diagnostic_assessment_sessions,
        scope="diagnostic_assessment_sessions",
    )


def _validate_parent_lineage(*, manifest: DatasetManifest, market_data: MarketDataBatch) -> None:
    if manifest.canonical_schema_version != PHASE1_SCHEMA_VERSION:
        _raise_isolation_error(
            "phase2_parent_canonical_schema_mismatch",
            "verified manifest must use the accepted Phase 1 canonical schema.",
        )
    if manifest.manifest_schema_version != PHASE1_MANIFEST_SCHEMA_VERSION:
        _raise_isolation_error(
            "phase2_parent_manifest_schema_mismatch",
            "verified manifest must use the accepted Phase 1 manifest schema.",
        )
    if manifest.symbol != market_data.metadata.symbol:
        _raise_isolation_error(
            "phase2_parent_symbol_mismatch",
            "verified manifest symbol must match parent market data.",
        )
    if manifest.timeframe != market_data.metadata.timeframe:
        _raise_isolation_error(
            "phase2_parent_timeframe_mismatch",
            "verified manifest timeframe must match parent market data.",
        )
    if manifest.row_count != market_data.metadata.row_count:
        _raise_isolation_error(
            "phase2_parent_row_count_mismatch",
            "verified manifest row count must match parent market data.",
        )
    if manifest.actual_first_session != market_data.metadata.first_session:
        _raise_isolation_error(
            "phase2_parent_first_session_mismatch",
            "verified manifest first session must match parent market data.",
        )
    if manifest.actual_last_session != market_data.metadata.last_session:
        _raise_isolation_error(
            "phase2_parent_last_session_mismatch",
            "verified manifest last session must match parent market data.",
        )


def _slice_market_data(
    market_data: MarketDataBatch,
    *,
    boundary: Phase2FinalTestExclusionBoundary,
) -> MarketDataBatch:
    data = market_data.data.iloc[: boundary.eligible_source_row_count].copy(deep=True)
    data = data.reset_index(drop=True)
    checksum = compute_market_data_checksum(data)
    if checksum != boundary.research_slice_checksum:
        _raise_isolation_error(
            "phase3_research_slice_checksum_mismatch",
            "research slice checksum must match the Phase 2 final-test exclusion boundary.",
        )
    metadata = MarketDataMetadata(
        provider_name=market_data.metadata.provider_name,
        symbol=market_data.metadata.symbol,
        timeframe=market_data.metadata.timeframe,
        adjustment_policy=market_data.metadata.adjustment_policy,
        downloaded_at=market_data.metadata.downloaded_at,
        created_at=market_data.metadata.created_at,
        first_session=boundary.eligible_source_first_session,
        last_session=boundary.eligible_source_last_session,
        row_count=len(data),
        dataset_checksum=checksum,
        schema_version=market_data.metadata.schema_version,
        source_description=(
            "Phase 3 development research slice excluding the accepted Phase 2 final-test "
            "prediction-session boundary."
        ),
    )
    return MarketDataBatch(data=data, metadata=metadata)


def _reject_intersection(
    protected_sessions: set[date],
    sessions: tuple[date, ...],
    *,
    scope: str,
) -> None:
    overlap = tuple(sorted(set(sessions).intersection(protected_sessions)))
    if overlap:
        _raise_isolation_error(
            "phase2_final_test_session_intersection",
            f"{scope} intersects Phase 2 final-test prediction sessions: {overlap[0]}.",
        )


def _raise_isolation_error(code: str, message: str) -> NoReturn:
    raise_research_error(ResearchRegistryError, code, message)
