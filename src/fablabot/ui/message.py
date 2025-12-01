"""User interface components for message-related features."""

from __future__ import annotations

from datetime import datetime
import logging
from typing import TYPE_CHECKING, Any, Literal, cast

from discord import ButtonStyle, ChannelType, Embed, Guild, Interaction, Member, SelectOption, TextChannel, TextStyle, ui
from discord.utils import get

from fablabot.helpers.constants import PARIS_TZ, ErrorMessages
from fablabot.helpers.utils import format_member_mention, get_members_by_role, send_dm_to_member
from fablabot.models.message import (
    ANONYMOUS_ICON_URL,
    SUGGESTION_OPTIONS,
    MessageDraft,
    MsgActionType,
    MsgCommand,
    MsgReactionEvent,
    SuggestionConfig,
    TrackedMessage,
)

if TYPE_CHECKING:
    from fablabot.cogs import MessageManagement

logger = logging.getLogger(__name__)

MAX_TRACKED_OPTIONS = 25

# region ====== BulkDM UI Components ======


class _BulkDMView(ui.View):
    """View for bulk direct message sending."""

    def __init__(self, sender: Member, followup_id: int, message: str) -> None:
        """Initialize the view for bulk direct messages.

        Args:
            sender (Member): The member initiating the message sending.
            followup_id (int): The ID of the follow-up message to edit with results.
            message (str): The message to send to the selected members.
        """
        super().__init__(timeout=None)
        self.sender = sender
        self.followup_id = followup_id
        self.message = message

        async def _on_select(interaction: Interaction) -> None:
            if not interaction.response.is_done():
                await interaction.response.defer()

        self.select: ui.UserSelect[Any] = ui.UserSelect(
            placeholder="Sélectionne les membres...",
            min_values=1,
            max_values=25,
        )
        self.select.callback = _on_select  # type: ignore[method-assign]

        self.confirm_button: ui.Button[Any] = ui.Button(label="Confirmer", style=ButtonStyle.primary)
        self.confirm_button.callback = self.confirm  # type: ignore[method-assign]

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

        for m in members:
            if await send_dm_to_member(logger, interaction.guild, m, self.message, dm_type="Bulk"):
                delivered.append(m)
            else:
                failed.append(m)

        logger.info(f"Bulk DM by {self.sender} delivered to {delivered} with failures {failed}")

        lines: list[str] = ["Envoi des messages terminé."]
        if delivered:
            lines.append("Succès : " + ", ".join(format_member_mention(m) for m in delivered))
        if failed:
            lines.append("Échecs : " + ", ".join(format_member_mention(m) for m in failed))
        lines.append("Contenu envoyé :")
        lines.append(f">>> {self.message}")

        await interaction.followup.edit_message(self.followup_id, content="\n".join(lines))
        await interaction.delete_original_response()


class BulkDMModal(ui.Modal, title="Envoyer un MP à plusieurs utilisateurs"):
    """Modal for composing a bulk direct message."""

    message_input: ui.TextInput = ui.TextInput(
        label="Message à envoyer",
        style=TextStyle.long,
        placeholder="Saisis le message à envoyer en MP...",
        required=True,
        max_length=2000,
    )

    def __init__(self, cog: MessageManagement) -> None:
        """Initialize the BulkDMModal.

        Args:
            cog (MessageManagement): The MessageManagement cog instance.
        """
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: Interaction) -> None:
        """Handle modal submission.

        Args:
            interaction (Interaction): The interaction context.
        """
        assert isinstance(interaction.user, Member)

        await interaction.response.defer(thinking=True)
        followup_mes = await interaction.followup.send("Sélection des membres en cours...", wait=True)

        message = self.message_input.value.strip()
        message += (
            f"\n\n*Ce message vous a été envoyé par un membre du Bureau du Fablab. Merci de ne pas y répondre directement.*"
            f"\nPour plus d'informations, contactez <@{interaction.user.id}>."
        )

        view = _BulkDMView(
            sender=interaction.user,
            followup_id=followup_mes.id,
            message=message,
        )
        await interaction.followup.send(
            (
                "Selectionnez les membres a qui envoyer le message puis cliquez sur **Confirmer**.\n\n"
                f"Message à envoyer :\n>>> {message}"
            ),
            view=view,
            ephemeral=True,
        )


# endregion BulkDM UI Components


