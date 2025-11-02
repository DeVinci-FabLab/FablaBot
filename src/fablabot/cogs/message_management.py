"""Message management commands for Discord Bot. Provides slash commands for sending and managing messages."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import logging
import random
from typing import Any
from warnings import deprecated

from discord import (
    ButtonStyle,
    CategoryChannel,
    DMChannel,
    Embed,
    ForumChannel,
    GroupChannel,
    Guild,
    HTTPException,
    Interaction,
    Member,
    Message,
    StageChannel,
    TextChannel,
    Thread,
    VoiceChannel,
    app_commands,
    ui,
)
from discord.ext import commands
from discord.utils import get

from fablabot.cogs.helpers import (
    EASTER_EGGS,
    PARIS_TZ,
    ErrorMessages,
    RoleNames,
    can_dm_user,
    format_member_mention,
    get_members_by_role,
    is_in_allowed_channel,
    log_request,
    send_dm_to_member,
)

logger = logging.getLogger(__name__)


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


class MessageManagement(commands.Cog):
    """Cog to register message management commands.

    Commands:
        - /message help: Display help for message management commands.
        - /message clear: Clear the current text channel of its last messages.
        - /message dm: Send a direct message to multiple users.
        - /suggest help: Display help for feature suggestion commands.
        - /suggest fm: Suggest a new formation.
        - /suggest it_feature: Suggest a new IT feature.
        - /suggest to_bureau: Suggest an improvement for the Bureau.

    Listeners:
        - on_message: Easter egg listener for specific message content.

    Attributes:
        message_group (app_commands.Group): Command group for message management commands.
        suggest_group (app_commands.Group): Command group for feature suggestion commands.
        FORMATION_SUGGESTION_CONFIG (SuggestionConfig): Configuration for formation suggestions.
        IT_SUGGESTION_CONFIG (SuggestionConfig): Configuration for IT feature suggestions.
        BUREAU_SUGGESTION_CONFIG (SuggestionConfig): Configuration for Bureau suggestions.
    """

    def __init__(self, bot: commands.Bot) -> None:
        """Initialize the cog and register its command groups.

        Args:
            bot (commands.Bot): The bot instance.
        """
        self.bot = bot
        logger.info("MessageManagement initialized")

    # region ====== Message Slash Commands Group ======

    message_group = app_commands.Group(name="message", description="Gestion des messages")

    @message_group.command(
        name="help",
        description="Affiche l'aide pour les commandes de gestion des messages.",
    )
    @app_commands.describe(show="Afficher l'aide publiquement ou non")
    async def message_help(self, interaction: Interaction, show: bool = False) -> None:
        """Display help for message management commands.

        Args:
            interaction (Interaction): The Discord interaction context.
            show (bool): Whether to show the help publicly or not.
        """
        help_text = (
            "**Commandes de gestion des messages :**\n"
            "- `/message clear [messages]`: Nettoie le salon actuel de ses derniers messages.\n"
            "- `/message dm`: Envoie un message privé à plusieurs utilisateurs via un sélecteur.\n"
            "- `/message help [show]`: Affiche cette aide. Par défaut, elle est affichée secrètement.\n"
            "\n"
            "Assurez-vous d'avoir les permissions nécessaires pour utiliser ces commandes."
        )
        await interaction.response.send_message(help_text, ephemeral=not show)

    @message_group.command(name="clear", description="Nettoie le salon actuel de ses derniers messages.")
    @app_commands.describe(messages="Le nombre de messages à supprimer (par défaut 5)")
    async def message_clear(self, interaction: Interaction, messages: app_commands.Range[int, 1, 50] = 5) -> None:
        """Clears the current channel of its last messages.

        Args:
            interaction (Interaction): The Discord interaction context.
            messages (app_commands.Range[int, 1, 50], optional): The number of messages to purge. Defaults to 5.
        """
        log_request(logger, "message.clear", interaction, messages=messages)
        assert not isinstance(
            interaction.channel,
            ForumChannel | CategoryChannel | DMChannel | GroupChannel | None,
        )
        if not interaction.permissions.manage_messages:
            logger.warning(f"Insufficient permissions for manage_messages: {interaction.user}")
            await interaction.response.send_message(ErrorMessages.NO_PERMISSION_MANAGE_MESSAGES, ephemeral=True)
            return
        if interaction.channel.name.endswith("_bot"):
            logger.warning(f"Attempt to clear {interaction.channel.name} channel")
            await interaction.response.send_message(
                f"Vous ne pouvez pas nettoyer le salon {interaction.channel.mention}."
                f" Veuillez contacter le pôle numérique si nécessaire.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message("Nettoyage en cours...", ephemeral=True)
        try:
            deleted = await interaction.channel.purge(
                limit=messages,
                reason=f"With clear command by {interaction.user}",
            )
        except HTTPException:
            logger.exception(f"HTTP error while purging {messages} messages in {interaction.channel}")
            await interaction.edit_original_response(content=ErrorMessages.CHANNEL_CLEAR_FAILED)
            return
        logger.info(f"Deleted {len(deleted)} messages in channel {interaction.channel.name}")
        await interaction.edit_original_response(content=f"{len(deleted)} messages supprimés avec succès !")

    @message_group.command(
        name="dm",
        description="Envoie un message privé à plusieurs utilisateurs via un sélecteur.",
    )
    @app_commands.describe(message="Le message à envoyer en MP.")
    async def message_dm(self, interaction: Interaction, message: str) -> None:
        """Send a direct message to multiple users.

        Args:
            interaction (Interaction): The Discord interaction context.
            message (str): The message content to send.
        """
        log_request(logger, "message.dm", interaction, message=message)
        if not await is_in_allowed_channel(logger, interaction):
            return

        assert isinstance(interaction.user, Member)

        role_names = {role.name for role in interaction.user.roles}
        if RoleNames.BUREAU not in role_names:
            logger.warning(f"Unauthorized dm by {interaction.user}")
            await interaction.response.send_message("Permissions insuffisantes.", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)
        followup_mes = await interaction.followup.send("Sélection des membres en cours...", wait=True)

        message += (
            f"\n\n*Ce message vous a été envoyé par un membre du Bureau du Fablab. Merci de ne pas y répondre directement.*"
            f"\nPour plus d'informations, contactez <@{interaction.user.id}>."
        )

        view = BulkDMView(interaction.user, followup_mes.id, message)
        await interaction.followup.send(
            (
                "Selectionnez les membres a qui envoyer le message puis cliquez sur **Confirmer**.\n\n"
                f"Message à envoyer :\n>>> {message}"
            ),
            view=view,
            ephemeral=True,
        )

    # endregion Message Slash Commands Group

    # region ====== Suggest Slash Commands Group ======

    suggest_group = app_commands.Group(name="suggest_to", description="Suggestions de fonctionnalités")

    BUREAU_SUGGESTION_CONFIG = SuggestionConfig(
        embed_title="Nouvelle suggestion pour le Bureau",
        role_name=RoleNames.BUREAU,
        channel_name="bureau-general",
        success_message="Suggestion envoyée au Bureau. Merci !",
        error_message="Aucun salon 'bureau-general' n'est configuré pour recevoir les suggestions.",
    )

    COMMUNICATION_SUGGESTION_CONFIG = SuggestionConfig(
        embed_title="Nouvelle suggestion pour le Pôle Communication",
        role_name=RoleNames.RESPO_COMMUNICATION,
        channel_name="pole-communication",
        success_message="Suggestion envoyée au Pôle Communication. Merci !",
        error_message="Aucun·e Respo Communication et aucun salon 'pole-communication' ne sont configurés pour recevoir les suggestions.",
    )

    EVENTS_SUGGESTION_CONFIG = SuggestionConfig(
        embed_title="Nouvelle suggestion pour le Pôle Événements",
        role_name=RoleNames.RESPO_EVENTS,
        channel_name="pole-events",
        success_message="Suggestion envoyée au Pôle Événements. Merci !",
        error_message="Aucun·e Respo Events et aucun salon 'pole-events' ne sont configurés pour recevoir les suggestions.",
    )

    FORMATION_SUGGESTION_CONFIG = SuggestionConfig(
        embed_title="Nouvelle suggestion de formation",
        role_name=RoleNames.RESPO_FORMATIONS,
        channel_name="pole-formations",
        success_message="Suggestion envoyée au pôle formations. Merci !",
        error_message="Aucun·e respo formation et aucun salon 'pole-formations' ne sont configurés pour recevoir les suggestions.",
    )

    PARTENARIATS_SUGGESTION_CONFIG = SuggestionConfig(
        embed_title="Nouvelle suggestion pour le Pôle Partenariats",
        role_name=RoleNames.RESPO_PARTENARIATS,
        channel_name="pole-partenariats",
        success_message="Suggestion envoyée au Pôle Partenariats. Merci !",
        error_message="Aucun·e Respo Partenariats et aucun salon 'pole-partenariats' ne sont configurés pour recevoir les suggestions.",
    )

    IT_SUGGESTION_CONFIG = SuggestionConfig(
        embed_title="Nouvelle suggestion pour le Pôle Numérique",
        role_name=RoleNames.RESPO_NUMERIQUE,
        channel_name="pole-numerique",
        success_message="Suggestion envoyée au pôle numérique. Merci !",
        error_message="Aucun·e respo numérique et aucun salon 'pole-numerique' ne sont configurés pour recevoir les suggestions.",
    )

    @app_commands.describe(show="Afficher l'aide publiquement ou non")
    async def suggest_help(self, interaction: Interaction, show: bool = False) -> None:
        """Display help for feature suggestion commands.

        Args:
            interaction (Interaction): The Discord interaction context.
            show (bool): Whether to show the help publicly or not.
        """
        help_text = (
            "**Commandes de suggestions de fonctionnalités :**\n"
            "- `/suggest_to bureau <improvement> [anonyme]` : Suggérer une amélioration pour le Bureau.\n"
            "- `/suggest_to pole_communication <suggestion> [anonyme]` : Suggérer quelque chose au pôle communication.\n"
            "- `/suggest_to pole_events <suggestion> [anonyme]` : Suggérer quelque chose au pôle événements.\n"
            "- `/suggest_to pole_formation <formation> [anonyme]` : Demander une formation.\n"
            "- `/suggest_to pole_numerique <suggestion> [anonyme]` : Suggérer une nouvelle fonctionnalité IT (Pour le bot discord, un site, etc.).\n"
            "- `/suggest_to pole_partenariats <suggestion> [anonyme]` : Suggérer quelque chose au pôle partenariats.\n"
            "- `/suggest_to help [show]`: Affiche cette aide. Par défaut, elle est affichée secrètement.\n"
            "\n"
            "N'hésitez pas à suggérer des idées pour qu'on puisse s'améliorer !"
        )
        await interaction.response.send_message(help_text, ephemeral=not show)

    @suggest_group.command(name="bureau", description="Suggérer quelque chose au bureau.")
    @app_commands.describe(suggestion="Détails de la suggestion")
    async def suggest_to_bureau(self, interaction: Interaction, suggestion: str) -> None:
        """Suggest something to the bureau.

        Args:
            interaction (Interaction): The Discord interaction context.
            suggestion (str): The details of the suggested improvement.
        """
        await self._handle_suggestion(interaction, suggestion, self.BUREAU_SUGGESTION_CONFIG, "suggest.to_bureau")

    @suggest_group.command(name="pole_comm", description="Suggérer quelque chose au pôle communication.")
    @app_commands.describe(suggestion="Détails de la suggestion")
    async def suggest_to_communication_pole(self, interaction: Interaction, suggestion: str) -> None:
        """Suggest something to the communication pole.

        Args:
            interaction (Interaction): The Discord interaction context.
            suggestion (str): The details of the suggested improvement.
        """
        await self._handle_suggestion(interaction, suggestion, self.COMMUNICATION_SUGGESTION_CONFIG, "suggest.to_communication")

    @suggest_group.command(name="pole_events", description="Suggérer quelque chose au pôle events.")
    @app_commands.describe(suggestion="Détails de la suggestion")
    async def suggest_to_events_pole(self, interaction: Interaction, suggestion: str) -> None:
        """Suggest something to the events pole.

        Args:
            interaction (Interaction): The Discord interaction context.
            suggestion (str): The details of the suggested improvement.
        """
        await self._handle_suggestion(interaction, suggestion, self.EVENTS_SUGGESTION_CONFIG, "suggest.to_events")

    @suggest_group.command(name="pole_formation", description="Suggérer une formation au pôle formations.")
    @app_commands.describe(formation="Détails de la formation demandée")
    async def suggest_to_pole_formation(self, interaction: Interaction, formation: str) -> None:
        """Suggest a formation to the formations pole.

        Args:
            interaction (Interaction): The Discord interaction context.
            formation (str): The details of the suggested formation.
        """
        await self._handle_suggestion(interaction, formation, self.FORMATION_SUGGESTION_CONFIG, "suggest.formation")

    @suggest_group.command(name="pole_partenariats", description="Suggérer quelque chose au pôle partenariats.")
    @app_commands.describe(suggestion="Détails de la suggestion")
    async def suggest_to_pole_partenariats(self, interaction: Interaction, suggestion: str) -> None:
        """Suggest something to the partnerships pole.

        Args:
            interaction (Interaction): The Discord interaction context.
            suggestion (str): The details of the suggested improvement.
        """
        await self._handle_suggestion(interaction, suggestion, self.PARTENARIATS_SUGGESTION_CONFIG, "suggest.to_partenariats")

    @suggest_group.command(name="pole_numerique", description="Suggérer une fonctionnalité IT au pôle numérique.")
    @app_commands.describe(it_feature="Détails de la suggestion")
    async def suggest_to_pole_numerique(self, interaction: Interaction, it_feature: str) -> None:
        """Suggest a new IT feature to the IT pole.

        Args:
            interaction (Interaction): The Discord interaction context.
            it_feature (str): The details of the suggested IT feature.
        """
        await self._handle_suggestion(interaction, it_feature, self.IT_SUGGESTION_CONFIG, "suggest.it_feature")

    # endregion Suggest Slash Commands Group

    # region ====== Listeners ======

    @commands.Cog.listener()
    async def on_message(self, message: Message) -> None:
        """Easter egg listener for specific message content.

        Args:
            message (discord.Message): The message that was sent.
        """
        if message.author.bot:
            return
        if not isinstance(message.channel, TextChannel | VoiceChannel | StageChannel | Thread):
            return
        if message.channel.category and message.channel.category.name in {"Bureau", "Annonces", "Chargés"}:
            return

        for egg in EASTER_EGGS:
            if any(keyword in message.content.lower() for keyword in egg.keywords) and random.random() < egg.probability:
                await message.channel.send(content=egg.response)
                logger.info(f"Easter egg triggered by {message.author} in {message.channel}: {egg.keywords}")

    # endregion Listeners

    # region ====== Helpers ======

    async def _handle_suggestion(
        self,
        interaction: Interaction,
        suggestion_text: str,
        config: SuggestionConfig,
        command_name: str,
    ) -> None:
        """Handle a suggestion command.

        Args:
            interaction: The Discord interaction context.
            suggestion_text: The raw suggestion text.
            config: The suggestion configuration.
            command_name: The command name for logging (e.g., "suggest.formation").
        """
        log_request(logger, command_name, interaction, suggestion=suggestion_text)
        await interaction.response.defer(thinking=True)

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
        embed.set_author(name=author.display_name, icon_url=author.display_avatar.url)
        embed.add_field(name="Utilisateur·ice", value=author.mention, inline=False)

        success = await self._send_suggestion(interaction.guild, embed, config, author.id)

        if not success:
            await interaction.followup.send(config.error_message, ephemeral=True)
            return

        logger.info(
            f"Guild {interaction.guild.id} user {interaction.user.id or 'Anonymous'} made a suggestion via {command_name}."
        )
        await interaction.followup.send(config.success_message, ephemeral=True)

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
    """Setup function to load the MessageManagement cog.

    Args:
        bot (commands.Bot): The bot instance.
    """
    await bot.add_cog(MessageManagement(bot))


# region ====== UI View ======


class BulkDMView(ui.View):
    """View for bulk direct message sending."""

    def __init__(
        self,
        sender: Member,
        followup_id: int,
        message: str,
    ) -> None:
        """Initialize the view for bulk direct messages.

        Args:
            sender (Member): The member initiating the message sending.
            followup_id (int): The ID of the follow-up message to edit with results.
            message (str): The message to send to the selected members.
        """
        super().__init__()
        self.sender = sender
        self.followup_id = followup_id
        self.message = message

        async def _on_select(interaction: Interaction) -> None:
            if not interaction.response.is_done():
                await interaction.response.defer()

        self.select: ui.UserSelect[Any] = ui.UserSelect(
            placeholder="Sélectionne les membres…",
            min_values=1,
            max_values=25,
        )
        self.select.callback = _on_select  # type: ignore

        self.confirm_button: ui.Button[Any] = ui.Button(label="Confirmer", style=ButtonStyle.primary)
        self.confirm_button.callback = self.confirm  # type: ignore

        self.add_item(self.select)
        self.add_item(self.confirm_button)

    async def confirm(self, interaction: Interaction) -> None:
        """Confirm the direct message sending.

        Args:
            interaction (Interaction): The Discord interaction triggered by the confirm button.
        """
        assert interaction.guild is not None
        members: list[Member] = [m for m in self.select.values if isinstance(m, Member)]
        if not members:
            await interaction.response.send_message("Aucun membre sélectionné.", ephemeral=True)
            return

        for child in self.children:
            if isinstance(child, ui.Button | ui.UserSelect):
                child.disabled = True
        await interaction.response.edit_message(view=self)

        delivered: list[Member] = []
        failed: list[Member] = []

        for member in members:
            if await send_dm_to_member(logger, interaction.guild, member, self.message, "Bulk"):
                delivered.append(member)
            else:
                failed.append(member)

        logger.info(f"Bulk DM by {self.sender} delivered to {delivered} with failures {failed}")

        lines: list[str] = ["Envoi des messages terminé."]
        if delivered:
            lines.append("Succès : " + ", ".join(format_member_mention(member) for member in delivered))
        if failed:
            lines.append("Échecs : " + ", ".join(format_member_mention(member) for member in failed))
        lines.append("Contenu envoyé :")
        lines.append(f">>> {self.message}")

        await interaction.followup.edit_message(self.followup_id, content="\n".join(lines))
        await interaction.delete_original_response()


# endregion UI View
