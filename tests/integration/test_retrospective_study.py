"""Synthetic-only verification of the AQ-P3C-R computation core."""

from __future__ import annotations

import ast
import dataclasses
import inspect
import json
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from spy_market_agent.datasets.models import LABEL_SCHEMA_VERSION
from spy_market_agent.intelligence.context import MI2A_REQUIRED_CONTEXT_IDS
from spy_market_agent.intelligence.context_features import (
    MI2B_CONTEXT_FEATURE_POLICY_ID,
    MI2B_SPY_CONTEXT_FEATURE_DEFINITIONS,
    ContextFeatureValue,
    SPYContextFeatureBundle,
)
from spy_market_agent.intelligence.legacy_spy import LEGACY_SPY_SERIES_ID
from spy_market_agent.market_data.checksum import compute_market_data_checksum
from spy_market_agent.market_data.manifest import DatasetManifest
from spy_market_agent.market_data.models import MarketDataBatch
from spy_market_agent.research import retrospective_study as study
from spy_market_agent.research.binary_context_preflight import (
    BinaryContextStatus,
    ContextSourceProof,
    assess_binary_context_preflight,
)
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
    fixture: study._FixtureIdentity


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
    # Synthetic manifests cannot reproduce the real parent's identity; the seam replaces
    # identity strings only and switches the whole run to the synthetic evidence class.
    fixture = study._FixtureIdentity(
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
        fixture,
    )


