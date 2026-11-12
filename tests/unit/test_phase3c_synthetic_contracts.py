"""Pure fixture/report contracts and static isolation of the synthetic runner."""

from __future__ import annotations

import ast
import inspect
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from spy_market_agent.benchmark.artifacts import sha256_bytes, sha256_json
from spy_market_agent.research import phase3c, phase3c_fixture, phase3c_reporting
from spy_market_agent.research.phase3c_fixture import _generate_fixture
from spy_market_agent.research.phase3c_reporting import (
    MODEL_NAMES,
    _differences,
    _score,
    _summarize,
)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("generator_id", "unknown"),
        ("seed", 7),
        ("evidence_kind", "real"),
        ("sessions", ()),
        ("spy_rows", []),
        ("qqq_closes", ()),
        ("iwm_closes", ()),
    ],
)
def test_invalid_fixture_identity_and_dimensions(field: str, value: Any) -> None:
    changes: dict[str, Any] = {field: value}
    fixture = replace(_generate_fixture(), **changes)
    with pytest.raises(
        ValueError,
        match=r"invalid_synthetic_generator_identity|invalid_synthetic_fixture_dimensions",
    ):
        fixture.validate()


@pytest.mark.parametrize("mode", ["ohlc", "volume", "nan", "row_shape", "context", "calendar"])
def test_incoherent_synthetic_source_is_rejected(mode: str) -> None:
    fixture = _generate_fixture()
    rows = list(fixture.spy_rows)
    changes: dict[str, Any] = {}
    if mode == "calendar":
        changes["sessions"] = (fixture.sessions[0], *fixture.sessions[:-1])
    elif mode == "context":
        changes["qqq_closes"] = (float("inf"), *fixture.qqq_closes[1:])
    else:
        rows[0] = {
            "ohlc": (100.0, 50.0, 90.0, 100.0, 1000),
            "volume": (100.0, 101.0, 99.0, 100.0, 1.5),
            "nan": (float("nan"), 101.0, 99.0, 100.0, 1000),
            "row_shape": (100.0,),
        }[mode]  # type: ignore[assignment]
        changes["spy_rows"] = tuple(rows)
    with pytest.raises(
        ValueError,
        match=r"invalid_synthetic_ohlcv|invalid_synthetic_context_close|unordered_or_duplicate_sessions",
    ):
        replace(fixture, **changes).validate()


def test_all_undefined_assessment_aggregates_and_recall() -> None:
    scores = {
        name: _score(name, "synthetic-one-class", [0, 0, 0], [0.5, 0.6, 0.7], 0.4)
        for name in MODEL_NAMES
    }
    summary = _summarize((scores, scores, scores))
    for model in summary["models"].values():
        auc = model["roc_auc"]
        assert auc["median"]["value"] is None
        assert auc["median"]["undefined_reason"] == "metric_undefined_for_all_folds"
        assert auc["defined_fold_count"] == 0
        assert auc["undefined_fold_count"] == 3
    for score in scores.values():
        assert score.metrics["positive_recall"].value is None
        assert score.metrics["negative_recall"].value == 0.0
        assert score.metrics["balanced_accuracy"].value is None
        assert score.metrics["log_loss"].value is not None
    for control in _differences(scores).values():
        assert control["roc_auc"].value is None
        assert control["roc_auc"].undefined_reason == "candidate_or_control_metric_undefined"


def test_public_runner_has_no_arbitrary_inputs_and_modules_have_no_io_adapters() -> None:
    assert not inspect.signature(phase3c.run_synthetic_experiment).parameters
    forbidden_imports = (
        "alpaca",
        "provider",
        "credential",
        "execution",
        "shadow",
        "paper",
        "persistence",
    )
    forbidden_calls = {
        "read_csv",
        "read_parquet",
        "read_pickle",
        "write_text",
        "write_bytes",
        "dump",
        "save",
        "getenv",
    }
    for module in (phase3c, phase3c_fixture, phase3c_reporting):
        tree = ast.parse(inspect.getsource(module))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert not any(part in (node.module or "") for part in forbidden_imports)
                assert node.module not in {
                    "spy_market_agent.research.runner",
                    "spy_market_agent.research.phase3b",
                }
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in forbidden_calls
    with pytest.raises(TypeError, match="positional argument"):
        phase3c.run_synthetic_experiment("arbitrary-data.csv")  # type: ignore[call-arg]


