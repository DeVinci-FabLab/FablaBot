"""Message management commands for Discord Bot. Provides slash commands for sending and managing messages."""

from __future__ import annotations

import contextlib
from datetime import datetime, timedelta
import logging
from pathlib import Path
import random
import re
from typing import Any, override
from warnings import deprecated

from discord import (
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
    Role,
    StageChannel,
    TextChannel,
    Thread,
    VoiceChannel,
    app_commands,
)
from discord.ext import commands

from fablabot.helpers.constants import ADMIN_ROLES, PARIS_TZ, ErrorMessages, RoleNames
from fablabot.helpers.reaction_log import ReactionLogManager
from fablabot.helpers.state_store import load_json_state, save_json_state
from fablabot.helpers.utils import (
    check_has_role,
    get_members_by_role,
    is_in_allowed_channel,
    is_valid_emoji,
    log_request,
    send_dm_to_member,
)
from fablabot.models.common import ReactionAction, ReactionEvent
from fablabot.models.message import EASTER_EGGS, MsgActionType, MsgReactionEvent, TrackedMessage
from fablabot.ui import mui

logger = logging.getLogger(__name__)

MESSAGES_STATE_FILE = Path("data/messages_state.json")
REACTION_LOG_RETENTION = timedelta(days=30)
ALLOWED_ROLES = ADMIN_ROLES | {RoleNames.BUREAU}


