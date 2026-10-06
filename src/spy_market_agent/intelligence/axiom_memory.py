from __future__ import annotations

import re

from pydantic import ValidationError

from spy_market_agent.intelligence.axiom_decision_support import DecisionSupportAssessment
from spy_market_agent.intelligence.axiom_evidence import MarketIntelligenceEvidence
from spy_market_agent.intelligence.axiom_session import IntelligenceSession
from spy_market_agent.research.artifacts import ResearchArtifactStore
from spy_market_agent.research.errors import ResearchRegistryError, raise_research_error
from spy_market_agent.research.validation_memory import ValidationMemoryRegistry

INTELLIGENCE_SESSION_PREFIX = "axiom_intelligence_session_"
INTELLIGENCE_EVIDENCE_PREFIX = "axiom_intelligence_evidence_"
INTELLIGENCE_ASSESSMENT_PREFIX = "axiom_intelligence_assessment_"

_SESSION_ID = re.compile(r"^aq-intel-session-[0-9a-f]{24}$")
_EVIDENCE_ID = re.compile(r"^aq-intel-evidence-[0-9a-f]{24}$")
_ASSESSMENT_ID = re.compile(r"^aq-intel-assessment-[0-9a-f]{24}$")


class IntelligenceMemoryRegistry:
    """Append-only Phase 3 memory over the existing safe research artifact store."""

    def __init__(self, store: ResearchArtifactStore | None = None) -> None:
        """Use one artifact store for Phase 2 lineage and all Phase 3 records."""

        self.store = store or ResearchArtifactStore()
        self.validation_memory = ValidationMemoryRegistry(self.store)

    def record_session(self, session: IntelligenceSession) -> str:
        """Persist an admitted session only when its exact Phase 2 decision is already stored."""

        canonical = IntelligenceSession.model_validate(session.model_dump(mode="python"))
        stored_decision = self.validation_memory.load_decision(
            canonical.experiment_id,
            canonical.decision_id,
        )
        if stored_decision.decision != canonical.decision:
            raise_research_error(
                ResearchRegistryError,
                "intelligence_session_validation_link_mismatch",
                "Phase 3 session must match its stored Phase 2 validation decision.",
            )
        self.store.write_json(
            canonical.experiment_id,
            self._session_name(canonical.session_id),
            canonical,
            allow_replace=False,
        )
        loaded = self.load_session(canonical.experiment_id, canonical.session_id)
        if loaded != canonical:
            raise_research_error(
                ResearchRegistryError,
                "intelligence_session_reload_mismatch",
                "stored Phase 3 session differs after canonical reload.",
            )
        return canonical.session_id

    def load_session(self, experiment_id: str, session_id: str) -> IntelligenceSession:
        """Load a session and re-verify canonical identity plus Phase 2 decision lineage."""

        payload = self.store.read_json(experiment_id, self._session_name(session_id))
        try:
            session = IntelligenceSession.model_validate(payload)
        except ValidationError:
            raise_research_error(
                ResearchRegistryError,
                "invalid_intelligence_session_record",
                "stored Phase 3 session failed canonical validation.",
            )
        if session.session_id != session_id or session.experiment_id != experiment_id:
            raise_research_error(
                ResearchRegistryError,
                "intelligence_session_identity_mismatch",
                "stored Phase 3 session identity and experiment must match the request.",
            )
        stored_decision = self.validation_memory.load_decision(experiment_id, session.decision_id)
        if stored_decision.decision != session.decision:
            raise_research_error(
                ResearchRegistryError,
                "intelligence_session_validation_link_mismatch",
                "stored Phase 3 session must match its stored Phase 2 validation decision.",
            )
        return session

    def record_evidence(self, evidence: MarketIntelligenceEvidence) -> str:
        """Append one canonical evidence snapshot after verifying its stored parent session."""

        canonical = MarketIntelligenceEvidence.model_validate(evidence.model_dump(mode="python"))
        experiment_id = canonical.session.experiment_id
        if self.load_session(experiment_id, canonical.session.session_id) != canonical.session:
            raise_research_error(
                ResearchRegistryError,
                "intelligence_evidence_session_link_mismatch",
                "Phase 3 evidence must match its stored parent session.",
            )
        self.store.write_json(
            experiment_id,
            self._evidence_name(canonical.evidence_id),
            canonical,
            allow_replace=False,
        )
        loaded = self.load_evidence(experiment_id, canonical.evidence_id)
        if loaded != canonical:
            raise_research_error(
                ResearchRegistryError,
                "intelligence_evidence_reload_mismatch",
                "stored Phase 3 evidence differs after canonical reload.",
            )
        return canonical.evidence_id

    def load_evidence(self, experiment_id: str, evidence_id: str) -> MarketIntelligenceEvidence:
        """Load evidence and re-verify the exact stored session cross-link."""

        payload = self.store.read_json(experiment_id, self._evidence_name(evidence_id))
        try:
            evidence = MarketIntelligenceEvidence.model_validate(payload)
        except ValidationError:
            raise_research_error(
                ResearchRegistryError,
                "invalid_intelligence_evidence_record",
                "stored Phase 3 evidence failed canonical validation.",
            )
        if evidence.evidence_id != evidence_id or evidence.session.experiment_id != experiment_id:
            raise_research_error(
                ResearchRegistryError,
                "intelligence_evidence_identity_mismatch",
                "stored Phase 3 evidence identity and experiment must match the request.",
            )
        stored_session = self.load_session(experiment_id, evidence.session.session_id)
        if stored_session != evidence.session:
            raise_research_error(
                ResearchRegistryError,
                "intelligence_evidence_session_link_mismatch",
                "stored Phase 3 evidence must match its stored parent session.",
            )
        return evidence

    def record_assessment(self, assessment: DecisionSupportAssessment) -> str:
        """Append one assessment only after verifying its stored evidence parent."""

        canonical = DecisionSupportAssessment.model_validate(assessment.model_dump(mode="python"))
        experiment_id = canonical.evidence.session.experiment_id
        if self.load_evidence(experiment_id, canonical.evidence.evidence_id) != canonical.evidence:
            raise_research_error(
                ResearchRegistryError,
                "intelligence_assessment_evidence_link_mismatch",
                "Phase 3 assessment must match its stored evidence parent.",
            )
        self.store.write_json(
            experiment_id,
            self._assessment_name(canonical.assessment_id),
            canonical,
            allow_replace=False,
        )
        loaded = self.load_assessment(experiment_id, canonical.assessment_id)
        if loaded != canonical:
            raise_research_error(
                ResearchRegistryError,
                "intelligence_assessment_reload_mismatch",
                "stored Phase 3 assessment differs after canonical reload.",
            )
        return canonical.assessment_id

    def load_assessment(
        self,
        experiment_id: str,
        assessment_id: str,
    ) -> DecisionSupportAssessment:
        """Load an assessment and re-verify the exact stored evidence/session chain."""

        payload = self.store.read_json(experiment_id, self._assessment_name(assessment_id))
        try:
            assessment = DecisionSupportAssessment.model_validate(payload)
        except ValidationError:
            raise_research_error(
                ResearchRegistryError,
                "invalid_intelligence_assessment_record",
                "stored Phase 3 assessment failed canonical validation.",
            )
        if (
            assessment.assessment_id != assessment_id
            or assessment.evidence.session.experiment_id != experiment_id
        ):
            raise_research_error(
                ResearchRegistryError,
                "intelligence_assessment_identity_mismatch",
                "stored Phase 3 assessment identity and experiment must match the request.",
            )
        stored_evidence = self.load_evidence(experiment_id, assessment.evidence.evidence_id)
        if stored_evidence != assessment.evidence:
            raise_research_error(
                ResearchRegistryError,
                "intelligence_assessment_evidence_link_mismatch",
                "stored Phase 3 assessment must match its stored evidence parent.",
            )
        return assessment

    def list_session_ids(self, experiment_id: str) -> tuple[str, ...]:
        """List stored session identities for one experiment in sorted order."""

        return self._list_ids(experiment_id, INTELLIGENCE_SESSION_PREFIX)

    def list_evidence_ids(self, experiment_id: str) -> tuple[str, ...]:
        """List stored evidence identities for one experiment in sorted order."""

        return self._list_ids(experiment_id, INTELLIGENCE_EVIDENCE_PREFIX)

    def list_assessment_ids(self, experiment_id: str) -> tuple[str, ...]:
        """List stored assessment identities for one experiment in sorted order."""

        return self._list_ids(experiment_id, INTELLIGENCE_ASSESSMENT_PREFIX)

    def _list_ids(self, experiment_id: str, prefix: str) -> tuple[str, ...]:
        suffix = ".json"
        return tuple(
            sorted(
                name[len(prefix) : -len(suffix)]
                for name in self.store.existing_artifacts(experiment_id)
                if name.startswith(prefix) and name.endswith(suffix)
            )
        )

    @staticmethod
    def _session_name(session_id: str) -> str:
        if not _SESSION_ID.fullmatch(session_id):
            raise_research_error(
                ResearchRegistryError,
                "invalid_intelligence_session_id",
                "session_id must be a canonical Axiom Intelligence OS identity.",
            )
        return f"{INTELLIGENCE_SESSION_PREFIX}{session_id}.json"

    @staticmethod
    def _evidence_name(evidence_id: str) -> str:
        if not _EVIDENCE_ID.fullmatch(evidence_id):
            raise_research_error(
                ResearchRegistryError,
                "invalid_intelligence_evidence_id",
                "evidence_id must be a canonical Axiom intelligence evidence identity.",
            )
        return f"{INTELLIGENCE_EVIDENCE_PREFIX}{evidence_id}.json"

    @staticmethod
    def _assessment_name(assessment_id: str) -> str:
        if not _ASSESSMENT_ID.fullmatch(assessment_id):
            raise_research_error(
                ResearchRegistryError,
                "invalid_intelligence_assessment_id",
                "assessment_id must be a canonical Axiom decision-support identity.",
            )
        return f"{INTELLIGENCE_ASSESSMENT_PREFIX}{assessment_id}.json"
