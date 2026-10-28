"""Shared logging configuration for the bot runtime."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fablabot.helpers.log_channels import operator_log_channel_id
from fablabot.logging_handlers import (
    DailyFileHandler,
    DiscordLogHandler,
    GuildContextFilter,
    SingleLineFormatter,
    install_gateway_guild_context,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from discord.ext import commands


_LOG_FORMAT = "[%(asctime)s] |  %(levelname)s  | guild_id=%(guild_id)s | %(name)s: %(message)s"


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

    formatter = SingleLineFormatter(_LOG_FORMAT)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)

    file_handler = DailyFileHandler(level=logging.DEBUG, formatter=formatter)
    # Private discord.py attribute (the gateway's parser table): re-check it on the discord.py 2.7 upgrade.
    # If it is renamed, no line is tied to a server and the operator channel stays off.
    attributed = install_gateway_guild_context(getattr(getattr(bot, "_connection", None), "parsers", None))
    discord_handler = DiscordLogHandler(
        bot,
        level=logging.ERROR,
        operator_channel_id=operator_log_channel_id() if attributed else None,
    )
    # The filter sits on each handler: a root-logger filter skips records propagated from child loggers.
    guild_filter = GuildContextFilter()
    for handler in (console_handler, file_handler, discord_handler):
        handler.addFilter(guild_filter)

    root_logger.setLevel(logging.DEBUG)
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(discord_handler)
    # discord.py's DEBUG traces carry the bodies the bot sends, so keep them out of the file.
    logging.getLogger("discord").setLevel(logging.INFO)

    if not attributed:
        logger.error(
            "Log lines cannot be tied to their server (discord.py internals changed): the operator log channel stays off.",
        )
    if discord_handler.operator_channel_id is not None:
        logger.info(f"Operator log channel: set (channel_id={discord_handler.operator_channel_id}).")
    else:
        logger.info("Operator log channel: not set; errors without a server stay in the log files.")

    return discord_handler, file_handler
