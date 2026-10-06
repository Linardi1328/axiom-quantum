from __future__ import annotations

import sqlite3
from pathlib import Path

from spy_market_agent.persistence.models import DatabasePath, PersistenceSchemaError
from spy_market_agent.persistence.schema import initialize_schema


def connect_database(database_path: DatabasePath, *, create: bool = False) -> sqlite3.Connection:
    path = _database_path(database_path)
    if path != ":memory:":
        file_path = Path(path)
        if create:
            file_path.parent.mkdir(parents=True, exist_ok=True)
        elif not file_path.exists():
            raise PersistenceSchemaError(
                "database_unavailable",
                "database is unavailable or has not been explicitly initialized.",
            )
    try:
        connection = sqlite3.connect(path)
    except sqlite3.Error as exc:
        raise PersistenceSchemaError(
            "database_unavailable",
            "database is unavailable.",
        ) from exc
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()
    if foreign_keys is None or int(foreign_keys[0]) != 1:
        connection.close()
        raise PersistenceSchemaError(
            "foreign_keys_unavailable",
            "SQLite foreign-key enforcement could not be enabled.",
        )
    return connection


def initialize_database(database_path: DatabasePath) -> None:
    connection = connect_database(database_path, create=True)
    try:
        connection.execute("BEGIN")
        initialize_schema(connection)
        connection.commit()
    except sqlite3.Error as exc:
        connection.rollback()
        raise PersistenceSchemaError(
            "schema_initialization_failed",
            "database schema initialization failed.",
        ) from exc
    finally:
        connection.close()


def _database_path(database_path: DatabasePath) -> str:
    if isinstance(database_path, Path):
        return str(database_path)
    if isinstance(database_path, str) and database_path:
        return database_path
    raise PersistenceSchemaError("invalid_database_path", "database_path must be non-empty.")


__all__ = ["connect_database", "initialize_database"]
