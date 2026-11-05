"""What the database keeps of a training: its anonymous counts once its check closes, and nothing personal past retention."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fablabot import db
from fablabot.helpers.constants import APPEL_EDIT_WINDOW

if TYPE_CHECKING:
    from datetime import datetime
    import sqlite3

_CLOSED = (
    "SELECT fm_message.id AS message_row, fm_message.guild_id, fm_message.message_id, formation.id AS formation_id, "
    "formation.position, formation.name, formation.emoji, formation.start_iso FROM formation "
    "JOIN fm_message ON fm_message.id = formation.fm_message_id "
    "WHERE fm_message.message_id IS NOT NULL AND unixepoch(formation.start_iso) <= ? AND NOT EXISTS ("
    "SELECT 1 FROM training_summary AS s WHERE s.guild_id = fm_message.guild_id AND s.message_id = fm_message.message_id "
    "AND s.position = formation.position) ORDER BY formation.id"
)


def _withdrawn(conn: sqlite3.Connection, row: sqlite3.Row) -> int:
    """Count the members whose last reaction on the training's emoji took it back."""
    events = conn.execute(
        "SELECT user_id, action FROM reaction_event WHERE scope = 'formation' AND guild_id = ? AND message_id = ? "
        "AND emoji = ? ORDER BY unixepoch(ts_iso), id",
        (row["guild_id"], row["message_id"], row["emoji"]),
    )
    last = {event["user_id"]: event["action"] for event in events}
    return sum(action == "remove" for action in last.values())


def _attendance(conn: sqlite3.Connection, row: sqlite3.Row) -> tuple[str, int, int]:
    """Read the outcome and the saved counts of the training's check."""
    appel = conn.execute(
        "SELECT validated_at, present_count, absent_count, handled_action FROM appel WHERE fm_message_id = ? AND position = ?",
        (row["message_row"], row["position"]),
    ).fetchone()
    if appel is None:
        return "not_asked", 0, 0
    if appel["handled_action"] == "cancel":
        return "cancelled", 0, 0
    if appel["validated_at"] is None:
        return "not_done", 0, 0
    return "done", appel["present_count"], appel["absent_count"]


def summarise(now: datetime) -> int:
    """Write, once, the anonymous counts of every published training whose check can no longer change.

    Args:
        now (datetime): The current time, timezone-aware.

    Returns:
        int: The number of trainings summarised.
    """
    with db.transaction() as conn:
        rows = conn.execute(_CLOSED, (int((now - APPEL_EDIT_WINDOW).timestamp()),)).fetchall()
        for row in rows:
            registered, waitlisted = (
                conn.execute(
                    "SELECT count(*) FROM registration WHERE formation_id = ? AND waitlisted = ?", (row["formation_id"], flag)
                ).fetchone()[0]
                for flag in (0, 1)
            )
            outcome, present, absent = _attendance(conn, row)
            conn.execute(
                "INSERT INTO training_summary (guild_id, message_id, position, name, starts_at, registered, waitlisted, "
                "withdrawn, present, absent, outcome) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    row["guild_id"],
                    row["message_id"],
                    row["position"],
                    row["name"],
                    row["start_iso"],
                    registered,
                    waitlisted,
                    _withdrawn(conn, row),
                    present,
                    absent,
                    outcome,
                ),
            )
    return len(rows)


def forget(cutoff: datetime) -> int:
    """Delete the training messages whose every training started before ``cutoff`` and has its counts.

    The training rows, their registrations, waitlists and checks go with the message. Only a published training whose
    start can be read gets counts, so a draft, or a training with such a start, keeps its message.

    Args:
        cutoff (datetime): The oldest start kept, timezone-aware.

    Returns:
        int: The number of messages deleted.
    """
    with db.transaction() as conn:
        return conn.execute(
            "DELETE FROM fm_message WHERE EXISTS (SELECT 1 FROM formation AS f WHERE f.fm_message_id = fm_message.id) "
            "AND NOT EXISTS (SELECT 1 FROM formation AS f WHERE f.fm_message_id = fm_message.id "
            "AND (unixepoch(f.start_iso) >= ? OR NOT EXISTS ("
            "SELECT 1 FROM training_summary AS s WHERE s.guild_id = fm_message.guild_id "
            "AND s.message_id = fm_message.message_id AND s.position = f.position)))",
            (int(cutoff.timestamp()),),
        ).rowcount


def forget_reactions(cutoff: datetime) -> int:
    """Delete the reaction history of both scopes from before ``cutoff``; a date SQLite cannot read stays.

    Args:
        cutoff (datetime): The oldest reaction kept, timezone-aware.

    Returns:
        int: The number of reactions deleted.
    """
    with db.transaction() as conn:
        return conn.execute("DELETE FROM reaction_event WHERE unixepoch(ts_iso) < ?", (int(cutoff.timestamp()),)).rowcount
