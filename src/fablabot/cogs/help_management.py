"""The ``/help`` command: what the member can run on this server."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from discord import AllowedMentions, Interaction, app_commands
from discord.app_commands import locale_str
from discord.ext import commands

from fablabot.helpers.access import EVERYONE, Caller, access
from fablabot.helpers.help_page import build_help, leaf_commands
from fablabot.helpers.i18n import catalog
from fablabot.helpers.utils import ensure_command_context

if TYPE_CHECKING:
    from discord import Guild, Member

logger = logging.getLogger(__name__)

_ENGLISH = catalog().texts("en")
"""The source strings Discord shows when a locale has no translation."""


def _source(key: str) -> locale_str:
    """Wrap the English text of ``key`` so the translator can localize it."""
    return locale_str(_ENGLISH.get(key), key=key)


class HelpManagement(commands.Cog):
    """Cog holding the ``/help`` command.

    Slash commands:
        - /help: List the commands the member can run, privately unless ``show`` is set.
    """

    def __init__(self, bot: commands.Bot) -> None:
        """Keep the bot whose command tree is listed.

        Args:
            bot (commands.Bot): The bot.
        """
        self.bot = bot

    @app_commands.command(
        name=_source("help.slash.name"),
        description=_source("help.slash.description"),
        extras=access(EVERYONE),
    )
    @app_commands.describe(show=_source("help.slash.show"))
    async def show_help(self, interaction: Interaction, *, show: bool = False) -> None:
        """Reply with the commands the member's roles open.

        Args:
            interaction (Interaction): The Discord interaction context.
            show (bool): Post the list in the channel instead of privately.
        """
        if not await ensure_command_context(logger, "help", interaction, log_details={"show": show}):
            return
        member: Member = interaction.user  # pyright: ignore[reportAssignmentType]  # commands run in servers only
        guild: Guild | None = interaction.guild
        assert guild is not None
        texts = catalog().texts(interaction.guild_locale.value if interaction.guild_locale else None)
        embeds = build_help(leaf_commands(self.bot.tree.walk_commands()), Caller.of(member, guild), texts)
        await interaction.response.send_message(
            embed=embeds[0],
            ephemeral=not show,
            allowed_mentions=AllowedMentions.none(),
        )