def _run(w: World, **overrides: Any) -> study.RetrospectiveStudyResult:
    args: dict[str, Any] = {
        "parent_manifest": w.manifest,
        "parent_market_data": w.parent,
        "accepted_boundary": w.boundary,
        "qqq": w.qqq,
        "iwm": w.iwm,
        "rights": w.rights,
        "fixture_identity": w.fixture,
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
    assert reference.evidence_kind == "synthetic_fixture"
    assert reference.experiment_id.startswith("aq-p3cr-synth-")
    assert not reference.experiment_id.startswith("aq-p3cr-retro-")
    assert reference.point_in_time_status == reference.usage_rights_status == "UNVERIFIED"
    assert reference.execution_authority == "none"
    assert reference.disposition == "NO CANDIDATE PROMOTION"
    payload = json.loads(reference.payload_json)
    assert payload["evidence_kind"] == payload["lineage"]["evidence_kind"] == "synthetic_fixture"
    assert "Not observed retrospective research" in payload["synthetic_notice"]
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
    series = {"qqq-daily": world.qqq.derived(), "iwm-daily": world.iwm.derived()}
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
    base = world.qqq.derived()
    future = base._replace(closes=base.closes[:701] + (1e9,) * (1708 - 701))
    later = study._context_features(
        spy, {"qqq-daily": future, "iwm-daily": world.iwm.derived()}, positions[:3]
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
        _run(world, fixture_identity=None)  # the real accepted IDs differ from the fixture
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
    assert "825930b0" not in json.dumps(dict(study._FROZEN_CONFIGURATION))
    assert "825930b0" not in study.PARENT_DATASET_ID
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
        # context_acquisition/context_transport are imported only for their pure, existing
        # canonical-format helpers (no client is constructed, no network call is possible
        # from this code); the provider, storage-writing and pipeline modules stay forbidden.
        "spy_market_agent.market_data.alpaca_provider",
        "spy_market_agent.market_data.context_storage",
        "spy_market_agent.market_data.pipeline",
        "spy_market_agent.research.binary_context_preflight",
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
        assert "ContextSourceProof" not in text  # cannot manufacture strict-PIT proofs
        assert "getenv" not in text
        assert "environ" not in text
        assert "read_csv" not in text


def test_fresh_process_import_touches_no_secrets_or_network() -> None:
    code = (
        "import socket, ssl, asyncio, sklearn, pandas, requests, urllib3\n"
        "import alpaca.common.exceptions\n"
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


# ---------------------------------------------------------------------------
# PR #101 remediation: H1 content binding, H2 frozen configuration/evidence class,
# H4 parent integrity, M1 lineage, M2 strict-PIT isolation.
# ---------------------------------------------------------------------------


def test_substituted_context_cannot_keep_the_original_identity(
    world: World, reference: study.RetrospectiveStudyResult
) -> None:
    # dataclasses.replace cannot inject closes/sessions/flags/identity: they are not fields.
    for forged in ("closes", "sessions", "dataset_id", "canonical_sha256", "_origin"):
        bad: dict[str, Any] = {forged: object()}
        with pytest.raises(TypeError):
            dataclasses.replace(world.qqq, **bad)
    # Tampering after construction is caught when the evaluator re-derives from the pair.
    victim = bind_context_series(world.qqq.manifest, world.qqq.canonical_bytes)
    _, other_bytes = make_artifact("QQQ", world.sessions, CONTEXT_SEED + 5)
    object.__setattr__(victim, "canonical_bytes", other_bytes)
    with pytest.raises(RetrospectiveInputError, match="canonical_checksum_mismatch"):
        _run(world, qqq=victim)
    # A genuinely different (resealed) artifact has a different identity and experiment ID.
    other = bind_context_series(*make_artifact("QQQ", world.sessions, CONTEXT_SEED + 5))
    assert other.dataset_id != world.qqq.dataset_id
    changed = _run(world, qqq=other, rights=_rights(other, world.iwm))
    assert changed.experiment_id != reference.experiment_id
    payload = json.loads(changed.payload_json)
    assert {c["effective_data_checksum"] for c in payload["lineage"]["context"]} != {
        c["effective_data_checksum"]
        for c in json.loads(reference.payload_json)["lineage"]["context"]
    }


def test_fixture_seam_changes_identity_strings_only(world: World) -> None:
    assert {f.name for f in dataclasses.fields(study._FixtureIdentity)} == {
        "parent_dataset_id",
        "parent_canonical_checksum",
        "parent_campaign_id",
    }
    parameters = inspect.signature(study._evaluate_retrospective_study).parameters
    assert "protocol" not in parameters
    assert not {"seed", "costs", "folds", "thresholds"} & set(parameters)
    assert not hasattr(study, "_Protocol")
    with pytest.raises(TypeError):
        study._FROZEN_CONFIGURATION["random_seed"] = 7  # type: ignore[index]
    with pytest.raises(study.RetrospectiveStudyError, match="fixture_identity_must_differ"):
        study._FixtureIdentity(study.PARENT_DATASET_ID, "f" * 64)
    with pytest.raises(study.RetrospectiveStudyError, match="fixture_identity_must_differ"):
        study._FixtureIdentity("spy-v2p1-" + "0" * 24, study.PARENT_CANONICAL_CHECKSUM)
    # Without the seam the accepted identity is mandatory, so fixtures cannot pose as real.
    with pytest.raises(study.RetrospectiveStudyError, match="parent_lineage_mismatch"):
        _run(world, fixture_identity=None)


def test_recorded_configuration_is_exactly_what_was_executed(
    world: World, monkeypatch: pytest.MonkeyPatch, reference: study.RetrospectiveStudyResult
) -> None:
    seen: dict[str, Any] = {}

    def spy(name: str, attribute: str) -> None:
        real = getattr(study, attribute)

        def wrapper(*args: Any, **kwargs: Any) -> Any:
            seen.setdefault(name, []).append(kwargs)
            return real(*args, **kwargs)

        monkeypatch.setattr(study, attribute, wrapper)

    spy("folds", "construct_disjoint_folds")
    spy("labels", "build_forward_label_set")
    spy("isolation", "apply_phase2_final_test_session_isolation")
    spy("features", "build_research_feature_matrix")
    spy("registry", "development_model_registry")
    result = _run(world)
    assert result == reference  # spies are call-through
    config = json.loads(result.payload_json)["lineage"]["configuration"]
    assert config == json.loads(json.dumps(dict(study._FROZEN_CONFIGURATION)))
    (folds,) = seen["folds"]
    for key in ("initial_training_rows", "purge_rows", "assessment_rows", "minimum_partial_rows"):
        assert folds[key] == config[key]
    (labels,) = seen["labels"]
    assert (
        str(labels["cost_assumptions"].commission_bps_per_side) == config["commission_bps_per_side"]
    )
    assert str(labels["cost_assumptions"].slippage_bps_per_side) == config["slippage_bps_per_side"]
    assert (
        seen["isolation"][0]["global_feature_warmup_rows"] == config["global_feature_warmup_rows"]
    )
    assert seen["features"][0]["global_feature_warmup_rows"] == config["global_feature_warmup_rows"]
    assert {k["random_seed"] for k in seen["registry"]} == {config["random_seed"]}
    from spy_market_agent.datasets.models import ENTRY_OFFSET_SESSIONS, EXIT_OFFSET_SESSIONS
    from spy_market_agent.research import phase3c_reporting

    assert (config["entry_offset_sessions"], config["exit_offset_sessions"]) == (
        ENTRY_OFFSET_SESSIONS,
        EXIT_OFFSET_SESSIONS,
    )
    score_source = inspect.getsource(phase3c_reporting._score)
    assert f"threshold={config['classification_threshold']}" in score_source
    assert f"reliability_bin_count={config['reliability_bin_count']}" in score_source
    phase3b = json.loads((ROOT / "configs/research/phase3b_nonoverlapping.json").read_text())
    for key in ("initial_training_rows", "purge_rows", "assessment_rows", "minimum_partial_rows"):
        assert phase3b[key] == config[key]
    assert phase3b["random_seed"] == config["random_seed"]
    assert phase3b["threshold"] == config["classification_threshold"]


def _parent_with(world: World, data: Any, **metadata: Any) -> MarketDataBatch:
    updated = world.parent.metadata.model_copy(update=metadata)
    if metadata:
        return MarketDataBatch(data=data, metadata=updated)
    # The batch validator would reject stale metadata, so emulate in-place DataFrame mutation
    # of an already-validated batch (the model documents that the frame is not immutable).
    return MarketDataBatch.model_construct(data=data, metadata=updated)


@pytest.mark.parametrize("row", [100, 1700, 1900])
def test_mutated_parent_rows_with_stale_metadata_are_rejected(world: World, row: int) -> None:
    # Row 100 and 1700 are eligible development rows; 1900 is a protected (excluded) row.
    assert row < len(world.parent.data)
    assert (row < 1708) == (row in (100, 1700))
    data = world.parent.data.copy(deep=True)
    data.loc[row, "volume"] = int(data.loc[row, "volume"]) + 1
    tampered = _parent_with(world, data)  # metadata checksum is now stale
    with pytest.raises(study.RetrospectiveStudyError, match="parent_market_data_checksum_mismatch"):
        _run(world, parent_market_data=tampered)


@pytest.mark.parametrize("row", [100, 1900])
def test_mutated_parent_with_refreshed_metadata_still_disagrees_with_accepted_boundary(
    world: World, row: int
) -> None:
    data = world.parent.data.copy(deep=True)
    data.loc[row, "volume"] = int(data.loc[row, "volume"]) + 1
    refreshed = _parent_with(world, data, dataset_checksum=compute_market_data_checksum(data))
    with pytest.raises(study.RetrospectiveStudyError, match="parent_market_data_checksum_mismatch"):
        _run(world, parent_market_data=refreshed)
    stale_boundary = {**world.boundary, "parent_canonical_market_data_checksum": "0" * 64}
    with pytest.raises(study.RetrospectiveStudyError, match="parent_market_data_checksum_mismatch"):
        _run(world, accepted_boundary=stale_boundary)


def test_exact_source_and_prediction_dates_are_enforced(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert (
        date(2018, 1, 2),
        date(2024, 10, 15),
    ) == (study.EXPECTED_SOURCE_FIRST, study.EXPECTED_SOURCE_LAST)
    assert (
        date(2018, 3, 29),
        date(2024, 10, 7),
    ) == (study.EXPECTED_PREDICTION_FIRST, study.EXPECTED_PREDICTION_LAST)
    for name, shifted in (
        ("EXPECTED_SOURCE_FIRST", date(2018, 1, 3)),
        ("EXPECTED_SOURCE_LAST", date(2024, 10, 14)),
        ("EXPECTED_PREDICTION_FIRST", date(2018, 3, 28)),
        ("EXPECTED_PREDICTION_LAST", date(2024, 10, 8)),
    ):
        with monkeypatch.context() as patch:
            patch.setattr(study, name, shifted)
            with pytest.raises(study.RetrospectiveStudyError, match="frozen_population_mismatch"):
                _run(world)


def test_lineage_covers_the_transitive_code_and_configuration(
    world: World, reference: study.RetrospectiveStudyResult, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = json.loads(reference.payload_json)["lineage"]["runtime"]
    checksums = runtime["source_checksums"]
    for needed in (
        "market_data/calendar.py",  # NYSE calendar
        "benchmark/splits.py",  # Phase 2 split definitions
        "intelligence/context_features.py",  # feature models
        "research/retrospective_study.py",
        "research/retrospective_context.py",
        "research/phase2_isolation.py",
        "research/phase3c_reporting.py",
        "research/qqq_iwm_policy.py",
    ):
        assert needed in checksums
    assert any(name.startswith("benchmark/") and "lock" in name for name in checksums)
    for name, digest in checksums.items():
        path = ROOT / "src/spy_market_agent" / name
        assert study.sha256_bytes(path.read_bytes()) == digest
    versions = runtime["dependency_versions"]
    for dependency in ("numpy", "pandas", "scikit-learn", "exchange_calendars", "pydantic"):
        assert dependency in versions
    config_file = "configs/research/phase3b_nonoverlapping.json"
    assert runtime["configuration_file_checksums"][config_file] == study.sha256_bytes(
        (ROOT / config_file).read_bytes()
    )
    # Changing any covered file's checksum, or a dependency version, changes the identity.
    original_checksums, original_versions = study._transitive_lineage()
    for patched in (
        ({**original_checksums, "market_data/calendar.py": "0" * 64}, original_versions),
        (original_checksums, {**original_versions, "numpy": "0.0.0"}),
    ):
        with monkeypatch.context() as patch:
            patch.setattr(study, "_transitive_lineage", lambda patched=patched: patched)
            assert _run(world).experiment_id != reference.experiment_id


def test_lineage_includes_every_module_loaded_by_importing_the_study() -> None:
    code = (
        "import sys, json\n"
        "import spy_market_agent.research.retrospective_study as s\n"
        "mods = sorted(m for m in sys.modules if m.startswith('spy_market_agent.'))\n"
        "checks, _ = s._transitive_lineage()\n"
        "print(json.dumps({'mods': mods, 'files': sorted(checks)}))\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=ROOT,
        env={"PYTHONPATH": str(ROOT / "src"), "PATH": ""},
    )
    assert done.returncode == 0, done.stderr
    loaded = json.loads(done.stdout)
    covered = set(loaded["files"])
    missing = []
    for module in loaded["mods"]:
        relative = module.removeprefix("spy_market_agent.").replace(".", "/")
        if not {relative + ".py", relative + "/__init__.py"} & covered:
            missing.append(module)
    assert not missing


def _bundle_for(session: date, cutoff: datetime, snapshots: tuple[str, ...]) -> Any:
    as_of = cutoff - timedelta(minutes=1)
    by_id = dict(zip(MI2A_REQUIRED_CONTEXT_IDS, snapshots, strict=True))
    features = tuple(
        ContextFeatureValue(
            policy_id=MI2B_CONTEXT_FEATURE_POLICY_ID,
            feature_id=definition.feature_id,
            methodology_id=definition.methodology_id,
            source_series_id=definition.source_series_id,
            source_snapshot_id=by_id[definition.source_series_id],
            target_snapshot_id="synthetic-spy",
            anchor_session=session,
            as_of=as_of,
            lookback_sessions=definition.lookback_sessions,
            unit=definition.unit,
            value=0.1,
        )
        for definition in MI2B_SPY_CONTEXT_FEATURE_DEFINITIONS
    )
    return SPYContextFeatureBundle(
        policy_id=MI2B_CONTEXT_FEATURE_POLICY_ID,
        as_of=as_of,
        anchor_session=session,
        target_series_id=LEGACY_SPY_SERIES_ID,
        target_snapshot_id="synthetic-spy",
        context_snapshot_ids=snapshots,
        features=features,
    )


def test_retrospective_and_synthetic_evidence_never_pass_the_real_mi2b_preflight(
    world: World,
) -> None:
    """Run the actual MI-2B binary preflight with the only truthful proofs we can derive."""
    session = world.sessions[900]
    cutoff = datetime(session.year, session.month, session.day, 22, 0, tzinfo=UTC)
    snapshots = tuple(f"snapshot-{i}" for i in range(len(MI2A_REQUIRED_CONTEXT_IDS)))
    bundle = _bundle_for(session, cutoff, snapshots)
    series = {"qqq-daily": world.qqq, "iwm-daily": world.iwm}
    assert set(series) <= set(MI2A_REQUIRED_CONTEXT_IDS)
    proofs = []
    for series_id, snapshot in zip(MI2A_REQUIRED_CONTEXT_IDS, snapshots, strict=True):
        item = series.get(series_id)
        if item is None:  # unrelated context sources: fully verified, so they add no reasons
            proofs.append(
                ContextSourceProof(
                    series_id, snapshot, "a" * 64, cutoff - timedelta(minutes=2), True, True
                )
            )
            continue
        manifest = item.manifest
        proofs.append(
            ContextSourceProof(
                series_id=series_id,
                snapshot_id=snapshot,
                checksum_sha256=manifest.canonical_sha256,
                # The only availability time the artifact itself can attest is retrieval.
                available_as_of=manifest.retrieval_completed_at,
                quality_verified=str(manifest.point_in_time_status) == "VERIFIED",
                usage_rights_verified=str(manifest.usage_rights_status) == "VERIFIED",
            )
        )
    result = assess_binary_context_preflight(
        bundle=bundle,
        label_schema=LABEL_SCHEMA_VERSION,
        prediction_session=session,
        signal_cutoff=cutoff,
        source_proofs=tuple(proofs),
    )
    assert result.status == BinaryContextStatus.INELIGIBLE
    assert {
        "unverified_context_quality",
        "unverified_context_usage_rights",
        "source_available_after_signal_cutoff",
    } <= set(result.reasons)
    assert result.values == ()
    assert result.execution_authority == "none"
    # The study's own output class is never an input to that gate and carries no authority.
    assert json.loads(_run(world).payload_json)["execution_authority"] == "none"
