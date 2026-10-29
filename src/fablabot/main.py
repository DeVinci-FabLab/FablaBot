"""Entry point for the FablaBot Discord bot."""

from __future__ import annotations

from http import HTTPStatus
import logging
import os
import sys
from typing import TYPE_CHECKING, Any, override

from discord import Intents
from discord.app_commands import AppCommandContext
from discord.ext import commands
from dotenv import load_dotenv

from fablabot.cogs import (
    ChannelManagement,
    FormationManagement,
    GuildAccess,
    LogManagement,
    MessageManagement,
    UserManagement,
    WelcomeManagement,
)
from fablabot.helpers.command_sync import (
    COMMAND_SYNC_STATE_FILE,
    DEV_GUILD_ID_ENV,
    parse_dev_guild_id,
    sync_commands_if_changed,
)
from fablabot.helpers.guild_access import GuildAccessPolicy, GuildAccessTree
from fablabot.helpers.logging_setup import configure_logging
from fablabot.helpers.startup_checks import TOKEN_FILE_ENV, StartupError, check_runtime_access
from fablabot.helpers.state_store import JsonStateStore

if TYPE_CHECKING:
    from fablabot.logging_handlers import DailyFileHandler, DiscordLogHandler

logger = logging.getLogger(__name__)
load_dotenv()


class DiscordGateway503Filter(logging.Filter):
    """Filter Discord gateway 503 warnings into a cleaner message."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Replace a gateway 503 handshake error with one warning and drop the original record.

        Args:
            record (logging.LogRecord): The log record being filtered.

        Returns:
            bool: False for a gateway 503 record, True for any other.
        """
        if not record.exc_info:
            return True

        exception = record.exc_info[1]

        from aiohttp import WSServerHandshakeError

        if (
            isinstance(exception, WSServerHandshakeError)
            and getattr(exception, "status", None) == HTTPStatus.SERVICE_UNAVAILABLE
        ):
            retry_message = record.getMessage()

            logger.warning(
                "Discord Gateway indisponible (503). %s",
                retry_message,
            )

            return False

        return True


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
        # Parsed before the base class so a malformed list or dev guild stops the process before login.
        policy = GuildAccessPolicy.from_env()
        dev_guild_id = parse_dev_guild_id(os.environ.get(DEV_GUILD_ID_ENV), allowed=policy.allowed)
        super().__init__(
            command_prefix=commands.when_mentioned,
            tree_cls=GuildAccessTree,
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
        self.guild_access: GuildAccessPolicy = policy
        self.dev_guild_id: int | None = dev_guild_id
        self.discord_log_handler: DiscordLogHandler | None = None
        self.file_log_handler: DailyFileHandler | None = None
        logger.info("Bot initialized")

    @override
    async def setup_hook(self) -> None:
        """Load the bot extensions."""
        self.discord_log_handler, self.file_log_handler = configure_logging(self)

        self.tree.clear_commands(guild=None)
        for cog in (
            GuildAccess(self),
            ChannelManagement(self),
            FormationManagement(self),
            LogManagement(self),
            MessageManagement(self),
            UserManagement(self),
            WelcomeManagement(self),
        ):
            await self.add_cog(cog)
            logger.info(f"Loaded cog {cog.__class__.__name__}.")

        await sync_commands_if_changed(
            self.tree,
            JsonStateStore(logger, COMMAND_SYNC_STATE_FILE),
            application_id=self.application_id,
            dev_guild_id=self.dev_guild_id,
        )

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
    """Check that the bot can start, then run it with the token from the environment."""
    try:
        token = check_runtime_access(os.environ.get(TOKEN_FILE_ENV))
    except StartupError as error:
        sys.exit("\n".join(f"Cannot start: {problem}" for problem in error.problems))

    discord_logger = logging.getLogger("discord.client")
    discord_logger.addFilter(DiscordGateway503Filter())

    logger.info("Starting FablaBot.")
    bot = Fablabot()
    logger.info("Running bot.")
    bot.run(token)


if __name__ == "__main__":
    main()
