"""Entry point for the FablaBot Discord bot."""

from __future__ import annotations

import logging
import os
from typing import Any, override

from discord import Intents
from discord.app_commands import AppCommandContext
from discord.ext import commands
from dotenv import load_dotenv

from fablabot.cogs import ChannelManagement, FormationManagement, UserManagement, Welcome
from fablabot.discord_log_handler import DiscordLogHandler

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
        """
        super().__init__(
            command_prefix=commands.when_mentioned,
            allowed_contexts=AppCommandContext(guild=True, dm_channel=False),
            intents=Intents(
                guilds=True, members=True, expressions=True, voice_states=True, guild_messages=True, reactions=True
            ),
        )
        self.log_handler: DiscordLogHandler | None = None
        logger.info("Bot initialized")

    @override
    async def setup_hook(self) -> None:
        """Load the bot extensions."""
        handler = DiscordLogHandler(self)
        handler.setLevel(logging.ERROR)
        logging.getLogger().addHandler(handler)
        logging.getLogger().setLevel(logging.INFO)
        self.log_handler = handler

        self.tree.clear_commands(guild=None)
        for cog in (ChannelManagement(self), FormationManagement(self), UserManagement(self), Welcome(self)):
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
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s:%(name)s: %(message)s")
    logger.info("Starting FablaBot")
    bot = Fablabot()
    logger.info("Running bot")
    bot.run(DISCORD_TOKEN)


if __name__ == "__main__":
    main()
