from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Any, cast

from spy_market_agent.config import Settings
from spy_market_agent.execution.errors import (
    PaperExecutionDuplicateError,
    PaperExecutionInputError,
    PaperExecutionIntegrityError,
    PaperExecutionNotFoundError,
)
from spy_market_agent.execution.identifiers import require_execution_id
from spy_market_agent.execution.models import (
    DISENGAGE_KILL_SWITCH_CONFIRMATION,
    PAPER_ATTEMPT_ACCEPTED,
    PAPER_ATTEMPT_BLOCKED,
    PAPER_ATTEMPT_BROKER_EXISTING_ORDER_FOUND,
    PAPER_ATTEMPT_RECONCILED,
    PAPER_ATTEMPT_REJECTED,
    PAPER_ATTEMPT_RESERVED,
    PAPER_ATTEMPT_STATES,
    PAPER_ATTEMPT_SUBMISSION_UNKNOWN,
    PAPER_EXECUTION_SCHEMA_VERSION,
    PaperExecutionAttempt,
    PaperExecutionControlState,
    PaperExecutionEvent,
    PaperExecutionStatus,
    PaperOrderApproval,
    PaperOrderInstruction,
    PaperOrderReceipt,
)
from spy_market_agent.persistence.database import connect_database
from spy_market_agent.persistence.models import (
    DatabasePath,
    PersistenceError,
    PersistenceIntegrityError,
)
from spy_market_agent.persistence.schema import validate_schema_version
from spy_market_agent.persistence.serialization import (
    bool_to_int,
    canonical_json_dumps,
    canonical_json_loads,
    date_to_text,
    datetime_to_text,
    int_from_storage,
    int_to_bool,
    optional_text,
    required_text,
    text_to_date,
    text_to_datetime,
    validate_checksum,
)

_RECEIPT_TRANSITIONS = {
    PAPER_ATTEMPT_ACCEPTED: {PAPER_ATTEMPT_RESERVED},
    PAPER_ATTEMPT_BROKER_EXISTING_ORDER_FOUND: {PAPER_ATTEMPT_RESERVED},
    PAPER_ATTEMPT_RECONCILED: {
        PAPER_ATTEMPT_RESERVED,
        PAPER_ATTEMPT_SUBMISSION_UNKNOWN,
    },
}
_SUBMISSION_UNKNOWN_PRIOR_STATES = {
    PAPER_ATTEMPT_RESERVED,
    PAPER_ATTEMPT_SUBMISSION_UNKNOWN,
}
_FAILURE_TARGET_STATES = {
    PAPER_ATTEMPT_BLOCKED,
    PAPER_ATTEMPT_REJECTED,
}


