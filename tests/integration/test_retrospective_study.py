"""Synthetic-only verification of the AQ-P3C-R computation core."""

from __future__ import annotations

import ast
import dataclasses
import json
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from spy_market_agent.market_data.manifest import DatasetManifest
from spy_market_agent.market_data.models import MarketDataBatch
from spy_market_agent.research import retrospective_study as study
from spy_market_agent.research.campaign import ResearchCampaignConfig
from spy_market_agent.research.phase2_isolation import apply_phase2_final_test_session_isolation
from spy_market_agent.research.phase3c_reporting import CANDIDATE, MODEL_NAMES
from spy_market_agent.research.qqq_iwm_alignment import align_qqq_iwm_history
from spy_market_agent.research.qqq_iwm_policy import (
    BASELINE_COLUMNS,
    CANDIDATE_COLUMNS,
    QQQIWMFeatureHistory,
    SyntheticCloseSnapshot,
    SyntheticSourceProof,
    context_values,
)
from spy_market_agent.research.retrospective_context import (
    RetrospectiveContextSeries,
    RetrospectiveInputError,
    bind_context_series,
)
from spy_market_agent.research.runner import _load_verified_research_market_data
from unit.retrospective_helpers import CONTEXT_SEED, make_artifact
from unit.v2_phase2_helpers import write_synthetic_phase1_dataset

ROOT = Path(__file__).resolve().parents[2]


@dataclasses.dataclass
class World:
    manifest: DatasetManifest
    parent: MarketDataBatch
    boundary: dict[str, Any]
    sessions: tuple[date, ...]
    qqq: RetrospectiveContextSeries
    iwm: RetrospectiveContextSeries
    rights: study.OwnerRightsEvidence
    protocol: study._Protocol


def _rights(*series: RetrospectiveContextSeries) -> study.OwnerRightsEvidence:
    return study.OwnerRightsEvidence(
        "owner-local-entitlement-check-0001",
        tuple((s.symbol, s.dataset_id, s.canonical_sha256) for s in series),
    )


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> World:
    root = tmp_path_factory.mktemp("parent")
    path = write_synthetic_phase1_dataset(root, start=date(2018, 1, 2), end=date(2025, 12, 31))
    manifest, parent = _load_verified_research_market_data(
        manifest_path=path,
        data_root=Path("data"),
        repository_root=root,
        config=ResearchCampaignConfig(),
    )
    sliced, boundary = apply_phase2_final_test_session_isolation(
        manifest=manifest, market_data=parent, global_feature_warmup_rows=60
    )
    sessions = tuple(sliced.data["session"].to_list())
    qqq, iwm = (
        bind_context_series(*make_artifact(sym, sessions, CONTEXT_SEED + i))
        for i, sym in enumerate(("QQQ", "IWM"))
    )
    # Synthetic manifests cannot reproduce the real parent's identity; test seam only.
    protocol = study._Protocol(
        parent_dataset_id=manifest.dataset_id,
        parent_canonical_checksum=manifest.canonical_content_checksum,
    )
    return World(
        manifest,
        parent,
        boundary.model_dump(mode="json"),
        sessions,
        qqq,
        iwm,
        _rights(qqq, iwm),
        protocol,
    )


def _run(w: World, **overrides: Any) -> study.RetrospectiveStudyResult:
    args: dict[str, Any] = {
        "parent_manifest": w.manifest,
        "parent_market_data": w.parent,
        "accepted_boundary": w.boundary,
        "qqq": w.qqq,
        "iwm": w.iwm,
        "rights": w.rights,
        "protocol": w.protocol,
    }
    args.update(overrides)
    return study._evaluate_retrospective_study(**args)


@pytest.fixture(scope="module")
def reference(world: World) -> study.RetrospectiveStudyResult:
    return _run(world)


