"""Utilities for recording and purging reaction logs shared across cogs."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from fablabot.helpers.constants import PARIS_TZ
from fablabot.models.common import ReactionEvent

if TYPE_CHECKING:
    from logging import Logger

    from fablabot.helpers.state_store import JsonStateStore


class ReactionLogManager:
    """Shared reaction log handler that persists alongside a guild-scoped state mapping."""

    def __init__(
        self,
        state_store: JsonStateStore,
        retention: timedelta,
        logger: Logger,
    ) -> None:
        """Initialize the manager.

        Args:
            state_store (JsonStateStore): Shared JSON-backed state store.
            retention (timedelta): Duration to retain reaction events.
            logger (Logger): Logger used for diagnostics.
        """
        self.state_store = state_store
        self.state = state_store.state
        self._retention = retention
        self.logger = logger

    def purge_all(self) -> None:
        """Purge all guild reaction logs according to retention."""
        if self._purge_all_internal():
            self.state_store.save()
            self.logger.debug("Purged reaction logs and persisted state.")
        else:
            self.logger.debug("No reaction log entries required purging.")

    def append(self, guild_id: int, event: ReactionEvent) -> None:
        """Append a reaction entry for a guild and persist.

        Args:
            guild_id (int): Guild identifier.
            event (ReactionEvent): Event payload to store.
        """
        self._purge_all_internal()
        guild_state = self._get_guild_state(guild_id)
        raw_log = guild_state.get("reactions_log") or []
        log = [ReactionEvent.from_dict(entry) for entry in raw_log]
        if event.user_name is None:
            event.user_name = self._last_known_name(log, event.user_id)
        log.append(event)
        guild_state["reactions_log"] = [ev.to_dict() for ev in log]
        self.state_store.save()
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
            list[ReactionEvent]: List of reaction events for the message, sorted by timestamp.
        """
        guild_state = self._get_guild_state(guild_id)
        raw_log = guild_state.get("reactions_log", []) or []
        history = [ev for entry in raw_log if (ev := ReactionEvent.from_dict(entry)).message_id == message_id]
        history.sort(key=lambda ev: ev.ts_iso or "")
        self.logger.debug(f"Loaded {len(history)} reaction events for guild {guild_id} message {message_id}.")
        return history

    def _get_guild_state(self, guild_id: int) -> dict:
        return self.state_store.ensure_guild(guild_id)

    def _purge_all_internal(self) -> bool:
        changed = False
        removed_total = 0
        for _, guild_state in self.state_store.guild_items():
            raw_log = guild_state.get("reactions_log")
            if not raw_log:
                continue
            filtered = self._purge_entries([ReactionEvent.from_dict(entry) for entry in raw_log])
            if len(filtered) != len(raw_log):
                removed_total += len(raw_log) - len(filtered)
                guild_state["reactions_log"] = [event.to_dict() for event in filtered]
                changed = True
        if changed:
            self.logger.debug(f"Purged {removed_total} reaction log entries across guilds.")
        return changed

    def _purge_entries(self, log: list[ReactionEvent]) -> list[ReactionEvent]:
        cutoff = datetime.now(PARIS_TZ) - self._retention
        filtered: list[ReactionEvent] = []
        for entry in log:
            ts_iso = entry.ts_iso
            if not ts_iso:
                filtered.append(entry)
                continue
            try:
                ts = datetime.fromisoformat(ts_iso).astimezone(PARIS_TZ)
            except Exception:
                filtered.append(entry)
                continue
            if ts >= cutoff:
                filtered.append(entry)
        return filtered

    @staticmethod
    def _last_known_name(log: list[ReactionEvent], user_id: int) -> str | None:
        for entry in reversed(log):
            if entry.user_id == user_id and entry.user_name:
                return entry.user_name
        return None
