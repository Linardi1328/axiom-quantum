from __future__ import annotations

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

import spy_market_agent.execution.repository as execution_repository
from spy_market_agent.config import Settings
from spy_market_agent.execution import (
    DISENGAGE_KILL_SWITCH_CONFIRMATION,
    PAPER_ATTEMPT_ACCEPTED,
    PAPER_ATTEMPT_BLOCKED,
    PAPER_ATTEMPT_BROKER_EXISTING_ORDER_FOUND,
    PAPER_ATTEMPT_RECONCILED,
    PAPER_ATTEMPT_REJECTED,
    PAPER_ATTEMPT_RESERVED,
    PAPER_ATTEMPT_SUBMISSION_UNKNOWN,
    PaperExecutionAttempt,
    PaperExecutionDuplicateError,
    PaperExecutionInputError,
    PaperExecutionIntegrityError,
    PaperOrderApproval,
    PaperOrderInstruction,
    PaperOrderReceipt,
    SQLitePaperExecutionRepository,
)
from spy_market_agent.persistence import initialize_database
from spy_market_agent.persistence.models import PersistenceSchemaError
from spy_market_agent.persistence.schema import (
    PERSISTENCE_SCHEMA_VERSION,
    PERSISTENCE_SCHEMA_VERSION_V1,
)
from spy_market_agent.persistence.serialization import date_to_text, datetime_to_text
from spy_market_agent.risk import SELL_SIDE
from unit.phase8_helpers import make_approval, make_instruction, make_proposed_order, make_receipt


def test_fresh_database_defaults_kill_switch_to_engaged(tmp_path: Path) -> None:
    database_path = tmp_path / "phase8.sqlite3"
    initialize_database(database_path)
    repository = SQLitePaperExecutionRepository(database_path)

    state = repository.get_kill_switch_state()
    status = repository.status(Settings())

    assert state.kill_switch_engaged is True
    assert state.reason == "default_engaged"
    assert status.kill_switch_engaged is True
    assert status.configuration_kill_switch_engaged is True
    assert status.durable_kill_switch_engaged is True
    assert status.effective_kill_switch_engaged is True
    assert status.paper_execution_enabled is False
    assert status.dry_run is True