class SQLitePaperExecutionRepository:
    """SQLite ledger for Phase 8 paper-execution control, attempts, and events."""

    def __init__(self, database_path: DatabasePath) -> None:
        self._database_path = database_path

    def get_kill_switch_state(self) -> PaperExecutionControlState:
        connection = self._connect()
        try:
            row = connection.execute(
                """
                SELECT kill_switch_engaged, updated_at_utc, reason, control_schema_version
                FROM paper_execution_control
                WHERE singleton_id = 1
                """
            ).fetchone()
            if row is None:
                return PaperExecutionControlState(
                    kill_switch_engaged=True,
                    updated_at_utc=datetime(1970, 1, 1, tzinfo=UTC),
                    reason="missing_state",
                    control_schema_version="missing",
                )
            try:
                return PaperExecutionControlState(
                    kill_switch_engaged=int_to_bool(
                        row["kill_switch_engaged"],
                        field_name="kill_switch_engaged",
                    ),
                    updated_at_utc=text_to_datetime(
                        row["updated_at_utc"],
                        field_name="updated_at_utc",
                    ),
                    reason=required_text(row["reason"], field_name="reason"),
                    control_schema_version=required_text(
                        row["control_schema_version"],
                        field_name="control_schema_version",
                    ),
                )
            except PersistenceIntegrityError as exc:
                raise PaperExecutionIntegrityError(
                    "invalid_kill_switch_state",
                    "paper-execution kill switch state is invalid and must be treated as engaged.",
                ) from exc
        finally:
            connection.close()

    def set_paper_execution_kill_switch(
        self,
        *,
        engaged: bool,
        reason: str,
        updated_at_utc: datetime,
        confirmation: str | None = None,
    ) -> PaperExecutionControlState:
        if type(engaged) is not bool:
            raise PaperExecutionInputError("invalid_kill_switch_state", "engaged must be boolean.")
        safe_reason = _safe_text(reason, field_name="reason")
        if not engaged and confirmation != DISENGAGE_KILL_SWITCH_CONFIRMATION:
            raise PaperExecutionInputError(
                "missing_kill_switch_confirmation",
                "disengaging the paper-execution kill switch requires explicit confirmation.",
            )
        timestamp = _utc_text(updated_at_utc)
        connection = self._connect()
        try:
            connection.execute("BEGIN")
            prior = self._get_control_state_for_update(connection)
            connection.execute(
                """
                INSERT INTO paper_execution_control (
                    singleton_id, kill_switch_engaged, updated_at_utc, reason,
                    control_schema_version
                )
                VALUES (1, ?, ?, ?, 'spy-paper-execution-control-v1')
                ON CONFLICT(singleton_id) DO UPDATE SET
                    kill_switch_engaged = excluded.kill_switch_engaged,
                    updated_at_utc = excluded.updated_at_utc,
                    reason = excluded.reason,
                    control_schema_version = excluded.control_schema_version
                """,
                (bool_to_int(engaged), timestamp, safe_reason),
            )
            self._insert_event(
                connection,
                signal_id=None,
                client_order_id=None,
                event_type="kill_switch_updated",
                prior_state="engaged" if prior.kill_switch_engaged else "disengaged",
                new_state="engaged" if engaged else "disengaged",
                event_timestamp_utc=updated_at_utc,
                safe_reason_code=safe_reason,
                safe_metadata={"engaged": engaged},
            )
            connection.commit()
            return self.get_kill_switch_state()
        except sqlite3.Error as exc:
            connection.rollback()
            raise PaperExecutionIntegrityError(
                "kill_switch_update_failed",
                "paper-execution kill switch state could not be updated.",
            ) from exc
        finally:
            connection.close()

    def reserve_attempt(
        self,
        instruction: PaperOrderInstruction,
        approval: PaperOrderApproval,
        *,
        execution_risk_approved: bool,
        now_utc: datetime,
    ) -> PaperExecutionAttempt:
        timestamp = _utc_text(now_utc)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            _require_symbol_execution_session_available(connection, instruction=instruction)
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
                    bool_to_int(instruction.original_risk_decision.approved),
                    bool_to_int(execution_risk_approved),
                    PAPER_ATTEMPT_RESERVED,
                    timestamp,
                    timestamp,
                ),
            )
            self._insert_event(
                connection,
                signal_id=instruction.signal_id,
                client_order_id=instruction.client_order_id,
                event_type="attempt_reserved",
                prior_state=None,
                new_state=PAPER_ATTEMPT_RESERVED,
                event_timestamp_utc=now_utc,
                safe_reason_code="reserved",
                safe_metadata={"approval_id": approval.approval_id},
            )
            reserved = _get_attempt_from_connection(connection, instruction.client_order_id)
            connection.commit()
            return reserved
        except PaperExecutionDuplicateError:
            connection.rollback()
            raise
        except PaperExecutionNotFoundError as exc:
            connection.rollback()
            raise PaperExecutionIntegrityError(
                "attempt_reservation_failed",
                "paper-execution attempt could not be reserved.",
            ) from exc
        except PaperExecutionIntegrityError:
            connection.rollback()
            raise
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            if _is_execution_session_unique_error(exc):
                raise _execution_session_duplicate_error() from exc
            if _is_identifier_unique_error(exc):
                raise PaperExecutionDuplicateError(
                    "duplicate_execution_identifier",
                    "signal_id, client_order_id, or approval_id has already been reserved.",
                ) from exc
            raise PaperExecutionIntegrityError(
                "attempt_reservation_failed",
                "paper-execution attempt could not be reserved.",
            ) from exc
        except sqlite3.Error as exc:
            connection.rollback()
            raise PaperExecutionIntegrityError(
                "attempt_reservation_failed",
                "paper-execution attempt could not be reserved.",
            ) from exc
        finally:
            connection.close()

    def record_receipt(
        self,
        receipt: PaperOrderReceipt,
        *,
        status: str,
        account_id_fingerprint: str | None,
        now_utc: datetime,
        event_type: str,
    ) -> PaperExecutionAttempt:
        if status not in PAPER_ATTEMPT_STATES:
            raise PaperExecutionInputError(
                "invalid_attempt_status", "attempt status is unsupported."
            )
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            try:
                prior = _get_attempt_from_connection(connection, receipt.client_order_id)
            except PaperExecutionNotFoundError as exc:
                raise PaperExecutionIntegrityError(
                    "receipt_lineage_mismatch",
                    "broker receipt does not match a reserved paper-execution attempt.",
                ) from exc
            _validate_receipt_against_attempt(receipt, prior=prior, status=status)
            cursor = connection.execute(
                """
                UPDATE paper_execution_attempts
                SET attempt_status = ?, broker_order_id = ?, broker_status = ?,
                    broker_environment = ?, account_id_fingerprint = ?,
                    sanitized_request_id = ?, updated_at_utc = ?, failure_code = NULL
                WHERE client_order_id = ?
                """,
                (
                    status,
                    receipt.broker_order_id,
                    receipt.broker_order_status,
                    receipt.execution_environment,
                    account_id_fingerprint,
                    receipt.sanitized_request_id,
                    _utc_text(now_utc),
                    receipt.client_order_id,
                ),
            )
            if cursor.rowcount != 1:
                raise PaperExecutionIntegrityError(
                    "attempt_update_row_count_mismatch",
                    "paper-execution attempt update did not affect exactly one row.",
                )
            self._insert_event(
                connection,
                signal_id=prior.signal_id,
                client_order_id=prior.client_order_id,
                event_type=event_type,
                prior_state=prior.attempt_status,
                new_state=status,
                event_timestamp_utc=now_utc,
                safe_reason_code=status,
                safe_metadata={"broker_status": receipt.broker_order_status},
            )
            updated = _get_attempt_from_connection(connection, receipt.client_order_id)
            connection.commit()
            return updated
        except (PaperExecutionIntegrityError, PaperExecutionNotFoundError):
            connection.rollback()
            raise
        except sqlite3.Error as exc:
            connection.rollback()
            raise PaperExecutionIntegrityError(
                "attempt_update_failed",
                "paper-execution attempt could not be updated.",
            ) from exc
        finally:
            connection.close()

    def mark_submission_unknown(
        self,
        *,
        client_order_id: str,
        signal_id: str,
        failure_code: str,
        now_utc: datetime,
        event_type: str = "submission_unknown",
    ) -> PaperExecutionAttempt:
        parsed_client_id = require_execution_id(client_order_id, field_name="client_order_id")
        parsed_signal_id = require_execution_id(signal_id, field_name="signal_id")
        safe_failure_code = _safe_text(failure_code, field_name="failure_code")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            prior = _get_attempt_from_connection(connection, parsed_client_id)
            _require_signal_matches(parsed_signal_id, prior=prior)
            _require_submission_unknown_transition(prior)
            cursor = connection.execute(
                """
                UPDATE paper_execution_attempts
                SET attempt_status = ?, failure_code = ?, updated_at_utc = ?
                WHERE client_order_id = ?
                """,
                (
                    PAPER_ATTEMPT_SUBMISSION_UNKNOWN,
                    safe_failure_code,
                    _utc_text(now_utc),
                    parsed_client_id,
                ),
            )
            if cursor.rowcount != 1:
                raise PaperExecutionIntegrityError(
                    "attempt_update_row_count_mismatch",
                    "paper-execution attempt update did not affect exactly one row.",
                )
            self._insert_event(
                connection,
                signal_id=prior.signal_id,
                client_order_id=prior.client_order_id,
                event_type=event_type,
                prior_state=prior.attempt_status,
                new_state=PAPER_ATTEMPT_SUBMISSION_UNKNOWN,
                event_timestamp_utc=now_utc,
                safe_reason_code=safe_failure_code,
                safe_metadata={},
            )
            connection.commit()
            return _get_attempt_from_connection(connection, parsed_client_id)
        except (PaperExecutionIntegrityError, PaperExecutionNotFoundError):
            connection.rollback()
            raise
        except sqlite3.Error as exc:
            connection.rollback()
            raise PaperExecutionIntegrityError(
                "attempt_unknown_update_failed",
                "paper-execution attempt could not be updated.",
            ) from exc
        finally:
            connection.close()

    def mark_failure(
        self,
        *,
        client_order_id: str,
        signal_id: str,
        status: str,
        failure_code: str,
        now_utc: datetime,
        event_type: str = "attempt_failed",
    ) -> PaperExecutionAttempt:
        if status not in _FAILURE_TARGET_STATES:
            raise PaperExecutionInputError(
                "invalid_attempt_status", "attempt status is unsupported."
            )
        parsed_client_id = require_execution_id(client_order_id, field_name="client_order_id")
        parsed_signal_id = require_execution_id(signal_id, field_name="signal_id")
        safe_failure_code = _safe_text(failure_code, field_name="failure_code")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            prior = _get_attempt_from_connection(connection, parsed_client_id)
            _require_signal_matches(parsed_signal_id, prior=prior)
            _require_failure_transition(prior, target_status=status)
            cursor = connection.execute(
                """
                UPDATE paper_execution_attempts
                SET attempt_status = ?, failure_code = ?, updated_at_utc = ?
                WHERE client_order_id = ?
                """,
                (status, safe_failure_code, _utc_text(now_utc), parsed_client_id),
            )
            if cursor.rowcount != 1:
                raise PaperExecutionIntegrityError(
                    "attempt_update_row_count_mismatch",
                    "paper-execution attempt update did not affect exactly one row.",
                )
            self._insert_event(
                connection,
                signal_id=prior.signal_id,
                client_order_id=prior.client_order_id,
                event_type=event_type,
                prior_state=prior.attempt_status,
                new_state=status,
                event_timestamp_utc=now_utc,
                safe_reason_code=safe_failure_code,
                safe_metadata={},
            )
            connection.commit()
            return _get_attempt_from_connection(connection, parsed_client_id)
        except (PaperExecutionIntegrityError, PaperExecutionNotFoundError):
            connection.rollback()
            raise
        except sqlite3.Error as exc:
            connection.rollback()
            raise PaperExecutionIntegrityError(
                "attempt_failure_update_failed",
                "paper-execution attempt could not be updated.",
            ) from exc
        finally:
            connection.close()

    def get_attempt(self, client_order_id: str) -> PaperExecutionAttempt:
        parsed_client_id = require_execution_id(client_order_id, field_name="client_order_id")
        connection = self._connect()
        try:
            return _get_attempt_from_connection(connection, parsed_client_id)
        finally:
            connection.close()

    def list_attempts(self, *, limit: int, offset: int) -> tuple[PaperExecutionAttempt, ...]:
        if isinstance(limit, bool) or limit < 1 or limit > 500:
            raise PaperExecutionInputError("invalid_limit", "limit must be between 1 and 500.")
        if isinstance(offset, bool) or offset < 0:
            raise PaperExecutionInputError("invalid_offset", "offset must be nonnegative.")
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT * FROM paper_execution_attempts
                ORDER BY created_at_utc DESC, client_order_id DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()
            return tuple(_attempt_from_row(row) for row in rows)
        finally:
            connection.close()

    def count_attempts(self) -> int:
        connection = self._connect()
        try:
            return int_from_storage(
                connection.execute("SELECT COUNT(*) FROM paper_execution_attempts").fetchone()[0],
                field_name="attempt_count",
                minimum=0,
            )
        finally:
            connection.close()

    def list_events(self, *, client_order_id: str | None = None) -> tuple[PaperExecutionEvent, ...]:
        connection = self._connect()
        try:
            if client_order_id is None:
                rows = connection.execute(
                    "SELECT * FROM paper_execution_events ORDER BY event_id ASC"
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT * FROM paper_execution_events
                    WHERE client_order_id = ?
                    ORDER BY event_id ASC
                    """,
                    (require_execution_id(client_order_id, field_name="client_order_id"),),
                ).fetchall()
            return tuple(_event_from_row(row) for row in rows)
        finally:
            connection.close()

    def status(self, settings: Settings) -> PaperExecutionStatus:
        control = self.get_kill_switch_state()
        connection = self._connect()
        try:
            last = connection.execute(
                """
                SELECT attempt_status
                FROM paper_execution_attempts
                ORDER BY updated_at_utc DESC, client_order_id DESC
                LIMIT 1
                """
            ).fetchone()
            success = connection.execute(
                """
                SELECT updated_at_utc
                FROM paper_execution_attempts
                WHERE attempt_status IN (?, ?, ?)
                ORDER BY updated_at_utc DESC
                LIMIT 1
                """,
                (
                    PAPER_ATTEMPT_ACCEPTED,
                    PAPER_ATTEMPT_RECONCILED,
                    PAPER_ATTEMPT_BROKER_EXISTING_ORDER_FOUND,
                ),
            ).fetchone()
            unresolved = connection.execute(
                """
                SELECT COUNT(*)
                FROM paper_execution_attempts
                WHERE attempt_status IN (?, ?)
                """,
                (PAPER_ATTEMPT_RESERVED, PAPER_ATTEMPT_SUBMISSION_UNKNOWN),
            ).fetchone()[0]
            configuration_kill_switch_engaged = settings.paper_execution_kill_switch
            durable_kill_switch_engaged = control.kill_switch_engaged
            effective_kill_switch_engaged = (
                configuration_kill_switch_engaged or durable_kill_switch_engaged
            )
            return PaperExecutionStatus(
                configuration_kill_switch_engaged=configuration_kill_switch_engaged,
                durable_kill_switch_engaged=durable_kill_switch_engaged,
                effective_kill_switch_engaged=effective_kill_switch_engaged,
                kill_switch_engaged=effective_kill_switch_engaged,
                execution_mode=settings.execution_mode,
                paper_execution_enabled=settings.enable_paper_execution,
                dry_run=settings.dry_run,
                alpaca_api_key_present=_secret_present(settings.alpaca_api_key),
                alpaca_secret_key_present=_secret_present(settings.alpaca_secret_key),
                last_local_attempt_status=None
                if last is None
                else required_text(last["attempt_status"], field_name="attempt_status"),
                last_successful_submission_at_utc=None
                if success is None
                else text_to_datetime(success["updated_at_utc"], field_name="updated_at_utc"),
                unresolved_submission_count=int_from_storage(
                    unresolved,
                    field_name="unresolved_submission_count",
                    minimum=0,
                ),
            )
        finally:
            connection.close()

    def _get_control_state_for_update(
        self,
        connection: sqlite3.Connection,
    ) -> PaperExecutionControlState:
        row = connection.execute(
            """
            SELECT kill_switch_engaged, updated_at_utc, reason, control_schema_version
            FROM paper_execution_control
            WHERE singleton_id = 1
            """
        ).fetchone()
        if row is None:
            return PaperExecutionControlState(
                kill_switch_engaged=True,
                updated_at_utc=datetime(1970, 1, 1, tzinfo=UTC),
                reason="missing_state",
                control_schema_version="missing",
            )
        try:
            return PaperExecutionControlState(
                kill_switch_engaged=int_to_bool(
                    row["kill_switch_engaged"],
                    field_name="kill_switch_engaged",
                ),
                updated_at_utc=text_to_datetime(row["updated_at_utc"], field_name="updated_at_utc"),
                reason=required_text(row["reason"], field_name="reason"),
                control_schema_version=required_text(
                    row["control_schema_version"],
                    field_name="control_schema_version",
                ),
            )
        except PersistenceIntegrityError as exc:
            raise PaperExecutionIntegrityError(
                "invalid_kill_switch_state",
                "paper-execution kill switch state is invalid and must be treated as engaged.",
            ) from exc

    def _insert_event(
        self,
        connection: sqlite3.Connection,
        *,
        signal_id: str | None,
        client_order_id: str | None,
        event_type: str,
        prior_state: str | None,
        new_state: str | None,
        event_timestamp_utc: datetime,
        safe_reason_code: str,
        safe_metadata: dict[str, str | int | bool | None],
    ) -> None:
        connection.execute(
            """
            INSERT INTO paper_execution_events (
                signal_id, client_order_id, event_type, prior_state, new_state,
                event_timestamp_utc, safe_reason_code, safe_metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                signal_id,
                client_order_id,
                _safe_text(event_type, field_name="event_type"),
                prior_state,
                new_state,
                _utc_text(event_timestamp_utc),
                _safe_text(safe_reason_code, field_name="safe_reason_code"),
                canonical_json_dumps(cast(Any, safe_metadata)),
            ),
        )

    def _connect(self) -> sqlite3.Connection:
        connection = connect_database(self._database_path, create=False)
        try:
            validate_schema_version(connection)
        except PersistenceError as exc:
            connection.close()
            raise PaperExecutionIntegrityError(
                "paper_execution_ledger_unavailable",
                "paper-execution ledger is unavailable or invalid.",
            ) from exc
        return connection


