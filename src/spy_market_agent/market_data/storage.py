from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import cast

from spy_market_agent.market_data.acquisition import (
    AcquisitionRequest,
    CanonicalDailyBar,
    DatasetManifest,
    RawAcquisitionSnapshot,
)
from spy_market_agent.market_data.calendar import XNYSCalendar
from spy_market_agent.market_data.canonicalization import canonicalize_snapshot
from spy_market_agent.market_data.errors import (
    AtomicWriteFailure,
    ChecksumMismatch,
    ExistingDatasetConflict,
    ManifestValidationFailure,
    UnsafeDataPath,
)
from spy_market_agent.market_data.manifest import (
    canonical_bars_from_csv_bytes,
    canonical_content_checksum,
    canonical_csv_bytes,
    canonical_json_bytes,
    dataset_identity,
    load_manifest_bytes,
    load_raw_snapshot_bytes,
    manifest_json_bytes,
    sha256_bytes,
    source_checksum,
)


@dataclass(frozen=True)
class DatasetArtifactPaths:
    raw_snapshot_path: Path
    canonical_path: Path
    manifest_path: Path

    def as_tuple(self) -> tuple[Path, Path, Path]:
        return (self.raw_snapshot_path, self.canonical_path, self.manifest_path)


@dataclass(frozen=True)
class ArtifactWriteResult:
    path: Path
    created: bool


