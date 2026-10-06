"""Provider-neutral, read-only contracts for AI explanations of market research."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AnalystContext:
    question: str
    evidence_refs: tuple[str, ...]
    research_summary: str
    risk_summary: str
    model_summary: str

    def validate(self) -> None:
        if not self.evidence_refs:
            raise ValueError("AI analyst requires verified evidence references")


@dataclass(frozen=True)
class AnalystExplanation:
    answer: str
    evidence_refs: tuple[str, ...]
    can_execute_trade: bool = False
    can_change_risk_state: bool = False

    def __post_init__(self) -> None:
        if self.can_execute_trade or self.can_change_risk_state:
            raise ValueError("AI analyst must remain read-only and non-authoritative")
