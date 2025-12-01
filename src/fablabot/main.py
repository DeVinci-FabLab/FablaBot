"""Entry point for the FablaBot Discord bot."""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any, override

from discord import Intents
from discord.app_commands import AppCommandContext
from discord.ext import commands
from dotenv import load_dotenv

from fablabot.cogs import (
    ChannelManagement,
    FormationManagement,
    LogManagement,
    MessageManagement,
    UserManagement,
    WelcomeManagement,
)
from fablabot.helpers.logging_setup import configure_logging

if TYPE_CHECKING:
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
        self.discord_log_handler, self.file_log_handler = configure_logging(self)

        self.tree.clear_commands(guild=None)
        for cog in (
            ChannelManagement(self),
            FormationManagement(self),
            LogManagement(self),
            MessageManagement(self),
            UserManagement(self),
            WelcomeManagement(self),
        ):
            await self.add_cog(cog)
            logger.info(f"Loaded cog {cog.__class__.__name__}.")

        # Synchronisation globale des commandes
        await self.tree.sync()
        logger.info("Commands synced.")

    @override
    async def on_command_error(self, ctx: commands.Context[Any], exception: Exception) -> None:
        """Handle uncaught command errors.

        Args:
            ctx (commands.Context[Any]): The invocation context of the command.
            exception (Exception): The raised exception.
        """
        logger.exception("Unhandled command error", exc_info=exception)
        await ctx.send("Une erreur inattendue est survenue lors de l'exécution de la commande.")


def main() -> None:
    """Run the bot using the token from the environment."""
    logger.info("Starting FablaBot.")
    bot = Fablabot()
    logger.info("Running bot.")
    bot.run(DISCORD_TOKEN)


if __name__ == "__main__":
    main()
