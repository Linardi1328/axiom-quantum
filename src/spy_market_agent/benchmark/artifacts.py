from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from spy_market_agent.benchmark.errors import BenchmarkArtifactError, raise_benchmark_error

JSON_SEPARATORS = (",", ":")
BENCHMARK_ARTIFACT_ROOT = Path("artifacts/benchmarks")


def to_jsonable(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, BaseModel):
        return to_jsonable(value.model_dump(mode="python"))
    if is_dataclass(value) and not isinstance(value, type):
        return to_jsonable(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, Iterable) and not isinstance(value, str | bytes | bytearray):
        return [to_jsonable(item) for item in value]
    return value


def canonical_json_bytes(payload: object) -> bytes:
    text = json.dumps(
        to_jsonable(payload),
        allow_nan=False,
        separators=JSON_SEPARATORS,
        sort_keys=True,
    )
    return f"{text}\n".encode()


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_json(payload: object) -> str:
    return sha256_bytes(canonical_json_bytes(payload))


def load_json_file(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise_benchmark_error(
            BenchmarkArtifactError,
            "artifact_json_load_failed",
            f"artifact JSON could not be loaded: {path.name}",
        )
    if not isinstance(raw, dict):
        raise_benchmark_error(
            BenchmarkArtifactError,
            "artifact_json_not_object",
            f"artifact JSON must be an object: {path.name}",
        )
    return raw


class BenchmarkArtifactStore:
    """Safe file-based store for ignored immutable Phase 2 benchmark artifacts."""

    def __init__(self, artifact_root: Path, *, repository_root: Path | None = None) -> None:
        self.repository_root = (repository_root or Path.cwd()).resolve()
        self.artifact_root = self._resolve_artifact_root(artifact_root)

    def benchmark_dir(self, benchmark_id: str) -> Path:
        self._validate_component(benchmark_id)
        return self._safe_child(self.artifact_root, benchmark_id)

    def artifact_path(self, benchmark_id: str, name: str) -> Path:
        self._validate_component(name)
        if not (
            name.endswith(".json")
            or name.endswith(".sha256")
            or name.endswith(".md")
            or name == ".gitkeep"
        ):
            raise_benchmark_error(
                BenchmarkArtifactError,
                "unsupported_artifact_name",
                "benchmark artifact names must be JSON, SHA-256, Markdown, or .gitkeep.",
            )
        return self._safe_child(self.benchmark_dir(benchmark_id), name)

    def relative_path(self, path: Path) -> str:
        resolved = self._safe_path(path)
        return resolved.relative_to(self.repository_root).as_posix()

    def write_json(
        self,
        benchmark_id: str,
        name: str,
        payload: object,
        *,
        allow_replace: bool = False,
    ) -> str:
        data = canonical_json_bytes(payload)
        checksum = sha256_bytes(data)
        self.write_bytes(
            benchmark_id,
            name,
            data,
            expected_checksum=checksum,
            allow_replace=allow_replace,
        )
        return checksum

    def write_text(
        self,
        benchmark_id: str,
        name: str,
        text: str,
        *,
        allow_replace: bool = False,
    ) -> str:
        if not text.endswith("\n"):
            text = f"{text}\n"
        data = text.encode("utf-8")
        checksum = sha256_bytes(data)
        self.write_bytes(
            benchmark_id,
            name,
            data,
            expected_checksum=checksum,
            allow_replace=allow_replace,
        )
        return checksum

    def write_bytes(
        self,
        benchmark_id: str,
        name: str,
        payload: bytes,
        *,
        expected_checksum: str,
        allow_replace: bool = False,
    ) -> None:
        path = self.artifact_path(benchmark_id, name)
        if sha256_bytes(payload) != expected_checksum:
            raise_benchmark_error(
                BenchmarkArtifactError,
                "artifact_checksum_generation_mismatch",
                "generated artifact checksum does not match expected checksum.",
            )
        if path.exists():
            if path.is_symlink():
                raise_benchmark_error(
                    BenchmarkArtifactError,
                    "artifact_symlink_rejected",
                    "benchmark artifact path must not be a symlink.",
                )
            existing_checksum = sha256_bytes(path.read_bytes())
            if existing_checksum == expected_checksum:
                return
            if not allow_replace:
                raise_benchmark_error(
                    BenchmarkArtifactError,
                    "artifact_conflict",
                    f"existing benchmark artifact conflicts: {name}",
                )
        self._atomic_write(path, payload, expected_checksum=expected_checksum)

    def read_json(self, benchmark_id: str, name: str) -> dict[str, Any]:
        path = self.artifact_path(benchmark_id, name)
        if not path.exists():
            raise_benchmark_error(
                BenchmarkArtifactError,
                "artifact_missing",
                f"required benchmark artifact is missing: {name}",
            )
        return load_json_file(path)

    def checksum(self, benchmark_id: str, name: str) -> str:
        path = self.artifact_path(benchmark_id, name)
        if not path.exists():
            raise_benchmark_error(
                BenchmarkArtifactError,
                "artifact_missing",
                f"required benchmark artifact is missing: {name}",
            )
        return sha256_bytes(path.read_bytes())

    def existing_artifacts(self, benchmark_id: str) -> tuple[str, ...]:
        directory = self.benchmark_dir(benchmark_id)
        if not directory.exists():
            return ()
        return tuple(sorted(path.name for path in directory.iterdir() if path.is_file()))

    def _resolve_artifact_root(self, artifact_root: Path) -> Path:
        if artifact_root.is_absolute():
            candidate = artifact_root.resolve(strict=False)
        else:
            candidate = (self.repository_root / artifact_root).resolve(strict=False)
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
            raise_benchmark_error(
                BenchmarkArtifactError,
                "unsafe_artifact_root",
                "artifact_root must not point inside source, test, doc, data, "
                "review, or Git paths.",
            )
        return candidate

    def _validate_component(self, value: str) -> None:
        if (
            not isinstance(value, str)
            or not value.strip()
            or value.startswith(".")
            or "/" in value
            or "\\" in value
            or ".." in Path(value).parts
        ):
            raise_benchmark_error(
                BenchmarkArtifactError,
                "unsafe_artifact_component",
                "benchmark artifact path component is unsafe.",
            )

    def _safe_child(self, base: Path, *components: str) -> Path:
        candidate = base.joinpath(*components).resolve(strict=False)
        self._validate_inside_artifact_root(candidate)
        return candidate

    def _safe_path(self, path: Path) -> Path:
        candidate = path.resolve(strict=False)
        self._validate_inside_artifact_root(candidate)
        return candidate

    def _validate_inside_repository(self, path: Path) -> None:
        try:
            path.relative_to(self.repository_root)
        except ValueError:
            raise_benchmark_error(
                BenchmarkArtifactError,
                "path_escape",
                "benchmark path must stay inside the repository root.",
            )

    def _validate_inside_artifact_root(self, path: Path) -> None:
        self._validate_inside_repository(path)
        try:
            path.relative_to(self.artifact_root)
        except ValueError:
            raise_benchmark_error(
                BenchmarkArtifactError,
                "artifact_root_escape",
                "benchmark artifact path must stay inside artifact_root.",
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
                raise_benchmark_error(
                    BenchmarkArtifactError,
                    "temporary_artifact_checksum_mismatch",
                    "temporary benchmark artifact checksum mismatch.",
                )
            temp_path.replace(path)
            if sha256_bytes(path.read_bytes()) != expected_checksum:
                raise_benchmark_error(
                    BenchmarkArtifactError,
                    "final_artifact_checksum_mismatch",
                    "final benchmark artifact checksum mismatch.",
                )
        except OSError:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
            raise_benchmark_error(
                BenchmarkArtifactError,
                "atomic_write_failed",
                f"atomic benchmark artifact write failed for {path.name}.",
            )


def write_loose_json(path: Path, payload: object, *, overwrite: bool = False) -> str:
    """Write a standalone owner-provided JSON record without using benchmark identity."""

    if path.is_absolute():
        raise_benchmark_error(
            BenchmarkArtifactError,
            "absolute_loose_artifact_rejected",
            "standalone benchmark records must use repository-relative paths.",
        )
    if any(part == ".." for part in path.parts):
        raise_benchmark_error(
            BenchmarkArtifactError,
            "loose_artifact_path_traversal",
            "standalone benchmark record path must not contain '..'.",
        )
    destination = (Path.cwd() / path).resolve(strict=False)
    try:
        destination.relative_to(Path.cwd().resolve())
    except ValueError:
        raise_benchmark_error(
            BenchmarkArtifactError,
            "loose_artifact_path_escape",
            "standalone benchmark record path must stay inside the repository.",
        )
    if destination.exists() and not overwrite:
        raise_benchmark_error(
            BenchmarkArtifactError,
            "loose_artifact_exists",
            "standalone benchmark record already exists.",
        )
    data = canonical_json_bytes(payload)
    checksum = sha256_bytes(data)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_name(f".{destination.name}.tmp")
    temp.write_bytes(data)
    temp.replace(destination)
    return checksum