class DatasetStore:
    """Safe repository-local storage for ignored Phase 1 data artifacts."""

    def __init__(self, data_root: Path, *, repository_root: Path | None = None) -> None:
        self.repository_root = (repository_root or Path.cwd()).resolve()
        self.data_root = self._resolve_data_root(data_root)

    def artifact_paths(
        self, *, request: AcquisitionRequest, dataset_id: str
    ) -> DatasetArtifactPaths:
        components = (
            request.provider,
            request.symbol,
            request.timeframe,
            request.feed,
            request.adjustment_mode,
        )
        raw_dir = self._safe_child(self.data_root / "raw", *components)
        canonical_dir = self._safe_child(self.data_root / "canonical", *components)
        manifest_dir = self._safe_child(self.data_root / "manifests", *components)
        filename_base = dataset_id
        return DatasetArtifactPaths(
            raw_snapshot_path=raw_dir / f"{filename_base}.raw.json",
            canonical_path=canonical_dir / f"{filename_base}.canonical.csv",
            manifest_path=manifest_dir / f"{filename_base}.manifest.json",
        )

    def relative_path(self, path: Path) -> str:
        resolved = self._safe_existing_or_parent_path(path)
        return resolved.relative_to(self.repository_root).as_posix()

    def write_dataset(
        self,
        *,
        paths: DatasetArtifactPaths,
        raw_bytes: bytes,
        canonical_bytes: bytes,
        manifest_bytes: bytes,
        expected_raw_checksum: str,
        expected_canonical_checksum: str,
        expected_manifest_checksum: str,
    ) -> tuple[ArtifactWriteResult, ArtifactWriteResult, ArtifactWriteResult]:
        safe_paths = tuple(self._safe_existing_or_parent_path(path) for path in paths.as_tuple())
        existed_before = {path: path.exists() for path in safe_paths}
        write_results: list[ArtifactWriteResult] = []
        try:
            raw_result = self._write_atomic_if_needed(
                paths.raw_snapshot_path,
                raw_bytes,
                expected_checksum=expected_raw_checksum,
            )
            write_results.append(raw_result)
            canonical_result = self._write_atomic_if_needed(
                paths.canonical_path,
                canonical_bytes,
                expected_checksum=expected_canonical_checksum,
            )
            write_results.append(canonical_result)
            manifest_result = self._write_atomic_if_needed(
                paths.manifest_path,
                manifest_bytes,
                expected_checksum=expected_manifest_checksum,
            )
            write_results.append(manifest_result)
        except Exception:
            self._cleanup_new_artifacts(
                paths=safe_paths,
                existed_before=existed_before,
                write_results=tuple(write_results),
            )
            raise
        return raw_result, canonical_result, manifest_result

    def load_existing_manifest(self, path: Path) -> DatasetManifest:
        safe_path = self._safe_existing_or_parent_path(path)
        if not safe_path.exists():
            raise ChecksumMismatch(f"manifest does not exist: {self.relative_path(path)}")
        return load_manifest_bytes(safe_path.read_bytes())

    def verify_manifest_artifacts(self, manifest_path: Path) -> DatasetManifest:
        safe_manifest_path = self._safe_existing_or_parent_path(manifest_path)
        if not safe_manifest_path.exists():
            raise ChecksumMismatch("manifest artifact is missing.")
        manifest_bytes = safe_manifest_path.read_bytes()
        manifest = load_manifest_bytes(manifest_bytes)
        manifest_without_checksum = manifest.model_copy(update={"manifest_artifact_checksum": None})
        expected_manifest_checksum = sha256_bytes(manifest_json_bytes(manifest_without_checksum))
        if manifest.manifest_artifact_checksum != expected_manifest_checksum:
            raise ChecksumMismatch("manifest self-checksum does not match.")

        repository_root = self.repository_root
        recorded_manifest_path = self._safe_existing_or_parent_path(
            repository_root / manifest.generated_file_locations.manifest_path
        )
        if recorded_manifest_path != safe_manifest_path:
            raise ManifestValidationFailure("manifest path does not match generated location.")
        raw_path = self._safe_existing_or_parent_path(
            repository_root / manifest.generated_file_locations.raw_snapshot_path
        )
        canonical_path = self._safe_existing_or_parent_path(
            repository_root / manifest.generated_file_locations.canonical_path
        )
        _verify_dataset_filename(
            raw_path,
            dataset_id=manifest.dataset_id,
            suffix=".raw.json",
        )
        _verify_dataset_filename(
            canonical_path,
            dataset_id=manifest.dataset_id,
            suffix=".canonical.csv",
        )
        _verify_dataset_filename(
            safe_manifest_path,
            dataset_id=manifest.dataset_id,
            suffix=".manifest.json",
        )
        if not raw_path.exists() or not canonical_path.exists():
            raise ChecksumMismatch("raw or canonical artifact is missing.")
        raw_bytes = raw_path.read_bytes()
        canonical_bytes = canonical_path.read_bytes()
        if sha256_bytes(raw_bytes) != manifest.raw_artifact_checksum:
            raise ChecksumMismatch("raw artifact checksum does not match manifest.")
        if sha256_bytes(canonical_bytes) != manifest.artifact_checksum:
            raise ChecksumMismatch("canonical artifact checksum does not match manifest.")
        raw_snapshot = load_raw_snapshot_bytes(raw_bytes)
        canonical_bars = canonical_bars_from_csv_bytes(canonical_bytes)
        self._verify_semantic_integrity(
            manifest=manifest,
            raw_snapshot=raw_snapshot,
            canonical_bars=canonical_bars,
            raw_path=raw_path,
            canonical_path=canonical_path,
            manifest_path=safe_manifest_path,
        )
        return manifest

    def _resolve_data_root(self, data_root: Path) -> Path:
        if data_root.is_absolute():
            raise UnsafeDataPath("data_root must be repository-relative.")
        if any(part == ".." for part in data_root.parts):
            raise UnsafeDataPath("data_root must not contain '..'.")
        candidate = (self.repository_root / data_root).resolve(strict=False)
        self._validate_inside_repository(candidate)
        blocked = (
            self.repository_root / ".git",
            self.repository_root / "src",
            self.repository_root / "tests",
            self.repository_root / "docs",
            self.repository_root / "reviews",
        )
        for blocked_path in blocked:
            try:
                candidate.relative_to(blocked_path.resolve(strict=False))
            except ValueError:
                continue
            raise UnsafeDataPath("data_root must not point inside source, test, doc, or Git paths.")
        return candidate

    def _safe_child(self, base: Path, *components: str) -> Path:
        if any(not component or "/" in component or "\\" in component for component in components):
            raise UnsafeDataPath("artifact path component is unsafe.")
        candidate = base.joinpath(*components).resolve(strict=False)
        self._validate_inside_data_root(candidate)
        return candidate

    def _safe_existing_or_parent_path(self, path: Path) -> Path:
        resolved_parent = path.parent.resolve(strict=False)
        self._validate_inside_data_root(resolved_parent)
        resolved_path = resolved_parent / path.name
        if path.name.startswith(".") or path.name.endswith(".tmp"):
            raise UnsafeDataPath("artifact filename is unsafe.")
        return resolved_path

    def _write_atomic_if_needed(
        self,
        path: Path,
        payload: bytes,
        *,
        expected_checksum: str,
    ) -> ArtifactWriteResult:
        safe_path = self._safe_existing_or_parent_path(path)
        if safe_path.exists():
            if safe_path.is_symlink():
                raise UnsafeDataPath("artifact path must not be a symlink.")
            existing_checksum = sha256_bytes(safe_path.read_bytes())
            if existing_checksum == expected_checksum:
                return ArtifactWriteResult(path=safe_path, created=False)
            raise ExistingDatasetConflict(
                f"existing artifact has conflicting checksum: {self.relative_path(safe_path)}"
            )

        safe_path.parent.mkdir(parents=True, exist_ok=True)
        actual_checksum = sha256_bytes(payload)
        if actual_checksum != expected_checksum:
            raise ChecksumMismatch("generated artifact checksum did not match expectation.")

        temp_path: Path | None = None
        try:
            descriptor, temp_name = tempfile.mkstemp(
                prefix=f".{safe_path.name}.",
                suffix=".tmp",
                dir=safe_path.parent,
            )
            temp_path = Path(temp_name)
            with os.fdopen(descriptor, "wb") as file_handle:
                file_handle.write(payload)
                file_handle.flush()
                os.fsync(file_handle.fileno())
            if sha256_bytes(temp_path.read_bytes()) != expected_checksum:
                raise ChecksumMismatch("temporary artifact checksum did not match expectation.")
            temp_path.replace(safe_path)
            if sha256_bytes(safe_path.read_bytes()) != expected_checksum:
                raise ChecksumMismatch("final artifact checksum did not match expectation.")
        except (OSError, ChecksumMismatch) as exc:
            if temp_path is not None:
                _best_effort_unlink(temp_path)
            if isinstance(exc, ChecksumMismatch):
                raise
            raise AtomicWriteFailure(f"atomic write failed for {safe_path.name}: {exc}") from exc
        return ArtifactWriteResult(path=safe_path, created=True)

    def _cleanup_new_artifacts(
        self,
        *,
        paths: tuple[Path, ...],
        existed_before: dict[Path, bool],
        write_results: tuple[ArtifactWriteResult, ...],
    ) -> None:
        created_paths = {result.path for result in write_results if result.created}
        created_paths.update(path for path in paths if not existed_before[path] and path.exists())
        for path in created_paths:
            _best_effort_unlink(path)

    def _verify_semantic_integrity(
        self,
        *,
        manifest: DatasetManifest,
        raw_snapshot: object,
        canonical_bars: tuple[object, ...],
        raw_path: Path,
        canonical_path: Path,
        manifest_path: Path,
    ) -> None:
        if not isinstance(raw_snapshot, RawAcquisitionSnapshot):
            raise ManifestValidationFailure("raw snapshot did not load into the expected model.")
        request = _request_from_manifest(manifest)
        expected_sanitized_request = request.sanitized_parameters()
        if raw_snapshot.sanitized_request != expected_sanitized_request:
            raise ManifestValidationFailure("raw snapshot request does not match manifest.")
        if raw_snapshot.retrieval_timestamp != manifest.retrieval_timestamp:
            raise ManifestValidationFailure(
                "raw snapshot retrieval timestamp does not match manifest."
            )
        provider_identity = raw_snapshot.provider_identity
        if provider_identity.provider_name != manifest.provider:
            raise ManifestValidationFailure("raw provider name does not match manifest.")
        if provider_identity.api_version != manifest.provider_api_version:
            raise ManifestValidationFailure("raw provider API version does not match manifest.")
        if provider_identity.sdk_package_name != manifest.sdk_package_name:
            raise ManifestValidationFailure("raw SDK package name does not match manifest.")
        if provider_identity.sdk_package_version != manifest.sdk_package_version:
            raise ManifestValidationFailure("raw SDK package version does not match manifest.")
        if provider_identity.feed != manifest.feed:
            raise ManifestValidationFailure("raw feed does not match manifest.")
        if provider_identity.adjustment_mode != manifest.adjustment_mode:
            raise ManifestValidationFailure("raw adjustment mode does not match manifest.")
        if source_checksum(raw_snapshot) != manifest.source_checksum:
            raise ChecksumMismatch("source checksum does not match reconstructed raw snapshot.")

        typed_bars = _require_canonical_bars(canonical_bars)
        for bar in typed_bars:
            if bar.symbol != manifest.symbol:
                raise ManifestValidationFailure("canonical symbol does not match manifest.")
            if bar.provider != manifest.provider:
                raise ManifestValidationFailure("canonical provider does not match manifest.")
            if bar.feed != manifest.feed:
                raise ManifestValidationFailure("canonical feed does not match manifest.")
            if bar.adjustment_mode != manifest.adjustment_mode:
                raise ManifestValidationFailure(
                    "canonical adjustment mode does not match manifest."
                )
        if canonical_csv_bytes(typed_bars) != canonical_path.read_bytes():
            raise ManifestValidationFailure("canonical CSV is not in deterministic form.")

        calendar = XNYSCalendar()
        recanonicalized = canonicalize_snapshot(
            request=request,
            snapshot=raw_snapshot,
            calendar=calendar,
            as_of=raw_snapshot.retrieval_timestamp,
        )
        if recanonicalized != typed_bars:
            raise ManifestValidationFailure("canonical artifact does not match raw snapshot.")

        canonical_checksum = canonical_content_checksum(
            bars=typed_bars,
            provider=manifest.provider,
            feed=manifest.feed,
            timeframe=manifest.timeframe,
            adjustment_mode=manifest.adjustment_mode,
            corporate_action_policy=manifest.corporate_action_policy,
        )
        if canonical_checksum != manifest.canonical_content_checksum:
            raise ChecksumMismatch("canonical content checksum does not match reconstructed data.")
        reconstructed_dataset_id = dataset_identity(
            request=request,
            canonical_checksum=canonical_checksum,
            corporate_action_policy=manifest.corporate_action_policy,
        )
        if reconstructed_dataset_id != manifest.dataset_id:
            raise ManifestValidationFailure("dataset ID does not match reconstructed identity.")

        expected_lineage = f"lineage-{canonical_checksum[:24]}"
        if manifest.lineage_identifier != expected_lineage:
            raise ManifestValidationFailure("manifest lineage identifier does not match dataset.")
        if any(bar.lineage_identifier != expected_lineage for bar in typed_bars):
            raise ManifestValidationFailure("canonical lineage identifier does not match dataset.")

        observed_sessions = tuple(bar.session_date for bar in typed_bars)
        expected_sessions = calendar.sessions_between(
            manifest.requested_start_date,
            manifest.requested_end_date,
        )
        missing_sessions = tuple(
            session.isoformat() for session in expected_sessions if session not in observed_sessions
        )
        duplicate_count = len(observed_sessions) - len(set(observed_sessions))
        if manifest.row_count != len(typed_bars):
            raise ManifestValidationFailure("manifest row count does not match canonical rows.")
        if manifest.actual_first_session != observed_sessions[0]:
            raise ManifestValidationFailure("manifest first session does not match canonical rows.")
        if manifest.actual_last_session != observed_sessions[-1]:
            raise ManifestValidationFailure("manifest last session does not match canonical rows.")
        if manifest.expected_session_count != len(expected_sessions):
            raise ManifestValidationFailure("expected session count does not match calendar.")
        if manifest.missing_session_summary.count != len(missing_sessions):
            raise ManifestValidationFailure("missing-session count does not match calendar.")
        if manifest.missing_session_summary.sessions != missing_sessions:
            raise ManifestValidationFailure("missing-session list does not match calendar.")
        if manifest.duplicate_session_count != duplicate_count:
            raise ManifestValidationFailure(
                "duplicate-session count does not match canonical rows."
            )

        if manifest.generated_file_locations.raw_snapshot_path != self.relative_path(raw_path):
            raise ManifestValidationFailure("raw generated file location does not match path.")
        if manifest.generated_file_locations.canonical_path != self.relative_path(canonical_path):
            raise ManifestValidationFailure(
                "canonical generated file location does not match path."
            )
        if manifest.generated_file_locations.manifest_path != self.relative_path(manifest_path):
            raise ManifestValidationFailure("manifest generated file location does not match path.")

    def _validate_inside_repository(self, path: Path) -> None:
        try:
            path.relative_to(self.repository_root)
        except ValueError as exc:
            raise UnsafeDataPath("path must remain inside the repository root.") from exc

    def _validate_inside_data_root(self, path: Path) -> None:
        self._validate_inside_repository(path)
        try:
            path.relative_to(self.data_root)
        except ValueError as exc:
            raise UnsafeDataPath("artifact path must remain inside data_root.") from exc


