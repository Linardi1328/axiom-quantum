from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INTELLIGENCE_ROOT = ROOT / "src/spy_market_agent/intelligence"
SPEC_PATH = ROOT / "docs/MI_00_MARKET_INTELLIGENCE_ARCHITECTURE_SPEC.md"


def test_mi0_specification_records_non_execution_boundary() -> None:
    spec = SPEC_PATH.read_text(encoding="utf-8")

    assert "Status: Owner-authorized architecture foundation" in spec
    assert "No broker calls" in spec
    assert "No paper-order submission" in spec
    assert "No live trading" in spec
    assert "No model inference" in spec
    assert "No network access" in spec
    assert "No version bump" in spec
    assert "P5-B" in spec
    assert "P5-C" in spec


def test_mi0_intelligence_package_is_execution_isolated() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(INTELLIGENCE_ROOT.glob("*.py"))
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
