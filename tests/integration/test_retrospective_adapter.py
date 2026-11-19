"""Synthetic-only verification of the AQ-P3C-R verified-data adapter."""

from __future__ import annotations

import ast
import json
import os
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
    clone,
    write_authorization,
)

ROOT = Path(__file__).resolve().parents[2]
ATTESTED = adapter.RuntimeAttestation(isolated=True, git_commit=COMMIT, clean=True)


def _attested(*_args: object, **_kwargs: object) -> adapter.RuntimeAttestation:
    return ATTESTED


@pytest.fixture(autouse=True)
def fresh_process_state(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """pytest is not an isolated, pinned process: attest explicitly, one process per test."""
    monkeypatch.setattr(adapter, "_collect_runtime_attestation", _attested)
    adapter._PROCESS["used"] = False
    yield
    adapter._PROCESS["used"] = False


@pytest.fixture(scope="module")
def sandbox(tmp_path_factory: pytest.TempPathFactory) -> Sandbox:
    return build_sandbox(tmp_path_factory.mktemp("sandbox"))


def _preflight(box: Sandbox, **overrides: Any) -> adapter.PreflightReport:
    adapter._PROCESS["used"] = False
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
    adapter._PROCESS["used"] = False
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
        mp.setattr(study, "_evaluate_retrospective_study", capture)
        record = _run(box, write_authorization(box.root, report.digest))
    return Ran(box, record, captured[0])


@pytest.fixture
def stub_evaluation(monkeypatch: pytest.MonkeyPatch, ran: Ran) -> None:
    monkeypatch.setattr(study, "_evaluate_retrospective_study", lambda **_k: ran.result)


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
    v1 = box.root / "data/manifests/alpaca/QQQ/1Day/sip/all" / f"aq-ctx-v1-{'0' * 24}.manifest.json"
    v1.write_text(json.dumps({"schema_version": "aq-qqq-iwm-context-acquisition-v1"}))
    relative = v1.relative_to(box.root)
    with pytest.raises(adapter.AdapterError, match="context_manifest_unreadable_or_not_v2"):
        _preflight(box, qqq_manifest=relative)
    with pytest.raises(adapter.AdapterError, match="context_source_policy_mismatch"):
        _preflight(box, qqq_manifest=box.iwm_manifest, iwm_manifest=box.qqq_manifest)
    with pytest.raises(adapter.AdapterError, match="context_source_policy_mismatch"):
        _preflight(box, iwm_manifest=box.qqq_manifest)
    assert all(name.endswith(".manifest.json") for name in read_log)


def test_manifest_tampering_and_path_identity_are_rejected(box: Sandbox) -> None:
    path = box.root / box.qqq_manifest
    data = json.loads(path.read_text())
    data["row_count"] = 1707
    path.write_text(json.dumps(data))
    with pytest.raises(adapter.AdapterError, match="context_scope_differs_from_approved_range"):
        _preflight(box)
    clean = clone(box, box.root.parent / "second")
    path2 = clean.root / clean.qqq_manifest
    path2.write_text(path2.read_text().replace("\n", "") + " ")
    with pytest.raises(adapter.AdapterError):
        _preflight(clean)


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
    with pytest.raises(adapter.AdapterError, match="exclusion_boundary_differs"):
        _preflight(box)


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
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
    subprocess.run([*git, "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "a.txt").write_text("a")
    subprocess.run([*git, "-C", str(tmp_path), "add", "a.txt"], check=True)
    subprocess.run([*git, "-C", str(tmp_path), "commit", "-q", "-m", "x"], check=True)
    head = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"], capture_output=True, text=True
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


@pytest.mark.usefixtures("stub_evaluation")
def test_booleans_or_missing_rights_evidence_cannot_authorize(box: Sandbox) -> None:
    directory = box.root / ".local"
    directory.mkdir(exist_ok=True)
    for name, body in (
        ("bool.json", {"authorized": True}),
        ("empty.json", {}),
        ("norights.json", {"schema_version": "aq-p3cr-experiment-authorization-v1"}),
    ):
        path = directory / name
        path.write_text(json.dumps(body))
        path.chmod(0o600)
        with pytest.raises(adapter.AdapterError, match="authorization_invalid"):
            _run(box, path)


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

    def mutate(**_k: object) -> study.RetrospectiveStudyResult:
        target.write_bytes(target.read_bytes() + b" ")
        return ran.result

    monkeypatch.setattr(study, "_evaluate_retrospective_study", mutate)
    with pytest.raises(adapter.AdapterError, match="runtime_or_inputs_changed_during_evaluation"):
        _run(box, auth)
    assert not (box.root / "artifacts/research/phase3cr_synthetic").exists()

    calls: list[int] = []

    def flaky(*_a: object, **_k: object) -> adapter.RuntimeAttestation:
        calls.append(1)
        return ATTESTED if len(calls) == 1 else adapter.RuntimeAttestation(True, COMMIT, False)

    monkeypatch.setattr(study, "_evaluate_retrospective_study", lambda **_k: ran.result)
    monkeypatch.setattr(adapter, "_collect_runtime_attestation", flaky)
    with pytest.raises(adapter.AdapterError, match="runtime_or_inputs_changed_during_evaluation"):
        _run(clean, write_authorization(clean.root, report.digest))


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
    with pytest.raises(adapter.AdapterError, match="output_already_exists_no_overwrite"):
        _run(ran.box, auth)
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
    assert not (box.root / "artifacts/research/phase3cr_synthetic").exists()


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
    for needle in ("environ", "getenv", "acquire_historical", "StockHistorical"):
        assert needle not in text
    assert "api_key" in adapter.FORBIDDEN_OUTPUT_KEYS


def test_run_has_no_network_side_effects(
    box: Sandbox, report: adapter.PreflightReport, ran: Ran, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*_a: object, **_k: object) -> None:
        raise AssertionError("no network access is allowed")

    monkeypatch.setattr("socket.socket", forbidden)
    monkeypatch.setattr("socket.create_connection", forbidden)
    monkeypatch.setattr(study, "_evaluate_retrospective_study", lambda **_k: ran.result)
    record = _run(box, write_authorization(box.root, report.digest))
    assert record.experiment_id == ran.record.experiment_id
    assert (record.output_directory / "artifact_index.json").read_bytes() == (
        ran.record.output_directory / "artifact_index.json"
    ).read_bytes()
