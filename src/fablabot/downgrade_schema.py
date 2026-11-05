"""Take the database back one schema version, for a rollback to the previous release.

Run with the bot stopped: ``python -m fablabot.downgrade_schema``. Each step is a file of ``migrations/down/``.
"""

from __future__ import annotations

import sqlite3
import sys
from typing import TYPE_CHECKING

from fablabot import db

if TYPE_CHECKING:
    from pathlib import Path


def downgrade(path: Path = db.DATABASE_FILE, migrations: Path = db.MIGRATIONS_DIR) -> int:
    """Move the schema back one version, in one transaction.

    Args:
        path (Path): The database file; it must exist.
        migrations (Path): The folder of migration files, holding a ``down/`` folder.

    Returns:
        int: The schema version the database is at now.

    Raises:
        StorageError: If there is no file, nothing to go back to, no step for this version, or the step fails.
    """
    if not path.exists():
        msg = f"{path.as_posix()} does not exist; nothing was changed. Is this the bot's data volume?"
        raise db.StorageError(msg)
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(path, autocommit=True)
        current: int = conn.execute("PRAGMA user_version").fetchone()[0]
        if current <= 1:
            msg = f"{path.as_posix()} is already at schema version {current}; nothing was changed."
            raise db.StorageError(msg)
        steps = sorted((migrations / "down").glob(f"{current:04d}_*.sql"))
        if not steps:
            msg = f"{path.as_posix()} has no way back from schema version {current}; nothing was changed."
            raise db.StorageError(msg)
        # A step that fails leaves the transaction open; closing the connection below takes it back.
        conn.executescript(f"BEGIN IMMEDIATE;\n{steps[0].read_text(encoding='utf-8')}\nPRAGMA user_version = {current - 1};")
        conn.execute("COMMIT")
    except (sqlite3.DatabaseError, OSError) as error:
        msg = f"{path.as_posix()} cannot be used: {error}."
        raise db.StorageError(msg) from error
    finally:
        if conn is not None:
            conn.close()
    return current - 1


def main(argv: list[str] | None = None) -> None:
    """Take the database back one schema version.

    Args:
        argv (list[str] | None): The arguments, or None for the command line; there are none.
    """
    args = sys.argv[1:] if argv is None else argv
    if args:
        sys.exit("Usage: python -m fablabot.downgrade_schema")
    path = db.DATABASE_FILE
    try:
        reached = downgrade(path)
    except db.StorageError as error:
        sys.exit(f"Cannot go back: {error}")
    print(f"Moved {path.as_posix()} back from schema version {reached + 1} to {reached}.")


if __name__ == "__main__":
    main()
