"""Discord log handler for logging messages to a specific Discord channel."""

import logging

from discord import TextChannel
from discord.ext import commands

LOG_CHANNEL_ID = 1401698341653446686


class DiscordLogHandler(logging.Handler):
    """Un handler de logging qui post les messages dans un channel Discord."""

    def __init__(self, bot: commands.Bot):
        super().__init__()
        self.bot = bot
        self.setFormatter(logging.Formatter("[2;31m[%(levelname)s][0m [2;31m[0m[2;34m%(module)s:[0m %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        msg = self.format(record)
        self.bot.loop.create_task(self._send(msg))

    async def _send(self, message: str) -> None:
        channel = self.bot.get_channel(LOG_CHANNEL_ID)
        if channel is None:
            return
        if isinstance(channel, TextChannel):
            await channel.send(f"```ansi\n{message}\n```")
