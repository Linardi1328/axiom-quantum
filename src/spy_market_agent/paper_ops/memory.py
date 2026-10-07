from __future__ import annotations

import re

from pydantic import ValidationError

from spy_market_agent.benchmark.artifacts import canonical_json_bytes
from spy_market_agent.paper_ops.assessment import PaperReadinessAssessment
from spy_market_agent.paper_ops.recovery_case import PaperRecoveryCase
from spy_market_agent.paper_ops.session import PaperReadinessSession
from spy_market_agent.research.artifacts import ResearchArtifactStore
from spy_market_agent.research.errors import ResearchRegistryError, raise_research_error
from spy_market_agent.supervision.memory import SupervisionMemoryRegistry
from spy_market_agent.supervision.reporting import load_supervision_report

PAPER_READINESS_SESSION_PREFIX = "axiom_paper_readiness_session_"
PAPER_READINESS_ASSESSMENT_PREFIX = "axiom_paper_readiness_assessment_"
PAPER_RECOVERY_CASE_PREFIX = "axiom_paper_recovery_case_"

_SESSION_ID = re.compile(r"^aq-paper-readiness-session-[0-9a-f]{24}$")
_ASSESSMENT_ID = re.compile(r"^aq-paper-readiness-assessment-[0-9a-f]{24}$")
_RECOVERY_CASE_ID = re.compile(r"^aq-paper-recovery-case-[0-9a-f]{24}$")


