"""Suggestion management commands for Discord Bot. Provides slash commands for sending suggestions."""

from __future__ import annotations

from datetime import datetime
import logging
from warnings import deprecated

from discord import Embed, Guild, Interaction, Member, app_commands
from discord.ext import commands
from discord.utils import get

from fablabot.helpers import PARIS_TZ, get_members_by_role, send_dm_to_member
from fablabot.models.message import ANONYMOUS_ICON_URL, SuggestionConfig
from fablabot.ui import message as mui

logger = logging.getLogger(__name__)


class SuggestionManagement(commands.Cog):
    """Cog to register suggestion management commands.

    Commands:
        - /suggest: Send a suggestion to a chosen recipient.
    """

    def __init__(self, bot: commands.Bot) -> None:
        """Initialize the cog and register its command groups.

        Args:
            bot (commands.Bot): The bot instance.
        """
        self.bot = bot
        logger.info("SuggestionManagement initialized")

    # region ====== Suggest Slash Command ======

    @app_commands.command(name="suggest", description="Envoyer une suggestion (choisir destinataire, texte, anonymat)")
    async def suggest(self, interaction: Interaction) -> None:
        """Single command entrypoint for sending suggestions via a small interactive flow.

        The flow collects the recipient and anonymity via components, then opens a modal to input the suggestion text.

        Args:
            interaction (Interaction): The Discord interaction context.
        """
        view = mui.InitialView(self)
        await interaction.response.send_message(
            "Veuillez choisir le destinataire et si vous souhaitez rester anonyme, puis cliquez sur 'Rédiger la suggestion'.",
            view=view,
            ephemeral=True,
        )

    # endregion Suggest Slash Commands Group

    # region ====== Helpers ======

    async def handle_suggestion(
        self,
        interaction: Interaction,
        suggestion_text: str,
        config: SuggestionConfig,
        *,
        anonymous: bool,
    ) -> None:
        """Handle a suggestion command.

        Args:
            interaction (Interaction): The Discord interaction context.
            suggestion_text (str): The raw suggestion text.
            config (SuggestionConfig): The suggestion configuration.
            anonymous (bool): Whether the suggestion is anonymous or not.
        """
        logger.info(
            f"[{config.command_name}]: user={interaction.user if not anonymous else 'Anonymous'!r} "
            f"suggestion_text={suggestion_text!r}",
        )

        cleaned_text = suggestion_text.strip()
        if not cleaned_text:
            await interaction.followup.send(
                "Le texte de la suggestion ne peut pas être vide.",
                ephemeral=True,
            )
            return

        assert interaction.guild is not None
        author = interaction.user
        assert isinstance(author, Member)

        embed = Embed(
            title=config.embed_title,
            description=suggestion_text,
            color=config.embed_color,
            timestamp=datetime.now(PARIS_TZ),
        )
        if not anonymous:
            embed.set_author(name=author.display_name, icon_url=author.display_avatar.url)
            embed.add_field(name="Utilisateur·ice", value=author.mention, inline=False)
        else:
            embed.set_author(
                name="Anonymous",
                icon_url=ANONYMOUS_ICON_URL,
            )

        success = await self._send_suggestion(interaction.guild, embed, config, author.id if not anonymous else None)

        if not success:
            await interaction.response.send_message(config.error_message, ephemeral=True)
            return

        logger.info(
            f"Guild {interaction.guild.id} user "
            f"{interaction.user.id if not anonymous else 'Anonymous'} made a suggestion via {config.command_name}.",
        )
        await interaction.response.defer()

    async def _send_suggestion(
        self,
        guild: Guild,
        embed: Embed,
        config: SuggestionConfig,
        user_id: int | None,
    ) -> bool:
        """Send a suggestion to responsible members and channel.

        Args:
            guild (Guild): The guild where the suggestion is made.
            embed (Embed): The suggestion embed to send.
            config (SuggestionConfig): The suggestion configuration.
            user_id (int | None): The ID of the user making the suggestion.

        Returns:
            bool: True if at least one recipient received the suggestion, False otherwise.
        """
        responsibles: set[Member] = get_members_by_role(logger, guild, role=config.role_name) if config.role_name else set()
        channel = get(guild.text_channels, name=config.channel_name) if config.channel_name else None

        if not responsibles and not channel:
            logger.error(
                f"Guild {guild.id} has no {config.role_name} responsibles and no {config.channel_name} channel configured.",
            )
            return False

        for responsible in responsibles:
            await send_dm_to_member(logger, guild, responsible, None, embed=embed, dm_type="suggestion")
        if channel:
            try:
                await channel.send(embed=embed)
            except Exception:
                logger.exception(f"Failed to send suggestion from user {user_id or 'Anonymous'} to channel {channel.id}.")

        return True

    # endregion Helpers


@deprecated("Load the cog using `bot.add_cog()` instead.")
async def setup(bot: commands.Bot) -> None:
    """Setup function to load the SuggestionManagement cog.

    Args:
        bot (commands.Bot): The bot instance.
    """
    await bot.add_cog(SuggestionManagement(bot))
