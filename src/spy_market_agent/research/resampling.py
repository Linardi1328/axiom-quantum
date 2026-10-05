from __future__ import annotations

import math
import random
import re
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from spy_market_agent.benchmark.artifacts import sha256_json
from spy_market_agent.research.experiment_core import (
    ExperimentLifecycleState,
    ExperimentOutcome,
    ExperimentResult,
    StrategyResearchState,
)

RESAMPLING_EVIDENCE_SCHEMA_VERSION = "axiom-resampling-evidence-v1"
RESAMPLING_SOURCE_SCHEMA_VERSION = "axiom-simple-return-source-v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ResamplingMethod(StrEnum):
    """Supported deterministic bootstrap methods for Phase 2 validation evidence."""

    IID_BOOTSTRAP = "iid_bootstrap"
    MOVING_BLOCK_BOOTSTRAP = "moving_block_bootstrap"


class ResamplingConfig(BaseModel):
    """Explicit deterministic configuration for one return-path resampling study."""

    model_config = ConfigDict(frozen=True)

    method: ResamplingMethod
    sample_count: int = Field(ge=100, le=100_000)
    seed: int = Field(ge=0, le=2**63 - 1)
    block_size: int | None = Field(default=None, ge=1)
    drawdown_breach_threshold: float = Field(gt=0.0, le=1.0)

    @model_validator(mode="after")
    def _method_parameters(self) -> ResamplingConfig:
        """Require method-specific block configuration with no ignored parameters."""

        if self.method == ResamplingMethod.IID_BOOTSTRAP and self.block_size is not None:
            raise ValueError("iid_bootstrap must not define block_size")
        if self.method == ResamplingMethod.MOVING_BLOCK_BOOTSTRAP and self.block_size is None:
            raise ValueError("moving_block_bootstrap requires block_size")
        return self


class ResamplingDistributionSummary(BaseModel):
    """Deterministic five-number percentile summary over one resampled statistic."""

    model_config = ConfigDict(frozen=True)

    minimum: float
    p05: float
    median: float
    p95: float
    maximum: float

    @model_validator(mode="after")
    def _ordered_finite_values(self) -> ResamplingDistributionSummary:
        """Require finite monotonically ordered distribution summary values."""

        values = (self.minimum, self.p05, self.median, self.p95, self.maximum)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("resampling distribution summary values must be finite")
        if tuple(sorted(values)) != values:
            raise ValueError("resampling distribution summary values must be ordered")
        return self


