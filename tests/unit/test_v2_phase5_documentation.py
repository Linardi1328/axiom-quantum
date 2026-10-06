from __future__ import annotations

from pathlib import Path

import spy_market_agent

ROOT = Path(__file__).resolve().parents[2]


def test_phase4_release_transition_is_documented() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    roadmap = (ROOT / "FUTURE_ROADMAP.md").read_text(encoding="utf-8")
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    phase4_spec = (ROOT / "docs/V2_PHASE_04_REAL_TIME_SHADOW_MODE_SPEC.md").read_text(
        encoding="utf-8"
    )
    phase4_evidence = (ROOT / "docs/V2_PHASE_04_BETA1_RELEASE_EVIDENCE.md").read_text(
        encoding="utf-8"
    )
    release_notes = (ROOT / "RELEASE_NOTES_V2.0.0_BETA_1.md").read_text(encoding="utf-8")
    release_commit = "1c8feae478c0f5536b2193eeb408e580f3f7e33c"

    assert spy_market_agent.__version__ == "2.0.0b1"
    assert "Current released identifier: `v2.0.0-beta.1`" in readme
    assert "V2 Phase 4: accepted and released as `v2.0.0-beta.1`" in readme
    assert f"Release commit: `{release_commit}`" in phase4_evidence
    assert f"Release commit: `{release_commit}`" in release_notes
    assert f"Release commit: `{release_commit}`" in roadmap
    assert "Public release identifier: `v2.0.0-beta.1` has been tagged and released" in (
        phase4_evidence
    )
    assert "Status: Accepted and released as `v2.0.0-beta.1`" in phase4_spec
    assert "Public tag: `v2.0.0-beta.1` CREATED" in phase4_spec
    assert "Public `v2.0.0-beta.2` release/tag: not yet created" in readme
    assert "no `v2.0.0-beta.2` tag exists" in changelog


def test_phase5_specification_and_runbook_are_documented() -> None:
    spec_path = ROOT / "docs/V2_PHASE_05_PRODUCTION_PAPER_OPERATION_SPEC.md"
    runbook_path = ROOT / "docs/V2_PHASE_05_PAPER_RECOVERY_RUNBOOK.md"
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    roadmap = (ROOT / "FUTURE_ROADMAP.md").read_text(encoding="utf-8")
    architecture = (ROOT / "docs/ARCHITECTURE.md").read_text(encoding="utf-8")
    reproducibility = (ROOT / "docs/REPRODUCIBILITY.md").read_text(encoding="utf-8")
    workflows = (ROOT / "docs/WORKFLOWS.md").read_text(encoding="utf-8")
    safety = (ROOT / "docs/SECURITY_AND_SAFETY.md").read_text(encoding="utf-8")
    spec = spec_path.read_text(encoding="utf-8")
    runbook = runbook_path.read_text(encoding="utf-8")
    combined = "\n".join(
        (readme, roadmap, architecture, reproducibility, workflows, safety, spec, runbook)
    )

    assert spec_path.is_file()
    assert runbook_path.is_file()
    assert "Target future release identifier: `v2.0.0-beta.2`" in spec
    assert "Current Phase 5 branch: `review/v2-phase-05-production-paper`" in spec
    assert "Status: Specification + non-submitting safety/recovery scaffold active" in spec
    assert "Current package/runtime version: `2.0.0b1`" in spec
    assert "Public `v2.0.0-beta.2` tag: NOT CREATED" in spec
    assert "Version 2 Phase 5 Production Paper Operation Specification" in readme
    assert "Version 2 Phase 5 Paper Recovery Runbook" in readme
    assert "ACTIVE - specification + non-submitting safety/recovery scaffold" in roadmap
    assert "spy_market_agent.paper_ops" in architecture
    assert "reserved` and `submission_unknown` require reconciliation" in reproducibility
    assert "Review Phase 5 Paper-Operation Recovery State" in workflows
    assert "uncertainty never means retry the order" in safety
    assert "uncertainty never means retry the order" in runbook.lower()
    assert "GATE P5-A - Infrastructure Entry" in spec
    assert "GATE P5-B - Broker Paper Submission By Phase 5 Workflow" in spec
    assert "GATE P5-C - Model-Connected Paper Operation" in spec
    assert "Status: `AUTHORIZED`" in spec
    assert "Status: `BLOCKED PENDING SEPARATE OWNER AUTHORIZATION`" in spec
    assert "Status: `BLOCKED_NO_APPROVED_PAPER_MODEL`" in spec

    for boundary in (
        "No real broker calls",
        "No credentials",
        "No real broker calls",
        "No scheduler",
        "No model inference",
        "No protected evaluation",
        "No live trading",
    ):
        assert boundary.lower() in combined.lower()


def test_phase5_spec_documents_recovery_and_failure_matrices() -> None:
    spec = (ROOT / "docs/V2_PHASE_05_PRODUCTION_PAPER_OPERATION_SPEC.md").read_text(
        encoding="utf-8"
    )

    for status in (
        "`reserved`",
        "`submission_unknown`",
        "`accepted`",
        "`broker_existing_order_found`",
        "`reconciled`",
        "`blocked`",
        "`rejected`",
        "unknown/unrecognized",
    ):
        assert status in spec

    for disposition in (
        "`RECONCILIATION_REQUIRED`",
        "`NO_ACTION_TERMINAL`",
        "`BLOCKED`",
        "`INVALID_STATE`",
    ):
        assert disposition in spec

    for failure_case in (
        "invalid approval",
        "expired approval",
        "kill switch engaged",
        "wrong execution mode",
        "broker environment mismatch",
        "account blocked",
        "wrong account configuration",
        "market closed",
        "wrong session",
        "stale instruction",
        "unsupported symbol",
        "unexpected position",
        "unexpected open order",
        "risk rejection",
        "duplicate attempt",
        "same-session collision",
        "database lock/failure",
        "pre-submission clock failure",
        "broker request construction failure",
        "broker rejection",
        "transport uncertainty",
        "cancellation during submission",
        "unknown submission outcome",
        "broker snapshot mismatch",
        "accepted order receipt persistence failure",
        "reconciliation lookup failure",
        "reconciliation mismatch",
        "reconciliation persistence failure",
    ):
        assert failure_case in spec

    assert "For every uncertain case: FAIL CLOSED." in spec
    assert "No Phase 5 recovery disposition may mean" in spec
