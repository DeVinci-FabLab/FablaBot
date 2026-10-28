"""Discord log handler posting each error in the log channel of the server it came from."""

import contextvars
import io
import logging
from typing import override

from discord import AllowedMentions, File, HTTPException, TextChannel
from discord.ext import commands

from fablabot.guild_config import get_log_channel_id
from fablabot.helpers.constants import MAX_MSG_CHARS

logger = logging.getLogger(__name__)

_CLEAR = "\u001b[0m"
_RED = "\u001b[31m"
_UNDERLINE = "\u001b[4m"
_CYAN = "\u001b[34m"
_ZERO_WIDTH_SPACE = "​"


class DiscordLogHandler(logging.Handler):
    """A logging handler that posts messages to the log channel of their server."""

    def __init__(self, bot: commands.Bot, level: int = logging.ERROR, *, operator_channel_id: int | None = None) -> None:
        """Initializes the DiscordLogHandler.

        Args:
            bot (commands.Bot): The bot instance to use for sending log messages.
            level (int): The logging level for this handler.
            operator_channel_id (int | None): The channel receiving errors tied to no server, if any.
        """
        super().__init__()
        self.bot = bot
        self.operator_channel_id = operator_channel_id
        # ANSI escape code formatter for colored log output in Discord
        ansi_log_format = (
            f"{_RED}[%(levelname)s]{_CLEAR} {_CYAN}{_UNDERLINE}%(module)s.%(funcName)s{_CLEAR}{_CYAN}:{_CLEAR} %(message)s"
        )
        self.setFormatter(logging.Formatter(ansi_log_format))
        self.setLevel(level)

    @override
    def emit(self, record: logging.LogRecord) -> None:
        """Posts a log record to the channel of its server.

        Args:
            record (logging.LogRecord): The log record to send.
        """
        # A blank context keeps the send task's own lines out of the record's server.
        self.bot.loop.create_task(
            self._send(getattr(record, "guild_id", None), self.format(record)),
            context=contextvars.Context(),
        )

    def destination(self, guild_id: int | None) -> TextChannel | None:
        """Find the channel allowed to receive a record.

        Args:
            guild_id (int | None): The server the record is tied to, or None.

        Returns:
            TextChannel | None: The operator channel for a record tied to no server; the server's
                own log channel otherwise, only when it belongs to that server. None if neither applies.
        """
        if guild_id is None:
            if self.operator_channel_id is None:
                return None
            channel = self.bot.get_channel(self.operator_channel_id)
            return channel if isinstance(channel, TextChannel) else None
        channel_id = get_log_channel_id(guild_id)
        if channel_id is None:
            return None
        channel = self.bot.get_channel(channel_id)
        if isinstance(channel, TextChannel) and channel.guild.id == guild_id:
            return channel
        return None

    async def _send(self, guild_id: int | None, message: str) -> None:
        """Sends a log message to the channel allowed to receive it.

        Args:
            guild_id (int | None): The server the message is tied to, or None.
            message (str): The message to send.
        """
        channel = self.destination(guild_id)
        if channel is None:
            return
        # A run of three or more backticks would close the code block: break every backtick with a zero-width space.
        safe = message.replace("`", f"`{_ZERO_WIDTH_SPACE}")
        log_message = f"```ansi\n{safe}\n```"
        file_obj: io.BytesIO | None = None
        if len(log_message) > MAX_MSG_CHARS:
            file_obj = io.BytesIO(log_message.encode("utf-8"))
            log_message = "Log message too long, see attached file."

        try:
            if file_obj:
                await channel.send(
                    log_message,
                    file=File(file_obj, filename="log.txt"),
                    allowed_mentions=AllowedMentions.none(),
                )
            else:
                await channel.send(log_message, allowed_mentions=AllowedMentions.none())
        except HTTPException as exc:
            # WARNING stays below this handler's ERROR threshold, so a failed post never loops.
            logger.warning(f"Could not post a log line (guild_id={guild_id}, channel_id={channel.id}): HTTP {exc.status}")