class StartMessageModal(ui.Modal, title="Préparer un message"):
    """Modal to start a new message draft.

    Attributes:
        content_input (ui.TextInput): Input field for the message content.
    """

    content_input: ui.TextInput = ui.TextInput(
        label="Contenu du message",
        style=TextStyle.long,
        placeholder="Saisis le message à publier",
        required=True,
        max_length=2000,
    )

    def __init__(self, cog: MessageManagement) -> None:
        """Initialize the StartMessageModal.

        Args:
            cog (MessageManagement): MessageManagement cog instance.
        """
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: Interaction) -> None:
        """Handle modal submission.

        Args:
            interaction (Interaction): The interaction context.
        """
        assert interaction.guild is not None
        draft = MessageDraft(content=self.content_input.value.strip(), reactions=[])
        self.cog.set_draft(interaction.guild.id, draft)

        await interaction.response.send_message(
            "Brouillon enregistré. Ajoute des réactions avec `/msg link_reaction` puis `/msg preview` et `/msg publish`.",
            embed=Embed(title="Aperçu brouillon", description=draft.content or "_(vide)_"),
            ephemeral=True,
        )


# region ====== UI Components for Linking Reactions ======


class _ReactionMessageInputModal(ui.Modal, title="Message à envoyer"):
    """Modal for inputting the content of the message to send for a reaction action."""

    message_input: ui.TextInput = ui.TextInput(
        label="Utilise {username} pour le nom du réacteur.",
        style=TextStyle.long,
        placeholder="{user} a réagi avec {emoji}...",
        required=True,
        max_length=2000,
    )

    def __init__(self, cog: MessageManagement, guild_id: int, reaction: MsgReactionEvent) -> None:
        """Initialize the modal.

        Args:
            cog (MessageManagement): The MessageManagement cog instance.
            guild_id (int): The guild identifier.
            reaction (MsgReactionEvent): The reaction event being configured.
        """
        super().__init__()
        self.cog = cog
        self.guild_id = guild_id
        self.reaction = reaction

    async def on_submit(self, interaction: Interaction) -> None:
        """Handle modal submission.

        Args:
            interaction (Interaction): The interaction context.
        """
        message_content = self.message_input.value.strip()
        self.reaction.message_content = message_content

        feedback = await self.cog.register_reaction_action(self.guild_id, self.reaction)
        await interaction.response.edit_message(content=feedback, view=None)


class _ReactionTargetView(ui.View):
    """View for selecting the target of a reaction action if it's necessary and the content of the message."""

    def __init__(
        self,
        cog: MessageManagement,
        guild_id: int,
        message_id: int | None,
        emoji: str,
        action_type: Literal["channel", "role_dm"],
    ) -> None:
        """Initialize the modal.

        Args:
            cog (MessageManagement): The MessageManagement cog instance.
            guild_id (int): The guild identifier.
            message_id (int): The message identifier.
            emoji (str): The emoji for this reaction.
            action_type (Literal["channel", "role_dm"]): The type of action.
        """
        super().__init__(timeout=None)
        self.cog = cog
        self.guild_id = guild_id
        self.message_id = message_id
        self.emoji = emoji
        self.action_type: MsgActionType = action_type

        async def _on_select(interaction: Interaction) -> None:
            match action_type:
                case "channel":
                    target_id = self.channel_select.values[0].id
                    target_value = self.channel_select.values[0].name
                case "role_dm":
                    target_id = self.role_select.values[0].id
                    target_value = self.role_select.values[0].name

            await interaction.response.send_modal(
                _ReactionMessageInputModal(
                    self.cog,
                    self.guild_id,
                    MsgReactionEvent(
                        emoji=self.emoji,
                        action_type=self.action_type,
                        message_content="",
                        target_id=target_id,
                        target_name=target_value,
                        message_id=self.message_id,
                    ),
                ),
            )

        match action_type:
            case "channel":
                self.channel_select: ui.ChannelSelect[Any] = ui.ChannelSelect(
                    placeholder="Sélectionne le canal...",
                    channel_types=[ChannelType.text],
                )
                self.channel_select.callback = _on_select  # type: ignore[method-assign]
                self.add_item(self.channel_select)
            case "role_dm":
                self.role_select: ui.RoleSelect[Any] = ui.RoleSelect(placeholder="Sélectionne le rôle...")
                self.role_select.callback = _on_select  # type: ignore[method-assign]
                self.add_item(self.role_select)