def _validate_receipt_against_attempt(
    receipt: PaperOrderReceipt,
    *,
    prior: PaperExecutionAttempt,
    status: str,
) -> None:
    allowed_prior_states = _RECEIPT_TRANSITIONS.get(status)
    if allowed_prior_states is None or prior.attempt_status not in allowed_prior_states:
        raise PaperExecutionIntegrityError(
            "invalid_receipt_state_transition",
            "broker receipt cannot update the paper-execution attempt from its current state.",
        )
    if (
        receipt.signal_id != prior.signal_id
        or receipt.client_order_id != prior.client_order_id
        or receipt.instruction_fingerprint != prior.instruction_fingerprint
        or receipt.symbol != prior.symbol
        or receipt.side != prior.side
        or receipt.submitted_quantity != prior.quantity
    ):
        raise PaperExecutionIntegrityError(
            "receipt_lineage_mismatch",
            "broker receipt does not match the reserved paper-execution attempt.",
        )
    if (
        receipt.order_type != "market"
        or receipt.time_in_force != "day"
        or receipt.extended_hours is not False
    ):
        raise PaperExecutionIntegrityError(
            "receipt_contract_mismatch",
            "broker receipt does not match the supported paper-order contract.",
        )
    if receipt.execution_environment != "alpaca_paper":
        raise PaperExecutionIntegrityError(
            "receipt_environment_mismatch",
            "broker receipt environment does not match the approved paper ledger.",
        )


