from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

DatabasePath = str | Path


class PersistenceError(RuntimeError):
    """Base class for expected Phase 7 persistence failures."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class PersistenceInputError(PersistenceError):
    """Raised when a persistence public boundary receives malformed input."""


class PersistenceSchemaError(PersistenceError):
    """Raised when the SQLite schema is missing, unsupported, or inconsistent."""


class PersistenceConflictError(PersistenceError):
    """Raised when an artifact run id already exists."""


class PersistenceIntegrityError(PersistenceError):
    """Raised when stored rows cannot reconstruct approved domain objects."""


class PersistenceNotFoundError(PersistenceError):
    """Raised when a requested persisted artifact is missing."""


@dataclass(frozen=True, slots=True)
class RuntimeSnapshot:
    git_commit_hash: str | None
    python_version: str
    dependency_versions: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class ModelRunSummary:
    run_id: str
    selected_model_name: str
    selection_reason: str
    created_at: str
    source_market_data_checksum: str
    test_row_count: int


@dataclass(frozen=True, slots=True)
class BacktestRunSummary:
    run_id: str
    selected_model_name: str
    created_at: str
    source_market_data_checksum: str
    final_equity: float
    total_return: float
    maximum_drawdown: float
    proposed_order_count: int
    fill_count: int


__all__ = [
    "BacktestRunSummary",
    "DatabasePath",
    "ModelRunSummary",
    "PersistenceConflictError",
    "PersistenceError",
    "PersistenceInputError",
    "PersistenceIntegrityError",
    "PersistenceNotFoundError",
    "PersistenceSchemaError",
    "RuntimeSnapshot",
]
