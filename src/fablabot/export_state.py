"""Write the database back as the JSON files of older releases, into a new folder, for a rollback.

Run with the bot stopped: ``python -m fablabot.export_state data/exports/<name>``.
"""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import sys
from typing import Any, Final

from fablabot import db
from fablabot.services import formations, messages

GUILD_CONFIG_FILE, FORMATIONS_FILE, MESSAGES_FILE, TEMP_ADMIN_FILE = db.STATE_FILES
LEGACY_SECTION: Final = "__global__"


def _events(conn: sqlite3.Connection, scope: str, guild_id: int) -> list[dict[str, Any]]:
    """Read one server's reaction history, in the order it was written."""
    rows = conn.execute(
        "SELECT message_id, user_id, user_name, emoji, action, ts_iso FROM reaction_event "
        "WHERE scope = ? AND guild_id = ? ORDER BY id",
        (scope, guild_id),
    )
    return [dict(row) for row in rows]


def _guilds(conn: sqlite3.Connection, table: str, scope: str) -> list[int]:
    """List the servers holding a row in ``table`` or an event of ``scope``; ``table`` is never user input."""
    rows = conn.execute(
        f"SELECT guild_id FROM {table} UNION SELECT guild_id FROM reaction_event WHERE scope = ? ORDER BY guild_id", (scope,)
    )
    return [row["guild_id"] for row in rows]


def _settings(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    """Read the server settings: unset ones are absent, as the JSON store kept them."""
    state: dict[str, dict[str, Any]] = {}
    for row in conn.execute("SELECT channel_id FROM legacy_log_channel"):
        state[LEGACY_SECTION] = {"log_channel_id": row["channel_id"]}
    for row in conn.execute("SELECT * FROM guild_settings ORDER BY guild_id"):
        entry: dict[str, Any] = {}
        for key in ("commands_channel_id", "log_channel_id"):
            if row[key] is not None:
                entry[key] = row[key]
        if row["welcome_verify_enabled"] is not None:
            entry["welcome_verify_enabled"] = bool(row["welcome_verify_enabled"])
        state[str(row["guild_id"])] = entry
    return state


def _formations(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    """Read the trainings: the file holds one published message per server, the current one."""
    state: dict[str, dict[str, Any]] = {}
    for guild_id in _guilds(conn, "fm_message", "formation"):
        entry: dict[str, Any] = {}
        if (draft := formations.get_draft(guild_id)) is not None:
            entry["draft"] = draft.to_dict()
        if (published := formations.current_published(guild_id)) is not None:
            entry["published"] = published.to_dict()
        entry["reactions_log"] = _events(conn, "formation", guild_id)
        state[str(guild_id)] = entry
    return state


def _messages(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    """Read the tracked messages."""
    state: dict[str, dict[str, Any]] = {}
    for guild_id in _guilds(conn, "tracked_message", "message"):
        entry: dict[str, Any] = {}
        if (draft := messages.get_draft(guild_id)) is not None:
            entry["draft"] = draft.to_dict()
        entry["tracked"] = {str(key): value.to_dict() for key, value in messages.tracked_messages(guild_id).items()}
        entry["reactions_log"] = _events(conn, "message", guild_id)
        state[str(guild_id)] = entry
    return state


def _ops(conn: sqlite3.Connection) -> dict[str, dict[str, dict[str, Any]]]:
    """Read the temporary admin expiries."""
    state: dict[str, dict[str, dict[str, Any]]] = {}
    for row in conn.execute("SELECT guild_id, member_id, expires_at, channel_id FROM op_expiry ORDER BY guild_id, member_id"):
        entry = {"expires_at": row["expires_at"], "channel_id": row["channel_id"]}
        state.setdefault(str(row["guild_id"]), {})[str(row["member_id"])] = entry
    return state


def snapshot() -> dict[str, dict[str, Any]]:
    """Read the whole state in the shape older releases kept, by file name.

    Returns:
        dict[str, dict[str, Any]]: The content of each JSON file.
    """
    conn = db.connection()
    return {
        GUILD_CONFIG_FILE: _settings(conn),
        FORMATIONS_FILE: _formations(conn),
        MESSAGES_FILE: _messages(conn),
        TEMP_ADMIN_FILE: _ops(conn),
    }


def replaced_messages() -> int:
    """Count the published messages the JSON shape cannot hold.

    Returns:
        int: The published messages that are not their server's current one.
    """
    row = (
        db.connection()
        .execute("SELECT count(*) - count(DISTINCT guild_id) FROM fm_message WHERE message_id IS NOT NULL")
        .fetchone()
    )
    return row[0]


def export_to(folder: Path) -> None:
    """Write every state file into a new folder, which appears whole or not at all.

    Args:
        folder (Path): The folder to create; it must not exist.

    Raises:
        FileExistsError: If the folder already exists.
    """
    if folder.exists():
        msg = f"{folder.as_posix()} already exists"
        raise FileExistsError(msg)
    state = snapshot()
    partial = folder.with_name(f"{folder.name}.partial")
    # The files hold member IDs and names: private to the bot's user.
    partial.mkdir(mode=0o700, parents=True)
    for name, content in state.items():
        (partial / name).write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")
    partial.rename(folder)


def main(argv: list[str] | None = None) -> None:
    """Export the database into the folder named on the command line.

    Args:
        argv (list[str] | None): The arguments, or None for the command line.
    """
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        sys.exit("Usage: python -m fablabot.export_state <new folder>")
    folder = Path(args[0])
    try:
        db.open_database()
        export_to(folder)
        replaced = replaced_messages()
    except (db.StorageError, OSError, sqlite3.Error) as error:
        sys.exit(f"Cannot export: {error}")
    finally:
        db.close_database()
    print(f"Exported to {folder.as_posix()}.")
    if replaced:
        print(f"{replaced} replaced message(s) have no place in these files; they stay in the database.")


if __name__ == "__main__":
    main()