def raw_snapshot_json_bytes(snapshot: object) -> bytes:
    return canonical_json_bytes(snapshot)


def manifest_bytes_with_checksum(manifest: DatasetManifest) -> tuple[bytes, str]:
    payload = canonical_json_bytes(manifest)
    return payload, sha256_bytes(payload)


def _verify_dataset_filename(path: Path, *, dataset_id: str, suffix: str) -> None:
    expected_name = f"{dataset_id}{suffix}"
    if path.name != expected_name:
        raise ManifestValidationFailure("artifact filename does not match dataset ID.")


def _request_from_manifest(manifest: DatasetManifest) -> AcquisitionRequest:
    asof_value = manifest.relevant_configuration.get("asof")
    asof = None
    if isinstance(asof_value, str) and asof_value:
        asof = date.fromisoformat(asof_value)
    data_root_value = manifest.relevant_configuration.get("data_root")
    data_root = Path(data_root_value) if isinstance(data_root_value, str) else Path("data")
    return AcquisitionRequest.model_construct(
        symbol=manifest.symbol,
        start_date=manifest.requested_start_date,
        end_date=manifest.requested_end_date,
        timeframe=manifest.timeframe,
        provider=manifest.provider,
        feed=manifest.feed,
        adjustment_mode=manifest.adjustment_mode,
        data_root=data_root,
        acknowledge_provider_terms=True,
        asof=asof,
    )


def _require_canonical_bars(values: tuple[object, ...]) -> tuple[CanonicalDailyBar, ...]:
    if not values:
        raise ManifestValidationFailure("canonical artifact contains no bars.")
    for value in values:
        if not isinstance(value, CanonicalDailyBar):
            raise ManifestValidationFailure("canonical artifact did not load typed bars.")
    return cast(tuple[CanonicalDailyBar, ...], values)


def _best_effort_unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        return
