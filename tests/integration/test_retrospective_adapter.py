"""Synthetic-only verification of the AQ-P3C-R verified-data adapter."""

from __future__ import annotations

import ast
import json
import os
import shutil
import stat
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from spy_market_agent.research import retrospective_adapter as adapter
from spy_market_agent.research import retrospective_study as study
from unit.retrospective_adapter_helpers import (
    COMMIT,
    Sandbox,
    acquire_context,
    build_campaign,
    build_sandbox,
    campaign_pins,
    clone,
    write_authorization,
)

ROOT = Path(__file__).resolve().parents[2]
ATTESTED = adapter.RuntimeAttestation(isolated=True, git_commit=COMMIT, clean=True)


def _scrub_git_environment(patch: pytest.MonkeyPatch) -> None:
    """The adapter requires an environment without GIT_*; tests start from a clean one."""
    for name in [n for n in os.environ if n.startswith("GIT_")]:
        patch.delenv(name)


def _fresh() -> None:
    adapter._PROCESS.update(preflight=False, run=False)


def _attested(*_args: object, **_kwargs: object) -> adapter.RuntimeAttestation:
    return ATTESTED


@pytest.fixture(autouse=True)
def fresh_process_state(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """pytest is not an isolated, pinned process: attest explicitly, one process per test."""
    monkeypatch.setattr(adapter, "_collect_runtime_attestation", _attested)
    _scrub_git_environment(monkeypatch)
    _fresh()
    yield
    _fresh()


@pytest.fixture(scope="module")
def sandbox(tmp_path_factory: pytest.TempPathFactory) -> Sandbox:
    return build_sandbox(tmp_path_factory.mktemp("sandbox"))


def _preflight(box: Sandbox, **overrides: Any) -> adapter.PreflightReport:
    _fresh()
    args: dict[str, Any] = {
        "repository_root": box.root,
        "qqq_manifest": box.qqq_manifest,
        "iwm_manifest": box.iwm_manifest,
        "pinned_commit": COMMIT,
        "_fixture_parent": box.fixture_parent,
    }
    args.update(overrides)
    return adapter.eligibility_preflight(**args)


def _run(box: Sandbox, auth: Path, **overrides: Any) -> adapter.ExperimentRecord:
    _fresh()
    args: dict[str, Any] = {
        "repository_root": box.root,
        "qqq_manifest": box.qqq_manifest,
        "iwm_manifest": box.iwm_manifest,
        "authorization_path": auth,
        "pinned_commit": COMMIT,
        "_fixture_parent": box.fixture_parent,
    }
    args.update(overrides)
    return adapter.run_authorized_experiment(**args)


@pytest.fixture(scope="module")
def report(sandbox: Sandbox) -> adapter.PreflightReport:
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(adapter, "_collect_runtime_attestation", _attested)
        _scrub_git_environment(mp)
        return _preflight(sandbox)


@dataclass
class Ran:
    box: Sandbox
    record: adapter.ExperimentRecord
    result: study.RetrospectiveStudyResult


@pytest.fixture(scope="module")
def ran(
    sandbox: Sandbox, report: adapter.PreflightReport, tmp_path_factory: pytest.TempPathFactory
) -> Ran:
    box = clone(sandbox, tmp_path_factory.mktemp("ran") / "repo")
    captured: list[study.RetrospectiveStudyResult] = []
    real = study._evaluate_retrospective_study

    def capture(**kwargs: Any) -> study.RetrospectiveStudyResult:
        captured.append(real(**kwargs))
        return captured[-1]

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(adapter, "_collect_runtime_attestation", _attested)
        _scrub_git_environment(mp)
        mp.setattr(study, "_evaluate_retrospective_study", capture)
        record = _run(box, write_authorization(box.root, report.digest))
    return Ran(box, record, captured[0])


@pytest.fixture
def stub_evaluation(monkeypatch: pytest.MonkeyPatch, ran: Ran) -> None:
    monkeypatch.setattr(study, "_evaluate_retrospective_study", lambda **_k: ran.result)


class Counter:
    """Counts evaluator invocations; returns the cached synthetic result."""

    def __init__(self, result: study.RetrospectiveStudyResult) -> None:
        self.calls = 0
        self.result = result
        self.error: Exception | None = None

    def __call__(self, **_kwargs: object) -> study.RetrospectiveStudyResult:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.result


@pytest.fixture
def counting(monkeypatch: pytest.MonkeyPatch, ran: Ran) -> Counter:
    counter = Counter(ran.result)
    monkeypatch.setattr(study, "_evaluate_retrospective_study", counter)
    return counter


@pytest.fixture
def box(sandbox: Sandbox, tmp_path: Path) -> Sandbox:
    return clone(sandbox, tmp_path / "repo")


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------


def test_preflight_reports_identities_only_and_never_fits(
    sandbox: Sandbox, report: adapter.PreflightReport, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(**_k: object) -> None:
        raise AssertionError("preflight must not fit or evaluate")

    monkeypatch.setattr(study, "_evaluate_retrospective_study", forbidden)
    again = _preflight(sandbox)
    assert again == report
    assert report.fit_performed is False
    assert (report.source_rows, report.prediction_rows) == (1708, 1642)
    assert (report.source_first_session, report.source_last_session) == (
        "2018-01-02",
        "2024-10-15",
    )
    assert (report.prediction_first_session, report.prediction_last_session) == (
        "2018-03-29",
        "2024-10-07",
    )
    assert report.excluded_protected_source_rows == 303
    assert report.evidence_kind == "synthetic_fixture"
    assert report.parent_market_data_checksum == sandbox.fixture_parent.market_data_checksum
    assert [c[0] for c in report.context] == ["QQQ", "IWM"]
    assert report.execution_authority == "none"
    assert report.disposition == "NO CANDIDATE PROMOTION"
    text = json.dumps(adapter._plain(report))
    assert not {"close", "open", "probabilities"} & set(adapter._plain(report))
    assert len(text) < 4000


def test_frozen_real_parent_evidence_is_exact() -> None:
    real = adapter.REAL_PARENT
    assert real.dataset_id == "spy-v2p1-900ca75371bf05e25b1935b5"
    assert real.canonical_checksum.startswith("3fbaed8976d4aa1b")
    assert real.market_data_checksum == (
        "09371762ecab4ee32dad6896b454d693d0e654f8f565ec021557a5df0bbad6c2"
    )
    assert real.manifest_self_checksum == (
        "de296295cd4b491848d7c0abe0470c3c74e81123491b92efbd006fa458fee6b6"
    )
    assert real.campaign_id == "spy-v2p3-dev-1f9a68f3dce8df02e3645468"
    with pytest.raises(adapter.AdapterError, match="fixture_parent_must_differ"):
        adapter._verify_inputs(
            root=ROOT,
            qqq_manifest=Path("x"),
            iwm_manifest=Path("y"),
            config_path=None,
            fixture_parent=real,
            attestation=ATTESTED,
            runtime_fingerprint="0" * 64,
        )


def test_real_identities_are_required_without_the_fixture_seam(
    sandbox: Sandbox,
) -> None:
    with pytest.raises(adapter.AdapterError, match="input_missing"):
        _preflight(sandbox, _fixture_parent=None)
    with pytest.raises(adapter.AdapterError, match="fixture_parent_must_differ"):
        _preflight(sandbox, _fixture_parent=replace(adapter.REAL_PARENT))


def test_each_entry_point_can_run_once_per_process(sandbox: Sandbox) -> None:
    _preflight(sandbox)
    with pytest.raises(adapter.AdapterError, match="process_not_fresh"):
        adapter.eligibility_preflight(
            repository_root=sandbox.root,
            qqq_manifest=sandbox.qqq_manifest,
            iwm_manifest=sandbox.iwm_manifest,
            pinned_commit=COMMIT,
            _fixture_parent=sandbox.fixture_parent,
        )


# ---------------------------------------------------------------------------
# Metadata eligibility before any observation access
# ---------------------------------------------------------------------------


@pytest.fixture
def read_log(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []
    real_read = adapter.ContextStore.read

    def logged(self: Any, path: Path) -> bytes:
        seen.append(Path(path).name)
        return real_read(self, path)

    monkeypatch.setattr(adapter.ContextStore, "read", logged)

    def forbidden(*_a: object, **_k: object) -> None:
        raise AssertionError("observations must not be verified before the metadata gate")

    monkeypatch.setattr(adapter, "verify_context_artifacts", forbidden)
    return seen


def _small_context(box: Sandbox, symbol: str, start: date, end: date) -> Path:
    return acquire_context(box.root, symbol, start, end, 5)


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (date(2024, 10, 16), date(2024, 10, 18)),  # protected-context scope only
        (date(2018, 1, 2), date(2024, 10, 14)),  # one session short
        (date(2023, 9, 18), date(2023, 9, 22)),  # unrelated short range
    ],
)
def test_wrong_scope_is_rejected_before_any_observation_is_read(
    box: Sandbox, read_log: list[str], start: date, end: date
) -> None:
    wrong = _small_context(box, "QQQ", start, end)
    with pytest.raises(adapter.AdapterError, match="context_scope_differs_from_approved_range"):
        _preflight(box, qqq_manifest=wrong)
    assert read_log
    assert all(name.endswith(".manifest.json") for name in read_log)


def test_v1_artifacts_symbol_swaps_and_duplicates_are_rejected_early(
    box: Sandbox, read_log: list[str]
) -> None:
    folder = box.root / "data/manifests/alpaca/QQQ/1Day/sip/all"
    v1_namespace = folder / f"aq-ctx-v1-{'0' * 24}.manifest.json"
    v1_namespace.write_text(json.dumps({"schema_version": "aq-qqq-iwm-context-acquisition-v1"}))
    with pytest.raises(adapter.AdapterError, match="context_manifest_path_invalid"):
        _preflight(box, qqq_manifest=v1_namespace.relative_to(box.root))
    assert read_log == []  # the v1 namespace is refused structurally, unread
    v1_content = folder / f"aq-ctx-v2-{'1' * 24}.manifest.json"
    v1_content.write_text(json.dumps({"schema_version": "aq-qqq-iwm-context-acquisition-v1"}))
    with pytest.raises(adapter.AdapterError, match="context_manifest_unreadable_or_not_v2"):
        _preflight(box, qqq_manifest=v1_content.relative_to(box.root))
    for swapped in (
        {"qqq_manifest": box.iwm_manifest, "iwm_manifest": box.qqq_manifest},
        {"iwm_manifest": box.qqq_manifest},
    ):
        with pytest.raises(adapter.AdapterError, match="context_manifest_path_invalid"):
            _preflight(box, **swapped)
    assert all(name.endswith(".manifest.json") for name in read_log)


def test_manifest_scope_tampering_is_rejected(box: Sandbox) -> None:
    path = box.root / box.qqq_manifest
    data = json.loads(path.read_text())
    data["row_count"] = 1707
    path.write_text(json.dumps(data))
    with pytest.raises(adapter.AdapterError, match="context_scope_differs_from_approved_range"):
        _preflight(box)


def test_noncanonical_manifest_serialization_is_rejected(box: Sandbox) -> None:
    path = box.root / box.qqq_manifest
    path.write_text(path.read_text() + " ")  # valid scope, non-canonical bytes
    with pytest.raises(adapter.AdapterError, match="context_manifest_checksum_mismatch"):
        _preflight(box)


# ---------------------------------------------------------------------------
# Parent and campaign verification
# ---------------------------------------------------------------------------


def test_campaign_artifact_tampering_is_rejected(box: Sandbox) -> None:
    registry = box.root / "artifacts/research" / box.fixture_parent.campaign_id
    (registry / "model_registry.json").write_bytes(b'{"models": []}')
    with pytest.raises(adapter.AdapterError, match="parent_campaign_verification_failed"):
        _preflight(box)


def test_boundary_must_equal_the_accepted_campaign_metadata_exactly(box: Sandbox) -> None:
    from spy_market_agent.research.campaign import ResearchCampaignConfig
    from spy_market_agent.research.runner import _load_verified_research_market_data

    manifest_path = (
        box.root
        / "data/manifests/alpaca/SPY/1Day/sip/all"
        / f"{box.fixture_parent.dataset_id}.manifest.json"
    )
    manifest, parent = _load_verified_research_market_data(
        manifest_path=manifest_path,
        data_root=Path("data"),
        repository_root=box.root,
        config=ResearchCampaignConfig(),
    )

    def edit(boundary: dict[str, Any]) -> None:
        boundary["excluded_source_session_count"] += 1

    build_campaign(box.root, manifest, parent, boundary_edit=edit)
    # Self-consistently resealed campaign: the old pins reject it outright ...
    with pytest.raises(adapter.AdapterError, match="campaign_digest_not_pinned"):
        _preflight(box)
    # ... and even with refreshed pins the mechanical boundary must still equal it exactly.
    refreshed = replace(box.fixture_parent, **campaign_pins(box.root))
    with pytest.raises(adapter.AdapterError, match="exclusion_boundary_differs"):
        _preflight(box, _fixture_parent=refreshed)


def test_wrong_frozen_checksums_are_rejected(box: Sandbox) -> None:
    for field in ("market_data_checksum", "manifest_self_checksum", "canonical_checksum"):
        wrong = replace(box.fixture_parent, **{field: "0" * 64})
        with pytest.raises(adapter.AdapterError):
            _preflight(box, _fixture_parent=wrong)


def test_mutated_parent_rows_are_rejected_by_existing_verification(box: Sandbox) -> None:
    canonical = (
        box.root
        / "data/canonical/alpaca/SPY/1Day/sip/all"
        / f"{box.fixture_parent.dataset_id}.canonical.csv"
    )
    lines = canonical.read_text().split("\n")
    parts = lines[1900].split(",")
    column = lines[0].split(",").index("volume")
    parts[column] = str(int(parts[column]) + 1)  # a protected row
    lines[1900] = ",".join(parts)
    canonical.write_text("\n".join(lines))
    with pytest.raises(adapter.AdapterError, match="parent_artifact_verification_failed"):
        _preflight(box)


# ---------------------------------------------------------------------------
# Artifact substitution and runtime lineage
# ---------------------------------------------------------------------------


def _canonical_path(box: Sandbox, symbol: str) -> Path:
    manifest = json.loads(
        (box.root / (box.qqq_manifest if symbol == "QQQ" else box.iwm_manifest)).read_text()
    )
    return box.root / str(manifest["generated_paths"]["canonical"])


def test_modified_or_swapped_context_bytes_fail_verification(box: Sandbox) -> None:
    qqq, iwm = _canonical_path(box, "QQQ"), _canonical_path(box, "IWM")
    original = qqq.read_bytes()
    qqq.write_bytes(iwm.read_bytes())
    with pytest.raises(adapter.AdapterError, match="context_artifact_verification_failed"):
        _preflight(box)
    qqq.write_bytes(original[:-1] + b"9")
    with pytest.raises(adapter.AdapterError, match="context_artifact_verification_failed"):
        _preflight(box)


def test_substitution_between_verification_and_consumption_is_detected(
    box: Sandbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = adapter.verify_context_artifacts
    target = _canonical_path(box, "QQQ")
    other = _canonical_path(box, "IWM")

    def verify_then_swap(*args: Any, **kwargs: Any) -> Any:
        result = real(*args, **kwargs)
        if result.symbol == "QQQ":
            target.write_bytes(other.read_bytes())
        return result

    monkeypatch.setattr(adapter, "verify_context_artifacts", verify_then_swap)
    with pytest.raises(
        adapter.AdapterError, match="context_artifact_changed_between_verification_and_use"
    ):
        _preflight(box)


def test_acquisition_runtime_lineage_mismatch_is_rejected(
    box: Sandbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    from spy_market_agent.market_data import context_acquisition

    real = context_acquisition.context_lineage()
    monkeypatch.setattr(
        context_acquisition,
        "context_lineage",
        lambda: real.model_copy(update={"git_commit": "b" * 40}),
    )
    with pytest.raises(adapter.AdapterError, match="context_artifact_verification_failed"):
        _preflight(box)


def test_runtime_prerequisites_are_enforced_mechanically() -> None:
    ok = adapter.RuntimeAttestation(True, COMMIT, True)
    adapter._require_runtime(ok, COMMIT)
    for attestation, pin, code in (
        (adapter.RuntimeAttestation(False, COMMIT, True), COMMIT, "process_not_isolated"),
        (adapter.RuntimeAttestation(True, COMMIT, False), COMMIT, "checkout_not_clean"),
        (adapter.RuntimeAttestation(True, "c" * 40, True), COMMIT, "checkout_not_at_pinned"),
        (ok, "short", "invalid_pinned_commit"),
        (ok, COMMIT.upper(), "invalid_pinned_commit"),
    ):
        with pytest.raises(adapter.AdapterError, match=code):
            adapter._require_runtime(attestation, pin)


def test_real_attestation_detects_dirty_untracked_and_head(tmp_path: Path) -> None:
    from unit.retrospective_adapter_helpers import git_env

    environment = git_env()
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
    subprocess.run([*git, "init", "-q", str(tmp_path)], check=True, env=environment)
    (tmp_path / "a.txt").write_text("a")
    subprocess.run([*git, "-C", str(tmp_path), "add", "a.txt"], check=True, env=environment)
    subprocess.run(
        [*git, "-C", str(tmp_path), "commit", "-q", "-m", "x"], check=True, env=environment
    )
    head = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
        env=environment,
    ).stdout.strip()
    collect = _real_collect()
    clean = collect(tmp_path)
    assert (clean.git_commit, clean.clean, clean.isolated) == (head, True, False)
    (tmp_path / "a.txt").write_text("changed")
    assert collect(tmp_path).clean is False
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "new.txt").write_text("untracked")
    assert collect(tmp_path).clean is False
    with pytest.raises(adapter.AdapterError, match="process_not_isolated"):
        adapter._require_runtime(clean, head)
    with pytest.raises(adapter.AdapterError, match="runtime_attestation_unavailable"):
        collect(tmp_path / "missing")


def _real_collect() -> Any:
    return adapter._REAL_COLLECT


def test_isolated_flag_is_read_from_the_interpreter() -> None:
    code = (
        f"import sys; sys.path.insert(0, {str(ROOT / 'src')!r})\n"
        "from spy_market_agent.research import retrospective_adapter as a\n"
        "print(a._REAL_COLLECT().isolated)\n"
    )
    isolated = subprocess.run(
        [sys.executable, "-I", "-c", code], capture_output=True, text=True, cwd=ROOT
    )
    assert isolated.returncode == 0, isolated.stderr
    assert isolated.stdout.strip() == "True"
    ordinary = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT,
        env={"PYTHONPATH": str(ROOT / "src"), "PATH": os.environ.get("PATH", "")},
    )  # fmt: skip
    assert ordinary.stdout.strip() == "False"


