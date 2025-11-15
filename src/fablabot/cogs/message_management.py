"""Message management commands for Discord Bot. Provides slash commands for sending and managing messages."""

from __future__ import annotations

import json
import logging
from pathlib import Path
import random
from typing import Any, Literal
from warnings import deprecated

from discord import (
    ButtonStyle,
    CategoryChannel,
    DMChannel,
    ForumChannel,
    GroupChannel,
    Guild,
    HTTPException,
    Interaction,
    Member,
    Message,
    RawReactionActionEvent,
    StageChannel,
    TextChannel,
    Thread,
    VoiceChannel,
    app_commands,
    ui,
)
from discord.ext import commands
from discord.utils import get
from emoji import EMOJI_DATA

from fablabot.cogs.helpers import (
    ADMIN_ROLES,
    DISCORD_EMOJI_RE,
    ErrorMessages,
    RoleNames,
    check_has_role,
    format_member_mention,
    get_members_by_role,
    is_in_allowed_channel,
    log_request,
    send_dm_to_member,
)
from fablabot.cogs.helpers.message import (
    EASTER_EGGS,
    MessageDraft,
    ReactionAction,
)

logger = logging.getLogger(__name__)

MESSAGES_STATE_FILE = Path("data/messages_state.json")


class MessageManagement(commands.Cog):
    """Cog to register message management commands.

    Commands:
        - /message help: Display help for message management commands.
        - /message clear: Clear the current text channel of its last messages.
        - /message dm: Send a direct message to multiple users.

    Listeners:
        - on_message: Easter egg listener for specific message content.

    Attributes:
        message_group (app_commands.Group): Command group for message management commands.
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
                await message.channel.send(content=egg.response, reference=message)
                logger.info(f"Easter egg triggered by {message.author} in {message.channel}: {egg.keywords}")

    # endregion Listeners


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
