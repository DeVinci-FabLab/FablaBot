"""Message management commands for Discord Bot. Provides slash commands for sending and managing messages."""

from __future__ import annotations

import json
import logging
from pathlib import Path
import random
from typing import Any, Literal
from warnings import deprecated

from discord import (
    CategoryChannel,
    DMChannel,
    ForumChannel,
    GroupChannel,
    HTTPException,
    Interaction,
    Member,
    Message,
    StageChannel,
    TextChannel,
    Thread,
    VoiceChannel,
    app_commands,
)
from discord.ext import commands

from fablabot.helpers.constants import ErrorMessages, RoleNames
from fablabot.helpers.utils import (
    check_has_role,
    is_in_allowed_channel,
    is_valid_emoji,
    log_request,
)
from fablabot.models.message import EASTER_EGGS, MessageDraft
from fablabot.ui import mui

logger = logging.getLogger(__name__)

MESSAGES_STATE_FILE = Path("data/messages_state.json")


class MessageManagement(commands.Cog):
    """Cog to register message management commands.

    Commands:
        - /message help: Display help for message management commands.
        - /message clear: Clear the current text channel of its last messages.
        - /message dm: Send a direct message to multiple users.
        - /message create: Create a new message draft.
        - /message add_reaction: Add a reaction-based action to the draft.
        - /message preview: Preview the current draft.
        - /message publish: Publish the draft message.
        - /message list: List all published messages with reactions.

    Listeners:
        - on_message: Easter egg listener for specific message content.
        - on_raw_reaction_add: Handle reaction-based automated actions.

    Attributes:
        message_group (app_commands.Group): Command group for message management commands.
        state (dict): State management for drafts and published messages.
    """

    def __init__(self, bot: commands.Bot) -> None:
        """Initialize the cog and register its command groups.

        Args:
            bot (commands.Bot): The bot instance.
        """
        self.bot = bot
        self.state: dict[str, dict[str, Any]] = self._load_state()
        logger.info("MessageManagement initialized")

    # region ====== Message Slash Commands Group ======

    msg_group = app_commands.Group(name="msg", description="Gestion des messages")

    @msg_group.command(
        name="help",
        description="Affiche l'aide pour les commandes de gestion des messages.",
    )
    @app_commands.describe(show="Afficher l'aide publiquement ou non")
    async def msg_help(self, interaction: Interaction, *, show: bool = False) -> None:
        """Display help for message management commands.

        Args:
            interaction (Interaction): The Discord interaction context.
            show (bool): Whether to show the help publicly or not.
        """
        help_text = (
            "**Commandes de gestion des messages :**\n"
            "- `/msg clear [messages]`: Nettoie le salon actuel de ses derniers messages.\n"
            "- `/msg dm`: Envoie un message privé à plusieurs utilisateurs via un sélecteur.\n"
            "- `/msg start <content>`: Crée un nouveau brouillon de message.\n"
            "- `/msg link <emoji> <action_type> <message>`: Ajoute une action liée à une réaction.\n"
            "  • Types d'actions : `channel` (message dans un salon), `user_dm` (MP à un utilisateur), `role_dm` (MP aux membres d'un rôle)\n"
            "- `/msg preview`: Prévisualise le brouillon actuel.\n"
            "- `/msg publish <channel>`: Publie le brouillon dans un salon.\n"
            "- `/msg list`: Liste tous les messages publiés avec réactions automatiques.\n"
            "- `/msg help [show]`: Affiche cette aide. Par défaut, elle est affichée secrètement.\n"
            "\n"
            "Assurez-vous d'avoir les permissions nécessaires pour utiliser ces commandes."
        )
        await interaction.response.send_message(help_text, ephemeral=not show)

    @msg_group.command(name="clear", description="Nettoie le salon actuel de ses derniers messages.")
    @app_commands.describe(messages="Le nombre de messages à supprimer (par défaut 5)")
    async def msg_clear(self, interaction: Interaction, *, messages: app_commands.Range[int, 1, 50] = 5) -> None:
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

    @msg_group.command(
        name="dm",
        description="Envoie un message privé à plusieurs utilisateurs via un sélecteur.",
    )
    @app_commands.describe(message="Le message à envoyer en MP.")
    async def msg_dm(self, interaction: Interaction, *, message: str) -> None:
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

        view = mui.BulkDMView(interaction.user, followup_mes.id, message)
        await interaction.followup.send(
            (
                "Selectionnez les membres a qui envoyer le message puis cliquez sur **Confirmer**.\n\n"
                f"Message à envoyer :\n>>> {message}"
            ),
            view=view,
            ephemeral=True,
        )

    @msg_group.command(name="start", description="Crée un nouveau brouillon de message.")
    async def msg_start(self, interaction: Interaction) -> None:
        """Create a new message draft.

        Args:
            interaction (Interaction): The Discord interaction context.
        """
        log_request(logger, "message.create", interaction)
        if not await is_in_allowed_channel(logger, interaction):
            return

        if not await check_has_role(logger, interaction, {RoleNames.BUREAU}):
            return

        await interaction.response.send_modal(mui.StartMessageModal(self))

    @msg_group.command(
        name="link_reaction",
        description="Ajoute une action automatique liée à une réaction.",
    )
    @app_commands.describe(
        emoji="L'emoji qui déclenchera l'action",
        action_type="Type d'action : channel (message dans salon), user_dm (MP membre), role_dm (MP aux membres d'un rôle)",
    )
    async def msg_link_reaction(
        self,
        interaction: Interaction,
        *,
        emoji: str,
        action_type: Literal["channel", "user_dm", "role_dm"],
    ) -> None:
        """Add a reaction-based action to the draft.

        Args:
            interaction (Interaction): The Discord interaction context.
            emoji (str): The emoji that triggers the action.
            action_type (Literal["channel", "user_dm", "role_dm"]): The type of action to perform.
        """
        log_request(logger, "message.add_reaction", interaction, emoji=emoji, action_type=action_type)
        if not await is_in_allowed_channel(logger, interaction):
            return

        if not await check_has_role(logger, interaction, {RoleNames.BUREAU}):
            return

        assert interaction.guild is not None
        guild_id = str(interaction.guild.id)

        draft = self.get_draft(guild_id)
        if draft is None:
            await interaction.response.send_message(
                "Aucun brouillon trouvé. Utilisez `/message create` d'abord.",
                ephemeral=True,
            )
            return

        if not is_valid_emoji(emoji):
            await interaction.response.send_message(
                f"L'emoji `{emoji}` n'est pas valide. Utilisez un emoji Unicode ou Discord personnalisé.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(thinking=True, ephemeral=True)
        followup_mes = await interaction.followup.send("Configuration de la réaction en cours...", ephemeral=True, wait=True)

        view = mui.ReactionTargetView(self, emoji, action_type, followup_mes.id)
        await interaction.followup.send(view=view, ephemeral=True)

    # endregion Message Slash Commands Group

    # region ====== Listeners ======

    @commands.Cog.listener()
    async def on_message(self, msg: Message) -> None:
        """Easter egg listener for specific message content.

        Args:
            msg (discord.Message): The message that was sent.
        """
        if msg.author.bot:
            return
        if not isinstance(msg.channel, TextChannel | VoiceChannel | StageChannel | Thread):
            return
        if msg.channel.category and msg.channel.category.name in {"Bureau", "Annonces", "Chargés"}:
            return

        for egg in EASTER_EGGS:
            if any(keyword in msg.content.lower() for keyword in egg.keywords) and random.random() < egg.probability:
                await msg.channel.send(content=egg.response, reference=msg)
                logger.info(f"Easter egg triggered by {msg.author} in {msg.channel}: {egg.keywords}")

    # endregion Listeners

    # region ====== Helpers ======

    def _load_state(self) -> dict[str, dict[str, Any]]:
        """Load state from JSON file.

        Returns:
            dict[str, dict[str, Any]]: The loaded state.
        """
        if not MESSAGES_STATE_FILE.exists():
            return {}

        try:
            with MESSAGES_STATE_FILE.open("r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            logger.exception("Failed to load messages state")
            return {}

    def _save_state(self) -> None:
        """Save state to JSON file."""
        MESSAGES_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

        try:
            temp_path = MESSAGES_STATE_FILE.with_suffix(".tmp")
            with temp_path.open("w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2, ensure_ascii=False)
            temp_path.replace(MESSAGES_STATE_FILE)
        except Exception:
            logger.exception("Failed to save messages state")

    def get_draft(self, guild_id: str) -> MessageDraft | None:
        """Get the current draft for a guild.

        Args:
            guild_id (str): The guild ID.

        Returns:
            MessageDraft | None: The draft if it exists, None otherwise.
        """
        if guild_id not in self.state:
            return None
        draft_data = self.state[guild_id].get("draft")
        if not draft_data:
            return None
        return MessageDraft.from_dict(draft_data)

    def set_draft(self, guild_id: str, draft: MessageDraft) -> None:
        """Set the draft for a guild.

        Args:
            guild_id (str): The guild ID.
            draft (MessageDraft): The draft to set.
        """
        if guild_id not in self.state:
            self.state[guild_id] = {}
        self.state[guild_id]["draft"] = draft.to_dict()
        self._save_state()

    def _clear_draft(self, guild_id: str) -> None:
        """Clear the draft for a guild.

        Args:
            guild_id (str): The guild ID.
        """
        if guild_id in self.state and "draft" in self.state[guild_id]:
            del self.state[guild_id]["draft"]
            self._save_state()

    def _get_published_messages(self, guild_id: str) -> dict[str, Any]:
        """Get all published messages for a guild.

        Args:
            guild_id (str): The guild ID.

        Returns:
            dict[str, Any]: Dictionary of published messages keyed by message ID.
        """
        if guild_id not in self.state:
            return {}
        return self.state[guild_id].get("published", {})

    def _add_published_message(self, guild_id: str, message_id: int, channel_id: int, draft: MessageDraft) -> None:
        """Add a published message to the state.

        Args:
            guild_id (str): The guild ID.
            message_id (int): The message ID.
            channel_id (int): The channel ID.
            draft (MessageDraft): The draft that was published.
        """
        if guild_id not in self.state:
            self.state[guild_id] = {}
        if "published" not in self.state[guild_id]:
            self.state[guild_id]["published"] = {}

        self.state[guild_id]["published"][str(message_id)] = {
            "channel_id": channel_id,
            "content": draft.content,
            "reactions": [r.to_dict() for r in draft.reactions],
        }
        self._save_state()

    # endregion Helpers


@deprecated("Load the cog using `bot.add_cog()` instead.")
async def setup(bot: commands.Bot) -> None:
    """Setup function to load the MessageManagement cog.

    Args:
        bot (commands.Bot): The bot instance.
    """
    await bot.add_cog(MessageManagement(bot))
