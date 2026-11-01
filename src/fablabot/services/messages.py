"""Tracked messages and the message draft of each guild."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fablabot import db
from fablabot.models.message import MessageDraft, MsgReactionEvent, TrackedMessage

if TYPE_CHECKING:
    from collections.abc import Sequence
    import sqlite3


def _reactions(conn: sqlite3.Connection, row_id: int) -> list[MsgReactionEvent]:
    """Read the reaction actions of a row, in their order."""
    rows = conn.execute(
        "SELECT emoji, action_type, message_content, target_id, target_name, message_id FROM tracked_reaction "
        "WHERE tracked_message_id = ? ORDER BY position",
        (row_id,),
    )
    return [MsgReactionEvent.from_dict(dict(row)) for row in rows]


def _replace_reactions(conn: sqlite3.Connection, row_id: int, reactions: Sequence[MsgReactionEvent]) -> None:
    """Replace the reaction actions of a row, inside the caller's transaction."""
    conn.execute("DELETE FROM tracked_reaction WHERE tracked_message_id = ?", (row_id,))
    conn.executemany(
        "INSERT INTO tracked_reaction "
        "(tracked_message_id, position, emoji, action_type, message_content, target_id, target_name, message_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (row_id, position, r.emoji, r.action_type, r.message_content, r.target_id, r.target_name, r.message_id)
            for position, r in enumerate(reactions)
        ],
    )


def get_draft(guild_id: int) -> MessageDraft | None:
    """Read the message draft of a guild.

    Args:
        guild_id (int): The ID of the guild.

    Returns:
        MessageDraft | None: The draft, or None when the guild has none.
    """
    conn = db.connection()
    row = conn.execute(
        "SELECT id, content FROM tracked_message WHERE guild_id = ? AND message_id IS NULL", (guild_id,)
    ).fetchone()
    return MessageDraft(content=row["content"], reactions=_reactions(conn, row["id"])) if row else None


def save_draft(guild_id: int, draft: MessageDraft) -> None:
    """Store the message draft of a guild, replacing any previous one.

    Args:
        guild_id (int): The ID of the guild.
        draft (MessageDraft): The draft.
    """
    with db.transaction() as conn:
        row = conn.execute("SELECT id FROM tracked_message WHERE guild_id = ? AND message_id IS NULL", (guild_id,)).fetchone()
        if row:
            row_id = row["id"]
            conn.execute("UPDATE tracked_message SET content = ? WHERE id = ?", (draft.content, row_id))
        else:
            row_id = conn.execute(
                "INSERT INTO tracked_message (guild_id, content) VALUES (?, ?)", (guild_id, draft.content)
            ).lastrowid
        assert row_id is not None
        _replace_reactions(conn, row_id, draft.reactions)


def clear_draft(guild_id: int) -> None:
    """Forget the message draft of a guild, if any.

    Args:
        guild_id (int): The ID of the guild.
    """
    with db.transaction() as conn:
        conn.execute("DELETE FROM tracked_message WHERE guild_id = ? AND message_id IS NULL", (guild_id,))


def tracked_messages(guild_id: int) -> dict[int, TrackedMessage]:
    """Read the tracked messages of a guild, oldest first.

    Args:
        guild_id (int): The ID of the guild.

    Returns:
        dict[int, TrackedMessage]: The tracked messages by message ID.
    """
    conn = db.connection()
    rows = conn.execute(
        "SELECT * FROM tracked_message WHERE guild_id = ? AND message_id IS NOT NULL ORDER BY id", (guild_id,)
    ).fetchall()
    return {
        row["message_id"]: TrackedMessage(
            message_id=row["message_id"],
            channel_id=row["channel_id"],
            content=row["content"],
            reactions=_reactions(conn, row["id"]),
            created_by=row["created_by"],
            created_at_iso=row["created_at_iso"],
        )
        for row in rows
    }


def save_tracked(guild_id: int, tracked: TrackedMessage) -> None:
    """Store a tracked message, replacing any previous version of it.

    Args:
        guild_id (int): The ID of the guild.
        tracked (TrackedMessage): The tracked message.
    """
    with db.transaction() as conn:
        row_id = conn.execute(
            "INSERT INTO tracked_message (guild_id, message_id, channel_id, content, created_by, created_at_iso) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (guild_id, message_id) DO UPDATE SET "
            "channel_id = excluded.channel_id, content = excluded.content, "
            "created_by = excluded.created_by, created_at_iso = excluded.created_at_iso RETURNING id",
            (guild_id, tracked.message_id, tracked.channel_id, tracked.content, tracked.created_by, tracked.created_at_iso),
        ).fetchone()["id"]
        _replace_reactions(conn, row_id, tracked.reactions)


def remove_tracked(guild_id: int, message_id: int) -> bool:
    """Forget a tracked message.

    Args:
        guild_id (int): The ID of the guild.
        message_id (int): The ID of the message.

    Returns:
        bool: True if the message was removed, False if it was not found.
    """
    with db.transaction() as conn:
        return (
            conn.execute("DELETE FROM tracked_message WHERE guild_id = ? AND message_id = ?", (guild_id, message_id)).rowcount
            > 0
        )
