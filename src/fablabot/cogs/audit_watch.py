"""Tell the log channel at once of every change made by a watched account, such as a shared admin login."""

from __future__ import annotations

from functools import cache
import logging
import os
from typing import TYPE_CHECKING, Final

from discord import AllowedMentions, TextChannel
from discord.ext import commands

from fablabot.guild_config import get_log_channel_id
from fablabot.helpers.op_report import describe, split_messages

if TYPE_CHECKING:
    from discord import AuditLogEntry

    from fablabot.main import Fablabot

logger = logging.getLogger(__name__)

AUDIT_WATCH_ENV: Final = "AUDIT_WATCH_USER_IDS"


def parse_watched_ids(raw: str | None) -> frozenset[int]:
    """Parse the watched account IDs, comma-separated; a malformed ID is dropped and reported, never echoed.

    Args:
        raw (str | None): The raw value of the environment variable.

    Returns:
        frozenset[int]: The account IDs.
    """
    ids: set[int] = set()
    for part in (raw or "").split(","):
        value = part.strip()
        if not value:
            continue
        # str.isdigit alone accepts non-ASCII digits.
        if value.isascii() and value.isdigit() and int(value) > 0:
            ids.add(int(value))
        else:
            logger.warning(f"{AUDIT_WATCH_ENV} holds a value that is not an account ID: it is ignored.")
    return frozenset(ids)


@cache
def watched_ids() -> frozenset[int]:
    """Read the watched accounts from the environment, once."""
    return parse_watched_ids(os.environ.get(AUDIT_WATCH_ENV))


class AuditWatch(commands.Cog):
    """Post each audit log entry of a watched account in its server's log channel.

    Listeners:
        - on_audit_log_entry_create: the entry, with each changed field before and after.
    """

    def __init__(self, bot: Fablabot) -> None:
        """Initialize the cog.

        Args:
            bot (Fablabot): The bot instance.
        """
        self.bot = bot
        # Read at start, so a malformed value is reported then and not at the first change.
        logger.info(f"Accounts watched through {AUDIT_WATCH_ENV}: {len(watched_ids())}.")

    @commands.Cog.listener()
    async def on_audit_log_entry_create(self, entry: AuditLogEntry) -> None:
        """Post a watched account's change in the log channel; messages sent are not in the audit log."""
        if entry.user_id not in watched_ids() or not self.bot.guild_access.is_served(entry.guild.id):
            return
        channel = entry.guild.get_channel(get_log_channel_id(entry.guild.id) or 0)
        if not isinstance(channel, TextChannel):
            logger.warning(f"Watched account acted with no log channel guild_id={entry.guild.id} user_id={entry.user_id}")
            return
        header = f"Compte surveillé <@{entry.user_id}> : nouvelle action."
        try:
            for message in split_messages(header, [describe(entry)]):
                await channel.send(message, allowed_mentions=AllowedMentions.none())
        except Exception:  # an odd value or a refused post: the log file still holds the failure
            logger.exception(f"Watched account alert not posted guild_id={entry.guild.id} user_id={entry.user_id}")
