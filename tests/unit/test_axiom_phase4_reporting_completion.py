from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime
from inspect import getsource
from pathlib import Path
from typing import Any

import pytest

import spy_market_agent.supervision.reporting as reporting
from spy_market_agent.benchmark.artifacts import sha256_bytes
from spy_market_agent.research.errors import ResearchRegistryError
from spy_market_agent.supervision.disposition import HumanReviewDisposition
from spy_market_agent.supervision.memory import SupervisionMemoryRegistry
from spy_market_agent.supervision.review_queue import SupervisedReviewStatus
from unit.test_axiom_phase4_supervision_session import _phase3_result


def _run_workflow(tmp_path: Path, *, actionable: bool) -> Any:
    store, phase3 = _phase3_result(tmp_path, actionable=actionable)
    result = reporting.run_supervised_operations_workflow(
        report=phase3.report,
        invocation_id="phase4-completion",
        disposition=(
            HumanReviewDisposition.OBSERVED
            if actionable
            else HumanReviewDisposition.ABSTENTION_ACKNOWLEDGED
        ),
        human_review_reference="phase4-review",
        recorded_at=datetime(2026, 10, 7, 8, 30, tzinfo=UTC),
        store=store,
    )
    return store, phase3, result


def test_supervised_workflow_round_trips_pending_human_review(tmp_path: Path) -> None:
    """The explicit human-review path persists and verifies the complete Phase 4 chain."""

    store, phase3, result = _run_workflow(tmp_path, actionable=True)
    memory = SupervisionMemoryRegistry(store)

    assert result.session.report == phase3.report
    assert result.review_item.review_status == SupervisedReviewStatus.PENDING_HUMAN_REVIEW
    assert result.disposition.disposition == HumanReviewDisposition.OBSERVED
    assert result.execution_authority == "none"
    assert result.report.execution_authority == "none"
    assert (
        memory.load_session(
            result.session.experiment_id,
            result.session.supervision_session_id,
        )
        == result.session
    )
    assert (
        memory.load_review_item(
            result.review_item.experiment_id,
            result.review_item.review_item_id,
        )
        == result.review_item
    )
    assert (
        memory.load_disposition(
            result.disposition.experiment_id,
            result.disposition.disposition_id,
        )
        == result.disposition
    )
    content = reporting.load_supervision_report(result.report, registry=memory)
    assert content == reporting.render_supervision_report(result.disposition)
    assert "pending_human_review" in content
    assert "observed" in content
    assert "Execution authority remains `none`." in content


def test_supervised_workflow_preserves_abstention(tmp_path: Path) -> None:
    """A Phase 3 abstention stays non-reviewable and can only be acknowledged."""

    _, _, result = _run_workflow(tmp_path, actionable=False)

    assert result.review_item.review_status == SupervisedReviewStatus.NON_REVIEWABLE_ABSTENTION
    assert result.disposition.disposition == HumanReviewDisposition.ABSTENTION_ACKNOWLEDGED
    content = reporting.render_supervision_report(result.disposition)
    assert "non_reviewable_abstention" in content
    assert "abstention_acknowledged" in content
    assert "remains an abstention" in content


def test_supervised_workflow_rejects_abstention_upgrade(tmp_path: Path) -> None:
    """The end-to-end workflow cannot convert an abstention into another disposition."""

    store, phase3 = _phase3_result(tmp_path, actionable=False)
    with pytest.raises(ValueError, match="may only be acknowledged"):
        reporting.run_supervised_operations_workflow(
            report=phase3.report,
            invocation_id="phase4-abstention",
            disposition=HumanReviewDisposition.OBSERVED,
            human_review_reference="phase4-review",
            recorded_at=datetime(2026, 10, 7, 8, 30, tzinfo=UTC),
            store=store,
        )


def test_supervision_reporting_is_deterministic_and_idempotent(tmp_path: Path) -> None:
    """Identical canonical inputs yield identical identities, bytes, and stored artifacts."""

    store, phase3 = _phase3_result(tmp_path, actionable=True)
    first = reporting.run_supervised_operations_workflow(
        report=phase3.report,
        invocation_id="phase4-deterministic",
        disposition=HumanReviewDisposition.DEFERRED,
        human_review_reference="phase4-review",
        recorded_at=datetime(2026, 10, 7, 8, 31, tzinfo=UTC),
        store=store,
    )
    second = reporting.run_supervised_operations_workflow(
        report=phase3.report,
        invocation_id="phase4-deterministic",
        disposition=HumanReviewDisposition.DEFERRED,
        human_review_reference="phase4-review",
        recorded_at=datetime(2026, 10, 7, 8, 31, tzinfo=UTC),
        store=store,
    )

    assert first == second
    assert first.report.report_id == second.report.report_id
    assert first.report.checksum == second.report.checksum


def test_supervision_report_rejects_tampered_bytes(tmp_path: Path) -> None:
    """Checksum verification makes report-byte tampering fail closed."""

    store, _, result = _run_workflow(tmp_path, actionable=True)
    altered = b"tampered supervision report\n"
    store.write_bytes(
        result.disposition.experiment_id,
        reporting.supervision_report_name(result.disposition),
        altered,
        expected_checksum=sha256_bytes(altered),
        allow_replace=True,
    )
    with pytest.raises(ResearchRegistryError, match="checksum"):
        reporting.load_supervision_report(
            result.report,
            registry=SupervisionMemoryRegistry(store),
        )


def test_supervision_report_rejects_tampered_parent(tmp_path: Path) -> None:
    """Reload fails if the stored disposition parent is corrupted after reporting."""

    store, _, result = _run_workflow(tmp_path, actionable=True)
    memory = SupervisionMemoryRegistry(store)
    store.write_json(
        result.disposition.experiment_id,
        memory._disposition_name(result.disposition.disposition_id),
        {"broken": True},
        allow_replace=True,
    )
    with pytest.raises(ResearchRegistryError, match="canonical validation"):
        reporting.load_supervision_report(result.report, registry=memory)


def test_supervision_report_artifact_rejects_noncanonical_identity(tmp_path: Path) -> None:
    """The public report artifact cannot carry a substituted content identity."""

    _, _, result = _run_workflow(tmp_path, actionable=True)
    payload = result.report.model_dump(mode="python")
    payload["report_id"] = "aq-supervision-report-" + ("0" * 24)
    with pytest.raises(ValueError, match="derived from the exact report checksum"):
        reporting.SupervisionReportArtifact.model_validate(payload)


def test_supervision_import_order_is_cycle_safe() -> None:
    """Research-first and intelligence-first imports remain cycle-free after Slice 5."""

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import spy_market_agent.research; "
                "import spy_market_agent.intelligence; "
                "import spy_market_agent.supervision; "
                "import spy_market_agent.supervision.reporting"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_supervision_reporting_module_has_no_operational_authority() -> None:
    """Slice 5 introduces reporting and orchestration only, not execution machinery."""

    source = getsource(reporting)
    forbidden = (
        "spy_market_agent.execution",
        "spy_market_agent.paper_ops",
        "TradingClient",
        "submit_order",
        "position_target",
        "broker_client",
        "schedule.every",
        "BackgroundScheduler",
        "send_notification",
    )
    for marker in forbidden:
        assert marker not in source
