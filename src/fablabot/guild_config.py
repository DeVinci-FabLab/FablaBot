"""Utilities to persist and retrieve guild-specific bot configuration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fablabot import db

if TYPE_CHECKING:
    from collections.abc import Callable
    import sqlite3

logger = logging.getLogger(__name__)

_COMMANDS = "commands_channel_id"
_WELCOME = "welcome_verify_enabled"
_LOG = "log_channel_id"


def _read(guild_id: int) -> sqlite3.Row | None:
    """Read the settings of a specific guild.

    Args:
        guild_id (int): The ID of the guild.

    Returns:
        sqlite3.Row | None: The guild's row, or None when it has no setting.
    """
    return db.connection().execute("SELECT * FROM guild_settings WHERE guild_id = ?", (guild_id,)).fetchone()


def _set(guild_id: int, column: str, value: int | None) -> None:
    """Change one setting of a specific guild; a guild left with no setting keeps no row.

    Args:
        guild_id (int): The ID of the guild to update.
        column (str): One of the column constants above, never user input.
        value (int | None): The value to store, or None to unset it.
    """
    with db.transaction() as conn:
        conn.execute(
            f"INSERT INTO guild_settings (guild_id, {column}) VALUES (?, ?) "
            f"ON CONFLICT (guild_id) DO UPDATE SET {column} = excluded.{column}",
            (guild_id, value),
        )
        conn.execute(
            "DELETE FROM guild_settings WHERE guild_id = ? "
            "AND commands_channel_id IS NULL AND log_channel_id IS NULL AND welcome_verify_enabled IS NULL",
            (guild_id,),
        )


def get_commands_channel_id(guild_id: int) -> int | None:
    """Retrieve the commands channel ID for a specific guild.

    Args:
        guild_id (int): The ID of the guild to retrieve the commands channel ID for.

    Returns:
        int | None: The commands channel ID if it exists, otherwise None.
    """
    row = _read(guild_id)
    return row[_COMMANDS] if row else None


def set_commands_channel_id(guild_id: int, channel_id: int | None) -> None:
    """Set the commands channel ID for a specific guild.

    Args:
        guild_id (int): The ID of the guild to set the commands channel ID for.
        channel_id (int | None): The channel ID to set, or None to unset it.
    """
    _set(guild_id, _COMMANDS, channel_id)


def get_log_channel_id(guild_id: int) -> int | None:
    """Retrieve the log channel ID of a specific guild.

    Args:
        guild_id (int): The ID of the guild to retrieve the log channel ID for.

    Returns:
        int | None: The log channel ID if the guild has one, otherwise None.
    """
    row = _read(guild_id)
    return row[_LOG] if row else None


def set_log_channel_id(guild_id: int, channel_id: int) -> None:
    """Set the log channel ID of a specific guild.

    Args:
        guild_id (int): The ID of the guild to set the log channel for.
        channel_id (int): The channel ID to set.
    """
    _set(guild_id, _LOG, channel_id)


def get_legacy_log_channel_id() -> int | None:
    """Retrieve the instance-wide log channel that older releases stored.

    Returns:
        int | None: The legacy log channel ID if one is still stored, otherwise None.
    """
    row = db.connection().execute("SELECT channel_id FROM legacy_log_channel").fetchone()
    return row["channel_id"] if row else None


def migrate_legacy_log_channel(guild_of_channel: Callable[[int], int | None]) -> int | None:
    """Give the legacy log channel to the guild that owns it, unless that guild already has one.

    The legacy entry stays, so the operator can still see where the channel came from.

    Args:
        guild_of_channel (Callable[[int], int | None]): Resolves a channel ID to its served guild ID, or None.

    Returns:
        int | None: The guild ID that received the channel, or None when nothing was written.
    """
    channel_id = get_legacy_log_channel_id()
    if channel_id is None:
        return None
    guild_id = guild_of_channel(channel_id)
    if guild_id is None:
        logger.warning(f"Legacy log channel not found or not served (channel_id={channel_id}): not migrated, kept in place.")
        return None
    if get_log_channel_id(guild_id) is not None:
        logger.debug(f"Legacy log channel already migrated or replaced (guild_id={guild_id}).")
        return None
    set_log_channel_id(guild_id, channel_id)
    logger.info(f"Legacy log channel migrated (guild_id={guild_id}, channel_id={channel_id}); legacy entry kept.")
    return guild_id


def is_welcome_verify_enabled(guild_id: int) -> bool:
    """Check if welcome verification is enabled for a specific guild.

    Args:
        guild_id (int): The ID of the guild to check.

    Returns:
        bool: True if welcome verification is enabled, False otherwise.
    """
    row = _read(guild_id)
    return bool(row[_WELCOME]) if row else False


def set_welcome_verify_enabled(guild_id: int, *, enabled: bool) -> None:
    """Set the welcome verification status for a specific guild.

    Args:
        guild_id (int): The ID of the guild to set the status for.
        enabled (bool): Whether to enable or disable the feature.
    """
    _set(guild_id, _WELCOME, int(enabled))
