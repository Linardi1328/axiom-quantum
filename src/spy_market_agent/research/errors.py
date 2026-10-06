from __future__ import annotations

from dataclasses import dataclass
from typing import NoReturn


@dataclass(frozen=True, slots=True)
class ResearchIssue:
    code: str
    message: str


class ResearchError(ValueError):
    """Base class for controlled Version 2 Phase 3 research failures."""

    def __init__(self, issues: list[ResearchIssue]) -> None:
        self.issues = tuple(issues)
        message = "; ".join(f"{issue.code}: {issue.message}" for issue in self.issues)
        super().__init__(message)

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(issue.code for issue in self.issues)


class WalkForwardSplitError(ResearchError):
    """Raised when Phase 3 walk-forward folds cannot be constructed safely."""


class LeakageValidationError(ResearchError):
    """Raised when a Phase 3 leakage guard fails closed."""


class ResearchRegistryError(ResearchError):
    """Raised when Phase 3 registry or manifest metadata is incomplete or unsafe."""


class ResearchMetricError(ResearchError):
    """Raised when Phase 3 metrics are invalid or non-finite."""


class CandidateSelectionError(ResearchError):
    """Raised when Phase 3 candidate ranking inputs are invalid."""


class ResearchArtifactError(ResearchError):
    """Raised when Phase 3 artifact paths or serialization are unsafe."""


class ProtectedEvaluationAccessError(ResearchError):
    """Raised when Phase 3 protected evaluation access is attempted too early."""


def research_issue(code: str, message: str) -> ResearchIssue:
    return ResearchIssue(code=code, message=message)


def raise_research_error(
    error_type: type[ResearchError],
    code: str,
    message: str,
) -> NoReturn:
    raise error_type([research_issue(code, message)])
