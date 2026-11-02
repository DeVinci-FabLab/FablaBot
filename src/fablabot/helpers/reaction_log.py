"""Utilities for recording and purging reaction logs shared across cogs."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Final, Literal

from fablabot import db
from fablabot.helpers.constants import PARIS_TZ
from fablabot.models.common import ReactionEvent

if TYPE_CHECKING:
    from datetime import timedelta
    from logging import Logger

type ReactionScope = Literal["formation", "message"]

# Above SQLite's largest integer, which a message ID typed by a member can pass.
_MAX_ID: Final = 2**63 - 1


class ReactionLogManager:
    """Shared reaction log handler, one instance per scope."""

    def __init__(
        self,
        scope: ReactionScope,
        retention: timedelta,
        logger: Logger,
    ) -> None:
        """Initialize the manager.

        Args:
            scope (ReactionScope): Which history this manager keeps.
            retention (timedelta): Duration to retain reaction events.
            logger (Logger): Logger used for diagnostics.
        """
        self._scope = scope
        self._retention = retention
        self.logger = logger

    def purge_all(self) -> None:
        """Purge all guild reaction logs according to retention."""
        with db.transaction():
            removed = self._purge()
        if removed:
            self.logger.debug(f"Purged {removed} reaction log entries across guilds.")
        else:
            self.logger.debug("No reaction log entries required purging.")

    def append(self, guild_id: int, event: ReactionEvent) -> None:
        """Append a reaction entry for a guild and persist.

        Args:
            guild_id (int): Guild identifier.
            event (ReactionEvent): Event payload to store.
        """
        with db.transaction() as conn:
            self._purge()
            if event.user_name is None:
                event.user_name = self._last_known_name(guild_id, event.user_id)
            conn.execute(
                "INSERT INTO reaction_event (scope, guild_id, message_id, user_id, user_name, emoji, action, ts_iso) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    self._scope,
                    guild_id,
                    event.message_id,
                    event.user_id,
                    event.user_name,
                    event.emoji,
                    event.action.value,
                    event.ts_iso,
                ),
            )
        self.logger.debug(
            f"Logged reaction {event.action} on message {event.message_id} in guild {guild_id} "
            f"for user {event.user_id} with emoji {event.emoji}.",
        )

    def history(self, guild_id: int, message_id: int) -> list[ReactionEvent]:
        """Retrieve reaction history for a message within a guild.

        Args:
            guild_id (int): Guild identifier.
            message_id (int): Message identifier.

        Returns:
            list[ReactionEvent]: List of reaction events for the message, in the order they happened.
        """
        if abs(message_id) > _MAX_ID:
            return []
        # By the instant, not the text: the hour from 02:00 to 03:00 on the last Sunday of October happens twice, and its
        # second pass has the smaller text (02:05+01:00 follows 02:50+02:00). A date SQLite cannot read comes first.
        rows = db.connection().execute(
            "SELECT message_id, user_id, user_name, emoji, action, ts_iso FROM reaction_event "
            "WHERE scope = ? AND guild_id = ? AND message_id = ? ORDER BY unixepoch(ts_iso), id",
            (self._scope, guild_id, message_id),
        )
        history = [ReactionEvent.from_dict(dict(row)) for row in rows]
        self.logger.debug(f"Loaded {len(history)} reaction events for guild {guild_id} message {message_id}.")
        return history

    def _purge(self) -> int:
        """Delete this scope's events past retention, inside the caller's transaction; a date SQLite cannot read stays.

        Returns:
            int: The number of events removed.
        """
        cutoff = int((datetime.now(PARIS_TZ) - self._retention).timestamp())
        return (
            db.connection()
            .execute("DELETE FROM reaction_event WHERE scope = ? AND unixepoch(ts_iso) < ?", (self._scope, cutoff))
            .rowcount
        )

    def _last_known_name(self, guild_id: int, user_id: int) -> str | None:
        """Retrieve the last known username for a user from the reaction log."""
        row = (
            db.connection()
            .execute(
                "SELECT user_name FROM reaction_event WHERE scope = ? AND guild_id = ? AND user_id = ? "
                "AND user_name IS NOT NULL AND user_name <> '' ORDER BY id DESC LIMIT 1",
                (self._scope, guild_id, user_id),
            )
            .fetchone()
        )
        return row["user_name"] if row else None
