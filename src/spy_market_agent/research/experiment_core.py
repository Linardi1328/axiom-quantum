from __future__ import annotations

import math
import re
from datetime import date, datetime, timedelta
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationInfo, field_validator, model_validator

from spy_market_agent.benchmark.artifacts import sha256_json

AXIOM_EXPERIMENT_SCHEMA_VERSION = "axiom-research-experiment-v1"
AXIOM_RESULT_SCHEMA_VERSION = "axiom-research-result-v1"
AXIOM_EXPERIMENT_ID_VERSION = "axiom-research-experiment-id-v1"
AXIOM_RESULT_ID_VERSION = "axiom-research-result-id-v1"

_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_ASSET_IDENTIFIER = re.compile(r"^[A-Z0-9][A-Z0-9._:-]{0,31}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_SHA = re.compile(r"^[0-9a-f]{7,64}$")
_FORBIDDEN_NOTE_TERMS = ("secret", "password", "api_key", "account_id")
MetricValue = str | int | float | bool | None


class ExperimentLifecycleState(StrEnum):
    PLANNED = "planned"
    RUNNING = "running"
    COMPLETED = "completed"
    REJECTED = "rejected"
    ARCHIVED = "archived"


class StrategyResearchState(StrEnum):
    RESEARCH_ONLY = "research_only"
    VALIDATION_CANDIDATE = "validation_candidate"
    REJECTED = "rejected"
    RETIRED = "retired"


class ExperimentOutcome(StrEnum):
    COMPLETED = "completed"
    INCONCLUSIVE = "inconclusive"
    REJECTED = "rejected"
    FAILED = "failed"


def _require_safe_identifier(value: str, *, field_name: str) -> str:
    if not _SAFE_IDENTIFIER.fullmatch(value):
        msg = f"{field_name} must be a nonempty path-safe identifier."
        raise ValueError(msg)
    return value


def _require_text(value: str, *, field_name: str) -> str:
    if not value.strip():
        msg = f"{field_name} must be nonempty."
        raise ValueError(msg)
    return value


def _require_sha256(value: str, *, field_name: str) -> str:
    if not _SHA256.fullmatch(value):
        msg = f"{field_name} must be a lowercase SHA-256 digest."
        raise ValueError(msg)
    return value


def _unique_tuple(values: tuple[str, ...], *, field_name: str) -> tuple[str, ...]:
    if len(values) != len(set(values)):
        msg = f"{field_name} must not contain duplicates."
        raise ValueError(msg)
    return values


def _validate_relative_path(value: str) -> str:
    if not value.strip() or "\\" in value:
        msg = "relative_path must be a nonempty POSIX-style relative path."
        raise ValueError(msg)
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        msg = "relative_path must stay relative and must not contain traversal components."
        raise ValueError(msg)
    return path.as_posix()


class ResearchDatasetRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    dataset_id: str
    checksum: str
    feature_schema: str
    label_schema: str
    first_session: date
    last_session: date

    @field_validator("dataset_id", "feature_schema", "label_schema")
    @classmethod
    def _safe_identifiers(cls, value: str, info: ValidationInfo) -> str:
        field_name = info.field_name or "identifier"
        return _require_safe_identifier(value, field_name=field_name)

    @field_validator("checksum")
    @classmethod
    def _checksum(cls, value: str) -> str:
        return _require_sha256(value, field_name="checksum")

    @model_validator(mode="after")
    def _session_range(self) -> ResearchDatasetRef:
        if self.first_session > self.last_session:
            msg = "first_session must not be after last_session."
            raise ValueError(msg)
        return self


class ResearchRuntimeLineage(BaseModel):
    model_config = ConfigDict(frozen=True)

    git_commit_sha: str
    package_version: str
    python_version: str
    dependency_versions: dict[str, str]

    @field_validator("git_commit_sha")
    @classmethod
    def _git_sha(cls, value: str) -> str:
        if not _GIT_SHA.fullmatch(value):
            msg = "git_commit_sha must be a lowercase hexadecimal Git commit identifier."
            raise ValueError(msg)
        return value

    @field_validator("package_version", "python_version")
    @classmethod
    def _lineage_text(cls, value: str, info: ValidationInfo) -> str:
        field_name = info.field_name or "lineage"
        return _require_text(value, field_name=field_name)

    @field_validator("dependency_versions")
    @classmethod
    def _dependencies(cls, value: dict[str, str]) -> dict[str, str]:
        if not value:
            msg = "dependency_versions must record at least one dependency."
            raise ValueError(msg)
        normalized: dict[str, str] = {}
        for package, version in value.items():
            normalized[_require_safe_identifier(package, field_name="dependency package")] = (
                _require_text(version, field_name="dependency version")
            )
        return dict(sorted(normalized.items()))


class ExperimentDefinition(BaseModel):
    """Canonical, execution-free scientific definition for an Axiom Quantum experiment."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["axiom-research-experiment-v1"] = "axiom-research-experiment-v1"
    name: str
    hypothesis: str
    research_question: str
    asset_universe: tuple[str, ...]
    datasets: tuple[ResearchDatasetRef, ...]
    feature_families: tuple[str, ...] = ()
    strategy_ids: tuple[str, ...] = ()
    model_ids: tuple[str, ...] = ()
    evaluation_protocol: str
    cost_model_id: str
    primary_metrics: tuple[str, ...]
    runtime_lineage: ResearchRuntimeLineage
    tags: tuple[str, ...] = ()
    execution_authority: Literal["none"] = "none"
    notes: str = ""

    @field_validator("name", "hypothesis", "research_question")
    @classmethod
    def _required_text(cls, value: str, info: ValidationInfo) -> str:
        field_name = info.field_name or "text"
        return _require_text(value, field_name=field_name)

    @field_validator("evaluation_protocol", "cost_model_id")
    @classmethod
    def _safe_single_identifiers(cls, value: str, info: ValidationInfo) -> str:
        field_name = info.field_name or "identifier"
        return _require_safe_identifier(value, field_name=field_name)

    @field_validator("asset_universe")
    @classmethod
    def _assets(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            msg = "asset_universe must contain at least one asset."
            raise ValueError(msg)
        _unique_tuple(value, field_name="asset_universe")
        for asset in value:
            if not _ASSET_IDENTIFIER.fullmatch(asset):
                msg = "asset_universe entries must use canonical uppercase asset identifiers."
                raise ValueError(msg)
        return tuple(sorted(value))

    @field_validator("datasets")
    @classmethod
    def _datasets(cls, value: tuple[ResearchDatasetRef, ...]) -> tuple[ResearchDatasetRef, ...]:
        if not value:
            msg = "datasets must contain at least one dataset reference."
            raise ValueError(msg)
        dataset_ids = tuple(item.dataset_id for item in value)
        _unique_tuple(dataset_ids, field_name="dataset IDs")
        return tuple(sorted(value, key=lambda item: item.dataset_id))

    @field_validator("feature_families", "strategy_ids", "model_ids", "tags")
    @classmethod
    def _identifier_tuples(cls, value: tuple[str, ...], info: ValidationInfo) -> tuple[str, ...]:
        field_name = info.field_name or "identifiers"
        _unique_tuple(value, field_name=field_name)
        for item in value:
            _require_safe_identifier(item, field_name=field_name)
        return tuple(sorted(value))

    @field_validator("primary_metrics")
    @classmethod
    def _metrics(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            msg = "primary_metrics must contain at least one metric."
            raise ValueError(msg)
        _unique_tuple(value, field_name="primary_metrics")
        for metric in value:
            _require_safe_identifier(metric, field_name="primary_metrics")
        return value

    @field_validator("notes")
    @classmethod
    def _safe_notes(cls, value: str) -> str:
        lowered = value.lower()
        if any(term in lowered for term in _FORBIDDEN_NOTE_TERMS):
            msg = "notes must not contain secrets or account identifiers."
            raise ValueError(msg)
        return value


class ResearchEvidenceRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    relative_path: str
    checksum: str

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        return _require_safe_identifier(value, field_name="name")

    @field_validator("relative_path")
    @classmethod
    def _path(cls, value: str) -> str:
        return _validate_relative_path(value)

    @field_validator("checksum")
    @classmethod
    def _checksum(cls, value: str) -> str:
        return _require_sha256(value, field_name="checksum")


class ExperimentResult(BaseModel):
    """Immutable research conclusion. It cannot grant execution authority."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["axiom-research-result-v1"] = "axiom-research-result-v1"
    experiment_id: str
    lifecycle_state: Literal[
        ExperimentLifecycleState.COMPLETED,
        ExperimentLifecycleState.REJECTED,
    ]
    outcome: ExperimentOutcome
    strategy_state: StrategyResearchState = StrategyResearchState.RESEARCH_ONLY
    summary: str
    conclusion: str
    metric_snapshot: dict[str, MetricValue]
    evidence: tuple[ResearchEvidenceRef, ...] = ()
    completed_at: datetime

    @field_validator("experiment_id")
    @classmethod
    def _experiment_id(cls, value: str) -> str:
        if not value.startswith("aq-exp-"):
            msg = "experiment_id must be an Axiom experiment identity."
            raise ValueError(msg)
        return _require_safe_identifier(value, field_name="experiment_id")

    @field_validator("summary", "conclusion")
    @classmethod
    def _result_text(cls, value: str, info: ValidationInfo) -> str:
        field_name = info.field_name or "result text"
        return _require_text(value, field_name=field_name)

    @field_validator("metric_snapshot")
    @classmethod
    def _metric_snapshot(cls, value: dict[str, MetricValue]) -> dict[str, MetricValue]:
        normalized: dict[str, MetricValue] = {}
        for metric, metric_value in value.items():
            key = _require_safe_identifier(metric, field_name="metric name")
            if isinstance(metric_value, float) and not math.isfinite(metric_value):
                msg = f"metric_snapshot value for {metric!r} must be finite."
                raise ValueError(msg)
            normalized[key] = metric_value
        return dict(sorted(normalized.items()))

    @field_validator("evidence")
    @classmethod
    def _evidence(cls, value: tuple[ResearchEvidenceRef, ...]) -> tuple[ResearchEvidenceRef, ...]:
        names = tuple(item.name for item in value)
        _unique_tuple(names, field_name="evidence names")
        return tuple(sorted(value, key=lambda item: item.name))

    @field_validator("completed_at")
    @classmethod
    def _completed_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            msg = "completed_at must be timezone-aware UTC."
            raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def _state_consistency(self) -> ExperimentResult:
        if self.lifecycle_state == ExperimentLifecycleState.COMPLETED and self.outcome in {
            ExperimentOutcome.REJECTED,
            ExperimentOutcome.FAILED,
        }:
            msg = "completed lifecycle requires a completed or inconclusive outcome."
            raise ValueError(msg)
        if self.lifecycle_state == ExperimentLifecycleState.REJECTED and self.outcome not in {
            ExperimentOutcome.REJECTED,
            ExperimentOutcome.FAILED,
        }:
            msg = "rejected lifecycle requires a rejected or failed outcome."
            raise ValueError(msg)
        if (
            self.strategy_state == StrategyResearchState.VALIDATION_CANDIDATE
            and self.outcome != ExperimentOutcome.COMPLETED
        ):
            msg = "validation_candidate requires a completed experiment outcome."
            raise ValueError(msg)
        return self


class StoredExperiment(BaseModel):
    model_config = ConfigDict(frozen=True)

    experiment_id: str
    initial_lifecycle_state: Literal[ExperimentLifecycleState.PLANNED] = (
        ExperimentLifecycleState.PLANNED
    )
    initial_strategy_state: Literal[StrategyResearchState.RESEARCH_ONLY] = (
        StrategyResearchState.RESEARCH_ONLY
    )
    definition: ExperimentDefinition

    @model_validator(mode="after")
    def _identity_matches(self) -> StoredExperiment:
        if self.experiment_id != experiment_identity(self.definition):
            msg = "experiment_id must match the canonical experiment definition."
            raise ValueError(msg)
        return self


class StoredExperimentResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    result_id: str
    result: ExperimentResult

    @model_validator(mode="after")
    def _identity_matches(self) -> StoredExperimentResult:
        if self.result_id != result_identity(self.result):
            msg = "result_id must match the canonical result content."
            raise ValueError(msg)
        return self


def experiment_identity_payload(definition: ExperimentDefinition) -> dict[str, object]:
    payload = definition.model_dump(mode="python")
    payload.pop("notes", None)
    payload["identity_version"] = AXIOM_EXPERIMENT_ID_VERSION
    return payload


def experiment_identity(definition: ExperimentDefinition) -> str:
    digest = sha256_json(experiment_identity_payload(definition))
    return f"aq-exp-{digest[:24]}"


def result_identity_payload(result: ExperimentResult) -> dict[str, object]:
    payload = result.model_dump(mode="python")
    payload["identity_version"] = AXIOM_RESULT_ID_VERSION
    return payload


def result_identity(result: ExperimentResult) -> str:
    digest = sha256_json(result_identity_payload(result))
    return f"aq-result-{digest[:24]}"
