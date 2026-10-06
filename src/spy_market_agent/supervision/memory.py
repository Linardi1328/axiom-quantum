from __future__ import annotations

import re

from pydantic import ValidationError

from spy_market_agent.intelligence.axiom_memory import IntelligenceMemoryRegistry
from spy_market_agent.intelligence.axiom_reporting import load_intelligence_report
from spy_market_agent.research.artifacts import ResearchArtifactStore
from spy_market_agent.research.errors import ResearchRegistryError, raise_research_error
from spy_market_agent.supervision.disposition import SupervisedDisposition
from spy_market_agent.supervision.review_queue import SupervisedReviewItem
from spy_market_agent.supervision.session import SupervisedSession

SUPERVISION_SESSION_PREFIX = "axiom_supervision_session_"
SUPERVISION_REVIEW_PREFIX = "axiom_supervision_review_"
SUPERVISION_DISPOSITION_PREFIX = "axiom_supervision_disposition_"

_SESSION_ID = re.compile(r"^aq-supervision-session-[0-9a-f]{24}$")
_REVIEW_ID = re.compile(r"^aq-supervision-review-[0-9a-f]{24}$")
_DISPOSITION_ID = re.compile(r"^aq-supervision-disposition-[0-9a-f]{24}$")


class SupervisionMemoryRegistry:
    """Append-only Phase 4 supervision memory over the canonical artifact store."""

    def __init__(self, store: ResearchArtifactStore | None = None) -> None:
        self.store = store or ResearchArtifactStore()
        self.intelligence_memory = IntelligenceMemoryRegistry(self.store)

    def record_session(self, session: SupervisedSession) -> str:
        """Persist one session only after re-verifying its exact stored Phase 3 report chain."""

        canonical = SupervisedSession.model_validate(session.model_dump(mode="python"))
        load_intelligence_report(canonical.report, registry=self.intelligence_memory)
        self.store.write_json(
            canonical.experiment_id,
            self._session_name(canonical.supervision_session_id),
            canonical,
            allow_replace=False,
        )
        loaded = self.load_session(canonical.experiment_id, canonical.supervision_session_id)
        if loaded != canonical:
            raise_research_error(
                ResearchRegistryError,
                "supervision_session_reload_mismatch",
                "stored Phase 4 supervision session differs after canonical reload.",
            )
        return canonical.supervision_session_id

    def load_session(
        self,
        experiment_id: str,
        supervision_session_id: str,
    ) -> SupervisedSession:
        """Load a session and re-verify its identity plus complete stored Phase 3 lineage."""

        payload = self.store.read_json(
            experiment_id,
            self._session_name(supervision_session_id),
        )
        try:
            session = SupervisedSession.model_validate(payload)
        except ValidationError:
            raise_research_error(
                ResearchRegistryError,
                "invalid_supervision_session_record",
                "stored Phase 4 supervision session failed canonical validation.",
            )
        if (
            session.supervision_session_id != supervision_session_id
            or session.experiment_id != experiment_id
        ):
            raise_research_error(
                ResearchRegistryError,
                "supervision_session_identity_mismatch",
                "stored Phase 4 session identity and experiment must match the request.",
            )
        load_intelligence_report(session.report, registry=self.intelligence_memory)
        return session

    def record_review_item(self, item: SupervisedReviewItem) -> str:
        """Persist one review item only when its exact stored session parent verifies."""

        canonical = SupervisedReviewItem.model_validate(item.model_dump(mode="python"))
        stored_session = self.load_session(
            canonical.experiment_id,
            canonical.supervision_session_id,
        )
        if stored_session != canonical.session:
            raise_research_error(
                ResearchRegistryError,
                "supervision_review_session_link_mismatch",
                "Phase 4 review item must match its exact stored supervised session.",
            )
        self.store.write_json(
            canonical.experiment_id,
            self._review_name(canonical.review_item_id),
            canonical,
            allow_replace=False,
        )
        loaded = self.load_review_item(canonical.experiment_id, canonical.review_item_id)
        if loaded != canonical:
            raise_research_error(
                ResearchRegistryError,
                "supervision_review_reload_mismatch",
                "stored Phase 4 review item differs after canonical reload.",
            )
        return canonical.review_item_id

    def load_review_item(
        self,
        experiment_id: str,
        review_item_id: str,
    ) -> SupervisedReviewItem:
        """Load a review item and re-verify its exact stored session and Phase 3 chain."""

        payload = self.store.read_json(
            experiment_id,
            self._review_name(review_item_id),
        )
        try:
            item = SupervisedReviewItem.model_validate(payload)
        except ValidationError:
            raise_research_error(
                ResearchRegistryError,
                "invalid_supervision_review_record",
                "stored Phase 4 review item failed canonical validation.",
            )
        if item.review_item_id != review_item_id or item.experiment_id != experiment_id:
            raise_research_error(
                ResearchRegistryError,
                "supervision_review_identity_mismatch",
                "stored Phase 4 review identity and experiment must match the request.",
            )
        stored_session = self.load_session(experiment_id, item.supervision_session_id)
        if stored_session != item.session:
            raise_research_error(
                ResearchRegistryError,
                "supervision_review_session_link_mismatch",
                "stored Phase 4 review item must match its exact stored session parent.",
            )
        return item

    def record_disposition(self, disposition: SupervisedDisposition) -> str:
        """Persist one human disposition only when its exact stored review parent verifies."""

        canonical = SupervisedDisposition.model_validate(
            disposition.model_dump(mode="python")
        )
        stored_item = self.load_review_item(
            canonical.experiment_id,
            canonical.review_item_id,
        )
        if stored_item != canonical.review_item:
            raise_research_error(
                ResearchRegistryError,
                "supervision_disposition_review_link_mismatch",
                "Phase 4 disposition must match its exact stored review item.",
            )
        self.store.write_json(
            canonical.experiment_id,
            self._disposition_name(canonical.disposition_id),
            canonical,
            allow_replace=False,
        )
        loaded = self.load_disposition(canonical.experiment_id, canonical.disposition_id)
        if loaded != canonical:
            raise_research_error(
                ResearchRegistryError,
                "supervision_disposition_reload_mismatch",
                "stored Phase 4 disposition differs after canonical reload.",
            )
        return canonical.disposition_id

    def load_disposition(
        self,
        experiment_id: str,
        disposition_id: str,
    ) -> SupervisedDisposition:
        """Load a disposition and re-verify the complete stored Phase 4 and Phase 3 chain."""

        payload = self.store.read_json(
            experiment_id,
            self._disposition_name(disposition_id),
        )
        try:
            disposition = SupervisedDisposition.model_validate(payload)
        except ValidationError:
            raise_research_error(
                ResearchRegistryError,
                "invalid_supervision_disposition_record",
                "stored Phase 4 disposition failed canonical validation.",
            )
        if (
            disposition.disposition_id != disposition_id
            or disposition.experiment_id != experiment_id
        ):
            raise_research_error(
                ResearchRegistryError,
                "supervision_disposition_identity_mismatch",
                "stored Phase 4 disposition identity and experiment must match the request.",
            )
        stored_item = self.load_review_item(experiment_id, disposition.review_item_id)
        if stored_item != disposition.review_item:
            raise_research_error(
                ResearchRegistryError,
                "supervision_disposition_review_link_mismatch",
                "stored Phase 4 disposition must match its exact stored review parent.",
            )
        return disposition

    def list_session_ids(self, experiment_id: str) -> tuple[str, ...]:
        """List stored supervision-session identities in deterministic order."""

        return self._list_ids(experiment_id, SUPERVISION_SESSION_PREFIX)

    def list_review_item_ids(self, experiment_id: str) -> tuple[str, ...]:
        """List stored review-item identities in deterministic order."""

        return self._list_ids(experiment_id, SUPERVISION_REVIEW_PREFIX)

    def list_disposition_ids(self, experiment_id: str) -> tuple[str, ...]:
        """List stored human-disposition identities in deterministic order."""

        return self._list_ids(experiment_id, SUPERVISION_DISPOSITION_PREFIX)

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
    def _session_name(supervision_session_id: str) -> str:
        if not _SESSION_ID.fullmatch(supervision_session_id):
            raise_research_error(
                ResearchRegistryError,
                "invalid_supervision_session_id",
                "supervision_session_id must be a canonical Axiom supervision identity.",
            )
        return f"{SUPERVISION_SESSION_PREFIX}{supervision_session_id}.json"

    @staticmethod
    def _review_name(review_item_id: str) -> str:
        if not _REVIEW_ID.fullmatch(review_item_id):
            raise_research_error(
                ResearchRegistryError,
                "invalid_supervision_review_id",
                "review_item_id must be a canonical Axiom supervision review identity.",
            )
        return f"{SUPERVISION_REVIEW_PREFIX}{review_item_id}.json"

    @staticmethod
    def _disposition_name(disposition_id: str) -> str:
        if not _DISPOSITION_ID.fullmatch(disposition_id):
            raise_research_error(
                ResearchRegistryError,
                "invalid_supervision_disposition_id",
                "disposition_id must be a canonical Axiom supervision disposition identity.",
            )
        return f"{SUPERVISION_DISPOSITION_PREFIX}{disposition_id}.json"
