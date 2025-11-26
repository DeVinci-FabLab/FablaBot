"""User interface components for message-related features."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Literal

from discord import ButtonStyle, ChannelType, Interaction, Member, SelectOption, TextStyle, ui

from fablabot.helpers import ErrorMessages, format_member_mention, send_dm_to_member
from fablabot.models.message import SUGGESTION_OPTIONS, MessageDraft, MsgActionType, MsgReactionEvent, TrackedMessage
from fablabot.ui.common import build_preview_embed

if TYPE_CHECKING:
    from fablabot.cogs import MessageManagement, SuggestionManagement

logger = logging.getLogger(__name__)

MAX_TRACKED_OPTIONS = 25


class BulkDMView(ui.View):
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
            embed=build_preview_embed("Aperçu brouillon", draft.content),
            ephemeral=True,
        )


class _ReactionMessageInputModal(ui.Modal, title="Message à envoyer"):
    """Modal for inputting the content of the message to send for a reaction action."""

    message_input: ui.TextInput = ui.TextInput(
        label="Utilise {username} pour le nom du réacteur.",
        style=TextStyle.long,
        placeholder="Entrez le message à envoyer...",
        required=True,
        max_length=2000,
    )

    def __init__(
        self,
        cog: MessageManagement,
        guild_id: int,
        message_id: int | None,
        emoji: str,
        action_type: MsgActionType,
        target_id: int,
        target_value: str,
        followup_id: int,
    ) -> None:
        """Initialize the modal.

        Args:
            cog (MessageManagement): The MessageManagement cog instance.
            guild_id (int): The guild identifier.
            message_id (int | None): The message identifier (None = brouillon).
            emoji (str): The emoji for this reaction.
            action_type (MsgActionType): The type of action.
            target_id (int): The pre-filled target ID.
            target_value (str): The pre-filled target value.
            followup_id (int): The ID of the follow-up message to edit with results.
        """
        super().__init__()
        self.cog = cog
        self.guild_id = guild_id
        self.message_id = message_id
        self.emoji = emoji
        self.action_type: MsgActionType = action_type
        self.target_id = target_id
        self.target_value = target_value
        self.followup_id = followup_id

    async def on_submit(self, interaction: Interaction) -> None:
        """Handle modal submission.

        Args:
            interaction (Interaction): The interaction context.
        """
        message_content = self.message_input.value.strip()
        reaction = MsgReactionEvent(
            emoji=self.emoji,
            action_type=self.action_type,
            message_content=message_content,
            target_id=self.target_id,
            target_name=self.target_value,
            message_id=self.message_id,
        )

        await interaction.response.defer()
        feedback = await self.cog.register_reaction_action(self.guild_id, reaction)
        await interaction.followup.edit_message(self.followup_id, content=feedback, view=None)


class _ReactionTargetView(ui.View):
    """View for selecting the target of a reaction action if it's necessary and the content of the message."""

    def __init__(
        self,
        cog: MessageManagement,
        guild_id: int,
        message_id: int | None,
        emoji: str,
        action_type: Literal["channel", "role_dm"],
        followup_id: int | None,
    ) -> None:
        """Initialize the modal.

        Args:
            cog (MessageManagement): The MessageManagement cog instance.
            guild_id (int): The guild identifier.
            message_id (int): The message identifier.
            emoji (str): The emoji for this reaction.
            action_type (Literal["channel", "role_dm"]): The type of action.
            followup_id (int | None): The ID of the follow-up message to edit with results.
        """
        super().__init__(timeout=None)
        self.cog = cog
        self.guild_id = guild_id
        self.message_id = message_id
        self.emoji = emoji
        self.action_type: MsgActionType = action_type
        self.followup_id = followup_id

        async def _on_select(interaction: Interaction) -> None:
            match action_type:
                case "channel":
                    target_id = self.channel_select.values[0].id
                    target_value = self.channel_select.values[0].name
                case "role_dm":
                    target_id = self.role_select.values[0].id
                    target_value = self.role_select.values[0].name

            followup_message_id = self.followup_id or (interaction.message.id if interaction.message else 0)

            await interaction.response.send_modal(
                _ReactionMessageInputModal(
                    self.cog,
                    self.guild_id,
                    self.message_id,
                    self.emoji,
                    self.action_type,
                    target_id,
                    target_value,
                    followup_message_id,
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


class ReactionActionTypeView(ui.View):
    """View to pick an action type before selecting the reaction target."""

    def __init__(
        self,
        cog: MessageManagement,
        guild_id: int,
        message_id: int | None,
        emoji: str,
        followup_id: int | None,
    ) -> None:
        """Initialize the ReactionActionTypeView.

        Args:
            cog (MessageManagement): The MessageManagement cog instance.
            guild_id (int): The guild identifier.
            message_id (int | None): The message identifier.
            emoji (str): The emoji for this reaction.
            followup_id (int | None): The ID of the follow-up message to edit with results.
        """
        super().__init__(timeout=None)
        self.cog = cog
        self.guild_id = guild_id
        self.message_id = message_id
        self.emoji = emoji
        self.followup_id = followup_id

    async def _open_target_view(self, interaction: Interaction, action_type: Literal["channel", "role_dm"]) -> None:
        target_view = _ReactionTargetView(
            self.cog,
            self.guild_id,
            self.message_id,
            self.emoji,
            action_type,
            self.followup_id or (interaction.message.id if interaction.message else 0),
        )
        await interaction.response.edit_message(
            content="Choisis la cible puis rédige le message envoyé lors de la réaction.",
            view=target_view,
        )

    @ui.button(label="Message dans un salon", style=ButtonStyle.primary)
    async def channel_button(self, interaction: Interaction, _button: ui.Button) -> None:
        """Handle channel button click.

        Args:
            interaction (Interaction): The Discord interaction context.
            _button (ui.Button): The button that was clicked.
        """
        await self._open_target_view(interaction, "channel")

    @ui.button(label="DM la personne", style=ButtonStyle.secondary)
    async def user_dm_button(self, interaction: Interaction, _button: ui.Button) -> None:
        """Handle user DM button click.

        Args:
            interaction (Interaction): The Discord interaction context.
            _button (ui.Button): The button that was clicked.
        """
        followup_message_id = self.followup_id or (interaction.message.id if interaction.message else 0)

        await interaction.response.send_modal(
            _ReactionMessageInputModal(
                self.cog,
                self.guild_id,
                self.message_id,
                self.emoji,
                "user_dm",
                0,
                "",
                followup_message_id,
            ),
        )

    @ui.button(label="DM un rôle", style=ButtonStyle.secondary)
    async def role_dm_button(self, interaction: Interaction, _button: ui.Button) -> None:
        """Handle role DM button click.

        Args:
            interaction (Interaction): The Discord interaction context.
            _button (ui.Button): The button that was clicked.
        """
        await self._open_target_view(interaction, "role_dm")


class _SelectMessageButton(ui.Button[Any]):
    """Button to select a tracked message to unlink a reaction from or stop tracking."""

    def __init__(self, message_id: int) -> None:
        """Initialize the _SelectMessageButton.

        Args:
            message_id (int): The ID of the message to select.
        """
        super().__init__(
            label=f"Message {message_id}",
            style=ButtonStyle.primary,
            custom_id=f"select_msg_{message_id}",
        )
        self.message_id = message_id

    async def callback(self, interaction: Interaction) -> None:
        """Handle button click.

        Args:
            interaction (Interaction): The Discord interaction context.
        """
        view = self.view
        if view is None:
            await interaction.response.send_message(ErrorMessages.EXPIRED_VIEW_MESSAGE, ephemeral=True)
            return

        assert isinstance(view, UnlinkMessageSelectView | StopTrackingSelectView)
        await view.handle_selection(interaction, self.message_id)


class UnlinkMessageSelectView(ui.View):
    """View to pick which tracked message to edit before unlinking a reaction."""

    def __init__(self, cog: MessageManagement, followup_id: int, tracked_messages: list[TrackedMessage]) -> None:
        """Initialize the UnlinkMessageSelectView.

        Args:
            cog (MessageManagement): The MessageManagement cog instance.
            followup_id (int): The ID of the follow-up message to edit with results.
            tracked_messages (list[TrackedMessage]): The list of tracked messages to select from.
        """
        super().__init__(timeout=None)
        self.cog = cog
        self.followup_id = followup_id
        self.tracked_messages = tracked_messages

        for tracked in tracked_messages[:MAX_TRACKED_OPTIONS]:
            self.add_item(_SelectMessageButton(tracked.message_id))

    async def handle_selection(self, interaction: Interaction, message_id: int) -> None:
        """Handle the selection of a tracked message to unlink a reaction from.

        Args:
            interaction (Interaction): The Discord interaction context.
            message_id (int): The ID of the message to unlink a reaction from.
        """
        await interaction.response.defer()
        tracked = next((t for t in self.tracked_messages if t.message_id == message_id), None)
        if tracked is None:
            await interaction.followup.edit_message(self.followup_id, content="Message suivi introuvable.", view=None)
            return
        if not tracked.reactions:
            await interaction.followup.edit_message(
                self.followup_id,
                content="Aucune action configurée sur ce message suivi.",
                view=None,
            )
            return

        view = _UnlinkReactionSelectView(self.cog, self.followup_id, tracked)
        await interaction.followup.edit_message(
            self.followup_id,
            content=f"Sélectionne l'action à retirer pour le message `{tracked.message_id}`.",
            view=view,
        )


class _UnlinkReactionSelectView(ui.View):  # TODO: review
    """View to pick which reaction action to remove from a tracked message."""

    def __init__(self, cog: MessageManagement, followup_id: int, tracked: TrackedMessage) -> None:
        super().__init__(timeout=None)
        self.cog = cog
        self.followup_id = followup_id
        self.tracked = tracked

        for idx, reaction in enumerate(tracked.reactions[:MAX_TRACKED_OPTIONS], start=1):
            action_label = self.cog.format_reaction_action(reaction)
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


class _UnlinkReactionButton(ui.Button[_UnlinkReactionSelectView]):  # TODO: review
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
        assert interaction.guild is not None
        await interaction.response.defer()

        if self.reaction_index >= len(view.tracked.reactions):
            await interaction.response.send_message("Action introuvable.", ephemeral=True)
            return

        removed = view.tracked.reactions.pop(self.reaction_index)
        view.cog.set_tracked_message(interaction.guild.id, view.tracked)
        logger.info(
            f"Removed reaction action {removed.emoji} ({removed.action_type}) on message "
            f"{view.tracked.message_id} in guild {interaction.guild.id}",
        )

        await interaction.followup.edit_message(
            view.followup_id,
            content=f"Action retirée : {removed.emoji} : {view.cog.format_reaction_action(removed)}",
            view=None,
        )


class StopTrackingSelectView(ui.View):  # TODO: review
    """View to pick a tracked message to stop following."""

    def __init__(self, cog: MessageManagement, followup_id: int, tracked_messages: list[TrackedMessage]) -> None:
        """Initialize the StopTrackingSelectView.

        Args:
            cog (MessageManagement): The MessageManagement cog instance.
            followup_id (int): The ID of the follow-up message to edit with results.
            tracked_messages (list[TrackedMessage]): The list of tracked messages to select from.
        """
        super().__init__(timeout=None)
        self.cog = cog
        self.followup_id = followup_id

        for tracked in tracked_messages[:MAX_TRACKED_OPTIONS]:
            self.add_item(_SelectMessageButton(tracked.message_id))

    async def handle_selection(self, interaction: Interaction, message_id: int) -> None:
        """Handle the selection of a tracked message to stop tracking.

        Args:
            interaction (Interaction): The Discord interaction context.
            message_id (int): The ID of the message to stop tracking.
        """
        assert interaction.guild is not None
        await interaction.response.defer()
        if not self.cog.remove_tracked_message(interaction.guild.id, message_id):
            await interaction.followup.edit_message(self.followup_id, content="Suivi introuvable pour ce message.", view=None)
            return

        logger.info(f"Stopped tracking message {message_id} in guild {interaction.guild.id}")
        await interaction.followup.edit_message(
            self.followup_id,
            content=f"Suivi arrêté pour le message `{message_id}`.",
            view=None,
        )


# region ====== Suggestion UI Components ======


class RecipientSelect(ui.Select):
    """Select component for choosing the recipient of a suggestion."""

    def __init__(self, view: InitialView) -> None:
        """Initialize the RecipientSelect.

        Args:
            view (InitialView): The parent view.
        """
        options = [SelectOption(label=config.label, value=key) for key, config in SUGGESTION_OPTIONS.items()]
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

    def __init__(
        self,
        cog: SuggestionManagement,
        initial_view_followup_id: int,
        recipient_key: str,
        *,
        anonymous_flag: bool,
    ) -> None:
        """Initialize the SuggestionModal.

        Args:
            cog (SuggestionManagement): The SuggestionManagement cog instance.
            initial_view_followup_id (int): The ID of the initial view message to update.
            recipient_key (str): The key of the recipient configuration.
            anonymous_flag (bool): Whether the suggestion is anonymous.
        """
        super().__init__()
        self.cog = cog
        self.initial_view_followup_id = initial_view_followup_id
        self.recipient_key = recipient_key
        self.anonymous_flag = anonymous_flag

    async def on_submit(self, interaction: Interaction) -> None:
        """Handle modal submission.

        Args:
            interaction (Interaction): The interaction context.
        """
        config = SUGGESTION_OPTIONS[self.recipient_key]
        await self.cog.handle_suggestion(interaction, self.suggestion_input.value, config, anonymous=self.anonymous_flag)

        await interaction.followup.edit_message(self.initial_view_followup_id, content=config.success_message, view=None)


class OpenModalButton(ui.Button):
    def __init__(self, view: InitialView, cog: SuggestionManagement) -> None:
        """Initialize the OpenModalButton.

        Args:
            view (InitialView): The parent view.
            cog (SuggestionManagement): The SuggestionManagement cog instance.
        """
        super().__init__(label="Rédiger la suggestion", style=ButtonStyle.primary)
        self.view_ref = view
        self.cog = cog

    async def callback(self, interaction: Interaction) -> None:
        """Handle button click.

        Args:
            interaction (Interaction): The interaction context.
        """
        recipient = getattr(self.view_ref, "selected_recipient", None)
        if recipient is None:
            await interaction.response.send_message("Veuillez d'abord choisir le destinataire.", ephemeral=True)
            return

        assert interaction.message is not None
        followup_id = interaction.message.id

        modal = SuggestionModal(self.cog, followup_id, recipient, anonymous_flag=getattr(self.view_ref, "anonymous", False))
        await interaction.response.send_modal(modal)


class InitialView(ui.View):
    """Initial view for submitting a suggestion."""

    def __init__(self, cog: SuggestionManagement) -> None:
        """Initialize the InitialView.

        Args:
            cog (SuggestionManagement): The SuggestionManagement cog instance.
        """
        super().__init__(timeout=None)
        self.selected_recipient: str | None = None
        self.anonymous: bool = False
        self.add_item(RecipientSelect(self))
        self.add_item(AnonymitySelect(self))
        self.add_item(OpenModalButton(self, cog))


# endregion Suggestion UI Components
