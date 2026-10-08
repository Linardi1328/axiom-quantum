"""Append-only Phase 7 validation memory with verified Phase 5/6 lineage."""

from __future__ import annotations

import os
import re
from typing import TypeVar

from pydantic import BaseModel

from spy_market_agent.benchmark.artifacts import canonical_json_bytes
from spy_market_agent.phase6_execution.memory import PaperExecutionMemoryRegistry
from spy_market_agent.phase6_execution.reporting import load_paper_execution_report
from spy_market_agent.phase7_validation.pilot import Phase7PaperPilotObservation
from spy_market_agent.phase7_validation.safety import Phase7SafetyEvidence
from spy_market_agent.phase7_validation.session import Phase7ValidationSession
from spy_market_agent.research.artifacts import ResearchArtifactStore

_SESSION_PREFIX = "axiom_phase7_validation_session_"
_SAFETY_PREFIX = "axiom_phase7_validation_safety_"
_PILOT_PREFIX = "axiom_phase7_validation_pilot_"
_DAY_PREFIX = "axiom_phase7_paper_day_"
_SESSION_ID = re.compile(r"aq-paper-validation-session-[0-9a-f]{24}")
_SAFETY_ID = re.compile(r"aq-paper-validation-safety-[0-9a-f]{24}")
_PILOT_ID = re.compile(r"aq-paper-validation-pilot-[0-9a-f]{24}")
_ModelT = TypeVar("_ModelT", bound=BaseModel)


