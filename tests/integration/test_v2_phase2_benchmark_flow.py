from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from spy_market_agent.benchmark import cli as benchmark_cli
from spy_market_agent.benchmark.artifacts import canonical_json_bytes, sha256_bytes
from spy_market_agent.benchmark.dataset import record_feed_availability
from spy_market_agent.benchmark.errors import BenchmarkArtifactError, BenchmarkFinalTestAccessError
from spy_market_agent.benchmark.locks import BenchmarkRole
from spy_market_agent.benchmark.pipeline import (
    finalize_lock,
    prepare_benchmark,
    run_final_test,
    run_validation,
)
from spy_market_agent.benchmark.verification import verify_benchmark_directory
from unit.v2_phase2_helpers import SYNTHETIC_NOW, write_synthetic_phase1_dataset


def _prepared_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    manifest_path = write_synthetic_phase1_dataset(tmp_path)
    record_feed_availability(
        provider="alpaca",
        requested_feed="sip",
        symbol="SPY",
        timeframe="1Day",
        adjustment_mode="all",
        requested_start=date(2016, 1, 4),
        requested_end=date(2025, 12, 31),
        probe_timestamp=SYNTHETIC_NOW,
        success=True,
        owner_acknowledgement=True,
        evidence_source_description="synthetic owner-provided offline probe record",
        output=Path("feed.json"),
    )
    lock = prepare_benchmark(
        manifest_path=manifest_path,
        feed_record_path=tmp_path / "feed.json",
        benchmark_role=BenchmarkRole.PRIMARY,
        latest_complete_research_year=2025,
        artifact_root=Path("artifacts/benchmarks"),
        owner_approve_assumptions=True,
        repository_root=tmp_path,
    )
    return tmp_path / "artifacts" / "benchmarks" / lock.benchmark_id / "benchmark_lock.json"


def test_synthetic_manifest_to_prepare_validation_final_and_audit_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock_path = _prepared_lock(tmp_path, monkeypatch)
    benchmark_root = lock_path.parent

    verify_benchmark_directory(benchmark_root, repository_root=tmp_path)
    validation = run_validation(benchmark_lock_path=lock_path)
    assert validation.selected_model_name in {"logistic_regression", "gradient_boosting"}
    assert validation.regime_results is not None
    assert validation.regime_results.partition_name == "validation"
    assert not (benchmark_root / "final_test_results.json").exists()
    assert not (benchmark_root / "regime_results.json").exists()

    final_lock = finalize_lock(
        benchmark_lock_path=lock_path,
        acknowledge_final_test_policy=True,
    )
    final_lock_path = benchmark_root / "final_test_lock.json"
    assert final_lock.selected_model_name == validation.selected_model_name

    final_results = run_final_test(
        final_test_lock_path=final_lock_path,
        acknowledge_final_test_access=True,
    )
    assert final_results["selected_model_name"] == validation.selected_model_name
    assert (benchmark_root / "final_test_access.json").exists()
    assert (benchmark_root / "final_test_completion.json").exists()
    assert (benchmark_root / "cost_sensitivity.json").exists()
    assert (benchmark_root / "regime_results.json").exists()
    access = json.loads((benchmark_root / "final_test_access.json").read_text(encoding="utf-8"))
    completion = json.loads(
        (benchmark_root / "final_test_completion.json").read_text(encoding="utf-8")
    )
    assert access["access_state"] == "started"
    assert completion["completed_state"] == "completed"
    assert completion["access_record_checksum"] == sha256_bytes(
        (benchmark_root / "final_test_access.json").read_bytes()
    )
    completed_verification = verify_benchmark_directory(
        benchmark_root,
        repository_root=tmp_path,
        require_runtime_reproduction=True,
    )
    assert completed_verification.passed is True

    with pytest.raises(BenchmarkFinalTestAccessError):
        run_final_test(
            final_test_lock_path=final_lock_path,
            acknowledge_final_test_access=True,
        )
    access_checksum_before = sha256_bytes((benchmark_root / "final_test_access.json").read_bytes())

    replay = run_final_test(
        final_test_lock_path=final_lock_path,
        acknowledge_final_test_access=False,
        audit_replay=True,
    )
    assert replay["audit_replay"] == "passed"
    assert sha256_bytes((benchmark_root / "final_test_access.json").read_bytes()) == (
        access_checksum_before
    )


