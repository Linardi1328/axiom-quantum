from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import cast

from spy_market_agent.persistence.serialization import datetime_to_text
from spy_market_agent.run_ids import validate_run_id
from spy_market_agent.shadow.types import (
    PHASE4_PHASE_ID,
    PHASE4_SHADOW_SCHEMA_VERSION,
    FreshnessStatus,
    ModelAdmissionStatus,
    ShadowHealthStatus,
    ShadowMode,
)

SHADOW_DB_SCHEMA_VERSION = "spy-v2-phase4-shadow-db-v1"

_APPLICATION_TABLES = {
    "shadow_schema_metadata",
    "shadow_runs",
    "shadow_input_snapshots",
    "shadow_health_events",
    "shadow_alerts",
}

_SQLITE_INTERNAL_PREFIX = "sqlite_"


class ShadowOperationalRunStatus(StrEnum):
    RESERVED = "reserved"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"


class ShadowPersistenceError(RuntimeError):
    """Base class for Phase 4 shadow persistence failures."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class ShadowSchemaError(ShadowPersistenceError):
    """Raised when a SQLite database is not a valid Phase 4 shadow database."""


class ShadowDuplicateRunError(ShadowPersistenceError):
    """Raised when a deterministic shadow run identity already exists."""


class ShadowRecoveryRequiredError(ShadowPersistenceError):
    """Raised when an earlier run reservation must be reviewed before retry."""


class ShadowRunNotFoundError(ShadowPersistenceError):
    """Raised when an inspected shadow run does not exist."""


@dataclass(frozen=True, slots=True)
class ShadowRunRecord:
    shadow_run_id: str
    configuration_version: str
    mode: ShadowMode
    symbol: str
    timeframe: str
    signal_session: str
    as_of: str
    parent_dataset_id: str
    canonical_dataset_checksum: str
    provider_finalization_policy_id: str
    run_status: ShadowOperationalRunStatus
    freshness_status: FreshnessStatus
    monitoring_status: ShadowHealthStatus
    model_gate_status: ModelAdmissionStatus
    created_at: str
    completed_at: str | None = None
    phase_id: str = PHASE4_PHASE_ID
    artifact_schema_version: str = PHASE4_SHADOW_SCHEMA_VERSION
    db_schema_version: str = SHADOW_DB_SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class ShadowInputSnapshotRecord:
    shadow_run_id: str
    parent_dataset_id: str
    canonical_dataset_checksum: str
    symbol: str
    timeframe: str
    provider: str
    feed: str
    adjustment: str
    first_session: str
    latest_session: str
    target_session: str
    row_count: int
    provider_finalization_policy_id: str
    manifest_artifact_checksum: str | None
    snapshot_created_at: str


@dataclass(frozen=True, slots=True)
class ShadowHealthEventRecord:
    shadow_run_id: str
    event_code: str
    status: ShadowHealthStatus
    message: str
    event_timestamp: str


@dataclass(frozen=True, slots=True)
class ShadowAlertRecord:
    shadow_run_id: str
    alert_code: str
    status: ShadowHealthStatus
    message: str
    created_at: str


@dataclass(frozen=True, slots=True)
class ShadowStoredRun:
    run: ShadowRunRecord
    input_snapshot: ShadowInputSnapshotRecord | None
    health_events: tuple[ShadowHealthEventRecord, ...]
    alerts: tuple[ShadowAlertRecord, ...]


class ShadowSQLiteRepository:
    """Dedicated SQLite repository for Phase 4 observation-only shadow state."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)

    def initialize(self) -> None:
        try:
            with closing(self._connect(create=True)) as connection, connection:
                _validate_or_initialize_schema(connection)
        except ShadowPersistenceError:
            raise
        except sqlite3.Error as exc:
            raise ShadowPersistenceError(
                "persistence_failure",
                "shadow database initialization failed.",
            ) from exc

    def reserve_run(self, run: ShadowRunRecord) -> None:
        validate_run_id(run.shadow_run_id)
        if run.run_status != ShadowOperationalRunStatus.RESERVED:
            raise ShadowPersistenceError(
                "invalid_shadow_run_reservation",
                "shadow run reservation must use reserved status.",
            )

        with closing(self._connect(create=True)) as connection:
            try:
                _validate_or_initialize_schema(connection)
                connection.commit()
                _begin_immediate(connection)
                existing = _fetch_run_row(connection, run.shadow_run_id)
                if existing is not None:
                    _raise_existing_run(existing["run_status"])
                connection.execute(
                    """
                    INSERT INTO shadow_runs (
                        shadow_run_id,
                        phase_id,
                        artifact_schema_version,
                        db_schema_version,
                        configuration_version,
                        mode,
                        symbol,
                        timeframe,
                        signal_session,
                        as_of,
                        parent_dataset_id,
                        canonical_dataset_checksum,
                        provider_finalization_policy_id,
                        run_status,
                        freshness_status,
                        monitoring_status,
                        model_gate_status,
                        created_at,
                        completed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    _run_values(run),
                )
                connection.commit()
            except ShadowPersistenceError:
                _rollback_quietly(connection)
                raise
            except sqlite3.IntegrityError as exc:
                _rollback_quietly(connection)
                raise ShadowDuplicateRunError(
                    "duplicate_run",
                    "shadow run identity has already been reserved.",
                ) from exc
            except sqlite3.Error as exc:
                _rollback_quietly(connection)
                raise ShadowPersistenceError(
                    "persistence_failure",
                    "shadow run reservation failed.",
                ) from exc

    def record_retry_rejection(
        self,
        *,
        shadow_run_id: str,
        event: ShadowHealthEventRecord,
        alert: ShadowAlertRecord,
    ) -> None:
        validate_run_id(shadow_run_id)
        _validate_child_run_ids(
            shadow_run_id=shadow_run_id,
            health_events=(event,),
            alerts=(alert,),
        )

        with closing(self._connect(create=False)) as connection:
            try:
                _validate_or_initialize_schema(connection)
                connection.commit()
                _begin_immediate(connection)
                row = _fetch_run_row(connection, shadow_run_id)
                if row is None:
                    raise ShadowRunNotFoundError(
                        "shadow_run_not_found",
                        "shadow run does not exist for retry-rejection audit.",
                    )
                connection.execute(
                    """
                    INSERT INTO shadow_health_events (
                        shadow_run_id,
                        event_code,
                        status,
                        message,
                        event_timestamp
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    _health_event_values(event),
                )
                connection.execute(
                    """
                    INSERT INTO shadow_alerts (
                        shadow_run_id,
                        alert_code,
                        status,
                        message,
                        created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    _alert_values(alert),
                )
                connection.commit()
            except ShadowPersistenceError:
                _rollback_quietly(connection)
                raise
            except sqlite3.IntegrityError as exc:
                _rollback_quietly(connection)
                raise ShadowPersistenceError(
                    "persistence_failure",
                    "shadow retry-rejection audit failed integrity checks.",
                ) from exc
            except sqlite3.Error as exc:
                _rollback_quietly(connection)
                raise ShadowPersistenceError(
                    "persistence_failure",
                    "shadow retry-rejection audit failed.",
                ) from exc

    def finalize_run(
        self,
        *,
        shadow_run_id: str,
        terminal_status: ShadowOperationalRunStatus,
        freshness_status: FreshnessStatus,
        monitoring_status: ShadowHealthStatus,
        model_gate_status: ModelAdmissionStatus,
        completed_at: datetime,
        input_snapshot: ShadowInputSnapshotRecord,
        health_events: tuple[ShadowHealthEventRecord, ...],
        alerts: tuple[ShadowAlertRecord, ...],
    ) -> None:
        if terminal_status not in (
            ShadowOperationalRunStatus.COMPLETED,
            ShadowOperationalRunStatus.BLOCKED,
            ShadowOperationalRunStatus.FAILED,
        ):
            raise ShadowPersistenceError(
                "invalid_shadow_terminal_status",
                "terminal shadow run status must be completed, blocked, or failed.",
            )
        validate_run_id(shadow_run_id)
        _validate_input_snapshot_run_id(shadow_run_id, input_snapshot)
        _validate_child_run_ids(
            shadow_run_id=shadow_run_id,
            health_events=health_events,
            alerts=alerts,
        )
        with closing(self._connect(create=True)) as connection:
            try:
                _validate_or_initialize_schema(connection)
                connection.commit()
                _begin_immediate(connection)
                row = _fetch_run_row(connection, shadow_run_id)
                if row is None:
                    raise ShadowRunNotFoundError(
                        "shadow_run_not_found",
                        "shadow run reservation does not exist.",
                    )
                if row["run_status"] != ShadowOperationalRunStatus.RESERVED.value:
                    _raise_existing_run(row["run_status"])

                connection.execute(
                    """
                    INSERT INTO shadow_input_snapshots (
                        shadow_run_id,
                        parent_dataset_id,
                        canonical_dataset_checksum,
                        symbol,
                        timeframe,
                        provider,
                        feed,
                        adjustment,
                        first_session,
                        latest_session,
                        target_session,
                        row_count,
                        provider_finalization_policy_id,
                        manifest_artifact_checksum,
                        snapshot_created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    _input_snapshot_values(input_snapshot),
                )
                connection.executemany(
                    """
                    INSERT INTO shadow_health_events (
                        shadow_run_id,
                        event_code,
                        status,
                        message,
                        event_timestamp
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    tuple(_health_event_values(event) for event in health_events),
                )
                connection.executemany(
                    """
                    INSERT INTO shadow_alerts (
                        shadow_run_id,
                        alert_code,
                        status,
                        message,
                        created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    tuple(_alert_values(alert) for alert in alerts),
                )
                connection.execute(
                    """
                    UPDATE shadow_runs
                    SET run_status = ?,
                        freshness_status = ?,
                        monitoring_status = ?,
                        model_gate_status = ?,
                        completed_at = ?
                    WHERE shadow_run_id = ?
                    """,
                    (
                        terminal_status.value,
                        freshness_status.value,
                        monitoring_status.value,
                        model_gate_status.value,
                        datetime_to_text(completed_at),
                        shadow_run_id,
                    ),
                )
                connection.commit()
            except ShadowPersistenceError:
                _rollback_quietly(connection)
                raise
            except sqlite3.IntegrityError as exc:
                _rollback_quietly(connection)
                raise ShadowPersistenceError(
                    "shadow_run_finalize_failed",
                    "shadow run finalization failed integrity checks.",
                ) from exc
            except sqlite3.Error as exc:
                _rollback_quietly(connection)
                raise ShadowPersistenceError(
                    "shadow_run_finalize_failed",
                    "shadow run finalization failed.",
                ) from exc

    def mark_failed(
        self,
        *,
        shadow_run_id: str,
        completed_at: datetime,
        event: ShadowHealthEventRecord,
        alert: ShadowAlertRecord,
    ) -> None:
        validate_run_id(shadow_run_id)
        _validate_child_run_ids(
            shadow_run_id=shadow_run_id,
            health_events=(event,),
            alerts=(alert,),
        )
        with closing(self._connect(create=True)) as connection:
            try:
                _validate_or_initialize_schema(connection)
                connection.commit()
                _begin_immediate(connection)
                row = _fetch_run_row(connection, shadow_run_id)
                if row is None:
                    connection.rollback()
                    return
                if row["run_status"] != ShadowOperationalRunStatus.RESERVED.value:
                    connection.rollback()
                    return
                connection.execute(
                    """
                    INSERT INTO shadow_health_events (
                        shadow_run_id,
                        event_code,
                        status,
                        message,
                        event_timestamp
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    _health_event_values(event),
                )
                connection.execute(
                    """
                    INSERT INTO shadow_alerts (
                        shadow_run_id,
                        alert_code,
                        status,
                        message,
                        created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    _alert_values(alert),
                )
                connection.execute(
                    """
                    UPDATE shadow_runs
                    SET run_status = ?,
                        monitoring_status = ?,
                        completed_at = ?
                    WHERE shadow_run_id = ?
                    """,
                    (
                        ShadowOperationalRunStatus.FAILED.value,
                        ShadowHealthStatus.BLOCKED.value,
                        datetime_to_text(completed_at),
                        shadow_run_id,
                    ),
                )
                connection.commit()
            except ShadowPersistenceError:
                _rollback_quietly(connection)
                raise
            except sqlite3.IntegrityError as exc:
                _rollback_quietly(connection)
                raise ShadowPersistenceError(
                    "persistence_failure",
                    "shadow run failure audit failed integrity checks.",
                ) from exc
            except sqlite3.Error as exc:
                _rollback_quietly(connection)
                raise ShadowPersistenceError(
                    "persistence_failure",
                    "shadow run failure audit failed.",
                ) from exc

    def get_run(self, shadow_run_id: str) -> ShadowStoredRun:
        validate_run_id(shadow_run_id)
        try:
            with closing(self._connect(create=False)) as connection, connection:
                _validate_or_initialize_schema(connection)
                row = _fetch_run_row(connection, shadow_run_id)
                if row is None:
                    raise ShadowRunNotFoundError(
                        "shadow_run_not_found",
                        "shadow run does not exist.",
                    )
                snapshot_row = connection.execute(
                    """
                    SELECT *
                    FROM shadow_input_snapshots
                    WHERE shadow_run_id = ?
                    """,
                    (shadow_run_id,),
                ).fetchone()
                event_rows = connection.execute(
                    """
                    SELECT *
                    FROM shadow_health_events
                    WHERE shadow_run_id = ?
                    ORDER BY id ASC
                    """,
                    (shadow_run_id,),
                ).fetchall()
                alert_rows = connection.execute(
                    """
                    SELECT *
                    FROM shadow_alerts
                    WHERE shadow_run_id = ?
                    ORDER BY id ASC
                    """,
                    (shadow_run_id,),
                ).fetchall()
        except ShadowPersistenceError:
            raise
        except sqlite3.Error as exc:
            raise ShadowPersistenceError(
                "persistence_failure",
                "shadow run inspection failed.",
            ) from exc
        return ShadowStoredRun(
            run=_run_record_from_row(row),
            input_snapshot=_input_snapshot_from_row(snapshot_row) if snapshot_row else None,
            health_events=tuple(_health_event_from_row(event_row) for event_row in event_rows),
            alerts=tuple(_alert_from_row(alert_row) for alert_row in alert_rows),
        )

    def find_run(self, shadow_run_id: str) -> ShadowStoredRun | None:
        validate_run_id(shadow_run_id)
        try:
            with closing(self._connect(create=False)) as connection, connection:
                _validate_existing_schema(connection)
                row = _fetch_run_row(connection, shadow_run_id)
                if row is None:
                    return None
                snapshot_row = connection.execute(
                    """
                    SELECT *
                    FROM shadow_input_snapshots
                    WHERE shadow_run_id = ?
                    """,
                    (shadow_run_id,),
                ).fetchone()
                event_rows = connection.execute(
                    """
                    SELECT *
                    FROM shadow_health_events
                    WHERE shadow_run_id = ?
                    ORDER BY id ASC
                    """,
                    (shadow_run_id,),
                ).fetchall()
                alert_rows = connection.execute(
                    """
                    SELECT *
                    FROM shadow_alerts
                    WHERE shadow_run_id = ?
                    ORDER BY id ASC
                    """,
                    (shadow_run_id,),
                ).fetchall()
        except ShadowPersistenceError:
            raise
        except sqlite3.Error as exc:
            raise ShadowPersistenceError(
                "persistence_failure",
                "shadow run lookup failed.",
            ) from exc
        return ShadowStoredRun(
            run=_run_record_from_row(row),
            input_snapshot=_input_snapshot_from_row(snapshot_row) if snapshot_row else None,
            health_events=tuple(_health_event_from_row(event_row) for event_row in event_rows),
            alerts=tuple(_alert_from_row(alert_row) for alert_row in alert_rows),
        )

    def list_runs(self, *, limit: int = 20) -> tuple[ShadowRunRecord, ...]:
        if limit <= 0:
            raise ShadowPersistenceError("invalid_list_limit", "list limit must be positive.")
        try:
            with closing(self._connect(create=False)) as connection, connection:
                _validate_or_initialize_schema(connection)
                rows = connection.execute(
                    """
                    SELECT *
                    FROM shadow_runs
                    ORDER BY created_at DESC, shadow_run_id ASC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
        except ShadowPersistenceError:
            raise
        except sqlite3.Error as exc:
            raise ShadowPersistenceError(
                "persistence_failure",
                "shadow run listing failed.",
            ) from exc
        return tuple(_run_record_from_row(row) for row in rows)

    def list_observation_runs(self) -> tuple[ShadowRunRecord, ...]:
        try:
            with closing(self._connect(create=False)) as connection, connection:
                _validate_existing_schema(connection)
                rows = connection.execute(
                    """
                    SELECT *
                    FROM shadow_runs
                    WHERE mode = ?
                    ORDER BY signal_session ASC, created_at ASC, shadow_run_id ASC
                    """,
                    (ShadowMode.OBSERVATION_ONLY_NO_MODEL.value,),
                ).fetchall()
        except ShadowPersistenceError:
            raise
        except sqlite3.Error as exc:
            raise ShadowPersistenceError(
                "persistence_failure",
                "shadow observation history lookup failed.",
            ) from exc
        return tuple(_run_record_from_row(row) for row in rows)

    def _connect(self, *, create: bool) -> sqlite3.Connection:
        if not create and not self.database_path.exists():
            raise ShadowSchemaError(
                "shadow_database_missing",
                "shadow database does not exist.",
            )
        if create:
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(self.database_path, timeout=1.0)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
        except sqlite3.Error as exc:
            if connection is not None:
                connection.close()
            raise ShadowPersistenceError(
                "shadow_database_unavailable",
                "shadow database cannot be opened.",
            ) from exc
        return connection


def initialize_shadow_database(database_path: str | Path) -> ShadowSQLiteRepository:
    repository = ShadowSQLiteRepository(database_path)
    repository.initialize()
    return repository


def _validate_or_initialize_schema(connection: sqlite3.Connection) -> None:
    tables = _application_table_names(connection)
    non_shadow_tables = sorted(table for table in tables if not table.startswith("shadow_"))
    if non_shadow_tables:
        raise ShadowSchemaError(
            "mixed_shadow_database",
            "shadow database must not contain non-shadow application tables.",
        )
    if not tables:
        _create_schema(connection)
        return
    unexpected_shadow_tables = sorted(tables - _APPLICATION_TABLES)
    if unexpected_shadow_tables:
        raise ShadowSchemaError(
            "unsupported_shadow_database",
            "shadow database contains unsupported shadow tables.",
        )
    missing_tables = sorted(_APPLICATION_TABLES - tables)
    if missing_tables:
        raise ShadowSchemaError(
            "incomplete_shadow_schema",
            "shadow database schema is incomplete.",
        )
    row = connection.execute(
        """
        SELECT schema_version
        FROM shadow_schema_metadata
        WHERE singleton_id = 1
        """
    ).fetchone()
    if row is None or row["schema_version"] != SHADOW_DB_SCHEMA_VERSION:
        raise ShadowSchemaError(
            "incompatible_shadow_schema",
            "shadow database schema version is incompatible.",
        )


def _validate_existing_schema(connection: sqlite3.Connection) -> None:
    tables = _application_table_names(connection)
    if not tables:
        raise ShadowSchemaError(
            "incomplete_shadow_schema",
            "shadow database does not contain an initialized Phase 4 shadow schema.",
        )
    non_shadow_tables = sorted(table for table in tables if not table.startswith("shadow_"))
    if non_shadow_tables:
        raise ShadowSchemaError(
            "mixed_shadow_database",
            "shadow database must not contain non-shadow application tables.",
        )
    unexpected_shadow_tables = sorted(tables - _APPLICATION_TABLES)
    if unexpected_shadow_tables:
        raise ShadowSchemaError(
            "unsupported_shadow_database",
            "shadow database contains unsupported shadow tables.",
        )
    missing_tables = sorted(_APPLICATION_TABLES - tables)
    if missing_tables:
        raise ShadowSchemaError(
            "incomplete_shadow_schema",
            "shadow database schema is incomplete.",
        )
    row = connection.execute(
        """
        SELECT schema_version
        FROM shadow_schema_metadata
        WHERE singleton_id = 1
        """
    ).fetchone()
    if row is None or row["schema_version"] != SHADOW_DB_SCHEMA_VERSION:
        raise ShadowSchemaError(
            "incompatible_shadow_schema",
            "shadow database schema version is incompatible.",
        )


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE shadow_schema_metadata (
            singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
            schema_version TEXT NOT NULL
        );

        INSERT INTO shadow_schema_metadata (singleton_id, schema_version)
        VALUES (1, 'spy-v2-phase4-shadow-db-v1');

        CREATE TABLE shadow_runs (
            shadow_run_id TEXT PRIMARY KEY,
            phase_id TEXT NOT NULL,
            artifact_schema_version TEXT NOT NULL,
            db_schema_version TEXT NOT NULL,
            configuration_version TEXT NOT NULL,
            mode TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            signal_session TEXT NOT NULL,
            as_of TEXT NOT NULL,
            parent_dataset_id TEXT NOT NULL,
            canonical_dataset_checksum TEXT NOT NULL,
            provider_finalization_policy_id TEXT NOT NULL,
            run_status TEXT NOT NULL,
            freshness_status TEXT NOT NULL,
            monitoring_status TEXT NOT NULL,
            model_gate_status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            completed_at TEXT
        );

        CREATE TABLE shadow_input_snapshots (
            shadow_run_id TEXT PRIMARY KEY,
            parent_dataset_id TEXT NOT NULL,
            canonical_dataset_checksum TEXT NOT NULL,
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            provider TEXT NOT NULL,
            feed TEXT NOT NULL,
            adjustment TEXT NOT NULL,
            first_session TEXT NOT NULL,
            latest_session TEXT NOT NULL,
            target_session TEXT NOT NULL,
            row_count INTEGER NOT NULL,
            provider_finalization_policy_id TEXT NOT NULL,
            manifest_artifact_checksum TEXT,
            snapshot_created_at TEXT NOT NULL,
            FOREIGN KEY (shadow_run_id)
                REFERENCES shadow_runs (shadow_run_id)
                ON DELETE RESTRICT
        );

        CREATE TABLE shadow_health_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shadow_run_id TEXT NOT NULL,
            event_code TEXT NOT NULL,
            status TEXT NOT NULL,
            message TEXT NOT NULL,
            event_timestamp TEXT NOT NULL,
            FOREIGN KEY (shadow_run_id)
                REFERENCES shadow_runs (shadow_run_id)
                ON DELETE RESTRICT
        );

        CREATE TABLE shadow_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shadow_run_id TEXT NOT NULL,
            alert_code TEXT NOT NULL,
            status TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (shadow_run_id)
                REFERENCES shadow_runs (shadow_run_id)
                ON DELETE RESTRICT
        );

        CREATE INDEX shadow_health_events_run_idx
            ON shadow_health_events (shadow_run_id, id);
        CREATE INDEX shadow_alerts_run_idx
            ON shadow_alerts (shadow_run_id, id);
        """
    )


def _application_table_names(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
        """
    ).fetchall()
    return {row["name"] for row in rows if not str(row["name"]).startswith(_SQLITE_INTERNAL_PREFIX)}


def _begin_immediate(connection: sqlite3.Connection) -> None:
    try:
        connection.execute("BEGIN IMMEDIATE")
    except sqlite3.Error as exc:
        raise ShadowPersistenceError(
            "persistence_failure",
            "shadow database write reservation failed.",
        ) from exc


def _rollback_quietly(connection: sqlite3.Connection) -> None:
    try:
        connection.rollback()
    except sqlite3.Error:
        return


def _validate_input_snapshot_run_id(
    shadow_run_id: str,
    input_snapshot: ShadowInputSnapshotRecord,
) -> None:
    if input_snapshot.shadow_run_id != shadow_run_id:
        raise ShadowPersistenceError(
            "audit_identity_mismatch",
            "input snapshot run identity must match the shadow run being finalized.",
        )


def _validate_child_run_ids(
    *,
    shadow_run_id: str,
    health_events: tuple[ShadowHealthEventRecord, ...],
    alerts: tuple[ShadowAlertRecord, ...],
) -> None:
    mismatched_event = next(
        (event for event in health_events if event.shadow_run_id != shadow_run_id),
        None,
    )
    if mismatched_event is not None:
        raise ShadowPersistenceError(
            "audit_identity_mismatch",
            "health event run identity must match the shadow run being audited.",
        )
    mismatched_alert = next(
        (alert for alert in alerts if alert.shadow_run_id != shadow_run_id),
        None,
    )
    if mismatched_alert is not None:
        raise ShadowPersistenceError(
            "audit_identity_mismatch",
            "alert run identity must match the shadow run being audited.",
        )


def _fetch_run_row(connection: sqlite3.Connection, shadow_run_id: str) -> sqlite3.Row | None:
    row = connection.execute(
        """
        SELECT *
        FROM shadow_runs
        WHERE shadow_run_id = ?
        """,
        (shadow_run_id,),
    ).fetchone()
    return cast(sqlite3.Row | None, row)


def _raise_existing_run(status: str) -> None:
    if status == ShadowOperationalRunStatus.RESERVED.value:
        raise ShadowRecoveryRequiredError(
            "recovery_required",
            "a prior shadow run reservation is incomplete and requires review.",
        )
    if status in {
        ShadowOperationalRunStatus.COMPLETED.value,
        ShadowOperationalRunStatus.BLOCKED.value,
        ShadowOperationalRunStatus.FAILED.value,
    }:
        raise ShadowDuplicateRunError(
            "duplicate_run",
            "shadow run identity already has a terminal record.",
        )
    raise ShadowRecoveryRequiredError(
        "recovery_required",
        "shadow run identity has an unknown stored lifecycle status.",
    )


def _run_values(run: ShadowRunRecord) -> tuple[object, ...]:
    return (
        run.shadow_run_id,
        run.phase_id,
        run.artifact_schema_version,
        run.db_schema_version,
        run.configuration_version,
        run.mode.value,
        run.symbol,
        run.timeframe,
        run.signal_session,
        run.as_of,
        run.parent_dataset_id,
        run.canonical_dataset_checksum,
        run.provider_finalization_policy_id,
        run.run_status.value,
        run.freshness_status.value,
        run.monitoring_status.value,
        run.model_gate_status.value,
        run.created_at,
        run.completed_at,
    )


def _input_snapshot_values(snapshot: ShadowInputSnapshotRecord) -> tuple[object, ...]:
    return (
        snapshot.shadow_run_id,
        snapshot.parent_dataset_id,
        snapshot.canonical_dataset_checksum,
        snapshot.symbol,
        snapshot.timeframe,
        snapshot.provider,
        snapshot.feed,
        snapshot.adjustment,
        snapshot.first_session,
        snapshot.latest_session,
        snapshot.target_session,
        snapshot.row_count,
        snapshot.provider_finalization_policy_id,
        snapshot.manifest_artifact_checksum,
        snapshot.snapshot_created_at,
    )


def _health_event_values(event: ShadowHealthEventRecord) -> tuple[str, str, str, str, str]:
    return (
        event.shadow_run_id,
        event.event_code,
        event.status.value,
        event.message,
        event.event_timestamp,
    )


def _alert_values(alert: ShadowAlertRecord) -> tuple[str, str, str, str, str]:
    return (
        alert.shadow_run_id,
        alert.alert_code,
        alert.status.value,
        alert.message,
        alert.created_at,
    )


def _run_record_from_row(row: sqlite3.Row) -> ShadowRunRecord:
    return ShadowRunRecord(
        shadow_run_id=row["shadow_run_id"],
        phase_id=row["phase_id"],
        artifact_schema_version=row["artifact_schema_version"],
        db_schema_version=row["db_schema_version"],
        configuration_version=row["configuration_version"],
        mode=ShadowMode(row["mode"]),
        symbol=row["symbol"],
        timeframe=row["timeframe"],
        signal_session=row["signal_session"],
        as_of=row["as_of"],
        parent_dataset_id=row["parent_dataset_id"],
        canonical_dataset_checksum=row["canonical_dataset_checksum"],
        provider_finalization_policy_id=row["provider_finalization_policy_id"],
        run_status=ShadowOperationalRunStatus(row["run_status"]),
        freshness_status=FreshnessStatus(row["freshness_status"]),
        monitoring_status=ShadowHealthStatus(row["monitoring_status"]),
        model_gate_status=ModelAdmissionStatus(row["model_gate_status"]),
        created_at=row["created_at"],
        completed_at=row["completed_at"],
    )


def _input_snapshot_from_row(row: sqlite3.Row) -> ShadowInputSnapshotRecord:
    return ShadowInputSnapshotRecord(
        shadow_run_id=row["shadow_run_id"],
        parent_dataset_id=row["parent_dataset_id"],
        canonical_dataset_checksum=row["canonical_dataset_checksum"],
        symbol=row["symbol"],
        timeframe=row["timeframe"],
        provider=row["provider"],
        feed=row["feed"],
        adjustment=row["adjustment"],
        first_session=row["first_session"],
        latest_session=row["latest_session"],
        target_session=row["target_session"],
        row_count=row["row_count"],
        provider_finalization_policy_id=row["provider_finalization_policy_id"],
        manifest_artifact_checksum=row["manifest_artifact_checksum"],
        snapshot_created_at=row["snapshot_created_at"],
    )


def _health_event_from_row(row: sqlite3.Row) -> ShadowHealthEventRecord:
    return ShadowHealthEventRecord(
        shadow_run_id=row["shadow_run_id"],
        event_code=row["event_code"],
        status=ShadowHealthStatus(row["status"]),
        message=row["message"],
        event_timestamp=row["event_timestamp"],
    )


def _alert_from_row(row: sqlite3.Row) -> ShadowAlertRecord:
    return ShadowAlertRecord(
        shadow_run_id=row["shadow_run_id"],
        alert_code=row["alert_code"],
        status=ShadowHealthStatus(row["status"]),
        message=row["message"],
        created_at=row["created_at"],
    )


__all__ = [
    "SHADOW_DB_SCHEMA_VERSION",
    "ShadowAlertRecord",
    "ShadowDuplicateRunError",
    "ShadowHealthEventRecord",
    "ShadowInputSnapshotRecord",
    "ShadowOperationalRunStatus",
    "ShadowPersistenceError",
    "ShadowRecoveryRequiredError",
    "ShadowRunNotFoundError",
    "ShadowRunRecord",
    "ShadowSQLiteRepository",
    "ShadowSchemaError",
    "ShadowStoredRun",
    "initialize_shadow_database",
]
