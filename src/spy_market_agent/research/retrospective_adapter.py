"""AQ-P3C-R minimal verified-data adapter (RIC-183). Offline; owner-gated; no acquisition.

Two entry points, each usable at most once per process:

* :func:`eligibility_preflight` verifies metadata and artifacts and returns a sanitized
  report. It never fits a model.
* :func:`run_authorized_experiment` additionally requires an owner-authored authorization
  file bound to the exact report digest, commit and destination, atomically consumes a
  single-use execution marker, evaluates once and persists sanitized, immutable output.

Before the single-use marker can be reserved, both entry points verify the exact bytes of the
approved economic specification and policy lock (read-only; digests are bound to the approval
report but not to the logical single-use key) and re-check them immediately before reserving.

Nothing here acquires data, reads credentials, contacts a network or broker, or writes
outside the approved ignored output root. Existing verification (``verify_context_artifacts``,
``_verify_parent_campaign``, ``_load_verified_research_market_data``, the Phase 2 isolation
function, ``bind_context_series`` and the evaluator) is reused unchanged.

Trust boundaries (honest limits): the owner rights reference is an owner attestation that
this code cannot verify; the authorization and execution marker are private same-user files,
not cryptographic authorization; the runtime fingerprint is a source/dependency-version
comparison against a baseline taken when this module was imported, not cryptographic
loaded-code attestation. None of this defends against an adversary operating under the same
OS account, which could rewrite any of these files or the interpreter.

Requirements on the calling environment: a fresh ``python -I`` process with NO ``GIT_*``
environment variables (they are rejected at both entry points because the existing context
verifiers invoke git with the ambient environment). An interrupted or failed execution is
never retried, cleaned up or staged automatically: its marker and any partial output
directory remain and recovery is a manual, separately owner-authorized action (accepted
limitation).
"""

from __future__ import annotations

import contextlib
import json
import os
import platform
import re
import stat
import subprocess
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from spy_market_agent.benchmark.artifacts import canonical_json_bytes, sha256_bytes, sha256_json
from spy_market_agent.market_data.calendar import XNYSCalendar
from spy_market_agent.market_data.checksum import compute_market_data_checksum
from spy_market_agent.market_data.context_acquisition import verify_context_artifacts
from spy_market_agent.market_data.context_contracts import (
    CONTEXT_ACQUISITION_SCHEMA,
    DATASET_PATTERN,
    ContextAcquisitionManifest,
)
from spy_market_agent.market_data.context_storage import ContextStore
from spy_market_agent.market_data.errors import MarketDataAcquisitionError
from spy_market_agent.research import retrospective_study as study
from spy_market_agent.research.campaign import ResearchCampaignConfig
from spy_market_agent.research.phase2_isolation import apply_phase2_final_test_session_isolation
from spy_market_agent.research.phase3b import (
    Phase3BConfig,
    _verify_parent_campaign,
    load_phase3b_config,
)
from spy_market_agent.research.retrospective_context import (
    RetrospectiveContextSeries,
    bind_context_series,
)
from spy_market_agent.research.runner import _load_verified_research_market_data

ADAPTER_ID: Final = "aq-p3cr-verified-data-adapter-v1"
AUTHORIZATION_SCHEMA: Final = "aq-p3cr-experiment-authorization-v1"
MARKER_SCHEMA: Final = "aq-p3cr-execution-marker-v1"
APPROVED_START: Final = date(2018, 1, 2)
APPROVED_END: Final = date(2024, 10, 15)
APPROVED_SESSIONS: Final = 1708
OUTPUT_ROOTS: Final = {
    "retrospective_corrected": "artifacts/research/phase3cr",
    "synthetic_fixture": "artifacts/research/phase3cr_synthetic",
}
NAMESPACES: Final = {
    "retrospective_corrected": study.RETROSPECTIVE_NAMESPACE,
    "synthetic_fixture": study.SYNTHETIC_NAMESPACE,
}
EXECUTIONS_DIRECTORY: Final = "executions"
DEFAULT_PHASE3B_CONFIG: Final = "configs/research/phase3b_nonoverlapping.json"
MAX_AUTHORIZATION_BYTES: Final = 64 * 1024
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_AUTH_ID = re.compile(r"[A-Za-z0-9._:-]{8,128}\Z")
_EXPERIMENT_ID = re.compile(r"aq-p3cr-(retro|synth)-[0-9a-f]{64}\Z")
FORBIDDEN_OUTPUT_KEYS: Final = frozenset(
    {
        "probabilities",
        "row_probabilities",
        "predicted_probabilities",
        "feature_values",
        "features",
        "prices",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "estimator",
        "fitted_model",
        "api_key",
        "secret_key",
        "secret",
        "token",
    }
)
_PROCESS: dict[str, bool] = {"preflight": False, "run": False}


class AdapterError(ValueError):
    """Fixed-text, value-free rejection."""


def _reject(code: str) -> AdapterError:
    return AdapterError(code)


@dataclass(frozen=True, slots=True)
class FrozenParent:
    """Accepted parent evidence. Fixture instances may never equal the real one."""

    dataset_id: str
    canonical_checksum: str
    market_data_checksum: str
    manifest_self_checksum: str
    campaign_id: str
    campaign_index_sha256: str
    campaign_manifest_sha256: str