class Phase7ValidationMemoryRegistry:
    """No submission methods: immutable observation memory and fail-closed day claims."""

    def __init__(self, store: ResearchArtifactStore | None = None) -> None:
        self.store = store or ResearchArtifactStore()
        self.execution_memory = PaperExecutionMemoryRegistry(self.store)
        self.readiness_memory = self.execution_memory.readiness_memory

    def record_session(self, session: Phase7ValidationSession) -> str:
        canonical = Phase7ValidationSession.model_validate(session.model_dump(mode="python"))
        self._verify_parent(canonical)
        self.store.write_json(
            canonical.experiment_id,
            self._session_name(canonical.validation_session_id),
            canonical,
            allow_replace=False,
        )
        if self.load_session(canonical.experiment_id, canonical.validation_session_id) != canonical:
            raise ValueError("stored Phase 7 session mismatch")
        return canonical.validation_session_id

    def load_session(self, experiment_id: str, session_id: str) -> Phase7ValidationSession:
        session = self._load(
            experiment_id,
            self._session_name(session_id),
            Phase7ValidationSession,
        )
        if session.experiment_id != experiment_id or session.validation_session_id != session_id:
            raise ValueError("stored Phase 7 session identity mismatch")
        self._verify_parent(session)
        return session

    def record_safety(self, evidence: Phase7SafetyEvidence) -> str:
        canonical = Phase7SafetyEvidence.model_validate(evidence.model_dump(mode="python"))
        if self.load_session(canonical.experiment_id, canonical.validation_session_id) != (
            canonical.session
        ):
            raise ValueError("safety record must link exact stored session")
        self.store.write_json(
            canonical.experiment_id,
            self._safety_name(canonical.safety_evidence_id),
            canonical,
            allow_replace=False,
        )
        if self.load_safety(canonical.experiment_id, canonical.safety_evidence_id) != canonical:
            raise ValueError("stored safety record mismatch")
        return canonical.safety_evidence_id

    def load_safety(self, experiment_id: str, evidence_id: str) -> Phase7SafetyEvidence:
        evidence = self._load(experiment_id, self._safety_name(evidence_id), Phase7SafetyEvidence)
        if evidence.experiment_id != experiment_id or evidence.safety_evidence_id != evidence_id:
            raise ValueError("stored safety identity mismatch")
        if self.load_session(experiment_id, evidence.validation_session_id) != evidence.session:
            raise ValueError("stored safety session lineage mismatch")
        return evidence

    def record_pilot(self, observation: Phase7PaperPilotObservation) -> str:
        canonical = Phase7PaperPilotObservation.model_validate(
            observation.model_dump(mode="python")
        )
        self._verify_pilot_lineage(canonical)
        self._claim_day(canonical)
        self.store.write_json(
            canonical.experiment_id,
            self._pilot_name(canonical.pilot_observation_id),
            canonical,
            allow_replace=False,
        )
        if self.load_pilot(canonical.experiment_id, canonical.pilot_observation_id) != canonical:
            raise ValueError("stored pilot observation mismatch")
        return canonical.pilot_observation_id

    def load_pilot(self, experiment_id: str, observation_id: str) -> Phase7PaperPilotObservation:
        observation = self._load(
            experiment_id,
            self._pilot_name(observation_id),
            Phase7PaperPilotObservation,
        )
        if (
            observation.experiment_id != experiment_id
            or observation.pilot_observation_id != observation_id
        ):
            raise ValueError("stored pilot identity mismatch")
        self._verify_pilot_lineage(observation)
        if self.store.read_json(experiment_id, self._day_name(observation.session)) != {
            "pilot_observation_id": observation_id
        }:
            raise ValueError("stored paper pilot date guard mismatch")
        return observation

    def list_session_ids(self, experiment_id: str) -> tuple[str, ...]:
        return self._list_ids(experiment_id, _SESSION_PREFIX, _SESSION_ID)

    def list_safety_ids(self, experiment_id: str) -> tuple[str, ...]:
        return self._list_ids(experiment_id, _SAFETY_PREFIX, _SAFETY_ID)

    def list_pilot_ids(self, experiment_id: str) -> tuple[str, ...]:
        return self._list_ids(experiment_id, _PILOT_PREFIX, _PILOT_ID)

    def list_pilots(self, experiment_id: str) -> tuple[Phase7PaperPilotObservation, ...]:
        """Return validated pilot records sorted by observation date and identity."""

        records = tuple(
            self.load_pilot(experiment_id, value) for value in self.list_pilot_ids(experiment_id)
        )
        seen: set[str] = set()
        for record in records:
            day = record.session.observation_date.isoformat()
            if day in seen:
                raise ValueError("multiple pilot observations on one paper-broker date")
            seen.add(day)
        return tuple(
            sorted(
                records,
                key=lambda item: (
                    item.session.observation_date,
                    item.pilot_observation_id,
                ),
            )
        )

    def _verify_parent(self, session: Phase7ValidationSession) -> None:
        parent = self.readiness_memory.load_assessment(session.experiment_id, session.assessment_id)
        if parent != session.assessment:
            raise ValueError("Phase 7 parent must match exact stored Phase 5 assessment")

    def _verify_pilot_lineage(self, observation: Phase7PaperPilotObservation) -> None:
        if (
            self.load_session(observation.experiment_id, observation.validation_session_id)
            != observation.session
        ):
            raise ValueError("pilot record must link exact stored Phase 7 session")
        if observation.outcome is not None:
            stored = self.execution_memory.load_outcome(
                observation.experiment_id, observation.outcome.paper_execution_outcome_id
            )
            if stored != observation.outcome:
                raise ValueError("pilot record must link exact stored Phase 6 outcome")
        if observation.report is not None:
            load_paper_execution_report(observation.report, registry=self.execution_memory)

    def _claim_day(self, observation: Phase7PaperPilotObservation) -> None:
        """Atomically reserve one observation per paper date; no overwrite or race."""

        experiment_id = observation.experiment_id
        name = self._day_name(observation.session)
        data = {"pilot_observation_id": observation.pilot_observation_id}
        path = self.store.artifact_path(experiment_id, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path, flags, 0o600)
        except FileExistsError:
            if self.store.read_json(experiment_id, name) != data:
                raise ValueError("pilot observation date is already claimed") from None
            return
        with os.fdopen(descriptor, "wb") as output:
            output.write(canonical_json_bytes(data))
            output.flush()
            os.fsync(output.fileno())

    def _load(self, experiment_id: str, name: str, model: type[_ModelT]) -> _ModelT:
        """Do not accept a corrupt canonical JSON record as valid evidence."""

        return model.model_validate(self.store.read_json(experiment_id, name))

    def _list_ids(
        self,
        experiment_id: str,
        prefix: str,
        pattern: re.Pattern[str],
    ) -> tuple[str, ...]:
        ids: list[str] = []
        directory = self.store.experiment_dir(experiment_id)
        if directory.exists():
            if directory.is_symlink() or not directory.is_dir():
                raise ValueError("invalid Phase 7 experiment directory")
            for artifact in directory.iterdir():
                if artifact.name.startswith(prefix) and (
                    artifact.is_symlink() or not artifact.is_file()
                ):
                    raise ValueError("unsafe Phase 7 evidence artifact")
        for name in self.store.existing_artifacts(experiment_id):
            if not name.startswith(prefix):
                continue
            suffix = name.removeprefix(prefix)
            if not suffix.endswith(".json"):
                raise ValueError("invalid Phase 7 memory artifact name")
            candidate = suffix.removesuffix(".json")
            if not pattern.fullmatch(candidate):
                raise ValueError("invalid Phase 7 memory artifact identity")
            ids.append(candidate)
        return tuple(sorted(ids))

    @staticmethod
    def _session_name(value: str) -> str:
        if not _SESSION_ID.fullmatch(value):
            raise ValueError("invalid Phase 7 session id")
        return f"{_SESSION_PREFIX}{value}.json"

    @staticmethod
    def _safety_name(value: str) -> str:
        if not _SAFETY_ID.fullmatch(value):
            raise ValueError("invalid Phase 7 safety id")
        return f"{_SAFETY_PREFIX}{value}.json"

    @staticmethod
    def _pilot_name(value: str) -> str:
        if not _PILOT_ID.fullmatch(value):
            raise ValueError("invalid Phase 7 pilot id")
        return f"{_PILOT_PREFIX}{value}.json"

    @staticmethod
    def _day_name(session: Phase7ValidationSession) -> str:
        if session.mode != "paper_broker":
            raise ValueError("date claims require a paper broker session")
        return f"{_DAY_PREFIX}{session.observation_date.isoformat()}.json"
