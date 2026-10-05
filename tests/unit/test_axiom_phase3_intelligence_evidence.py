from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from pydantic import ValidationError

from spy_market_agent.benchmark.artifacts import sha256_json
from spy_market_agent.intelligence.axiom_evidence import (
    INTELLIGENCE_EVIDENCE_ID_VERSION,
    MarketIntelligenceEvidence,
    build_market_intelligence_evidence,
    intelligence_evidence_identity,
)
from spy_market_agent.intelligence.axiom_session import (
    IntelligenceSession,
    build_intelligence_session,
)
from spy_market_agent.intelligence.brief import (
    ScenarioBriefEntry,
    SPYMarketIntelligenceBrief,
    build_spy_market_intelligence_brief,
)
from spy_market_agent.intelligence.contracts import (
    AnalysisHorizon,
    DataQualityDecision,
    DataQualityStatus,
    HorizonUnit,
    IntelligenceRunIdentity,
    derive_intelligence_run_identity,
)
from spy_market_agent.intelligence.degradation import (
    MI1J_DEGRADATION_POLICY_ID,
    DegradationAssessment,
    DegradationStatus,
)
from spy_market_agent.intelligence.relationships import (
    MI1H_RELATIONSHIP_POLICY_ID,
    CrossAssetRelationshipSummary,
    RelationshipAvailability,
)
from spy_market_agent.intelligence.scenarios import (
    CalibrationStatus,
    ScenarioActionabilityDecision,
    ScenarioDecisionStatus,
    ScenarioForecast,
    ScenarioOutcome,
    ScenarioProbability,
)
from spy_market_agent.intelligence.state import (
    MarketStateDimension,
    MarketStateSnapshot,
    StateAvailability,
)
from spy_market_agent.research.scenario_analogues import (
    MI1G_ANALOGUE_POLICY_ID,
    HistoricalAnalogue,
    HistoricalAnalogueSummary,
)
from spy_market_agent.research.scenario_candidate import MI1D_FEATURE_COLUMNS
from spy_market_agent.research.validation_contract import VALIDATION_REQUIRED_EVIDENCE_STAGES
from spy_market_agent.research.validation_engine import (
    ValidationDecision,
    ValidationGateResult,
    ValidationGateStatus,
    ValidationVerdict,
)

AS_OF = datetime(2026, 10, 5, 20, 0, tzinfo=UTC)


def _decision() -> ValidationDecision:
    """Build one admitted Phase 2 validation decision."""

    gates = tuple(
        ValidationGateResult(
            stage=stage,
            status=ValidationGateStatus.PASSED,
            check_ids=(f"structural:{stage.value}",),
            reasons=("evidence passed",),
        )
        for stage in VALIDATION_REQUIRED_EVIDENCE_STAGES
    )
    return ValidationDecision(
        validation_id="aq-validation-111111111111111111111111",
        experiment_id="aq-exp-222222222222222222222222",
        result_id="aq-result-333333333333333333333333",
        policy_id="phase2-policy-v1",
        policy_digest="a" * 64,
        verdict=ValidationVerdict.VALIDATED_RESEARCH_CANDIDATE,
        gates=gates,
    )


def _run(*, as_of: datetime = AS_OF) -> IntelligenceRunIdentity:
    """Build one canonical point-in-time intelligence run."""

    return derive_intelligence_run_identity(
        target_instrument_id="spy-us-equity-etf",
        as_of=as_of,
        analysis_profile_id="mi1-spy-analysis-v1",
        snapshot_ids=("mi0-snapshot-spy", "mi0-snapshot-vix"),
        code_revision="phase3-test",
        configuration_hash="b" * 64,
    )


def _session(run: IntelligenceRunIdentity | None = None) -> IntelligenceSession:
    """Build an admitted human-requested Intelligence OS session."""

    return build_intelligence_session(
        decision=_decision(),
        intelligence_run=run or _run(),
        invocation_id="owner-session-001",
    )


