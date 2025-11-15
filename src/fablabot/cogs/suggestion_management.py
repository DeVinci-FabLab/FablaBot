"""Suggestion management commands for Discord Bot. Provides slash commands for sending suggestions."""

from __future__ import annotations

from datetime import datetime
import logging
from warnings import deprecated

from discord import (
    ButtonStyle,
    Embed,
    Guild,
    Interaction,
    Member,
    SelectOption,
    TextStyle,
    app_commands,
    ui,
)
from discord.ext import commands
from discord.utils import get

from fablabot.cogs.helpers import PARIS_TZ, get_members_by_role, send_dm_to_member
from fablabot.cogs.helpers.message import ANONYMOUS_ICON_URL, SUGGESTION_OPTIONS, SuggestionConfig

logger = logging.getLogger(__name__)


class SuggestionManagement(commands.Cog):
    """Cog to register suggestion management commands.

    Commands:
        - /suggest: Send a suggestion to a chosen recipient.

    Attributes:
        suggest_group (app_commands.Group): Command group for feature suggestion commands.
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
        """
        view = InitialView(self)
        await interaction.response.send_message(
            "Veuillez choisir le destinataire et si vous souhaitez rester anonyme, puis cliquez sur 'Rédiger la suggestion'.",
            view=view,
            ephemeral=True,
        )

    # endregion Suggest Slash Commands Group

    # region ====== Helpers ======

    async def _handle_suggestion(
        self,
        interaction: Interaction,
        suggestion_text: str,
        anonymous: bool,
        config: SuggestionConfig,
        command_name: str,
    ) -> None:
        """Handle a suggestion command.

        Args:
            interaction: The Discord interaction context.
            suggestion_text: The raw suggestion text.
            anonymous: Whether the suggestion is anonymous or not.
            config: The suggestion configuration.
            command_name: The command name for logging (e.g., "suggest.formation").
        """
        logger.info(
            f"[{command_name}]: user={interaction.user if not anonymous else 'Anonymous'!r} suggestion_text={suggestion_text!r}"
        )

        cleaned_text = suggestion_text.strip()
        if not cleaned_text:
            await interaction.followup.send("Le texte de la suggestion ne peut pas être vide.", ephemeral=True)
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
            f"{interaction.user.id if not anonymous else 'Anonymous'} made a suggestion via {command_name}."
        )
        await interaction.response.send_message(config.success_message, ephemeral=True)

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
            True if at least one recipient received the suggestion, False otherwise.
        """
        responsibles: set[Member] = get_members_by_role(logger, guild, role=config.role_name) if config.role_name else set()
        channel = get(guild.text_channels, name=config.channel_name) if config.channel_name else None

        if not responsibles and not channel:
            logger.error(
                f"Guild {guild.id} has no {config.role_name} responsibles and no {config.channel_name} channel configured."
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


class RecipientSelect(ui.Select):
    def __init__(self, view: InitialView) -> None:
        options = [SelectOption(label=label, value=key) for key, (label, _, _) in SUGGESTION_OPTIONS.items()]
        super().__init__(placeholder="Choisir le destinataire...", min_values=1, max_values=1, options=options)
        self.view_ref = view

    async def callback(self, interaction: Interaction) -> None:
        self.view_ref.selected_recipient = self.values[0]
        await interaction.response.defer(ephemeral=True)


class AnonymitySelect(ui.Select):
    def __init__(self, view: InitialView) -> None:
        options = [SelectOption(label="Non (avec nom)", value="no"), SelectOption(label="Oui (anonyme)", value="yes")]
        super().__init__(placeholder="Souhaitez-vous rester anonyme ?", min_values=1, max_values=1, options=options)
        self.view_ref = view

    async def callback(self, interaction: Interaction) -> None:
        self.view_ref.anonymous = self.values[0] == "yes"
        await interaction.response.defer(ephemeral=True)


class SuggestionModal(ui.Modal, title="Envoyer une suggestion"):
    suggestion_input: ui.TextInput = ui.TextInput(
        label="Votre suggestion",
        style=TextStyle.long,
        placeholder="Décrivez votre suggestion...",
        required=True,
        max_length=2000,
    )

    def __init__(self, cog: SuggestionManagement, recipient_key: str, anonymous_flag: bool, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.cog = cog
        self.recipient_key = recipient_key
        self.anonymous_flag = anonymous_flag

    async def on_submit(self, interaction: Interaction) -> None:
        label, config, command_name = SUGGESTION_OPTIONS[self.recipient_key]
        await self.cog._handle_suggestion(interaction, self.suggestion_input.value, self.anonymous_flag, config, command_name)


class OpenModalButton(ui.Button):
    def __init__(self, view: InitialView, cog: SuggestionManagement) -> None:
        super().__init__(label="Rédiger la suggestion", style=ButtonStyle.primary)
        self.view_ref = view
        self.cog = cog

    async def callback(self, interaction: Interaction) -> None:
        recipient = getattr(self.view_ref, "selected_recipient", None)
        if recipient is None:
            await interaction.response.send_message("Veuillez d'abord choisir le destinataire.", ephemeral=True)
            return

        modal = SuggestionModal(self.cog, recipient, getattr(self.view_ref, "anonymous", False))
        await interaction.response.send_modal(modal)


class InitialView(ui.View):
    def __init__(self, cog: SuggestionManagement) -> None:
        super().__init__(timeout=300)
        self.selected_recipient: str | None = None
        self.anonymous: bool = False
        self.add_item(RecipientSelect(self))
        self.add_item(AnonymitySelect(self))
        self.add_item(OpenModalButton(self, cog))
