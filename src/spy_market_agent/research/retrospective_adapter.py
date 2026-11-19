"""AQ-P3C-R minimal verified-data adapter (RIC-183). Offline; owner-gated; no acquisition.

Two entry points, each usable at most once per process:

* :func:`eligibility_preflight` verifies metadata and artifacts and returns a sanitized
  report. It never fits a model.
* :func:`run_authorized_experiment` additionally requires an owner-authored authorization
  file bound to the exact report digest, commit and destination, then evaluates once and
  persists sanitized, immutable output.

Nothing here acquires data, reads credentials, contacts a network or broker, or writes
outside the approved ignored output root. Existing verification (``verify_context_artifacts``,
``_verify_parent_campaign``, ``_load_verified_research_market_data``, the Phase 2 isolation
function, ``bind_context_series`` and the evaluator) is reused unchanged. The owner rights
reference is recorded as an owner attestation; this code cannot verify a licence, and the
manifest flags stay UNVERIFIED. Authorization is a private, owner-controlled, input-bound
file; it is not a cryptographic signature.
"""

from __future__ import annotations

import json
import os
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
    ContextAcquisitionManifest,
)
from spy_market_agent.market_data.context_storage import ContextStore
from spy_market_agent.market_data.errors import MarketDataAcquisitionError
from spy_market_agent.research import retrospective_study as study
from spy_market_agent.research.artifacts import ResearchArtifactStore
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
APPROVED_START: Final = date(2018, 1, 2)
APPROVED_END: Final = date(2024, 10, 15)
APPROVED_SESSIONS: Final = 1708
OUTPUT_ROOTS: Final = {
    "retrospective_corrected": "artifacts/research/phase3cr",
    "synthetic_fixture": "artifacts/research/phase3cr_synthetic",
}
DEFAULT_PHASE3B_CONFIG: Final = "configs/research/phase3b_nonoverlapping.json"
MAX_AUTHORIZATION_BYTES: Final = 64 * 1024
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_AUTH_ID = re.compile(r"[A-Za-z0-9._:-]{8,128}\Z")
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
_PROCESS: dict[str, bool] = {"used": False}


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


