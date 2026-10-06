from importlib import import_module
from typing import TYPE_CHECKING

from spy_market_agent.intelligence.axiom_session import (
    INTELLIGENCE_SESSION_ID_VERSION,
    INTELLIGENCE_SESSION_SCHEMA_VERSION,
    IntelligenceSession,
    build_intelligence_session,
    intelligence_session_identity,
)
from spy_market_agent.intelligence.contracts import (
    AnalysisHorizon,
    AnalysisProfile,
    AssetClass,
    DataQualityDecision,
    DataQualityStatus,
    HorizonUnit,
    InstrumentProfile,
    IntelligenceRunIdentity,
    SeriesSnapshot,
    SessionModel,
    derive_intelligence_run_identity,
    derive_series_snapshot_id,
)
from spy_market_agent.intelligence.evidence import EvidenceItem, evidence_reference_ids
from spy_market_agent.intelligence.legacy_spy import (
    LEGACY_SPY_INSTRUMENT_PROFILE,
    LEGACY_SPY_SERIES_ID,
    legacy_spy_market_data_to_snapshot,
)
from spy_market_agent.intelligence.profiles import (
    MI1_IWM_DAILY_SERIES_ID,
    MI1_QQQ_DAILY_SERIES_ID,
    MI1_SPY_ANALYSIS_PROFILE,
    MI1_SPY_SCENARIO_SCHEMA_ID,
    MI1_US_10Y_YIELD_DAILY_SERIES_ID,
    MI1_VIX_DAILY_SERIES_ID,
)
from spy_market_agent.intelligence.scenarios import (
    AbstentionReason,
    CalibrationStatus,
    ScenarioActionabilityDecision,
    ScenarioDecisionStatus,
    ScenarioForecast,
    ScenarioOutcome,
    ScenarioProbability,
    assess_scenario_actionability,
)
from spy_market_agent.intelligence.spy_state import (
    SPYMarketStateDerivation,
    derive_spy_market_state,
)
from spy_market_agent.intelligence.state import (
    MarketStateDimension,
    MarketStateSnapshot,
    StateAvailability,
)

if TYPE_CHECKING:
    from spy_market_agent.intelligence.axiom_evidence import (
        INTELLIGENCE_EVIDENCE_ID_VERSION,
        INTELLIGENCE_EVIDENCE_SCHEMA_VERSION,
        MarketIntelligenceEvidence,
        build_market_intelligence_evidence,
        intelligence_evidence_identity,
    )

_LAZY_AXIOM_EVIDENCE_EXPORTS = frozenset(
    {
        "INTELLIGENCE_EVIDENCE_ID_VERSION",
        "INTELLIGENCE_EVIDENCE_SCHEMA_VERSION",
        "MarketIntelligenceEvidence",
        "build_market_intelligence_evidence",
        "intelligence_evidence_identity",
    }
)


def __getattr__(name: str) -> object:
    """Load the brief-dependent Phase 3 bridge without creating research import cycles."""

    if name in _LAZY_AXIOM_EVIDENCE_EXPORTS:
        module = import_module("spy_market_agent.intelligence.axiom_evidence")
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "INTELLIGENCE_EVIDENCE_ID_VERSION",
    "INTELLIGENCE_EVIDENCE_SCHEMA_VERSION",
    "INTELLIGENCE_SESSION_ID_VERSION",
    "INTELLIGENCE_SESSION_SCHEMA_VERSION",
    "LEGACY_SPY_INSTRUMENT_PROFILE",
    "LEGACY_SPY_SERIES_ID",
    "MI1_IWM_DAILY_SERIES_ID",
    "MI1_QQQ_DAILY_SERIES_ID",
    "MI1_SPY_ANALYSIS_PROFILE",
    "MI1_SPY_SCENARIO_SCHEMA_ID",
    "MI1_US_10Y_YIELD_DAILY_SERIES_ID",
    "MI1_VIX_DAILY_SERIES_ID",
    "AbstentionReason",
    "AnalysisHorizon",
    "AnalysisProfile",
    "AssetClass",
    "CalibrationStatus",
    "DataQualityDecision",
    "DataQualityStatus",
    "EvidenceItem",
    "HorizonUnit",
    "InstrumentProfile",
    "IntelligenceRunIdentity",
    "IntelligenceSession",
    "MarketIntelligenceEvidence",
    "MarketStateDimension",
    "MarketStateSnapshot",
    "SPYMarketStateDerivation",
    "ScenarioActionabilityDecision",
    "ScenarioDecisionStatus",
    "ScenarioForecast",
    "ScenarioOutcome",
    "ScenarioProbability",
    "SeriesSnapshot",
    "SessionModel",
    "StateAvailability",
    "assess_scenario_actionability",
    "build_intelligence_session",
    "build_market_intelligence_evidence",
    "derive_intelligence_run_identity",
    "derive_series_snapshot_id",
    "derive_spy_market_state",
    "evidence_reference_ids",
    "intelligence_evidence_identity",
    "intelligence_session_identity",
    "legacy_spy_market_data_to_snapshot",
]