class _ReactionActionTypeSelectView(ui.View):
    """View to pick an action type before selecting the reaction target."""

    def __init__(
        self,
        cog: MessageManagement,
        guild_id: int,
        message_id: int | None,
        emoji: str,
    ) -> None:
        """Initialize the ReactionActionTypeView.

        Args:
            cog (MessageManagement): The MessageManagement cog instance.
            guild_id (int): The guild identifier.
            message_id (int | None): The message identifier.
            emoji (str): The emoji for this reaction.
        """
        super().__init__(timeout=None)
        self.cog = cog
        self.guild_id = guild_id
        self.message_id = message_id
        self.emoji = emoji

    @ui.button(label="Message dans un salon", style=ButtonStyle.primary)
    async def channel_button(self, interaction: Interaction, _button: ui.Button) -> None:
        """Handle channel button click.

        Args:
            interaction (Interaction): The Discord interaction context.
            _button (ui.Button): The button that was clicked.
        """
        target_view = _ReactionTargetView(self.cog, self.guild_id, self.message_id, self.emoji, "channel")
        await interaction.response.edit_message(
            content="Choisis le channel où envoyer le message puis rédige le message envoyé lors de la réaction.",
            view=target_view,
        )

    @ui.button(label="DM la personne", style=ButtonStyle.primary)
    async def user_dm_button(self, interaction: Interaction, _button: ui.Button) -> None:
        """Handle user DM button click.

        Args:
            interaction (Interaction): The Discord interaction context.
            _button (ui.Button): The button that was clicked.
        """
        await interaction.response.send_modal(
            _ReactionMessageInputModal(
                self.cog,
                self.guild_id,
                MsgReactionEvent(
                    emoji=self.emoji,
                    action_type="user_dm",
                    message_content="",
                    target_id=0,
                    target_name="",
                    message_id=self.message_id,
                ),
            ),
        )

    @ui.button(label="DM un rôle", style=ButtonStyle.primary)
    async def role_dm_button(self, interaction: Interaction, _button: ui.Button) -> None:
        """Handle role DM button click.

        Args:
            interaction (Interaction): The Discord interaction context.
            _button (ui.Button): The button that was clicked.
        """
        target_view = _ReactionTargetView(self.cog, self.guild_id, self.message_id, self.emoji, "role_dm")
        await interaction.response.edit_message(
            content="Choisis le rôle à MP puis rédige le message envoyé lors de la réaction.",
            view=target_view,
        )


# endregion UI Components for Linking Reactions

# TODO: follow prend link_reaction du draft


# region ====== TrackedMessageSelectView and its Components ======


class _TrackedMessageSelectButton(ui.Button["TrackedMessageSelectView"]):
    """Button to select a tracked message before performing an action."""

    def __init__(self, tracked: TrackedMessage) -> None:
        """Initialize the tracked message button.

        Args:
            tracked (TrackedMessage): The tracked message represented by this button.
        """
        super().__init__(
            label=f"Message {tracked.message_id}",
            style=ButtonStyle.primary,
            custom_id=f"select_tracked_msg_{tracked.message_id}",
        )
        self.tracked = tracked

    async def callback(self, interaction: Interaction) -> None:
        """Handle button click.

        Args:
            interaction (Interaction): The Discord interaction context.

        Raises:
            NotImplementedError: If the command is not yet implemented.
        """
        if self.view is None:
            await interaction.response.send_message(ErrorMessages.EXPIRED_VIEW_MESSAGE, ephemeral=True)
            return

        view = cast("TrackedMessageSelectView", self.view)
        match view.cmd:
            case MsgCommand.LINK:
                await view.open_link_selector(interaction, self.tracked.message_id)
            case MsgCommand.UNLINK:
                await view.open_unlink_selector(interaction, self.tracked)
            case MsgCommand.STOP:
                await view.stop_tracking(interaction, self.tracked)
            case MsgCommand.EXPORT:
                await view.export_history(interaction, self.tracked)
            case _:
                logger.error("Unknown command in TrackedMessageSelectView callback.")


