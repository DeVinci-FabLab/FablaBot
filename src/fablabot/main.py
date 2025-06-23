"""Entry point for the FablaBot Discord bot."""

from __future__ import annotations

import logging
import os

import discord
from discord.ext import commands
from dotenv import load_dotenv

from fablabot.cogs import user_management

logger = logging.getLogger(__name__)
load_dotenv()

DISCORD_TOKEN: str = os.environ.get("DISCORD_TOKEN") or ""


class Fablabot(commands.Bot):
    """Discord bot implementation for the DeVinci Fablab server."""

    def __init__(self) -> None:
        """Initialize the bot with the required intents."""
        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=discord.Intents.all(),  # TODO: only enable intents we use here and on the developer portal, this will make discord happy
        )
        logger.info("Bot initialized")

    async def setup_hook(self) -> None:
        """Load the bot extensions."""
        for cog in (user_management.UserManagement(self),):
            await self.add_cog(cog)
            logger.info("Loaded cog %s", cog.__class__.__name__)

        # Synchronisation globale des commandes
        await self.tree.sync()
        logger.info("Commands synced")

    async def on_command_error(self, ctx: commands.Context, exception: Exception) -> None:
        """Handle uncaught command errors.

        Args:
            ctx: The invocation context of the command.
            exception: The raised exception.
        """
        logger.error("Unhandled command error: %s", exception)
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
