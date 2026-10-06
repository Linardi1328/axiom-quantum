from __future__ import annotations

from dataclasses import dataclass
from typing import NoReturn


@dataclass(frozen=True, slots=True)
class BenchmarkIssue:
    code: str
    message: str


class BenchmarkError(ValueError):
    """Base class for controlled Phase 2 benchmark failures."""

    def __init__(self, issues: list[BenchmarkIssue] | tuple[BenchmarkIssue, ...]) -> None:
        self.issues = tuple(issues)
        super().__init__("; ".join(f"{issue.code}: {issue.message}" for issue in self.issues))

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(issue.code for issue in self.issues)


class BenchmarkInputError(BenchmarkError):
    """Raised when benchmark inputs or CLI arguments are invalid."""


class BenchmarkEligibilityError(BenchmarkError):
    """Raised when a Phase 1 dataset is not eligible for Phase 2."""


class BenchmarkSplitError(BenchmarkError):
    """Raised when deterministic Phase 2 split construction fails."""


class BenchmarkArtifactError(BenchmarkError):
    """Raised when benchmark artifacts are unsafe, missing, or inconsistent."""


class BenchmarkLockError(BenchmarkError):
    """Raised when immutable benchmark locks are missing or incompatible."""


class BenchmarkFinalTestAccessError(BenchmarkError):
    """Raised when final-test data access is unauthorized or repeated."""


def benchmark_issue(code: str, message: str) -> BenchmarkIssue:
    return BenchmarkIssue(code=code, message=message)


def raise_benchmark_error(
    error_type: type[BenchmarkError],
    code: str,
    message: str,
) -> NoReturn:
    raise error_type([benchmark_issue(code, message)])
