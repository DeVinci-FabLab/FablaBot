"""What the database keeps of a training: its anonymous counts once its check closes, and nothing personal past retention."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fablabot import db
from fablabot.helpers.constants import ANONYMOUS_MIN_REGISTERED, APPEL_EDIT_WINDOW

if TYPE_CHECKING:
    from datetime import datetime
    import sqlite3

_SAME_TRAINING = (
    "g.name = {f}.name AND g.trainer_mention = {f}.trainer_mention AND unixepoch(g.start_iso) = unixepoch({f}.start_iso)"
)
# A training published again lives in two messages; the newest copy is the one that is counted and followed.
_REISSUED = (
    "EXISTS (SELECT 1 FROM formation g JOIN fm_message n ON n.id = g.fm_message_id "
    "WHERE n.guild_id = {m}.guild_id AND n.message_id > {m}.message_id AND " + _SAME_TRAINING + ")"
)
# The counts of a training sit under whichever copy was summarised first, and that copy may be gone since. The summary keeps
# no trainer, so a copy is known by its own place in its message, or by its server, name and start.
_COUNTED = (
    "EXISTS (SELECT 1 FROM training_summary AS s WHERE s.guild_id = {m}.guild_id AND "
    "((s.message_id = {m}.message_id AND s.position = {f}.position) OR "
    "(s.name = {f}.name AND unixepoch(s.starts_at) = unixepoch({f}.start_iso))))"
)
# A training that still needs its counts: no newer copy carries it and no summary holds it.
_UNCOUNTED = "NOT " + _REISSUED + " AND NOT " + _COUNTED
_CLOSED = (
    "SELECT fm_message.id AS message_row, fm_message.guild_id, fm_message.message_id, formation.id AS formation_id, "
    "formation.position, formation.name, formation.trainer_mention, formation.emoji, formation.start_iso FROM formation "
    "JOIN fm_message ON fm_message.id = formation.fm_message_id "
    "WHERE fm_message.message_id IS NOT NULL AND unixepoch(formation.start_iso) <= ? AND "
    + _UNCOUNTED.format(m="fm_message", f="formation")
    + " ORDER BY formation.id"
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
    """Read the outcome and the saved counts of the training's check, whichever copy of the training holds it."""
    appel = conn.execute(
        "SELECT a.validated_at, a.present_count, a.absent_count, a.handled_action FROM appel a "
        "JOIN formation f ON f.fm_message_id = a.fm_message_id AND f.position = a.position "
        "JOIN fm_message m ON m.id = f.fm_message_id WHERE m.guild_id = ? AND f.name = ? AND f.trainer_mention = ? "
        "AND unixepoch(f.start_iso) = unixepoch(?) ORDER BY a.id LIMIT 1",
        (row["guild_id"], row["name"], row["trainer_mention"], row["start_iso"]),
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

    A training with fewer registered members than ``ANONYMOUS_MIN_REGISTERED`` keeps no present and absent counts.

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
            # A small training's counts could point at a person, since reactions stay public.
            counts = (present, absent) if registered >= ANONYMOUS_MIN_REGISTERED else (None, None)
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
                    *counts,
                    outcome,
                ),
            )
    return len(rows)


def forget(cutoff: datetime) -> int:
    """Delete the training messages whose every training started before ``cutoff`` and has its counts.

    The training rows, their registrations, waitlists and checks go with the message. A draft is never deleted, though
    its trainings can match a counted one, since publishing leaves the draft as it was. Only a published training whose
    start can be read gets counts, so a message holding a training with an unreadable start is kept. A training
    published again is counted once, on its newest copy or on the copy summarised first, so its other copies need no
    counts of their own, even when the copy that carries them was deleted before.

    Args:
        cutoff (datetime): The oldest start kept, timezone-aware.

    Returns:
        int: The number of messages deleted.
    """
    with db.transaction() as conn:
        return conn.execute(
            "DELETE FROM fm_message WHERE message_id IS NOT NULL "
            "AND EXISTS (SELECT 1 FROM formation AS f WHERE f.fm_message_id = fm_message.id) "
            "AND NOT EXISTS (SELECT 1 FROM formation AS f WHERE f.fm_message_id = fm_message.id "
            "AND (unixepoch(f.start_iso) >= ? OR (" + _UNCOUNTED.format(m="fm_message", f="f") + ")))",
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
