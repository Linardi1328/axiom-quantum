from __future__ import annotations

from spy_market_agent.research.errors import ProtectedEvaluationAccessError, raise_research_error
from spy_market_agent.research.models import ProtectedEvaluationStatus


def assert_protected_evaluation_not_accessed(status: ProtectedEvaluationStatus) -> None:
    if status.protected_labels_loaded or status.state in {"accessed", "completed"}:
        raise_research_error(
            ProtectedEvaluationAccessError,
            "protected_evaluation_already_accessed",
            "protected evaluation labels have already been accessed.",
        )


def deny_protected_label_access() -> None:
    raise_research_error(
        ProtectedEvaluationAccessError,
        "protected_evaluation_not_authorized",
        "Phase 3 protected labels are inaccessible during framework scaffolding.",
    )
