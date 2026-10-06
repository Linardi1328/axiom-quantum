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
    from spy_market_agent.intelligence.axiom_decision_support import (
        DECISION_SUPPORT_ID_VERSION,
        DECISION_SUPPORT_POLICY_ID,
        DECISION_SUPPORT_SCHEMA_VERSION,
        DecisionSupportAssessment,
        DecisionSupportGate,
        DecisionSupportGateResult,
        DecisionSupportGateStatus,
        DecisionSupportPolicy,
        DecisionSupportVerdict,
        assess_intelligence_evidence,
        decision_support_assessment_identity,
        decision_support_policy_digest,
    )
    from spy_market_agent.intelligence.axiom_evidence import (
        INTELLIGENCE_EVIDENCE_ID_VERSION,
        INTELLIGENCE_EVIDENCE_SCHEMA_VERSION,
        MarketIntelligenceEvidence,
        build_market_intelligence_evidence,
        intelligence_evidence_identity,
    )
    from spy_market_agent.intelligence.axiom_memory import (
        INTELLIGENCE_ASSESSMENT_PREFIX,
        INTELLIGENCE_EVIDENCE_PREFIX,
        INTELLIGENCE_SESSION_PREFIX,
        IntelligenceMemoryRegistry,
    )

_LAZY_PHASE3_EXPORTS = {
    "DECISION_SUPPORT_ID_VERSION": "axiom_decision_support",
    "DECISION_SUPPORT_POLICY_ID": "axiom_decision_support",
    "DECISION_SUPPORT_SCHEMA_VERSION": "axiom_decision_support",
    "DecisionSupportAssessment": "axiom_decision_support",
    "DecisionSupportGate": "axiom_decision_support",
    "DecisionSupportGateResult": "axiom_decision_support",
    "DecisionSupportGateStatus": "axiom_decision_support",
    "DecisionSupportPolicy": "axiom_decision_support",
    "DecisionSupportVerdict": "axiom_decision_support",
    "INTELLIGENCE_EVIDENCE_ID_VERSION": "axiom_evidence",
    "INTELLIGENCE_EVIDENCE_SCHEMA_VERSION": "axiom_evidence",
    "INTELLIGENCE_ASSESSMENT_PREFIX": "axiom_memory",
    "INTELLIGENCE_EVIDENCE_PREFIX": "axiom_memory",
    "INTELLIGENCE_SESSION_PREFIX": "axiom_memory",
    "IntelligenceMemoryRegistry": "axiom_memory",
    "MarketIntelligenceEvidence": "axiom_evidence",
    "assess_intelligence_evidence": "axiom_decision_support",
    "build_market_intelligence_evidence": "axiom_evidence",
    "decision_support_assessment_identity": "axiom_decision_support",
    "decision_support_policy_digest": "axiom_decision_support",
    "intelligence_evidence_identity": "axiom_evidence",
}


def __getattr__(name: str) -> object:
    """Load brief/research-dependent Phase 3 exports without package import cycles."""

    module_name = _LAZY_PHASE3_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(f"spy_market_agent.intelligence.{module_name}")
    return getattr(module, name)


__all__ = [
    "DECISION_SUPPORT_ID_VERSION",
    "DECISION_SUPPORT_POLICY_ID",
    "DECISION_SUPPORT_SCHEMA_VERSION",
    "INTELLIGENCE_ASSESSMENT_PREFIX",
    "INTELLIGENCE_EVIDENCE_ID_VERSION",
    "INTELLIGENCE_EVIDENCE_PREFIX",
    "INTELLIGENCE_EVIDENCE_SCHEMA_VERSION",
    "INTELLIGENCE_SESSION_ID_VERSION",
    "INTELLIGENCE_SESSION_PREFIX",
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
    "DecisionSupportAssessment",
    "DecisionSupportGate",
    "DecisionSupportGateResult",
    "DecisionSupportGateStatus",
    "DecisionSupportPolicy",
    "DecisionSupportVerdict",
    "EvidenceItem",
    "HorizonUnit",
    "InstrumentProfile",
    "IntelligenceMemoryRegistry",
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
    "assess_intelligence_evidence",
    "assess_scenario_actionability",
    "build_intelligence_session",
    "build_market_intelligence_evidence",
    "decision_support_assessment_identity",
    "decision_support_policy_digest",
    "derive_intelligence_run_identity",
    "derive_series_snapshot_id",
    "derive_spy_market_state",
    "evidence_reference_ids",
    "intelligence_evidence_identity",
    "intelligence_session_identity",
    "legacy_spy_market_data_to_snapshot",
]
