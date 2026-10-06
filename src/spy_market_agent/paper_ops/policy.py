"""Phase 5 non-submitting readiness policy."""

from __future__ import annotations

from collections.abc import Mapping

from spy_market_agent.paper_ops.gates import (
    evaluate_phase5_broker_submission_gate,
    evaluate_phase5_infrastructure_gate,
    evaluate_phase5_model_connected_paper_gate,
)
from spy_market_agent.paper_ops.types import PaperOperationReadiness


def evaluate_phase5_readiness(
    *,
    broker_metadata: Mapping[str, object] | None = None,
    model_metadata: Mapping[str, object] | None = None,
) -> tuple[PaperOperationReadiness, ...]:
    """Return the current Phase 5 gate posture without side effects."""

    return (
        evaluate_phase5_infrastructure_gate(),
        evaluate_phase5_broker_submission_gate(broker_metadata),
        evaluate_phase5_model_connected_paper_gate(model_metadata),
    )