def test_runner_completes_with_external_capabilities_disabled() -> None:
    # Existing package __init__ eagerly exports legacy declarations. Establish
    # that baseline first, then assert this runner adds no external dependency
    # and can execute while the corresponding capabilities are forbidden.
    script = r"""
import json
import os
import sys
from typing import NoReturn
from unittest.mock import patch
import spy_market_agent.research
prefixes = ('alpaca', 'spy_market_agent.execution', 'spy_market_agent.paper_ops',
            'spy_market_agent.shadow', 'spy_market_agent.market_data.acquisition',
            'spy_market_agent.research.runner', 'spy_market_agent.persistence')
before = {name for name in sys.modules if name.startswith(prefixes)}
from spy_market_agent.research.phase3c import run_synthetic_experiment
assert {name for name in sys.modules if name.startswith(prefixes)} == before
original_getenv = os.getenv

def forbidden(*args: object, **kwargs: object) -> NoReturn:
    raise AssertionError('external capability called by synthetic runner')

def guarded_getenv(key: str, default: str | None = None) -> str | None:
    assert not key.startswith(('ALPACA', 'APCA_', 'AWS_', 'ENABLE_PAPER', 'EXECUTION_'))
    return original_getenv(key, default)

with patch('socket.socket.connect', forbidden), \
     patch('pandas.read_csv', forbidden), \
     patch('pandas.read_parquet', forbidden), \
     patch('os.getenv', guarded_getenv), \
     patch('spy_market_agent.market_data.acquisition.require_market_data_credentials', forbidden), \
     patch('spy_market_agent.market_data.pipeline.acquire_historical_spy_data', forbidden), \
     patch('spy_market_agent.research.runner._load_verified_research_market_data', forbidden):
    result = run_synthetic_experiment()
assert result.evidence_kind == 'synthetic_fixture'
assert result.execution_authority == 'none'
assert {name for name in sys.modules if name.startswith(prefixes)} == before
print(json.dumps({'isolated': True}))
"""
    result = subprocess.run(
        [sys.executable, "-c", script], check=True, capture_output=True, text=True
    )
    assert json.loads(result.stdout) == {"isolated": True}


def test_unavailable_git_lineage_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*_args: Any, **_kwargs: Any) -> Any:
        raise OSError("synthetic missing git")

    monkeypatch.setattr(phase3c.subprocess, "run", fail)
    with pytest.raises(
        phase3c.SyntheticExperimentError, match="synthetic_code_lineage_unavailable"
    ):
        phase3c._runtime_lineage()


def test_code_lineage_reads_only_known_source_files() -> None:
    lineage = phase3c._runtime_lineage()
    assert lineage["python"].startswith("3.12.")
    assert lineage["dependency_versions"]["spy-market-agent"]
    assert "intelligence/context_features.py" in lineage["source_checksums"]
    for path, checksum in lineage["source_checksums"].items():
        assert not Path(path).is_absolute()
        assert path.endswith(".py")
        assert len(checksum) == 64


def test_uncommitted_context_definition_changes_lineage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    relative_path = "intelligence/context_features.py"
    source_path = Path(phase3c.__file__).resolve().parents[1] / relative_path
    original_read_bytes = Path.read_bytes
    original = original_read_bytes(source_path)
    modified = original.replace(b"lookback_sessions=5,", b"lookback_sessions=6,", 1)
    assert modified != original
    before = phase3c._runtime_lineage()
    assert before["source_checksums"][relative_path] == sha256_bytes(original)

    def read_bytes(path: Path) -> bytes:
        if path == source_path:
            return modified
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    after = phase3c._runtime_lineage()
    assert after["git_commit"] == before["git_commit"]
    assert after["source_checksums"] == {
        **before["source_checksums"],
        relative_path: sha256_bytes(modified),
    }
    assert sha256_json(after) != sha256_json(before)
