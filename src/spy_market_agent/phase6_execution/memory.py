from __future__ import annotations

import re
from typing import Literal, NoReturn, TypeVar

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

from spy_market_agent.benchmark.artifacts import canonical_json_bytes, sha256_json
from spy_market_agent.paper_ops.authorization import PaperSubmissionAuthorization
from spy_market_agent.paper_ops.execution_session import PaperExecutionSession
from spy_market_agent.paper_ops.memory import PaperReadinessMemoryRegistry
from spy_market_agent.phase6_execution.bridge import PaperExecutionOutcome
from spy_market_agent.research.artifacts import ResearchArtifactStore
from spy_market_agent.research.errors import ResearchRegistryError, raise_research_error

PAPER_EXECUTION_SESSION_PREFIX = "axiom_paper_execution_session_"
PAPER_SUBMISSION_AUTHORIZATION_PREFIX = "axiom_paper_submission_authorization_"
PAPER_AUTHORIZATION_CONSUMPTION_PREFIX = "axiom_paper_authorization_consumption_"
PAPER_EXECUTION_OUTCOME_PREFIX = "axiom_paper_execution_outcome_"
PAPER_AUTHORIZATION_CONSUMPTION_SCHEMA_VERSION = "axiom-paper-authorization-consumption-v1"
PAPER_AUTHORIZATION_CONSUMPTION_ID_VERSION = "axiom-paper-authorization-consumption-id-v1"

_SESSION_ID = re.compile(r"^aq-paper-execution-session-[0-9a-f]{24}$")
_AUTH_ID = re.compile(r"^aq-paper-submission-authorization-[0-9a-f]{24}$")
_CLAIM_ID = re.compile(r"^aq-paper-authorization-consumption-[0-9a-f]{24}$")
_OUTCOME_ID = re.compile(r"^aq-paper-execution-outcome-[0-9a-f]{24}$")
_ModelT = TypeVar("_ModelT", bound=BaseModel)


