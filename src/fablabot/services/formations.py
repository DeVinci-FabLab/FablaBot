"""Training drafts and published messages, with their formations and registrations in rows."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fablabot import db
from fablabot.models.formation import FmMessageDraft, Formation, PublishedMessage

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime
    import sqlite3


def _users(conn: sqlite3.Connection, formation_id: int, *, waitlisted: bool) -> list[dict[str, Any]]:
    """Read the ordered members of one list of a formation."""
    rows = conn.execute(
        "SELECT user_id, username, ts_iso FROM registration WHERE formation_id = ? AND waitlisted = ? ORDER BY position",
        (formation_id, int(waitlisted)),
    )
    return [dict(row) for row in rows]


def _message(conn: sqlite3.Connection, row: sqlite3.Row) -> FmMessageDraft:
    """Build the message payload of an ``fm_message`` row."""
    formations = conn.execute("SELECT * FROM formation WHERE fm_message_id = ? ORDER BY position", (row["id"],)).fetchall()
    return FmMessageDraft(
        header=row["header"],
        role_id=row["role_id"],
        intro=row["intro"],
        fms=[
            Formation(
                emoji=fm["emoji"],
                name=fm["name"],
                trainer_mention=fm["trainer_mention"],
                start_iso=fm["start_iso"],
                duration=fm["duration"],
                seats=fm["seats"],
                description=fm["description"],
                excusable=bool(fm["excusable"]),
                registered_users=_users(conn, fm["id"], waitlisted=False),
                waitlisted_users=_users(conn, fm["id"], waitlisted=True),
                notified_hour_before=bool(fm["notified_hour_before"]),
                notified_at_start=bool(fm["notified_at_start"]),
            )
            for fm in formations
        ],
        end=row["end_text"],
        request_forms_url=row["request_forms_url"],
    )


def write_message(
    conn: sqlite3.Connection, guild_id: int, message_id: int | None, channel_id: int | None, message: FmMessageDraft
) -> None:
    """Write one ``fm_message`` row and replace its formations, inside the caller's transaction.

    Args:
        conn (sqlite3.Connection): The connection, inside a transaction.
        guild_id (int): The ID of the guild.
        message_id (int | None): The published message, or None for the draft.
        channel_id (int | None): Its channel, or None for the draft.
        message (FmMessageDraft): The message payload.
    """
    columns = (message.header, message.role_id, message.intro, message.end, message.request_forms_url)
    row = conn.execute("SELECT id FROM fm_message WHERE guild_id = ? AND message_id IS ?", (guild_id, message_id)).fetchone()
    if row:
        row_id = row["id"]
        conn.execute(
            "UPDATE fm_message SET channel_id = ?, header = ?, role_id = ?, intro = ?, end_text = ?, request_forms_url = ? "
            "WHERE id = ?",
            (channel_id, *columns, row_id),
        )
        conn.execute("DELETE FROM formation WHERE fm_message_id = ?", (row_id,))
    else:
        row_id = conn.execute(
            "INSERT INTO fm_message (guild_id, message_id, channel_id, header, role_id, intro, end_text, request_forms_url) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (guild_id, message_id, channel_id, *columns),
        ).lastrowid
    for position, fm in enumerate(message.fms):
        formation_id = conn.execute(
            "INSERT INTO formation (fm_message_id, position, emoji, name, trainer_mention, start_iso, duration, seats, "
            "description, excusable, notified_hour_before, notified_at_start) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                row_id,
                position,
                fm.emoji,
                fm.name,
                fm.trainer_mention,
                fm.start_iso,
                fm.duration,
                fm.seats,
                fm.description,
                int(fm.excusable),
                int(fm.notified_hour_before),
                int(fm.notified_at_start),
            ),
        ).lastrowid
        for waitlisted, users in ((0, fm.registered_users), (1, fm.waitlisted_users)):
            conn.executemany(
                "INSERT INTO registration (formation_id, waitlisted, position, user_id, username, ts_iso) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (formation_id, waitlisted, place, user["user_id"], user["username"], user["ts_iso"])
                    for place, user in enumerate(users)
                ],
            )


def get_draft(guild_id: int) -> FmMessageDraft | None:
    """Read the draft of a guild.

    Args:
        guild_id (int): The ID of the guild.

    Returns:
        FmMessageDraft | None: The draft, or None when the guild has none.
    """
    conn = db.connection()
    row = conn.execute("SELECT * FROM fm_message WHERE guild_id = ? AND message_id IS NULL", (guild_id,)).fetchone()
    return _message(conn, row) if row else None


def save_draft(guild_id: int, draft: FmMessageDraft) -> None:
    """Store the draft of a guild, replacing the previous one.

    Args:
        guild_id (int): The ID of the guild.
        draft (FmMessageDraft): The draft.
    """
    with db.transaction() as conn:
        write_message(conn, guild_id, None, None, draft)


def current_published(guild_id: int) -> PublishedMessage | None:
    """Read the current published message of a guild: the newest one, which has the highest message ID.

    Args:
        guild_id (int): The ID of the guild.

    Returns:
        PublishedMessage | None: The message, or None when the guild has published nothing.
    """
    conn = db.connection()
    row = conn.execute(
        "SELECT * FROM fm_message WHERE guild_id = ? AND message_id IS NOT NULL ORDER BY message_id DESC LIMIT 1", (guild_id,)
    ).fetchone()
    if row is None:
        return None
    return PublishedMessage(message_id=row["message_id"], channel_id=row["channel_id"], message=_message(conn, row))


def save_published(guild_id: int, published: PublishedMessage) -> None:
    """Store a published message under its own ID.

    The current message is the one with the highest ID (message IDs grow with time). A known ID changes that
    message alone, and an older message written for the first time late is stored without taking the place of
    a newer one.

    Args:
        guild_id (int): The ID of the guild.
        published (PublishedMessage): The published message.
    """
    with db.transaction() as conn:
        write_message(conn, guild_id, published.message_id, published.channel_id, published.message)


def _move_places(conn: sqlite3.Connection, guild_id: int, message_id: int, moves: Mapping[int, int]) -> None:
    """Move the checks and the anonymous summaries of one published message to their trainings' new places.

    SQLite checks a unique key row by row while an UPDATE runs, so a plain shift fails or passes by the order the rows come
    in. Each moved row goes first to a negative place, then all of them come back positive: no row meets another.

    Args:
        conn (sqlite3.Connection): The connection, inside a transaction.
        guild_id (int): The ID of the guild.
        message_id (int): The published message whose places move.
        moves (Mapping[int, int]): The new place of each old place that changes.
    """
    if not moves:
        return
    owner = (guild_id, message_id)
    conn.executemany(
        "UPDATE appel SET position = -1 - ? WHERE position = ? "
        "AND fm_message_id = (SELECT id FROM fm_message WHERE guild_id = ? AND message_id = ?)",
        [(new, old, *owner) for old, new in moves.items()],
    )
    conn.execute(
        "UPDATE appel SET position = -position - 1 WHERE position < 0 "
        "AND fm_message_id = (SELECT id FROM fm_message WHERE guild_id = ? AND message_id = ?)",
        owner,
    )
    conn.executemany(
        "UPDATE training_summary SET position = -1 - ? WHERE position = ? AND guild_id = ? AND message_id = ?",
        [(new, old, *owner) for old, new in moves.items()],
    )
    conn.execute(
        "UPDATE training_summary SET position = -position - 1 WHERE position < 0 AND guild_id = ? AND message_id = ?", owner
    )


def save_update(guild_id: int, published: PublishedMessage, draft: FmMessageDraft, *, moves: Mapping[int, int]) -> None:
    """Store an updated announcement and the draft that now says the same, with the places that moved: all or nothing.

    Args:
        guild_id (int): The ID of the guild.
        published (PublishedMessage): The published message as updated, under its own ID.
        draft (FmMessageDraft): The draft to write back.
        moves (Mapping[int, int]): The new place of each training of the message whose place changed, old to new: the
            checks and the anonymous summaries of the message follow their trainings.
    """
    with db.transaction() as conn:
        write_message(conn, guild_id, published.message_id, published.channel_id, published.message)
        write_message(conn, guild_id, None, None, draft)
        _move_places(conn, guild_id, published.message_id, moves)


def guilds_with_published() -> list[int]:
    """List the guilds that have published a message.

    Returns:
        list[int]: Their IDs.
    """
    rows = db.connection().execute("SELECT DISTINCT guild_id FROM fm_message WHERE message_id IS NOT NULL ORDER BY guild_id")
    return [row["guild_id"] for row in rows]


def published_awaiting_start(guild_id: int, after: datetime, until: datetime) -> list[PublishedMessage]:
    """List the published messages of a guild holding a training whose start has not been handled yet.

    Args:
        guild_id (int): The ID of the guild.
        after (datetime): Trainings starting at or before this are left out, timezone-aware.
        until (datetime): Trainings starting after this are left out, timezone-aware.

    Returns:
        list[PublishedMessage]: The messages, oldest first, each with all its trainings.
    """
    conn = db.connection()
    rows = conn.execute(
        "SELECT * FROM fm_message m WHERE m.guild_id = ? AND m.message_id IS NOT NULL AND EXISTS ("
        "SELECT 1 FROM formation f WHERE f.fm_message_id = m.id AND f.notified_at_start = 0 "
        "AND unixepoch(?) < unixepoch(f.start_iso) AND unixepoch(f.start_iso) <= unixepoch(?)) ORDER BY m.message_id",
        (guild_id, after.isoformat(), until.isoformat()),
    ).fetchall()
    return [
        PublishedMessage(message_id=row["message_id"], channel_id=row["channel_id"], message=_message(conn, row))
        for row in rows
    ]


def reissued_positions(guild_id: int, message_id: int) -> set[int]:
    """List the trainings of a published message that a newer published message of the guild holds again.

    A training is held again when the newer message has the same name, trainer and start instant: the
    announcement was published anew and the newer copy is the one registrations follow.

    Args:
        guild_id (int): The ID of the guild.
        message_id (int): The ID of the published message.

    Returns:
        set[int]: The places of those trainings in the message.
    """
    rows = db.connection().execute(
        "SELECT f.position FROM formation f JOIN fm_message m ON m.id = f.fm_message_id "
        "WHERE m.guild_id = ? AND m.message_id = ? AND EXISTS ("
        "SELECT 1 FROM formation g JOIN fm_message n ON n.id = g.fm_message_id "
        "WHERE n.guild_id = m.guild_id AND n.message_id > m.message_id AND g.name = f.name "
        "AND g.trainer_mention = f.trainer_mention AND unixepoch(g.start_iso) = unixepoch(f.start_iso))",
        (guild_id, message_id),
    )
    return {row["position"] for row in rows}


def reacted_emojis(guild_id: int, message_id: int) -> set[str]:
    """List the emojis members reacted with on a published message, from the reaction log.

    Args:
        guild_id (int): The ID of the guild.
        message_id (int): The ID of the published message.

    Returns:
        set[str]: Every emoji the log holds an add or a removal for on that message, whatever session wore it.
    """
    rows = db.connection().execute(
        "SELECT DISTINCT emoji FROM reaction_event WHERE scope = 'formation' AND guild_id = ? AND message_id = ?",
        (guild_id, message_id),
    )
    return {row["emoji"] for row in rows}