def test_frozen_population_folds_and_labels(
    world: World, reference: study.RetrospectiveStudyResult
) -> None:
    payload = json.loads(reference.payload_json)
    assert payload["dimensions"] == {
        "source_rows": 1708,
        "excluded_protected_source_rows": len(world.parent.data) - 1708,
        "supervised_rows": 1642,
        "fold_count": 7,
        "assessment_sizes": [126, 126, 126, 126, 126, 126, 88],
        "assessed_rows": 844,
        "unassessed_rows": 0,
    }
    assert world.boundary["eligible_source_first_session"] == "2018-01-02"
    assert world.boundary["eligible_source_last_session"] == "2024-10-15"
    assert world.boundary["eligible_development_first_prediction_session"] == "2018-03-29"
    assert world.boundary["eligible_development_last_prediction_session"] == "2024-10-07"
    seen: set[str] = set()
    for fold in payload["folds"]:
        member = fold["membership"]
        assert len(member["training_rows"]) >= 756
        assert len(member["purge_rows"]) == 6
        assert member["training_last_exit"] < member["assessment_first_prediction"]
        assert not seen & set(member["assessment_sessions"])
        seen |= set(member["assessment_sessions"])
        assert set(fold["models"]) == set(MODEL_NAMES)
    assert len(seen) == 844
    protected = world.boundary["phase2_final_test_prediction_sessions"]
    every = {
        s
        for f in payload["folds"]
        for k, v in f["membership"].items()
        if k.endswith("sessions")
        for s in v
    }
    assert not every & set(protected)


def test_result_is_labelled_retrospective_and_non_authoritative(
    reference: study.RetrospectiveStudyResult,
) -> None:
    assert reference.evidence_kind == "retrospective_corrected"
    assert reference.point_in_time_status == reference.usage_rights_status == "UNVERIFIED"
    assert reference.execution_authority == "none"
    assert reference.disposition == "NO CANDIDATE PROMOTION"
    payload = json.loads(reference.payload_json)
    assert payload["execution_authority"] == "none"
    assert payload["disposition"] == "NO CANDIDATE PROMOTION"
    assert payload["screen"]["exploratory_screen"] in ("CRITERIA_MET", "CRITERIA_NOT_MET")
    for phrase in ("cannot be attributed purely", "linearly dependent", "UNVERIFIED", "Phase 3B"):
        assert phrase in payload["limitations"]
    models = payload["lineage"]["models"]
    assert tuple(models[CANDIDATE]["feature_columns"]) == CANDIDATE_COLUMNS
    assert tuple(models["gradient_boosting"]["feature_columns"]) == BASELINE_COLUMNS
    assert (
        models[CANDIDATE]["definition"] == models["logistic_regression_research_00"]["definition"]
    )
    params = dict(models[CANDIDATE]["definition"]["parameters"])
    assert (params["classifier.C"], params["classifier.penalty"]) == (0.1, "l2")
    assert params["classifier.solver"] == "liblinear"
    assert params["classifier.class_weight"] is None
    assert params["classifier.random_state"] == 42
    sources = payload["lineage"]["runtime"]["source_checksums"]
    for needed in (
        "intelligence/context_features.py",
        "research/phase3c_reporting.py",
        "research/phase2_isolation.py",
        "research/retrospective_study.py",
        "research/retrospective_context.py",
    ):
        assert needed in sources
    assert payload["lineage"]["owner_rights_reference"] == "owner-local-entitlement-check-0001"
    assert {c["point_in_time_status"] for c in payload["lineage"]["context"]} == {"UNVERIFIED"}


def test_experiment_is_reproducible_and_bound_to_inputs(
    world: World, reference: study.RetrospectiveStudyResult
) -> None:
    again = _run(world)
    assert again == reference
    assert again.result_checksum == reference.result_checksum
    other_rights = _rights(world.qqq, world.iwm)
    other_rights = dataclasses.replace(
        other_rights, reference_id="owner-local-entitlement-check-0002"
    )
    assert _run(world, rights=other_rights).experiment_id != reference.experiment_id
    altered, _ = make_artifact("QQQ", world.sessions, CONTEXT_SEED + 99)
    qqq2 = bind_context_series(altered, make_artifact("QQQ", world.sessions, CONTEXT_SEED + 99)[1])
    moved = _run(world, qqq=qqq2, rights=_rights(qqq2, world.iwm))
    assert moved.experiment_id != reference.experiment_id