def _require_symbol_execution_session_available(
    connection: sqlite3.Connection,
    *,
    instruction: PaperOrderInstruction,
) -> None:
    row = connection.execute(
        """
        SELECT 1
        FROM paper_execution_attempts
        WHERE symbol = ? AND execution_session = ?
        LIMIT 1
        """,
        (
            instruction.proposed_order.symbol,
            date_to_text(instruction.proposed_order.execution_session),
        ),
    ).fetchone()
    if row is not None:
        raise _execution_session_duplicate_error()


def _execution_session_duplicate_error() -> PaperExecutionDuplicateError:
    return PaperExecutionDuplicateError(
        "execution_session_already_reserved",
        "a paper-execution attempt already exists for this symbol and execution session.",
    )


def _is_execution_session_unique_error(exc: sqlite3.IntegrityError) -> bool:
    message = str(exc).lower()
    return "ux_paper_execution_attempt_symbol_session" in message or (
        "paper_execution_attempts.symbol" in message
        and "paper_execution_attempts.execution_session" in message
    )


def _is_identifier_unique_error(exc: sqlite3.IntegrityError) -> bool:
    message = str(exc).lower()
    return any(
        token in message
        for token in (
            "paper_execution_attempts.client_order_id",
            "paper_execution_attempts.signal_id",
            "paper_execution_attempts.approval_id",
        )
    )


