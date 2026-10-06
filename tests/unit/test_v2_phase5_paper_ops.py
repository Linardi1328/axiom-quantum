from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import spy_market_agent
from spy_market_agent.execution.models import (
    PAPER_ATTEMPT_ACCEPTED,
    PAPER_ATTEMPT_BLOCKED,
    PAPER_ATTEMPT_BROKER_EXISTING_ORDER_FOUND,
    PAPER_ATTEMPT_RECONCILED,
    PAPER_ATTEMPT_REJECTED,
    PAPER_ATTEMPT_RESERVED,
    PAPER_ATTEMPT_STATES,
    PAPER_ATTEMPT_SUBMISSION_UNKNOWN,
)
from spy_market_agent.paper_ops import (
    PAPER_ATTEMPT_RECOVERY_DISPOSITIONS,
    PHASE5_KNOWN_PAPER_ATTEMPT_STATES,
    PaperRecoveryDisposition,
    Phase5PaperGateStatus,
    classify_paper_attempt_recovery,
    evaluate_phase5_broker_submission_gate,
    evaluate_phase5_infrastructure_gate,
    evaluate_phase5_model_connected_paper_gate,
    evaluate_phase5_readiness,
)
from spy_market_agent.paper_ops.gates import (
    PHASE5_BRANCH,
    PHASE5_CURRENT_PACKAGE_VERSION,
    PHASE5_TARGET_RELEASE,
)

ROOT = Path(__file__).resolve().parents[2]
PAPER_OPS_ROOT = ROOT / "src/spy_market_agent/paper_ops"


def test_phase5_gate_statuses_are_explicit() -> None:
    infrastructure = evaluate_phase5_infrastructure_gate()
    broker_submission = evaluate_phase5_broker_submission_gate()
    model_connected = evaluate_phase5_model_connected_paper_gate()

    assert spy_market_agent.__version__ == "2.0.0b1"
    assert PHASE5_CURRENT_PACKAGE_VERSION == "2.0.0b1"
    assert PHASE5_TARGET_RELEASE == "v2.0.0-beta.2"
    assert PHASE5_BRANCH == "review/v2-phase-05-production-paper"
    assert infrastructure.gate == "P5-A"
    assert infrastructure.status is Phase5PaperGateStatus.AUTHORIZED
    assert infrastructure.allowed is True
    assert broker_submission.gate == "P5-B"
    assert broker_submission.status is (
        Phase5PaperGateStatus.BLOCKED_PENDING_SEPARATE_OWNER_AUTHORIZATION
    )
    assert broker_submission.allowed is False
    assert model_connected.gate == "P5-C"
    assert model_connected.status is Phase5PaperGateStatus.BLOCKED_NO_APPROVED_PAPER_MODEL
    assert model_connected.allowed is False


def test_caller_metadata_cannot_self_authorize_blocked_phase5_gates() -> None:
    caller_claims = {
        "approved": True,
        "approved_for_paper": True,
        "model_status": "approved",
        "owner_approved": True,
        "broker_submission_allowed": True,
    }

    broker_submission = evaluate_phase5_broker_submission_gate(caller_claims)
    model_connected = evaluate_phase5_model_connected_paper_gate(caller_claims)
    readiness = evaluate_phase5_readiness(
        broker_metadata=caller_claims,
        model_metadata=caller_claims,
    )

    assert broker_submission.allowed is False
    assert model_connected.allowed is False
    assert readiness[0].allowed is True
    assert readiness[1].allowed is False
    assert readiness[2].allowed is False
    assert readiness[1].status is (
        Phase5PaperGateStatus.BLOCKED_PENDING_SEPARATE_OWNER_AUTHORIZATION
    )
    assert readiness[2].status is Phase5PaperGateStatus.BLOCKED_NO_APPROVED_PAPER_MODEL


def test_recovery_classification_covers_every_existing_paper_attempt_state() -> None:
    assert set(PHASE5_KNOWN_PAPER_ATTEMPT_STATES) == set(PAPER_ATTEMPT_STATES)
    assert set(PAPER_ATTEMPT_RECOVERY_DISPOSITIONS) == set(PAPER_ATTEMPT_STATES)

    reconciliation_states = (PAPER_ATTEMPT_RESERVED, PAPER_ATTEMPT_SUBMISSION_UNKNOWN)
    terminal_states = (
        PAPER_ATTEMPT_ACCEPTED,
        PAPER_ATTEMPT_BROKER_EXISTING_ORDER_FOUND,
        PAPER_ATTEMPT_RECONCILED,
    )
    blocked_states = (PAPER_ATTEMPT_BLOCKED, PAPER_ATTEMPT_REJECTED)

    for status in reconciliation_states:
        decision = classify_paper_attempt_recovery(status)
        assert decision.disposition is PaperRecoveryDisposition.RECONCILIATION_REQUIRED
        assert decision.requires_client_order_id_lookup is True
        assert decision.broker_submission_allowed is False
        assert decision.automatic_resubmission_allowed is False

    for status in terminal_states:
        decision = classify_paper_attempt_recovery(status)
        assert decision.disposition is PaperRecoveryDisposition.NO_ACTION_TERMINAL
        assert decision.requires_client_order_id_lookup is False
        assert decision.broker_submission_allowed is False
        assert decision.automatic_resubmission_allowed is False

    for status in blocked_states:
        decision = classify_paper_attempt_recovery(status)
        assert decision.disposition is PaperRecoveryDisposition.BLOCKED
        assert decision.requires_client_order_id_lookup is False
        assert decision.broker_submission_allowed is False
        assert decision.automatic_resubmission_allowed is False


def test_unknown_recovery_state_fails_closed() -> None:
    decision = classify_paper_attempt_recovery("unexpected_new_state")

    assert decision.disposition is PaperRecoveryDisposition.INVALID_STATE
    assert decision.broker_submission_allowed is False
    assert decision.automatic_resubmission_allowed is False
    assert decision.requires_client_order_id_lookup is False


def test_phase5_paper_ops_static_isolation() -> None:
    package_text = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(PAPER_OPS_ROOT.glob("*.py"))
    )
    forbidden_fragments = (
        "alpaca.trading",
        "TradingClient",
        "AlpacaPaperBroker",
        "PaperBrokerProtocol",
        "submit_order",
        "submit_market_day_order",
        "submit_approved_order",
        "APCA_API_KEY_ID",
        "APCA_API_SECRET_KEY",
        "ALPACA_MARKET_DATA_API_KEY",
        "ALPACA_MARKET_DATA_SECRET_KEY",
        "APScheduler",
        "Celery",
        "RQ",
        "while True",
        "time.sleep",
        "paper=False",
        "predict_proba",
        ".predict(",
    )

    for fragment in forbidden_fragments:
        assert fragment not in package_text


def test_importing_paper_ops_has_no_broker_settings_or_filesystem_side_effect(
    tmp_path: Path,
) -> None:
    script = (
        "import os, pathlib, sys; "
        f"os.chdir({str(tmp_path)!r}); "
        "before=set(pathlib.Path('.').iterdir()); "
        "import spy_market_agent.paper_ops as paper_ops; "
        "after=set(pathlib.Path('.').iterdir()); "
        "assert before == after; "
        "assert 'alpaca.trading.client' not in sys.modules; "
        "assert 'spy_market_agent.execution.alpaca_paper' not in sys.modules; "
        "assert 'spy_market_agent.execution.service' not in sys.modules; "
        "assert 'spy_market_agent.execution.protocols' not in sys.modules; "
        "assert paper_ops.evaluate_phase5_infrastructure_gate().allowed is True"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
