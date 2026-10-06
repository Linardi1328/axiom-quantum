from __future__ import annotations

import asyncio
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import SecretStr

from spy_market_agent.config import Settings
from spy_market_agent.execution import (
    DISENGAGE_KILL_SWITCH_CONFIRMATION,
    PAPER_ATTEMPT_ACCEPTED,
    PAPER_ATTEMPT_BLOCKED,
    PAPER_ATTEMPT_BROKER_EXISTING_ORDER_FOUND,
    PAPER_ATTEMPT_RECONCILED,
    PAPER_ATTEMPT_REJECTED,
    PAPER_ATTEMPT_SUBMISSION_UNKNOWN,
    PaperExecutionApprovalError,
    PaperExecutionBrokerRejectionError,
    PaperExecutionBrokerRequestError,
    PaperExecutionBrokerStateError,
    PaperExecutionConfigurationError,
    PaperExecutionDuplicateError,
    PaperExecutionError,
    PaperExecutionIntegrityError,
    PaperExecutionKillSwitchError,
    PaperExecutionPermissionError,
    PaperExecutionService,
    PaperExecutionStaleSignalError,
    PaperExecutionSubmissionUnknownError,
    SQLitePaperExecutionRepository,
)
from spy_market_agent.execution.models import (
    BrokerAccountConfigurationSnapshot,
    BrokerAccountSnapshot,
    BrokerClockSnapshot,
    BrokerEnvironmentSnapshot,
    BrokerOpenOrderSnapshot,
    BrokerPositionSnapshot,
    PaperOrderApproval,
    PaperOrderInstruction,
    PaperOrderReceipt,
)
from spy_market_agent.persistence import initialize_database
from spy_market_agent.risk import SELL_SIDE
from unit.phase8_helpers import (
    BROKER_TIME,
    CLIENT_ORDER_ID,
    FakePaperBroker,
    make_approval,
    make_broker_order_snapshot,
    make_instruction,
    make_proposed_order,
)


def _repository(tmp_path: Path) -> SQLitePaperExecutionRepository:
    database_path = tmp_path / "phase8.sqlite3"
    initialize_database(database_path)
    return SQLitePaperExecutionRepository(database_path)


def _enabled_settings() -> Settings:
    return Settings(
        enable_paper_execution=True,
        dry_run=False,
        paper_execution_kill_switch=False,
        alpaca_api_key=SecretStr("AKTEST"),
        alpaca_secret_key=SecretStr("SKTEST"),
    )


def _ready_service(tmp_path: Path) -> tuple[PaperExecutionService, SQLitePaperExecutionRepository]:
    repository = _repository(tmp_path)
    repository.set_paper_execution_kill_switch(
        engaged=False,
        reason="explicit_test",
        updated_at_utc=BROKER_TIME,
        confirmation=DISENGAGE_KILL_SWITCH_CONFIRMATION,
    )
    return PaperExecutionService(settings=_enabled_settings(), repository=repository), repository