def _require_signal_matches(signal_id: str, *, prior: PaperExecutionAttempt) -> None:
    if signal_id != prior.signal_id:
        raise PaperExecutionIntegrityError(
            "attempt_lineage_mismatch",
            "paper-execution attempt lineage does not match the supplied identifiers.",
        )


def _require_submission_unknown_transition(prior: PaperExecutionAttempt) -> None:
    if prior.attempt_status not in _SUBMISSION_UNKNOWN_PRIOR_STATES:
        raise PaperExecutionIntegrityError(
            "invalid_submission_unknown_transition",
            (
                "paper-execution attempt cannot transition to submission_unknown "
                "from its current state."
            ),
        )


def _require_failure_transition(
    prior: PaperExecutionAttempt,
    *,
    target_status: str,
) -> None:
    if target_status not in _FAILURE_TARGET_STATES:
        raise PaperExecutionIntegrityError(
            "invalid_failure_transition",
            "paper-execution failure transition target is unsupported.",
        )
    if prior.attempt_status != PAPER_ATTEMPT_RESERVED:
        raise PaperExecutionIntegrityError(
            "invalid_failure_transition",
            "paper-execution attempt cannot transition to failure from its current state.",
        )


def _attempt_from_row(row: sqlite3.Row) -> PaperExecutionAttempt:
    try:
        status = required_text(row["attempt_status"], field_name="attempt_status")
        if status not in PAPER_ATTEMPT_STATES:
            raise PersistenceIntegrityError(
                "invalid_attempt_status",
                "attempt_status is unsupported.",
            )
        symbol = required_text(row["symbol"], field_name="symbol")
        if symbol != "SPY":
            raise PersistenceIntegrityError("unsupported_symbol", "symbol must be SPY.")
        side = required_text(row["side"], field_name="side")
        if side not in ("buy", "sell"):
            raise PersistenceIntegrityError("invalid_side", "side must be buy or sell.")
        version = required_text(
            row["execution_schema_version"],
            field_name="execution_schema_version",
        )
        if version != PAPER_EXECUTION_SCHEMA_VERSION:
            raise PersistenceIntegrityError(
                "invalid_execution_schema_version",
                "execution schema version is unsupported.",
            )
        return PaperExecutionAttempt(
            signal_id=require_execution_id(row["signal_id"], field_name="signal_id"),
            client_order_id=require_execution_id(
                row["client_order_id"],
                field_name="client_order_id",
            ),
            approval_id=require_execution_id(row["approval_id"], field_name="approval_id"),
            instruction_fingerprint=validate_checksum(
                row["instruction_fingerprint"],
                field_name="instruction_fingerprint",
            ),
            execution_schema_version=version,
            symbol=symbol,
            side=cast(Any, side),
            quantity=int_from_storage(row["quantity"], field_name="quantity", minimum=1),
            signal_session=text_to_date(row["signal_session"], field_name="signal_session"),
            execution_session=text_to_date(
                row["execution_session"],
                field_name="execution_session",
            ),
            instruction_created_at_utc=text_to_datetime(
                row["instruction_created_at_utc"],
                field_name="instruction_created_at_utc",
            ),
            expires_at_utc=text_to_datetime(row["expires_at_utc"], field_name="expires_at_utc"),
            approval_at_utc=text_to_datetime(
                row["approval_at_utc"],
                field_name="approval_at_utc",
            ),
            approval_source=required_text(row["approval_source"], field_name="approval_source"),
            original_risk_approved=int_to_bool(
                row["original_risk_approved"],
                field_name="original_risk_approved",
            ),
            execution_risk_approved=int_to_bool(
                row["execution_risk_approved"],
                field_name="execution_risk_approved",
            ),
            attempt_status=status,
            broker_order_id=optional_text(row["broker_order_id"], field_name="broker_order_id"),
            broker_status=optional_text(row["broker_status"], field_name="broker_status"),
            broker_environment=optional_text(
                row["broker_environment"],
                field_name="broker_environment",
            ),
            account_id_fingerprint=_optional_checksum(
                row["account_id_fingerprint"],
                field_name="account_id_fingerprint",
            ),
            sanitized_request_id=optional_text(
                row["sanitized_request_id"],
                field_name="sanitized_request_id",
            ),
            created_at_utc=text_to_datetime(row["created_at_utc"], field_name="created_at_utc"),
            updated_at_utc=text_to_datetime(row["updated_at_utc"], field_name="updated_at_utc"),
            failure_code=optional_text(row["failure_code"], field_name="failure_code"),
        )
    except (PaperExecutionInputError, PersistenceIntegrityError, ValueError) as exc:
        raise PaperExecutionIntegrityError(
            "invalid_paper_execution_attempt",
            "persisted paper-execution attempt is invalid.",
        ) from exc