class PaperReadinessMemoryRegistry:
    """Append-only Phase 5 readiness memory over the canonical artifact store."""

    def __init__(self, store: ResearchArtifactStore | None = None) -> None:
        self.store = store or ResearchArtifactStore()
        self.supervision_memory = SupervisionMemoryRegistry(self.store)

    def record_session(self, session: PaperReadinessSession) -> str:
        """Persist a readiness session only after re-verifying its exact Phase 4 report."""

        canonical = PaperReadinessSession.model_validate(session.model_dump(mode="python"))
        load_supervision_report(canonical.report, registry=self.supervision_memory)
        self.store.write_json(
            canonical.experiment_id,
            self._session_name(canonical.paper_readiness_session_id),
            canonical,
            allow_replace=False,
        )
        loaded = self.load_session(canonical.experiment_id, canonical.paper_readiness_session_id)
        if loaded != canonical:
            raise_research_error(
                ResearchRegistryError,
                "paper_readiness_session_reload_mismatch",
                "stored Phase 5 readiness session differs after canonical reload.",
            )
        return canonical.paper_readiness_session_id

    def load_session(
        self,
        experiment_id: str,
        paper_readiness_session_id: str,
    ) -> PaperReadinessSession:
        """Load a readiness session and re-verify its complete stored Phase 4 lineage."""

        name = self._session_name(paper_readiness_session_id)
        payload = self.store.read_json(experiment_id, name)
        try:
            session = PaperReadinessSession.model_validate(payload)
        except ValidationError:
            raise_research_error(
                ResearchRegistryError,
                "invalid_paper_readiness_session_record",
                "stored Phase 5 readiness session failed canonical validation.",
            )
        if self.store.artifact_path(experiment_id, name).read_bytes() != canonical_json_bytes(
            session
        ):
            raise_research_error(
                ResearchRegistryError,
                "noncanonical_paper_readiness_session_record",
                "stored Phase 5 readiness session bytes must be canonical.",
            )
        if (
            session.paper_readiness_session_id != paper_readiness_session_id
            or session.experiment_id != experiment_id
        ):
            raise_research_error(
                ResearchRegistryError,
                "paper_readiness_session_identity_mismatch",
                "stored Phase 5 readiness session identity and experiment must match the request.",
            )
        load_supervision_report(session.report, registry=self.supervision_memory)
        return session

    def record_assessment(self, assessment: PaperReadinessAssessment) -> str:
        """Persist an assessment only when its exact readiness-session parent verifies."""

        canonical = PaperReadinessAssessment.model_validate(assessment.model_dump(mode="python"))
        stored_session = self.load_session(
            canonical.experiment_id,
            canonical.paper_readiness_session_id,
        )
        if stored_session != canonical.session:
            raise_research_error(
                ResearchRegistryError,
                "paper_readiness_assessment_session_link_mismatch",
                "Phase 5 assessment must match its exact stored readiness session.",
            )
        self.store.write_json(
            canonical.experiment_id,
            self._assessment_name(canonical.assessment_id),
            canonical,
            allow_replace=False,
        )
        loaded = self.load_assessment(canonical.experiment_id, canonical.assessment_id)
        if loaded != canonical:
            raise_research_error(
                ResearchRegistryError,
                "paper_readiness_assessment_reload_mismatch",
                "stored Phase 5 readiness assessment differs after canonical reload.",
            )
        return canonical.assessment_id

    def load_assessment(
        self,
        experiment_id: str,
        assessment_id: str,
    ) -> PaperReadinessAssessment:
        """Load an assessment and re-verify its exact stored session and Phase 4 chain."""

        name = self._assessment_name(assessment_id)
        payload = self.store.read_json(experiment_id, name)
        try:
            assessment = PaperReadinessAssessment.model_validate(payload)
        except ValidationError:
            raise_research_error(
                ResearchRegistryError,
                "invalid_paper_readiness_assessment_record",
                "stored Phase 5 readiness assessment failed canonical validation.",
            )
        if self.store.artifact_path(experiment_id, name).read_bytes() != canonical_json_bytes(
            assessment
        ):
            raise_research_error(
                ResearchRegistryError,
                "noncanonical_paper_readiness_assessment_record",
                "stored Phase 5 readiness assessment bytes must be canonical.",
            )
        if assessment.assessment_id != assessment_id or assessment.experiment_id != experiment_id:
            raise_research_error(
                ResearchRegistryError,
                "paper_readiness_assessment_identity_mismatch",
                "stored Phase 5 assessment identity and experiment must match the request.",
            )
        stored_session = self.load_session(experiment_id, assessment.paper_readiness_session_id)
        if stored_session != assessment.session:
            raise_research_error(
                ResearchRegistryError,
                "paper_readiness_assessment_session_link_mismatch",
                "stored Phase 5 assessment must match its exact readiness-session parent.",
            )
        return assessment

    def record_recovery_case(self, recovery_case: PaperRecoveryCase) -> str:
        """Persist recovery evidence only when its exact readiness assessment verifies."""

        canonical = PaperRecoveryCase.model_validate(recovery_case.model_dump(mode="python"))
        stored_assessment = self.load_assessment(
            canonical.experiment_id,
            canonical.assessment_id,
        )
        if stored_assessment != canonical.assessment:
            raise_research_error(
                ResearchRegistryError,
                "paper_recovery_assessment_link_mismatch",
                "Phase 5 recovery case must match its exact stored readiness assessment.",
            )
        self.store.write_json(
            canonical.experiment_id,
            self._recovery_name(canonical.recovery_case_id),
            canonical,
            allow_replace=False,
        )
        loaded = self.load_recovery_case(canonical.experiment_id, canonical.recovery_case_id)
        if loaded != canonical:
            raise_research_error(
                ResearchRegistryError,
                "paper_recovery_reload_mismatch",
                "stored Phase 5 recovery case differs after canonical reload.",
            )
        return canonical.recovery_case_id

    def load_recovery_case(
        self,
        experiment_id: str,
        recovery_case_id: str,
    ) -> PaperRecoveryCase:
        """Load recovery evidence and re-verify the complete stored Phase 5/4 parent chain."""

        name = self._recovery_name(recovery_case_id)
        payload = self.store.read_json(experiment_id, name)
        try:
            recovery_case = PaperRecoveryCase.model_validate(payload)
        except ValidationError:
            raise_research_error(
                ResearchRegistryError,
                "invalid_paper_recovery_record",
                "stored Phase 5 recovery case failed canonical validation.",
            )
        if self.store.artifact_path(experiment_id, name).read_bytes() != canonical_json_bytes(
            recovery_case
        ):
            raise_research_error(
                ResearchRegistryError,
                "noncanonical_paper_recovery_record",
                "stored Phase 5 recovery-case bytes must be canonical.",
            )
        if (
            recovery_case.recovery_case_id != recovery_case_id
            or recovery_case.experiment_id != experiment_id
        ):
            raise_research_error(
                ResearchRegistryError,
                "paper_recovery_identity_mismatch",
                "stored Phase 5 recovery identity and experiment must match the request.",
            )
        stored_assessment = self.load_assessment(experiment_id, recovery_case.assessment_id)
        if stored_assessment != recovery_case.assessment:
            raise_research_error(
                ResearchRegistryError,
                "paper_recovery_assessment_link_mismatch",
                "stored Phase 5 recovery case must match its exact readiness-assessment parent.",
            )
        return recovery_case

    def list_session_ids(self, experiment_id: str) -> tuple[str, ...]:
        """List stored readiness-session identities in deterministic order."""

        return self._list_ids(experiment_id, PAPER_READINESS_SESSION_PREFIX, _SESSION_ID)

    def list_assessment_ids(self, experiment_id: str) -> tuple[str, ...]:
        """List stored readiness-assessment identities in deterministic order."""

        return self._list_ids(experiment_id, PAPER_READINESS_ASSESSMENT_PREFIX, _ASSESSMENT_ID)

    def list_recovery_case_ids(self, experiment_id: str) -> tuple[str, ...]:
        """List stored recovery-case identities in deterministic order."""

        return self._list_ids(experiment_id, PAPER_RECOVERY_CASE_PREFIX, _RECOVERY_CASE_ID)

    def _list_ids(
        self,
        experiment_id: str,
        prefix: str,
        pattern: re.Pattern[str],
    ) -> tuple[str, ...]:
        suffix = ".json"
        identities: list[str] = []
        for name in self.store.existing_artifacts(experiment_id):
            if not name.startswith(prefix):
                continue
            if not name.endswith(suffix):
                raise_research_error(
                    ResearchRegistryError,
                    "invalid_paper_readiness_artifact_name",
                    f"stored Phase 5 readiness artifact name is not canonical: {name}",
                )
            identity = name[len(prefix) : -len(suffix)]
            if not pattern.fullmatch(identity):
                raise_research_error(
                    ResearchRegistryError,
                    "invalid_paper_readiness_artifact_name",
                    f"stored Phase 5 readiness artifact name is not canonical: {name}",
                )
            identities.append(identity)
        return tuple(sorted(identities))

    @staticmethod
    def _session_name(paper_readiness_session_id: str) -> str:
        if not _SESSION_ID.fullmatch(paper_readiness_session_id):
            raise_research_error(
                ResearchRegistryError,
                "invalid_paper_readiness_session_id",
                "paper_readiness_session_id must be a canonical Axiom Phase 5 identity.",
            )
        return f"{PAPER_READINESS_SESSION_PREFIX}{paper_readiness_session_id}.json"

    @staticmethod
    def _assessment_name(assessment_id: str) -> str:
        if not _ASSESSMENT_ID.fullmatch(assessment_id):
            raise_research_error(
                ResearchRegistryError,
                "invalid_paper_readiness_assessment_id",
                "assessment_id must be a canonical Axiom Phase 5 readiness identity.",
            )
        return f"{PAPER_READINESS_ASSESSMENT_PREFIX}{assessment_id}.json"

    @staticmethod
    def _recovery_name(recovery_case_id: str) -> str:
        if not _RECOVERY_CASE_ID.fullmatch(recovery_case_id):
            raise_research_error(
                ResearchRegistryError,
                "invalid_paper_recovery_case_id",
                "recovery_case_id must be a canonical Axiom Phase 5 recovery identity.",
            )
        return f"{PAPER_RECOVERY_CASE_PREFIX}{recovery_case_id}.json"
