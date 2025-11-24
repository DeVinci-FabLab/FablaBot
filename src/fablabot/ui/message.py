"""User interface components for message-related features."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Literal

from discord import ButtonStyle, ChannelType, Embed, Interaction, Member, SelectOption, TextStyle, ui

from fablabot.helpers.utils import format_member_mention, send_dm_to_member
from fablabot.models.message import SUGGESTION_OPTIONS, MsgActionType, MsgReactionEvent

if TYPE_CHECKING:
    from fablabot.cogs import MessageManagement, SuggestionManagement

logger = logging.getLogger(__name__)


class BulkDMView(ui.View):
    """View for bulk direct message sending."""

    def __init__(self, sender: Member, followup_id: int, message: str) -> None:
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


class StartMessageModal(ui.Modal, title="Commencer une annonce"):
    """Modal to start a new message draft.

    Attributes:
        text_input (ui.TextInput): Text input for the message content.
    """

    text_input: ui.TextInput = ui.TextInput(
        label="Message",
        style=TextStyle.long,
        placeholder="Entrez le message...",
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
        """Called when the modal is submitted.

        Args:
            interaction (Interaction): The interaction that triggered the modal submission.
        """
        log_request(logger, "message.start", interaction, text_input=self.text_input.value)

        content = self.text_input.value.strip()

        assert interaction.guild is not None
        guild_id = str(interaction.guild.id)

        draft = MessageDraft(content=content, reactions=[])
        self.cog.set_draft(guild_id, draft)

        await interaction.response.send_message(
            "Brouillon initialisé.\nUtilise **/message link_reaction** pour lier des réactions à des actions. "
            "**/message preview** pour voir le rendu.",
            embed=Embed(
                title="Aperçu brouillon",
                description=f"{content or '_(vide)_'}",
            ),
            ephemeral=True,
        )
class _ReactionMessageInputModal(ui.Modal, title="Message à envoyer"):
    """Modal for inputting the content of the message to send for a reaction action."""

    message_input: ui.TextInput = ui.TextInput(
        label="Utilise {username} et {user} pour le nom du réacteur.",
        style=TextStyle.long,
        placeholder="Entrez le message à envoyer...",
        required=True,
        max_length=2000,
    )

    def __init__(
        self,
        cog: MessageManagement,
        guild_id: int,
        message_id: int,
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
            message_id (int): The message identifier.
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
        )

        await interaction.response.defer()
        feedback = await self.cog.register_reaction_action(self.guild_id, self.message_id, reaction)
        await interaction.followup.edit_message(self.followup_id, content=feedback, view=None)


class ReactionTargetView(ui.View):
    """View for selecting the target of a reaction action if it's necessary and the content of the message."""

    def __init__(
        self,
        cog: MessageManagement,
        guild_id: int,
        message_id: int,
        emoji: str,
        action_type: MsgActionType,
        followup_id: int,
    ) -> None:
        """Initialize the modal.

        Args:
            cog (MessageManagement): The MessageManagement cog instance.
            guild_id (int): The guild identifier.
            message_id (int): The message identifier.
            emoji (str): The emoji for this reaction.
            action_type (MsgActionType): The type of action.
            followup_id (int): The ID of the follow-up message to edit with results.
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
                case _:
                    target_id = 0
                    target_value = ""

            await interaction.response.send_modal(
                _ReactionMessageInputModal(
                    self.cog,
                    self.guild_id,
                    self.message_id,
                    self.emoji,
                    self.action_type,
                    target_id,
                    target_value,
                    self.followup_id,
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
            case "user_dm":
                self.continue_button: ui.Button[Any] = ui.Button(label="Écrire le message à envoyer", style=ButtonStyle.primary)
                self.continue_button.callback = _on_select  # type: ignore[method-assign]
                self.add_item(self.continue_button)


# region ====== Suggestion UI Components ======


class RecipientSelect(ui.Select):
    def __init__(self, view: InitialView) -> None:
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
    def __init__(self, cog: SuggestionManagement) -> None:
        super().__init__(timeout=300)
        self.selected_recipient: str | None = None
        self.anonymous: bool = False
        self.add_item(RecipientSelect(self))
        self.add_item(AnonymitySelect(self))
        self.add_item(OpenModalButton(self, cog))


# endregion Suggestion UI Components
