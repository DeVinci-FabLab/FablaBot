"""Shared logging configuration for the bot runtime."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fablabot.logging_handlers import DailyFileHandler, DiscordLogHandler

if TYPE_CHECKING:
    from discord.ext import commands


_LOG_FORMAT = "[%(asctime)s] |  %(levelname)s  | %(name)s: %(message)s"


def configure_logging(bot: commands.Bot) -> tuple[DiscordLogHandler, DailyFileHandler]:
    """Configure root logging with console, file, and Discord handlers.

    Args:
        bot (commands.Bot): The bot instance for Discord logging.

    Returns:
        tuple[DiscordLogHandler, DailyFileHandler]: The Discord and file log handlers.
    """
    root_logger = logging.getLogger()
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)

    formatter = logging.Formatter(_LOG_FORMAT)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)

    file_handler = DailyFileHandler(level=logging.DEBUG, formatter=formatter)
    discord_handler = DiscordLogHandler(bot, level=logging.ERROR)

    root_logger.setLevel(logging.DEBUG)
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(discord_handler)

    return discord_handler, file_handler
