"""Entry point for the FablaBot Discord bot."""

from __future__ import annotations

import logging
import os
from typing import Any, override

from discord import Intents
from discord.app_commands import AppCommandContext
from discord.ext import commands
from dotenv import load_dotenv

from fablabot.cogs import (
    ChannelManagement,
    FormationManagement,
    LogManagement,
    MessageManagement,
    SuggestionManagement,
    UserManagement,
    WelcomeManagement,
)
from fablabot.logging_handlers import DailyFileHandler, DiscordLogHandler

logger = logging.getLogger(__name__)
load_dotenv()

DISCORD_TOKEN_FILE = os.environ.get("DISCORD_TOKEN_FILE") or ""
with open(DISCORD_TOKEN_FILE) as f:
    DISCORD_TOKEN = f.read().strip()


class Fablabot(commands.Bot):
    """Discord bot implementation for the DeVinci Fablab server."""

    def __init__(self) -> None:
        """Initialize the bot with the required intents.

        Intents required:
            - guilds: for guild-related events and commands
            - members: for member join/leave and member management
            - expressions: for custom emoji/sticker usage
            - voice_states: for voice channel management
            - guild_messages: for message handling in guilds
            - reactions: for handling message reactions
            - message_content: for reading message content
        """
        super().__init__(
            command_prefix=commands.when_mentioned,
            allowed_contexts=AppCommandContext(guild=True, dm_channel=False),
            intents=Intents(
                guilds=True,
                members=True,
                expressions=True,
                voice_states=True,
                guild_messages=True,
                reactions=True,
                message_content=True,
            ),
        )
        self.discord_log_handler: DiscordLogHandler | None = None
        self.file_log_handler: DailyFileHandler | None = None
        logger.info("Bot initialized")

    @override
    async def setup_hook(self) -> None:
        """Load the bot extensions."""
        root_logger = logging.getLogger()
        for h in list(root_logger.handlers):
            root_logger.removeHandler(h)

        formatter = logging.Formatter("[%(asctime)s] |  %(levelname)s  | %(name)s: %(message)s")

        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)

        file_handler = DailyFileHandler(level=logging.DEBUG, formatter=formatter)
        root_logger.addHandler(file_handler)

        discord_handler = DiscordLogHandler(self, level=logging.ERROR)
        root_logger.addHandler(discord_handler)

        root_logger.setLevel(logging.DEBUG)

        self.discord_log_handler = discord_handler
        self.file_log_handler = file_handler

        self.tree.clear_commands(guild=None)
        for cog in (
            ChannelManagement(self),
            FormationManagement(self),
            LogManagement(self),
            MessageManagement(self),
            SuggestionManagement(self),
            UserManagement(self),
            WelcomeManagement(self),
        ):
            await self.add_cog(cog)
            logger.info(f"Loaded cog {cog.__class__.__name__}")

        # Synchronisation globale des commandes
        await self.tree.sync()
        logger.info("Commands synced")

    @override
    async def on_command_error(self, ctx: commands.Context[Any], exception: Exception) -> None:
        """Handle uncaught command errors.

        Args:
            ctx (commands.Context[Any]): The invocation context of the command.
            exception (Exception): The raised exception.
        """
        logger.error(f"Unhandled command error: {exception}")
        await ctx.reply(str(exception), ephemeral=True)


def main() -> None:
    """Run the bot using the token from the environment."""
    logger.info("Starting FablaBot")
    bot = Fablabot()
    logger.info("Running bot")
    bot.run(DISCORD_TOKEN)


if __name__ == "__main__":
    main()