class _DraftSelectButton(ui.Button["TrackedMessageSelectView"]):
    """Button to select the current draft."""

    def __init__(self) -> None:
        super().__init__(label="Brouillon", style=ButtonStyle.secondary, custom_id="select_draft")

    async def callback(self, interaction: Interaction) -> None:
        """Handle draft selection.

        Args:
            interaction (Interaction): The Discord interaction context.
        """
        if self.view is None:
            await interaction.response.send_message(ErrorMessages.EXPIRED_VIEW_MESSAGE, ephemeral=True)
            return

        view = cast("TrackedMessageSelectView", self.view)
        if view.draft is None:
            await interaction.response.send_message(ErrorMessages.MSG_NO_DRAFT, ephemeral=True)
            return

        match view.cmd:
            case MsgCommand.LINK:
                await view.open_link_selector(interaction, None)
            case MsgCommand.UNLINK:
                await view.open_unlink_selector(interaction, view.draft)
            case _:
                logger.error("Unknown command in TrackedMessageSelectView draft callback.")
                await interaction.response.send_message(ErrorMessages.EXPIRED_VIEW_MESSAGE, ephemeral=True)


class TrackedMessageSelectView(ui.View):  # TODO: show preview
    """View to pick a tracked message or draft before running message commands."""

    def __init__(
        self,
        cog: MessageManagement,
        tracked_messages: list[TrackedMessage],
        cmd: MsgCommand,
        *,
        draft: MessageDraft | None = None,
        emoji: str | None = None,
        followup_message_id: int | None = None,
    ) -> None:
        """Initialize the TrackedMessageSelectView.

        Args:
            cog (MessageManagement): The MessageManagement cog instance.
            tracked_messages (list[TrackedMessage]): The list of tracked messages to select from.
            cmd (MsgTrackedCommand): The command determining what happens after selection.
            draft (MessageDraft | None): Optional draft to include as a selectable target.
            emoji (str | None): Emoji passed through when linking a reaction.
            followup_message_id (int | None): ID of the follow-up message to edit with results.
        """
        super().__init__(timeout=None)
        self.cog = cog
        self.tracked_messages = tracked_messages
        self.cmd = cmd
        self.draft = draft
        self.emoji = emoji
        self.followup_message_id = followup_message_id

        if self.draft is not None and self.cmd in {MsgCommand.LINK, MsgCommand.UNLINK}:
            self.add_item(_DraftSelectButton())

        max_options = MAX_TRACKED_OPTIONS - (1 if self.draft else 0)
        for tracked in tracked_messages[:max_options]:
            self.add_item(_TrackedMessageSelectButton(tracked))

    async def open_link_selector(self, interaction: Interaction, target_message_id: int | None) -> None:
        """Open the link workflow for the selected target.

        Args:
            interaction (Interaction): The Discord interaction context.
            target_message_id (int | None): The ID of the target message (None = draft).
        """
        assert interaction.guild is not None

        if self.emoji is None:
            await interaction.response.send_message(ErrorMessages.INVALID_EMOJI, ephemeral=True)
            return

        if target_message_id is None and self.draft is None:
            await interaction.response.send_message(ErrorMessages.MSG_NO_DRAFT, ephemeral=True)
            return

        target_label = "le brouillon" if target_message_id is None else f"le message `{target_message_id}`"
        view = _ReactionActionTypeSelectView(self.cog, interaction.guild.id, target_message_id, self.emoji)
        await interaction.response.edit_message(
            content=f"Choisis le type d'action pour {target_label} puis la cible.",
            view=view,
        )

    async def open_unlink_selector(self, interaction: Interaction, tracked: TrackedMessage | MessageDraft) -> None:
        """Open the reaction unlink view for the selected target.

        Args:
            interaction (Interaction): The Discord interaction context.
            tracked (TrackedMessage | MessageDraft): The target for which to open the unlink selector.
        """
        if not tracked.reactions:
            await interaction.response.edit_message(content="Aucune action configurée sur ce message.", view=None)
            return

        target_label = f"message `{tracked.message_id}`" if isinstance(tracked, TrackedMessage) else "brouillon"
        view = _UnlinkReactionSelectView(self.cog, tracked)
        await interaction.response.edit_message(
            content=f"Sélectionne l'action à retirer pour le {target_label}.",
            view=view,
        )

    async def export_history(self, interaction: Interaction, tracked: TrackedMessage) -> None:
        """Export reaction history for a tracked message.

        Args:
            interaction (Interaction): The Discord interaction context.
            tracked (TrackedMessage): The tracked message to export history for.
        """
        assert interaction.guild is not None
        assert self.followup_message_id is not None
        await interaction.response.defer()

        file, content = self.cog.build_reaction_export(interaction.guild.id, tracked.message_id)
        if file is None:
            await interaction.followup.edit_message(self.followup_message_id, content=content, view=None)
        else:
            await interaction.followup.edit_message(self.followup_message_id, content=content, attachments=[file], view=None)

        await interaction.delete_original_response()

    async def stop_tracking(self, interaction: Interaction, tracked: TrackedMessage) -> None:
        """Remove tracking for the selected message.

        Args:
            interaction (Interaction): The Discord interaction context.
            tracked (TrackedMessage): The tracked message to stop tracking.
        """
        assert interaction.guild is not None
        assert self.followup_message_id is not None
        await interaction.response.defer()

        if not self.cog.remove_tracked_message(interaction.guild.id, tracked.message_id):
            logger.error(
                f"Tracked message {tracked.message_id} not found in guild {interaction.guild.id} during stop tracking.",
            )
            await interaction.response.edit_message(content="Suivi introuvable pour ce message.", view=None)
            return

        logger.info(f"Stopped tracking message {tracked.message_id} in guild {interaction.guild.id}")
        await interaction.followup.edit_message(
            self.followup_message_id,
            content=f"Suivi arrêté pour le message `{tracked.message_id}`.",
            view=None,
        )


