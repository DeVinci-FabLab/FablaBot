"""Recorded ends of temporary admin roles, one row per guild and member."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fablabot import db

if TYPE_CHECKING:
    from collections.abc import Mapping


def get(guild_id: int, member_id: int) -> dict[str, Any] | None:
    """Read the recorded expiry of a member's op on one guild, as stored.

    Args:
        guild_id (int): The ID of the guild.
        member_id (int): The ID of the member.

    Returns:
        dict[str, Any] | None: ``expires_at`` and ``channel_id``, or None without a record.
    """
    row = (
        db.connection()
        .execute("SELECT expires_at, channel_id FROM op_expiry WHERE guild_id = ? AND member_id = ?", (guild_id, member_id))
        .fetchone()
    )
    return dict(row) if row else None


def record(guild_id: int, member_id: int, expiry: Mapping[str, Any]) -> None:
    """Record the expiry of a member's op on one guild, replacing any previous one.

    Args:
        guild_id (int): The ID of the guild.
        member_id (int): The ID of the member.
        expiry (Mapping[str, Any]): ``expires_at`` and ``channel_id``.
    """
    with db.transaction() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO op_expiry (guild_id, member_id, expires_at, channel_id) VALUES (?, ?, ?, ?)",
            (guild_id, member_id, expiry["expires_at"], expiry["channel_id"]),
        )


def clear(guild_id: int, member_id: int) -> None:
    """Forget the expiry of a member's op on one guild, if any.

    Args:
        guild_id (int): The ID of the guild.
        member_id (int): The ID of the member.
    """
    with db.transaction() as conn:
        conn.execute("DELETE FROM op_expiry WHERE guild_id = ? AND member_id = ?", (guild_id, member_id))


def member_ids(guild_id: int) -> list[int]:
    """List the members with a recorded op on one guild.

    Args:
        guild_id (int): The ID of the guild.

    Returns:
        list[int]: Their IDs.
    """
    rows = db.connection().execute("SELECT member_id FROM op_expiry WHERE guild_id = ?", (guild_id,))
    return [row["member_id"] for row in rows]
