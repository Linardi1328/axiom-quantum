from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from spy_market_agent.intelligence._validation import (
    normalized_identifiers,
    require_nonempty,
    require_optional_finite,
    require_safe_identifier,
)
from spy_market_agent.intelligence.contracts import IntelligenceRunIdentity


class StateAvailability(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class MarketStateDimension:
    dimension_id: str
    label: str
    availability: StateAvailability
    value: float | None = None
    unit: str | None = None
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.availability, StateAvailability):
            raise ValueError("availability must be a StateAvailability.")
        object.__setattr__(
            self,
            "dimension_id",
            require_safe_identifier(self.dimension_id, field_name="dimension_id"),
        )
        object.__setattr__(self, "label", require_nonempty(self.label, field_name="label"))
        object.__setattr__(
            self,
            "value",
            require_optional_finite(self.value, field_name="value"),
        )
        if self.unit is not None:
            object.__setattr__(self, "unit", require_nonempty(self.unit, field_name="unit"))
        object.__setattr__(
            self,
            "evidence_refs",
            normalized_identifiers(self.evidence_refs, field_name="evidence_refs"),
        )
        if self.availability == StateAvailability.UNAVAILABLE and self.value is not None:
            raise ValueError("unavailable state dimensions must not carry a numeric value.")
        if self.availability == StateAvailability.AVAILABLE and not self.evidence_refs:
            raise ValueError("available state dimensions require at least one evidence reference.")


@dataclass(frozen=True, slots=True)
class MarketStateSnapshot:
    run_identity: IntelligenceRunIdentity
    dimensions: tuple[MarketStateDimension, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.run_identity, IntelligenceRunIdentity):
            raise ValueError("run_identity must be an IntelligenceRunIdentity.")
        if not self.dimensions:
            raise ValueError("dimensions must not be empty.")
        identifiers = [dimension.dimension_id for dimension in self.dimensions]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("dimensions must not contain duplicate dimension_id values.")
        object.__setattr__(
            self,
            "dimensions",
            tuple(sorted(self.dimensions, key=lambda dimension: dimension.dimension_id)),
        )

    @property
    def evidence_refs(self) -> tuple[str, ...]:
        references = {
            reference for dimension in self.dimensions for reference in dimension.evidence_refs
        }
        return tuple(sorted(references))