def test_status_reports_configuration_durable_and_effective_kill_switch_states(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "phase8.sqlite3"
    initialize_database(database_path)
    repository = SQLitePaperExecutionRepository(database_path)

    default_status = repository.status(Settings())
    config_only = repository.status(Settings(paper_execution_kill_switch=True))
    durable_only = repository.status(Settings(paper_execution_kill_switch=False))
    repository.set_paper_execution_kill_switch(
        engaged=False,
        reason="explicit_test",
        updated_at_utc=datetime(2025, 1, 3, 14, 0, tzinfo=UTC),
        confirmation=DISENGAGE_KILL_SWITCH_CONFIRMATION,
    )
    none_engaged = repository.status(Settings(paper_execution_kill_switch=False))
    configuration_engaged = repository.status(Settings(paper_execution_kill_switch=True))

    assert default_status.kill_switch_engaged is True
    assert config_only.configuration_kill_switch_engaged is True
    assert config_only.durable_kill_switch_engaged is True
    assert config_only.effective_kill_switch_engaged is True
    assert durable_only.configuration_kill_switch_engaged is False
    assert durable_only.durable_kill_switch_engaged is True
    assert durable_only.effective_kill_switch_engaged is True
    assert none_engaged.kill_switch_engaged is False
    assert none_engaged.effective_kill_switch_engaged is False
    assert configuration_engaged.kill_switch_engaged is True
    assert configuration_engaged.configuration_kill_switch_engaged is True
    assert configuration_engaged.durable_kill_switch_engaged is False


def test_disengaging_kill_switch_requires_confirmation_reason_and_audits(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "phase8.sqlite3"
    initialize_database(database_path)
    repository = SQLitePaperExecutionRepository(database_path)
    now = datetime(2025, 1, 3, 14, 0, tzinfo=UTC)

    with pytest.raises(PaperExecutionInputError):
        repository.set_paper_execution_kill_switch(
            engaged=False,
            reason="explicit_test",
            updated_at_utc=now,
        )

    disengaged = repository.set_paper_execution_kill_switch(
        engaged=False,
        reason="explicit_test",
        updated_at_utc=now,
        confirmation=DISENGAGE_KILL_SWITCH_CONFIRMATION,
    )
    reengaged = repository.set_paper_execution_kill_switch(
        engaged=True,
        reason="safety_reengaged",
        updated_at_utc=now,
    )
    events = repository.list_events()

    assert disengaged.kill_switch_engaged is False
    assert reengaged.kill_switch_engaged is True
    assert [event.event_type for event in events] == [
        "kill_switch_updated",
        "kill_switch_updated",
    ]
    assert events[0].new_state == "disengaged"
    assert events[1].new_state == "engaged"


def test_attempt_round_trip_and_duplicate_protection_survive_repository_reopen(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "phase8.sqlite3"
    initialize_database(database_path)
    instruction = make_instruction()
    approval = make_approval(instruction)
    repository = SQLitePaperExecutionRepository(database_path)

    reserved = repository.reserve_attempt(
        instruction,
        approval,
        execution_risk_approved=True,
        now_utc=instruction.created_at_utc,
    )
    accepted = repository.record_receipt(
        make_receipt(instruction),
        status=PAPER_ATTEMPT_ACCEPTED,
        account_id_fingerprint="a" * 64,
        now_utc=instruction.created_at_utc,
        event_type="broker_order_accepted",
    )

    reopened = SQLitePaperExecutionRepository(database_path)
    loaded = reopened.get_attempt(instruction.client_order_id)
    with pytest.raises(PaperExecutionDuplicateError):
        reopened.reserve_attempt(
            instruction,
            approval,
            execution_risk_approved=True,
            now_utc=instruction.created_at_utc,
        )

    assert reserved.attempt_status == PAPER_ATTEMPT_RESERVED
    assert accepted.attempt_status == PAPER_ATTEMPT_ACCEPTED
    assert loaded.broker_status == "accepted"
    assert reopened.count_attempts() == 1
    assert [
        event.event_type
        for event in reopened.list_events(client_order_id=instruction.client_order_id)
    ] == [
        "attempt_reserved",
        "broker_order_accepted",
    ]


def test_fresh_initialization_creates_symbol_session_unique_index(tmp_path: Path) -> None:
    database_path = tmp_path / "phase8.sqlite3"
    initialize_database(database_path)

    assert _index_exists(database_path, "ux_paper_execution_attempt_symbol_session")


def test_phase7_migration_creates_symbol_session_unique_index(tmp_path: Path) -> None:
    database_path = tmp_path / "phase7.sqlite3"
    connection = sqlite3.connect(database_path)
    try:
        connection.execute("CREATE TABLE schema_migrations (version TEXT PRIMARY KEY)")
        connection.execute(
            "INSERT INTO schema_migrations(version) VALUES (?)",
            (PERSISTENCE_SCHEMA_VERSION_V1,),
        )
        connection.commit()
    finally:
        connection.close()

    initialize_database(database_path)

    assert _index_exists(database_path, "ux_paper_execution_attempt_symbol_session")


def test_repeated_initialization_keeps_symbol_session_unique_index_idempotent(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "phase8.sqlite3"

    initialize_database(database_path)
    initialize_database(database_path)

    assert _index_exists(database_path, "ux_paper_execution_attempt_symbol_session")


def test_conflicting_development_rows_fail_closed_when_unique_index_is_created(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "phase8-conflict.sqlite3"
    first = make_instruction(signal_id="signal-legacy-a", client_order_id="client-legacy-a")
    second = make_instruction(signal_id="signal-legacy-b", client_order_id="client-legacy-b")
    first_approval = make_approval(first, approval_id="approval-legacy-a")
    second_approval = make_approval(second, approval_id="approval-legacy-b")
    connection = sqlite3.connect(database_path)
    try:
        connection.execute("CREATE TABLE schema_migrations (version TEXT PRIMARY KEY)")
        connection.execute(
            "INSERT INTO schema_migrations(version) VALUES (?)",
            (PERSISTENCE_SCHEMA_VERSION,),
        )
        connection.execute(
            """
            CREATE TABLE paper_execution_attempts (
                client_order_id TEXT PRIMARY KEY,
                signal_id TEXT NOT NULL UNIQUE,
                approval_id TEXT NOT NULL UNIQUE,
                instruction_fingerprint TEXT NOT NULL,
                execution_schema_version TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                quantity INTEGER NOT NULL CHECK (quantity > 0),
                signal_session TEXT NOT NULL,
                execution_session TEXT NOT NULL,
                instruction_created_at_utc TEXT NOT NULL,
                expires_at_utc TEXT NOT NULL,
                approval_at_utc TEXT NOT NULL,
                approval_source TEXT NOT NULL,
                original_risk_approved INTEGER NOT NULL CHECK (original_risk_approved IN (0, 1)),
                execution_risk_approved INTEGER NOT NULL CHECK (execution_risk_approved IN (0, 1)),
                attempt_status TEXT NOT NULL,
                broker_order_id TEXT,
                broker_status TEXT,
                broker_environment TEXT,
                account_id_fingerprint TEXT,
                sanitized_request_id TEXT,
                created_at_utc TEXT NOT NULL,
                updated_at_utc TEXT NOT NULL,
                failure_code TEXT
            )
            """
        )
        _insert_raw_attempt(connection, first, first_approval)
        _insert_raw_attempt(connection, second, second_approval)
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(PersistenceSchemaError) as exc_info:
        initialize_database(database_path)

    assert exc_info.value.code == "schema_initialization_failed"


@pytest.mark.parametrize(
    ("instruction", "approval_id"),
    [
        (
            make_instruction(
                signal_id="signal-same-session-a",
                client_order_id="client-same-session-a",
            ),
            "approval-same-session-a",
        ),
        (
            make_instruction(
                signal_id="signal-same-session-side",
                client_order_id="client-same-session-side",
                order=make_proposed_order(side=SELL_SIDE),
            ),
            "approval-same-session-side",
        ),
        (
            make_instruction(
                signal_id="signal-same-session-quantity",
                client_order_id="client-same-session-quantity",
                order=make_proposed_order(quantity=11),
            ),
            "approval-same-session-quantity",
        ),
    ],
)
def test_same_spy_execution_session_rejects_different_ids_side_and_quantity(
    tmp_path: Path,
    instruction: PaperOrderInstruction,
    approval_id: str,
) -> None:
    repository, existing_instruction, _approval = _repository_with_reserved_attempt(tmp_path)
    before_attempt = repository.get_attempt(existing_instruction.client_order_id)
    before_events = repository.list_events()

    with pytest.raises(PaperExecutionDuplicateError) as exc_info:
        repository.reserve_attempt(
            instruction,
            make_approval(instruction, approval_id=approval_id),
            execution_risk_approved=True,
            now_utc=instruction.created_at_utc,
        )

    assert exc_info.value.code == "execution_session_already_reserved"
    assert "signal" not in str(exc_info.value).lower()
    assert repository.count_attempts() == 1
    assert repository.get_attempt(existing_instruction.client_order_id) == before_attempt
    assert repository.list_events() == before_events


@pytest.mark.parametrize(
    "status",
    [
        PAPER_ATTEMPT_RESERVED,
        PAPER_ATTEMPT_BLOCKED,
        PAPER_ATTEMPT_REJECTED,
        PAPER_ATTEMPT_ACCEPTED,
        PAPER_ATTEMPT_SUBMISSION_UNKNOWN,
        PAPER_ATTEMPT_RECONCILED,
        PAPER_ATTEMPT_BROKER_EXISTING_ORDER_FOUND,
    ],
)
def test_any_prior_attempt_status_consumes_the_symbol_execution_session(
    tmp_path: Path,
    status: str,
) -> None:
    repository, existing_instruction, _approval = _repository_with_reserved_attempt(tmp_path)
    _move_attempt_to_status(repository, existing_instruction, status)
    new_instruction = make_instruction(
        signal_id=f"signal-session-consumed-{status.replace('_', '-')}",
        client_order_id=f"client-session-consumed-{status.replace('_', '-')}",
    )

    with pytest.raises(PaperExecutionDuplicateError) as exc_info:
        repository.reserve_attempt(
            new_instruction,
            make_approval(
                new_instruction,
                approval_id=f"approval-session-consumed-{status.replace('_', '-')}",
            ),
            execution_risk_approved=True,
            now_utc=new_instruction.created_at_utc,
        )

    assert exc_info.value.code == "execution_session_already_reserved"
    assert repository.count_attempts() == 1


def test_different_future_execution_session_remains_reservable(tmp_path: Path) -> None:
    database_path = tmp_path / "phase8.sqlite3"
    initialize_database(database_path)
    repository = SQLitePaperExecutionRepository(database_path)
    first = make_instruction()
    first_approval = make_approval(first)
    future_order = replace(
        make_proposed_order(),
        sequence_number=2,
        signal_session=date(2025, 1, 6),
        execution_session=date(2025, 1, 7),
    )
    second = make_instruction(
        signal_id="signal-future-session",
        client_order_id="client-future-session",
        order=future_order,
    )
    second_approval = make_approval(second, approval_id="approval-future-session")

    repository.reserve_attempt(
        first,
        first_approval,
        execution_risk_approved=True,
        now_utc=first.created_at_utc,
    )
    reserved = repository.reserve_attempt(
        second,
        second_approval,
        execution_risk_approved=True,
        now_utc=second.created_at_utc,
    )

    assert reserved.execution_session == date(2025, 1, 7)
    assert repository.count_attempts() == 2


def test_session_reservation_protection_survives_reopen_and_repository_instances(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "phase8.sqlite3"
    initialize_database(database_path)
    first_repository = SQLitePaperExecutionRepository(database_path)
    second_repository = SQLitePaperExecutionRepository(database_path)
    first = make_instruction()
    first_repository.reserve_attempt(
        first,
        make_approval(first),
        execution_risk_approved=True,
        now_utc=first.created_at_utc,
    )
    second = make_instruction(
        signal_id="signal-reopen-session",
        client_order_id="client-reopen-session",
    )

    with pytest.raises(PaperExecutionDuplicateError) as exc_info:
        second_repository.reserve_attempt(
            second,
            make_approval(second, approval_id="approval-reopen-session"),
            execution_risk_approved=True,
            now_utc=second.created_at_utc,
        )

    reopened = SQLitePaperExecutionRepository(database_path)
    third = make_instruction(
        signal_id="signal-reopened-session",
        client_order_id="client-reopened-session",
    )
    with pytest.raises(PaperExecutionDuplicateError) as reopened_exc:
        reopened.reserve_attempt(
            third,
            make_approval(third, approval_id="approval-reopened-session"),
            execution_risk_approved=True,
            now_utc=third.created_at_utc,
        )

    assert exc_info.value.code == "execution_session_already_reserved"
    assert reopened_exc.value.code == "execution_session_already_reserved"
    assert reopened.count_attempts() == 1


def test_duplicate_identifier_constraints_still_apply_for_different_sessions(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "phase8.sqlite3"
    initialize_database(database_path)
    repository = SQLitePaperExecutionRepository(database_path)
    first = make_instruction()
    future_order = replace(
        make_proposed_order(),
        sequence_number=2,
        signal_session=date(2025, 1, 6),
        execution_session=date(2025, 1, 7),
    )
    duplicate_signal = make_instruction(
        signal_id=first.signal_id,
        client_order_id="client-duplicate-signal-future",
        order=future_order,
    )

    repository.reserve_attempt(
        first,
        make_approval(first),
        execution_risk_approved=True,
        now_utc=first.created_at_utc,
    )
    with pytest.raises(PaperExecutionDuplicateError) as exc_info:
        repository.reserve_attempt(
            duplicate_signal,
            make_approval(duplicate_signal, approval_id="approval-duplicate-signal-future"),
            execution_risk_approved=True,
            now_utc=duplicate_signal.created_at_utc,
        )

    assert exc_info.value.code == "duplicate_execution_identifier"
    assert repository.count_attempts() == 1


def test_reserve_attempt_reconstructs_before_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "phase8.sqlite3"
    initialize_database(database_path)
    instruction = make_instruction()
    approval = make_approval(instruction)
    repository = SQLitePaperExecutionRepository(database_path)
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    tracking_connection = _TrackingConnection(connection)
    real_get_attempt = execution_repository._get_attempt_from_connection

    def checked_get_attempt(connection_arg: object, client_order_id: str) -> object:
        if connection_arg is tracking_connection and tracking_connection.commit_called:
            raise AssertionError("reserve_attempt read after commit")
        return real_get_attempt(connection_arg, client_order_id)  # type: ignore[arg-type]

    monkeypatch.setattr(repository, "_connect", lambda: tracking_connection)
    monkeypatch.setattr(execution_repository, "_get_attempt_from_connection", checked_get_attempt)

    reserved = repository.reserve_attempt(
        instruction,
        approval,
        execution_risk_approved=True,
        now_utc=instruction.created_at_utc,
    )

    assert reserved.attempt_status == PAPER_ATTEMPT_RESERVED
    assert tracking_connection.commit_called is True


def test_reservation_reconstruction_failure_rolls_back_attempt_and_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "phase8.sqlite3"
    initialize_database(database_path)
    repository = SQLitePaperExecutionRepository(database_path)
    instruction = make_instruction()
    approval = make_approval(instruction)
    real_attempt_from_row = execution_repository._attempt_from_row

    def failing_attempt_from_row(row: sqlite3.Row) -> object:
        attempt = real_attempt_from_row(row)
        if attempt.attempt_status == PAPER_ATTEMPT_RESERVED:
            raise PaperExecutionIntegrityError(
                "invalid_paper_execution_attempt",
                "paper-execution attempt is invalid.",
            )
        return attempt

    monkeypatch.setattr(execution_repository, "_attempt_from_row", failing_attempt_from_row)

    with pytest.raises(PaperExecutionIntegrityError):
        repository.reserve_attempt(
            instruction,
            approval,
            execution_risk_approved=True,
            now_utc=instruction.created_at_utc,
        )

    assert _table_count(database_path, "paper_execution_attempts") == 0
    assert _table_count(database_path, "paper_execution_events") == 0


def test_reservation_event_failure_rolls_back_attempt(tmp_path: Path) -> None:
    database_path = tmp_path / "phase8.sqlite3"
    initialize_database(database_path)
    repository = SQLitePaperExecutionRepository(database_path)
    instruction = make_instruction()
    approval = make_approval(instruction)
    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            """
            CREATE TRIGGER fail_attempt_reserved_event
            BEFORE INSERT ON paper_execution_events
            WHEN NEW.event_type = 'attempt_reserved'
            BEGIN
                SELECT RAISE(FAIL, 'raw sqlite secret reservation failure');
            END
            """
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(PaperExecutionIntegrityError) as exc_info:
        repository.reserve_attempt(
            instruction,
            approval,
            execution_risk_approved=True,
            now_utc=instruction.created_at_utc,
        )

    assert "secret" not in str(exc_info.value).lower()
    assert _table_count(database_path, "paper_execution_attempts") == 0
    assert _table_count(database_path, "paper_execution_events") == 0


def test_failed_session_reservation_inserts_no_attempt_or_event(tmp_path: Path) -> None:
    repository, first, _approval = _repository_with_reserved_attempt(tmp_path)
    before_attempt = repository.get_attempt(first.client_order_id)
    before_events = repository.list_events()
    second = make_instruction(
        signal_id="signal-failed-session-reservation",
        client_order_id="client-failed-session-reservation",
    )

    with pytest.raises(PaperExecutionDuplicateError) as exc_info:
        repository.reserve_attempt(
            second,
            make_approval(second, approval_id="approval-failed-session-reservation"),
            execution_risk_approved=True,
            now_utc=second.created_at_utc,
        )

    assert exc_info.value.code == "execution_session_already_reserved"
    assert repository.count_attempts() == 1
    assert repository.get_attempt(first.client_order_id) == before_attempt
    assert repository.list_events() == before_events


def test_concurrent_repository_reservations_allow_one_winner_per_session(
    tmp_path: Path,
) -> None:
    for iteration in range(5):
        database_path = tmp_path / f"phase8-concurrent-{iteration}.sqlite3"
        initialize_database(database_path)
        barrier = threading.Barrier(2)

        def reserve(
            index: int,
            *,
            database_path: Path = database_path,
            iteration: int = iteration,
            barrier: threading.Barrier = barrier,
        ) -> PaperExecutionAttempt | PaperExecutionDuplicateError:
            repository = SQLitePaperExecutionRepository(database_path)
            instruction = make_instruction(
                signal_id=f"signal-concurrent-{iteration}-{index}",
                client_order_id=f"client-concurrent-{iteration}-{index}",
            )
            approval = make_approval(
                instruction,
                approval_id=f"approval-concurrent-{iteration}-{index}",
            )
            barrier.wait(timeout=10)
            try:
                return repository.reserve_attempt(
                    instruction,
                    approval,
                    execution_risk_approved=True,
                    now_utc=instruction.created_at_utc,
                )
            except PaperExecutionDuplicateError as exc:
                return exc

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = tuple(executor.map(reserve, (1, 2)))

        successes = [item for item in results if isinstance(item, PaperExecutionAttempt)]
        duplicates = [item for item in results if isinstance(item, PaperExecutionDuplicateError)]

        assert len(successes) == 1
        assert len(duplicates) == 1
        assert duplicates[0].code == "execution_session_already_reserved"
        assert _table_count(database_path, "paper_execution_attempts") == 1
        assert _table_count(database_path, "paper_execution_events") == 1


@pytest.mark.parametrize(
    "receipt_change",
    [
        {"signal_id": "other-signal"},
        {"instruction_fingerprint": "b" * 64},
        {"client_order_id": "other-client-order"},
        {"symbol": "QQQ"},
        {"side": "sell"},
        {"submitted_quantity": 11},
        {"order_type": "limit"},
        {"time_in_force": "gtc"},
        {"extended_hours": True},
    ],
)
def test_record_receipt_rejects_forged_or_mismatched_lineage_transactionally(
    tmp_path: Path,
    receipt_change: dict[str, object],
) -> None:
    database_path = tmp_path / "phase8.sqlite3"
    initialize_database(database_path)
    instruction = make_instruction()
    approval = make_approval(instruction)
    repository = SQLitePaperExecutionRepository(database_path)
    repository.reserve_attempt(
        instruction,
        approval,
        execution_risk_approved=True,
        now_utc=instruction.created_at_utc,
    )
    forged = _forged_receipt(instruction, **receipt_change)

    with pytest.raises(PaperExecutionIntegrityError):
        repository.record_receipt(
            forged,
            status=PAPER_ATTEMPT_ACCEPTED,
            account_id_fingerprint="a" * 64,
            now_utc=instruction.created_at_utc,
            event_type="broker_order_accepted",
        )

    attempt = repository.get_attempt(instruction.client_order_id)
    events = repository.list_events(client_order_id=instruction.client_order_id)
    assert attempt.attempt_status == PAPER_ATTEMPT_RESERVED
    assert attempt.broker_order_id is None
    assert [event.event_type for event in events] == ["attempt_reserved"]


def test_record_receipt_rejects_invalid_state_transition_after_reopen(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "phase8.sqlite3"
    initialize_database(database_path)
    instruction = make_instruction()
    approval = make_approval(instruction)
    repository = SQLitePaperExecutionRepository(database_path)
    repository.reserve_attempt(
        instruction,
        approval,
        execution_risk_approved=True,
        now_utc=instruction.created_at_utc,
    )
    repository.mark_failure(
        client_order_id=instruction.client_order_id,
        signal_id=instruction.signal_id,
        status="blocked",
        failure_code="final_kill_switch_blocked",
        now_utc=instruction.created_at_utc,
    )
    reopened = SQLitePaperExecutionRepository(database_path)

    with pytest.raises(PaperExecutionIntegrityError):
        reopened.record_receipt(
            make_receipt(instruction),
            status=PAPER_ATTEMPT_RECONCILED,
            account_id_fingerprint="a" * 64,
            now_utc=instruction.created_at_utc,
            event_type="broker_order_reconciled",
        )

    assert reopened.get_attempt(instruction.client_order_id).attempt_status == "blocked"
    assert [
        event.event_type
        for event in reopened.list_events(client_order_id=instruction.client_order_id)
    ] == [
        "attempt_reserved",
        "attempt_failed",
    ]


def test_submission_unknown_retains_reservation(tmp_path: Path) -> None:
    database_path = tmp_path / "phase8.sqlite3"
    initialize_database(database_path)
    instruction = make_instruction()
    approval = make_approval(instruction)
    repository = SQLitePaperExecutionRepository(database_path)
    repository.reserve_attempt(
        instruction,
        approval,
        execution_risk_approved=True,
        now_utc=instruction.created_at_utc,
    )

    unknown = repository.mark_submission_unknown(
        client_order_id=instruction.client_order_id,
        signal_id=instruction.signal_id,
        failure_code="timeout",
        now_utc=instruction.created_at_utc,
    )

    assert unknown.attempt_status == PAPER_ATTEMPT_SUBMISSION_UNKNOWN
    assert unknown.failure_code == "timeout"
    with pytest.raises(PaperExecutionDuplicateError):
        repository.reserve_attempt(
            instruction,
            approval,
            execution_risk_approved=True,
            now_utc=instruction.created_at_utc,
        )


def test_submission_unknown_transition_allows_reserved_and_same_state(
    tmp_path: Path,
) -> None:
    repository, instruction, _approval = _repository_with_reserved_attempt(tmp_path)

    first = repository.mark_submission_unknown(
        client_order_id=instruction.client_order_id,
        signal_id=instruction.signal_id,
        failure_code="timeout",
        now_utc=instruction.created_at_utc,
    )
    second = repository.mark_submission_unknown(
        client_order_id=instruction.client_order_id,
        signal_id=instruction.signal_id,
        failure_code="broker_order_mismatch",
        now_utc=instruction.created_at_utc,
        event_type="broker_order_mismatch",
    )
    events = repository.list_events(client_order_id=instruction.client_order_id)

    assert first.attempt_status == PAPER_ATTEMPT_SUBMISSION_UNKNOWN
    assert second.attempt_status == PAPER_ATTEMPT_SUBMISSION_UNKNOWN
    assert second.failure_code == "broker_order_mismatch"
    assert [event.event_type for event in events] == [
        "attempt_reserved",
        "submission_unknown",
        "broker_order_mismatch",
    ]
    assert events[-1].signal_id == instruction.signal_id
    assert events[-1].client_order_id == instruction.client_order_id


@pytest.mark.parametrize(
    "terminal_status",
    [
        PAPER_ATTEMPT_ACCEPTED,
        PAPER_ATTEMPT_BROKER_EXISTING_ORDER_FOUND,
        PAPER_ATTEMPT_RECONCILED,
        PAPER_ATTEMPT_REJECTED,
        PAPER_ATTEMPT_BLOCKED,
    ],
)
def test_submission_unknown_rejects_terminal_state_regression(
    tmp_path: Path,
    terminal_status: str,
) -> None:
    repository, instruction, _approval = _repository_with_reserved_attempt(tmp_path)
    _move_attempt_to_status(repository, instruction, terminal_status)
    before_attempt = repository.get_attempt(instruction.client_order_id)
    before_events = repository.list_events(client_order_id=instruction.client_order_id)

    with pytest.raises(PaperExecutionIntegrityError):
        repository.mark_submission_unknown(
            client_order_id=instruction.client_order_id,
            signal_id=instruction.signal_id,
            failure_code="late_unknown",
            now_utc=instruction.created_at_utc,
        )

    assert repository.get_attempt(instruction.client_order_id) == before_attempt
    assert repository.list_events(client_order_id=instruction.client_order_id) == before_events


@pytest.mark.parametrize("target_status", [PAPER_ATTEMPT_BLOCKED, PAPER_ATTEMPT_REJECTED])
def test_failure_transition_allows_reserved_to_blocked_or_rejected(
    tmp_path: Path,
    target_status: str,
) -> None:
    repository, instruction, _approval = _repository_with_reserved_attempt(tmp_path)

    failed = repository.mark_failure(
        client_order_id=instruction.client_order_id,
        signal_id=instruction.signal_id,
        status=target_status,
        failure_code=f"{target_status}_reason",
        now_utc=instruction.created_at_utc,
    )
    events = repository.list_events(client_order_id=instruction.client_order_id)

    assert failed.attempt_status == target_status
    assert events[-1].signal_id == instruction.signal_id
    assert events[-1].client_order_id == instruction.client_order_id


@pytest.mark.parametrize(
    "target_status",
    [
        PAPER_ATTEMPT_RESERVED,
        PAPER_ATTEMPT_ACCEPTED,
        PAPER_ATTEMPT_BROKER_EXISTING_ORDER_FOUND,
        PAPER_ATTEMPT_SUBMISSION_UNKNOWN,
        PAPER_ATTEMPT_RECONCILED,
    ],
)
def test_failure_transition_rejects_unsupported_target_states(
    tmp_path: Path,
    target_status: str,
) -> None:
    repository, instruction, _approval = _repository_with_reserved_attempt(tmp_path)

    with pytest.raises(PaperExecutionInputError):
        repository.mark_failure(
            client_order_id=instruction.client_order_id,
            signal_id=instruction.signal_id,
            status=target_status,
            failure_code="invalid_target",
            now_utc=instruction.created_at_utc,
        )

    assert repository.get_attempt(instruction.client_order_id).attempt_status == (
        PAPER_ATTEMPT_RESERVED
    )
    assert len(repository.list_events(client_order_id=instruction.client_order_id)) == 1


@pytest.mark.parametrize(
    "terminal_status",
    [
        PAPER_ATTEMPT_ACCEPTED,
        PAPER_ATTEMPT_BROKER_EXISTING_ORDER_FOUND,
        PAPER_ATTEMPT_RECONCILED,
        PAPER_ATTEMPT_REJECTED,
        PAPER_ATTEMPT_BLOCKED,
    ],
)
def test_failure_transition_rejects_terminal_state_regression(
    tmp_path: Path,
    terminal_status: str,
) -> None:
    repository, instruction, _approval = _repository_with_reserved_attempt(tmp_path)
    _move_attempt_to_status(repository, instruction, terminal_status)
    before_attempt = repository.get_attempt(instruction.client_order_id)
    before_events = repository.list_events(client_order_id=instruction.client_order_id)

    with pytest.raises(PaperExecutionIntegrityError):
        repository.mark_failure(
            client_order_id=instruction.client_order_id,
            signal_id=instruction.signal_id,
            status=PAPER_ATTEMPT_BLOCKED,
            failure_code="late_block",
            now_utc=instruction.created_at_utc,
        )

    assert repository.get_attempt(instruction.client_order_id) == before_attempt
    assert repository.list_events(client_order_id=instruction.client_order_id) == before_events


def test_wrong_signal_lineage_rejected_by_unknown_and_failure_updates(
    tmp_path: Path,
) -> None:
    repository, instruction, _approval = _repository_with_reserved_attempt(tmp_path)
    before_attempt = repository.get_attempt(instruction.client_order_id)
    before_events = repository.list_events(client_order_id=instruction.client_order_id)

    with pytest.raises(PaperExecutionIntegrityError):
        repository.mark_submission_unknown(
            client_order_id=instruction.client_order_id,
            signal_id="other-signal",
            failure_code="timeout",
            now_utc=instruction.created_at_utc,
        )
    with pytest.raises(PaperExecutionIntegrityError):
        repository.mark_failure(
            client_order_id=instruction.client_order_id,
            signal_id="other-signal",
            status=PAPER_ATTEMPT_BLOCKED,
            failure_code="blocked",
            now_utc=instruction.created_at_utc,
        )

    assert repository.get_attempt(instruction.client_order_id) == before_attempt
    assert repository.list_events(client_order_id=instruction.client_order_id) == before_events


def test_update_row_count_mismatch_is_rejected_transactionally(tmp_path: Path) -> None:
    database_path = tmp_path / "phase8.sqlite3"
    initialize_database(database_path)
    instruction = make_instruction()
    approval = make_approval(instruction)
    repository = SQLitePaperExecutionRepository(database_path)
    repository.reserve_attempt(
        instruction,
        approval,
        execution_risk_approved=True,
        now_utc=instruction.created_at_utc,
    )
    before_attempt = repository.get_attempt(instruction.client_order_id)
    before_events = repository.list_events(client_order_id=instruction.client_order_id)
    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            """
            CREATE TRIGGER skip_paper_attempt_update
            BEFORE UPDATE ON paper_execution_attempts
            BEGIN
                SELECT RAISE(IGNORE);
            END
            """
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(PaperExecutionIntegrityError):
        repository.mark_submission_unknown(
            client_order_id=instruction.client_order_id,
            signal_id=instruction.signal_id,
            failure_code="timeout",
            now_utc=instruction.created_at_utc,
        )

    assert repository.get_attempt(instruction.client_order_id) == before_attempt
    assert repository.list_events(client_order_id=instruction.client_order_id) == before_events


def test_sqlite_failure_rolls_back_attempt_update_and_event_append(tmp_path: Path) -> None:
    database_path = tmp_path / "phase8.sqlite3"
    initialize_database(database_path)
    instruction = make_instruction()
    approval = make_approval(instruction)
    repository = SQLitePaperExecutionRepository(database_path)
    repository.reserve_attempt(
        instruction,
        approval,
        execution_risk_approved=True,
        now_utc=instruction.created_at_utc,
    )
    before_attempt = repository.get_attempt(instruction.client_order_id)
    before_events = repository.list_events(client_order_id=instruction.client_order_id)
    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            """
            CREATE TRIGGER fail_submission_unknown_event
            BEFORE INSERT ON paper_execution_events
            WHEN NEW.event_type = 'submission_unknown'
            BEGIN
                SELECT RAISE(FAIL, 'raw sqlite failure with secret text');
            END
            """
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(PaperExecutionIntegrityError) as exc_info:
        repository.mark_submission_unknown(
            client_order_id=instruction.client_order_id,
            signal_id=instruction.signal_id,
            failure_code="timeout",
            now_utc=instruction.created_at_utc,
        )

    assert "secret" not in str(exc_info.value).lower()
    assert repository.get_attempt(instruction.client_order_id) == before_attempt
    assert repository.list_events(client_order_id=instruction.client_order_id) == before_events


def test_receipt_environment_must_be_alpaca_paper(tmp_path: Path) -> None:
    repository, instruction, _approval = _repository_with_reserved_attempt(tmp_path)
    accepted = repository.record_receipt(
        make_receipt(instruction),
        status=PAPER_ATTEMPT_ACCEPTED,
        account_id_fingerprint="a" * 64,
        now_utc=instruction.created_at_utc,
        event_type="broker_order_accepted",
    )

    assert accepted.broker_environment == "alpaca_paper"


@pytest.mark.parametrize("environment", ["alpaca_live", "live", "production", "paper", "unknown"])
def test_forged_receipt_environment_is_rejected_transactionally(
    tmp_path: Path,
    environment: str,
) -> None:
    repository, instruction, _approval = _repository_with_reserved_attempt(tmp_path)
    before_attempt = repository.get_attempt(instruction.client_order_id)
    before_events = repository.list_events(client_order_id=instruction.client_order_id)
    forged = _forged_receipt(instruction, execution_environment=environment)

    with pytest.raises(PaperExecutionIntegrityError) as exc_info:
        repository.record_receipt(
            forged,
            status=PAPER_ATTEMPT_ACCEPTED,
            account_id_fingerprint="a" * 64,
            now_utc=instruction.created_at_utc,
            event_type="broker_order_accepted",
        )

    assert exc_info.value.code == "receipt_environment_mismatch"
    assert repository.get_attempt(instruction.client_order_id) == before_attempt
    assert repository.list_events(client_order_id=instruction.client_order_id) == before_events
    reopened = SQLitePaperExecutionRepository(tmp_path / "phase8.sqlite3")
    assert reopened.get_attempt(instruction.client_order_id) == before_attempt


def test_record_receipt_update_failure_rolls_back_attempt_and_event(
    tmp_path: Path,
) -> None:
    repository, instruction, _approval = _repository_with_reserved_attempt(tmp_path)
    before_attempt = repository.get_attempt(instruction.client_order_id)
    before_events = repository.list_events(client_order_id=instruction.client_order_id)
    connection = sqlite3.connect(tmp_path / "phase8.sqlite3")
    try:
        connection.execute(
            """
            CREATE TRIGGER skip_receipt_update
            BEFORE UPDATE ON paper_execution_attempts
            WHEN NEW.broker_order_id IS NOT NULL
            BEGIN
                SELECT RAISE(IGNORE);
            END
            """
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(PaperExecutionIntegrityError):
        repository.record_receipt(
            make_receipt(instruction),
            status=PAPER_ATTEMPT_ACCEPTED,
            account_id_fingerprint="a" * 64,
            now_utc=instruction.created_at_utc,
            event_type="broker_order_accepted",
        )

    assert repository.get_attempt(instruction.client_order_id) == before_attempt
    assert repository.list_events(client_order_id=instruction.client_order_id) == before_events


def test_record_receipt_event_insertion_failure_rolls_back_attempt_update(
    tmp_path: Path,
) -> None:
    repository, instruction, _approval = _repository_with_reserved_attempt(tmp_path)
    before_attempt = repository.get_attempt(instruction.client_order_id)
    before_events = repository.list_events(client_order_id=instruction.client_order_id)
    connection = sqlite3.connect(tmp_path / "phase8.sqlite3")
    try:
        connection.execute(
            """
            CREATE TRIGGER fail_receipt_event
            BEFORE INSERT ON paper_execution_events
            WHEN NEW.event_type = 'broker_order_accepted'
            BEGIN
                SELECT RAISE(FAIL, 'raw sqlite secret event failure');
            END
            """
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(PaperExecutionIntegrityError) as exc_info:
        repository.record_receipt(
            make_receipt(instruction),
            status=PAPER_ATTEMPT_ACCEPTED,
            account_id_fingerprint="a" * 64,
            now_utc=instruction.created_at_utc,
            event_type="broker_order_accepted",
        )

    assert "secret" not in str(exc_info.value).lower()
    assert repository.get_attempt(instruction.client_order_id) == before_attempt
    assert repository.list_events(client_order_id=instruction.client_order_id) == before_events


def test_record_receipt_result_reconstruction_failure_rolls_back_before_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, instruction, _approval = _repository_with_reserved_attempt(tmp_path)
    before_attempt = repository.get_attempt(instruction.client_order_id)
    before_events = repository.list_events(client_order_id=instruction.client_order_id)
    real_attempt_from_row = execution_repository._attempt_from_row

    def failing_attempt_from_row(row: sqlite3.Row) -> object:
        attempt = real_attempt_from_row(row)
        if attempt.attempt_status == PAPER_ATTEMPT_ACCEPTED:
            raise PaperExecutionIntegrityError(
                "invalid_paper_execution_attempt",
                "paper-execution attempt is invalid.",
            )
        return attempt

    monkeypatch.setattr(execution_repository, "_attempt_from_row", failing_attempt_from_row)

    with pytest.raises(PaperExecutionIntegrityError):
        repository.record_receipt(
            make_receipt(instruction),
            status=PAPER_ATTEMPT_ACCEPTED,
            account_id_fingerprint="a" * 64,
            now_utc=instruction.created_at_utc,
            event_type="broker_order_accepted",
        )

    assert repository.get_attempt(instruction.client_order_id) == before_attempt
    assert repository.list_events(client_order_id=instruction.client_order_id) == before_events


def test_record_receipt_does_not_read_attempt_after_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "phase8.sqlite3"
    initialize_database(database_path)
    instruction = make_instruction()
    approval = make_approval(instruction)
    repository = SQLitePaperExecutionRepository(database_path)
    repository.reserve_attempt(
        instruction,
        approval,
        execution_risk_approved=True,
        now_utc=instruction.created_at_utc,
    )
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    tracking_connection = _TrackingConnection(connection)
    real_get_attempt = execution_repository._get_attempt_from_connection

    def checked_get_attempt(connection_arg: object, client_order_id: str) -> object:
        if connection_arg is tracking_connection and tracking_connection.commit_called:
            raise AssertionError("record_receipt read after commit")
        return real_get_attempt(connection_arg, client_order_id)  # type: ignore[arg-type]

    monkeypatch.setattr(repository, "_connect", lambda: tracking_connection)
    monkeypatch.setattr(execution_repository, "_get_attempt_from_connection", checked_get_attempt)

    accepted = repository.record_receipt(
        make_receipt(instruction),
        status=PAPER_ATTEMPT_ACCEPTED,
        account_id_fingerprint="a" * 64,
        now_utc=instruction.created_at_utc,
        event_type="broker_order_accepted",
    )

    assert accepted.attempt_status == PAPER_ATTEMPT_ACCEPTED
    assert tracking_connection.commit_called is True


def test_blank_receipt_environment_is_rejected_by_model_validation() -> None:
    instruction = make_instruction()

    with pytest.raises(PaperExecutionInputError):
        replace(make_receipt(instruction), execution_environment="")


def test_tampered_attempt_rows_fail_with_project_owned_integrity_error(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "phase8.sqlite3"
    initialize_database(database_path)
    instruction = make_instruction()
    approval = make_approval(instruction)
    repository = SQLitePaperExecutionRepository(database_path)
    repository.reserve_attempt(
        instruction,
        approval,
        execution_risk_approved=True,
        now_utc=instruction.created_at_utc,
    )
    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            "UPDATE paper_execution_attempts SET quantity = ? WHERE client_order_id = ?",
            ("not-an-int", instruction.client_order_id),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(PaperExecutionIntegrityError):
        repository.get_attempt(instruction.client_order_id)


def test_corrupted_kill_switch_state_fails_closed(tmp_path: Path) -> None:
    database_path = tmp_path / "phase8.sqlite3"
    initialize_database(database_path)
    connection = sqlite3.connect(database_path)
    try:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            "UPDATE paper_execution_control SET kill_switch_engaged = 7 WHERE singleton_id = 1"
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(PaperExecutionIntegrityError):
        SQLitePaperExecutionRepository(database_path).get_kill_switch_state()


def test_phase7_v1_database_migrates_to_v2_with_engaged_kill_switch(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "phase7.sqlite3"
    connection = sqlite3.connect(database_path)
    try:
        connection.execute("CREATE TABLE schema_migrations (version TEXT PRIMARY KEY)")
        connection.execute(
            "INSERT INTO schema_migrations(version) VALUES (?)",
            (PERSISTENCE_SCHEMA_VERSION_V1,),
        )
        connection.commit()
    finally:
        connection.close()

    initialize_database(database_path)
    connection = sqlite3.connect(database_path)
    try:
        versions = {row[0] for row in connection.execute("SELECT version FROM schema_migrations")}
    finally:
        connection.close()
    state = SQLitePaperExecutionRepository(database_path).get_kill_switch_state()

    assert versions == {PERSISTENCE_SCHEMA_VERSION}
    assert state.kill_switch_engaged is True


def test_unsupported_future_schema_is_rejected_without_migration(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "future.sqlite3"
    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            "CREATE TABLE schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT)"
        )
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES ('future', 'now')"
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(PersistenceSchemaError):
        initialize_database(database_path)


def _index_exists(database_path: Path, index_name: str) -> bool:
    connection = sqlite3.connect(database_path)
    try:
        row = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND name = ?",
            (index_name,),
        ).fetchone()
        return row is not None
    finally:
        connection.close()


def _table_count(database_path: Path, table_name: str) -> int:
    connection = sqlite3.connect(database_path)
    try:
        return int(connection.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0])
    finally:
        connection.close()


def _insert_raw_attempt(
    connection: sqlite3.Connection,
    instruction: PaperOrderInstruction,
    approval: PaperOrderApproval,
) -> None:
    connection.execute(
        """
        INSERT INTO paper_execution_attempts (
            client_order_id, signal_id, approval_id, instruction_fingerprint,
            execution_schema_version, symbol, side, quantity, signal_session,
            execution_session, instruction_created_at_utc, expires_at_utc,
            approval_at_utc, approval_source, original_risk_approved,
            execution_risk_approved, attempt_status, created_at_utc, updated_at_utc
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            instruction.client_order_id,
            instruction.signal_id,
            approval.approval_id,
            instruction.instruction_fingerprint,
            instruction.schema_version,
            instruction.proposed_order.symbol,
            instruction.proposed_order.side,
            instruction.proposed_order.quantity,
            date_to_text(instruction.proposed_order.signal_session),
            date_to_text(instruction.proposed_order.execution_session),
            datetime_to_text(instruction.created_at_utc),
            datetime_to_text(instruction.expires_at_utc),
            datetime_to_text(approval.approved_at_utc),
            approval.approved_by,
            1,
            1,
            PAPER_ATTEMPT_RESERVED,
            datetime_to_text(instruction.created_at_utc),
            datetime_to_text(instruction.created_at_utc),
        ),
    )


def _forged_receipt(
    instruction: PaperOrderInstruction,
    **changes: object,
) -> PaperOrderReceipt:
    receipt = make_receipt(instruction)
    for field_name, value in changes.items():
        object.__setattr__(receipt, field_name, value)
    return receipt


def _repository_with_reserved_attempt(
    tmp_path: Path,
) -> tuple[SQLitePaperExecutionRepository, PaperOrderInstruction, object]:
    database_path = tmp_path / "phase8.sqlite3"
    initialize_database(database_path)
    instruction = make_instruction()
    approval = make_approval(instruction)
    repository = SQLitePaperExecutionRepository(database_path)
    repository.reserve_attempt(
        instruction,
        approval,
        execution_risk_approved=True,
        now_utc=instruction.created_at_utc,
    )
    return repository, instruction, approval


def _move_attempt_to_status(
    repository: SQLitePaperExecutionRepository,
    instruction: PaperOrderInstruction,
    status: str,
) -> None:
    if status == PAPER_ATTEMPT_RESERVED:
        return
    if status == PAPER_ATTEMPT_SUBMISSION_UNKNOWN:
        repository.mark_submission_unknown(
            client_order_id=instruction.client_order_id,
            signal_id=instruction.signal_id,
            failure_code="submission_unknown",
            now_utc=instruction.created_at_utc,
        )
        return
    if status in {PAPER_ATTEMPT_ACCEPTED, PAPER_ATTEMPT_BROKER_EXISTING_ORDER_FOUND}:
        repository.record_receipt(
            make_receipt(instruction),
            status=status,
            account_id_fingerprint="a" * 64,
            now_utc=instruction.created_at_utc,
            event_type=f"{status}_event",
        )
        return
    if status == PAPER_ATTEMPT_RECONCILED:
        repository.mark_submission_unknown(
            client_order_id=instruction.client_order_id,
            signal_id=instruction.signal_id,
            failure_code="timeout",
            now_utc=instruction.created_at_utc,
        )
        repository.record_receipt(
            make_receipt(instruction),
            status=PAPER_ATTEMPT_RECONCILED,
            account_id_fingerprint="a" * 64,
            now_utc=instruction.created_at_utc,
            event_type="broker_order_reconciled",
        )
        return
    if status in {PAPER_ATTEMPT_BLOCKED, PAPER_ATTEMPT_REJECTED}:
        repository.mark_failure(
            client_order_id=instruction.client_order_id,
            signal_id=instruction.signal_id,
            status=status,
            failure_code=f"{status}_reason",
            now_utc=instruction.created_at_utc,
        )
        return
    raise AssertionError(f"unsupported test status {status}")


class _TrackingConnection:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self.commit_called = False

    def execute(self, *args: Any, **kwargs: Any) -> sqlite3.Cursor:
        return self._connection.execute(*args, **kwargs)

    def commit(self) -> None:
        self.commit_called = True
        self._connection.commit()

    def rollback(self) -> None:
        self._connection.rollback()

    def close(self) -> None:
        self._connection.close()