class CanonicalResamplingEvidence(BaseModel):
    """Canonical deterministic bootstrap evidence over a finite simple-return path."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["axiom-resampling-evidence-v1"] = "axiom-resampling-evidence-v1"
    source_return_count: int = Field(ge=2)
    source_return_checksum: str
    config: ResamplingConfig
    cumulative_return_distribution: ResamplingDistributionSummary
    maximum_drawdown_distribution: ResamplingDistributionSummary
    loss_frequency: float = Field(ge=0.0, le=1.0)
    drawdown_breach_frequency: float = Field(ge=0.0, le=1.0)

    @field_validator("source_return_checksum")
    @classmethod
    def _source_checksum(cls, value: str) -> str:
        """Require a canonical lowercase SHA-256 digest for the source return path."""

        if not _SHA256.fullmatch(value):
            raise ValueError("source_return_checksum must be a lowercase SHA-256 digest")
        return value

    @model_validator(mode="after")
    def _schema_version(self) -> CanonicalResamplingEvidence:
        """Reject unsupported serialized resampling evidence schema versions."""

        if self.schema_version != RESAMPLING_EVIDENCE_SCHEMA_VERSION:
            raise ValueError("unsupported resampling evidence schema version")
        return self


def _validated_returns(returns: tuple[float, ...]) -> tuple[float, ...]:
    """Normalize a finite simple-return path and reject impossible losses below -100%."""

    if len(returns) < 2:
        raise ValueError("resampling requires at least two source returns")
    normalized: list[float] = []
    for value in returns:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("source returns must be numeric")
        numeric = float(value)
        if not math.isfinite(numeric):
            raise ValueError("source returns must be finite")
        if numeric < -1.0:
            raise ValueError("simple returns cannot be below -100%")
        normalized.append(numeric)
    return tuple(normalized)


def _iid_bootstrap_sample(
    returns: tuple[float, ...],
    *,
    rng: random.Random,
) -> tuple[float, ...]:
    """Draw one IID bootstrap path with replacement at the source-path horizon."""

    return tuple(returns[rng.randrange(len(returns))] for _ in returns)


def _moving_block_bootstrap_sample(
    returns: tuple[float, ...],
    *,
    block_size: int,
    rng: random.Random,
) -> tuple[float, ...]:
    """Draw contiguous source blocks with replacement and truncate to the source horizon."""

    if block_size > len(returns):
        raise ValueError("block_size cannot exceed the source return count")
    sampled: list[float] = []
    last_start = len(returns) - block_size
    while len(sampled) < len(returns):
        start = rng.randrange(last_start + 1)
        sampled.extend(returns[start : start + block_size])
    return tuple(sampled[: len(returns)])


def _cumulative_return(returns: tuple[float, ...]) -> float:
    """Calculate compounded simple return over one path."""

    equity = 1.0
    for value in returns:
        equity *= 1.0 + value
    return equity - 1.0


def _maximum_drawdown(returns: tuple[float, ...]) -> float:
    """Calculate maximum peak-to-trough drawdown over one non-levered simple-return path."""

    equity = 1.0
    peak = 1.0
    maximum = 0.0
    for value in returns:
        equity *= 1.0 + value
        peak = max(peak, equity)
        maximum = max(maximum, (peak - equity) / peak)
    return maximum


def _quantile(values: tuple[float, ...], probability: float) -> float:
    """Return a deterministic linearly interpolated quantile for a non-empty finite sample."""

    ordered = tuple(sorted(values))
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _distribution_summary(values: tuple[float, ...]) -> ResamplingDistributionSummary:
    """Build the canonical min/5th/median/95th/max summary for a statistic sample."""

    return ResamplingDistributionSummary(
        minimum=min(values),
        p05=_quantile(values, 0.05),
        median=_quantile(values, 0.50),
        p95=_quantile(values, 0.95),
        maximum=max(values),
    )


def canonical_resampling_evidence(
    returns: tuple[float, ...],
    *,
    config: ResamplingConfig,
) -> CanonicalResamplingEvidence:
    """Generate deterministic empirical resampling evidence for a finite return path."""

    source = _validated_returns(returns)
    if config.block_size is not None and config.block_size > len(source):
        raise ValueError("block_size cannot exceed the source return count")

    rng = random.Random(config.seed)
    cumulative_returns: list[float] = []
    drawdowns: list[float] = []
    for _ in range(config.sample_count):
        if config.method == ResamplingMethod.IID_BOOTSTRAP:
            sample = _iid_bootstrap_sample(source, rng=rng)
        else:
            assert config.block_size is not None
            sample = _moving_block_bootstrap_sample(
                source,
                block_size=config.block_size,
                rng=rng,
            )
        cumulative_returns.append(_cumulative_return(sample))
        drawdowns.append(_maximum_drawdown(sample))

    cumulative_tuple = tuple(cumulative_returns)
    drawdown_tuple = tuple(drawdowns)
    source_checksum = sha256_json(
        {
            "schema_version": RESAMPLING_SOURCE_SCHEMA_VERSION,
            "returns": source,
        }
    )
    return CanonicalResamplingEvidence(
        source_return_count=len(source),
        source_return_checksum=source_checksum,
        config=config,
        cumulative_return_distribution=_distribution_summary(cumulative_tuple),
        maximum_drawdown_distribution=_distribution_summary(drawdown_tuple),
        loss_frequency=sum(value < 0.0 for value in cumulative_tuple) / config.sample_count,
        drawdown_breach_frequency=(
            sum(value >= config.drawdown_breach_threshold for value in drawdown_tuple)
            / config.sample_count
        ),
    )


def resampling_evidence_to_axiom_result(
    *,
    experiment_id: str,
    evidence: CanonicalResamplingEvidence,
    completed_at: datetime,
) -> ExperimentResult:
    """Record resampling diagnostics as research-only evidence with no promotion authority."""

    return ExperimentResult(
        experiment_id=experiment_id,
        lifecycle_state=ExperimentLifecycleState.COMPLETED,
        outcome=ExperimentOutcome.COMPLETED,
        strategy_state=StrategyResearchState.RESEARCH_ONLY,
        summary="Deterministic empirical resampling diagnostics completed.",
        conclusion=(
            "Retain as research-only risk evidence; empirical resampling frequencies are not "
            "guaranteed future probabilities."
        ),
        metric_snapshot={
            "resampling_loss_frequency": evidence.loss_frequency,
            "resampling_drawdown_breach_frequency": evidence.drawdown_breach_frequency,
            "resampling_cumulative_return_p05": evidence.cumulative_return_distribution.p05,
            "resampling_cumulative_return_median": evidence.cumulative_return_distribution.median,
            "resampling_cumulative_return_p95": evidence.cumulative_return_distribution.p95,
            "resampling_maximum_drawdown_median": evidence.maximum_drawdown_distribution.median,
            "resampling_maximum_drawdown_p95": evidence.maximum_drawdown_distribution.p95,
        },
        completed_at=completed_at,
    )
