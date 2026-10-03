from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from spy_market_agent.benchmark.artifacts import canonical_json_bytes, sha256_bytes
from spy_market_agent.research.constants import RESEARCH_ARTIFACT_ROOT
from spy_market_agent.research.errors import ResearchArtifactError, raise_research_error


class ResearchArtifactStore:
    """Safe deterministic store for ignored research artifacts."""

    def __init__(
        self,
        artifact_root: Path = RESEARCH_ARTIFACT_ROOT,
        *,
        repository_root: Path | None = None,
    ) -> None:
        self.repository_root = (repository_root or Path.cwd()).resolve()
        self.artifact_root = self._resolve_artifact_root(artifact_root)

    def experiment_dir(self, experiment_id: str) -> Path:
        self._validate_component(experiment_id)
        return self._safe_child(self.artifact_root, experiment_id)

    def artifact_path(self, experiment_id: str, name: str) -> Path:
        self._validate_component(name)
        if not (name.endswith(".json") or name.endswith(".md") or name == ".gitkeep"):
            raise_research_error(
                ResearchArtifactError,
                "unsupported_research_artifact_name",
                "research artifact names must be JSON, Markdown, or .gitkeep.",
            )
        return self._safe_child(self.experiment_dir(experiment_id), name)

    def write_json(
        self,
        experiment_id: str,
        name: str,
        payload: object,
        *,
        allow_replace: bool = False,
    ) -> str:
        data = canonical_json_bytes(payload)
        checksum = sha256_bytes(data)
        self.write_bytes(
            experiment_id,
            name,
            data,
            expected_checksum=checksum,
            allow_replace=allow_replace,
        )
        return checksum

    def read_json(self, experiment_id: str, name: str) -> dict[str, Any]:
        path = self.artifact_path(experiment_id, name)
        if not path.exists() or not path.is_file() or path.is_symlink():
            raise_research_error(
                ResearchArtifactError,
                "research_artifact_missing",
                f"required research artifact is missing: {name}",
            )
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            raise_research_error(
                ResearchArtifactError,
                "research_artifact_json_load_failed",
                f"research artifact JSON could not be loaded: {name}",
            )
        if not isinstance(payload, dict):
            raise_research_error(
                ResearchArtifactError,
                "research_artifact_json_not_object",
                f"research artifact JSON must be an object: {name}",
            )
        return payload

    def checksum(self, experiment_id: str, name: str) -> str:
        path = self.artifact_path(experiment_id, name)
        if not path.exists() or not path.is_file() or path.is_symlink():
            raise_research_error(
                ResearchArtifactError,
                "research_artifact_missing",
                f"required research artifact is missing: {name}",
            )
        return sha256_bytes(path.read_bytes())

    def existing_artifacts(self, experiment_id: str) -> tuple[str, ...]:
        directory = self.experiment_dir(experiment_id)
        if not directory.exists():
            return ()
        if directory.is_symlink() or not directory.is_dir():
            raise_research_error(
                ResearchArtifactError,
                "invalid_research_experiment_directory",
                "research experiment path must be a real directory.",
            )
        return tuple(
            sorted(
                path.name
                for path in directory.iterdir()
                if path.is_file() and not path.is_symlink()
            )
        )

    def write_bytes(
        self,
        experiment_id: str,
        name: str,
        payload: bytes,
        *,
        expected_checksum: str,
        allow_replace: bool = False,
    ) -> None:
        path = self.artifact_path(experiment_id, name)
        if sha256_bytes(payload) != expected_checksum:
            raise_research_error(
                ResearchArtifactError,
                "research_artifact_checksum_generation_mismatch",
                "generated research artifact checksum does not match expected checksum.",
            )
        if path.exists():
            if path.is_symlink():
                raise_research_error(
                    ResearchArtifactError,
                    "research_artifact_symlink_rejected",
                    "research artifact path must not be a symlink.",
                )
            existing_checksum = sha256_bytes(path.read_bytes())
            if existing_checksum == expected_checksum:
                return
            if not allow_replace:
                raise_research_error(
                    ResearchArtifactError,
                    "research_artifact_conflict",
                    f"existing research artifact conflicts: {name}",
                )
        self._atomic_write(path, payload, expected_checksum=expected_checksum)

    def relative_path(self, path: Path) -> str:
        resolved = path.resolve(strict=False)
        self._validate_inside_artifact_root(resolved)
        return resolved.relative_to(self.repository_root).as_posix()

    def _resolve_artifact_root(self, artifact_root: Path) -> Path:
        candidate = (
            artifact_root.resolve(strict=False)
            if artifact_root.is_absolute()
            else (self.repository_root / artifact_root).resolve(strict=False)
        )
        self._validate_inside_repository(candidate)
        blocked = (
            self.repository_root / ".git",
            self.repository_root / "src",
            self.repository_root / "tests",
            self.repository_root / "docs",
            self.repository_root / "reviews",
            self.repository_root / "data",
        )
        for blocked_path in blocked:
            try:
                candidate.relative_to(blocked_path.resolve(strict=False))
            except ValueError:
                continue
            raise_research_error(
                ResearchArtifactError,
                "unsafe_research_artifact_root",
                "artifact_root must not point inside source, test, doc, "
                "data, review, or Git paths.",
            )
        return candidate

    def _validate_component(self, value: str) -> None:
        if (
            not isinstance(value, str)
            or not value.strip()
            or value.startswith(".")
            or "/" in value
            or "\" in value
            or ".." in Path(value).parts
        ):
            raise_research_error(
                ResearchArtifactError,
                "unsafe_research_artifact_component",
                "research artifact path component is unsafe.",
            )

    def _safe_child(self, base: Path, *components: str) -> Path:
        candidate = base.joinpath(*components)
        self._validate_inside_artifact_root(candidate.resolve(strict=False))
        return candidate

    def _validate_inside_repository(self, path: Path) -> None:
        try:
            path.relative_to(self.repository_root)
        except ValueError:
            raise_research_error(
                ResearchArtifactError,
                "research_path_escape",
                "research path must stay inside the repository root.",
            )

    def _validate_inside_artifact_root(self, path: Path) -> None:
        self._validate_inside_repository(path)
        try:
            path.relative_to(self.artifact_root)
        except ValueError:
            raise_research_error(
                ResearchArtifactError,
                "research_artifact_root_escape",
                "research artifact path must stay inside artifact_root.",
            )

    def _atomic_write(self, path: Path, payload: bytes, *, expected_checksum: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path: Path | None = None
        try:
            descriptor, temp_name = tempfile.mkstemp(
                prefix=f".{path.name}.",
                suffix=".tmp",
                dir=path.parent,
            )
            temp_path = Path(temp_name)
            with os.fdopen(descriptor, "wb") as file_handle:
                file_handle.write(payload)
                file_handle.flush()
                os.fsync(file_handle.fileno())
            if sha256_bytes(temp_path.read_bytes()) != expected_checksum:
                raise_research_error(
                    ResearchArtifactError,
                    "temporary_research_artifact_checksum_mismatch",
                    "temporary research artifact checksum mismatch.",
                )
            temp_path.replace(path)
            if sha256_bytes(path.read_bytes()) != expected_checksum:
                raise_research_error(
                    ResearchArtifactError,
                    "final_research_artifact_checksum_mismatch",
                    "final research artifact checksum mismatch.",
                )
        except OSError:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
            raise_research_error(
                ResearchArtifactError,
                "research_atomic_write_failed",
                f"atomic research artifact write failed for {path.name}.",
            )
