"""Discord log handler for logging messages to a specific Discord channel."""

import logging
from typing import override

from discord import TextChannel
from discord.ext import commands

LOG_CHANNEL_ID = 1401698341653446686


class DiscordLogHandler(logging.Handler):
    """An logging handler that posts messages to a Discord channel."""

    def __init__(self, bot: commands.Bot):
        """Initializes the DiscordLogHandler.

        Args:
            bot (commands.Bot): The bot instance to use for sending log messages.
        """
        super().__init__()
        self.bot = bot
        self.setFormatter(logging.Formatter("[2;31m[%(levelname)s][0m [2;31m[0m[2;34m%(module)s:[0m %(message)s"))

    @override
    def emit(self, record: logging.LogRecord) -> None:
        """Sends a log message to the Discord channel.

        Args:
            record (logging.LogRecord): The log record to send.
        """
        msg = self.format(record)
        self.bot.loop.create_task(self._send(msg))

    async def _send(self, message: str) -> None:
        """Sends a log message to the Discord channel.

        Args:
            message (str): The message to send.
        """
        channel = self.bot.get_channel(LOG_CHANNEL_ID)
        if channel is None:
            return
        if isinstance(channel, TextChannel):
            await channel.send(f"```ansi\n{message}\n```")