def _event_from_row(row: sqlite3.Row) -> PaperExecutionEvent:
    try:
        metadata = canonical_json_loads(row["safe_metadata_json"], field_name="safe_metadata_json")
        if not isinstance(metadata, dict):
            raise PersistenceIntegrityError(
                "invalid_safe_metadata_json",
                "safe metadata must be a JSON object.",
            )
        return PaperExecutionEvent(
            event_id=int_from_storage(row["event_id"], field_name="event_id", minimum=1),
            signal_id=None
            if row["signal_id"] is None
            else require_execution_id(row["signal_id"], field_name="signal_id"),
            client_order_id=None
            if row["client_order_id"] is None
            else require_execution_id(row["client_order_id"], field_name="client_order_id"),
            event_type=required_text(row["event_type"], field_name="event_type"),
            prior_state=optional_text(row["prior_state"], field_name="prior_state"),
            new_state=optional_text(row["new_state"], field_name="new_state"),
            event_timestamp_utc=text_to_datetime(
                row["event_timestamp_utc"],
                field_name="event_timestamp_utc",
            ),
            safe_reason_code=required_text(
                row["safe_reason_code"],
                field_name="safe_reason_code",
            ),
            safe_metadata=cast(dict[str, str | int | bool | None], metadata),
        )
    except (PaperExecutionInputError, PersistenceIntegrityError, ValueError) as exc:
        raise PaperExecutionIntegrityError(
            "invalid_paper_execution_event",
            "persisted paper-execution event is invalid.",
        ) from exc