REAL_PARENT: Final = FrozenParent(
    dataset_id=study.PARENT_DATASET_ID,
    canonical_checksum=study.PARENT_CANONICAL_CHECKSUM,
    market_data_checksum="09371762ecab4ee32dad6896b454d693d0e654f8f565ec021557a5df0bbad6c2",
    manifest_self_checksum="de296295cd4b491848d7c0abe0470c3c74e81123491b92efbd006fa458fee6b6",
    campaign_id=study.PARENT_CAMPAIGN_ID,
    # Locally verified digests of the accepted campaign's artifact_index.json and
    # experiment_manifest.json; a self-consistently resealed campaign cannot match them.
    campaign_index_sha256="e1934f41878f26d680ec2ec3a76744b6d7c9431e4eda6d02ac54ec5781c5f60f",
    campaign_manifest_sha256="bdab6f063aa0ab56f6c72b9d29c1257ed72398355de6029ad03b3bf40193977f",
)


@dataclass(frozen=True, slots=True)
class RuntimeAttestation:
    isolated: bool
    git_commit: str
    clean: bool


@dataclass(frozen=True, slots=True)
class PreflightReport:
    """Identities, checksums and dimensions only; no observations of any kind."""

    evidence_kind: str
    git_commit: str
    python_version: str
    runtime_fingerprint: str
    parent_dataset_id: str
    parent_canonical_checksum: str
    parent_market_data_checksum: str
    parent_manifest_self_checksum: str
    campaign_id: str
    campaign_index_sha256: str
    campaign_manifest_sha256: str
    research_slice_checksum: str
    input_snapshot_sha256: str
    source_rows: int
    source_first_session: str
    source_last_session: str
    prediction_rows: int
    prediction_first_session: str
    prediction_last_session: str
    excluded_protected_source_rows: int
    context: tuple[tuple[str, str, str, str], ...]  # symbol, dataset, canonical, manifest sha
    # Exact bytes of the approved economic specification and policy lock. They are part of the
    # approval digest but deliberately NOT of ``input_digest`` (the logical single-use key).
    economic_specification_sha256: str
    economic_policy_lock_sha256: str
    fit_performed: bool = False
    execution_authority: str = "none"
    disposition: str = "NO CANDIDATE PROMOTION"

    @property
    def digest(self) -> str:
        """Binds inputs, commit and runtime identity: the approval granularity."""
        return sha256_json(_plain(self))

    @property
    def input_digest(self) -> str:
        """Inputs only (no commit/runtime): the single-use execution granularity."""
        return sha256_json(
            {
                "study_id": study.STUDY_ID,
                "evidence_kind": self.evidence_kind,
                "parent": [
                    self.parent_dataset_id,
                    self.parent_canonical_checksum,
                    self.parent_market_data_checksum,
                    self.parent_manifest_self_checksum,
                ],
                "campaign": [
                    self.campaign_id,
                    self.campaign_index_sha256,
                    self.campaign_manifest_sha256,
                    self.research_slice_checksum,
                ],
                "context": [list(item) for item in self.context],
                "input_snapshot_sha256": self.input_snapshot_sha256,
            }
        )


def _plain(report: PreflightReport) -> dict[str, Any]:
    return {
        name: getattr(report, name)
        for name in PreflightReport.__dataclass_fields__
        if name != "context"
    } | {"context": [list(item) for item in report.context]}


