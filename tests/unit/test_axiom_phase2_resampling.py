from __future__ import annotations

import random
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from spy_market_agent.research.experiment_core import StrategyResearchState
from spy_market_agent.research.resampling import (
    CanonicalResamplingEvidence,
    ResamplingConfig,
    ResamplingMethod,
    _moving_block_bootstrap_sample,
    canonical_resampling_evidence,
    resampling_evidence_to_axiom_result,
)

SOURCE_RETURNS = (0.01, -0.02, 0.03, 0.005, -0.01, 0.015)
COMPLETED_AT = datetime(2026, 10, 5, 13, 0, tzinfo=UTC)


def _config(
    *,
    method: ResamplingMethod = ResamplingMethod.IID_BOOTSTRAP,
    seed: int = 42,
    block_size: int | None = None,
) -> ResamplingConfig:
    """Build a deterministic compact resampling configuration fixture."""

    return ResamplingConfig(
        method=method,
        sample_count=200,
        seed=seed,
        block_size=block_size,
        drawdown_breach_threshold=0.05,
    )


def test_iid_resampling_is_deterministic_and_seed_is_part_of_evidence() -> None:
    """Equivalent seeded studies are identical while a different seed changes evidence."""

    config = _config()
    first = canonical_resampling_evidence(SOURCE_RETURNS, config=config)
    second = canonical_resampling_evidence(SOURCE_RETURNS, config=config)
    changed_seed = canonical_resampling_evidence(SOURCE_RETURNS, config=_config(seed=7))

    assert first == second
    assert first != changed_seed
    assert first.source_return_checksum == changed_seed.source_return_checksum
    assert first.source_return_count == len(SOURCE_RETURNS)


def test_moving_block_bootstrap_preserves_contiguous_source_blocks() -> None:
    """Every complete sampled block retains adjacency from the original source path."""

    source = (0.00, 0.01, 0.02, 0.03, 0.04, 0.05)
    sample = _moving_block_bootstrap_sample(source, block_size=2, rng=random.Random(9))

    assert len(sample) == len(source)
    for offset in range(0, len(sample), 2):
        first_index = source.index(sample[offset])
        second_index = source.index(sample[offset + 1])
        assert second_index == first_index + 1


def test_resampling_distributions_and_frequencies_are_internally_consistent() -> None:
    """Canonical summaries remain ordered and empirical frequencies remain bounded."""

    evidence = canonical_resampling_evidence(
        SOURCE_RETURNS,
        config=_config(
            method=ResamplingMethod.MOVING_BLOCK_BOOTSTRAP,
            block_size=2,
        ),
    )

    for summary in (
        evidence.cumulative_return_distribution,
        evidence.maximum_drawdown_distribution,
    ):
        values = (summary.minimum, summary.p05, summary.median, summary.p95, summary.maximum)
        assert values == tuple(sorted(values))
    assert 0.0 <= evidence.loss_frequency <= 1.0
    assert 0.0 <= evidence.drawdown_breach_frequency <= 1.0


@pytest.mark.parametrize(
    ("returns", "message"),
    [
        ((0.01,), "at least two"),
        ((0.01, float("nan")), "finite"),
        ((0.01, float("inf")), "finite"),
        ((0.01, -1.01), "below -100%"),
    ],
)
def test_resampling_rejects_invalid_source_returns(
    returns: tuple[float, ...],
    message: str,
) -> None:
    """Short, non-finite, or impossible simple-return paths fail closed."""

    with pytest.raises(ValueError, match=message):
        canonical_resampling_evidence(returns, config=_config())


def test_resampling_rejects_invalid_method_configuration() -> None:
    """Method-specific block parameters cannot be silently ignored or omitted."""

    with pytest.raises(ValidationError, match="must not define block_size"):
        _config(block_size=2)
    with pytest.raises(ValidationError, match="requires block_size"):
        _config(method=ResamplingMethod.MOVING_BLOCK_BOOTSTRAP)
    with pytest.raises(ValidationError):
        ResamplingConfig(
            method=ResamplingMethod.IID_BOOTSTRAP,
            sample_count=99,
            seed=42,
            drawdown_breach_threshold=0.05,
        )


def test_resampling_rejects_block_larger_than_source_path() -> None:
    """Moving-block studies reject blocks that cannot exist in the source path."""

    with pytest.raises(ValueError, match="cannot exceed"):
        canonical_resampling_evidence(
            SOURCE_RETURNS,
            config=_config(
                method=ResamplingMethod.MOVING_BLOCK_BOOTSTRAP,
                block_size=len(SOURCE_RETURNS) + 1,
            ),
        )


def test_resampling_evidence_model_rejects_malformed_source_checksum() -> None:
    """Serialized resampling evidence cannot carry a malformed source checksum."""

    evidence = canonical_resampling_evidence(SOURCE_RETURNS, config=_config())
    payload = evidence.model_dump(mode="python")
    payload["source_return_checksum"] = "bad"
    with pytest.raises(ValidationError, match="SHA-256"):
        CanonicalResamplingEvidence.model_validate(payload)


def test_resampling_result_adapter_is_research_only() -> None:
    """Resampling diagnostics can never elevate a strategy into execution authority."""

    evidence = canonical_resampling_evidence(SOURCE_RETURNS, config=_config())
    result = resampling_evidence_to_axiom_result(
        experiment_id="aq-exp-111111111111111111111111",
        evidence=evidence,
        completed_at=COMPLETED_AT,
    )

    assert result.strategy_state == StrategyResearchState.RESEARCH_ONLY
    assert result.metric_snapshot["resampling_loss_frequency"] == evidence.loss_frequency
    assert "not guaranteed future probabilities" in result.conclusion