def test_cli_stage_a_commands_and_verify(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    manifest_path = write_synthetic_phase1_dataset(tmp_path)

    assert (
        benchmark_cli.main(
            [
                "record-feed-decision",
                "--provider",
                "alpaca",
                "--feed",
                "sip",
                "--symbol",
                "SPY",
                "--timeframe",
                "1Day",
                "--adjustment",
                "all",
                "--start",
                "2016-01-04",
                "--end",
                "2025-12-31",
                "--probe-timestamp",
                SYNTHETIC_NOW.isoformat(),
                "--available",
                "--evidence-source",
                "synthetic owner-provided offline probe record",
                "--owner-acknowledge",
                "--output",
                "feed.json",
            ]
        )
        == 0
    )
    assert "available=true" in capsys.readouterr().out

    assert (
        benchmark_cli.main(
            [
                "prepare",
                "--manifest",
                str(manifest_path),
                "--feed-record",
                "feed.json",
                "--benchmark-role",
                "primary",
                "--latest-complete-research-year",
                "2025",
                "--artifact-root",
                "artifacts/benchmarks",
                "--owner-approve-assumptions",
            ]
        )
        == 0
    )
    prepare_output = capsys.readouterr().out.splitlines()
    lock_path = Path(prepare_output[1].split("=", 1)[1])
    benchmark_root = lock_path.parent

    assert benchmark_cli.main(["validate", "--benchmark-lock", str(lock_path)]) == 0
    assert "benchmark_id=" in capsys.readouterr().out
    assert benchmark_cli.main(["run-validation", "--benchmark-lock", str(lock_path)]) == 0
    assert "selected_model=" in capsys.readouterr().out
    assert (
        benchmark_cli.main(
            [
                "finalize-lock",
                "--benchmark-lock",
                str(lock_path),
                "--acknowledge-final-test-policy",
            ]
        )
        == 0
    )
    assert "final_test_lock=" in capsys.readouterr().out
    assert (
        benchmark_cli.main(
            [
                "verify",
                "--benchmark-root",
                str(benchmark_root),
                "--require-runtime-lineage",
            ]
        )
        == 0
    )
    assert "verification=passed" in capsys.readouterr().out


def test_final_test_before_final_lock_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock_path = _prepared_lock(tmp_path, monkeypatch)

    with pytest.raises(BenchmarkFinalTestAccessError):
        run_final_test(
            final_test_lock_path=lock_path.parent / "final_test_lock.json",
            acknowledge_final_test_access=True,
        )


def test_corrupted_artifact_fails_verification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock_path = _prepared_lock(tmp_path, monkeypatch)
    (lock_path.parent / "benchmark_lock.sha256").write_text("bad\n", encoding="utf-8")

    with pytest.raises(BenchmarkArtifactError):
        verify_benchmark_directory(lock_path.parent, repository_root=tmp_path)


def test_semantic_tampering_fails_when_artifact_index_is_recomputed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock_path = _prepared_lock(tmp_path, monkeypatch)
    benchmark_root = lock_path.parent
    run_validation(benchmark_lock_path=lock_path)
    baselines_path = benchmark_root / "classification_baselines.json"
    baselines = json.loads(baselines_path.read_text(encoding="utf-8"))
    baselines["always_negative"]["model_name"] = "tampered_baseline"
    baselines_path.write_bytes(canonical_json_bytes(baselines))

    index_path = benchmark_root / "artifact_index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    index["artifacts"]["classification_baselines.json"]["sha256"] = sha256_bytes(
        baselines_path.read_bytes()
    )
    index_path.write_bytes(canonical_json_bytes(index))

    with pytest.raises(BenchmarkArtifactError):
        verify_benchmark_directory(benchmark_root, repository_root=tmp_path)