def test_stale_lineage_is_detected(sandbox: Sandbox, monkeypatch: pytest.MonkeyPatch) -> None:
    checks, versions = study._transitive_lineage()
    monkeypatch.setattr(
        study, "_transitive_lineage", lambda: ({**checks, "x/y.py": "0" * 64}, versions)
    )
    with pytest.raises(adapter.AdapterError, match="source_or_dependencies_changed_since_import"):
        _preflight(sandbox)


def test_runtime_identity_is_bound_to_the_report_and_baselined_at_import(
    sandbox: Sandbox, report: adapter.PreflightReport, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert report.runtime_fingerprint == adapter._IMPORT_BASELINE
    assert report.python_version == sys.version.split()[0]
    assert replace(report, runtime_fingerprint="f" * 64).digest != report.digest
    assert replace(report, python_version="0.0.0").digest != report.digest
    assert replace(report, git_commit="b" * 40).digest != report.digest
    # input_digest (single-use granularity) deliberately ignores commit and runtime
    assert replace(report, runtime_fingerprint="f" * 64).input_digest == report.input_digest
    monkeypatch.setattr(adapter, "_IMPORT_BASELINE", "0" * 64)
    with pytest.raises(adapter.AdapterError, match="source_or_dependencies_changed_since_import"):
        _preflight(sandbox)


def test_dependency_or_source_drift_after_verification_is_detected(
    sandbox: Sandbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_closure, real_verify = study._closure, adapter._verify_inputs
    state = {"verified": False}

    def verify(**kwargs: Any) -> Any:
        result = real_verify(**kwargs)
        state["verified"] = True  # drift starts only once every input has been verified
        return result

    def drifting(roots: tuple[str, ...]) -> Any:
        checksums, versions = real_closure(roots)
        return (checksums, {**versions, "scikit-learn": "0.0.0"} if state["verified"] else versions)

    monkeypatch.setattr(adapter, "_verify_inputs", verify)
    monkeypatch.setattr(study, "_closure", drifting)
    with pytest.raises(adapter.AdapterError, match="source_or_dependencies_changed_since_import"):
        _preflight(sandbox)
    assert state["verified"] is True  # the rejection came from the post-verification recheck


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("stub_evaluation")
def test_authorization_is_required_and_must_bind_the_exact_inputs(
    box: Sandbox, report: adapter.PreflightReport
) -> None:
    with pytest.raises(adapter.AdapterError, match="input_missing"):
        _run(box, box.root / ".local" / "absent.json")
    cases: dict[str, dict[str, Any]] = {
        "authorization_does_not_match": {"report_digest": "f" * 64},
        "authorization_does_not_match ": {"git_commit": "c" * 40},
        "authorization_does_not_match  ": {"evidence_kind": "retrospective_corrected"},
        "authorization_does_not_match   ": {"output_destination": "artifacts/research/phase3cr"},
        "authorization_invalid": {"owner_rights_reference_id": ""},
        "authorization_invalid ": {"owner_rights_reference_id": "short"},
        "authorization_invalid  ": {"study_id": "other"},
        "authorization_invalid   ": {"execution_authority": "paper"},
        "authorization_invalid    ": {"extra_flag": True},
    }
    for code, overrides in cases.items():
        auth = write_authorization(box.root, report.digest, **overrides)
        with pytest.raises(adapter.AdapterError, match=code.strip()):
            _run(box, auth)


def test_booleans_or_missing_rights_evidence_cannot_authorize(
    box: Sandbox, report: adapter.PreflightReport, counting: Counter
) -> None:
    directory = box.root / ".local"
    directory.mkdir(exist_ok=True)
    valid = json.loads(write_authorization(box.root, report.digest).read_text())
    missing_rights = {k: v for k, v in valid.items() if k != "owner_rights_reference_id"}
    for name, body in (
        ("bool.json", {"authorized": True}),
        ("empty.json", {}),
        ("norights.json", missing_rights),  # a valid authorization minus only the rights ref
    ):
        path = directory / name
        path.write_text(json.dumps(body))
        path.chmod(0o600)
        with pytest.raises(adapter.AdapterError, match="authorization_invalid"):
            _run(box, path)
    assert counting.calls == 0


@pytest.mark.usefixtures("stub_evaluation")
def test_authorization_file_must_be_private_ignored_untracked_and_regular(
    box: Sandbox, report: adapter.PreflightReport
) -> None:
    auth = write_authorization(box.root, report.digest)
    auth.chmod(0o644)
    with pytest.raises(adapter.AdapterError, match="private_regular_file"):
        _run(box, auth)
    auth.chmod(0o600)
    visible = box.root / "authorization.json"
    visible.write_bytes(auth.read_bytes())
    visible.chmod(0o600)
    with pytest.raises(adapter.AdapterError, match="ignored_and_untracked"):
        _run(box, visible)
    link = box.root / ".local" / "link.json"
    link.symlink_to(auth)
    with pytest.raises(adapter.AdapterError, match="input_not_privately_owned"):
        _run(box, link)
    outside = box.root.parent / "outside.json"
    outside.write_bytes(auth.read_bytes())
    with pytest.raises(adapter.AdapterError, match="input_outside_repository"):
        _run(box, outside)


def test_changes_during_evaluation_are_detected_before_anything_is_written(
    box: Sandbox, report: adapter.PreflightReport, ran: Ran, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth = write_authorization(box.root, report.digest)
    clean = clone(box, box.root.parent / "third")
    target = _canonical_path(box, "QQQ")

    evaluated: list[int] = []

    def mutate(**_k: object) -> study.RetrospectiveStudyResult:
        evaluated.append(1)
        target.write_bytes(target.read_bytes() + b" ")
        return ran.result

    monkeypatch.setattr(study, "_evaluate_retrospective_study", mutate)
    with pytest.raises(adapter.AdapterError, match="runtime_or_inputs_changed_during_evaluation"):
        _run(box, auth)
    assert evaluated == [1]  # the change happened during, and was caught after, the fit
    assert not [
        p for p in (box.root / "artifacts/research/phase3cr_synthetic").iterdir()
        if p.name.startswith("aq-p3cr")
    ]  # fmt: skip

    calls: list[int] = []

    def flaky(*_a: object, **_k: object) -> adapter.RuntimeAttestation:
        calls.append(1)
        return ATTESTED if len(calls) == 1 else adapter.RuntimeAttestation(True, COMMIT, False)

    prefit: list[int] = []

    def never_fit(**_k: object) -> study.RetrospectiveStudyResult:
        prefit.append(1)
        return ran.result

    monkeypatch.setattr(study, "_evaluate_retrospective_study", never_fit)
    monkeypatch.setattr(adapter, "_collect_runtime_attestation", flaky)
    with pytest.raises(adapter.AdapterError, match="runtime_or_inputs_changed_during_evaluation"):
        _run(clean, write_authorization(clean.root, report.digest))
    assert prefit == []  # the checkout changed before the fit: nothing was fitted


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def _documents(directory: Path) -> dict[str, Any]:
    return {p.name: json.loads(p.read_text()) for p in directory.glob("*.json")}


def test_synthetic_run_is_isolated_labelled_and_sanitized(ran: Ran) -> None:
    record = ran.record
    assert record.evidence_kind == "synthetic_fixture"
    assert record.experiment_id.startswith("aq-p3cr-synth-")
    assert record.output_directory == (
        ran.box.root / "artifacts/research/phase3cr_synthetic" / record.experiment_id
    )
    assert not (ran.box.root / "artifacts/research/phase3cr").exists()
    documents = _documents(record.output_directory)
    assert set(documents) == {
        "artifact_index.json",
        "authorization_record.json",
        "configuration.json",
        "findings.json",
        "lineage.json",
        "metrics.json",
    }
    assert (record.output_directory / "limitations.md").read_text().startswith("# Limitations")
    for name, document in documents.items():
        if name == "artifact_index.json":
            continue
        assert document["evidence_kind"] == "synthetic_fixture"
        assert document["execution_authority"] == "none"
        assert document["disposition"] == "NO CANDIDATE PROMOTION"
    assert "synthetic_notice" in documents["findings.json"]
    assert (
        documents["authorization_record.json"]["rights_status"]
        == "owner_attested_reference_not_verified_by_system"
    )
    assert documents["metrics.json"]["dimensions"]["assessed_rows"] == 844


def test_persisted_output_contains_no_observations_or_row_level_material(ran: Ran) -> None:
    def keys(value: Any) -> Iterator[str]:
        if isinstance(value, dict):
            for key, item in value.items():
                yield str(key).lower()
                yield from keys(item)
        elif isinstance(value, list):
            for item in value:
                yield from keys(item)

    documents = _documents(ran.record.output_directory)
    seen = {key for document in documents.values() for key in keys(document)}
    assert not seen & adapter.FORBIDDEN_OUTPUT_KEYS
    lineage = documents["lineage.json"]["lineage"]
    assert "fold_membership" not in lineage
    assert len(lineage["fold_membership_sha256"]) == 64
    assert "phase2_final_test_prediction_sessions" not in lineage["parent"]["boundary"]
    assert len(lineage["fold_membership_summary"]) == 7
    raw = "".join(p.read_text() for p in ran.record.output_directory.iterdir())
    for needle in (
        '"training_rows":[',
        '"assessment_rows":[',
        "mean_probability",
        '"probabilities"',
    ):
        assert needle not in raw
    assert documents["metrics.json"]["reliability_bins"].startswith("omitted")
    assert 'reliability_bins":[' not in raw
    assert adapter._strip_bins({"a": [{"reliability_bins": [1], "b": 2}]}) == {"a": [{"b": 2}]}
    with pytest.raises(adapter.AdapterError, match="unsanitized_output_key"):
        adapter._assert_sanitized({"nested": [{"Close": [1.0]}]})


@pytest.mark.usefixtures("stub_evaluation")
def test_output_is_immutable_indexed_private_and_never_overwritten(
    ran: Ran, report: adapter.PreflightReport
) -> None:
    directory = ran.record.output_directory
    index = json.loads((directory / "artifact_index.json").read_text())
    assert (directory / "artifact_index.json").exists()
    for name, digest in index["artifacts"].items():
        data = (directory / name).read_bytes()
        assert adapter.sha256_bytes(data) == digest
        assert (directory / name).stat().st_mode & 0o077 == 0
    assert directory.stat().st_mode & 0o077 == 0
    assert ran.record.artifact_index_sha256 == adapter.sha256_bytes(
        (directory / "artifact_index.json").read_bytes()
    )
    before = {p.name: p.read_bytes() for p in directory.iterdir()}
    auth = write_authorization(ran.box.root, report.digest)
    with pytest.raises(adapter.AdapterError, match="execution_already_consumed_or_interrupted"):
        _run(ran.box, auth)
    with pytest.raises(adapter.AdapterError, match="output_already_exists_no_overwrite"):
        adapter._publish(
            ran.box.root, "synthetic_fixture", ran.record.experiment_id, {"limitations.md": b"x"}
        )
    assert {p.name: p.read_bytes() for p in directory.iterdir()} == before


@pytest.mark.usefixtures("stub_evaluation")
def test_output_destination_must_be_ignored_and_untracked(
    box: Sandbox, report: adapter.PreflightReport
) -> None:
    (box.root / ".gitignore").write_text(
        "data/raw/*\ndata/canonical/*\ndata/manifests/*\n.local/\n"
        "artifacts/research/*\n!artifacts/research/phase3cr_synthetic/\n"
    )
    auth = write_authorization(box.root, report.digest)
    with pytest.raises(adapter.AdapterError, match="ignored_and_untracked"):
        _run(box, auth)
    output = box.root / "artifacts/research/phase3cr_synthetic"
    assert not [p for p in output.rglob("*") if p.is_file()]  # no marker, nothing published


def test_real_namespace_is_unreachable_from_synthetic_inputs(ran: Ran) -> None:
    assert ran.result.experiment_id.startswith(study.SYNTHETIC_NAMESPACE)
    assert (
        adapter.OUTPUT_ROOTS["retrospective_corrected"] != adapter.OUTPUT_ROOTS["synthetic_fixture"]
    )
    assert ran.result.evidence_kind == "synthetic_fixture"


def test_adapter_has_no_network_credential_broker_or_acquisition_dependencies() -> None:
    path = ROOT / "src/spy_market_agent/research/retrospective_adapter.py"
    text = path.read_text()
    tree = ast.parse(text)
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    forbidden = (
        "alpaca",
        "requests",
        "httpx",
        "socket",
        "spy_market_agent.market_data.alpaca_provider",
        "spy_market_agent.market_data.context_transport",
        "spy_market_agent.market_data.pipeline",
        "spy_market_agent.execution",
        "spy_market_agent.broker",
    )
    assert not [m for m in imported for f in forbidden if m == f or m.startswith(f + ".")]
    for needle in ("getenv", "environ.get", "environ[", "acquire_historical", "StockHistorical"):
        assert needle not in text
    assert text.count("os.environ") == 2  # git-variable scrubbing and the GIT_* rejection
    assert "api_key" in adapter.FORBIDDEN_OUTPUT_KEYS


def test_run_has_no_network_side_effects(
    box: Sandbox, report: adapter.PreflightReport, ran: Ran, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*_a: object, **_k: object) -> None:
        raise AssertionError("no network access is allowed")

    monkeypatch.setattr("socket.socket", forbidden)
    monkeypatch.setattr("socket.create_connection", forbidden)
    record = _run(box, write_authorization(box.root, report.digest))  # real evaluator
    assert record.experiment_id == ran.record.experiment_id
    assert (record.output_directory / "artifact_index.json").read_bytes() == (
        ran.record.output_directory / "artifact_index.json"
    ).read_bytes()


# ---------------------------------------------------------------------------
# PR #102 remediation: H1 path gate, H2 single use, H3 safe publication, H4 pins/inputs
# ---------------------------------------------------------------------------


def test_misshapen_manifest_paths_are_rejected_before_any_read(
    box: Sandbox, read_log: list[str]
) -> None:
    canonical = _canonical_path(box, "QQQ").relative_to(box.root)
    raw = Path(
        str(canonical).replace("/canonical/", "/raw/").replace(".canonical.csv", ".raw.json")
    )
    good = box.qqq_manifest
    name = good.name
    folder = good.parent
    bad_paths = [
        canonical,
        raw,
        folder / name.replace(".manifest.json", ".canonical.csv"),
        folder / f"aq-ctx-v1-{'0' * 24}.manifest.json",
        folder / "notes.manifest.json",
        folder.parent / "x" / name,
        Path("data/manifests/alpaca/IWM/1Day/sip/all") / name,  # wrong symbol directory
        Path("../outside") / good,
        Path("data/manifests") / name,
        box.root.parent / "elsewhere" / good,
    ]
    for bad in bad_paths:
        with pytest.raises(adapter.AdapterError, match="context_manifest_path_invalid"):
            _preflight(box, qqq_manifest=bad)
    # the second path is validated before the first valid manifest is opened
    with pytest.raises(adapter.AdapterError, match="context_manifest_path_invalid"):
        _preflight(box, iwm_manifest=canonical)
    assert read_log == []


def test_identical_manifest_paths_are_rejected_before_reading(
    box: Sandbox, read_log: list[str]
) -> None:
    with pytest.raises(adapter.AdapterError, match="context_manifest_path_invalid"):
        _preflight(box, iwm_manifest=box.qqq_manifest)
    assert read_log == []


def test_replay_with_new_authorization_ids_cannot_fit_twice(
    box: Sandbox, report: adapter.PreflightReport, counting: Counter
) -> None:
    first = _run(box, write_authorization(box.root, report.digest))
    assert counting.calls == 1
    assert first.output_directory.is_dir()
    for identifier in ("auth-20260105-0001", "auth-20260106-9999"):  # even a brand-new ID
        auth = write_authorization(box.root, report.digest, authorization_id=identifier)
        with pytest.raises(adapter.AdapterError, match="execution_already_consumed_or_interrupted"):
            _run(box, auth)
    assert counting.calls == 1
    markers = list((box.root / "artifacts/research/phase3cr_synthetic/executions").iterdir())
    assert len(markers) == 1
    assert markers[0].stat().st_mode & 0o077 == 0


def test_interrupted_execution_is_never_retried(
    box: Sandbox, report: adapter.PreflightReport, counting: Counter
) -> None:
    counting.error = RuntimeError("simulated crash during fitting")
    with pytest.raises(RuntimeError):
        _run(box, write_authorization(box.root, report.digest))
    counting.error = None
    with pytest.raises(adapter.AdapterError, match="execution_already_consumed_or_interrupted"):
        _run(box, write_authorization(box.root, report.digest, authorization_id="auth-retry-0002"))
    assert counting.calls == 1
    directory = box.root / "artifacts/research/phase3cr_synthetic"
    assert not [p for p in directory.iterdir() if p.name.startswith("aq-p3cr")]


def test_concurrent_claims_allow_exactly_one_execution(
    box: Sandbox, report: adapter.PreflightReport
) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    adapter._prepare_destination(box.root, report)
    auth = adapter.ExperimentAuthorization.model_validate(
        json.loads(write_authorization(box.root, report.digest).read_text())
    )
    barrier = Barrier(8)

    def claim(_: int) -> str:
        barrier.wait()
        try:
            adapter._reserve_execution(box.root, report, auth)
        except adapter.AdapterError as exc:
            return str(exc)
        return "claimed"

    with ThreadPoolExecutor(8) as pool:
        outcomes = list(pool.map(claim, range(8)))
    assert outcomes.count("claimed") == 1
    assert set(outcomes) - {"claimed"} == {"execution_already_consumed_or_interrupted"}


def test_evaluator_is_not_invoked_when_authorization_or_destination_is_invalid(
    box: Sandbox, report: adapter.PreflightReport, counting: Counter
) -> None:
    for overrides in (
        {"report_digest": "f" * 64},
        {"git_commit": "c" * 40},
        {"output_destination": "artifacts/research/phase3cr"},
        {"owner_rights_reference_id": "x"},
    ):
        with pytest.raises(adapter.AdapterError):
            _run(box, write_authorization(box.root, report.digest, **overrides))
    outside = box.root.parent / "redirected"
    outside.mkdir()
    research = box.root / "artifacts/research"
    (research / "phase3cr_synthetic").symlink_to(outside, target_is_directory=True)
    with pytest.raises(adapter.AdapterError, match="output_ancestry_unsafe"):
        _run(box, write_authorization(box.root, report.digest))
    assert counting.calls == 0
    assert list(outside.iterdir()) == []  # nothing written through the redirect
    executions = research / "phase3cr_synthetic"
    executions.unlink()
    executions.mkdir()
    executions.chmod(0o777)
    with pytest.raises(adapter.AdapterError, match="output_ancestry_unsafe"):
        _run(box, write_authorization(box.root, report.digest))
    assert counting.calls == 0


def test_every_output_path_and_tracked_descendants_are_checked_before_fitting(
    box: Sandbox, report: adapter.PreflightReport, counting: Counter
) -> None:
    (box.root / ".gitignore").write_text(
        "data/raw/*\ndata/canonical/*\ndata/manifests/*\n.local/\n"
        "artifacts/research/*\n!artifacts/research/phase3cr_synthetic/\n"
        "artifacts/research/phase3cr_synthetic/*/artifact_index.json\n"
        "artifacts/research/phase3cr_synthetic/executions/\n"
    )  # only the index and marker would be covered: metrics.json etc. would be committable
    with pytest.raises(adapter.AdapterError, match="ignored_and_untracked"):
        _run(box, write_authorization(box.root, report.digest))
    assert counting.calls == 0


def test_tracked_files_under_the_output_root_block_the_run(
    box: Sandbox, report: adapter.PreflightReport, counting: Counter
) -> None:
    output = box.root / "artifacts/research/phase3cr_synthetic"
    output.mkdir(parents=True)
    (output / "stray.json").write_text("{}")
    from unit.retrospective_adapter_helpers import git_env

    subprocess.run(
        [
            "git",
            "-C",
            str(box.root),
            "add",
            "-f",
            "artifacts/research/phase3cr_synthetic/stray.json",
        ],
        check=True,
        env=git_env(),
    )
    with pytest.raises(adapter.AdapterError, match="ignored_and_untracked"):
        _run(box, write_authorization(box.root, report.digest))
    assert counting.calls == 0


def test_publication_is_exclusive_private_and_index_last(
    box: Sandbox, monkeypatch: pytest.MonkeyPatch, ran: Ran
) -> None:
    name = ran.record.experiment_id
    files = {n: n.encode() for n in adapter._ARTIFACT_NAMES if n != "artifact_index.json"}
    modes: list[int] = []
    written: list[str] = []
    real_mkdir, real_write = os.mkdir, adapter._write_exclusive

    def spy_mkdir(path: Any, mode: int = 0o777, **kwargs: Any) -> None:
        if str(path) == name:
            modes.append(mode)
        real_mkdir(path, mode, **kwargs)

    def spy_write(directory: int, file_name: str, data: bytes) -> None:
        written.append(file_name)
        real_write(directory, file_name, data)

    monkeypatch.setattr(os, "mkdir", spy_mkdir)
    monkeypatch.setattr(adapter, "_write_exclusive", spy_write)
    adapter._prepare_destination(box.root, ran_report(ran))
    adapter._publish(box.root, "synthetic_fixture", name, files)
    assert modes == [0o700]  # private at creation; never chmod'ed afterwards
    assert written[-1] == "artifact_index.json"
    assert sorted(written[:-1]) == sorted(files)
    directory = box.root / "artifacts/research/phase3cr_synthetic" / name
    assert directory.stat().st_mode & 0o077 == 0
    with pytest.raises(adapter.AdapterError, match="output_already_exists_no_overwrite"):
        adapter._publish(box.root, "synthetic_fixture", name, files)


def ran_report(ran: Ran) -> adapter.PreflightReport:
    return adapter.PreflightReport(**_report_from_output(ran))


def _report_from_output(ran: Ran) -> dict[str, Any]:
    document = json.loads((ran.record.output_directory / "lineage.json").read_text())
    fields = dict(document["lineage"]["adapter"]["preflight_report"])
    fields["context"] = tuple(tuple(item) for item in fields["context"])
    return fields


def test_symlinked_or_partial_experiment_directories_are_never_reused(
    box: Sandbox, ran: Ran
) -> None:
    name = ran.record.experiment_id
    base = box.root / "artifacts/research/phase3cr_synthetic"
    adapter._open_directory_chain(
        box.root, ("artifacts", "research", "phase3cr_synthetic"), create=True
    )
    target = box.root.parent / "elsewhere"
    target.mkdir()
    (base / name).symlink_to(target, target_is_directory=True)
    with pytest.raises(adapter.AdapterError, match="output_already_exists_no_overwrite"):
        adapter._publish(box.root, "synthetic_fixture", name, {"limitations.md": b"x"})
    assert list(target.iterdir()) == []
    (base / name).unlink()
    (base / name).mkdir()
    (base / name / "metrics.json").write_text("partial")
    with pytest.raises(adapter.AdapterError, match="output_already_exists_no_overwrite"):
        adapter._publish(box.root, "synthetic_fixture", name, {"limitations.md": b"x"})
    assert (base / name / "metrics.json").read_text() == "partial"


def test_concurrent_publication_has_one_winner_and_a_consistent_index(
    box: Sandbox, ran: Ran
) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    name = ran.record.experiment_id
    barrier = Barrier(6)

    def publish(index: int) -> str:
        files = {n: f"writer-{index}".encode() for n in adapter._ARTIFACT_NAMES[:-1]}
        barrier.wait()
        try:
            adapter._publish(box.root, "synthetic_fixture", name, files)
        except adapter.AdapterError as exc:
            return str(exc)
        return f"won-{index}"

    with ThreadPoolExecutor(6) as pool:
        outcomes = list(pool.map(publish, range(6)))
    winners = [o for o in outcomes if o.startswith("won-")]
    assert len(winners) == 1
    assert set(outcomes) - set(winners) == {"output_already_exists_no_overwrite"}
    directory = box.root / "artifacts/research/phase3cr_synthetic" / name
    index = json.loads((directory / "artifact_index.json").read_text())
    for artifact, digest in index["artifacts"].items():
        assert adapter.sha256_bytes((directory / artifact).read_bytes()) == digest
        assert (directory / artifact).read_bytes() == winners[0].replace("won-", "writer-").encode()


def test_invalid_experiment_ids_cannot_escape_or_cross_namespaces(box: Sandbox) -> None:
    for bad in (
        "../escape",
        "aq-p3cr-retro-" + "0" * 64,  # retrospective ID into the synthetic root
        "aq-p3cr-synth-" + "0" * 63,
        "aq-p3cr-synth-" + "G" * 64,
    ):
        with pytest.raises(adapter.AdapterError, match="invalid_experiment_id"):
            adapter._publish(box.root, "synthetic_fixture", bad, {"limitations.md": b"x"})


# H4 ----------------------------------------------------------------------------------


def test_real_campaign_digests_are_pinned_to_the_locally_verified_values() -> None:
    real = adapter.REAL_PARENT
    assert real.campaign_index_sha256 == (
        "e1934f41878f26d680ec2ec3a76744b6d7c9431e4eda6d02ac54ec5781c5f60f"
    )
    assert real.campaign_manifest_sha256 == (
        "bdab6f063aa0ab56f6c72b9d29c1257ed72398355de6029ad03b3bf40193977f"
    )
    with pytest.raises(adapter.AdapterError, match="fixture_parent_must_differ"):
        adapter._verify_inputs(
            root=ROOT,
            qqq_manifest=Path("x"),
            iwm_manifest=Path("y"),
            config_path=None,
            fixture_parent=replace(
                _distinct_fixture(), campaign_index_sha256=real.campaign_index_sha256
            ),
            attestation=ATTESTED,
            runtime_fingerprint="0" * 64,
        )


def _distinct_fixture() -> adapter.FrozenParent:
    return adapter.FrozenParent(
        dataset_id="spy-v2p1-" + "1" * 24,
        canonical_checksum="2" * 64,
        market_data_checksum="3" * 64,
        manifest_self_checksum="4" * 64,
        campaign_id="spy-v2p3-dev-" + "5" * 24,
        campaign_index_sha256="6" * 64,
        campaign_manifest_sha256="7" * 64,
    )


def _omitted_file_paths(box: Sandbox) -> dict[str, Path]:
    store_root = box.root
    parent_id = box.fixture_parent.dataset_id
    qqq = json.loads((box.root / box.qqq_manifest).read_text())
    return {
        "parent_raw": store_root / f"data/raw/alpaca/SPY/1Day/sip/all/{parent_id}.raw.json",
        "context_raw": store_root / qqq["generated_paths"]["raw"],
        "campaign_registry": store_root
        / "artifacts/research"
        / box.fixture_parent.campaign_id
        / "model_registry.json",
    }


@pytest.mark.parametrize("which", ["parent_raw", "context_raw", "campaign_registry"])
def test_previously_unchecked_inputs_must_also_be_private(box: Sandbox, which: str) -> None:
    _omitted_file_paths(box)[which].chmod(0o664)
    with pytest.raises(adapter.AdapterError, match="input_not_privately_owned"):
        _preflight(box)


@pytest.mark.parametrize(
    "which", ["parent_raw", "context_raw", "campaign_registry", "parent_canonical"]
)
def test_mutating_any_consumed_artifact_during_evaluation_is_detected(
    box: Sandbox,
    report: adapter.PreflightReport,
    counting: Counter,
    monkeypatch: pytest.MonkeyPatch,
    which: str,
) -> None:
    paths = _omitted_file_paths(box)
    paths["parent_canonical"] = (
        box.root
        / "data/canonical/alpaca/SPY/1Day/sip/all"
        / f"{box.fixture_parent.dataset_id}.canonical.csv"
    )
    target = paths[which]

    def mutate(**_k: object) -> study.RetrospectiveStudyResult:
        counting.calls += 1
        target.write_bytes(target.read_bytes() + b" ")
        return counting.result

    monkeypatch.setattr(study, "_evaluate_retrospective_study", mutate)
    with pytest.raises(adapter.AdapterError, match="runtime_or_inputs_changed_during_evaluation"):
        _run(box, write_authorization(box.root, report.digest))
    assert counting.calls == 1  # reached the evaluator; the change was caught afterwards
    assert not [
        p for p in (box.root / "artifacts/research/phase3cr_synthetic").iterdir()
        if p.name.startswith("aq-p3cr")
    ]  # fmt: skip


def test_report_binds_the_complete_input_snapshot(
    box: Sandbox, report: adapter.PreflightReport
) -> None:
    again = _preflight(box)
    assert again.input_snapshot_sha256 == report.input_snapshot_sha256
    _omitted_file_paths(box)["context_raw"].write_bytes(b"{}")
    with pytest.raises(adapter.AdapterError):
        _preflight(box)


# M1 ----------------------------------------------------------------------------------


def test_real_isolated_processes_end_to_end(tmp_path: Path) -> None:
    """Fresh interpreters (python -I): attestation, preflight, authorized run and replay."""
    code = _code_checkout(tmp_path / "code")
    data = tmp_path / "data_repo"
    data.mkdir()
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1"}
    build = (
        "import json, sys, dataclasses\n"
        "from pathlib import Path\n"
        "from unit.retrospective_adapter_helpers import build_sandbox\n"
        "box = build_sandbox(Path(sys.argv[1]))\n"
        "print(json.dumps({'qqq': str(box.qqq_manifest), 'iwm': str(box.iwm_manifest),"
        " 'parent': dataclasses.asdict(box.fixture_parent)}))\n"
    )
    built = subprocess.run(
        [sys.executable, "-c", build, str(data)],
        capture_output=True,
        text=True,
        cwd=code,
        env={**env, "PYTHONPATH": f"{code / 'src'}{os.pathsep}{ROOT / 'tests'}"},
    )
    assert built.returncode == 0, built.stderr[-2000:]
    spec = json.loads(built.stdout.strip().splitlines()[-1])
    from unit.retrospective_adapter_helpers import git_env

    head = subprocess.run(
        ["git", "-C", str(code), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
        env=git_env(),
    )
    assert head.returncode == 0
    commit = head.stdout.strip()

    def invoke(
        mode: str, *, isolated: bool = True, pin: str = commit, auth: str = ""
    ) -> dict[str, Any]:
        driver = (
            "import json, sys\n"
            f"sys.path.insert(0, {str(code / 'src')!r})\n"
            "from pathlib import Path\n"
            "from spy_market_agent.research import retrospective_adapter as a\n"
            f"assert str(Path(a.__file__).resolve()).startswith({str(code.resolve())!r})\n"
            f"spec = {spec!r}\n"
            "parent = a.FrozenParent(**spec['parent'])\n"
            f"kw = dict(repository_root=Path({str(data)!r}), qqq_manifest=Path(spec['qqq']),"
            f" iwm_manifest=Path(spec['iwm']), pinned_commit={pin!r}, _fixture_parent=parent)\n"
            "try:\n"
            f"    if {mode!r} == 'preflight':\n"
            "        r = a.eligibility_preflight(**kw)\n"
            "        out = {'ok': True, 'digest': r.digest, 'fingerprint': r.runtime_fingerprint,"
            " 'python': r.python_version}\n"
            "    else:\n"
            f"        rec = a.run_authorized_experiment(authorization_path=Path({auth!r}), **kw)\n"
            "        out = {'ok': True, 'experiment_id': rec.experiment_id}\n"
            "except a.AdapterError as exc:\n"
            "    out = {'ok': False, 'error': str(exc)}\n"
            "print(json.dumps(out))\n"
        )
        flags = ["-I"] if isolated else []
        done = subprocess.run(
            [sys.executable, *flags, "-c", driver],
            capture_output=True,
            text=True,
            cwd=code,
            env=env,
        )
        assert done.returncode == 0, done.stderr[-2000:]
        return json.loads(done.stdout.strip().splitlines()[-1])  # type: ignore[no-any-return]

    first = invoke("preflight")
    assert first["ok"], first
    assert first["python"] == sys.version.split()[0]
    assert invoke("preflight")["digest"] == first["digest"]  # deterministic across processes
    assert invoke("preflight", isolated=False) == {"ok": False, "error": "process_not_isolated"}
    assert invoke("preflight", pin="d" * 40) == {
        "ok": False,
        "error": "checkout_not_at_pinned_commit",
    }
    (code / "stray.txt").write_text("untracked")
    assert invoke("preflight") == {"ok": False, "error": "checkout_not_clean"}
    (code / "stray.txt").unlink()

    auth = write_authorization(data, first["digest"], git_commit=commit)
    ran_once = invoke("run", auth=str(auth))
    assert ran_once["ok"], ran_once
    assert ran_once["experiment_id"].startswith("aq-p3cr-synth-")
    replay = invoke("run", auth=str(auth))  # a brand-new real process: single use enforced
    assert replay == {"ok": False, "error": "execution_already_consumed_or_interrupted"}
    other = write_authorization(
        data, first["digest"], git_commit=commit, authorization_id="auth-new-9999"
    )
    assert invoke("run", auth=str(other))["error"] == "execution_already_consumed_or_interrupted"


def _code_checkout(destination: Path) -> Path:
    """A clean, committed copy of the code under test (no dependency on the dev worktree)."""
    from unit.retrospective_adapter_helpers import git_env

    destination.mkdir()
    for name in ("src", "configs", "pyproject.toml", ".gitignore"):
        source = ROOT / name
        if source.is_dir():
            shutil.copytree(
                source, destination / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
            )
        else:
            shutil.copy2(source, destination / name)
    environment = git_env()
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-C", str(destination)]
    subprocess.run([*git, "init", "-q"], check=True, env=environment)
    subprocess.run([*git, "add", "-A"], check=True, env=environment)
    subprocess.run([*git, "commit", "-q", "-m", "snapshot"], check=True, env=environment)
    return destination


# ---------------------------------------------------------------------------
# PR #102 final remediation: directory ignore, durability, config, git probes
# ---------------------------------------------------------------------------


def _make_repo(path: Path, name: str = "a.txt") -> Path:
    from unit.retrospective_adapter_helpers import git_env

    path.mkdir(parents=True)
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-C", str(path)]
    subprocess.run([*git, "init", "-q"], check=True, env=git_env())
    (path / name).write_text(name)
    subprocess.run([*git, "add", name], check=True, env=git_env())
    subprocess.run([*git, "commit", "-q", "-m", "x"], check=True, env=git_env())
    return path


def _head(path: Path) -> str:
    from unit.retrospective_adapter_helpers import git_env

    done = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
        env=git_env(),
    )
    assert done.returncode == 0
    return done.stdout.strip()


def test_git_probes_ignore_inherited_repository_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _make_repo(tmp_path / "target", "target.txt")
    other = _make_repo(tmp_path / "other", "other.txt")
    (other / "dirty.txt").write_text("untracked")  # would make `other` look dirty
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other))
    monkeypatch.setenv("GIT_INDEX_FILE", str(other / ".git" / "index"))
    attestation = _real_collect()(target)
    assert attestation.git_commit == _head(target)
    assert attestation.git_commit != _head(other)
    assert attestation.clean is True
    assert adapter._git(["rev-parse", "--show-toplevel"], target).stdout.strip() == str(
        target.resolve()
    )


def test_configured_fsmonitor_hook_is_never_executed(tmp_path: Path) -> None:
    from unit.retrospective_adapter_helpers import git_env

    repo = _make_repo(tmp_path / "repo")
    proof = tmp_path / "hook-ran"
    hook = tmp_path / "hook.sh"
    hook.write_text(f"#!/bin/sh\ntouch {proof}\n")
    hook.chmod(0o755)
    subprocess.run(
        ["git", "-C", str(repo), "config", "core.fsmonitor", str(hook)],
        check=True,
        env=git_env(),
    )
    # control: plain git status executes the configured hook, so the probe below is meaningful
    subprocess.run(["git", "-C", str(repo), "status"], capture_output=True, env=git_env())
    assert proof.exists()
    proof.unlink()
    attestation = _real_collect()(repo)
    assert attestation.clean is True
    assert not proof.exists()


def test_a_git_index_error_is_not_treated_as_untracked(
    box: Sandbox, report: adapter.PreflightReport, counting: Counter
) -> None:
    auth = write_authorization(box.root, report.digest)
    (box.root / ".git" / "index").write_bytes(b"not a git index")
    listing = adapter._git(["ls-files", "--", ".local/authorization.json"], box.root)
    assert listing.returncode != 0  # precondition: the index really is unreadable
    with pytest.raises(adapter.AdapterError, match="path_must_be_ignored_and_untracked"):
        _run(box, auth)
    assert counting.calls == 0


def test_whole_output_directory_must_be_ignored_before_authorization_is_consumed(
    box: Sandbox, report: adapter.PreflightReport, counting: Counter
) -> None:
    (box.root / ".gitignore").write_text(
        "data/raw/*\ndata/canonical/*\ndata/manifests/*\n.local/\n"
        "artifacts/research/*\n!artifacts/research/phase3cr_synthetic/\n"
        "artifacts/research/phase3cr_synthetic/*\n"
    )  # every file under the directory is ignored, but the directory itself is not
    with pytest.raises(adapter.AdapterError, match="ignored_and_untracked"):
        _run(box, write_authorization(box.root, report.digest))
    assert counting.calls == 0
    executions = box.root / "artifacts/research/phase3cr_synthetic/executions"
    assert not list(executions.iterdir())  # no marker: nothing was consumed


def test_marker_directory_sync_failure_stops_before_fitting(
    box: Sandbox,
    report: adapter.PreflightReport,
    counting: Counter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter._prepare_destination(box.root, report)
    executions = box.root / "artifacts/research/phase3cr_synthetic/executions"
    marker_inode = executions.stat().st_ino
    real = os.fsync

    def failing(descriptor: int) -> None:
        if os.fstat(descriptor).st_ino == marker_inode:  # only the marker directory itself
            raise OSError("simulated sync failure")
        real(descriptor)

    with monkeypatch.context() as sync_patch:
        sync_patch.setattr(os, "fsync", failing)
        with pytest.raises(adapter.AdapterError, match="durability_not_established"):
            _run(box, write_authorization(box.root, report.digest))
    assert counting.calls == 0
    assert len(list(executions.iterdir())) == 1  # the marker exists: consumed, fail closed
    with pytest.raises(adapter.AdapterError, match="execution_already_consumed_or_interrupted"):
        _run(box, write_authorization(box.root, report.digest))  # no automatic retry
    assert counting.calls == 0


def test_marker_and_ancestor_entries_are_synced_before_the_evaluator_runs(
    box: Sandbox, report: adapter.PreflightReport, ran: Ran, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    real = os.fsync

    def recording(descriptor: int) -> None:
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            events.append("sync-directory")
        real(descriptor)

    def evaluate(**_k: object) -> study.RetrospectiveStudyResult:
        events.append("evaluate")
        return ran.result

    monkeypatch.setattr(os, "fsync", recording)
    monkeypatch.setattr(study, "_evaluate_retrospective_study", evaluate)
    _run(box, write_authorization(box.root, report.digest))
    first = events.index("evaluate")
    # new ancestors (artifacts/research/<root>, executions) plus the marker directory
    assert events[:first].count("sync-directory") >= 2
    assert events[first - 1] == "sync-directory"  # the marker directory sync is last pre-fit


def test_parents_of_new_and_reused_entries_are_both_synced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    synced: list[int] = []
    real = os.fsync

    def recording(descriptor: int) -> None:
        synced.append(descriptor)
        real(descriptor)

    monkeypatch.setattr(os, "fsync", recording)
    os.close(adapter._open_directory_chain(root, ("a", "b", "c"), create=True))
    assert len(synced) == 3  # one parent sync per newly created entry
    synced.clear()
    os.close(adapter._open_directory_chain(root, ("a", "b", "c"), create=True))
    assert len(synced) == 3  # reused entries are synced too
    synced.clear()
    os.close(adapter._open_directory_chain(root, ("a", "b", "c"), create=False))
    assert synced == []  # read-only walks have nothing to persist


def test_failed_sync_then_retry_cannot_skip_durability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    real = os.fsync

    def broken(_descriptor: int) -> None:
        raise OSError("simulated sync failure")

    with monkeypatch.context() as patch:
        patch.setattr(os, "fsync", broken)
        with pytest.raises(adapter.AdapterError, match="durability_not_established"):
            adapter._open_directory_chain(root, ("x", "y"), create=True)
    assert (root / "x").is_dir()  # the entry exists although its parent sync failed
    with monkeypatch.context() as patch:
        patch.setattr(os, "fsync", broken)
        with pytest.raises(adapter.AdapterError, match="durability_not_established"):
            # the reused entry must not be trusted: the retry also fails closed
            adapter._open_directory_chain(root, ("x", "y"), create=True)
    synced: list[int] = []

    def working(descriptor: int) -> None:
        synced.append(descriptor)
        real(descriptor)

    monkeypatch.setattr(os, "fsync", working)
    os.close(adapter._open_directory_chain(root, ("x", "y"), create=True))
    assert len(synced) == 2  # only after a successful sync of every parent does it proceed


def test_evaluation_cannot_begin_before_every_durability_step_succeeds(
    box: Sandbox,
    report: adapter.PreflightReport,
    counting: Counter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter._prepare_destination(box.root, report)  # every directory now exists (reused)
    real = os.fsync

    def failing(descriptor: int) -> None:
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise OSError("simulated sync failure")
        real(descriptor)

    with monkeypatch.context() as patch:
        patch.setattr(os, "fsync", failing)
        with pytest.raises(adapter.AdapterError, match="durability_not_established"):
            _run(box, write_authorization(box.root, report.digest))
    assert counting.calls == 0  # reused entries could not be synced: nothing was fitted
    markers = box.root / "artifacts/research/phase3cr_synthetic/executions"
    assert list(markers.iterdir()) == []  # and no execution was consumed either


def test_real_input_cannot_override_the_phase3b_configuration(sandbox: Sandbox) -> None:
    with pytest.raises(adapter.AdapterError, match="config_override_not_permitted_for_real_input"):
        _preflight(sandbox, _fixture_parent=None, phase3b_config_path=ROOT / "anything.json")


def test_fixture_config_override_is_part_of_the_snapshot_and_rechecked(
    box: Sandbox,
    report: adapter.PreflightReport,
    counting: Counter,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    default = ROOT / adapter.DEFAULT_PHASE3B_CONFIG
    override = tmp_path / "phase3b_override.json"
    override.write_bytes(default.read_bytes())
    same = _preflight(box, phase3b_config_path=override)
    assert same.input_snapshot_sha256 == report.input_snapshot_sha256
    override.write_text(default.read_text().replace("{", "{ ", 1))  # same JSON, different bytes
    changed = _preflight(box, phase3b_config_path=override)
    assert changed.input_snapshot_sha256 != report.input_snapshot_sha256
    assert changed.digest != report.digest

    def mutate(**_k: object) -> study.RetrospectiveStudyResult:
        counting.calls += 1
        override.write_text(override.read_text() + "\n")
        return counting.result

    monkeypatch.setattr(study, "_evaluate_retrospective_study", mutate)
    auth = write_authorization(box.root, changed.digest)
    with pytest.raises(adapter.AdapterError, match="runtime_or_inputs_changed_during_evaluation"):
        _run(box, auth, phase3b_config_path=override)
    assert counting.calls == 1


def test_checkout_change_after_the_fit_is_detected_and_nothing_is_published(
    box: Sandbox, report: adapter.PreflightReport, ran: Ran, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = {"dirty": False}
    evaluated: list[int] = []

    def attest(*_a: object, **_k: object) -> adapter.RuntimeAttestation:
        return adapter.RuntimeAttestation(True, COMMIT, not state["dirty"])

    def evaluate(**_k: object) -> study.RetrospectiveStudyResult:
        evaluated.append(1)
        state["dirty"] = True  # the checkout becomes dirty only after the fit has run
        return ran.result

    monkeypatch.setattr(adapter, "_collect_runtime_attestation", attest)
    monkeypatch.setattr(study, "_evaluate_retrospective_study", evaluate)
    with pytest.raises(adapter.AdapterError, match="runtime_or_inputs_changed_during_evaluation"):
        _run(box, write_authorization(box.root, report.digest))
    assert evaluated == [1]
    output = box.root / "artifacts/research/phase3cr_synthetic"
    assert not [p for p in output.iterdir() if p.name.startswith("aq-p3cr")]


# ---------------------------------------------------------------------------
# PR #102 final decisions: ambient GIT_* rejection and wrong-path manifest
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "variable", ["GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_CEILING_DIRECTORIES"]
)
@pytest.mark.parametrize("entry", ["preflight", "run"])
def test_ambient_git_environment_is_rejected_before_any_verification(
    box: Sandbox,
    report: adapter.PreflightReport,
    counting: Counter,
    read_log: list[str],
    monkeypatch: pytest.MonkeyPatch,
    variable: str,
    entry: str,
) -> None:
    auth = write_authorization(box.root, report.digest)
    loaded: list[int] = []
    attestations: list[int] = []
    real_load = adapter._load_authorization

    def tracked_load(*args: Any, **kwargs: Any) -> Any:
        loaded.append(1)
        return real_load(*args, **kwargs)

    def tracked_attestation(*_args: object, **_kwargs: object) -> adapter.RuntimeAttestation:
        attestations.append(1)
        return ATTESTED

    monkeypatch.setattr(adapter, "_load_authorization", tracked_load)
    monkeypatch.setattr(adapter, "_collect_runtime_attestation", tracked_attestation)
    monkeypatch.setenv(variable, str(box.root / "somewhere-else"))

    def invoke() -> object:
        return _preflight(box) if entry == "preflight" else _run(box, auth)

    with pytest.raises(adapter.AdapterError, match="ambient_git_environment_not_permitted"):
        invoke()
    assert read_log == []  # no context observation (or manifest) was opened
    assert counting.calls == 0
    assert loaded == []
    assert attestations == []  # rejected before any runtime probe or delegated verifier
    # State left by the rejected entry point itself: it never consumed its process claim.
    assert adapter._PROCESS == {"preflight": False, "run": False}


def test_the_clean_environment_requirement_is_documented() -> None:
    spec = (ROOT / "docs/AQ_P3C_R_RETROSPECTIVE_QQQ_IWM_SPEC.md").read_text()
    source = (ROOT / "src/spy_market_agent/research/retrospective_adapter.py").read_text()
    for text in (spec, source):
        assert "GIT_*" in text
    assert "manual" in spec
    assert "never retried, cleaned up or staged automatically" in source


def test_valid_manifest_at_a_wrong_but_wellformed_identity_path_is_rejected(
    box: Sandbox, read_log: list[str]
) -> None:
    original = box.root / box.qqq_manifest
    wrong = original.with_name(f"aq-ctx-v2-{'a' * 24}.manifest.json")
    assert wrong != original
    wrong.write_bytes(original.read_bytes())  # byte-identical, valid v2 manifest
    relative = wrong.relative_to(box.root)
    # structurally valid shape: the path gate passes and only the identity check can reject
    adapter._manifest_relative(box.root, relative, "QQQ")
    with pytest.raises(adapter.AdapterError, match="context_manifest_path_identity_mismatch"):
        _preflight(box, qqq_manifest=relative)
    assert read_log == [wrong.name]  # exactly one read: the manifest, never raw or canonical
