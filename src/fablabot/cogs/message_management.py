"""Message management commands for Discord Bot. Provides slash commands for sending and managing messages."""

from __future__ import annotations

import contextlib
from copy import copy
import csv
from datetime import datetime, timedelta
import io
import logging
from pathlib import Path
import random
import re
from typing import Any
from warnings import deprecated

from discord import (
    CategoryChannel,
    DMChannel,
    Embed,
    File,
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
from fablabot.helpers.help_messages import build_help_message
from fablabot.helpers.reaction_log import ReactionLogManager
from fablabot.helpers.state_store import JsonStateStore
from fablabot.helpers.utils import (
    ensure_command_context,
    get_members_by_role,
    get_or_fetch_member,
    is_valid_emoji,
    log_request,
    send_dm_to_member,
)
from fablabot.models.common import ReactionAction, ReactionEvent
from fablabot.models.message import EASTER_EGGS, MessageDraft, MsgCommand, MsgReactionEvent, TrackedMessage
from fablabot.ui import mui

logger = logging.getLogger(__name__)

MESSAGES_STATE_FILE = Path("data/messages_state.json")
REACTION_LOG_RETENTION = timedelta(days=30)
ALLOWED_ROLES = ADMIN_ROLES | {RoleNames.BUREAU}


class MessageManagement(commands.Cog):
    """Cog handling message tracking, reaction automation, and quick messaging workflows.

    Commands:
        - /msg help: Display help for message management commands.
        - /msg clear: Clear the current channel of its last messages.
        - /msg dm: Send a direct message to multiple users.
        - /msg start: Create or overwrite a message draft via a modal.
        - /msg follow: Start tracking an already published message.
        - /msg link_reaction: Link an action (DM / channel / role) to a reaction.
        - /msg unlink_reaction: Unlink an action linked to a reaction.
        - /msg list: List active trackings.
        - /msg stop: Stop tracking.
        - /msg preview: Preview the current draft.
        - /msg publish: Publish the draft to a channel.
        - /msg export: Export reaction history of a tracked message.

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
        self._state_store = JsonStateStore(logger, MESSAGES_STATE_FILE)
        self._reaction_logs = ReactionLogManager(
            self._state_store,
            retention=REACTION_LOG_RETENTION,
            logger=logger,
        )
        self._reaction_logs.purge_all()
        logger.info("MessageManagement initialized")

    # region ====== Message Slash Commands Group ======

    msg_group = app_commands.Group(name="msg", description="Gestion des messages")

    @msg_group.command(
        name="help",
        description="Afficher l'aide sur les commandes message.",
    )
    @app_commands.describe(show="Afficher l'aide publiquement ou non")
    async def msg_help(self, interaction: Interaction, *, show: bool = False) -> None:
        """Display help for message management commands.

        Args:
            interaction (Interaction): The Discord interaction context.
            show (bool): Whether to show the help publicly or not.
        """
        help_message = build_help_message(
            "Commandes de gestion des messages",
            "msg",
            [
                ("clear [messages]", "Nettoyer les derniers messages du salon (par défaut 5)"),
                ("dm <message>", "Envoyer un message privé à plusieurs utilisateurs sélectionnés"),
                ("start", "Créer ou remplacer le brouillon via un modal"),
                ("follow <channel> <message>", "Ajouter le suivi sur un message existant (ID ou lien)"),
                (
                    "link_reaction <message> <emoji>",
                    "Associer une réaction à une action automatisée (DM utilisateur/rôle ou message salon)",
                ),
                ("unlink_reaction", "Retirer une action liée à une réaction (sélection via une vue)"),
                ("list", "Lister les messages suivis et leurs réactions"),
                ("preview", "Prévisualiser le brouillon en cours"),
                ("publish <channel>", "Publier le brouillon et activer le suivi"),
                ("export [message]", "Exporter l'historique des réactions d'un message suivi"),
                ("stop", "Arrêter le suivi d'un message (sélection via une vue)"),
            ],
            footer=(
                "Actions supportées : `user_dm` (DM la personne qui réagit), "
                "`role_dm` (DM les membres d'un rôle), `channel` (message dans un salon cible)"
            ),
        )
        await interaction.response.send_message(help_message, ephemeral=not show)

    @msg_group.command(name="clear", description="Nettoie le salon actuel de ses derniers messages.")
    @app_commands.describe(messages="Le nombre de messages à supprimer (par défaut 5)")
    async def msg_clear(self, interaction: Interaction, *, messages: app_commands.Range[int, 1, 50] = 5) -> None:
        """Clears the current channel of its last messages.

        Args:
            interaction (Interaction): The Discord interaction context.
            messages (app_commands.Range[int, 1, 50], optional): The number of messages to purge. Defaults to 5.
        """
        log_request(logger, "message.clear", interaction, messages=messages)
        assert interaction.guild is not None
        assert not isinstance(
            interaction.channel,
            ForumChannel | CategoryChannel | DMChannel | GroupChannel | None,
        )
        if not interaction.permissions.manage_messages:
            logger.warning(f"Insufficient permissions for manage_messages: {interaction.user}")
            await interaction.response.send_message(ErrorMessages.NO_PERMISSION_MANAGE_MESSAGES, ephemeral=True)
            return

        from fablabot.guild_config import get_commands_channel_id, get_log_channel_id

        if (
            interaction.channel.name.endswith("_bot")
            or get_log_channel_id() == interaction.channel.id
            or get_commands_channel_id(interaction.guild.id) == interaction.channel.id
        ):
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
    async def msg_dm(self, interaction: Interaction) -> None:
        """Send a direct message to multiple users.

        Args:
            interaction (Interaction): The Discord interaction context.
            message (str): The message content to send.
        """
        if not await ensure_command_context(logger, "message.dm", interaction, required_roles={RoleNames.BUREAU}):
            return

        await interaction.response.send_modal(mui.BulkDMModal(self))

    @msg_group.command(
        name="start",
        description="Démarrer/écraser un brouillon de message (modal).",
    )
    async def msg_start(self, interaction: Interaction) -> None:
        """Create or overwrite a message draft via a modal.

        Args:
            interaction (Interaction): The Discord interaction context.
        """
        if not await ensure_command_context(logger, "msg.start", interaction, required_roles=ALLOWED_ROLES):
            return

        await interaction.response.send_modal(mui.StartMessageModal(self))

    @msg_group.command(name="follow", description="Démarrer le suivi sur un message déjà envoyé.")
    @app_commands.describe(
        channel="Salon dans lequel se trouve le message",
        message="ID du message ou lien complet",
    )
    async def msg_follow(self, interaction: Interaction, *, channel: TextChannel, message: str) -> None:
        """Start tracking an already published message.

        Args:
            interaction (Interaction): The Discord interaction context.
            channel (TextChannel): The channel where the message is located.
            message (str): The message ID or full link.
        """
        if not await ensure_command_context(
            logger,
            "msg.follow",
            interaction,
            log_details={"channel": channel.name, "message": message},
            required_roles=ALLOWED_ROLES,
        ):
            return

        message_id = self._parse_message_id(message)
        if message_id is None:
            logger.warning(f"Invalid message id provided to /msg follow by {interaction.user}")
            await interaction.response.send_message(ErrorMessages.INVALID_MESSAGE_ID, ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            fetched: Message = await channel.fetch_message(message_id)
        except Exception:
            logger.exception(f"Failed to fetch message {message_id} in {channel}")
            await interaction.followup.send(ErrorMessages.INVALID_MESSAGE_ID, ephemeral=True)
            return

        assert interaction.guild is not None
        tracked = TrackedMessage(
            message_id=fetched.id,
            channel_id=channel.id,
            content=fetched.content or "(contenu vide ou embed)",
            reactions=[],
            created_by=interaction.user.id,
            created_at_iso=datetime.now(PARIS_TZ).isoformat(),
        )
        self.set_tracked_message(interaction.guild.id, tracked)

        logger.info(
            f"Started tracking message {fetched.id} in guild {interaction.guild.id} "
            f"(reactions preserved: {len(tracked.reactions)})",
        )
        await interaction.followup.send(
            f"Suivi démarré sur le message `{fetched.id}` dans {channel.mention}.\n"
            "Ajoute des actions avec `/msg link_reaction`.",
            ephemeral=True,
        )

    @msg_group.command(name="link_reaction", description="Associer une réaction à une action automatisée.")
    @app_commands.describe(emoji="Emoji déclencheur")
    async def msg_link_reaction(
        self,
        interaction: Interaction,
        *,
        emoji: str,
    ) -> None:
        """Link a reaction to an automated action.

        Args:
            interaction (Interaction): The Discord interaction context.
            emoji (str): The emoji that triggers the action.
        """
        if not await ensure_command_context(
            logger,
            "msg.link_reaction",
            interaction,
            log_details={"emoji": emoji},
            required_roles=ALLOWED_ROLES,
        ):
            return

        if not is_valid_emoji(emoji):
            await interaction.response.send_message(ErrorMessages.INVALID_EMOJI, ephemeral=True)
            return

        assert interaction.guild is not None

        draft = self.get_draft(interaction.guild.id)

        tracked_map = self._get_tracked_messages(interaction.guild.id)
        if not tracked_map and draft is None:
            await interaction.response.send_message(
                "Aucun brouillon ou message suivi disponible. Lance `/msg start` ou `/msg follow`.",
                ephemeral=True,
            )
            return

        view = mui.TrackedMessageSelectView(self, list(tracked_map.values()), MsgCommand.LINK, draft=draft, emoji=emoji)
        await interaction.response.send_message(
            "Choisis le brouillon ou un message suivi pour ajouter la réaction.",
            view=view,
            ephemeral=True,
        )

    @msg_group.command(
        name="unlink_reaction",
        description="Retirer une action associée à une réaction sur un message suivi.",
    )
    async def msg_unlink_reaction(self, interaction: Interaction) -> None:
        """Remove one reaction action from a tracked message via a selection view.

        Args:
            interaction (Interaction): The Discord interaction context.
        """
        if not await ensure_command_context(logger, "msg.unlink_reaction", interaction, required_roles=ALLOWED_ROLES):
            return

        assert interaction.guild is not None
        tracked_map = self._get_tracked_messages(interaction.guild.id)
        tracked_with_actions = [t for t in tracked_map.values() if t.reactions]
        draft = self.get_draft(interaction.guild.id)
        draft_with_actions = draft if draft and draft.reactions else None
        if not tracked_with_actions and draft_with_actions is None:
            await interaction.response.send_message(
                "Aucune action configurée sur les messages suivis ou le brouillon.",
                ephemeral=True,
            )
            return

        view = mui.TrackedMessageSelectView(
            self,
            tracked_with_actions,
            MsgCommand.UNLINK,
            draft=draft_with_actions,
        )
        await interaction.response.send_message(
            "Sélectionne le message suivi ou le brouillon puis l'action à retirer.",
            view=view,
            ephemeral=True,
        )

    @msg_group.command(
        name="list",
        description="Lister les messages actuellement suivis.",
    )
    async def msg_list(self, interaction: Interaction) -> None:  # TODO: voir brouillon
        """List currently tracked messages.

        Args:
            interaction (Interaction): The Discord interaction context.
        """
        if not await ensure_command_context(logger, "msg.list", interaction, required_roles=ALLOWED_ROLES):
            return

        assert interaction.guild is not None
        tracked_map = self._get_tracked_messages(interaction.guild.id)
        if not tracked_map:
            await interaction.response.send_message(ErrorMessages.MSG_NO_TRACKED_AVAILABLE, ephemeral=True)
            return

        lines: list[str] = ["**Suivis de messages actifs**", ""]
        for tracked in tracked_map.values():
            link = f"https://discord.com/channels/{interaction.guild.id}/{tracked.channel_id}/{tracked.message_id}"
            lines.append(f"- ID `{tracked.message_id}` : {link}")
            if tracked.reactions:
                for idx, reaction in enumerate(tracked.reactions, start=1):
                    action_label = self.format_reaction_action(reaction)
                    preview = reaction.message_content
                    lines.append(f"   {idx}. {reaction.emoji} : {action_label} | `{preview.replace('\n', '\\n')}`")
            else:
                lines.append("   (aucune action liée)")
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @msg_group.command(
        name="preview",
        description="Prévisualiser le brouillon courant.",
    )
    async def msg_preview(self, interaction: Interaction) -> None:
        """Preview the current draft message.

        Args:
            interaction (Interaction): The Discord interaction context.
        """
        if not await ensure_command_context(logger, "msg.preview", interaction, required_roles=ALLOWED_ROLES):
            return

        assert interaction.guild is not None
        draft = self.get_draft(interaction.guild.id)
        if draft is None:
            await interaction.response.send_message(ErrorMessages.MSG_NO_DRAFT, ephemeral=True)
            return

        embed = Embed(title="Réactions préliées")
        for idx, reaction in enumerate(draft.reactions, start=1):
            embed.add_field(
                name=f"{idx}. {reaction.emoji} :",
                value=f"**{self.format_reaction_action(reaction)}**\n{reaction.message_content or '_(vide)_'}",
                inline=False,
            )

        logger.debug(f"Previewed message draft for guild {interaction.guild.id} with {len(draft.reactions)} reactions")
        await interaction.response.send_message(draft.content or "_(vide)_", embed=embed)

    @msg_group.command(
        name="publish",
        description="Publier le brouillon dans un salon et activer le suivi.",
    )
    @app_commands.describe(channel="Salon cible pour la publication")
    async def msg_publish(self, interaction: Interaction, *, channel: TextChannel) -> None:
        """Publier le brouillon dans un salon et activer le suivi.

        Args:
            interaction (Interaction): Le contexte d'interaction Discord.
            channel (TextChannel): Le salon cible pour la publication.
        """
        if not await ensure_command_context(
            logger,
            "msg.publish",
            interaction,
            log_details={"channel": channel.name},
            required_roles=ALLOWED_ROLES,
        ):
            return

        assert interaction.guild is not None
        draft = self.get_draft(interaction.guild.id)
        if draft is None or not draft.content.strip():
            await interaction.response.send_message(ErrorMessages.MSG_DRAFT_EMPTY, ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            msg = await channel.send(draft.content)
        except Exception:
            logger.exception(f"Failed to publish draft in {channel}")
            await interaction.followup.send(
                ErrorMessages.HTTP_ERROR.format(operation="la publication du message"),
                ephemeral=True,
            )
            return

        updated_reactions: list[MsgReactionEvent] = []
        for reaction in draft.reactions:
            cloned = copy(reaction)
            cloned.message_id = msg.id
            updated_reactions.append(cloned)
        tracked = TrackedMessage(
            message_id=msg.id,
            channel_id=channel.id,
            content=draft.content,
            reactions=updated_reactions,
            created_by=interaction.user.id,
            created_at_iso=datetime.now(PARIS_TZ).isoformat(),
        )
        self.set_tracked_message(interaction.guild.id, tracked)

        await self._sync_reactions_on_message(interaction.guild.id, tracked)
        self._clear_draft(interaction.guild.id)

        logger.info(
            f"Published message {msg.id} in guild {interaction.guild.id} with {len(updated_reactions)} reaction actions",
        )
        await interaction.followup.send(
            f"Message publié dans {channel.mention} (ID `{msg.id}`) avec {len(updated_reactions)} réaction(s) configurée(s).",
        )

    @msg_group.command(
        name="export",
        description="Exporter l'historique des réactions d'un message suivi.",
    )
    async def msg_export(self, interaction: Interaction) -> None:
        """Export the reaction history of a tracked message.

        Args:
            interaction (Interaction): The Discord interaction context.
        """
        if not await ensure_command_context(logger, "msg.export", interaction, required_roles=ALLOWED_ROLES):
            return

        assert interaction.guild is not None

        tracked_map = self._get_tracked_messages(interaction.guild.id)
        if not tracked_map:
            await interaction.response.send_message(ErrorMessages.MSG_NO_TRACKED_AVAILABLE, ephemeral=True)
            return

        view = mui.TrackedMessageSelectView(self, list(tracked_map.values()), MsgCommand.EXPORT)
        await interaction.response.send_message(
            "Choisis le message suivi dont tu veux exporter les réactions.",
            view=view,
            ephemeral=True,
        )

    @msg_group.command(
        name="stop",
        description="Arrêter le suivi d'un message.",
    )
    async def msg_stop(self, interaction: Interaction) -> None:
        """Stop tracking a tracked message.

        Args:
            interaction (Interaction): The Discord interaction context.
        """
        if not await ensure_command_context(logger, "msg.stop", interaction, required_roles=ALLOWED_ROLES):
            return

        assert interaction.guild is not None
        tracked_map = self._get_tracked_messages(interaction.guild.id)
        if not tracked_map:
            await interaction.response.send_message(ErrorMessages.MSG_NO_TRACKED_AVAILABLE, ephemeral=True)
            return

        view = mui.TrackedMessageSelectView(self, list(tracked_map.values()), MsgCommand.STOP)
        await interaction.response.send_message(content="Sélectionne le message dont tu veux arrêter le suivi.", view=view)

    # endregion Message Slash Commands Group

    # region ====== Suggestion Slash Command ======

    @app_commands.command(name="suggest", description="Envoyer une suggestion (choisir destinataire, texte, anonymat)")
    async def suggest(self, interaction: Interaction) -> None:
        """Single command entrypoint for sending suggestions via a small interactive flow.

        The flow collects the recipient and anonymity via components, then opens a modal to input the suggestion text.

        Args:
            interaction (Interaction): The Discord interaction context.
        """
        await ensure_command_context(logger, "suggest", interaction, enforce_commands_channel=False)
        view = mui.SuggestionView(self)
        await interaction.response.send_message(
            "Veuillez choisir le destinataire et si vous souhaitez rester anonyme, puis cliquez sur 'Rédiger la suggestion'.",
            view=view,
            ephemeral=True,
        )

    # endregion Suggestion Slash Command

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
            if any(pattern.search(msg.content) for pattern in egg.keywords) and random.random() < egg.probability:
                await msg.channel.send(content=egg.response, reference=msg)
                if egg.reaction:
                    with contextlib.suppress(Exception):
                        await msg.add_reaction(egg.reaction)
                logger.info(f"Easter egg triggered by {msg.author} in {msg.channel}: {egg.keywords}")

                break

        from fablabot.guild_config import is_philippine_bully_enabled, set_philippine_bully_enabled

        assert msg.guild is not None
        if "monster" in msg.content.lower() and is_philippine_bully_enabled(msg.guild.id):
            philippine = await get_or_fetch_member(msg.guild, 641386630581714976)
            if philippine is not None:
                await philippine.timeout(timedelta(seconds=10), reason="Monster detected in message")
                logger.debug(f"Philippine timed out in guild {msg.guild.id} due to monster message by {msg.author}")

        if "go bully philippine" in msg.content.lower() and msg.author.id == 585347569329373214:
            set_philippine_bully_enabled(msg.guild.id, enabled=True)
            logger.debug(f"Philippine Bully enabled in guild {msg.guild.id} by {msg.author}")

        if "stop bullying philippine" in msg.content.lower() and msg.author.id == 585347569329373214:
            set_philippine_bully_enabled(msg.guild.id, enabled=False)
            logger.debug(f"Philippine Bully disabled in guild {msg.guild.id} by {msg.author}")

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
        if tracked is None:
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
    def format_reaction_action(reaction: MsgReactionEvent) -> str:
        """Format a reaction action for display.

        Args:
            reaction (MsgReactionEvent): The reaction event to format.

        Returns:
            str: The formatted action description.
        """
        target = reaction.target_id or reaction.target_name or ""
        return {
            "channel": f"message salon <#{target}>",
            "user_dm": "DM réacteur",
            "role_dm": f"DM rôle <@&{target}>",
        }.get(reaction.action_type, reaction.action_type)

    def build_reaction_export(self, guild_id: int, message_id: int) -> tuple[File | None, str]:
        """Build the reaction history export for a message.

        Args:
            guild_id (int): The guild identifier.
            message_id (int): The message identifier.

        Returns:
            tuple[File | None, str]: The file to send (None if no history) and the associated message.
        """
        history = self._reaction_logs.history(guild_id, message_id)
        if not history:
            return None, ErrorMessages.MSG_NO_REACTION_HISTORY

        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(["timestamp_iso", "action", "emoji", "user_name", "user_id"])
        for ev in history:
            writer.writerow([ev.ts_iso, ev.action, ev.emoji, ev.user_name or "", ev.user_id])

        logger.info(f"Exported {len(history)} reaction events for message {message_id} in guild {guild_id}")
        file = File(
            fp=io.BytesIO(buffer.getvalue().encode("utf-8")),
            filename=f"message_{message_id}_reactions_log.csv",
        )
        return file, f"Export des réactions pour le message `{message_id}`."

    def _get_guild_state(self, guild_id: int) -> dict[str, Any]:
        """Retrieve or initialize the state for a guild."""
        return self._state_store.ensure_guild(guild_id)

    def _set_guild_state(self, guild_id: int, payload: dict[str, Any]) -> None:
        """Set the state for a guild."""
        self._state_store.set_guild(guild_id, payload)

    def _get_tracked_messages(self, guild_id: int) -> dict[int, TrackedMessage]:
        """Retrieve all tracked messages for a guild."""
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
        """Retrieve a specific tracked message for a guild."""
        return self._get_tracked_messages(guild_id).get(message_id)

    def _latest_tracked_id(self, guild_id: int) -> int | None:
        """Retrieve the most recently tracked message ID for a guild."""
        tracked = self._get_tracked_messages(guild_id)
        if not tracked:
            return None
        sorted_msgs = sorted(
            (t for t in tracked.values()),
            key=lambda t: t.created_at_iso or "",
            reverse=True,
        )
        return sorted_msgs[0].message_id if sorted_msgs else None

    def set_tracked_message(self, guild_id: int, tracked: TrackedMessage) -> None:
        """Set or update a tracked message for a guild.

        Args:
            guild_id (int): The ID of the guild.
            tracked (TrackedMessage): The tracked message to set or update.
        """
        guild_state = self._get_guild_state(guild_id)
        tracked_map = guild_state.setdefault("tracked", {})
        tracked_map[str(tracked.message_id)] = tracked.to_dict()
        self._set_guild_state(guild_id, guild_state)

    def remove_tracked_message(self, guild_id: int, message_id: int) -> bool:
        """Remove a tracked message for a guild.

        Args:
            guild_id (int): The ID of the guild.
            message_id (int): The ID of the message to remove.

        Returns:
            bool: True if the message was removed, False if it was not found.
        """
        guild_state = self._get_guild_state(guild_id)
        tracked_map: dict[str, Any] = guild_state.get("tracked") or {}
        if str(message_id) not in tracked_map:
            return False
        tracked_map.pop(str(message_id), None)
        guild_state["tracked"] = tracked_map
        self._set_guild_state(guild_id, guild_state)
        return True

    def get_draft(self, guild_id: int) -> MessageDraft | None:
        """Retrieve the message draft for a guild.

        Args:
            guild_id (int): The ID of the guild.

        Returns:
            MessageDraft | None: The message draft for the guild, or None if not found.
        """
        guild_state = self._get_guild_state(guild_id)
        draft_raw = guild_state.get("draft")
        return MessageDraft.from_dict(draft_raw) if draft_raw else None

    def set_draft(self, guild_id: int, draft: MessageDraft) -> None:
        """Set the message draft for a guild.

        Args:
            guild_id (int): The ID of the guild.
            draft (MessageDraft): The message draft to set.
        """
        guild_state = self._get_guild_state(guild_id)
        guild_state["draft"] = draft.to_dict()
        self._set_guild_state(guild_id, guild_state)
        logger.debug(f"Draft updated for guild {guild_id} with {len(draft.reactions)} reactions")

    def _clear_draft(self, guild_id: int) -> None:
        """Clear the message draft for a guild."""
        guild_state = self._get_guild_state(guild_id)
        if "draft" in guild_state:
            guild_state.pop("draft", None)
            self._set_guild_state(guild_id, guild_state)
            logger.debug(f"Draft cleared for guild {guild_id}")

    async def register_reaction_action(self, guild_id: int, reaction: MsgReactionEvent) -> str:
        """Register a reaction action for a tracked message or draft.

        Args:
            guild_id (int): The ID of the guild.
            reaction (MsgReactionEvent): The reaction event to register.
        """
        if reaction.message_id is None:
            draft = self.get_draft(guild_id)
            if draft is None:
                return ErrorMessages.MSG_NO_DRAFT
            draft.reactions.append(reaction)
            self.set_draft(guild_id, draft)
            logger.info(f"Added reaction {reaction.emoji} to draft in guild {guild_id}")
            return f"Réaction {reaction.emoji} ajoutée au brouillon."

        tracked = self._get_tracked_message(guild_id, reaction.message_id)
        if tracked is None:
            return ErrorMessages.MSG_NO_TRACKED

        tracked.reactions.append(reaction)
        self.set_tracked_message(guild_id, tracked)

        logger.info(
            f"Registered reaction {reaction.emoji} ({reaction.action_type}) "
            f"for message {tracked.message_id} in guild {guild_id}",
        )
        await self._ensure_reaction_on_message(guild_id, tracked, reaction.emoji)
        action_desc = self.format_reaction_action(reaction)

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
        """Ensure a specific reaction emoji is present on the tracked message."""
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
        rendered = (
            action.message_content.replace("{username}", member.display_name)
            .replace("{user}", member.mention)
            .replace("{emoji}", str(action.emoji))
        )

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