class MessageManagement(commands.Cog):
    """Cog handling message tracking, reaction automation, and quick messaging workflows.

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
        - on_raw_reaction_add: Handle reaction additions on tracked messages.

    Attributes:
        msg_group (app_commands.Group): Command group for message management.
    """

    def __init__(self, bot: commands.Bot) -> None:
        """Initialize the cog and register its command groups.

        Args:
            bot (commands.Bot): The bot instance.
        """
        self.bot = bot
        self.state: dict[str, dict[str, Any]] = self._load_state()
        self._reaction_logs = ReactionLogManager(
            self.state,
            save_state=self._save_state,
            retention=REACTION_LOG_RETENTION,
            logger=logger,
        )
        self._reaction_logs.purge_all()
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

    @msg_group.command(name="send", description="Envoyer un message et démarrer son suivi.")
    @app_commands.describe(
        channel="Salon cible",
        content="Contenu du message à envoyer",
        track="Activer immédiatement le suivi des réactions",
    )
    async def msg_send(
        self,
        interaction: Interaction,
        *,
        channel: TextChannel,
        content: str,
        track: bool = True,
    ) -> None:
        """Send a message and start tracking it.

        Args:
            interaction (Interaction): The Discord interaction context.
            channel (TextChannel): The target text channel.
            content (str): The content of the message to send.
            track (bool, optional): Whether to start tracking the message immediately. Defaults to True.
        """
        log_request(logger, "msg.send", interaction, channel=channel.name, track=track)
        if not await is_in_allowed_channel(logger, interaction):
            return

        if not await check_has_role(logger, interaction, ALLOWED_ROLES):
            return

        await interaction.response.defer(thinking=True)
        try:
            sent_msg = await channel.send(content)
        except Exception:
            logger.exception(f"Failed to send message in {channel}")
            await interaction.followup.send(ErrorMessages.HTTP_ERROR.format(operation="l'envoi du message"), ephemeral=True)
            return

        assert interaction.guild is not None
        if track:
            tracked = TrackedMessage(
                message_id=sent_msg.id,
                channel_id=channel.id,
                content=content,
                reactions=[],
                active=True,
                created_by=interaction.user.id,
                created_at_iso=datetime.now(PARIS_TZ).isoformat(),
            )
            self._set_tracked_message(interaction.guild.id, tracked)

        footer = f"{'Suivi activé.' if track else 'Suivi désactivé (active-le avec /msg follow).'}"

        await interaction.followup.send(f"Message envoyé dans {channel.mention} (ID `{sent_msg.id}`).\n{footer}")

    @msg_group.command(name="follow", description="Démarrer le suivi sur un message déjà envoyé.")
    @app_commands.describe(
        channel="Salon dans lequel se trouve le message",
        message="ID du message ou lien complet",
    )
    async def msg_follow(self, interaction: Interaction, *, channel: TextChannel, message: str) -> None:  # TODO: review
        log_request(logger, "msg.follow", interaction, channel=channel.name, message=message)
        if not await is_in_allowed_channel(logger, interaction):
            return
        if not await check_has_role(logger, interaction, ALLOWED_ROLES):
            return

        message_id = self._parse_message_id(message)
        if message_id is None:
            await interaction.response.send_message("ID ou lien de message invalide.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            fetched = await channel.fetch_message(message_id)
        except Exception:
            logger.exception(f"Failed to fetch message {message_id} in {channel}")
            await interaction.followup.send("Impossible de récupérer ce message.", ephemeral=True)
            return

        assert interaction.guild is not None
        tracked = TrackedMessage(
            message_id=fetched.id,
            channel_id=channel.id,
            content=fetched.content or "(contenu vide ou embed)",
            reactions=[],
            active=True,
            created_by=interaction.user.id,
            created_at_iso=datetime.now(PARIS_TZ).isoformat(),
        )
        self._set_tracked_message(interaction.guild.id, tracked)
        await interaction.followup.send(
            f"Suivi démarré sur le message `{fetched.id}` dans {channel.mention}.\n"
            "Ajoute des actions avec `/msg link_reaction`.",
            ephemeral=True,
        )

    @msg_group.command(name="link_reaction", description="Associer une réaction à une action automatisée.")
    @app_commands.describe(
        message="ID ou lien du message suivi",
        emoji="Emoji déclencheur",
        action_type="Action: channel (salon), user_dm (MP réacteur), role_dm (MP rôle)",
    )
    async def msg_link_reaction(
        self,
        interaction: Interaction,
        *,
        message: str,
        emoji: str,
        action_type: MsgActionType,
    ) -> None:
        """Link a reaction to an automated action.

        Args:
            interaction (Interaction): The Discord interaction context.
            message (str): The ID or link of the tracked message.
            emoji (str): The emoji that triggers the action.
            action_type (MsgActionType): The type of action to perform.
        """
        log_request(logger, "msg.link_reaction", interaction, message=message, emoji=emoji, action_type=action_type)
        if not await is_in_allowed_channel(logger, interaction):
            return

        if not await check_has_role(logger, interaction, ALLOWED_ROLES):
            return

        if not is_valid_emoji(emoji):
            await interaction.response.send_message(ErrorMessages.INVALID_EMOJI, ephemeral=True)
            return

        assert interaction.guild is not None

        message_id = self._parse_message_id(message)
        if message_id is None:
            await interaction.response.send_message("ID ou lien de message invalide.", ephemeral=True)
            return

        tracked = self._get_tracked_message(interaction.guild.id, message_id)
        if tracked is None:
            await interaction.response.send_message("Aucun suivi pour ce message. Lance `/msg follow` d'abord.", ephemeral=True)
            return
        if not tracked.active:
            await interaction.response.send_message(
                "Ce suivi est inactif. Relance `/msg follow` pour le réactiver.",
                ephemeral=True,
            )
            return

        view = mui.ReactionTargetView(self, interaction.guild.id, message_id, emoji, action_type, 0)
        await interaction.response.send_message(
            "Choisis la cible puis rédige le message envoyé lors de la réaction.",
            view=view,
            ephemeral=True,
        )
        followup_message = await interaction.original_response()
        view.followup_id = followup_message.id

    @msg_group.command(
        name="unlink_reaction",
        description="Retirer une action associée à une réaction sur un message suivi.",
    )
    @app_commands.describe(
        message="ID ou lien du message suivi",
        emoji="Emoji ciblé",
        action_type="Action ciblée (channel, user_dm, role_dm)",
        occurrence="Occurrence à retirer (1 = première correspondance)",
    )
    async def msg_unlink_reaction(  # TODO: review
        self,
        interaction: Interaction,
        *,
        message: str,
        emoji: str,
        action_type: MsgActionType,
        occurrence: app_commands.Range[int, 1, 50] = 1,
    ) -> None:
        """Remove one reaction action from a tracked message."""
        log_request(
            logger,
            "msg.unlink_reaction",
            interaction,
            message=message,
            emoji=emoji,
            action_type=action_type,
            occurrence=occurrence,
        )
        if not await is_in_allowed_channel(logger, interaction):
            return

        if not await check_has_role(logger, interaction, ALLOWED_ROLES):
            return

        message_id = self._parse_message_id(message)
        if message_id is None:
            await interaction.response.send_message("ID ou lien de message invalide.", ephemeral=True)
            return

        assert interaction.guild is not None
        tracked = self._get_tracked_message(interaction.guild.id, message_id)
        if tracked is None:
            await interaction.response.send_message("Aucun suivi trouvé pour ce message.", ephemeral=True)
            return

        matches: list[tuple[int, MsgReactionEvent]] = [
            (idx, r) for idx, r in enumerate(tracked.reactions) if r.emoji == emoji and r.action_type == action_type
        ]
        if not matches:
            await interaction.response.send_message("Aucune action trouvée pour cet émoji et type.", ephemeral=True)
            return

        if occurrence > len(matches):
            await interaction.response.send_message(
                f"Il n'existe que {len(matches)} action(s) correspondante(s) pour cet émoji.",
                ephemeral=True,
            )
            return

        target_idx, target_reaction = matches[occurrence - 1]
        del tracked.reactions[target_idx]
        self._set_tracked_message(interaction.guild.id, tracked)

        await interaction.response.send_message(
            f"Action retirée : {emoji} → {self._format_reaction_action(target_reaction)}",
            ephemeral=True,
        )

    @msg_group.command(
        name="list",
        description="Lister les messages actuellement suivis.",
    )
    async def msg_list(self, interaction: Interaction) -> None:
        """List currently tracked messages.

        Args:
            interaction (Interaction): The Discord interaction context.
        """
        log_request(logger, "msg.list", interaction)
        if not await is_in_allowed_channel(logger, interaction):
            return

        if not await check_has_role(logger, interaction, ALLOWED_ROLES):
            return

        assert interaction.guild is not None
        tracked_map = self._get_tracked_messages(interaction.guild.id)
        if not tracked_map:
            await interaction.response.send_message("Aucun suivi actif pour ce serveur.", ephemeral=True)
            return

        lines: list[str] = ["**Suivis de messages actifs**", ""]
        for tracked in tracked_map.values():
            if not tracked.active:
                continue
            link = f"https://discord.com/channels/{interaction.guild.id}/{tracked.channel_id}/{tracked.message_id}"
            lines.append(f"- ID `{tracked.message_id}` : {link}")
            if tracked.reactions:
                for idx, reaction in enumerate(tracked.reactions, start=1):
                    action_label = self._format_reaction_action(reaction)
                    preview = reaction.message_content
                    if len(preview) > 120:
                        preview = f"{preview[:117]}..."
                    lines.append(f"   {idx}. {reaction.emoji} : {action_label} | {preview}")
            else:
                lines.append("   (aucune action liée)")
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @msg_group.command(
        name="stop",
        description="Arrêter le suivi d'un message.",
    )
    @app_commands.describe(message="ID ou lien du message suivi")
    async def msg_stop(self, interaction: Interaction, *, message: str) -> None:
        """Stop tracking a tracked message.

        Args:
            interaction (Interaction): The Discord interaction context.
            message (str): The ID or link of the tracked message.
        """
        log_request(logger, "msg.stop", interaction, message=message)
        if not await is_in_allowed_channel(logger, interaction):
            return

        if not await check_has_role(logger, interaction, ALLOWED_ROLES):
            return

        message_id = self._parse_message_id(message)
        if message_id is None:
            await interaction.response.send_message("ID ou lien de message invalide.", ephemeral=True)
            return

        assert interaction.guild is not None
        if not self._remove_tracked_message(interaction.guild.id, message_id):
            await interaction.response.send_message("Aucun suivi trouvé pour ce message.", ephemeral=True)
            return

        await interaction.response.send_message(
            f"Suivi arrêté pour le message `{message_id}`.",
            ephemeral=True,
        )

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
        if not msg.channel.category or msg.channel.category.name in {"Bureau", "Annonces", "Chargés"}:
            return

        for egg in EASTER_EGGS:
            if any(keyword in msg.content.lower() for keyword in egg.keywords) and random.random() < egg.probability:
                await msg.channel.send(content=egg.response, reference=msg)
                logger.info(f"Easter egg triggered by {msg.author} in {msg.channel}: {egg.keywords}")

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: RawReactionActionEvent) -> None:
        """Handle reaction additions on tracked messages.

        Args:
            payload (RawReactionActionEvent): The raw reaction add event payload.
        """  # TODO: add logging
        if payload.guild_id is None:
            return
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return

        member = payload.member
        if member is None:
            member = guild.get_member(payload.user_id)
        if member is None or member.bot:
            return

        tracked = self._get_tracked_message(payload.guild_id, payload.message_id)
        if tracked is None or not tracked.active:
            return

        emoji_str = str(payload.emoji)
        actions = [r for r in tracked.reactions if r.emoji == emoji_str]
        if not actions:
            return

        for action in actions:
            try:
                await self._execute_reaction_action(guild, member, action)
            except Exception:
                logger.exception(
                    f"Failed to execute reaction action {action.action_type} for emoji {emoji_str} "
                    f"on message {payload.message_id} by {member}",
                )
            self._reaction_logs.append(
                guild.id,
                ReactionEvent(
                    message_id=payload.message_id,
                    user_id=member.id,
                    user_name=member.display_name,
                    emoji=emoji_str,
                    action=ReactionAction.ADD,
                    ts_iso=datetime.now(PARIS_TZ).isoformat(),
                ),
            )

    # endregion Listeners

    # region ====== Helpers ======

    @staticmethod
    def _parse_message_id(raw: str) -> int | None:
        digits = re.findall(r"(\d{15,25})", raw)
        if not digits:
            return None
        try:
            return int(digits[-1])
        except ValueError:
            return None

    @staticmethod
    def _format_reaction_action(reaction: MsgReactionEvent) -> str:
        target = reaction.target_id or reaction.target_name or ""
        return {
            "channel": f"message salon <#{target}>",
            "user_dm": "DM réacteur",
            "role_dm": f"DM rôle <@&{target}>",
        }.get(reaction.action_type, reaction.action_type)

    def _load_state(self) -> dict[str, dict[str, Any]]:
        state = load_json_state(logger, MESSAGES_STATE_FILE)
        logger.debug("Loaded message state.")
        return state

    @staticmethod
    def _save_state(state: dict[str, dict[str, Any]]) -> None:
        save_json_state(logger, MESSAGES_STATE_FILE, state)

    def _get_guild_state(self, guild_id: int) -> dict[str, Any]:
        key = str(guild_id)
        if key not in self.state:
            self.state[key] = {}
        return self.state[key]

    def _set_guild_state(self, guild_id: int, payload: dict[str, Any]) -> None:
        self.state[str(guild_id)] = payload
        self._save_state(self.state)

    def _get_tracked_messages(self, guild_id: int) -> dict[int, TrackedMessage]:
        guild_state = self._get_guild_state(guild_id)
        tracked_raw = guild_state.get("tracked", {}) or {}
        tracked: dict[int, TrackedMessage] = {}
        for msg_id_str, payload in tracked_raw.items():
            try:
                tracked[int(msg_id_str)] = TrackedMessage.from_dict(payload)
            except Exception:
                logger.warning(f"Ignoring invalid tracked message payload for guild {guild_id}: {payload}")
        return tracked

    def _get_tracked_message(self, guild_id: int, message_id: int) -> TrackedMessage | None:
        return self._get_tracked_messages(guild_id).get(message_id)

    def _set_tracked_message(self, guild_id: int, tracked: TrackedMessage) -> None:
        guild_state = self._get_guild_state(guild_id)
        tracked_map = guild_state.setdefault("tracked", {})
        tracked_map[str(tracked.message_id)] = tracked.to_dict()
        self._set_guild_state(guild_id, guild_state)

    def _remove_tracked_message(self, guild_id: int, message_id: int) -> bool:
        guild_state = self._get_guild_state(guild_id)
        tracked_map: dict[str, Any] = guild_state.get("tracked") or {}
        if str(message_id) not in tracked_map:
            return False
        tracked_map.pop(str(message_id), None)
        guild_state["tracked"] = tracked_map
        self._set_guild_state(guild_id, guild_state)
        return True

    async def register_reaction_action(self, guild_id: int, message_id: int, reaction: MsgReactionEvent) -> str:
        """Register a reaction action for a tracked message.

        Args:
            guild_id (int): The guild identifier.
            message_id (int): The message identifier.
            reaction (MsgReactionEvent): The reaction event to register.

        Returns:
            str: A confirmation message about the registered reaction action.
        """
        tracked = self._get_tracked_message(guild_id, message_id)
        if tracked is None or not tracked.active:
            return "Le suivi est inactif ou introuvable pour ce message."

        tracked.reactions.append(reaction)
        self._set_tracked_message(guild_id, tracked)

        await self._ensure_reaction_on_message(guild_id, tracked, reaction.emoji)

        action_desc = {
            "channel": f"message dans <#{reaction.target_id}>",
            "user_dm": "DM à la personne qui réagit",
            "role_dm": f"DM aux membres du rôle <@&{reaction.target_id}>",
        }.get(reaction.action_type, reaction.action_type)

        return f"Réaction {reaction.emoji} ajoutée.\nAction : {action_desc}\nMessage envoyé :\n{reaction.message_content}"

    async def _sync_reactions_on_message(self, guild_id: int, tracked: TrackedMessage) -> None:
        """Ensure all configured reaction emojis are present on the tracked message."""
        if not tracked.reactions:
            return
        unique_emojis = {r.emoji for r in tracked.reactions}
        guild = self.bot.get_guild(guild_id)
        channel = guild.get_channel(tracked.channel_id) if guild else self.bot.get_channel(tracked.channel_id)
        if channel is None and guild:
            with contextlib.suppress(Exception):
                channel = await guild.fetch_channel(tracked.channel_id)
        if not isinstance(channel, TextChannel):
            return
        try:
            msg = await channel.fetch_message(tracked.message_id)
        except Exception:
            logger.exception(f"Tracked message {tracked.message_id} no longer accessible.")
            return
        for emoji in unique_emojis:
            try:
                await msg.add_reaction(emoji)
            except Exception:
                logger.exception(f"Could not add reaction {emoji} on message {tracked.message_id}")

    async def _ensure_reaction_on_message(self, guild_id: int, tracked: TrackedMessage, emoji: str) -> None:
        guild = self.bot.get_guild(guild_id)
        channel = guild.get_channel(tracked.channel_id) if guild else self.bot.get_channel(tracked.channel_id)
        if channel is None and guild:
            with contextlib.suppress(Exception):
                channel = await guild.fetch_channel(tracked.channel_id)
        if not isinstance(channel, TextChannel):
            return
        try:
            msg = await channel.fetch_message(tracked.message_id)
        except Exception:
            logger.exception(f"Tracked message {tracked.message_id} no longer accessible.")
            return
        if any(reaction.me for reaction in msg.reactions if str(reaction.emoji) == emoji):
            logger.debug(f"Message {tracked.message_id} already has reaction {emoji}")
            return
        try:
            await msg.add_reaction(emoji)
        except Exception:
            logger.exception(f"Could not add reaction {emoji} on message {tracked.message_id}")

    async def _execute_reaction_action(self, guild: Guild, member: Member, action: MsgReactionEvent) -> None:
        """Execute a reaction action for a member in a guild.

        Args:
            guild (Guild): The guild where the reaction occurred.
            member (Member): The member who reacted.
            action (MsgReactionEvent): The reaction action to execute.
        """
        rendered = action.message_content.replace("{username}", member.display_name).replace("{user}", member.mention)

        match action.action_type:
            case "channel":
                channel = guild.get_channel(action.target_id) if action.target_id else None
                if isinstance(channel, TextChannel):
                    try:
                        await channel.send(rendered)
                        logger.info(f"Sent reaction message to channel {action.target_id} in guild {guild.id}")
                    except Exception:
                        logger.exception(f"Failed to send message to channel {action.target_id} in guild {guild.id}")
                else:
                    logger.error(f"Channel {action.target_id} not found for guild {guild.id}")
                return

            case "user_dm":
                await send_dm_to_member(logger, guild, member, rendered, dm_type="msg_reaction")
                logger.info(f"Sent user DM to {member.id} in guild {guild.id}")
                return

            case "role_dm":
                role: Role | None = guild.get_role(action.target_id) if action.target_id else None
                if role is None:
                    logger.error(f"Role {action.target_id} not found for guild {guild.id}")
                    return
                recipients = get_members_by_role(role=role)
                sent = 0
                for target in recipients:
                    if await send_dm_to_member(logger, guild, target, rendered, dm_type="msg_role_reaction"):
                        sent += 1
                logger.info(f"Sent role DM to {sent} members of role {role.name} in guild {guild.id}")
                return

        logger.error(f"Unknown reaction action type {action.action_type} in guild {guild.id}")

    # endregion Helpers


@deprecated("Load the cog using `bot.add_cog()` instead.")
async def setup(bot: commands.Bot) -> None:
    """Setup function to load the MessageManagement cog.

    Args:
        bot (commands.Bot): The bot instance.
    """
    await bot.add_cog(MessageManagement(bot))
