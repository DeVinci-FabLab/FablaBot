"""Attendance checks: who was present at each training, the alerts, and the answers to them."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from fablabot import db
from fablabot.helpers.constants import APPEL_EDIT_WINDOW
from fablabot.models.attendance import AlertAction, Appel, Registrant, Validation

if TYPE_CHECKING:
    from collections.abc import Iterable
    import sqlite3

# The check joins its announcement by message and place; the training's own row is rebuilt at every save, so nothing here
# stores its ID. LEFT JOIN: a training taken out of the message leaves its check readable.
_SELECT = (
    "SELECT a.id, m.guild_id, m.message_id, m.channel_id, a.position, COALESCE(f.name, '') AS name, "
    "COALESCE(f.duration, '') AS duration, COALESCE(f.excusable, 1) AS excusable, a.trainer_id, a.starts_at, a.ends_at, "
    "a.closes_at, a.thread_id, a.validated_by, a.validated_at, a.present_count, a.absent_count, a.alert_sent, "
    "a.handled_by, a.handled_at, a.handled_action "
    "FROM appel a JOIN fm_message m ON m.id = a.fm_message_id "
    "LEFT JOIN formation f ON f.fm_message_id = a.fm_message_id AND f.position = a.position "
)


def _time(value: str | None) -> datetime | None:
    """Read a stored time."""
    return datetime.fromisoformat(value) if value else None


def _appel(row: sqlite3.Row) -> Appel:
    """Build an ``Appel`` from a row of ``_SELECT``."""
    return Appel(
        id=row["id"],
        guild_id=row["guild_id"],
        message_id=row["message_id"],
        channel_id=row["channel_id"],
        position=row["position"],
        name=row["name"],
        duration=row["duration"],
        excusable=bool(row["excusable"]),
        trainer_id=row["trainer_id"],
        starts_at=datetime.fromisoformat(row["starts_at"]),
        ends_at=datetime.fromisoformat(row["ends_at"]),
        closes_at=datetime.fromisoformat(row["closes_at"]),
        thread_id=row["thread_id"],
        validated_by=row["validated_by"],
        validated_at=_time(row["validated_at"]),
        present_count=row["present_count"],
        absent_count=row["absent_count"],
        alert_sent=bool(row["alert_sent"]),
        handled_by=row["handled_by"],
        handled_at=_time(row["handled_at"]),
        handled_action=AlertAction(row["handled_action"]) if row["handled_action"] else None,
    )


def open_appel(  # noqa: PLR0913
    guild_id: int, message_id: int, position: int, *, trainer_id: int | None, starts_at: datetime, ends_at: datetime
) -> int:
    """Open the check of a training, once: a second call returns the same check and changes nothing.

    Args:
        guild_id (int): The server.
        message_id (int): The published announcement.
        position (int): The training's place in it.
        trainer_id (int | None): The trainer, when known.
        starts_at (datetime): The start, timezone-aware.
        ends_at (datetime): The end of the slot, timezone-aware.

    Returns:
        int: The check's ID.

    Raises:
        LookupError: If the announcement is not stored as published.
        ValueError: If a time has no timezone.
    """
    if starts_at.tzinfo is None or ends_at.tzinfo is None:
        msg = "A check needs timezone-aware times."
        raise ValueError(msg)
    with db.transaction() as conn:
        message = conn.execute(
            "SELECT id FROM fm_message WHERE guild_id = ? AND message_id = ?", (guild_id, message_id)
        ).fetchone()
        if message is None:
            msg = f"No published message {message_id} on server {guild_id}."
            raise LookupError(msg)
        conn.execute(
            "INSERT OR IGNORE INTO appel (fm_message_id, position, trainer_id, starts_at, ends_at, closes_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                message["id"],
                position,
                trainer_id,
                starts_at.isoformat(),
                ends_at.isoformat(),
                (starts_at + APPEL_EDIT_WINDOW).isoformat(),
            ),
        )
        row = conn.execute(
            "SELECT id FROM appel WHERE fm_message_id = ? AND position = ?", (message["id"], position)
        ).fetchone()
    return row["id"]


def checked_elsewhere(guild_id: int, message_id: int) -> set[int]:
    """List the trainings of a published message whose check is already open from another message of the server.

    A training is checked elsewhere when another message of the server holds it again, with the same name, trainer
    and start instant, and that copy has a check: the announcement was published anew after the start was handled.

    Args:
        guild_id (int): The server.
        message_id (int): The published message.

    Returns:
        set[int]: The places of those trainings in the message.
    """
    rows = db.connection().execute(
        "SELECT f.position FROM formation f JOIN fm_message m ON m.id = f.fm_message_id "
        "WHERE m.guild_id = ? AND m.message_id = ? AND EXISTS ("
        "SELECT 1 FROM formation g JOIN fm_message n ON n.id = g.fm_message_id "
        "JOIN appel a ON a.fm_message_id = g.fm_message_id AND a.position = g.position "
        "WHERE n.guild_id = m.guild_id AND n.message_id <> m.message_id AND g.name = f.name "
        "AND g.trainer_mention = f.trainer_mention AND unixepoch(g.start_iso) = unixepoch(f.start_iso))",
        (guild_id, message_id),
    )
    return {row["position"] for row in rows}


def get(appel_id: int) -> Appel | None:
    """Read a check.

    Args:
        appel_id (int): Its ID.

    Returns:
        Appel | None: The check, or None when it does not exist.
    """
    row = db.connection().execute(_SELECT + "WHERE a.id = ?", (appel_id,)).fetchone()
    return _appel(row) if row else None


def registrants(appel_id: int) -> list[Registrant]:
    """List the registered members of a check's training, in their order, without the waitlist.

    Args:
        appel_id (int): The check's ID.

    Returns:
        list[Registrant]: The members.
    """
    rows = db.connection().execute(
        "SELECT r.user_id, r.username FROM appel a "
        "JOIN formation f ON f.fm_message_id = a.fm_message_id AND f.position = a.position "
        "JOIN registration r ON r.formation_id = f.id AND r.waitlisted = 0 WHERE a.id = ? ORDER BY r.position",
        (appel_id,),
    )
    return [Registrant(user_id=row["user_id"], username=row["username"]) for row in rows]


def present(appel_id: int) -> set[int]:
    """List the members marked present.

    Args:
        appel_id (int): The check's ID.

    Returns:
        set[int]: Their IDs.
    """
    rows = db.connection().execute("SELECT user_id FROM appel_present WHERE appel_id = ?", (appel_id,))
    return {row["user_id"] for row in rows}


def mark(appel_id: int, shown: Iterable[int], chosen: Iterable[int]) -> None:
    """Set the members a control showed: those chosen are present, the other shown ones absent, the rest untouched.

    Args:
        appel_id (int): The check's ID.
        shown (Iterable[int]): The members the control listed.
        chosen (Iterable[int]): The ones picked; an ID not shown is ignored.
    """
    listed = set(shown)
    picked = listed & set(chosen)
    with db.transaction() as conn:
        conn.executemany("DELETE FROM appel_present WHERE appel_id = ? AND user_id = ?", [(appel_id, user) for user in listed])
        conn.executemany(
            "INSERT INTO appel_present (appel_id, user_id) VALUES (?, ?)", [(appel_id, user) for user in sorted(picked)]
        )


def validate(appel_id: int, user_id: int, at: datetime) -> Validation:
    """Record the check: the registered members marked present, and the rest of the registered ones absent.

    Args:
        appel_id (int): The check's ID.
        user_id (int): Who validates.
        at (datetime): When.

    Returns:
        Validation: The check and the members saved present and absent, read with the save so that nothing marked
            afterwards is reported as saved.

    Raises:
        LookupError: If the check does not exist.
    """
    with db.transaction() as conn:
        before = conn.execute(_SELECT + "WHERE a.id = ?", (appel_id,)).fetchone()
        if before is None:
            msg = f"No check {appel_id}."
            raise LookupError(msg)
        # One connection: these reads sit inside the transaction, so they see what the save counts.
        registered = [r.user_id for r in registrants(appel_id)]
        marked = present(appel_id)
        here = [user for user in registered if user in marked]
        away = [user for user in registered if user not in marked]
        conn.execute(
            "UPDATE appel SET validated_by = ?, validated_at = ?, present_count = ?, absent_count = ? WHERE id = ?",
            (user_id, at.isoformat(), len(here), len(away), appel_id),
        )
        saved = conn.execute(_SELECT + "WHERE a.id = ?", (appel_id,)).fetchone()
    return Validation(appel=_appel(saved), here=here, away=away, first=before["validated_at"] is None)


def set_thread(appel_id: int, thread_id: int) -> bool:
    """Remember the thread made for a trainer whose DMs are closed, unless the check already has one.

    Args:
        appel_id (int): The check's ID.
        thread_id (int): The thread.

    Returns:
        bool: True when the thread was stored, False when the check already had one or does not exist.
    """
    with db.transaction() as conn:
        return (
            conn.execute("UPDATE appel SET thread_id = ? WHERE id = ? AND thread_id IS NULL", (thread_id, appel_id)).rowcount
            == 1
        )


def due_alerts(now: datetime) -> list[Appel]:
    """List the checks nobody made whose slot is over, still open, not alerted and not answered.

    Args:
        now (datetime): The current time.

    Returns:
        list[Appel]: The checks, oldest slot first.
    """
    rows = db.connection().execute(
        _SELECT + "WHERE a.validated_at IS NULL AND a.alert_sent = 0 AND a.handled_action IS NULL "
        "AND unixepoch(a.ends_at) <= unixepoch(?) AND unixepoch(?) < unixepoch(a.closes_at) "
        "ORDER BY unixepoch(a.ends_at), a.id",
        (now.isoformat(), now.isoformat()),
    )
    return [_appel(row) for row in rows]


def record_alert(appel_id: int, messages: Iterable[tuple[int, int]]) -> None:
    """Mark a check as alerted and keep where the alert went.

    Args:
        appel_id (int): The check's ID.
        messages (Iterable[tuple[int, int]]): The ``(channel_id, message_id)`` of each copy sent.
    """
    with db.transaction() as conn:
        conn.execute("UPDATE appel SET alert_sent = 1 WHERE id = ?", (appel_id,))
        conn.executemany(
            "INSERT OR IGNORE INTO appel_alert (appel_id, channel_id, message_id) VALUES (?, ?, ?)",
            [(appel_id, channel, message) for channel, message in messages],
        )


def alert_messages(appel_id: int) -> list[tuple[int, int]]:
    """List where the alert of a check was sent.

    Args:
        appel_id (int): The check's ID.

    Returns:
        list[tuple[int, int]]: The ``(channel_id, message_id)`` pairs, in the order they were recorded.
    """
    rows = db.connection().execute(
        "SELECT channel_id, message_id FROM appel_alert WHERE appel_id = ? ORDER BY rowid", (appel_id,)
    )
    return [(row["channel_id"], row["message_id"]) for row in rows]


def handle(appel_id: int, user_id: int, action: AlertAction, at: datetime) -> bool:
    """Record the answer to an alert; only the first answer counts.

    Args:
        appel_id (int): The check's ID.
        user_id (int): Who answers.
        action (AlertAction): The answer.
        at (datetime): When.

    Returns:
        bool: True if this answer was recorded, False if the alert was already answered.
    """
    with db.transaction() as conn:
        changed = conn.execute(
            "UPDATE appel SET handled_by = ?, handled_at = ?, handled_action = ? WHERE id = ? AND handled_action IS NULL",
            (user_id, at.isoformat(), action.value, appel_id),
        ).rowcount
    return changed == 1


def open_in(guild_id: int, now: datetime) -> list[Appel]:
    """List the checks of a server that still take changes.

    Args:
        guild_id (int): The server.
        now (datetime): The current time.

    Returns:
        list[Appel]: The checks, newest start first.
    """
    rows = db.connection().execute(
        _SELECT + "WHERE m.guild_id = ? AND a.handled_action IS NOT 'cancel' AND unixepoch(?) < unixepoch(a.closes_at) "
        "ORDER BY unixepoch(a.starts_at) DESC, a.id DESC",
        (guild_id, now.isoformat()),
    )
    return [_appel(row) for row in rows]