class PaperAuthorizationConsumption(BaseModel):
    """Immutable single-use claim over one exact Phase 6 authorization."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["axiom-paper-authorization-consumption-v1"] = (
        "axiom-paper-authorization-consumption-v1"
    )
    paper_authorization_consumption_id: str
    paper_submission_authorization_id: str
    paper_execution_session_id: str
    experiment_id: str
    signal_id: str
    client_order_id: str
    use_policy: Literal["single_use"] = "single_use"
    claim_source: Literal["human_invoked_execution"] = "human_invoked_execution"
    execution_authority: Literal["none"] = "none"

    @field_validator("paper_authorization_consumption_id")
    @classmethod
    def _canonical_id(cls, value: str) -> str:
        """Require a canonical Phase 6 consumption identity."""

        if not _CLAIM_ID.fullmatch(value):
            raise ValueError("paper_authorization_consumption_id must be canonical")
        return value

    @model_validator(mode="after")
    def _identity_matches(self) -> PaperAuthorizationConsumption:
        """Require content-addressed identity to match immutable claim fields."""

        if (
            self.paper_authorization_consumption_id
            != paper_authorization_consumption_identity(self)
        ):
            raise ValueError("paper_authorization_consumption_id must match canonical content")
        return self


def paper_authorization_consumption_identity(
    consumption: PaperAuthorizationConsumption,
) -> str:
    """Return deterministic identity for one authorization-consumption claim."""

    payload = consumption.model_dump(
        mode="json",
        exclude={"paper_authorization_consumption_id"},
    )
    payload["identity_version"] = PAPER_AUTHORIZATION_CONSUMPTION_ID_VERSION
    return f"aq-paper-authorization-consumption-{sha256_json(payload)[:24]}"


def build_paper_authorization_consumption(
    authorization: PaperSubmissionAuthorization,
) -> PaperAuthorizationConsumption:
    """Build the deterministic single-use claim for one exact authorization."""

    canonical = PaperSubmissionAuthorization.model_validate(
        authorization.model_dump(mode="python")
    )
    payload: dict[str, object] = {
        "schema_version": PAPER_AUTHORIZATION_CONSUMPTION_SCHEMA_VERSION,
        "paper_submission_authorization_id": canonical.paper_submission_authorization_id,
        "paper_execution_session_id": canonical.paper_execution_session_id,
        "experiment_id": canonical.experiment_id,
        "signal_id": canonical.signal_id,
        "client_order_id": canonical.client_order_id,
        "use_policy": "single_use",
        "claim_source": "human_invoked_execution",
        "execution_authority": "none",
    }
    identity_payload = payload | {
        "identity_version": PAPER_AUTHORIZATION_CONSUMPTION_ID_VERSION
    }
    claim_id = f"aq-paper-authorization-consumption-{sha256_json(identity_payload)[:24]}"
    return PaperAuthorizationConsumption.model_validate(
        {"paper_authorization_consumption_id": claim_id, **payload}
    )


class PaperExecutionMemoryRegistry:
    """Append-only Phase 6 audit memory with exact lineage verification."""

    def __init__(self, store: ResearchArtifactStore | None = None) -> None:
        self.store = store or ResearchArtifactStore()
        self.readiness_memory = PaperReadinessMemoryRegistry(self.store)

    def record_session(self, session: PaperExecutionSession) -> str:
        """Persist a session only after exact stored Phase 5 lineage verification."""

        canonical = PaperExecutionSession.model_validate(session.model_dump(mode="python"))
        stored = self.readiness_memory.load_assessment(
            canonical.experiment_id,
            canonical.assessment_id,
        )
        if stored != canonical.assessment:
            self._error(
                "paper_execution_session_assessment_link_mismatch",
                "Phase 6 session must match its exact stored Phase 5 assessment.",
            )
        self.store.write_json(
            canonical.experiment_id,
            self._session_name(canonical.paper_execution_session_id),
            canonical,
            allow_replace=False,
        )
        if self.load_session(
            canonical.experiment_id,
            canonical.paper_execution_session_id,
        ) != canonical:
            self._error(
                "paper_execution_session_reload_mismatch",
                "stored Phase 6 session differs after canonical reload.",
            )
        return canonical.paper_execution_session_id

    def load_session(
        self,
        experiment_id: str,
        session_id: str,
    ) -> PaperExecutionSession:
        """Load a session and re-verify its exact stored Phase 5/4 chain."""

        session = self._load_model(
            experiment_id,
            self._session_name(session_id),
            PaperExecutionSession,
            "invalid_paper_execution_session_record",
        )
        if (
            session.paper_execution_session_id != session_id
            or session.experiment_id != experiment_id
        ):
            self._error(
                "paper_execution_session_identity_mismatch",
                "stored Phase 6 session identity and experiment must match the request.",
            )
        stored = self.readiness_memory.load_assessment(experiment_id, session.assessment_id)
        if stored != session.assessment:
            self._error(
                "paper_execution_session_assessment_link_mismatch",
                "stored Phase 6 session must match its exact Phase 5 assessment.",
            )
        return session

    def record_authorization(self, authorization: PaperSubmissionAuthorization) -> str:
        """Persist authorization only after exact stored Phase 6 session verification."""

        canonical = PaperSubmissionAuthorization.model_validate(
            authorization.model_dump(mode="python")
        )
        if self.load_session(
            canonical.experiment_id,
            canonical.paper_execution_session_id,
        ) != canonical.session:
            self._error(
                "paper_submission_authorization_session_link_mismatch",
                "authorization must match its exact stored Phase 6 session.",
            )
        self.store.write_json(
            canonical.experiment_id,
            self._authorization_name(canonical.paper_submission_authorization_id),
            canonical,
            allow_replace=False,
        )
        if self.load_authorization(
            canonical.experiment_id,
            canonical.paper_submission_authorization_id,
        ) != canonical:
            self._error(
                "paper_submission_authorization_reload_mismatch",
                "stored Phase 6 authorization differs after canonical reload.",
            )
        return canonical.paper_submission_authorization_id

    def load_authorization(
        self,
        experiment_id: str,
        authorization_id: str,
    ) -> PaperSubmissionAuthorization:
        """Load authorization and re-verify its exact Phase 6/5 parent chain."""

        authorization = self._load_model(
            experiment_id,
            self._authorization_name(authorization_id),
            PaperSubmissionAuthorization,
            "invalid_paper_submission_authorization_record",
        )
        if (
            authorization.paper_submission_authorization_id != authorization_id
            or authorization.experiment_id != experiment_id
        ):
            self._error(
                "paper_submission_authorization_identity_mismatch",
                "stored authorization identity and experiment must match the request.",
            )
        if self.load_session(
            experiment_id,
            authorization.paper_execution_session_id,
        ) != authorization.session:
            self._error(
                "paper_submission_authorization_session_link_mismatch",
                "stored authorization must match its exact Phase 6 session.",
            )
        return authorization

    def claim_submission(self, authorization: PaperSubmissionAuthorization) -> None:
        """Consume one exact authorization exactly once before broker submission."""

        canonical = PaperSubmissionAuthorization.model_validate(
            authorization.model_dump(mode="python")
        )
        if self.load_authorization(
            canonical.experiment_id,
            canonical.paper_submission_authorization_id,
        ) != canonical:
            self._error(
                "paper_submission_authorization_claim_link_mismatch",
                "submission claim must reference the exact stored authorization.",
            )
        claim = build_paper_authorization_consumption(canonical)
        name = self._consumption_name(claim.paper_authorization_consumption_id)
        if name in self.store.existing_artifacts(canonical.experiment_id):
            self._error(
                "paper_submission_authorization_already_consumed",
                "paper submission authorization has already been consumed.",
            )
        self.store.write_json(
            canonical.experiment_id,
            name,
            claim,
            allow_replace=False,
        )
        if self.load_consumption(
            canonical.experiment_id,
            claim.paper_authorization_consumption_id,
        ) != claim:
            self._error(
                "paper_authorization_consumption_reload_mismatch",
                "stored authorization consumption differs after canonical reload.",
            )

    def load_consumption(
        self,
        experiment_id: str,
        claim_id: str,
    ) -> PaperAuthorizationConsumption:
        """Load a consumption claim and re-verify its exact authorization lineage."""

        claim = self._load_model(
            experiment_id,
            self._consumption_name(claim_id),
            PaperAuthorizationConsumption,
            "invalid_paper_authorization_consumption_record",
        )
        if (
            claim.paper_authorization_consumption_id != claim_id
            or claim.experiment_id != experiment_id
        ):
            self._error(
                "paper_authorization_consumption_identity_mismatch",
                "stored consumption identity and experiment must match the request.",
            )
        authorization = self.load_authorization(
            experiment_id,
            claim.paper_submission_authorization_id,
        )
        if (
            claim.paper_execution_session_id != authorization.paper_execution_session_id
            or claim.signal_id != authorization.signal_id
            or claim.client_order_id != authorization.client_order_id
        ):
            self._error(
                "paper_authorization_consumption_lineage_mismatch",
                "stored consumption must preserve exact authorization lineage.",
            )
        return claim

    def load_consumption_for_authorization(
        self,
        authorization: PaperSubmissionAuthorization,
    ) -> PaperAuthorizationConsumption:
        """Load deterministic consumption evidence for one stored authorization."""

        stored = self.load_authorization(
            authorization.experiment_id,
            authorization.paper_submission_authorization_id,
        )
        expected = build_paper_authorization_consumption(stored)
        return self.load_consumption(
            stored.experiment_id,
            expected.paper_authorization_consumption_id,
        )

    def record_outcome(self, outcome: PaperExecutionOutcome) -> str:
        """Append one valid submission or lookup-only reconciliation outcome."""

        canonical = PaperExecutionOutcome.model_validate(outcome.model_dump(mode="python"))
        authorization = self.load_authorization(
            canonical.experiment_id,
            canonical.paper_submission_authorization_id,
        )
        if authorization != canonical.authorization:
            self._error(
                "paper_execution_outcome_authorization_link_mismatch",
                "execution outcome must match its exact stored authorization.",
            )
        self.load_consumption_for_authorization(authorization)
        prior = tuple(
            self.load_outcome(canonical.experiment_id, outcome_id)
            for outcome_id in self.list_outcome_ids_for_authorization(authorization)
        )
        submissions = tuple(item for item in prior if not item.reconciliation_lookup_only)
        reconciliations = tuple(item for item in prior if item.reconciliation_lookup_only)
        if canonical.reconciliation_lookup_only:
            if len(submissions) != 1 or submissions[0].disposition != "submission_unknown":
                self._error(
                    "paper_reconciliation_without_unknown_submission",
                    "reconciliation requires exactly one prior submission_unknown outcome.",
                )
            if reconciliations:
                self._error(
                    "paper_reconciliation_already_recorded",
                    "only one reconciliation outcome may be recorded per authorization.",
                )
        elif submissions:
            self._error(
                "paper_submission_outcome_already_recorded",
                "only one submission outcome may be recorded per authorization.",
            )
        self.store.write_json(
            canonical.experiment_id,
            self._outcome_name(canonical.paper_execution_outcome_id),
            canonical,
            allow_replace=False,
        )
        if self.load_outcome(
            canonical.experiment_id,
            canonical.paper_execution_outcome_id,
        ) != canonical:
            self._error(
                "paper_execution_outcome_reload_mismatch",
                "stored Phase 6 outcome differs after canonical reload.",
            )
        return canonical.paper_execution_outcome_id

    def load_outcome(self, experiment_id: str, outcome_id: str) -> PaperExecutionOutcome:
        """Load an outcome and re-verify its complete Phase 6/5 lineage."""

        outcome = self._load_model(
            experiment_id,
            self._outcome_name(outcome_id),
            PaperExecutionOutcome,
            "invalid_paper_execution_outcome_record",
        )
        if (
            outcome.paper_execution_outcome_id != outcome_id
            or outcome.experiment_id != experiment_id
        ):
            self._error(
                "paper_execution_outcome_identity_mismatch",
                "stored outcome identity and experiment must match the request.",
            )
        if self.load_authorization(
            experiment_id,
            outcome.paper_submission_authorization_id,
        ) != outcome.authorization:
            self._error(
                "paper_execution_outcome_authorization_link_mismatch",
                "stored outcome must match its exact authorization.",
            )
        return outcome

    def list_session_ids(self, experiment_id: str) -> tuple[str, ...]:
        """List stored session identities deterministically."""

        return self._list_ids(experiment_id, PAPER_EXECUTION_SESSION_PREFIX, _SESSION_ID)

    def list_authorization_ids(self, experiment_id: str) -> tuple[str, ...]:
        """List stored authorization identities deterministically."""

        return self._list_ids(experiment_id, PAPER_SUBMISSION_AUTHORIZATION_PREFIX, _AUTH_ID)

    def list_consumption_ids(self, experiment_id: str) -> tuple[str, ...]:
        """List stored consumption identities deterministically."""

        return self._list_ids(experiment_id, PAPER_AUTHORIZATION_CONSUMPTION_PREFIX, _CLAIM_ID)

    def list_outcome_ids(self, experiment_id: str) -> tuple[str, ...]:
        """List stored outcome identities deterministically."""

        return self._list_ids(experiment_id, PAPER_EXECUTION_OUTCOME_PREFIX, _OUTCOME_ID)

    def list_outcome_ids_for_authorization(
        self,
        authorization: PaperSubmissionAuthorization,
    ) -> tuple[str, ...]:
        """List exact outcomes for one authorization deterministically."""

        stored = self.load_authorization(
            authorization.experiment_id,
            authorization.paper_submission_authorization_id,
        )
        matches: list[str] = []
        for outcome_id in self.list_outcome_ids(stored.experiment_id):
            outcome = self.load_outcome(stored.experiment_id, outcome_id)
            if (
                outcome.paper_submission_authorization_id
                == stored.paper_submission_authorization_id
            ):
                matches.append(outcome_id)
        return tuple(sorted(matches))

    def _load_model(
        self,
        experiment_id: str,
        name: str,
        model_type: type[_ModelT],
        error_code: str,
    ) -> _ModelT:
        payload = self.store.read_json(experiment_id, name)
        try:
            model = model_type.model_validate(payload)
        except ValidationError:
            self._error(error_code, "stored Phase 6 record failed canonical validation.")
        if (
            self.store.artifact_path(experiment_id, name).read_bytes()
            != canonical_json_bytes(model)
        ):
            self._error(
                "noncanonical_phase6_execution_record",
                "stored Phase 6 record bytes must be canonical.",
            )
        return model

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
                self._error(
                    "invalid_phase6_execution_artifact_name",
                    "stored Phase 6 artifact name is not canonical.",
                )
            identity = name[len(prefix) : -len(suffix)]
            if not pattern.fullmatch(identity):
                self._error(
                    "invalid_phase6_execution_artifact_name",
                    "stored Phase 6 artifact name is not canonical.",
                )
            identities.append(identity)
        return tuple(sorted(identities))

    @staticmethod
    def _error(code: str, message: str) -> NoReturn:
        raise_research_error(ResearchRegistryError, code, message)

    @staticmethod
    def _session_name(value: str) -> str:
        return PaperExecutionMemoryRegistry._name(
            value, _SESSION_ID, PAPER_EXECUTION_SESSION_PREFIX
        )

    @staticmethod
    def _authorization_name(value: str) -> str:
        return PaperExecutionMemoryRegistry._name(
            value,
            _AUTH_ID,
            PAPER_SUBMISSION_AUTHORIZATION_PREFIX,
        )

    @staticmethod
    def _consumption_name(value: str) -> str:
        return PaperExecutionMemoryRegistry._name(
            value,
            _CLAIM_ID,
            PAPER_AUTHORIZATION_CONSUMPTION_PREFIX,
        )

    @staticmethod
    def _outcome_name(value: str) -> str:
        return PaperExecutionMemoryRegistry._name(
            value, _OUTCOME_ID, PAPER_EXECUTION_OUTCOME_PREFIX
        )

    @staticmethod
    def _name(value: str, pattern: re.Pattern[str], prefix: str) -> str:
        if not pattern.fullmatch(value):
            raise_research_error(
                ResearchRegistryError,
                "invalid_phase6_execution_identity",
                "Phase 6 execution identity must be canonical.",
            )
        return f"{prefix}{value}.json"
