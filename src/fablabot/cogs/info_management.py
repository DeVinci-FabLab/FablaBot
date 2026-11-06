"""The ``/info`` command: how the bot and this server stand, for the staff who keep them running."""

from __future__ import annotations

from datetime import timedelta
import logging
import math
import os
from time import monotonic
from typing import TYPE_CHECKING

from discord import AllowedMentions, Interaction, TextChannel, app_commands
from discord.ext import commands

from fablabot import db
from fablabot.cogs.audit_watch import watched_ids
from fablabot.guild_config import get_log_channel_id
from fablabot.helpers.access import AnyRole, access
from fablabot.helpers.constants import INFO_ROLES, staff_default_permissions
from fablabot.helpers.info_page import REVISION_ENV, Facts, build_info, short_revision
from fablabot.helpers.server_check import FIXES, check_guild, languages
from fablabot.helpers.utils import ensure_command_context, find_commands_channel

if TYPE_CHECKING:
    from discord import Guild

    from fablabot.main import Fablabot

logger = logging.getLogger(__name__)

_MILLISECONDS = 1000


class InfoManagement(commands.Cog):
    """Cog holding the ``/info`` command.

    Slash commands:
        - /info: Show how the bot runs and what this server lacks, privately, for Respo Numérique and the admin roles.
    """

    def __init__(self, bot: Fablabot) -> None:
        """Keep the bot whose state is shown.

        Args:
            bot (Fablabot): The bot.
        """
        self.bot = bot

    def _facts(self, guild: Guild) -> Facts:
        """Gather what the embed says: the instance as counts, and the settings of ``guild`` only.

        Args:
            guild (Guild): The server that asked.

        Returns:
            Facts: The facts, read now.
        """
        stored_log = get_log_channel_id(guild.id)
        log_channel = guild.get_channel(stored_log) if stored_log is not None else None
        commands_channel = find_commands_channel(guild)
        status = db.status()
        latency = self.bot.latency
        return Facts(
            version=db.release(),
            uptime=timedelta(seconds=monotonic() - self.bot.started_at),
            latency_ms=round(latency * _MILLISECONDS) if math.isfinite(latency) else None,
            schema_version=status.schema_version,
            database_bytes=status.size,
            copies=status.copies,
            servers=sum(self.bot.guild_access.is_served(served.id) for served in self.bot.guilds),
            commands=len(self.bot.tree.get_commands()),
            watched=len(watched_ids()),
            commands_channel_id=commands_channel.id if commands_channel else None,
            log_channel_id=log_channel.id if isinstance(log_channel, TextChannel) else None,
            language=languages(guild),
            revision=short_revision(os.environ.get(REVISION_ENV)),
        )

    @app_commands.command(
        name="info",
        description="Montre l'état du bot et ce qu'il faut corriger sur ce serveur.",
        extras=access(AnyRole(INFO_ROLES)),
    )
    @app_commands.default_permissions(staff_default_permissions())
    async def info(self, interaction: Interaction) -> None:
        """Run the checks of this server again and answer privately with the state of the bot and what they found.

        Args:
            interaction (Interaction): The Discord interaction context.
        """
        # Not held to the commands channel: this is where a server finds out it has none.
        if not await ensure_command_context(
            logger,
            "info",
            interaction,
            required_roles=INFO_ROLES,
            enforce_commands_channel=False,
        ):
            return
        assert interaction.guild is not None
        guild = interaction.guild
        # The checks read from Discord and can outlast the three seconds an interaction waits for its first answer.
        await interaction.response.defer(ephemeral=True)
        try:
            problems = await check_guild(self.bot, guild)
            embed = build_info(self._facts(guild), problems)
        except Exception:
            logger.exception(f"Info could not be read guild_id={guild.id}")
            await interaction.followup.send(
                "Impossible de lire l'état du bot pour l'instant. Réessaie dans un moment : le détail est dans les logs.",
                ephemeral=True,
            )
            return
        blocking = sum(FIXES[problem.code][0] == "blocking" for problem in problems)
        logger.info(f"Info: guild_id={guild.id} blocking={blocking} warnings={len(problems) - blocking}")
        await interaction.followup.send(embed=embed, ephemeral=True, allowed_mentions=AllowedMentions.none())
