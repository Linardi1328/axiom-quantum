from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from spy_market_agent.market_data.calendar import MARKET_CALENDAR, MARKET_TIMEZONE
from spy_market_agent.market_data.models import (
    MARKET_SYMBOL,
    MARKET_TIMEFRAME,
    require_utc_datetime,
)

PHASE4_SHADOW_SCHEMA_VERSION = "spy-v2-phase4-shadow-v1"
PHASE4_SHADOW_RUN_ID_VERSION = "spy-v2-phase4-shadow-run-id-v1"
PHASE4_PHASE_ID = "v2-phase-04"
PHASE4_TARGET_RELEASE = "v2.0.0-beta.1"
OBSERVATION_ONLY_MODE = "observation_only_no_model"
MODEL_CONNECTED_MODE = "model_connected"
BLOCKED_NO_APPROVED_MODEL = "blocked_no_approved_model"
NO_APPROVED_SHADOW_MODEL = "NO APPROVED SHADOW MODEL"


class ShadowMode(StrEnum):
    OBSERVATION_ONLY_NO_MODEL = OBSERVATION_ONLY_MODE
    MODEL_CONNECTED = MODEL_CONNECTED_MODE


class ShadowHealthStatus(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    BLOCKED = "blocked"


class ShadowRunStatus(StrEnum):
    OBSERVATION_READY = "observation_ready"
    MODEL_INFERENCE_READY = "model_inference_ready"
    BLOCKED = "blocked"


class ModelAdmissionStatus(StrEnum):
    NOT_REQUIRED_OBSERVATION_ONLY = "not_required_observation_only"
    BLOCKED_NO_APPROVED_MODEL = BLOCKED_NO_APPROVED_MODEL
    APPROVED_FOR_SHADOW = "approved_for_shadow"
    REJECTED_NOT_APPROVED = "rejected_not_approved"


class FreshnessStatus(StrEnum):
    FRESH = "fresh"
    BLOCKED = "blocked"


class ProposalStatus(StrEnum):
    NOT_GENERATED_OBSERVATION_ONLY = "not_generated_observation_only"
    MODEL_GATE_LOCKED = "model_gate_locked"
    SCAFFOLDED_NOT_EXECUTABLE = "scaffolded_not_executable"


class HypotheticalTargetState(StrEnum):
    LONG = "LONG"
    CASH = "CASH"


def _require_nonempty_text(value: str, *, field_name: str) -> str:
    trimmed = value.strip()
    if not trimmed:
        msg = f"{field_name} must be nonempty."
        raise ValueError(msg)
    return trimmed


def _require_safe_identifier(value: str, *, field_name: str) -> str:
    trimmed = _require_nonempty_text(value, field_name=field_name)
    if "/" in trimmed or "\\" in trimmed or ".." in trimmed:
        msg = f"{field_name} must be path-safe."
        raise ValueError(msg)
    return trimmed


def _require_sha256(value: str, *, field_name: str) -> str:
    allowed = set("0123456789abcdef")
    if len(value) != 64 or any(character not in allowed for character in value):
        msg = f"{field_name} must be a lowercase SHA-256 hex digest."
        raise ValueError(msg)
    return value


def _require_spy(value: str, *, field_name: str) -> str:
    if value != MARKET_SYMBOL:
        msg = f"{field_name} must be {MARKET_SYMBOL!r} for Phase 4 shadow mode."
        raise ValueError(msg)
    return value


def _require_daily(value: str, *, field_name: str) -> str:
    if value != MARKET_TIMEFRAME:
        msg = f"{field_name} must be {MARKET_TIMEFRAME!r} for Phase 4 shadow mode."
        raise ValueError(msg)
    return value


class ShadowBaseModel(BaseModel):
    model_config = ConfigDict(frozen=True)

    artifact_schema_version: str = PHASE4_SHADOW_SCHEMA_VERSION

    @field_validator("artifact_schema_version")
    @classmethod
    def _validate_artifact_schema_version(cls, value: str) -> str:
        if value != PHASE4_SHADOW_SCHEMA_VERSION:
            msg = f"artifact_schema_version must be {PHASE4_SHADOW_SCHEMA_VERSION!r}."
            raise ValueError(msg)
        return value


class ShadowRunConfiguration(ShadowBaseModel):
    phase_id: Literal["v2-phase-04"] = "v2-phase-04"
    configuration_version: str
    mode: ShadowMode = ShadowMode.OBSERVATION_ONLY_NO_MODEL
    symbol: str = MARKET_SYMBOL
    timeframe: str = MARKET_TIMEFRAME
    exchange_calendar: str = MARKET_CALENDAR
    market_timezone: str = MARKET_TIMEZONE
    provider_finalization_policy_id: str
    requires_provider_finalized: bool = True

    @field_validator("configuration_version", "provider_finalization_policy_id")
    @classmethod
    def _safe_text(cls, value: str, info: Any) -> str:
        return _require_safe_identifier(value, field_name=info.field_name)

    @field_validator("symbol")
    @classmethod
    def _symbol(cls, value: str) -> str:
        return _require_spy(value, field_name="symbol")

    @field_validator("timeframe")
    @classmethod
    def _timeframe(cls, value: str) -> str:
        return _require_daily(value, field_name="timeframe")

    @field_validator("exchange_calendar")
    @classmethod
    def _exchange_calendar(cls, value: str) -> str:
        if value != MARKET_CALENDAR:
            msg = f"exchange_calendar must be {MARKET_CALENDAR!r}."
            raise ValueError(msg)
        return value

    @field_validator("market_timezone")
    @classmethod
    def _market_timezone(cls, value: str) -> str:
        if value != MARKET_TIMEZONE:
            msg = f"market_timezone must be {MARKET_TIMEZONE!r}."
            raise ValueError(msg)
        return value


class DataSnapshotLineage(ShadowBaseModel):
    dataset_id: str
    canonical_dataset_checksum: str
    provider: str
    feed: str
    timeframe: str = MARKET_TIMEFRAME
    adjustment: str
    symbol: str = MARKET_SYMBOL
    session: date
    first_session: date | None = None
    latest_session: date | None = None
    manifest_artifact_checksum: str | None = None
    row_count: int = Field(gt=0)

    @field_validator("dataset_id", "provider", "feed", "adjustment")
    @classmethod
    def _safe_text(cls, value: str, info: Any) -> str:
        return _require_safe_identifier(value, field_name=info.field_name)

    @field_validator("canonical_dataset_checksum")
    @classmethod
    def _checksum(cls, value: str) -> str:
        return _require_sha256(value, field_name="canonical_dataset_checksum")

    @field_validator("manifest_artifact_checksum")
    @classmethod
    def _manifest_checksum(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_sha256(value, field_name="manifest_artifact_checksum")

    @field_validator("symbol")
    @classmethod
    def _symbol(cls, value: str) -> str:
        return _require_spy(value, field_name="symbol")

    @field_validator("timeframe")
    @classmethod
    def _timeframe(cls, value: str) -> str:
        return _require_daily(value, field_name="timeframe")

    @model_validator(mode="after")
    def _validate_session_range(self) -> DataSnapshotLineage:
        if (
            self.first_session is not None
            and self.latest_session is not None
            and self.first_session > self.latest_session
        ):
            msg = "first_session must not be after latest_session."
            raise ValueError(msg)
        if self.latest_session is not None and self.session != self.latest_session:
            msg = "session must match latest_session for operational shadow snapshots."
            raise ValueError(msg)
        return self


class ShadowModelMetadata(ShadowBaseModel):
    model_id: str
    experiment_id: str
    campaign_id: str
    model_artifact_checksum: str
    feature_schema: str
    label_schema: str
    git_commit_sha: str
    source_lineage: str
    approval_status: str
    approved_for_shadow: bool = False

    @field_validator(
        "model_id",
        "experiment_id",
        "campaign_id",
        "feature_schema",
        "label_schema",
        "git_commit_sha",
        "source_lineage",
        "approval_status",
    )
    @classmethod
    def _safe_text(cls, value: str, info: Any) -> str:
        return _require_safe_identifier(value, field_name=info.field_name)

    @field_validator("model_artifact_checksum")
    @classmethod
    def _checksum(cls, value: str) -> str:
        return _require_sha256(value, field_name="model_artifact_checksum")

    @model_validator(mode="after")
    def _validate_approval(self) -> ShadowModelMetadata:
        if self.approved_for_shadow and self.approval_status != "approved":
            msg = "approved_for_shadow requires approval_status='approved'."
            raise ValueError(msg)
        return self


class DailyMarketDataStatus(ShadowBaseModel):
    symbol: str = MARKET_SYMBOL
    timeframe: str = MARKET_TIMEFRAME
    exchange_calendar: str = MARKET_CALENDAR
    adjustment: str
    session: date
    as_of: datetime
    provider_finalized: bool
    expected_session_present: bool = True
    session_complete: bool = True
    duplicate_sessions_detected: bool = False
    out_of_order_sessions_detected: bool = False
    ohlcv_valid: bool = True
    stale: bool = False

    @field_validator("symbol")
    @classmethod
    def _symbol(cls, value: str) -> str:
        return _require_spy(value, field_name="symbol")

    @field_validator("timeframe")
    @classmethod
    def _timeframe(cls, value: str) -> str:
        return _require_daily(value, field_name="timeframe")

    @field_validator("exchange_calendar")
    @classmethod
    def _exchange_calendar(cls, value: str) -> str:
        if value != MARKET_CALENDAR:
            msg = f"exchange_calendar must be {MARKET_CALENDAR!r}."
            raise ValueError(msg)
        return value

    @field_validator("adjustment")
    @classmethod
    def _adjustment(cls, value: str) -> str:
        return _require_safe_identifier(value, field_name="adjustment")

    @field_validator("as_of")
    @classmethod
    def _utc_as_of(cls, value: datetime) -> datetime:
        return require_utc_datetime(value, field_name="as_of")


class FreshnessDecision(ShadowBaseModel):
    status: FreshnessStatus
    health_status: ShadowHealthStatus
    eligible: bool
    reasons: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _validate_consistency(self) -> FreshnessDecision:
        if self.eligible and self.reasons:
            msg = "eligible freshness decisions must not contain refusal reasons."
            raise ValueError(msg)
        if self.eligible and (
            self.status != FreshnessStatus.FRESH or self.health_status != ShadowHealthStatus.HEALTHY
        ):
            msg = "eligible freshness decisions must be fresh and healthy."
            raise ValueError(msg)
        if not self.eligible and self.health_status != ShadowHealthStatus.BLOCKED:
            msg = "ineligible freshness decisions must be blocked."
            raise ValueError(msg)
        return self


class ModelAdmissionDecision(ShadowBaseModel):
    status: ModelAdmissionStatus
    inference_allowed: bool
    model_id: str | None = None
    reasons: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _validate_consistency(self) -> ModelAdmissionDecision:
        if self.inference_allowed:
            if self.status != ModelAdmissionStatus.APPROVED_FOR_SHADOW or self.model_id is None:
                msg = "approved model admission decisions must include a model_id."
                raise ValueError(msg)
            if self.reasons:
                msg = "approved model admission decisions must not contain refusal reasons."
                raise ValueError(msg)
        return self


class ShadowRunRequest(ShadowBaseModel):
    configuration: ShadowRunConfiguration
    data_lineage: DataSnapshotLineage
    signal_session: date
    feature_schema: str
    as_of: datetime
    model_metadata: ShadowModelMetadata | None = None

    @field_validator("feature_schema")
    @classmethod
    def _feature_schema(cls, value: str) -> str:
        return _require_safe_identifier(value, field_name="feature_schema")

    @field_validator("as_of")
    @classmethod
    def _utc_as_of(cls, value: datetime) -> datetime:
        return require_utc_datetime(value, field_name="as_of")

    @model_validator(mode="after")
    def _validate_alignment(self) -> ShadowRunRequest:
        if self.signal_session != self.data_lineage.session:
            msg = "signal_session must match data_lineage.session."
            raise ValueError(msg)
        if (
            self.configuration.mode == ShadowMode.OBSERVATION_ONLY_NO_MODEL
            and self.model_metadata is not None
        ):
            msg = "observation-only shadow requests must not carry model metadata."
            raise ValueError(msg)
        if (
            self.model_metadata is not None
            and self.model_metadata.feature_schema != self.feature_schema
        ):
            msg = "model_metadata.feature_schema must match request feature_schema."
            raise ValueError(msg)
        return self


class ShadowRunDecision(ShadowBaseModel):
    shadow_run_id: str
    mode: ShadowMode
    run_status: ShadowRunStatus
    observation_allowed: bool
    model_inference_allowed: bool
    admission_status: ModelAdmissionStatus
    freshness_status: FreshnessStatus
    monitoring_status: ShadowHealthStatus
    refusal_reasons: tuple[str, ...] = ()

    @field_validator("shadow_run_id")
    @classmethod
    def _safe_text(cls, value: str) -> str:
        return _require_safe_identifier(value, field_name="shadow_run_id")

    @model_validator(mode="after")
    def _validate_consistency(self) -> ShadowRunDecision:
        if self.run_status == ShadowRunStatus.MODEL_INFERENCE_READY:
            if self.mode != ShadowMode.MODEL_CONNECTED:
                msg = "MODEL_INFERENCE_READY requires model-connected mode."
                raise ValueError(msg)
            if not self.model_inference_allowed:
                msg = "MODEL_INFERENCE_READY requires model_inference_allowed=true."
                raise ValueError(msg)
            if self.admission_status != ModelAdmissionStatus.APPROVED_FOR_SHADOW:
                msg = "MODEL_INFERENCE_READY requires approved model admission."
                raise ValueError(msg)
            if self.freshness_status != FreshnessStatus.FRESH:
                msg = "MODEL_INFERENCE_READY requires fresh market data."
                raise ValueError(msg)
            if self.monitoring_status != ShadowHealthStatus.HEALTHY:
                msg = "MODEL_INFERENCE_READY requires healthy monitoring."
                raise ValueError(msg)
            if self.refusal_reasons:
                msg = "MODEL_INFERENCE_READY must not include refusal reasons."
                raise ValueError(msg)
        if self.run_status == ShadowRunStatus.OBSERVATION_READY:
            if self.mode != ShadowMode.OBSERVATION_ONLY_NO_MODEL:
                msg = "OBSERVATION_READY requires observation-only mode."
                raise ValueError(msg)
            if not self.observation_allowed:
                msg = "OBSERVATION_READY requires observation_allowed=true."
                raise ValueError(msg)
            if self.model_inference_allowed:
                msg = "OBSERVATION_READY must not allow model inference."
                raise ValueError(msg)
        if self.run_status == ShadowRunStatus.BLOCKED and self.model_inference_allowed:
            msg = "BLOCKED shadow decisions must not allow model inference."
            raise ValueError(msg)
        if (
            self.model_inference_allowed
            and self.run_status != ShadowRunStatus.MODEL_INFERENCE_READY
        ):
            msg = "model_inference_allowed requires MODEL_INFERENCE_READY."
            raise ValueError(msg)
        return self


class ShadowMonitoringEvent(ShadowBaseModel):
    code: str
    message: str
    status: ShadowHealthStatus

    @field_validator("code")
    @classmethod
    def _code(cls, value: str) -> str:
        return _require_safe_identifier(value, field_name="code")

    @field_validator("message")
    @classmethod
    def _message(cls, value: str) -> str:
        return _require_nonempty_text(value, field_name="message")


class ShadowMonitoringState(ShadowBaseModel):
    status: ShadowHealthStatus
    events: tuple[ShadowMonitoringEvent, ...] = ()


class ShadowProposal(ShadowBaseModel):
    shadow_run_id: str
    symbol: str = MARKET_SYMBOL
    signal_session: date
    generated_at: datetime
    mode: ShadowMode
    model_id: str | None = None
    model_checksum: str | None = None
    feature_schema: str
    data_lineage: DataSnapshotLineage
    predicted_probability: float | None = None
    score: float | None = None
    hypothetical_target_state: HypotheticalTargetState | None = None
    admission_status: ModelAdmissionStatus
    freshness_status: FreshnessStatus
    monitoring_status: ShadowHealthStatus
    proposal_status: ProposalStatus

    @field_validator("shadow_run_id", "feature_schema")
    @classmethod
    def _safe_text(cls, value: str, info: Any) -> str:
        return _require_safe_identifier(value, field_name=info.field_name)

    @field_validator("symbol")
    @classmethod
    def _symbol(cls, value: str) -> str:
        return _require_spy(value, field_name="symbol")

    @field_validator("generated_at")
    @classmethod
    def _utc_generated_at(cls, value: datetime) -> datetime:
        return require_utc_datetime(value, field_name="generated_at")

    @field_validator("model_checksum")
    @classmethod
    def _model_checksum(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_sha256(value, field_name="model_checksum")

    @field_validator("predicted_probability")
    @classmethod
    def _probability(cls, value: float | None) -> float | None:
        if value is None:
            return None
        if not 0.0 <= value <= 1.0:
            msg = "predicted_probability must be within [0, 1]."
            raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def _validate_consistency(self) -> ShadowProposal:
        if self.mode == ShadowMode.OBSERVATION_ONLY_NO_MODEL:
            if self.model_id is not None:
                msg = "observation-only proposals must not include model_id."
                raise ValueError(msg)
            if self.model_checksum is not None:
                msg = "observation-only proposals must not include model_checksum."
                raise ValueError(msg)
            if self.predicted_probability is not None:
                msg = "observation-only proposals must not include predicted_probability."
                raise ValueError(msg)
            if self.score is not None:
                msg = "observation-only proposals must not include model score."
                raise ValueError(msg)
            if self.hypothetical_target_state is not None:
                msg = "observation-only proposals must not include a LONG/CASH target."
                raise ValueError(msg)
            if self.admission_status == ModelAdmissionStatus.APPROVED_FOR_SHADOW:
                msg = "observation-only proposals must not claim approved model admission."
                raise ValueError(msg)
            if self.proposal_status != ProposalStatus.NOT_GENERATED_OBSERVATION_ONLY:
                msg = "observation-only proposals must use NOT_GENERATED_OBSERVATION_ONLY."
                raise ValueError(msg)
        if self.mode == ShadowMode.MODEL_CONNECTED:
            if self.model_id is None:
                msg = "model-connected proposals require model_id."
                raise ValueError(msg)
            if self.model_checksum is None:
                msg = "model-connected proposals require model_checksum."
                raise ValueError(msg)
            if self.admission_status != ModelAdmissionStatus.APPROVED_FOR_SHADOW:
                msg = "model-connected proposals require approved model admission."
                raise ValueError(msg)
            if self.freshness_status != FreshnessStatus.FRESH:
                msg = "model-connected proposals require fresh market data."
                raise ValueError(msg)
            if self.monitoring_status != ShadowHealthStatus.HEALTHY:
                msg = "model-connected proposals require healthy monitoring."
                raise ValueError(msg)
            if self.proposal_status != ProposalStatus.SCAFFOLDED_NOT_EXECUTABLE:
                msg = "model-connected proposals must remain scaffolded and non-executable."
                raise ValueError(msg)
        return self