# endregion TrackedMessageSelectView and its Components

# region ====== UnlinkReaction UI Components ======


class _UnlinkReactionButton(ui.Button["_UnlinkReactionSelectView"]):  # TODO: review
    """Button to remove a specific reaction action."""

    def __init__(self, reaction_index: int, *, label: str, description: str) -> None:
        super().__init__(label=label, style=ButtonStyle.danger, custom_id=f"unlink_action_{reaction_index}")
        self.reaction_index = reaction_index
        self.description = description

    async def callback(self, interaction: Interaction) -> None:
        view = self.view
        if view is None:
            await interaction.response.send_message(ErrorMessages.EXPIRED_VIEW_MESSAGE, ephemeral=True)
            return

        assert isinstance(view, _UnlinkReactionSelectView)
        guild = interaction.guild
        assert guild is not None

        if self.reaction_index >= len(view.target.reactions):
            await interaction.response.send_message("Action introuvable.", ephemeral=True)
            return

        removed = view.target.reactions.pop(self.reaction_index)
        if isinstance(view.target, MessageDraft):
            view.cog.set_draft(guild.id, view.target)
            target_label = "le brouillon"
        else:
            view.cog.set_tracked_message(guild.id, view.target)
            target_label = f"le message {view.target.message_id}"

            if removed.emoji not in [ev.emoji for ev in view.target.reactions]:
                channel = await guild.fetch_channel(view.target.channel_id)
                if not isinstance(channel, TextChannel):
                    return
                try:
                    msg = await channel.fetch_message(view.target.message_id)
                except Exception:
                    logger.exception(f"Tracked message {view.target.message_id} no longer accessible.")
                    return
                await msg.remove_reaction(removed.emoji, view.cog.bot.user)
        logger.info(f"Removed reaction action {removed.emoji} ({removed.action_type}) on {target_label} in guild {guild.id}")

        await interaction.response.edit_message(
            content=f"Action retirée de {target_label} : {removed.emoji} : {view.cog.format_reaction_action(removed)}",
            view=None,
        )


class _UnlinkReactionSelectView(ui.View):  # TODO: review, see preview
    """View to pick which reaction action to remove from a tracked message."""

    def __init__(self, cog: MessageManagement, target: TrackedMessage | MessageDraft) -> None:
        super().__init__(timeout=None)
        self.cog = cog
        self.target = target

        for idx, reaction in enumerate(target.reactions[:MAX_TRACKED_OPTIONS], start=1):
            action_label = self.cog.format_reaction_action(reaction, show_label=True)
            preview = reaction.message_content
            if len(preview) > 60:
                preview = f"{preview[:57]}..."

            self.add_item(
                _UnlinkReactionButton(
                    idx - 1,
                    label=f"{idx}. {reaction.emoji} - {action_label}"[:80],
                    description=preview,
                ),
            )


# endregion UnlinkReaction UI Components

# region ====== Suggestion UI Components ======


