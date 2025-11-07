"""Suggestion management commands for Discord Bot. Provides slash commands for sending suggestions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import logging
from typing import ClassVar
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

from fablabot.cogs.helpers import (
    PARIS_TZ,
    RoleNames,
    can_dm_user,
    get_members_by_role,
)

logger = logging.getLogger(__name__)

ANONYMOUS_ICON_URL = "https://e7.pngegg.com/pngimages/84/165/png-clipart-united-states-avatar-organization-information-user-avatar-service-computer-wallpaper-thumbnail.png"


@dataclass
class SuggestionConfig:
    """Configuration for a suggestion type.

    Attributes:
        embed_title (str): The title of the suggestion embed.
        role_name (str | None): The role name to get responsible members.
        channel_name (str | None): The channel name to send the suggestion to.
        embed_color (int): The color of the embed. Defaults to 0x00AAFF.
        success_message (str): The success message to send to the user.
        error_message (str): The error message when no recipients are configured.
    """

    embed_title: str
    """The title of the suggestion embed."""
    role_name: str | None
    """The role name to get responsible members."""
    channel_name: str | None
    """The channel name to send the suggestion to."""
    success_message: str
    """The success message to send to the user."""
    error_message: str
    """The error message when no recipients are configured."""
    embed_color: int = 0x00AAFF


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

    CODIR_SUGGESTION_CONFIG = SuggestionConfig(
        embed_title="Nouvelle suggestion pour le CoDir",
        role_name=None,
        channel_name="codir-general",
        success_message="Suggestion envoyée au CoDir. Merci !",
        error_message="Aucun salon 'codir-general' n'est configuré pour recevoir les suggestions.",
    )

    BUREAU_SUGGESTION_CONFIG = SuggestionConfig(
        embed_title="Nouvelle suggestion pour le Bureau",
        role_name=None,
        channel_name="bureau-general",
        success_message="Suggestion envoyée au Bureau. Merci !",
        error_message="Aucun salon 'bureau-general' n'est configuré pour recevoir les suggestions.",
    )

    COMMUNICATION_SUGGESTION_CONFIG = SuggestionConfig(
        embed_title="Nouvelle suggestion pour le Pôle Communication",
        role_name=RoleNames.COMMUNICATION_MANAGER,
        channel_name="pole-communication",
        success_message="Suggestion envoyée au Pôle Communication. Merci !",
        error_message="Aucun·e Respo Communication et aucun salon 'pole-communication'"
        " ne sont configurés pour recevoir les suggestions.",
    )

    EVENT_SUGGESTION_CONFIG = SuggestionConfig(
        embed_title="Nouvelle suggestion pour le Pôle Événements",
        role_name=RoleNames.EVENTS_MANAGER,
        channel_name="pole-event",
        success_message="Suggestion envoyée au Pôle Events. Merci !",
        error_message="Aucun·e Respo Events et aucun salon 'pole-events' ne sont configurés pour recevoir les suggestions.",
    )

    TRAINING_SUGGESTION_CONFIG = SuggestionConfig(
        embed_title="Nouvelle suggestion de formation",
        role_name=RoleNames.TRAININGS_MANAGER,
        channel_name="pole-formation",
        success_message="Suggestion envoyée au Pôle Formation. Merci !",
        error_message="Aucun·e Respo Formation et aucun salon 'pole-formation'"
        " ne sont configurés pour recevoir les suggestions.",
    )

    IT_SUGGESTION_CONFIG = SuggestionConfig(
        embed_title="Nouvelle suggestion pour le Pôle Numérique",
        role_name=RoleNames.DIGITAL_MANAGER,
        channel_name="pole-numerique",
        success_message="Suggestion envoyée au Pôle Numérique. Merci !",
        error_message="Aucun·e Respo Numérique et aucun salon 'pole-numerique'"
        " ne sont configurés pour recevoir les suggestions.",
    )

    PARTERSHIPS_SUGGESTION_CONFIG = SuggestionConfig(
        embed_title="Nouvelle suggestion pour le Pôle Partenariat",
        role_name=RoleNames.PARTNERSHIPS_MANAGER,
        channel_name="pole-partenariat",
        success_message="Suggestion envoyée au Pôle Partenariat. Merci !",
        error_message="Aucun·e Respo Partenariat et aucun salon 'pole-partenariat'"
        " ne sont configurés pour recevoir les suggestions.",
    )

    PROJECTS_SUGGESTION_CONFIG = SuggestionConfig(
        embed_title="Nouvelle suggestion pour le Pôle Projets",
        role_name=RoleNames.PROJECTS_MANAGER,
        channel_name=None,
        success_message="Suggestion envoyée au Pôle Projets. Merci !",
        error_message="Aucun·e Respo Projets n'est configuré·e pour recevoir les suggestions.",
    )

    _SUGGESTION_OPTIONS: ClassVar[dict[str, tuple[str, SuggestionConfig, str]]] = {
        "codir": ("CoDir", CODIR_SUGGESTION_CONFIG, "suggest.to_codir"),
        "bureau": ("Bureau", BUREAU_SUGGESTION_CONFIG, "suggest.to_bureau"),
        "communication": ("Pôle Communication", COMMUNICATION_SUGGESTION_CONFIG, "suggest.to_communication"),
        "events": ("Pôle Event", EVENT_SUGGESTION_CONFIG, "suggest.to_event"),
        "formation": ("Pôle Formation", TRAINING_SUGGESTION_CONFIG, "suggest.formation"),
        "numerique": ("Pôle Numérique", IT_SUGGESTION_CONFIG, "suggest.it_feature"),
        "partenariat": ("Pôle Partenariat", PARTERSHIPS_SUGGESTION_CONFIG, "suggest.to_partenariat"),
        "projets": ("Pôle Projet", PROJECTS_SUGGESTION_CONFIG, "suggest.to_projet"),
    }

    async def _send_initial_suggest_view(self, interaction: Interaction) -> None:
        """Send an ephemeral view where the user picks recipient and anonymous flag.

        Args:
            interaction (Interaction): The Discord interaction context.
        """

        class RecipientSelect(ui.Select):
            def __init__(self, view: InitialView) -> None:
                options = [SelectOption(label=label, value=key) for key, (label, _, _) in self_view._SUGGESTION_OPTIONS.items()]
                super().__init__(placeholder="Choisir le destinataire...", min_values=1, max_values=1, options=options)
                self.view_ref = view

            async def callback(self, select_interaction: Interaction) -> None:
                # store chosen recipient on the view
                self.view_ref.selected_recipient = self.values[0]
                await select_interaction.response.defer(ephemeral=True)

        class AnonymitySelect(ui.Select):
            def __init__(self, view: InitialView) -> None:
                options = [SelectOption(label="Non (avec nom)", value="no"), SelectOption(label="Oui (anonyme)", value="yes")]
                super().__init__(placeholder="Souhaitez-vous rester anonyme ?", min_values=1, max_values=1, options=options)
                self.view_ref = view

            async def callback(self, select_interaction: Interaction) -> None:
                self.view_ref.anonymous = self.values[0] == "yes"
                await select_interaction.response.defer(ephemeral=True)

        class OpenModalButton(ui.Button):
            def __init__(self, view: InitialView, cog: SuggestionManagement) -> None:
                super().__init__(label="Rédiger la suggestion", style=ButtonStyle.primary)
                self.view_ref = view
                self.cog = cog

            async def callback(self, button_interaction: Interaction) -> None:
                recipient = getattr(self.view_ref, "selected_recipient", None)
                if recipient is None:
                    await button_interaction.response.send_message("Veuillez d'abord choisir le destinataire.", ephemeral=True)
                    return

                class SuggestionModal(ui.Modal, title="Envoyer une suggestion"):
                    suggestion_input: ui.TextInput = ui.TextInput(
                        label="Votre suggestion",
                        style=TextStyle.long,
                        placeholder="Décrivez votre suggestion...",
                        required=True,
                        max_length=2000,
                    )

                    def __init__(
                        self, cog: SuggestionManagement, recipient_key: str, anonymous_flag: bool, *args, **kwargs
                    ) -> None:
                        super().__init__(*args, **kwargs)
                        self.cog = cog
                        self.recipient_key = recipient_key
                        self.anonymous_flag = anonymous_flag

                    async def on_submit(self, modal_interaction: Interaction) -> None:
                        # call existing handler using the collected inputs
                        label, config, command_name = self.cog._SUGGESTION_OPTIONS[self.recipient_key]
                        await self.cog._handle_suggestion(
                            modal_interaction, self.suggestion_input.value, self.anonymous_flag, config, command_name
                        )

                modal = SuggestionModal(self.cog, recipient, getattr(self.view_ref, "anonymous", False))
                await button_interaction.response.send_modal(modal)

        self_view = self

        class InitialView(ui.View):
            def __init__(self, cog: SuggestionManagement) -> None:
                super().__init__(timeout=300)
                self.selected_recipient: str | None = None
                self.anonymous: bool = False
                self.add_item(RecipientSelect(self))
                self.add_item(AnonymitySelect(self))
                self.add_item(OpenModalButton(self, cog))

        view = InitialView(self)
        await interaction.response.send_message(
            "Veuillez choisir le destinataire et si vous souhaitez rester anonyme, puis cliquez sur 'Rédiger la suggestion'.",
            view=view,
            ephemeral=True,
        )

    @app_commands.command(name="suggest", description="Envoyer une suggestion (choisir destinataire, texte, anonymat)")
    async def suggest(self, interaction: Interaction) -> None:
        """Single command entrypoint for sending suggestions via a small interactive flow.

        The flow collects the recipient and anonymity via components, then opens a modal to input the suggestion text.
        """
        await self._send_initial_suggest_view(interaction)

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
            if await can_dm_user(responsible):
                try:
                    await responsible.send(embed=embed)
                except Exception:
                    logger.exception(
                        f"Failed to send suggestion from user {user_id or 'Anonymous'} to responsible {responsible.id}."
                    )

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