REAL_PARENT: Final = FrozenParent(
    dataset_id=study.PARENT_DATASET_ID,
    canonical_checksum=study.PARENT_CANONICAL_CHECKSUM,
    market_data_checksum="09371762ecab4ee32dad6896b454d693d0e654f8f565ec021557a5df0bbad6c2",
    manifest_self_checksum="de296295cd4b491848d7c0abe0470c3c74e81123491b92efbd006fa458fee6b6",
    campaign_id=study.PARENT_CAMPAIGN_ID,
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
    parent_dataset_id: str
    parent_canonical_checksum: str
    parent_market_data_checksum: str
    parent_manifest_self_checksum: str
    campaign_id: str
    campaign_index_sha256: str
    campaign_manifest_sha256: str
    research_slice_checksum: str
    source_rows: int
    source_first_session: str
    source_last_session: str
    prediction_rows: int
    prediction_first_session: str
    prediction_last_session: str
    excluded_protected_source_rows: int
    context: tuple[tuple[str, str, str, str], ...]  # symbol, dataset, canonical, manifest sha
    fit_performed: bool = False
    execution_authority: str = "none"
    disposition: str = "NO CANDIDATE PROMOTION"

    @property
    def digest(self) -> str:
        return sha256_json(_plain(self))


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
    fingerprint: tuple[str, ...]


# ---------------------------------------------------------------------------
# Runtime prerequisites
# ---------------------------------------------------------------------------


def _code_repository() -> Path:
    return Path(__file__).resolve().parents[3]


def _collect_runtime_attestation(code_repository: Path | None = None) -> RuntimeAttestation:
    repository = code_repository or _code_repository()
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repository,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=repository,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        raise _reject("runtime_attestation_unavailable") from None
    return RuntimeAttestation(
        isolated=bool(sys.flags.isolated), git_commit=head, clean=not status.strip()
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


def _claim_process() -> None:
    if _PROCESS["used"]:
        raise _reject("process_not_fresh")
    _PROCESS["used"] = True


def _runtime_fingerprint() -> str:
    """Uncached code/dependency fingerprint for the study and this adapter."""
    here = str(Path(__file__).resolve())
    roots = (str(Path(study.__file__).resolve()), here)
    checksums, versions = study._closure(roots)
    return sha256_json({"checksums": checksums, "versions": versions})


def _require_lineage_not_stale() -> str:
    cached = study._transitive_lineage()
    fresh = study._closure((str(Path(study.__file__).resolve()),))
    if cached != fresh:
        raise _reject("source_or_dependencies_changed_since_import")
    return _runtime_fingerprint()


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


def _require_ignored_untracked(root: Path, relative: Path) -> None:
    ignored = subprocess.run(
        ["git", "check-ignore", "--no-index", "-q", "--", relative.as_posix()],
        cwd=root,
        capture_output=True,
        check=False,
    )
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", relative.as_posix()],
        cwd=root,
        capture_output=True,
        check=False,
    )
    if ignored.returncode != 0 or tracked.returncode == 0:
        raise _reject("path_must_be_ignored_and_untracked")


# ---------------------------------------------------------------------------
# Metadata gate (manifests only; no raw or canonical observations are read)
# ---------------------------------------------------------------------------


def _metadata_gate(
    store: ContextStore, qqq_path: Path, iwm_path: Path
) -> dict[str, tuple[ContextAcquisitionManifest, bytes, Path]]:
    expected_sessions = len(XNYSCalendar().sessions_between(APPROVED_START, APPROVED_END))
    if expected_sessions != APPROVED_SESSIONS:
        raise _reject("approved_scope_session_count_mismatch")
    found: dict[str, tuple[ContextAcquisitionManifest, bytes, Path]] = {}
    for symbol, path in (("QQQ", qqq_path), ("IWM", iwm_path)):
        try:
            with store.locked(create=False):
                data = store.read(path)
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
        expected = store.paths(symbol, manifest.dataset_id)[2]
        relative = path.relative_to(store.root) if path.is_absolute() else path
        if relative != expected:
            raise _reject("context_manifest_path_identity_mismatch")
        found[symbol] = (manifest, data, expected)
    if found["QQQ"][0].dataset_id == found["IWM"][0].dataset_id:
        raise _reject("duplicate_context_dataset")
    return found


# ---------------------------------------------------------------------------
# Verified inputs
# ---------------------------------------------------------------------------


def _phase3b_config(config_path: Path | None, frozen: FrozenParent) -> Phase3BConfig:
    try:
        config = load_phase3b_config(config_path or _code_repository() / DEFAULT_PHASE3B_CONFIG)
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


def _verify_inputs(
    *,
    root: Path,
    qqq_manifest: Path,
    iwm_manifest: Path,
    config_path: Path | None,
    fixture_parent: FrozenParent | None,
    attestation: RuntimeAttestation,
) -> _Verified:
    frozen = fixture_parent or REAL_PARENT
    if fixture_parent is not None and (
        fixture_parent.dataset_id == REAL_PARENT.dataset_id
        or fixture_parent.canonical_checksum == REAL_PARENT.canonical_checksum
        or fixture_parent.campaign_id == REAL_PARENT.campaign_id
    ):
        raise _reject("fixture_parent_must_differ_from_accepted_parent")
    evidence_kind = "retrospective_corrected" if fixture_parent is None else "synthetic_fixture"
    root = root.resolve(strict=True)
    store = ContextStore(root)
    gate = _metadata_gate(store, qqq_manifest, iwm_manifest)  # BEFORE any observation read

    parent_manifest_path = (
        root / "data/manifests/alpaca/SPY/1Day/sip/all" / f"{frozen.dataset_id}.manifest.json"
    )
    parent_canonical_path = (
        root / "data/canonical/alpaca/SPY/1Day/sip/all" / f"{frozen.dataset_id}.canonical.csv"
    )
    campaign_dir = root / "artifacts/research" / frozen.campaign_id
    for private in (
        parent_manifest_path,
        parent_canonical_path,
        campaign_dir,
        *(campaign_dir / n for n in ("artifact_index.json", "experiment_manifest.json")),
        *(root / item[2] for item in gate.values()),
        *(root / store.paths(sym, gate[sym][0].dataset_id)[1] for sym in gate),
    ):
        _require_private(private, root)

    config = _phase3b_config(config_path, frozen)
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

    artifact_store = ResearchArtifactStore(root / "artifacts/research", repository_root=root)
    report = PreflightReport(
        evidence_kind=evidence_kind,
        git_commit=attestation.git_commit,
        parent_dataset_id=manifest.dataset_id,
        parent_canonical_checksum=manifest.canonical_content_checksum,
        parent_market_data_checksum=recomputed,
        parent_manifest_self_checksum=str(manifest.manifest_artifact_checksum),
        campaign_id=frozen.campaign_id,
        campaign_index_sha256=artifact_store.checksum(frozen.campaign_id, "artifact_index.json"),
        campaign_manifest_sha256=artifact_store.checksum(
            frozen.campaign_id, "experiment_manifest.json"
        ),
        research_slice_checksum=boundary.research_slice_checksum,
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
    )
    fingerprint = (
        report.digest,
        sha256_bytes(parent_manifest_path.read_bytes()),
        *(sha256_bytes(gate[s][1]) for s in ("QQQ", "IWM")),
        *(series[s].canonical_sha256 for s in ("QQQ", "IWM")),
    )
    return _Verified(
        report, manifest, parent, accepted_boundary, series["QQQ"], series["IWM"], frozen,
        fingerprint,
    )  # fmt: skip


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
    _claim_process()
    attestation = _collect_runtime_attestation()
    _require_runtime(attestation, pinned_commit)
    _require_lineage_not_stale()
    verified = _verify_inputs(
        root=repository_root,
        qqq_manifest=qqq_manifest,
        iwm_manifest=iwm_manifest,
        config_path=phase3b_config_path,
        fixture_parent=_fixture_parent,
        attestation=attestation,
    )
    return verified.report


# ---------------------------------------------------------------------------
# Authorization and output
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
    _require_ignored_untracked(root, resolved.relative_to(root))
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
    payload: dict[str, Any],
    report: PreflightReport,
    auth: ExperimentAuthorization,
    fingerprint: str,
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
        "runtime_fingerprint": fingerprint,
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
        },
        "findings.json": {
            **status,
            "screen": payload["screen"],
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


def _write_outputs(
    root: Path,
    payload_json: str,
    experiment_id: str,
    evidence_kind: str,
    report: PreflightReport,
    auth: ExperimentAuthorization,
    fingerprint: str,
) -> ExperimentRecord:
    relative_root = Path(OUTPUT_ROOTS[evidence_kind])
    _require_ignored_untracked(root, relative_root / experiment_id / "artifact_index.json")
    store = ResearchArtifactStore(root / relative_root, repository_root=root)
    directory = store.experiment_dir(experiment_id)
    if directory.exists() or directory.is_symlink():
        raise _reject("output_already_exists_no_overwrite")
    payload = json.loads(payload_json)
    documents = _sanitized_documents(payload, report, auth, fingerprint)
    limitations = f"# Limitations\n\n{payload['limitations']}\n"
    store.write_bytes(
        experiment_id,
        "limitations.md",
        limitations.encode(),
        expected_checksum=sha256_bytes(limitations.encode()),
    )
    directory.chmod(0o700)
    index: dict[str, str] = {"limitations.md": sha256_bytes(limitations.encode())}
    for name, document in documents.items():
        index[name] = store.write_json(experiment_id, name, document)
    index_checksum = store.write_json(
        experiment_id,
        "artifact_index.json",
        {
            "experiment_id": experiment_id,
            "evidence_kind": evidence_kind,
            "adapter_id": ADAPTER_ID,
            "artifacts": dict(sorted(index.items())),
            "execution_authority": "none",
            "disposition": "NO CANDIDATE PROMOTION",
        },
    )  # written last: completion marker
    return ExperimentRecord(experiment_id, directory, evidence_kind, index_checksum)


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
    _claim_process()
    root = repository_root.resolve(strict=True)
    attestation = _collect_runtime_attestation()
    _require_runtime(attestation, pinned_commit)
    before = _require_lineage_not_stale()
    auth = _load_authorization(root, authorization_path)
    verified = _verify_inputs(
        root=root,
        qqq_manifest=qqq_manifest,
        iwm_manifest=iwm_manifest,
        config_path=phase3b_config_path,
        fixture_parent=_fixture_parent,
        attestation=attestation,
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
    result = study._evaluate_retrospective_study(
        parent_manifest=verified.manifest,
        parent_market_data=verified.market_data,
        accepted_boundary=verified.boundary,
        qqq=verified.qqq,
        iwm=verified.iwm,
        rights=rights,
        fixture_identity=fixture,
    )
    # Source, dependencies, checkout and inputs must be unchanged throughout evaluation.
    after_attestation = _collect_runtime_attestation()
    if (
        after_attestation != attestation
        or _require_lineage_not_stale() != before
        or _input_fingerprint(root, verified) != verified.fingerprint
    ):
        raise _reject("runtime_or_inputs_changed_during_evaluation")
    return _write_outputs(
        root,
        result.payload_json,
        result.experiment_id,
        result.evidence_kind,
        verified.report,
        auth,
        before,
    )


def _input_fingerprint(root: Path, verified: _Verified) -> tuple[str, ...]:
    store = ContextStore(root)
    parent = (
        root
        / "data/manifests/alpaca/SPY/1Day/sip/all"
        / f"{verified.frozen.dataset_id}.manifest.json"
    )
    gated = []
    canon = []
    for series in (verified.qqq, verified.iwm):
        manifest_path, canonical_path = (
            store.paths(series.symbol, series.dataset_id)[2],
            store.paths(series.symbol, series.dataset_id)[1],
        )
        with store.locked(create=False):
            gated.append(sha256_bytes(store.read(manifest_path)))
            canon.append(sha256_bytes(store.read(canonical_path)))
    return (verified.report.digest, sha256_bytes(parent.read_bytes()), *gated, *canon)