def _relationship(
    *,
    as_of: datetime = AS_OF,
    target_snapshot_id: str = "mi0-snapshot-spy",
) -> CrossAssetRelationshipSummary:
    """Build an unavailable relationship that still carries point-in-time lineage."""

    return CrossAssetRelationshipSummary(
        policy_id=MI1H_RELATIONSHIP_POLICY_ID,
        target_series_id="spy",
        context_series_id="vix",
        as_of=as_of,
        trailing_window=20,
        availability=RelationshipAvailability.UNAVAILABLE,
        aligned_observation_count=0,
        return_correlation=None,
        target_return=None,
        context_return=None,
        relative_performance=None,
        reason="context unavailable",
        target_snapshot_id=target_snapshot_id,
        context_snapshot_id=None,
    )


def _brief(
    run: IntelligenceRunIdentity | None = None,
    *,
    limitations: tuple[str, ...] = ("Context may be unavailable.",),
    relationships: tuple[CrossAssetRelationshipSummary, ...] = (),
) -> SPYMarketIntelligenceBrief:
    """Build a compact existing Market Intelligence brief fixture."""

    run = run or _run()
    horizon = AnalysisHorizon(unit=HorizonUnit.SESSIONS, length=5)
    scenario = ScenarioBriefEntry(
        forecast=ScenarioForecast(
            run_identity=run,
            horizon=horizon,
            probabilities=(
                ScenarioProbability(outcome=ScenarioOutcome.DOWNSIDE, probability=0.1),
                ScenarioProbability(outcome=ScenarioOutcome.RANGE, probability=0.2),
                ScenarioProbability(outcome=ScenarioOutcome.UPSIDE, probability=0.7),
            ),
            calibration_status=CalibrationStatus.CALIBRATED,
            evidence_refs=("evidence-trend",),
        ),
        actionability=ScenarioActionabilityDecision(
            status=ScenarioDecisionStatus.HIGH_EVIDENCE,
            selected_outcome=ScenarioOutcome.UPSIDE,
            reasons=(),
        ),
    )
    analogue = HistoricalAnalogueSummary(
        policy_id=MI1G_ANALOGUE_POLICY_ID,
        query_anchor_session=date(2026, 10, 5),
        horizon_length=5,
        feature_columns=MI1D_FEATURE_COLUMNS,
        candidate_history_rows=100,
        analogues=(
            HistoricalAnalogue(
                anchor_session=date(2026, 8, 3),
                outcome_session=date(2026, 8, 10),
                distance=0.25,
                outcome=ScenarioOutcome.UPSIDE,
                forward_return=0.03,
            ),
        ),
        downside_count=0,
        range_count=0,
        upside_count=1,
        mean_forward_return=0.03,
        median_forward_return=0.03,
    )
    degradation = DegradationAssessment(
        policy_id=MI1J_DEGRADATION_POLICY_ID,
        status=DegradationStatus.INSUFFICIENT_EVIDENCE,
        recent_row_count=10,
        recent_metrics=None,
        recent_ece=None,
        selected_rows=0,
        selected_precision=None,
        selected_coverage=None,
        breached_metrics=(),
    )
    return build_spy_market_intelligence_brief(
        run_identity=run,
        data_quality=DataQualityDecision(
            status=DataQualityStatus.VERIFIED,
            eligible=True,
            reasons=(),
        ),
        market_state=MarketStateSnapshot(
            run_identity=run,
            dimensions=(
                MarketStateDimension(
                    dimension_id="trend",
                    label="Trend",
                    availability=StateAvailability.AVAILABLE,
                    value=0.02,
                    unit="return",
                    evidence_refs=("evidence-trend",),
                ),
            ),
        ),
        scenarios=(scenario,),
        analogues=(analogue,),
        relationships=relationships,
        degradation=(degradation,),
        limitations=limitations,
    )