class ExperimentAuthorization(BaseModel):
    """Owner-authored, input-bound authorization. A Boolean can never authorize a run."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_version: Literal["aq-p3cr-experiment-authorization-v1"]
    authorization_id: str
    study_id: Literal["aq-p3cr-retrospective-study-v1"]
    evidence_kind: Literal["retrospective_corrected", "synthetic_fixture"]
    git_commit: str
    report_digest: str
    owner_rights_reference_id: str
    output_destination: str
    execution_authority: Literal["none"]
    disposition: Literal["NO CANDIDATE PROMOTION"]


@dataclass(frozen=True, slots=True)
class ExperimentRecord:
    experiment_id: str
    output_directory: Path
    evidence_kind: str
    artifact_index_sha256: str


@dataclass(frozen=True, slots=True)
class _Verified:
    report: PreflightReport
    manifest: Any
    market_data: Any
    boundary: dict[str, Any]
    qqq: RetrospectiveContextSeries
    iwm: RetrospectiveContextSeries
    frozen: FrozenParent
    files: tuple[Path, ...]
    config_file: Path
    snapshot: dict[str, str]


# ---------------------------------------------------------------------------
# Git (never inherits caller repository selection) and runtime prerequisites
# ---------------------------------------------------------------------------


def _require_clean_git_environment() -> None:
    """Delegated verifiers (context storage/lineage) call git with the ambient environment.

    They are not modified here, so the offline one-shot contract requires that no GIT_*
    variable (repository, work tree, index, config selection) is present at all.
    """
    if any(name.startswith("GIT_") for name in os.environ):
        raise _reject("ambient_git_environment_not_permitted")


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    environment = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    # core.fsmonitor is forced off so a configured hook can never execute during a probe.
    return subprocess.run(
        ["git", "-c", "core.fsmonitor=false", *args],
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def _code_repository() -> Path:
    return Path(__file__).resolve().parents[3]


def _collect_runtime_attestation(code_repository: Path | None = None) -> RuntimeAttestation:
    repository = code_repository or _code_repository()
    try:
        head = _git(["rev-parse", "HEAD"], repository)
        status = _git(["status", "--porcelain", "--untracked-files=all"], repository)
    except OSError:
        raise _reject("runtime_attestation_unavailable") from None
    if head.returncode != 0 or status.returncode != 0:
        raise _reject("runtime_attestation_unavailable")
    return RuntimeAttestation(
        isolated=bool(sys.flags.isolated),
        git_commit=head.stdout.strip(),
        clean=not status.stdout.strip(),
    )


_REAL_COLLECT = _collect_runtime_attestation  # stable handle for tests that replace the probe


def _require_runtime(attestation: RuntimeAttestation, pinned_commit: str) -> None:
    if not isinstance(pinned_commit, str) or not _COMMIT.fullmatch(pinned_commit):
        raise _reject("invalid_pinned_commit")
    if not attestation.isolated:
        raise _reject("process_not_isolated")
    if not attestation.clean:
        raise _reject("checkout_not_clean")
    if attestation.git_commit != pinned_commit:
        raise _reject("checkout_not_at_pinned_commit")


def _claim_process(entry: str) -> None:
    if _PROCESS[entry]:
        raise _reject("process_not_fresh")
    _PROCESS[entry] = True


def _runtime_fingerprint() -> str:
    """Fresh (uncached) fingerprint of study+adapter sources, dependency versions and Python."""
    roots = (str(Path(study.__file__).resolve()), str(Path(__file__).resolve()))
    checksums, versions = study._closure(roots)
    return sha256_json(
        {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "checksums": checksums,
            "versions": versions,
        }
    )


# Baseline captured when this module is imported (before any entry point can run).
_IMPORT_BASELINE: str = _runtime_fingerprint()


def _require_runtime_unchanged() -> str:
    if _runtime_fingerprint() != _IMPORT_BASELINE or study._transitive_lineage() != study._closure(
        (str(Path(study.__file__).resolve()),)
    ):
        raise _reject("source_or_dependencies_changed_since_import")
    return _IMPORT_BASELINE


# ---------------------------------------------------------------------------
# Filesystem safety
# ---------------------------------------------------------------------------


def _require_private(path: Path, root: Path) -> None:
    """Every component must be a non-symlink, owned by this user, not group/other writable."""
    try:
        relative = path.relative_to(root)
    except ValueError:
        raise _reject("input_outside_repository") from None
    current = root
    for part in relative.parts:
        current = current / part
        try:
            info = os.lstat(current)
        except OSError:
            raise _reject("input_missing") from None
        if stat.S_ISLNK(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
            raise _reject("input_not_privately_owned")


def _require_ignored_untracked(
    root: Path, relatives: list[Path], *, tree: Path | None = None
) -> None:
    """Every path must be ignored and untracked; ``tree`` must have no tracked descendants.

    "Untracked" requires a successful ``ls-files`` query with an empty listing, so an
    unreadable or corrupt index fails closed instead of reading as untracked.
    """
    for relative in relatives:
        ignored = _git(["check-ignore", "--no-index", "-q", "--", relative.as_posix()], root)
        listing = _git(["ls-files", "--", relative.as_posix()], root)
        if ignored.returncode != 0 or listing.returncode != 0 or listing.stdout.strip():
            raise _reject("path_must_be_ignored_and_untracked")
    if tree is not None:
        listing = _git(["ls-files", "--", tree.as_posix()], root)
        if listing.returncode != 0 or listing.stdout.strip():
            raise _reject("path_must_be_ignored_and_untracked")


def _require_ignored_directory(root: Path, directory: Path) -> None:
    """The (existing) directory itself must be ignored; git cannot re-include its files.

    The path is given without a trailing slash: an existing directory is recognised as one,
    whereas ``dir/`` also matches a ``dir/*`` rule that ignores only the directory contents.
    """
    ignored = _git(["check-ignore", "--no-index", "-q", "--", directory.as_posix()], root)
    if ignored.returncode != 0:
        raise _reject("path_must_be_ignored_and_untracked")


def _fsync_directory(descriptor: int) -> None:
    try:
        os.fsync(descriptor)
    except OSError:
        raise _reject("durability_not_established") from None


def _check_directory(descriptor: int) -> None:
    info = os.fstat(descriptor)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
        raise _reject("output_ancestry_unsafe")


def _open_directory_chain(root: Path, parts: tuple[str, ...], *, create: bool) -> int:
    """Descriptor-relative, no-follow walk; missing components are created exclusively 0700."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        descriptor = os.open(root, flags)
    except OSError:
        raise _reject("output_ancestry_unsafe") from None
    try:
        _check_directory(descriptor)
        for part in parts:
            if create:
                with contextlib.suppress(FileExistsError):
                    os.mkdir(part, 0o700, dir_fd=descriptor)
                # Sync the parent for newly created AND reused entries: a name left behind
                # by an earlier, failed sync is not durable merely because it exists now.
                _fsync_directory(descriptor)
            following = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = following
            _check_directory(descriptor)
        return descriptor
    except OSError:
        os.close(descriptor)
        raise _reject("output_ancestry_unsafe") from None
    except BaseException:
        os.close(descriptor)
        raise


def _check_existing_chain(root: Path, parts: tuple[str, ...]) -> None:
    """Reject unsafe components that already exist; stop quietly at the first missing one."""
    current = root
    for part in parts:
        current = current / part
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            return
        if (
            stat.S_ISLNK(info.st_mode)
            or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_mode & 0o022
        ):
            raise _reject("output_ancestry_unsafe")


def _write_exclusive(directory: int, name: str, data: bytes) -> None:
    descriptor = os.open(
        name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory
    )
    try:
        view = memoryview(data)
        while view:
            view = view[os.write(descriptor, view) :]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


# ---------------------------------------------------------------------------
# Metadata gate (manifests only; no raw or canonical observations are read)
# ---------------------------------------------------------------------------


def _manifest_relative(root: Path, path: Path, symbol: str) -> Path:
    """Exact v2 manifest path shape, validated BEFORE any file is opened."""
    candidate = Path(path)
    if candidate.is_absolute():
        try:
            candidate = candidate.relative_to(root)
        except ValueError:
            raise _reject("context_manifest_path_invalid") from None
    parts = candidate.parts
    if (
        len(parts) != 8
        or ".." in parts
        or parts[:7] != ("data", "manifests", "alpaca", symbol, "1Day", "sip", "all")
        or not parts[7].endswith(".manifest.json")
        or not DATASET_PATTERN.fullmatch(parts[7].removesuffix(".manifest.json"))
    ):
        raise _reject("context_manifest_path_invalid")
    return candidate


def _metadata_gate(
    store: ContextStore, qqq_path: Path, iwm_path: Path
) -> dict[str, tuple[ContextAcquisitionManifest, bytes, Path]]:
    expected_sessions = len(XNYSCalendar().sessions_between(APPROVED_START, APPROVED_END))
    if expected_sessions != APPROVED_SESSIONS:
        raise _reject("approved_scope_session_count_mismatch")
    # Both paths are validated structurally before either is read.
    relatives = {
        symbol: _manifest_relative(store.root, path, symbol)
        for symbol, path in (("QQQ", qqq_path), ("IWM", iwm_path))
    }
    if relatives["QQQ"] == relatives["IWM"]:
        raise _reject("duplicate_context_dataset")
    found: dict[str, tuple[ContextAcquisitionManifest, bytes, Path]] = {}
    for symbol, relative in relatives.items():
        try:
            with store.locked(create=False):
                data = store.read(relative)
            manifest = ContextAcquisitionManifest.model_validate_json(data)
        except (MarketDataAcquisitionError, ValidationError, OSError, ValueError):
            raise _reject("context_manifest_unreadable_or_not_v2") from None
        if (
            manifest.schema_version != CONTEXT_ACQUISITION_SCHEMA
            or manifest.symbol != symbol
            or manifest.provider != "alpaca"
            or manifest.feed != "sip"
            or manifest.timeframe != "1Day"
            or manifest.adjustment_mode != "all"
            or manifest.execution_authority != "none"
            or manifest.point_in_time_status != "UNVERIFIED"
            or manifest.usage_rights_status != "UNVERIFIED"
        ):
            raise _reject("context_source_policy_mismatch")
        if (
            manifest.start_date != APPROVED_START
            or manifest.end_date != APPROVED_END
            or manifest.first_session != APPROVED_START
            or manifest.last_session != APPROVED_END
            or manifest.row_count != APPROVED_SESSIONS
        ):
            raise _reject("context_scope_differs_from_approved_range")
        if data != canonical_json_bytes(manifest) or manifest.manifest_sha256 != sha256_bytes(
            canonical_json_bytes(manifest.model_copy(update={"manifest_sha256": None}))
        ):
            raise _reject("context_manifest_checksum_mismatch")
        if relative != store.paths(symbol, manifest.dataset_id)[2]:
            raise _reject("context_manifest_path_identity_mismatch")
        found[symbol] = (manifest, data, relative)
    if found["QQQ"][0].dataset_id == found["IWM"][0].dataset_id:
        raise _reject("duplicate_context_dataset")
    return found


# ---------------------------------------------------------------------------
# Verified inputs
# ---------------------------------------------------------------------------


def _phase3b_config(config_path: Path, frozen: FrozenParent) -> Phase3BConfig:
    try:
        config = load_phase3b_config(config_path)
    except Exception:
        raise _reject("phase3b_config_invalid") from None
    if frozen == REAL_PARENT:
        return config
    # Fixture identities only: model_copy performs no validation, by design.
    return config.model_copy(
        update={
            "parent_dataset_id": frozen.dataset_id,
            "parent_canonical_checksum": frozen.canonical_checksum,
            "parent_campaign_id": frozen.campaign_id,
        }
    )


def _consumed_files(
    root: Path, store: ContextStore, frozen: FrozenParent, gate: dict[str, Any]
) -> tuple[Path, ...]:
    """The COMPLETE set of artifacts any verifier or consumer reads."""
    parent = [
        root / "data" / kind / "alpaca/SPY/1Day/sip/all" / f"{frozen.dataset_id}.{suffix}"
        for kind, suffix in (
            ("manifests", "manifest.json"),
            ("canonical", "canonical.csv"),
            ("raw", "raw.json"),
        )
    ]
    campaign_dir = root / "artifacts/research" / frozen.campaign_id
    try:
        campaign = sorted(campaign_dir.iterdir())
    except OSError:
        raise _reject("input_missing") from None
    context = [
        root / relative
        for symbol, (manifest, _data, _path) in gate.items()
        for relative in store.paths(symbol, manifest.dataset_id)
    ]
    return (*parent, *campaign, *context)


def _snapshot(root: Path, files: tuple[Path, ...], config_file: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in files:
        _require_private(path, root)
        info = os.lstat(path)
        if not stat.S_ISREG(info.st_mode):
            raise _reject("input_not_regular_file")
        result[path.relative_to(root).as_posix()] = sha256_bytes(path.read_bytes())
    # The Phase 3B configuration used by the verifier is part of the verified input set.
    try:
        info = os.lstat(config_file)
        if not stat.S_ISREG(info.st_mode):
            raise _reject("phase3b_config_invalid")
        result["phase3b_config"] = sha256_bytes(config_file.read_bytes())
    except OSError:
        raise _reject("phase3b_config_invalid") from None
    return result


def _verify_economic_policy() -> tuple[str, str]:
    """Read-only exact-byte verification of the approved economic specification and lock.

    Runs during preflight and BEFORE the single-use marker is reserved; the evaluator repeats
    the same verification as defense in depth. Returns (specification, lock) SHA-256 digests.
    """
    try:
        policy = study._bound_economic_policy(_code_repository())
    except study.RetrospectiveStudyError as error:
        raise _reject(str(error)) from None
    return policy.specification_sha256, policy.lock_sha256


def _require_policy_unchanged(report: PreflightReport) -> None:
    """The bytes verified at preflight must still be the bytes the report was approved for."""
    if _verify_economic_policy() != (
        report.economic_specification_sha256,
        report.economic_policy_lock_sha256,
    ):
        raise _reject("economic_policy_changed_after_verification")


def _verify_inputs(
    *,
    root: Path,
    qqq_manifest: Path,
    iwm_manifest: Path,
    config_path: Path | None,
    fixture_parent: FrozenParent | None,
    attestation: RuntimeAttestation,
    runtime_fingerprint: str,
) -> _Verified:
    frozen = fixture_parent or REAL_PARENT
    if fixture_parent is None and config_path is not None:
        # Real input uses only the verified default inside the pinned, clean checkout.
        raise _reject("phase3b_config_override_not_permitted_for_real_input")
    config_file = config_path or _code_repository() / DEFAULT_PHASE3B_CONFIG
    if fixture_parent is not None and (
        fixture_parent.dataset_id == REAL_PARENT.dataset_id
        or fixture_parent.canonical_checksum == REAL_PARENT.canonical_checksum
        or fixture_parent.campaign_id == REAL_PARENT.campaign_id
        or fixture_parent.campaign_index_sha256 == REAL_PARENT.campaign_index_sha256
        or fixture_parent.campaign_manifest_sha256 == REAL_PARENT.campaign_manifest_sha256
    ):
        raise _reject("fixture_parent_must_differ_from_accepted_parent")
    evidence_kind = "retrospective_corrected" if fixture_parent is None else "synthetic_fixture"
    specification_sha256, policy_lock_sha256 = _verify_economic_policy()  # before any read
    root = root.resolve(strict=True)
    store = ContextStore(root)
    gate = _metadata_gate(store, qqq_manifest, iwm_manifest)  # BEFORE any observation read

    files = _consumed_files(root, store, frozen, gate)
    before = _snapshot(
        root, files, config_file
    )  # privacy of the complete set + exact bytes to be verified
    campaign_prefix = f"artifacts/research/{frozen.campaign_id}/"
    if (
        before.get(campaign_prefix + "artifact_index.json") != frozen.campaign_index_sha256
        or before.get(campaign_prefix + "experiment_manifest.json")
        != frozen.campaign_manifest_sha256
    ):
        raise _reject("campaign_digest_not_pinned")

    parent_manifest_path = (
        root / "data/manifests/alpaca/SPY/1Day/sip/all" / f"{frozen.dataset_id}.manifest.json"
    )
    config = _phase3b_config(config_file, frozen)
    try:
        campaign_manifest = _verify_parent_campaign(root / "artifacts/research", root, config)
    except Exception:
        raise _reject("parent_campaign_verification_failed") from None
    try:
        manifest, parent = _load_verified_research_market_data(
            manifest_path=parent_manifest_path,
            data_root=Path("data"),
            repository_root=root,
            config=ResearchCampaignConfig(),
        )
    except Exception:
        raise _reject("parent_artifact_verification_failed") from None
    recomputed = compute_market_data_checksum(parent.data)
    if (
        manifest.dataset_id != frozen.dataset_id
        or manifest.canonical_content_checksum != frozen.canonical_checksum
        or manifest.manifest_artifact_checksum != frozen.manifest_self_checksum
        or campaign_manifest.get("phase1_manifest_artifact_checksum")
        != frozen.manifest_self_checksum
        or recomputed != frozen.market_data_checksum
        or campaign_manifest.get("parent_canonical_market_data_checksum") != recomputed
        or parent.metadata.dataset_checksum != recomputed
    ):
        raise _reject("parent_identity_or_checksum_mismatch")
    accepted_boundary = campaign_manifest.get("phase2_final_test_exclusion_boundary")
    sliced, boundary = apply_phase2_final_test_session_isolation(
        manifest=manifest, market_data=parent, global_feature_warmup_rows=60
    )
    if not isinstance(accepted_boundary, dict) or accepted_boundary != boundary.model_dump(
        mode="json"
    ):
        raise _reject("exclusion_boundary_differs_from_accepted_campaign")
    sessions = tuple(sliced.data["session"].to_list())
    if (
        len(sessions) != APPROVED_SESSIONS
        or (sessions[0], sessions[-1]) != (APPROVED_START, APPROVED_END)
        or boundary.eligible_development_prediction_row_count != study.EXPECTED_PREDICTION_ROWS
    ):
        raise _reject("development_population_mismatch")

    series: dict[str, RetrospectiveContextSeries] = {}
    for symbol, (gated, gated_bytes, manifest_relative) in gate.items():
        try:
            verified = verify_context_artifacts(root / manifest_relative, repository_root=root)
        except (MarketDataAcquisitionError, ValueError, OSError):
            raise _reject("context_artifact_verification_failed") from None
        canonical_relative = store.paths(symbol, verified.dataset_id)[1]
        with store.locked(create=False):
            manifest_again = store.read(manifest_relative)
            canonical_bytes = store.read(canonical_relative)
        # Consume exactly what was gated and verified, or refuse (substitution defence).
        if (
            verified != gated
            or manifest_again != gated_bytes
            or sha256_bytes(canonical_bytes) != verified.canonical_sha256
        ):
            raise _reject("context_artifact_changed_between_verification_and_use")
        series[symbol] = bind_context_series(verified, canonical_bytes)
        if series[symbol].sessions != sessions:
            raise _reject("context_sessions_differ_from_eligible_source")

    if _snapshot(root, files, config_file) != before:
        raise _reject("inputs_changed_during_verification")
    report = PreflightReport(
        evidence_kind=evidence_kind,
        git_commit=attestation.git_commit,
        python_version=platform.python_version(),
        runtime_fingerprint=runtime_fingerprint,
        parent_dataset_id=manifest.dataset_id,
        parent_canonical_checksum=manifest.canonical_content_checksum,
        parent_market_data_checksum=recomputed,
        parent_manifest_self_checksum=str(manifest.manifest_artifact_checksum),
        campaign_id=frozen.campaign_id,
        campaign_index_sha256=before[campaign_prefix + "artifact_index.json"],
        campaign_manifest_sha256=before[campaign_prefix + "experiment_manifest.json"],
        research_slice_checksum=boundary.research_slice_checksum,
        input_snapshot_sha256=sha256_json(before),
        source_rows=len(sessions),
        source_first_session=sessions[0].isoformat(),
        source_last_session=sessions[-1].isoformat(),
        prediction_rows=boundary.eligible_development_prediction_row_count,
        prediction_first_session=boundary.eligible_development_first_prediction_session.isoformat(),
        prediction_last_session=boundary.eligible_development_last_prediction_session.isoformat(),
        excluded_protected_source_rows=boundary.excluded_source_session_count,
        context=tuple(
            (s, series[s].dataset_id, series[s].canonical_sha256, series[s].manifest_sha256)
            for s in ("QQQ", "IWM")
        ),
        economic_specification_sha256=specification_sha256,
        economic_policy_lock_sha256=policy_lock_sha256,
    )
    return _Verified(
        report,
        manifest,
        parent,
        accepted_boundary,
        series["QQQ"],
        series["IWM"],
        frozen,
        files,
        config_file,
        before,
    )


def _recheck(root: Path, verified: _Verified, attestation: RuntimeAttestation, fp: str) -> None:
    """Checkout, runtime identity and every consumed artifact must be unchanged."""
    if (
        _collect_runtime_attestation() != attestation
        or _require_runtime_unchanged() != fp
        or _snapshot(root.resolve(strict=True), verified.files, verified.config_file)
        != verified.snapshot
    ):
        raise _reject("runtime_or_inputs_changed_during_evaluation")
    _require_policy_unchanged(verified.report)


def eligibility_preflight(
    *,
    repository_root: Path,
    qqq_manifest: Path,
    iwm_manifest: Path,
    pinned_commit: str,
    phase3b_config_path: Path | None = None,
    _fixture_parent: FrozenParent | None = None,
) -> PreflightReport:
    """Verify everything and report identities. Never fits a model."""
    _require_clean_git_environment()
    _claim_process("preflight")
    attestation = _collect_runtime_attestation()
    _require_runtime(attestation, pinned_commit)
    fingerprint = _require_runtime_unchanged()
    verified = _verify_inputs(
        root=repository_root,
        qqq_manifest=qqq_manifest,
        iwm_manifest=iwm_manifest,
        config_path=phase3b_config_path,
        fixture_parent=_fixture_parent,
        attestation=attestation,
        runtime_fingerprint=fingerprint,
    )
    _recheck(repository_root, verified, attestation, fingerprint)
    return verified.report


# ---------------------------------------------------------------------------
# Authorization, single-use execution marker and output
# ---------------------------------------------------------------------------


def _load_authorization(root: Path, path: Path) -> ExperimentAuthorization:
    candidate = path if path.is_absolute() else root / path
    if ".." in candidate.parts:
        raise _reject("input_outside_repository")
    resolved = candidate.absolute()  # never follow a final symlink
    _require_private(resolved, root)
    info = os.lstat(resolved)
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
        raise _reject("authorization_file_must_be_private_regular_file")
    if info.st_size > MAX_AUTHORIZATION_BYTES:
        raise _reject("authorization_file_too_large")
    _require_ignored_untracked(root, [resolved.relative_to(root)])
    try:
        auth = ExperimentAuthorization.model_validate_json(resolved.read_bytes())
    except (ValidationError, ValueError):
        raise _reject("authorization_invalid") from None
    if (
        not _AUTH_ID.fullmatch(auth.authorization_id)
        or not _COMMIT.fullmatch(auth.git_commit)
        or not _DIGEST.fullmatch(auth.report_digest)
        or not _AUTH_ID.fullmatch(auth.owner_rights_reference_id)
    ):
        raise _reject("authorization_invalid")
    return auth


def _bind_authorization(
    auth: ExperimentAuthorization, report: PreflightReport, attestation: RuntimeAttestation
) -> None:
    if (
        auth.report_digest != report.digest
        or auth.git_commit != attestation.git_commit
        or auth.evidence_kind != report.evidence_kind
        or auth.output_destination != OUTPUT_ROOTS[report.evidence_kind]
    ):
        raise _reject("authorization_does_not_match_verified_inputs")


_ARTIFACT_NAMES: Final = (
    "limitations.md",
    "configuration.json",
    "lineage.json",
    "metrics.json",
    "findings.json",
    "authorization_record.json",
    "artifact_index.json",
)


def _execution_key(report: PreflightReport) -> str:
    return sha256_json(
        {
            "study_id": study.STUDY_ID,
            "evidence_kind": report.evidence_kind,
            "input_digest": report.input_digest,
        }
    )


def _prepare_destination(root: Path, report: PreflightReport) -> None:
    """Validate the destination BEFORE the evaluator can run; creates only owner-only dirs."""
    base = Path(OUTPUT_ROOTS[report.evidence_kind])
    probe = base / f"{NAMESPACES[report.evidence_kind]}{'0' * 64}"
    marker = base / EXECUTIONS_DIRECTORY / f"{_execution_key(report)}.json"
    chain = (*base.parts, EXECUTIONS_DIRECTORY)
    _check_existing_chain(root, chain)
    # Empty owner-only directories are created first so Git can judge the real directory.
    os.close(_open_directory_chain(root, chain, create=True))
    _require_ignored_directory(root, base)  # whole output tree, before authorization is consumed
    _require_ignored_untracked(
        root, [marker, *(probe / name for name in _ARTIFACT_NAMES)], tree=base
    )


def _reserve_execution(root: Path, report: PreflightReport, auth: ExperimentAuthorization) -> None:
    """Atomically consume the single execution allowed for these inputs; never retried."""
    base = Path(OUTPUT_ROOTS[report.evidence_kind])
    directory = _open_directory_chain(root, (*base.parts, EXECUTIONS_DIRECTORY), create=False)
    try:
        marker = canonical_json_bytes(
            {
                "schema_version": MARKER_SCHEMA,
                "state": "reserved",
                "study_id": study.STUDY_ID,
                "evidence_kind": report.evidence_kind,
                "input_digest": report.input_digest,
                "authorization_id": auth.authorization_id,
                "execution_authority": "none",
                "disposition": "NO CANDIDATE PROMOTION",
            }
        )
        try:
            _write_exclusive(directory, f"{_execution_key(report)}.json", marker)
        except FileExistsError:
            raise _reject("execution_already_consumed_or_interrupted") from None
        # The marker's name must survive a crash before any fitting begins.
        _fsync_directory(directory)
    finally:
        os.close(directory)


def _strip_bins(value: Any) -> Any:
    """Reliability bins can hold one row's mean probability; they are never persisted."""
    if isinstance(value, dict):
        return {k: _strip_bins(v) for k, v in value.items() if k != "reliability_bins"}
    if isinstance(value, list):
        return [_strip_bins(v) for v in value]
    return value


def _assert_sanitized(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in FORBIDDEN_OUTPUT_KEYS:
                raise _reject("unsanitized_output_key")
            _assert_sanitized(item)
    elif isinstance(value, list | tuple):
        for item in value:
            _assert_sanitized(item)


def _sanitized_documents(
    payload: dict[str, Any], report: PreflightReport, auth: ExperimentAuthorization
) -> dict[str, Any]:
    lineage = json.loads(json.dumps(payload["lineage"]))
    membership = lineage.pop("fold_membership")
    boundary = lineage["parent"]["boundary"]
    protected = boundary.pop("phase2_final_test_prediction_sessions")
    boundary["phase2_final_test_prediction_sessions_sha256"] = sha256_json(protected)
    summary = [
        {
            "index": m["index"],
            "training_rows": len(m["training_rows"]),
            "purge_rows": len(m["purge_rows"]),
            "assessment_rows": len(m["assessment_rows"]),
            "training_last_exit": m["training_last_exit"],
            "assessment_first_prediction": m["assessment_first_prediction"],
            "assessment_last_exit": m["assessment_last_exit"],
        }
        for m in membership
    ]
    lineage["fold_membership_sha256"] = sha256_json(membership)
    lineage["fold_membership_summary"] = summary
    lineage["adapter"] = {
        "adapter_id": ADAPTER_ID,
        "runtime_fingerprint": report.runtime_fingerprint,
        "preflight_report": _plain(report),
    }
    folds = [
        {
            "index": index,
            "models": fold["models"],
            "candidate_minus_control": fold["candidate_minus_control"],
        }
        for index, fold in enumerate(payload["folds"])
    ]
    status = {
        "experiment_id": payload["experiment_id"],
        "evidence_kind": payload["evidence_kind"],
        "point_in_time_status": payload["point_in_time_status"],
        "usage_rights_status": payload["usage_rights_status"],
        "execution_authority": "none",
        "disposition": "NO CANDIDATE PROMOTION",
    }
    documents: dict[str, Any] = {
        "configuration.json": {**status, "configuration": lineage["configuration"]},
        "lineage.json": {**status, "lineage": lineage},
        "metrics.json": {
            **status,
            "dimensions": payload["dimensions"],
            "folds": folds,
            "aggregates": payload["aggregates"],
            "economics": payload["economics"],
        },
        "findings.json": {
            **status,
            "screen": payload["screen"],
            "economic_outcome": {
                "outcome": payload["economics"]["outcome"],
                "rule_outcome": payload["economics"]["rule_outcome"],
                "decisional": payload["economics"]["decisional"],
                "evidence_label": payload["economics"]["evidence_label"],
            },
            **(
                {"synthetic_notice": payload["synthetic_notice"]}
                if "synthetic_notice" in payload
                else {}
            ),
        },
        "authorization_record.json": {
            **status,
            "authorization_id": auth.authorization_id,
            "report_digest": auth.report_digest,
            "owner_rights_reference_id": auth.owner_rights_reference_id,
            "rights_status": "owner_attested_reference_not_verified_by_system",
        },
    }
    documents = _strip_bins(documents)
    documents["metrics.json"]["reliability_bins"] = "omitted_to_prevent_row_level_disclosure"
    _assert_sanitized(documents)
    return documents


def _publish(root: Path, evidence_kind: str, experiment_id: str, files: dict[str, bytes]) -> None:
    """Exclusive private directory reservation, O_EXCL no-follow writes, index written last."""
    match = _EXPERIMENT_ID.fullmatch(experiment_id)
    if match is None or not experiment_id.startswith(NAMESPACES[evidence_kind]):
        raise _reject("invalid_experiment_id_for_evidence_kind")
    base = Path(OUTPUT_ROOTS[evidence_kind])
    parent = _open_directory_chain(root, base.parts, create=True)
    try:
        try:
            os.stat(experiment_id, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise _reject("output_already_exists_no_overwrite")
        _require_ignored_untracked(
            root, [base / experiment_id / name for name in _ARTIFACT_NAMES], tree=base
        )
        try:
            os.mkdir(experiment_id, 0o700, dir_fd=parent)  # exclusive: fails if anything exists
        except FileExistsError:
            raise _reject("output_already_exists_no_overwrite") from None
        _fsync_directory(parent)
        directory = os.open(
            experiment_id, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent
        )
        try:
            _check_directory(directory)
            index = {name: sha256_bytes(data) for name, data in files.items()}
            for name, data in files.items():
                _write_exclusive(directory, name, data)
            _write_exclusive(
                directory,
                "artifact_index.json",
                canonical_json_bytes(
                    {
                        "experiment_id": experiment_id,
                        "evidence_kind": evidence_kind,
                        "adapter_id": ADAPTER_ID,
                        "artifacts": dict(sorted(index.items())),
                        "execution_authority": "none",
                        "disposition": "NO CANDIDATE PROMOTION",
                    }
                ),
            )  # written last: completion marker
            _fsync_directory(directory)
        finally:
            os.close(directory)
    finally:
        os.close(parent)


def _output_files(
    payload_json: str, report: PreflightReport, auth: ExperimentAuthorization
) -> dict[str, bytes]:
    payload = json.loads(payload_json)
    documents = _sanitized_documents(payload, report, auth)
    files = {"limitations.md": f"# Limitations\n\n{payload['limitations']}\n".encode()}
    files.update({name: canonical_json_bytes(doc) for name, doc in documents.items()})
    return files


def run_authorized_experiment(
    *,
    repository_root: Path,
    qqq_manifest: Path,
    iwm_manifest: Path,
    authorization_path: Path,
    pinned_commit: str,
    phase3b_config_path: Path | None = None,
    _fixture_parent: FrozenParent | None = None,
) -> ExperimentRecord:
    """Evaluate once, only under an owner authorization bound to the verified inputs."""
    _require_clean_git_environment()
    _claim_process("run")
    root = repository_root.resolve(strict=True)
    attestation = _collect_runtime_attestation()
    _require_runtime(attestation, pinned_commit)
    fingerprint = _require_runtime_unchanged()
    auth = _load_authorization(root, authorization_path)
    verified = _verify_inputs(
        root=root,
        qqq_manifest=qqq_manifest,
        iwm_manifest=iwm_manifest,
        config_path=phase3b_config_path,
        fixture_parent=_fixture_parent,
        attestation=attestation,
        runtime_fingerprint=fingerprint,
    )
    _bind_authorization(auth, verified.report, attestation)
    rights = study.OwnerRightsEvidence(
        auth.owner_rights_reference_id,
        tuple((s.symbol, s.dataset_id, s.canonical_sha256) for s in (verified.qqq, verified.iwm)),
    )
    fixture = (
        None
        if _fixture_parent is None
        else study._FixtureIdentity(
            parent_dataset_id=verified.frozen.dataset_id,
            parent_canonical_checksum=verified.frozen.canonical_checksum,
            parent_campaign_id=verified.frozen.campaign_id,
        )
    )
    _recheck(root, verified, attestation, fingerprint)
    _prepare_destination(root, verified.report)  # destination valid BEFORE any fitting
    _require_policy_unchanged(verified.report)  # last read-only check before consuming the marker
    _reserve_execution(root, verified.report, auth)  # atomic single use; no retry on failure
    result = study._evaluate_retrospective_study(
        parent_manifest=verified.manifest,
        parent_market_data=verified.market_data,
        accepted_boundary=verified.boundary,
        qqq=verified.qqq,
        iwm=verified.iwm,
        rights=rights,
        fixture_identity=fixture,
    )
    _recheck(root, verified, attestation, fingerprint)
    files = _output_files(result.payload_json, verified.report, auth)
    _publish(root, result.evidence_kind, result.experiment_id, files)
    directory = root / OUTPUT_ROOTS[result.evidence_kind] / result.experiment_id
    return ExperimentRecord(
        result.experiment_id,
        directory,
        result.evidence_kind,
        sha256_bytes((directory / "artifact_index.json").read_bytes()),
    )
