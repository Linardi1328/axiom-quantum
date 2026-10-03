from __future__ import annotations

from pydantic import ValidationError

from spy_market_agent.research.artifacts import ResearchArtifactStore
from spy_market_agent.research.errors import ResearchRegistryError, raise_research_error
from spy_market_agent.research.experiment_core import (
    ExperimentDefinition,
    ExperimentResult,
    StoredExperiment,
    StoredExperimentResult,
    experiment_identity,
    result_identity,
)

AXIOM_EXPERIMENT_MANIFEST_NAME = "axiom_experiment.json"
AXIOM_RESULT_PREFIX = "axiom_result_"


class ResearchMemoryRegistry:
    """Append-only research memory over the existing safe artifact store."""

    def __init__(self, store: ResearchArtifactStore | None = None) -> None:
        self.store = store or ResearchArtifactStore()

    def register_experiment(self, definition: ExperimentDefinition) -> str:
        canonical_definition = ExperimentDefinition.model_validate(
            definition.model_dump(mode="python")
        )
        experiment_id = experiment_identity(canonical_definition)
        manifest_path = self.store.artifact_path(
            experiment_id,
            AXIOM_EXPERIMENT_MANIFEST_NAME,
        )
        if manifest_path.exists():
            self.load_experiment(experiment_id)
            return experiment_id
        record = StoredExperiment(
            experiment_id=experiment_id,
            definition=canonical_definition,
        )
        self.store.write_json(
            experiment_id,
            AXIOM_EXPERIMENT_MANIFEST_NAME,
            record,
            allow_replace=False,
        )
        return experiment_id

    def load_experiment(self, experiment_id: str) -> StoredExperiment:
        payload = self.store.read_json(experiment_id, AXIOM_EXPERIMENT_MANIFEST_NAME)
        try:
            record = StoredExperiment.model_validate(payload)
        except ValidationError:
            raise_research_error(
                ResearchRegistryError,
                "invalid_axiom_experiment_record",
                "stored Axiom experiment record failed canonical validation.",
            )
        if record.experiment_id != experiment_id:
            raise_research_error(
                ResearchRegistryError,
                "axiom_experiment_directory_identity_mismatch",
                "stored experiment identity must match its artifact directory.",
            )
        return record

    def record_result(self, result: ExperimentResult) -> str:
        canonical_result = ExperimentResult.model_validate(result.model_dump(mode="python"))
        self.load_experiment(canonical_result.experiment_id)
        result_id = result_identity(canonical_result)
        record = StoredExperimentResult(result_id=result_id, result=canonical_result)
        self.store.write_json(
            canonical_result.experiment_id,
            self._result_name(result_id),
            record,
            allow_replace=False,
        )
        return result_id

    def load_result(self, experiment_id: str, result_id: str) -> StoredExperimentResult:
        self.load_experiment(experiment_id)
        payload = self.store.read_json(experiment_id, self._result_name(result_id))
        try:
            record = StoredExperimentResult.model_validate(payload)
        except ValidationError:
            raise_research_error(
                ResearchRegistryError,
                "invalid_axiom_result_record",
                "stored Axiom experiment result failed canonical validation.",
            )
        if record.result_id != result_id or record.result.experiment_id != experiment_id:
            raise_research_error(
                ResearchRegistryError,
                "axiom_result_identity_mismatch",
                "stored result identity and parent experiment must match the requested record.",
            )
        return record

    def list_result_ids(self, experiment_id: str) -> tuple[str, ...]:
        self.load_experiment(experiment_id)
        result_ids: list[str] = []
        for name in self.store.existing_artifacts(experiment_id):
            if name.startswith(AXIOM_RESULT_PREFIX) and name.endswith(".json"):
                result_ids.append(name[len(AXIOM_RESULT_PREFIX) : -len(".json")])
        return tuple(sorted(result_ids))

    def list_experiment_ids(self) -> tuple[str, ...]:
        root = self.store.artifact_root
        if not root.exists():
            return ()
        experiment_ids: list[str] = []
        for path in root.iterdir():
            if path.is_symlink() or not path.is_dir():
                continue
            if (
                path.name.startswith("aq-exp-")
                and (path / AXIOM_EXPERIMENT_MANIFEST_NAME).is_file()
            ):
                experiment_ids.append(path.name)
        return tuple(sorted(experiment_ids))

    @staticmethod
    def _result_name(result_id: str) -> str:
        if not result_id.startswith("aq-result-") or "/" in result_id or "\\" in result_id:
            raise_research_error(
                ResearchRegistryError,
                "invalid_axiom_result_id",
                "result_id must be a canonical Axiom result identity.",
            )
        return f"{AXIOM_RESULT_PREFIX}{result_id}.json"