class _SuggestionModal(ui.Modal, title="Envoyer une suggestion"):
    """Modal for submitting a suggestion."""

    suggestion_input: ui.TextInput = ui.TextInput(
        label="Votre suggestion",
        style=TextStyle.long,
        placeholder="Décrivez votre suggestion...",
        required=True,
        max_length=2000,
    )

    def __init__(
        self,
        cog: MessageManagement,
        recipient_key: str,
        *,
        anonymous_flag: bool,
    ) -> None:
        """Initialize the SuggestionModal.

        Args:
            cog (MessageManagement): The MessageManagement cog instance.
            recipient_key (str): The key of the recipient configuration.
            anonymous_flag (bool): Whether the suggestion is anonymous.
        """
        super().__init__()
        self.cog = cog
        self.recipient_key = recipient_key
        self.anonymous_flag = anonymous_flag

    async def on_submit(self, interaction: Interaction) -> None:
        """Handle modal submission.

        Args:
            interaction (Interaction): The interaction context.
        """
        config = SUGGESTION_OPTIONS[self.recipient_key]
        suggestion_text = self.suggestion_input.value
        logger.info(
            f"[{config.command_name}]: user={interaction.user if not self.anonymous_flag else 'Anonymous'!r} "
            f"suggestion_text={suggestion_text!r}",
        )

        assert interaction.guild is not None
        author = interaction.user

        embed = Embed(
            title=config.embed_title,
            description=suggestion_text,
            color=config.embed_color,
            timestamp=datetime.now(PARIS_TZ),
        )
        if not self.anonymous_flag:
            embed.set_author(name=author.display_name, icon_url=author.display_avatar.url)
            embed.add_field(name="Utilisateur·ice", value=author.mention, inline=False)
        else:
            embed.set_author(
                name="Anonymous",
                icon_url=ANONYMOUS_ICON_URL,
            )

        success = await self._send_suggestion(interaction.guild, embed, config, author.id if not self.anonymous_flag else None)

        if not success:
            await interaction.response.send_message(config.error_message, ephemeral=True)
            return

        logger.info(
            f"Guild {interaction.guild.id} user "
            f"{interaction.user.id if not self.anonymous_flag else 'Anonymous'} made a suggestion via {config.command_name}.",
        )

        await interaction.response.edit_message(content=config.success_message, view=None)

    @staticmethod
    async def _send_suggestion(guild: Guild, embed: Embed, config: SuggestionConfig, user_id: int | None) -> bool:
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


class SuggestionView(ui.View):
    """Initial view for submitting a suggestion."""

    def __init__(self, cog: MessageManagement) -> None:
        """Initialize the InitialView.

        Args:
            cog (MessageManagement): The MessageManagement cog instance.
        """
        super().__init__(timeout=None)
        self.cog = cog
        self.selected_recipient: str | None = None
        self.anonymous: bool = False

    @ui.select(
        placeholder="Choisir le destinataire...",
        options=[SelectOption(label=config.label, value=key) for key, config in SUGGESTION_OPTIONS.items()],
    )
    async def recipient_select(self, interaction: Interaction, select: ui.Select) -> None:
        """Handle recipient selection.

        Args:
            interaction (Interaction): The interaction context.
            select (ui.Select): The select menu instance.
        """
        self.selected_recipient = select.values[0]
        await interaction.response.defer(ephemeral=True)

    @ui.button(label="Masquer mon nom", style=ButtonStyle.secondary)
    async def anonymity_button(self, interaction: Interaction, button: ui.Button) -> None:
        """Handle anonymity toggle.

        Args:
            interaction (Interaction): The interaction context.
            button (ui.Button): The button instance.
        """
        self.anonymous = not self.anonymous
        button.label = "Afficher mon nom" if self.anonymous else "Masquer mon nom"
        await interaction.response.edit_message(view=self)

    @ui.button(label="Rédiger la suggestion", style=ButtonStyle.primary)
    async def open_modal_button(self, interaction: Interaction, _button: ui.Button) -> None:
        """Handle button click to open the suggestion modal.

        Args:
            interaction (Interaction): The interaction context.
            _button (ui.Button): The button instance.
        """
        if self.selected_recipient is None:
            await interaction.response.send_message("Veuillez d'abord choisir le destinataire.", ephemeral=True)
            return

        assert interaction.message is not None

        modal = _SuggestionModal(
            cog=self.cog,
            recipient_key=self.selected_recipient,
            anonymous_flag=self.anonymous,
        )
        await interaction.response.send_modal(modal)


# endregion Suggestion UI Components