def test_context_features_match_3ca_math_and_use_only_past_closes(world: World) -> None:
    spy = world.parent.data["close"].to_numpy(dtype="float64")[:1708]
    positions = np.asarray([60, 61, 700, 1700])
    series = {"qqq-daily": world.qqq, "iwm-daily": world.iwm}
    frame = study._context_features(spy, series, positions)
    sessions = world.sessions
    for row, p in enumerate(positions):
        snaps = tuple(
            SyntheticCloseSnapshot(sid, sessions[p - 20 : p + 1], tuple(vals[p - 20 : p + 1]))
            for sid, vals in (
                ("SPY", spy.tolist()),
                ("qqq-daily", world.qqq.closes),
                ("iwm-daily", world.iwm.closes),
            )
        )
        assert tuple(frame.iloc[row]) == context_values(snaps[0], snaps[1:])
    # Mutating every close after each prediction day cannot change its features.
    future = dataclasses.replace(
        world.qqq, closes=world.qqq.closes[:701] + (1e9,) * (1708 - 701), _origin=world.qqq._origin
    )
    later = study._context_features(
        spy, {"qqq-daily": future, "iwm-daily": world.iwm}, positions[:3]
    )
    assert later.equals(frame.iloc[:3])


@pytest.mark.parametrize("case", ["protected_extra", "short", "shifted"])
def test_context_must_cover_exactly_the_eligible_sessions(world: World, case: str) -> None:
    from unit.retrospective_helpers import xnys_sessions

    sessions = {
        "protected_extra": xnys_sessions(world.sessions[0], date(2024, 10, 16)),
        "short": world.sessions[1:],
        "shifted": world.sessions[:-1],
    }[case]
    qqq = bind_context_series(*make_artifact("QQQ", sessions, 3))
    iwm = bind_context_series(*make_artifact("IWM", sessions, 4))
    with pytest.raises(study.RetrospectiveStudyError, match="context_sessions_differ"):
        _run(world, qqq=qqq, iwm=iwm, rights=_rights(qqq, iwm))


def test_parent_and_boundary_lineage_fail_closed(world: World) -> None:
    with pytest.raises(study.RetrospectiveStudyError, match="parent_lineage_mismatch"):
        _run(world, protocol=study._Protocol())  # frozen real IDs differ from the fixture
    with pytest.raises(study.RetrospectiveStudyError, match="accepted_boundary_mismatch"):
        _run(world, accepted_boundary={**world.boundary, "research_slice_checksum": "0" * 64})
    stale = dict(world.boundary)
    stale.pop("excluded_source_session_count")
    with pytest.raises(study.RetrospectiveStudyError, match="accepted_boundary_mismatch"):
        _run(world, accepted_boundary=stale)


def test_frozen_constants_follow_the_owner_disposition() -> None:
    assert study.PARENT_DATASET_ID == "spy-v2p1-900ca75371bf05e25b1935b5"
    assert study.PARENT_CANONICAL_CHECKSUM.startswith("3fbaed8976d4aa1b")
    assert study.PARENT_CAMPAIGN_ID == "spy-v2p3-dev-1f9a68f3dce8df02e3645468"
    assert "825930b0" not in json.dumps(dataclasses.asdict(study._Protocol()))
    phase3b = json.loads((ROOT / "configs/research/phase3b_nonoverlapping.json").read_text())
    assert phase3b["parent_dataset_id"] == study.PARENT_DATASET_ID
    assert phase3b["parent_canonical_checksum"] == study.PARENT_CANONICAL_CHECKSUM
    assert phase3b["parent_campaign_id"] == study.PARENT_CAMPAIGN_ID


def test_rights_evidence_must_be_separate_and_bound(world: World) -> None:
    with pytest.raises(study.RetrospectiveStudyError, match="owner_rights"):
        _run(world, rights=_rights(world.qqq))
    with pytest.raises(study.RetrospectiveStudyError, match="owner_rights"):
        _run(world, rights=_rights(world.iwm, world.qqq))
    with pytest.raises(RetrospectiveInputError):
        study.OwnerRightsEvidence("short", ())
    with pytest.raises(RetrospectiveInputError):
        study.OwnerRightsEvidence("owner-local-entitlement-check-0001", [])  # type: ignore[arg-type]


