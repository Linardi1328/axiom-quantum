from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from spy_market_agent.intelligence._validation import (
    normalized_identifiers,
    require_aware_utc,
    require_nonempty,
    require_optional_finite,
    require_safe_identifier,
)


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    evidence_id: str
    source_id: str
    methodology_id: str
    observed_at: datetime
    available_at: datetime
    summary: str
    numeric_value: float | None = None
    standardized_value: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "evidence_id",
            require_safe_identifier(self.evidence_id, field_name="evidence_id"),
        )
        object.__setattr__(
            self,
            "source_id",
            require_safe_identifier(self.source_id, field_name="source_id"),
        )
        object.__setattr__(
            self,
            "methodology_id",
            require_safe_identifier(self.methodology_id, field_name="methodology_id"),
        )
        observed_at = require_aware_utc(self.observed_at, field_name="observed_at")
        available_at = require_aware_utc(self.available_at, field_name="available_at")
        if available_at < observed_at:
            raise ValueError("available_at must not be before observed_at.")
        object.__setattr__(self, "observed_at", observed_at)
        object.__setattr__(self, "available_at", available_at)
        object.__setattr__(self, "summary", require_nonempty(self.summary, field_name="summary"))
        object.__setattr__(
            self,
            "numeric_value",
            require_optional_finite(self.numeric_value, field_name="numeric_value"),
        )
        object.__setattr__(
            self,
            "standardized_value",
            require_optional_finite(
                self.standardized_value,
                field_name="standardized_value",
            ),
        )


def evidence_reference_ids(items: Iterable[EvidenceItem]) -> tuple[str, ...]:
    return normalized_identifiers(
        tuple(item.evidence_id for item in items),
        field_name="evidence_refs",
    )