def _get_attempt_from_connection(
    connection: sqlite3.Connection,
    client_order_id: str,
) -> PaperExecutionAttempt:
    row = connection.execute(
        "SELECT * FROM paper_execution_attempts WHERE client_order_id = ?",
        (client_order_id,),
    ).fetchone()
    if row is None:
        raise PaperExecutionNotFoundError(
            "paper_order_not_found",
            "paper-order attempt was not found.",
        )
    return _attempt_from_row(row)


def _optional_checksum(value: object, *, field_name: str) -> str | None:
    if value is None:
        return None
    return validate_checksum(value, field_name=field_name)


def _utc_text(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PaperExecutionInputError("invalid_timestamp", "timestamp must be timezone-aware.")
    return datetime_to_text(value)


def _safe_text(value: object, *, field_name: str) -> str:
    if type(value) is not str or value.strip() != value or not value:
        raise PaperExecutionInputError(
            f"invalid_{field_name}",
            f"{field_name} must be nonblank text without surrounding whitespace.",
        )
    lowered = value.lower()
    if any(marker in lowered for marker in ("secret", "api_key", "apikey", "password", "token=")):
        raise PaperExecutionInputError(
            f"unsafe_{field_name}",
            f"{field_name} must not contain credential-like content.",
        )
    return value


def _secret_present(value: object) -> bool:
    if value is None:
        return False
    getter = getattr(value, "get_secret_value", None)
    if getter is None:
        return bool(str(value).strip())
    return bool(str(getter()).strip())


__all__ = ["SQLitePaperExecutionRepository"]
