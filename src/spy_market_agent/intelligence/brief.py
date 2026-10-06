from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from spy_market_agent.intelligence.contracts import (
    DataQualityDecision,
    IntelligenceRunIdentity,
)
from spy_market_agent.intelligence.degradation import DegradationAssessment
from spy_market_agent.intelligence.relationships import CrossAssetRelationshipSummary
from spy_market_agent.intelligence.scenarios import (
    ScenarioActionabilityDecision,
    ScenarioForecast,
)
from spy_market_agent.intelligence.state import MarketStateSnapshot
from spy_market_agent.research.scenario_analogues import HistoricalAnalogueSummary
from spy_market_agent.research.scenario_protected import (
    MI1ProtectedEvaluationResult,
    MI1ProtectedScientificStatus,
)

MI1K_BRIEF_SCHEMA_ID = "mi1k-spy-deterministic-brief-v1"
MI1K_ACCEPTANCE_POLICY_ID = "mi1k-phase1-acceptance-v1"


class MI1ImplementationStatus(StrEnum):
    IMPLEMENTATION_APPROVED = "implementation_approved"


class MI1ScientificStatus(StrEnum):
    PENDING_PROTECTED_EVALUATION = "pending_protected_evaluation"
    PROTECTED_EVALUATION_COMPLETED_NO_PROMOTION = "protected_evaluation_completed_no_promotion"
    ELIGIBLE_FOR_SEPARATE_PROMOTION_REVIEW = "eligible_for_separate_promotion_review"


@dataclass(frozen=True, slots=True)
class ScenarioBriefEntry:
    forecast: ScenarioForecast
    actionability: ScenarioActionabilityDecision

    def __post_init__(self) -> None:
        if not isinstance(self.forecast, ScenarioForecast):
            raise ValueError("forecast must be a ScenarioForecast.")
        if not isinstance(self.actionability, ScenarioActionabilityDecision):
            raise ValueError("actionability must be a ScenarioActionabilityDecision.")


@dataclass(frozen=True, slots=True)
class SPYMarketIntelligenceBrief:
    schema_id: str
    run_identity: IntelligenceRunIdentity
    data_quality: DataQualityDecision
    market_state: MarketStateSnapshot
    scenarios: tuple[ScenarioBriefEntry, ...]
    analogues: tuple[HistoricalAnalogueSummary, ...]
    relationships: tuple[CrossAssetRelationshipSummary, ...]
    degradation: tuple[DegradationAssessment, ...]
    limitations: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.schema_id != MI1K_BRIEF_SCHEMA_ID:
            raise ValueError("schema_id must match the MI-1K deterministic brief schema.")
        if self.market_state.run_identity != self.run_identity:
            raise ValueError("market state must use the brief run identity.")
        horizons = tuple(entry.forecast.horizon for entry in self.scenarios)
        if len(horizons) != len(set(horizons)):
            raise ValueError("brief scenarios must have unique horizons.")
        if any(entry.forecast.run_identity != self.run_identity for entry in self.scenarios):
            raise ValueError("all scenario forecasts must use the brief run identity.")
        analogue_horizons = tuple(item.horizon_length for item in self.analogues)
        if len(analogue_horizons) != len(set(analogue_horizons)):
            raise ValueError("brief analogue summaries must have unique horizons.")
        relationship_ids = tuple(item.context_series_id for item in self.relationships)
        if len(relationship_ids) != len(set(relationship_ids)):
            raise ValueError("brief relationships must have unique context series identifiers.")
        normalized_limitations = tuple(item.strip() for item in self.limitations)
        if any(not item for item in normalized_limitations):
            raise ValueError("limitations must contain non-empty text.")
        if len(normalized_limitations) != len(set(normalized_limitations)):
            raise ValueError("limitations must not contain duplicates.")
        object.__setattr__(self, "limitations", normalized_limitations)


@dataclass(frozen=True, slots=True)
class MI1Phase1Acceptance:
    policy_id: str
    implementation_status: MI1ImplementationStatus
    scientific_status: MI1ScientificStatus
    protected_evaluation_id: str | None
    model_connected_trading_authorized: bool
    notes: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.policy_id != MI1K_ACCEPTANCE_POLICY_ID:
            raise ValueError("policy_id must match MI-1K acceptance policy.")
        if self.implementation_status != MI1ImplementationStatus.IMPLEMENTATION_APPROVED:
            raise ValueError("Phase 1 acceptance requires implementation approval.")
        if self.model_connected_trading_authorized:
            raise ValueError("Phase 1 must never authorize model-connected trading.")
        if self.scientific_status == MI1ScientificStatus.PENDING_PROTECTED_EVALUATION:
            if self.protected_evaluation_id is not None:
                raise ValueError("pending scientific status must not reference protected evidence.")
        elif self.protected_evaluation_id is None:
            raise ValueError("completed scientific status requires protected evidence lineage.")
        if any(not note.strip() for note in self.notes):
            raise ValueError("acceptance notes must be non-empty.")


def build_spy_market_intelligence_brief(
    *,
    run_identity: IntelligenceRunIdentity,
    data_quality: DataQualityDecision,
    market_state: MarketStateSnapshot,
    scenarios: tuple[ScenarioBriefEntry, ...] = (),
    analogues: tuple[HistoricalAnalogueSummary, ...] = (),
    relationships: tuple[CrossAssetRelationshipSummary, ...] = (),
    degradation: tuple[DegradationAssessment, ...] = (),
    limitations: tuple[str, ...] = (),
) -> SPYMarketIntelligenceBrief:
    return SPYMarketIntelligenceBrief(
        schema_id=MI1K_BRIEF_SCHEMA_ID,
        run_identity=run_identity,
        data_quality=data_quality,
        market_state=market_state,
        scenarios=tuple(sorted(scenarios, key=lambda item: item.forecast.horizon.length)),
        analogues=tuple(sorted(analogues, key=lambda item: item.horizon_length)),
        relationships=tuple(sorted(relationships, key=lambda item: item.context_series_id)),
        degradation=degradation,
        limitations=limitations,
    )


def build_phase1_acceptance(
    *,
    protected_result: MI1ProtectedEvaluationResult | None = None,
    notes: tuple[str, ...] = (),
) -> MI1Phase1Acceptance:
    if protected_result is None:
        return MI1Phase1Acceptance(
            policy_id=MI1K_ACCEPTANCE_POLICY_ID,
            implementation_status=MI1ImplementationStatus.IMPLEMENTATION_APPROVED,
            scientific_status=MI1ScientificStatus.PENDING_PROTECTED_EVALUATION,
            protected_evaluation_id=None,
            model_connected_trading_authorized=False,
            notes=notes,
        )
    scientific_status = (
        MI1ScientificStatus.ELIGIBLE_FOR_SEPARATE_PROMOTION_REVIEW
        if protected_result.scientific_status
        == MI1ProtectedScientificStatus.ELIGIBLE_FOR_SEPARATE_PROMOTION_REVIEW
        else MI1ScientificStatus.PROTECTED_EVALUATION_COMPLETED_NO_PROMOTION
    )
    return MI1Phase1Acceptance(
        policy_id=MI1K_ACCEPTANCE_POLICY_ID,
        implementation_status=MI1ImplementationStatus.IMPLEMENTATION_APPROVED,
        scientific_status=scientific_status,
        protected_evaluation_id=protected_result.evaluation_id,
        model_connected_trading_authorized=False,
        notes=notes,
    )
