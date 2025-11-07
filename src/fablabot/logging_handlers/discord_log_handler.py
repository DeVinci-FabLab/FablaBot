"""Discord log handler for logging messages to a specific Discord channel."""

import io
import logging
from typing import override

from discord import File, TextChannel
from discord.ext import commands

from fablabot.cogs.helpers.constants import MAX_MSG_CHARS
from fablabot.guild_config import get_log_channel_id

CLEAR = "\u001b[0m"
RED = "\u001b[31m"
UNDERLINE = "\u001b[4m"
CYAN = "\u001b[34m"


class DiscordLogHandler(logging.Handler):
    """An logging handler that posts messages to a Discord channel."""

    def __init__(self, bot: commands.Bot, level: int = logging.ERROR) -> None:
        """Initializes the DiscordLogHandler.

        Args:
            bot (commands.Bot): The bot instance to use for sending log messages.
            level (int): The logging level for this handler.
        """
        super().__init__()
        self.bot = bot
        self.log_channel_id = get_log_channel_id() or 0
        # ANSI escape code formatter for colored log output in Discord
        ansi_log_format = (
            f"{RED}[%(levelname)s]{CLEAR} {CYAN}{UNDERLINE}%(module)s.%(funcName)s{CLEAR}{CYAN}:{CLEAR} %(message)s"
        )
        self.setFormatter(logging.Formatter(ansi_log_format))
        self.setLevel(level)

    @override
    def emit(self, record: logging.LogRecord) -> None:
        """Sends a log message to the Discord channel.

        Args:
            record (logging.LogRecord): The log record to send.
        """
        msg = self.format(record)
        self.bot.loop.create_task(self._send(msg))

    def set_log_channel(self, channel: TextChannel | int) -> None:
        """Update the log channel destination.

        Args:
            channel (TextChannel | int): Channel instance or channel id receiving logs.
        """
        if isinstance(channel, TextChannel):
            self.log_channel_id = channel.id
        else:
            self.log_channel_id = channel

    async def _send(self, message: str) -> None:
        """Sends a log message to the Discord channel.

        Args:
            message (str): The message to send.
        """
        channel = self.bot.get_channel(self.log_channel_id)
        if not isinstance(channel, TextChannel):
            return
        log_message = f"```ansi\n{message}\n```"
        file_obj: io.BytesIO | None = None
        if len(log_message) > MAX_MSG_CHARS:
            file_obj = io.BytesIO(log_message.encode("utf-8"))
            log_message = "Log message too long, see attached file."

        if file_obj:
            await channel.send(log_message, file=File(file_obj, filename="log.txt"))
        else:
            await channel.send(log_message)