def test_default_settings_and_dry_run_block_submission_without_broker_submit(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    instruction = make_instruction()
    approval = make_approval(instruction)
    broker = FakePaperBroker()

    with pytest.raises(PaperExecutionKillSwitchError) as default_exc:
        PaperExecutionService(settings=Settings(), repository=repository).submit_approved_order(
            instruction,
            approval,
            broker=broker,
        )

    dry_run_service = PaperExecutionService(
        settings=Settings(
            enable_paper_execution=True,
            dry_run=True,
            paper_execution_kill_switch=False,
        ),
        repository=repository,
    )
    preview = dry_run_service.preview_submission(
        instruction,
        approval,
        now_utc=BROKER_TIME,
    )
    with pytest.raises(PaperExecutionPermissionError):
        dry_run_service.submit_approved_order(instruction, approval, broker=broker)

    assert "dry_run_enabled" in preview.blocked_gate_codes
    assert default_exc.value.code == "configuration_kill_switch_engaged"
    assert broker.submit_calls == 0


def test_missing_credentials_block_explicit_submission(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    service = PaperExecutionService(
        settings=Settings(
            enable_paper_execution=True,
            dry_run=False,
            paper_execution_kill_switch=False,
        ),
        repository=repository,
    )

    with pytest.raises(PaperExecutionConfigurationError, match="Alpaca paper API key"):
        service.submit_approved_order(
            make_instruction(),
            make_approval(make_instruction()),
            broker=FakePaperBroker(),
        )


def test_configuration_kill_switch_blocks_before_any_broker_operation(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    repository.set_paper_execution_kill_switch(
        engaged=False,
        reason="explicit_test",
        updated_at_utc=BROKER_TIME,
        confirmation=DISENGAGE_KILL_SWITCH_CONFIRMATION,
    )
    service = PaperExecutionService(
        settings=Settings(
            enable_paper_execution=True,
            dry_run=False,
            paper_execution_kill_switch=True,
            alpaca_api_key=SecretStr("AKTEST"),
            alpaca_secret_key=SecretStr("SKTEST"),
        ),
        repository=repository,
    )
    instruction = make_instruction()
    approval = make_approval(instruction)
    broker = FakePaperBroker()

    preview = service.preview_submission(instruction, approval, now_utc=BROKER_TIME)
    with pytest.raises(PaperExecutionKillSwitchError) as exc_info:
        service.submit_approved_order(instruction, approval, broker=broker)

    assert exc_info.value.code == "configuration_kill_switch_engaged"
    assert "configuration_kill_switch_engaged" in preview.blocked_gate_codes
    assert broker.operation_log == []
    assert broker.submit_calls == 0


def test_configuration_false_does_not_override_durable_kill_switch(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    service = PaperExecutionService(settings=_enabled_settings(), repository=repository)
    instruction = make_instruction()
    broker = FakePaperBroker()

    with pytest.raises(PaperExecutionKillSwitchError) as exc_info:
        service.submit_approved_order(instruction, make_approval(instruction), broker=broker)

    assert exc_info.value.code == "kill_switch_engaged"
    assert broker.submit_calls == 0


def test_live_mode_request_at_execution_boundary_raises_runtime_error(
    tmp_path: Path,
) -> None:
    settings = Settings.model_construct(
        execution_mode="live",
        enable_paper_execution=True,
        dry_run=False,
        paper_execution_kill_switch=False,
        alpaca_api_key=SecretStr("AKTEST"),
        alpaca_secret_key=SecretStr("SKTEST"),
        paper_execution_require_market_open=True,
    )
    service = PaperExecutionService(settings=settings, repository=_repository(tmp_path))
    instruction = make_instruction()

    with pytest.raises(RuntimeError):
        service.submit_approved_order(
            instruction,
            make_approval(instruction),
            broker=FakePaperBroker(),
        )


def test_kill_switch_blocks_submission_until_explicitly_disengaged(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    service = PaperExecutionService(settings=_enabled_settings(), repository=repository)
    instruction = make_instruction()
    broker = FakePaperBroker()

    with pytest.raises(PaperExecutionKillSwitchError):
        service.submit_approved_order(instruction, make_approval(instruction), broker=broker)

    assert broker.submit_calls == 0


def test_successful_submission_reserves_ids_records_receipt_and_blocks_duplicate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, repository = _ready_service(tmp_path)
    instruction = make_instruction()
    approval = make_approval(instruction)
    broker = FakePaperBroker()
    kill_switch_reads = 0
    real_kill_switch_state = repository.get_kill_switch_state

    def counted_kill_switch_state() -> object:
        nonlocal kill_switch_reads
        kill_switch_reads += 1
        return real_kill_switch_state()

    monkeypatch.setattr(repository, "get_kill_switch_state", counted_kill_switch_state)

    receipt = service.submit_approved_order(instruction, approval, broker=broker)
    second_broker = FakePaperBroker()

    assert receipt.client_order_id == CLIENT_ORDER_ID
    assert broker.submit_calls == 1
    assert kill_switch_reads == 2
    assert repository.get_attempt(CLIENT_ORDER_ID).attempt_status == PAPER_ATTEMPT_ACCEPTED
    with pytest.raises(PaperExecutionDuplicateError):
        service.submit_approved_order(instruction, approval, broker=second_broker)
    assert second_broker.submit_calls == 0


def test_broker_request_construction_failure_blocks_reserved_attempt(
    tmp_path: Path,
) -> None:
    service, repository = _ready_service(tmp_path)
    instruction = make_instruction()
    approval = make_approval(instruction)
    broker = FakePaperBroker(
        submit_error=PaperExecutionBrokerRequestError(
            "broker_request_construction_failed",
            "paper-order request could not be constructed locally.",
        )
    )

    with pytest.raises(PaperExecutionBrokerRequestError) as exc_info:
        service.submit_approved_order(instruction, approval, broker=broker)

    attempt = repository.get_attempt(CLIENT_ORDER_ID)
    events = repository.list_events(client_order_id=CLIENT_ORDER_ID)

    assert "reconciliation" not in str(exc_info.value).lower()
    assert broker.submit_calls == 1
    assert broker.lookup_calls == 1
    assert attempt.attempt_status == PAPER_ATTEMPT_BLOCKED
    assert attempt.failure_code == "broker_request_construction_failed"
    assert [event.event_type for event in events] == [
        "attempt_reserved",
        "broker_request_construction_failed",
    ]
    assert events[-1].signal_id == instruction.signal_id
    assert events[-1].client_order_id == instruction.client_order_id
    with pytest.raises(PaperExecutionDuplicateError):
        service.submit_approved_order(instruction, approval, broker=FakePaperBroker())


def test_existing_broker_order_is_reconciled_without_second_submission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, repository = _ready_service(tmp_path)
    instruction = make_instruction()
    approval = make_approval(instruction)
    snapshot = make_broker_order_snapshot(instruction)
    broker = FakePaperBroker(existing_order=snapshot)
    kill_switch_reads = 0
    real_kill_switch_state = repository.get_kill_switch_state

    def counted_kill_switch_state() -> object:
        nonlocal kill_switch_reads
        kill_switch_reads += 1
        return real_kill_switch_state()

    monkeypatch.setattr(repository, "get_kill_switch_state", counted_kill_switch_state)

    returned = service.submit_approved_order(instruction, approval, broker=broker)

    assert returned.signal_id == instruction.signal_id
    assert returned.instruction_fingerprint == instruction.instruction_fingerprint
    assert broker.submit_calls == 0
    assert broker.lookup_calls == 1
    assert kill_switch_reads == 1
    assert repository.get_attempt(CLIENT_ORDER_ID).attempt_status == (
        PAPER_ATTEMPT_BROKER_EXISTING_ORDER_FOUND
    )


def test_final_kill_switch_reread_blocks_after_reservation_and_keeps_ids(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, repository = _ready_service(tmp_path)
    instruction = make_instruction()
    approval = make_approval(instruction)
    broker = FakePaperBroker()
    real_kill_switch_state = repository.get_kill_switch_state
    kill_switch_reads = 0

    def race_kill_switch_state() -> object:
        nonlocal kill_switch_reads
        kill_switch_reads += 1
        if kill_switch_reads == 2:
            connection = sqlite3.connect(tmp_path / "phase8.sqlite3")
            try:
                connection.execute(
                    """
                    UPDATE paper_execution_control
                    SET kill_switch_engaged = 1, reason = 'race_engaged'
                    WHERE singleton_id = 1
                    """
                )
                connection.commit()
            finally:
                connection.close()
        return real_kill_switch_state()

    monkeypatch.setattr(repository, "get_kill_switch_state", race_kill_switch_state)

    with pytest.raises(PaperExecutionKillSwitchError) as exc_info:
        service.submit_approved_order(instruction, approval, broker=broker)

    attempt = repository.get_attempt(CLIENT_ORDER_ID)
    event_types = [
        event.event_type for event in repository.list_events(client_order_id=CLIENT_ORDER_ID)
    ]

    assert exc_info.value.code == "kill_switch_engaged_before_submission"
    assert broker.submit_calls == 0
    assert broker.lookup_calls == 1
    assert kill_switch_reads == 2
    assert attempt.attempt_status == PAPER_ATTEMPT_BLOCKED
    assert attempt.failure_code == "kill_switch_engaged_before_submission"
    assert event_types == ["attempt_reserved", "final_kill_switch_blocked"]
    with pytest.raises(PaperExecutionDuplicateError):
        repository.reserve_attempt(
            instruction,
            approval,
            execution_risk_approved=True,
            now_utc=BROKER_TIME,
        )


def test_final_kill_switch_read_failure_blocks_submission_safely(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, repository = _ready_service(tmp_path)
    instruction = make_instruction()
    approval = make_approval(instruction)
    broker = FakePaperBroker()
    real_kill_switch_state = repository.get_kill_switch_state
    kill_switch_reads = 0

    def failing_final_kill_switch_state() -> object:
        nonlocal kill_switch_reads
        kill_switch_reads += 1
        if kill_switch_reads == 2:
            raise PaperExecutionIntegrityError(
                "invalid_kill_switch_state",
                "paper-execution kill switch state is invalid and must be treated as engaged.",
            )
        return real_kill_switch_state()

    monkeypatch.setattr(repository, "get_kill_switch_state", failing_final_kill_switch_state)

    with pytest.raises(PaperExecutionKillSwitchError) as exc_info:
        service.submit_approved_order(instruction, approval, broker=broker)

    attempt = repository.get_attempt(CLIENT_ORDER_ID)

    assert exc_info.value.code == "kill_switch_state_unavailable_before_submission"
    assert broker.submit_calls == 0
    assert kill_switch_reads == 2
    assert attempt.attempt_status == PAPER_ATTEMPT_BLOCKED
    assert attempt.failure_code == "kill_switch_state_unavailable_before_submission"


def test_no_broker_operation_occurs_after_final_kill_switch_check_except_submit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, repository = _ready_service(tmp_path)
    instruction = make_instruction()
    approval = make_approval(instruction)
    broker = FakePaperBroker()
    operations: list[str] = []
    broker.operation_log = operations
    real_kill_switch_state = repository.get_kill_switch_state

    def logged_kill_switch_state() -> object:
        operations.append("kill_switch_read")
        return real_kill_switch_state()

    monkeypatch.setattr(repository, "get_kill_switch_state", logged_kill_switch_state)

    service.submit_approved_order(instruction, approval, broker=broker)

    final_read_index = len(operations) - 1 - operations[::-1].index("kill_switch_read")
    assert operations[final_read_index + 1] == "submit_market_day_order"


def test_refreshed_closed_clock_blocks_after_reservation_and_keeps_ids(
    tmp_path: Path,
) -> None:
    service, repository = _ready_service(tmp_path)
    instruction = make_instruction()
    approval = make_approval(instruction)
    broker = FakePaperBroker(
        clock_sequence=(
            _clock(),
            _clock(timestamp=BROKER_TIME + timedelta(minutes=1), is_open=False),
        )
    )

    with pytest.raises(PaperExecutionStaleSignalError) as exc_info:
        service.submit_approved_order(instruction, approval, broker=broker)

    attempt = repository.get_attempt(CLIENT_ORDER_ID)
    events = repository.list_events(client_order_id=CLIENT_ORDER_ID)
    assert exc_info.value.code == "market_closed_before_submission"
    assert broker.clock_calls == 2
    assert broker.submit_calls == 0
    assert attempt.attempt_status == PAPER_ATTEMPT_BLOCKED
    assert attempt.failure_code == "market_closed_before_submission"
    assert [event.event_type for event in events] == [
        "attempt_reserved",
        "pre_submission_clock_blocked",
    ]
    with pytest.raises(PaperExecutionDuplicateError):
        repository.reserve_attempt(
            instruction,
            approval,
            execution_risk_approved=True,
            now_utc=BROKER_TIME,
        )


def test_refreshed_later_session_blocks_after_reservation(tmp_path: Path) -> None:
    service, repository = _ready_service(tmp_path)
    instruction = make_instruction(expires_at=BROKER_TIME + timedelta(days=2))
    approval = make_approval(instruction)
    broker = FakePaperBroker(
        clock_sequence=(
            _clock(),
            _clock(timestamp=BROKER_TIME + timedelta(days=1), is_open=True),
        )
    )

    with pytest.raises(PaperExecutionStaleSignalError) as exc_info:
        service.submit_approved_order(instruction, approval, broker=broker)

    assert exc_info.value.code == "wrong_execution_session_before_submission"
    assert broker.submit_calls == 0
    assert repository.get_attempt(CLIENT_ORDER_ID).attempt_status == PAPER_ATTEMPT_BLOCKED


def test_instruction_exact_expiration_at_refreshed_clock_blocks_before_submission(
    tmp_path: Path,
) -> None:
    service, repository = _ready_service(tmp_path)
    expires_at = BROKER_TIME + timedelta(minutes=1)
    instruction = make_instruction(expires_at=expires_at)
    approval = make_approval(instruction)
    broker = FakePaperBroker(
        clock_sequence=(
            _clock(),
            _clock(timestamp=expires_at, is_open=True),
        )
    )

    with pytest.raises(PaperExecutionStaleSignalError) as exc_info:
        service.submit_approved_order(instruction, approval, broker=broker)

    attempt = repository.get_attempt(CLIENT_ORDER_ID)
    assert exc_info.value.code == "instruction_expired_before_submission"
    assert broker.submit_calls == 0
    assert attempt.attempt_status == PAPER_ATTEMPT_BLOCKED
    assert attempt.failure_code == "instruction_expired_before_submission"


def test_instruction_one_microsecond_before_expiration_can_submit(
    tmp_path: Path,
) -> None:
    service, repository = _ready_service(tmp_path)
    expires_at = BROKER_TIME + timedelta(minutes=1)
    instruction = make_instruction(expires_at=expires_at)
    approval = make_approval(instruction)
    broker = FakePaperBroker(
        clock_sequence=(
            _clock(),
            _clock(timestamp=expires_at - timedelta(microseconds=1), is_open=True),
        )
    )

    service.submit_approved_order(instruction, approval, broker=broker)

    assert broker.submit_calls == 1
    assert repository.get_attempt(CLIENT_ORDER_ID).attempt_status == PAPER_ATTEMPT_ACCEPTED


def test_approval_invalid_at_refreshed_clock_blocks_reserved_attempt(
    tmp_path: Path,
) -> None:
    service, repository = _ready_service(tmp_path)
    instruction = make_instruction()
    approval = make_approval(instruction, approved_at=BROKER_TIME + timedelta(minutes=2))
    broker = FakePaperBroker(
        clock_sequence=(
            _clock(timestamp=BROKER_TIME + timedelta(minutes=3), is_open=True),
            _clock(timestamp=BROKER_TIME + timedelta(minutes=1), is_open=True),
        )
    )

    with pytest.raises(PaperExecutionApprovalError) as exc_info:
        service.submit_approved_order(instruction, approval, broker=broker)

    attempt = repository.get_attempt(CLIENT_ORDER_ID)
    assert exc_info.value.code == "approval_invalid_before_submission"
    assert broker.submit_calls == 0
    assert attempt.attempt_status == PAPER_ATTEMPT_BLOCKED
    assert attempt.failure_code == "approval_invalid_before_submission"


def test_refreshed_clock_failure_blocks_safely_without_raw_error(
    tmp_path: Path,
) -> None:
    service, repository = _ready_service(tmp_path)
    instruction = make_instruction()
    approval = make_approval(instruction)
    broker = FakePaperBroker(clock_sequence=(_clock(), RuntimeError("raw clock secret")))

    with pytest.raises(PaperExecutionStaleSignalError) as exc_info:
        service.submit_approved_order(instruction, approval, broker=broker)

    attempt = repository.get_attempt(CLIENT_ORDER_ID)
    assert exc_info.value.code == "broker_clock_refresh_failed"
    assert "secret" not in str(exc_info.value).lower()
    assert broker.submit_calls == 0
    assert attempt.attempt_status == PAPER_ATTEMPT_BLOCKED
    assert attempt.failure_code == "broker_clock_refresh_failed"


def test_market_open_requirement_cannot_be_bypassed_by_constructed_settings(
    tmp_path: Path,
) -> None:
    settings = Settings.model_construct(
        execution_mode="paper",
        enable_paper_execution=True,
        dry_run=False,
        paper_execution_kill_switch=False,
        paper_execution_require_market_open=False,
        alpaca_api_key=SecretStr("AKTEST"),
        alpaca_secret_key=SecretStr("SKTEST"),
    )
    repository = _repository(tmp_path)
    repository.set_paper_execution_kill_switch(
        engaged=False,
        reason="explicit_test",
        updated_at_utc=BROKER_TIME,
        confirmation=DISENGAGE_KILL_SWITCH_CONFIRMATION,
    )
    service = PaperExecutionService(settings=settings, repository=repository)
    instruction = make_instruction()
    broker = FakePaperBroker(clock=_clock(is_open=False))

    with pytest.raises(PaperExecutionStaleSignalError) as exc_info:
        service.submit_approved_order(instruction, make_approval(instruction), broker=broker)

    assert exc_info.value.code == "market_closed"
    assert broker.submit_calls == 0


def test_successful_submission_ordering_refreshes_clock_before_final_kill_switch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, repository = _ready_service(tmp_path)
    instruction = make_instruction()
    approval = make_approval(instruction)
    broker = FakePaperBroker(clock_sequence=(_clock(), _clock()))
    operations: list[str] = []
    broker.operation_log = operations
    real_kill_switch_state = repository.get_kill_switch_state

    def logged_kill_switch_state() -> object:
        operations.append("kill_switch_read")
        return real_kill_switch_state()

    monkeypatch.setattr(repository, "get_kill_switch_state", logged_kill_switch_state)

    service.submit_approved_order(instruction, approval, broker=broker)

    assert broker.clock_calls == 2
    assert broker.submit_calls == 1
    assert operations[-4:] == [
        "get_order_by_client_order_id",
        "get_clock",
        "kill_switch_read",
        "submit_market_day_order",
    ]


def test_existing_broker_order_reconciliation_skips_final_clock_and_submit(
    tmp_path: Path,
) -> None:
    service, repository = _ready_service(tmp_path)
    instruction = make_instruction()
    approval = make_approval(instruction)
    broker = FakePaperBroker(
        existing_order=make_broker_order_snapshot(instruction),
        clock_sequence=(_clock(),),
    )

    service.submit_approved_order(instruction, approval, broker=broker)

    assert broker.clock_calls == 1
    assert broker.submit_calls == 0
    assert repository.get_attempt(CLIENT_ORDER_ID).attempt_status == (
        PAPER_ATTEMPT_BROKER_EXISTING_ORDER_FOUND
    )


def test_timeout_marks_submission_unknown_and_reconciliation_never_submits(
    tmp_path: Path,
) -> None:
    service, repository = _ready_service(tmp_path)
    instruction = make_instruction()
    approval = make_approval(instruction)
    broker = FakePaperBroker(submit_error=TimeoutError("network timeout with secret"))

    with pytest.raises(PaperExecutionSubmissionUnknownError) as exc_info:
        service.submit_approved_order(instruction, approval, broker=broker)
    assert "secret" not in str(exc_info.value).lower()
    assert broker.submit_calls == 1
    assert repository.get_attempt(CLIENT_ORDER_ID).attempt_status == (
        PAPER_ATTEMPT_SUBMISSION_UNKNOWN
    )

    duplicate_broker = FakePaperBroker()
    with pytest.raises(PaperExecutionDuplicateError):
        service.submit_approved_order(instruction, approval, broker=duplicate_broker)
    assert duplicate_broker.submit_calls == 0

    reconcile_broker = FakePaperBroker(existing_order=make_broker_order_snapshot(instruction))
    reconciled = service.reconcile_by_client_order_id(
        CLIENT_ORDER_ID,
        broker=reconcile_broker,
        now_utc=BROKER_TIME + timedelta(minutes=1),
    )

    assert reconciled is not None
    assert reconcile_broker.submit_calls == 0
    assert repository.get_attempt(CLIENT_ORDER_ID).attempt_status == PAPER_ATTEMPT_RECONCILED


def test_definitive_broker_rejection_records_rejected_without_retry(tmp_path: Path) -> None:
    service, repository = _ready_service(tmp_path)
    instruction = make_instruction()
    approval = make_approval(instruction)
    broker = FakePaperBroker(
        submit_error=PaperExecutionBrokerRejectionError(
            "broker_order_rejected",
            "paper broker rejected the order.",
        )
    )

    with pytest.raises(PaperExecutionBrokerRejectionError):
        service.submit_approved_order(instruction, approval, broker=broker)

    assert broker.submit_calls == 1
    assert repository.get_attempt(CLIENT_ORDER_ID).attempt_status == PAPER_ATTEMPT_REJECTED
    with pytest.raises(PaperExecutionDuplicateError):
        service.submit_approved_order(instruction, approval, broker=FakePaperBroker())


@pytest.mark.parametrize(
    "submit_error",
    [
        TimeoutError("raw secret timeout"),
        ConnectionError("raw secret connection loss"),
        RuntimeError("raw secret sdk failure"),
        asyncio.CancelledError("raw secret cancellation"),
    ],
)
def test_uncertain_submit_exceptions_become_submission_unknown(
    tmp_path: Path,
    submit_error: BaseException,
) -> None:
    service, repository = _ready_service(tmp_path)
    instruction = make_instruction()
    approval = make_approval(instruction)
    broker = FakePaperBroker(submit_error=submit_error)

    with pytest.raises(PaperExecutionSubmissionUnknownError) as exc_info:
        service.submit_approved_order(instruction, approval, broker=broker)

    assert "secret" not in str(exc_info.value).lower()
    assert broker.submit_calls == 1
    assert repository.get_attempt(CLIENT_ORDER_ID).attempt_status == (
        PAPER_ATTEMPT_SUBMISSION_UNKNOWN
    )
    assert repository.get_attempt(CLIENT_ORDER_ID).attempt_status != PAPER_ATTEMPT_REJECTED
    with pytest.raises(PaperExecutionDuplicateError):
        service.submit_approved_order(instruction, approval, broker=FakePaperBroker())


@pytest.mark.parametrize(
    "snapshot_change",
    [
        {"broker_order_id": ""},
        {"client_order_id": "other-client-order"},
        {"symbol": "QQQ"},
        {"side": "sell"},
        {"submitted_quantity": 11},
        {"order_type": "limit"},
        {"time_in_force": "gtc"},
        {"extended_hours": True},
    ],
)
def test_post_submit_malformed_or_contradictory_snapshot_is_unknown_not_rejected(
    tmp_path: Path,
    snapshot_change: dict[str, object],
) -> None:
    service, repository = _ready_service(tmp_path)
    instruction = make_instruction()
    approval = make_approval(instruction)
    snapshot = make_broker_order_snapshot(instruction)
    for field_name, value in snapshot_change.items():
        object.__setattr__(snapshot, field_name, value)
    broker = FakePaperBroker(submit_snapshot=snapshot)

    with pytest.raises(PaperExecutionSubmissionUnknownError):
        service.submit_approved_order(instruction, approval, broker=broker)

    attempt = repository.get_attempt(CLIENT_ORDER_ID)
    assert broker.submit_calls == 1
    assert attempt.attempt_status == PAPER_ATTEMPT_SUBMISSION_UNKNOWN
    assert attempt.failure_code == "broker_snapshot_mismatch"
    with pytest.raises(PaperExecutionDuplicateError):
        service.submit_approved_order(instruction, approval, broker=FakePaperBroker())


def test_accepted_receipt_persistence_failure_after_submit_becomes_unknown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, repository = _ready_service(tmp_path)
    instruction = make_instruction()
    approval = make_approval(instruction)
    broker = FakePaperBroker()
    real_record_receipt = repository.record_receipt
    fail_once = True

    def failing_record_receipt(
        receipt: PaperOrderReceipt,
        *,
        status: str,
        account_id_fingerprint: str | None,
        now_utc: datetime,
        event_type: str,
    ) -> object:
        nonlocal fail_once
        if fail_once and status == PAPER_ATTEMPT_ACCEPTED:
            fail_once = False
            raise PaperExecutionIntegrityError(
                "attempt_update_failed",
                "raw sqlite path /tmp/secret.sqlite3",
            )
        return real_record_receipt(
            receipt,
            status=status,
            account_id_fingerprint=account_id_fingerprint,
            now_utc=now_utc,
            event_type=event_type,
        )

    monkeypatch.setattr(repository, "record_receipt", failing_record_receipt)

    with pytest.raises(PaperExecutionSubmissionUnknownError) as exc_info:
        service.submit_approved_order(instruction, approval, broker=broker)

    attempt = repository.get_attempt(CLIENT_ORDER_ID)
    assert broker.submit_calls == 1
    assert exc_info.value.code == "accepted_receipt_persistence_failed"
    assert "secret" not in str(exc_info.value).lower()
    assert attempt.attempt_status == PAPER_ATTEMPT_SUBMISSION_UNKNOWN
    assert attempt.failure_code == "accepted_receipt_persistence_failed"

    second_broker = FakePaperBroker()
    with pytest.raises(PaperExecutionDuplicateError):
        service.submit_approved_order(instruction, approval, broker=second_broker)
    assert second_broker.submit_calls == 0

    reconcile_broker = FakePaperBroker(existing_order=make_broker_order_snapshot(instruction))
    reconciled = service.reconcile_by_client_order_id(
        CLIENT_ORDER_ID,
        broker=reconcile_broker,
        now_utc=BROKER_TIME + timedelta(minutes=1),
    )

    assert reconciled is not None
    assert reconcile_broker.submit_calls == 0
    assert repository.get_attempt(CLIENT_ORDER_ID).attempt_status == PAPER_ATTEMPT_RECONCILED


def test_accepted_receipt_and_unknown_mark_failures_still_raise_sanitized_unknown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, repository = _ready_service(tmp_path)
    instruction = make_instruction()
    approval = make_approval(instruction)
    broker = FakePaperBroker()

    def failing_record_receipt(
        _receipt: PaperOrderReceipt,
        *,
        status: str,
        account_id_fingerprint: str | None,
        now_utc: datetime,
        event_type: str,
    ) -> object:
        _ = (status, account_id_fingerprint, now_utc, event_type)
        raise PaperExecutionIntegrityError("attempt_update_failed", "raw sqlite secret")

    def failing_mark_submission_unknown(*_args: object, **_kwargs: object) -> object:
        raise PaperExecutionIntegrityError("attempt_unknown_update_failed", "raw sqlite secret")

    monkeypatch.setattr(repository, "record_receipt", failing_record_receipt)
    monkeypatch.setattr(repository, "mark_submission_unknown", failing_mark_submission_unknown)

    with pytest.raises(PaperExecutionSubmissionUnknownError) as exc_info:
        service.submit_approved_order(instruction, approval, broker=broker)

    assert exc_info.value.code == "accepted_receipt_persistence_failed"
    assert "secret" not in str(exc_info.value).lower()
    assert broker.submit_calls == 1


def test_existing_broker_order_receipt_persistence_failure_does_not_submit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, repository = _ready_service(tmp_path)
    instruction = make_instruction()
    approval = make_approval(instruction)
    broker = FakePaperBroker(existing_order=make_broker_order_snapshot(instruction))
    real_record_receipt = repository.record_receipt

    def failing_record_receipt(
        receipt: PaperOrderReceipt,
        *,
        status: str,
        account_id_fingerprint: str | None,
        now_utc: datetime,
        event_type: str,
    ) -> object:
        if status == PAPER_ATTEMPT_BROKER_EXISTING_ORDER_FOUND:
            raise PaperExecutionIntegrityError("attempt_update_failed", "raw sqlite secret")
        return real_record_receipt(
            receipt,
            status=status,
            account_id_fingerprint=account_id_fingerprint,
            now_utc=now_utc,
            event_type=event_type,
        )

    monkeypatch.setattr(repository, "record_receipt", failing_record_receipt)

    with pytest.raises(PaperExecutionSubmissionUnknownError) as exc_info:
        service.submit_approved_order(instruction, approval, broker=broker)

    attempt = repository.get_attempt(CLIENT_ORDER_ID)
    assert exc_info.value.code == "existing_order_receipt_persistence_failed"
    assert broker.submit_calls == 0
    assert attempt.attempt_status == PAPER_ATTEMPT_SUBMISSION_UNKNOWN
    assert attempt.failure_code == "existing_order_receipt_persistence_failed"


def test_existing_mismatching_broker_order_fails_closed_without_submission(
    tmp_path: Path,
) -> None:
    service, repository = _ready_service(tmp_path)
    instruction = make_instruction()
    approval = make_approval(instruction)
    snapshot = replace(make_broker_order_snapshot(instruction), side="sell")
    broker = FakePaperBroker(existing_order=snapshot)

    with pytest.raises(PaperExecutionSubmissionUnknownError):
        service.submit_approved_order(instruction, approval, broker=broker)

    attempt = repository.get_attempt(CLIENT_ORDER_ID)
    events = repository.list_events(client_order_id=CLIENT_ORDER_ID)
    assert broker.submit_calls == 0
    assert attempt.attempt_status == PAPER_ATTEMPT_SUBMISSION_UNKNOWN
    assert events[-1].event_type == "broker_order_mismatch"


def test_reconciliation_rejects_mismatch_and_never_submits(tmp_path: Path) -> None:
    service, repository = _ready_service(tmp_path)
    instruction = make_instruction()
    approval = make_approval(instruction)
    repository.reserve_attempt(
        instruction,
        approval,
        execution_risk_approved=True,
        now_utc=BROKER_TIME,
    )
    broker = FakePaperBroker(
        existing_order=replace(make_broker_order_snapshot(instruction), submitted_quantity=1)
    )

    with pytest.raises(PaperExecutionSubmissionUnknownError):
        service.reconcile_by_client_order_id(
            CLIENT_ORDER_ID,
            broker=broker,
            now_utc=BROKER_TIME + timedelta(minutes=1),
        )

    assert broker.submit_calls == 0
    assert repository.get_attempt(CLIENT_ORDER_ID).attempt_status == (
        PAPER_ATTEMPT_SUBMISSION_UNKNOWN
    )


def test_reconciliation_does_not_convert_terminal_attempt(tmp_path: Path) -> None:
    service, repository = _ready_service(tmp_path)
    instruction = make_instruction()
    approval = make_approval(instruction)
    broker = FakePaperBroker()
    service.submit_approved_order(instruction, approval, broker=broker)
    reconcile_broker = FakePaperBroker(existing_order=make_broker_order_snapshot(instruction))

    with pytest.raises(PaperExecutionIntegrityError):
        service.reconcile_by_client_order_id(
            CLIENT_ORDER_ID,
            broker=reconcile_broker,
            now_utc=BROKER_TIME + timedelta(minutes=1),
        )

    assert reconcile_broker.submit_calls == 0
    assert repository.get_attempt(CLIENT_ORDER_ID).attempt_status == PAPER_ATTEMPT_ACCEPTED


@pytest.mark.parametrize(
    ("broker", "error_type", "code"),
    [
        (
            FakePaperBroker(
                environment=BrokerEnvironmentSnapshot(
                    environment_name="alpaca_live",
                    endpoint_identity="https://api.alpaca.markets",
                    is_paper=False,
                    verified_at_utc=BROKER_TIME,
                )
            ),
            PaperExecutionBrokerStateError,
            "unsafe_broker_environment",
        ),
        (
            FakePaperBroker(
                account=BrokerAccountSnapshot(
                    status="INACTIVE",
                    currency="USD",
                    cash=Decimal("10000"),
                    equity=Decimal("10000"),
                    buying_power=Decimal("10000"),
                    trading_blocked=False,
                    account_blocked=False,
                    trade_suspended_by_user=False,
                    account_id_fingerprint="a" * 64,
                    retrieved_at_utc=BROKER_TIME,
                )
            ),
            PaperExecutionBrokerStateError,
            "account_not_active",
        ),
        (
            FakePaperBroker(
                account=BrokerAccountSnapshot(
                    status="active",
                    currency="USD",
                    cash=Decimal("10000"),
                    equity=Decimal("10000"),
                    buying_power=Decimal("10000"),
                    trading_blocked=True,
                    account_blocked=False,
                    trade_suspended_by_user=False,
                    account_id_fingerprint="a" * 64,
                    retrieved_at_utc=BROKER_TIME,
                )
            ),
            PaperExecutionBrokerStateError,
            "account_blocked",
        ),
        (
            FakePaperBroker(
                account_configuration=BrokerAccountConfigurationSnapshot(
                    no_shorting=False,
                    max_margin_multiplier=Decimal("1"),
                    fractional_trading_enabled=False,
                    suspend_trade=False,
                    retrieved_at_utc=BROKER_TIME,
                )
            ),
            PaperExecutionBrokerStateError,
            "shorting_enabled",
        ),
        (
            FakePaperBroker(
                clock=BrokerClockSnapshot(
                    timestamp=BROKER_TIME,
                    is_open=False,
                    next_open=BROKER_TIME + timedelta(days=1),
                    next_close=BROKER_TIME + timedelta(hours=6),
                )
            ),
            PaperExecutionStaleSignalError,
            "market_closed",
        ),
        (
            FakePaperBroker(
                positions=(
                    BrokerPositionSnapshot(
                        symbol="SPY",
                        side="long",
                        quantity=Decimal("1"),
                        available_quantity=Decimal("1"),
                    ),
                )
            ),
            PaperExecutionBrokerStateError,
            "pyramiding_forbidden",
        ),
        (
            FakePaperBroker(
                positions=(
                    BrokerPositionSnapshot(
                        symbol="SPY",
                        side="short",
                        quantity=Decimal("1"),
                        available_quantity=Decimal("1"),
                    ),
                )
            ),
            PaperExecutionBrokerStateError,
            "short_position",
        ),
        (
            FakePaperBroker(
                open_orders=(
                    BrokerOpenOrderSnapshot(
                        broker_order_id="broker-open-1",
                        client_order_id="other-order",
                        symbol="SPY",
                        side="buy",
                        quantity=Decimal("1"),
                        filled_quantity=Decimal("0"),
                        status="accepted",
                        submitted_at_utc=BROKER_TIME,
                    ),
                )
            ),
            PaperExecutionBrokerStateError,
            "conflicting_open_order",
        ),
    ],
)
def test_broker_preflight_failures_block_submission(
    tmp_path: Path,
    broker: FakePaperBroker,
    error_type: type[PaperExecutionError],
    code: str,
) -> None:
    service, _repository = _ready_service(tmp_path)
    instruction = make_instruction()

    with pytest.raises(error_type) as exc_info:
        service.submit_approved_order(
            instruction,
            make_approval(instruction),
            broker=broker,
        )

    assert exc_info.value.code == code
    assert broker.submit_calls == 0


def test_sell_requires_complete_existing_long_position(tmp_path: Path) -> None:
    service, _repository = _ready_service(tmp_path)
    order = make_proposed_order(quantity=10, side=SELL_SIDE)
    instruction = make_instruction(order=order)
    broker = FakePaperBroker(
        positions=(
            BrokerPositionSnapshot(
                symbol="SPY",
                side="long",
                quantity=Decimal("10"),
                available_quantity=Decimal("10"),
            ),
        )
    )

    receipt = service.submit_approved_order(
        instruction,
        make_approval(instruction),
        broker=broker,
    )

    assert receipt.side == SELL_SIDE
    assert broker.submit_calls == 1


def test_concurrent_service_submissions_allow_one_session_winner(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "phase8-concurrent-service.sqlite3"
    initialize_database(database_path)
    control_repository = SQLitePaperExecutionRepository(database_path)
    control_repository.set_paper_execution_kill_switch(
        engaged=False,
        reason="explicit_concurrent_service",
        updated_at_utc=BROKER_TIME,
        confirmation=DISENGAGE_KILL_SWITCH_CONFIRMATION,
    )
    first_repository = SQLitePaperExecutionRepository(database_path)
    second_repository = SQLitePaperExecutionRepository(database_path)
    first_service = PaperExecutionService(settings=_enabled_settings(), repository=first_repository)
    second_service = PaperExecutionService(
        settings=_enabled_settings(),
        repository=second_repository,
    )
    first_instruction = make_instruction(
        signal_id="signal-concurrent-service-a",
        client_order_id="client-concurrent-service-a",
    )
    second_instruction = make_instruction(
        signal_id="signal-concurrent-service-b",
        client_order_id="client-concurrent-service-b",
    )
    first_approval = make_approval(
        first_instruction,
        approval_id="approval-concurrent-service-a",
    )
    second_approval = make_approval(
        second_instruction,
        approval_id="approval-concurrent-service-b",
    )
    pre_reservation_barrier = threading.Barrier(2)
    first_broker = _CoordinatedBroker(pre_reservation_barrier=pre_reservation_barrier)
    second_broker = _CoordinatedBroker(pre_reservation_barrier=pre_reservation_barrier)

    def submit(
        service: PaperExecutionService,
        instruction: PaperOrderInstruction,
        approval: PaperOrderApproval,
        broker: _CoordinatedBroker,
    ) -> tuple[str, _CoordinatedBroker, PaperExecutionDuplicateError | None]:
        try:
            service.submit_approved_order(instruction, approval, broker=broker)
        except PaperExecutionDuplicateError as exc:
            return ("duplicate", broker, exc)
        return ("submitted", broker, None)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(
            submit,
            first_service,
            first_instruction,
            first_approval,
            first_broker,
        )
        second_future = executor.submit(
            submit,
            second_service,
            second_instruction,
            second_approval,
            second_broker,
        )
        results = (first_future.result(), second_future.result())

    submitted = [result for result in results if result[0] == "submitted"]
    duplicates = [result for result in results if result[0] == "duplicate"]
    losing_broker = duplicates[0][1]

    assert len(submitted) == 1
    assert len(duplicates) == 1
    assert duplicates[0][2] is not None
    assert duplicates[0][2].code == "execution_session_already_reserved"
    assert first_broker.submit_calls + second_broker.submit_calls == 1
    assert losing_broker.submit_calls == 0
    assert losing_broker.lookup_calls == 0
    assert losing_broker.operation_log == [
        "verify_environment",
        "get_account",
        "get_account_configuration",
        "get_clock",
        "get_asset",
        "list_positions",
        "list_open_orders",
    ]
    assert _table_count(database_path, "paper_execution_attempts") == 1
    assert _event_count(database_path, "attempt_reserved") == 1


def test_same_session_duplicate_after_repository_reopen_does_not_submit(
    tmp_path: Path,
) -> None:
    service, _repository = _ready_service(tmp_path)
    instruction = make_instruction()
    approval = make_approval(instruction)
    service.submit_approved_order(instruction, approval, broker=FakePaperBroker())
    reopened_repository = SQLitePaperExecutionRepository(tmp_path / "phase8.sqlite3")
    reopened_service = PaperExecutionService(
        settings=_enabled_settings(),
        repository=reopened_repository,
    )
    duplicate = make_instruction(
        signal_id="signal-restart-session",
        client_order_id="client-restart-session",
    )
    duplicate_broker = FakePaperBroker()

    with pytest.raises(PaperExecutionDuplicateError) as exc_info:
        reopened_service.submit_approved_order(
            duplicate,
            make_approval(duplicate, approval_id="approval-restart-session"),
            broker=duplicate_broker,
        )

    assert exc_info.value.code == "execution_session_already_reserved"
    assert duplicate_broker.submit_calls == 0
    assert reopened_repository.count_attempts() == 1


def test_different_execution_session_can_submit_after_prior_session_attempt(
    tmp_path: Path,
) -> None:
    service, repository = _ready_service(tmp_path)
    first = make_instruction()
    first_approval = make_approval(first)
    service.submit_approved_order(first, first_approval, broker=FakePaperBroker())
    future_clock = datetime(2025, 1, 7, 15, 30, tzinfo=UTC)
    future_order = replace(
        make_proposed_order(),
        sequence_number=2,
        signal_session=date(2025, 1, 6),
        execution_session=date(2025, 1, 7),
    )
    second = make_instruction(
        signal_id="signal-different-session-service",
        client_order_id="client-different-session-service",
        order=future_order,
        created_at=future_clock - timedelta(hours=1),
        expires_at=future_clock + timedelta(hours=1),
    )
    second_approval = make_approval(
        second,
        approval_id="approval-different-session-service",
        approved_at=future_clock - timedelta(minutes=30),
    )
    broker = FakePaperBroker(
        clock_sequence=(
            _clock(timestamp=future_clock),
            _clock(timestamp=future_clock),
        )
    )

    service.submit_approved_order(second, second_approval, broker=broker)

    assert broker.submit_calls == 1
    assert repository.count_attempts() == 2


def _clock(
    *,
    timestamp: datetime = BROKER_TIME,
    is_open: bool = True,
) -> BrokerClockSnapshot:
    return BrokerClockSnapshot(
        timestamp=timestamp,
        is_open=is_open,
        next_open=BROKER_TIME + timedelta(days=1),
        next_close=BROKER_TIME + timedelta(hours=6),
    )


class _CoordinatedBroker(FakePaperBroker):
    def __init__(self, *, pre_reservation_barrier: threading.Barrier) -> None:
        super().__init__()
        self._pre_reservation_barrier = pre_reservation_barrier

    def list_open_orders(self) -> tuple[BrokerOpenOrderSnapshot, ...]:
        open_orders = super().list_open_orders()
        self._pre_reservation_barrier.wait(timeout=10)
        return open_orders


def _table_count(database_path: Path, table_name: str) -> int:
    connection = sqlite3.connect(database_path)
    try:
        return int(connection.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0])
    finally:
        connection.close()


def _event_count(database_path: Path, event_type: str) -> int:
    connection = sqlite3.connect(database_path)
    try:
        return int(
            connection.execute(
                "SELECT COUNT(*) FROM paper_execution_events WHERE event_type = ?",
                (event_type,),
            ).fetchone()[0]
        )
    finally:
        connection.close()
