"""Entry point for the FablaBot Discord bot."""

from __future__ import annotations

import logging
import os

from discord import Intents
from discord.ext import commands
from dotenv import load_dotenv

from fablabot.cogs import ChannelManagement, UserManagement

logger = logging.getLogger(__name__)
load_dotenv()

DISCORD_TOKEN_FILE = os.environ.get("DISCORD_TOKEN_FILE") or ""
with open(DISCORD_TOKEN_FILE) as f:
    DISCORD_TOKEN = f.read().strip()


class Fablabot(commands.Bot):
    """Discord bot implementation for the DeVinci Fablab server."""

    def __init__(self) -> None:
        """Initialize the bot with the required intents."""
        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=Intents(guilds=True, members=True, voice_states=True, guild_messages=True),
        )
        logger.info("Bot initialized")

    async def setup_hook(self) -> None:
        """Load the bot extensions."""
        self.tree.clear_commands(guild=None)
        for cog in (ChannelManagement(self), UserManagement(self)):
            await self.add_cog(cog)
            logger.info(f"Loaded cog {cog.__class__.__name__}")

        # Synchronisation globale des commandes
        await self.tree.sync()
        logger.info("Commands synced")

    async def on_command_error(self, ctx: commands.Context, exception: Exception) -> None:
        """Handle uncaught command errors.

        Args:
            ctx (commands.Context): The invocation context of the command.
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