def _reidentify(payload: dict[str, object]) -> dict[str, object]:
    """Recompute an evidence ID so direct-validation safety checks cannot rely on stale IDs."""

    session = payload["session"]
    assert isinstance(session, dict)
    session_id = session["session_id"]
    assert isinstance(session_id, str)
    identity_payload = {
        "identity_version": INTELLIGENCE_EVIDENCE_ID_VERSION,
        "schema_version": payload["schema_version"],
        "session_id": session_id,
        "brief_digest": payload["brief_digest"],
        "execution_authority": payload["execution_authority"],
    }
    return payload | {"evidence_id": f"aq-intel-evidence-{sha256_json(identity_payload)[:24]}"}


def test_evidence_binds_exact_session_and_brief_deterministically() -> None:
    """Equivalent canonical inputs produce one exact evidence snapshot."""

    session = _session()
    brief = _brief()

    first = build_market_intelligence_evidence(session=session, brief=brief)
    second = build_market_intelligence_evidence(session=session, brief=_brief())

    assert first == second
    assert first.session == session
    assert first.brief == brief
    assert first.brief.scenarios == brief.scenarios
    assert first.brief.analogues == brief.analogues
    assert first.brief.degradation == brief.degradation
    assert first.brief.limitations == brief.limitations
    assert first.brief_digest == sha256_json(brief)
    assert first.evidence_id == intelligence_evidence_identity(first)
    assert first.execution_authority == "none"


def test_changed_brief_content_changes_evidence_identity() -> None:
    """Any retained brief-content change changes the evidence snapshot identity."""

    session = _session()
    baseline = build_market_intelligence_evidence(session=session, brief=_brief())
    changed = build_market_intelligence_evidence(
        session=session,
        brief=_brief(limitations=("A different explicit limitation.",)),
    )

    assert baseline.brief_digest != changed.brief_digest
    assert baseline.evidence_id != changed.evidence_id


def test_evidence_rejects_brief_from_another_intelligence_run() -> None:
    """A valid brief cannot be attached to a different Phase 3 point-in-time run."""

    session = _session()
    changed_run = _run(as_of=AS_OF + timedelta(hours=1))

    with pytest.raises(ValidationError, match="brief run identity must match"):
        build_market_intelligence_evidence(session=session, brief=_brief(changed_run))


@pytest.mark.parametrize(
    ("relationship", "message"),
    [
        (_relationship(as_of=AS_OF + timedelta(minutes=1)), "session as_of cutoff"),
        (_relationship(target_snapshot_id="mi0-snapshot-outside-session"), "belong to the session"),
    ],
)
def test_evidence_rejects_relationship_lineage_outside_session(
    relationship: CrossAssetRelationshipSummary,
    message: str,
) -> None:
    """Relationship time and snapshot provenance cannot escape the admitted session."""

    with pytest.raises(ValidationError, match=message):
        build_market_intelligence_evidence(
            session=_session(),
            brief=_brief(relationships=(relationship,)),
        )


def test_direct_evidence_validation_rejects_tampered_brief_digest() -> None:
    """A recomputed evidence ID cannot conceal a forged embedded-brief checksum."""

    evidence = build_market_intelligence_evidence(session=_session(), brief=_brief())
    payload = evidence.model_dump(mode="python")
    payload["brief_digest"] = "f" * 64

    with pytest.raises(ValidationError, match="brief_digest must match"):
        MarketIntelligenceEvidence.model_validate(_reidentify(payload))


def test_direct_evidence_validation_rejects_tampered_evidence_identity() -> None:
    """Stored evidence identity remains content-addressed to the exact session and brief."""

    evidence = build_market_intelligence_evidence(session=_session(), brief=_brief())
    payload = evidence.model_dump(mode="python")
    payload["evidence_id"] = "aq-intel-evidence-000000000000000000000000"

    with pytest.raises(ValidationError, match="evidence_id must match canonical"):
        MarketIntelligenceEvidence.model_validate(payload)


def test_bridge_module_retains_non_execution_boundary() -> None:
    """The canonical bridge does not import execution-capable subsystems."""

    from inspect import getsource

    from spy_market_agent.intelligence import axiom_evidence

    source = getsource(axiom_evidence)
    assert "spy_market_agent.execution" not in source
    assert "spy_market_agent.paper_ops" not in source
    assert "alpaca.trading" not in source
    assert "TradingClient" not in source
