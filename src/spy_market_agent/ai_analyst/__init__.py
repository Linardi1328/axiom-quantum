"""Read-only AI research analyst contracts.

The analyst may explain verified research artifacts but cannot create trading signals,
change risk decisions, submit orders, or authorize paper/live execution.
"""

from .contracts import AnalystContext, AnalystExplanation

__all__ = ["AnalystContext", "AnalystExplanation"]
