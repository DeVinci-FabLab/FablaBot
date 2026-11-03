"""The bot's SQLite database: one connection, opened at start and used from the event loop only."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
import hashlib
from importlib.metadata import PackageNotFoundError, version
import logging
from pathlib import Path
import re
import sqlite3
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Iterator

logger = logging.getLogger(__name__)

DATABASE_FILE: Final = Path("data/fablabot.db")
MIGRATIONS_DIR: Final = Path(__file__).resolve().parent / "migrations"
# What older releases kept as JSON. command_sync_state.json is no state to move: it stays a file.
STATE_FILES: Final = ("guild_config.json", "formations_state.json", "messages_state.json", "temp_admin_state.json")

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
        conn.execute("COMMIT")
    except BaseException:
        # A refused COMMIT can leave the transaction open: it must not outlive the block.
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


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


def _backup(conn: sqlite3.Connection, path: Path, label: str) -> None:
    """Copy the database next to it before a change of schema or of release; copies are never removed.

    A failed upgrade restarts in a loop, so a copy newer than the database stands for it: one copy per data.

    Args:
        conn (sqlite3.Connection): The open connection.
        path (Path): The database file.
        label (str): What is being left: ``v<schema version>`` or the release that last opened the file.
    """
    folder = path.parent / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    changed = path.stat().st_mtime_ns
    if any(copy.stat().st_mtime_ns >= changed for copy in folder.glob(f"{path.stem}-{label}-*.db")):
        return
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    target = folder / f"{path.stem}-{label}-{stamp}.db"
    partial = folder / f"{path.stem}-{label}.partial"
    partial.unlink(missing_ok=True)
    try:
        _copy_to(conn, partial)
        partial.replace(target)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise


def _copy_to(conn: sqlite3.Connection, target: Path) -> None:
    """Write a whole copy of the database to ``target``."""
    copy = sqlite3.connect(target)
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
        _backup(conn, path, f"v{current}")
    # Off while files run: a table rebuild drops the old table, which would cascade into its children.
    # The pragma does nothing inside a transaction, so it is set here, and each file is checked instead.
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        for file in files:
            number = int(file.name[:4])
            if number > current:
                _apply(conn, path, file, number)
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def _check_parents(conn: sqlite3.Connection, path: Path, file: Path) -> None:
    """Refuse a migration that leaves a row pointing at a row that does not exist.

    Args:
        conn (sqlite3.Connection): The open connection, inside the migration's transaction.
        path (Path): The database file.
        file (Path): The migration file.

    Raises:
        StorageError: If a table holds such rows.
    """
    tables = sorted({row[0] for row in conn.execute("PRAGMA foreign_key_check")})
    if tables:
        msg = f"{path.as_posix()} cannot be used: {file.name} leaves rows without a parent in {', '.join(tables)}."
        raise StorageError(msg)


def _apply(conn: sqlite3.Connection, path: Path, file: Path, number: int) -> None:
    """Run one migration file as one transaction, taken back if it fails or breaks a foreign key.

    Args:
        conn (sqlite3.Connection): The open connection, with foreign keys off.
        path (Path): The database file.
        file (Path): The migration file.
        number (int): Its number, the schema version it leads to.
    """
    try:
        conn.executescript(f"BEGIN IMMEDIATE;\n{file.read_text(encoding='utf-8')}\nPRAGMA user_version = {number};")
        _check_parents(conn, path, file)
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    logger.info(f"Database schema moved to version {number} ({file.name}).")


def _release() -> str:
    """Return the release of the running code, ``unknown`` outside an installed package."""
    try:
        return version("fablabot")
    except PackageNotFoundError:
        return "unknown"


def _release_file(path: Path) -> Path:
    """Return the file naming the release that last opened the database."""
    return path.with_suffix(".release")


def _backup_on_release_change(conn: sqlite3.Connection, path: Path) -> None:
    """Copy a database that another release opened last, or one whose last release is not known.

    Args:
        conn (sqlite3.Connection): The open connection, on a file that existed before this start.
        path (Path): The database file.
    """
    marker = _release_file(path)
    last = marker.read_text(encoding="utf-8").strip() if marker.exists() else ""
    if last != _release():
        # The name goes into a file name: anything but a version reads as unknown.
        _backup(conn, path, last if re.fullmatch(r"[0-9A-Za-z.+-]+", last) else "unknown")


def _not_imported(files: list[Path], path: Path) -> StorageError:
    """Build the refusal for state files the database does not hold.

    Args:
        files (list[Path]): The files.
        path (Path): The database file.

    Returns:
        StorageError: The error to raise.
    """
    names = ", ".join(file.as_posix() for file in files)
    return StorageError(f"{names} not imported into {path.as_posix()}; nothing was changed, see docs/OPERATIONS.md.")


def _check_imported(conn: sqlite3.Connection, path: Path, files: list[Path]) -> None:
    """Refuse state files the database does not hold, or that changed since it took them.

    This runs before the schema moves: a refused start changes nothing, backups included.

    Args:
        conn (sqlite3.Connection): The open connection.
        path (Path): The database file.
        files (list[Path]): The state files found next to it.

    Raises:
        StorageError: If a file was never imported, or no longer matches its import.
    """
    if not files:
        return
    # A file with no schema yet has imported nothing, and nothing may create the schema before this check says so.
    imported: dict[str, str] = {}
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'state_import'").fetchone():
        imported = {row["file_name"]: row["sha256"] for row in conn.execute("SELECT file_name, sha256 FROM state_import")}
    missing = [file for file in files if file.name not in imported]
    if missing:
        raise _not_imported(missing, path)
    changed = [file for file in files if hashlib.sha256(file.read_bytes()).hexdigest() != imported[file.name]]
    if changed:
        names = ", ".join(file.as_posix() for file in changed)
        msg = f"{names} changed since it was imported into {path.as_posix()}; nothing was changed, see docs/OPERATIONS.md."
        raise StorageError(msg)


def _check_writable(conn: sqlite3.Connection) -> None:
    """Refuse a database the bot can read but not write, by a write that is taken back.

    Args:
        conn (sqlite3.Connection): The open connection, in autocommit.
    """
    version: int = conn.execute("PRAGMA user_version").fetchone()[0]
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(f"PRAGMA user_version = {version}")
    finally:
        if conn.in_transaction:
            conn.execute("ROLLBACK")


def _state_files(path: Path) -> list[Path]:
    """List the JSON state files next to the database, refusing them when it does not exist yet.

    Args:
        path (Path): The database file.

    Returns:
        list[Path]: The state files found.

    Raises:
        StorageError: If there are state files and no database to hold them.
    """
    files = [file for name in STATE_FILES if (file := path.parent / name).exists()]
    if files and not path.exists():
        raise _not_imported(files, path)
    return files


def open_database(path: Path = DATABASE_FILE, *, migrations: Path = MIGRATIONS_DIR, gate: bool = True) -> None:
    """Open the database, creating it when missing, copy it when another release opened it last, and move its schema.

    JSON state left by an older release must have been imported first: the bot never starts over
    it, and a refused start creates and changes nothing.

    Args:
        path (Path): The database file.
        migrations (Path): The folder of migration files.
        gate (bool): False for the import alone, which exists to pass this gate.

    Raises:
        StorageError: If the database cannot be used.
    """
    global _connection
    close_database()
    conn: sqlite3.Connection | None = None
    try:
        state_files = _state_files(path) if gate else []
        existed = path.exists()
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, autocommit=True)
        _configure(conn)
        _check_imported(conn, path, state_files)
        if existed:
            _backup_on_release_change(conn, path)
        _migrate(conn, path, migrations)
        _check_writable(conn)
        _release_file(path).write_text(_release(), encoding="utf-8")
    except (sqlite3.DatabaseError, OSError) as error:
        if conn is not None:
            conn.close()
        msg = f"{path.as_posix()} cannot be used: {error}."
        raise StorageError(msg) from error
    except BaseException:
        if conn is not None:
            conn.close()
        raise
    _connection = conn


def close_database() -> None:
    """Close the connection, if any."""
    global _connection
    if _connection is not None:
        _connection.close()
        _connection = None
