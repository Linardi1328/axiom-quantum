from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INTELLIGENCE_ROOT = ROOT / "src/spy_market_agent/intelligence"
AI_ANALYST_ROOT = ROOT / "src/spy_market_agent/ai_analyst"
SPEC_PATH = ROOT / "docs/MI_01_SPY_MARKET_INTELLIGENCE_FOUNDATION_SPEC.md"


def test_mi1_spec_preserves_non_execution_boundary() -> None:
    spec = SPEC_PATH.read_text(encoding="utf-8")

    assert "Status: Owner-authorized implementation foundation" in spec
    assert "P5-B broker paper submission remains blocked" in spec
    assert "P5-C model-connected paper operation remains `BLOCKED_NO_APPROVED_PAPER_MODEL`" in spec
    assert "Live trading remains prohibited" in spec
    assert "does not authorize or implement" in spec
    assert "model inference" in spec
    assert "broker communication" in spec
    assert "paper-order submission" in spec
    assert "No version bump and no release tag" in spec


def test_mi1_intelligence_and_ai_analyst_remain_execution_isolated() -> None:
    source_roots = (INTELLIGENCE_ROOT, AI_ANALYST_ROOT)
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for root in source_roots
        for path in sorted(root.glob("*.py"))
    )
    forbidden = (
        "alpaca.trading",
        "TradingClient",
        "execution.alpaca_paper",
        "paper_ops",
        "submit_order",
        "submit_market_day_order",
        "submit_approved_order",
        "ENABLE_PAPER_EXECUTION",
    )

    for fragment in forbidden:
        assert fragment not in source
