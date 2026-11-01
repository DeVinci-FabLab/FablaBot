"""The bot's SQLite database: one connection, opened at start and used from the event loop only."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
import logging
from pathlib import Path
import sqlite3
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Iterator

logger = logging.getLogger(__name__)

DATABASE_FILE: Final = Path("data/fablabot.db")
MIGRATIONS_DIR: Final = Path(__file__).resolve().parent / "migrations"

_connection: sqlite3.Connection | None = None


class StorageError(Exception):
    """The database cannot be used; the message is one line for the operator."""


def connection() -> sqlite3.Connection:
    """Return the open connection.

    Returns:
        sqlite3.Connection: The connection ``open_database`` opened.

    Raises:
        RuntimeError: If the database is not open.
    """
    if _connection is None:
        msg = "The database is not open."
        raise RuntimeError(msg)
    return _connection


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    """Run a block of writes as one transaction, rolled back if the block raises.

    Yields:
        sqlite3.Connection: The connection to write through.
    """
    conn = connection()
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def _configure(conn: sqlite3.Connection) -> None:
    """Apply the settings the bot relies on.

    Args:
        conn (sqlite3.Connection): The connection, in autocommit: writes go through ``transaction``.
    """
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # One connection, no concurrent reader: the default journal leaves one file at rest.
    conn.execute("PRAGMA journal_mode = DELETE")
    # In production /tmp is 1 MB and the root filesystem is read-only.
    conn.execute("PRAGMA temp_store = MEMORY")


def _backup(conn: sqlite3.Connection, path: Path, version: int) -> None:
    """Copy the database next to it before its schema changes; copies are never removed.

    Args:
        conn (sqlite3.Connection): The open connection.
        path (Path): The database file.
        version (int): The schema version being left.
    """
    folder = path.parent / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    copy = sqlite3.connect(folder / f"{path.stem}-v{version}-{stamp}.db")
    try:
        conn.backup(copy)
    finally:
        copy.close()


def _migrate(conn: sqlite3.Connection, path: Path, migrations: Path) -> None:
    """Bring the schema to the latest version, one transaction per file, forward only.

    Args:
        conn (sqlite3.Connection): The open connection.
        path (Path): The database file.
        migrations (Path): The folder of ``NNNN_slug.sql`` files.

    Raises:
        StorageError: If the database is newer than this code.
    """
    files = sorted(migrations.glob("[0-9][0-9][0-9][0-9]_*.sql"))
    latest = int(files[-1].name[:4])
    current: int = conn.execute("PRAGMA user_version").fetchone()[0]
    if current > latest:
        msg = f"{path.as_posix()} is at schema version {current}, newer than this release ({latest}): run a newer image."
        raise StorageError(msg)
    if 0 < current < latest:
        _backup(conn, path, current)
    for file in files:
        number = int(file.name[:4])
        if number <= current:
            continue
        try:
            conn.executescript(
                f"BEGIN IMMEDIATE;\n{file.read_text(encoding='utf-8')}\nPRAGMA user_version = {number};\nCOMMIT;"
            )
        except sqlite3.Error:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        logger.info(f"Database schema moved to version {number} ({file.name}).")


def open_database(path: Path = DATABASE_FILE, *, migrations: Path = MIGRATIONS_DIR) -> None:
    """Open the database, creating it when missing, and bring its schema up to date.

    Args:
        path (Path): The database file.
        migrations (Path): The folder of migration files.

    Raises:
        StorageError: If the database cannot be used.
    """
    global _connection
    close_database()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, autocommit=True)
    try:
        _configure(conn)
        _migrate(conn, path, migrations)
    except sqlite3.DatabaseError as error:
        conn.close()
        msg = f"{path.as_posix()} cannot be used: {error}."
        raise StorageError(msg) from error
    except BaseException:
        conn.close()
        raise
    _connection = conn


def close_database() -> None:
    """Close the connection, if any."""
    global _connection
    if _connection is not None:
        _connection.close()
        _connection = None