def test_synthetic_proofs_and_swapped_series_are_not_accepted(world: World) -> None:
    sessions = world.sessions[:21]
    snapshot = SyntheticCloseSnapshot("qqq-daily", sessions, world.qqq.closes[:21])
    with pytest.raises(study.RetrospectiveStudyError, match="context_series_invalid"):
        _run(world, qqq=snapshot)
    with pytest.raises(study.RetrospectiveStudyError, match="context_series_invalid"):
        _run(world, qqq=world.iwm, iwm=world.qqq)


def test_strict_pit_paths_reject_retrospective_evidence(world: World) -> None:
    sessions = world.sessions[:21]
    with pytest.raises(ValueError, match="real_data_eligibility_blocked"):
        SyntheticCloseSnapshot(
            "qqq-daily",
            sessions,
            world.qqq.closes[:21],
            evidence_kind="retrospective_corrected",  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="close_schema_mismatch"):
        SyntheticCloseSnapshot("qqq-daily", sessions, world.qqq.closes[:21], schema_id="aq-p3cr")
    proof_fields = {f.name for f in dataclasses.fields(SyntheticSourceProof)}
    assert "evidence_kind" in proof_fields
    with pytest.raises(ValueError, match="real_data_eligibility_blocked"):
        QQQIWMFeatureHistory("c" * 64, "v", (), evidence_kind="retrospective_corrected")  # type: ignore[arg-type]
    assert align_qqq_iwm_history.__module__.endswith("qqq_iwm_alignment")


def test_no_public_real_input_runner_exists() -> None:
    public = [n for n in dir(study) if not n.startswith("_") and callable(getattr(study, n))]
    assert not [n for n in public if n.startswith(("run_", "load_", "acquire", "write_"))]
    assert study._evaluate_retrospective_study.__name__.startswith("_")


def test_computation_has_no_network_credential_or_io_side_effects(
    world: World, monkeypatch: pytest.MonkeyPatch, reference: study.RetrospectiveStudyResult
) -> None:
    def forbidden(*_a: object, **_k: object) -> None:
        raise AssertionError("forbidden side effect")

    monkeypatch.setattr("socket.socket", forbidden)
    monkeypatch.setattr("socket.create_connection", forbidden)
    monkeypatch.setattr("pandas.read_csv", forbidden)
    monkeypatch.setattr("pandas.read_parquet", forbidden)
    assert _run(world) == reference


def test_new_modules_have_no_credential_provider_broker_or_io_dependencies() -> None:
    forbidden = (
        "spy_market_agent.market_data.alpaca",
        "spy_market_agent.market_data.acquisition",
        "spy_market_agent.market_data.context_acquisition",
        "spy_market_agent.market_data.context_storage",
        "spy_market_agent.market_data.context_transport",
        "spy_market_agent.market_data.pipeline",
        "spy_market_agent.execution",
        "spy_market_agent.broker",
        "alpaca",
        "requests",
        "httpx",
        "socket",
        "os",
    )
    for name in ("retrospective_study", "retrospective_context"):
        path = ROOT / "src/spy_market_agent/research" / f"{name}.py"
        tree = ast.parse(path.read_text())
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        assert not [m for m in imported for f in forbidden if m == f or m.startswith(f + ".")]
        text = path.read_text()
        assert "getenv" not in text
        assert "environ" not in text
        assert "read_csv" not in text


def test_fresh_process_import_touches_no_secrets_or_network() -> None:
    code = (
        "import socket, ssl, asyncio, sklearn, pandas\n"
        "def f(*a,**k): raise AssertionError('forbidden')\n"
        "socket.socket=f; socket.create_connection=f\n"
        "import spy_market_agent.research.retrospective_study as s\n"
        "assert not [n for n in dir(s) if n.startswith(('run_','load_'))]\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=ROOT,
        env={"PYTHONPATH": str(ROOT / "src"), "PATH": ""},
    )
    assert done.returncode == 0, done.stderr


def test_weakened_or_unrelated_modules_are_unchanged() -> None:
    # Guardrail: the 3C-A/3C-B contracts remain strictly synthetic-only.
    from spy_market_agent.research import phase3c, qqq_iwm_policy

    assert qqq_iwm_policy.POLICY_ID == "aq-p3ca-qqq-iwm-synthetic-eligibility-v1"
    assert phase3c.RUNNER_ID == "aq-p3cb-synthetic-comparison-v1"
    assert not hasattr(phase3c, "RetrospectiveStudyResult")
