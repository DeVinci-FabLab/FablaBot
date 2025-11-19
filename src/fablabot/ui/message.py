"""User interface components for message-related features."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Literal

from discord import ButtonStyle, Interaction, Member, SelectOption, TextStyle, ui
from discord.utils import get

from fablabot.helpers.utils import format_member_mention, send_dm_to_member
from fablabot.models.message import SUGGESTION_OPTIONS, ReactionAction

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


class ReactionTargetModal(ui.Modal):
    """Modal for selecting the target of a reaction action."""

    def __init__(
        self,
        cog: MessageManagement,
        guild_id: str,
        emoji: str,
        action_type: Literal["channel", "user_dm", "role_dm"],
        message_content: str,
    ) -> None:
        """Initialize the modal.

        Args:
            cog (MessageManagement): The MessageManagement cog instance.
            guild_id (str): The guild ID.
            emoji (str): The emoji for this reaction.
            action_type (Literal["channel", "user_dm", "role_dm"]): The type of action.
            message_content (str): The message to send.
        """
        super().__init__(title=f"Configuration de la réaction {emoji}")
        self.cog = cog
        self.guild_id = guild_id
        self.emoji = emoji
        self.action_type: Literal["channel", "user_dm", "role_dm"] = action_type
        self.message_content = message_content

        target_label = {
            "channel": "Nom du salon (ex: général)",
            "user_dm": "ID de l'utilisateur",
            "role_dm": "Nom du rôle",
        }

        self.target_input: ui.TextInput[Any] = ui.TextInput(
            label=target_label.get(action_type, "Cible"),
            placeholder={
                "channel": "général",
                "user_dm": "123456789012345678",
                "role_dm": "Membre",
            }.get(action_type, ""),
            required=True,
            max_length=100,
        )
        self.add_item(self.target_input)

    async def on_submit(self, interaction: Interaction) -> None:
        """Handle modal submission.

        Args:
            interaction (Interaction): The interaction context.
        """
        assert interaction.guild is not None
        target_value = self.target_input.value.strip()

        target_id: int | None = None
        target_name: str | None = None

        if self.action_type == "channel":
            channel = get(interaction.guild.text_channels, name=target_value)
            if not channel:
                await interaction.response.send_message(
                    f"❌ Salon `{target_value}` introuvable.",
                    ephemeral=True,
                )
                return
            target_id = channel.id
            target_name = channel.name

        elif self.action_type == "user_dm":
            try:
                user_id = int(target_value)
                member = interaction.guild.get_member(user_id)
                if not member:
                    await interaction.response.send_message(
                        f"❌ Utilisateur avec ID `{target_value}` introuvable.",
                        ephemeral=True,
                    )
                    return
                target_id = user_id
                target_name = member.display_name
            except ValueError:
                await interaction.response.send_message(
                    f"❌ ID utilisateur invalide : `{target_value}`",
                    ephemeral=True,
                )
                return

        elif self.action_type == "role_dm":
            role = get(interaction.guild.roles, name=target_value)
            if not role:
                await interaction.response.send_message(
                    f"❌ Rôle `{target_value}` introuvable.",
                    ephemeral=True,
                )
                return
            target_id = role.id
            target_name = role.name

        draft = self.cog.get_draft(self.guild_id)
        if draft is None:
            await interaction.response.send_message(
                "❌ Le brouillon a été supprimé.",
                ephemeral=True,
            )
            return

        reaction = ReactionAction(
            emoji=self.emoji,
            action_type=self.action_type,
            message_content=self.message_content,
            target_id=target_id,
            target_name=target_name,
        )
        draft.reactions.append(reaction)
        self.cog.set_draft(self.guild_id, draft)

        action_desc = {
            "channel": f"message dans #{target_name}",
            "user_dm": f"MP à {target_name}",
            "role_dm": f"MP aux membres du rôle @{target_name}",
        }

        await interaction.response.send_message(
            f"✅ Réaction {self.emoji} ajoutée avec succès !\n"
            f"Action : {action_desc.get(self.action_type, self.action_type)}\n"
            f"Message : `{self.message_content[:100]}{'...' if len(self.message_content) > 100 else ''}`\n\n"
            f"Utilisez `/message preview` pour voir le brouillon complet.",
            ephemeral=True,
        )


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

    def __init__(self, cog: SuggestionManagement, recipient_key: str, anonymous_flag: bool) -> None:
        super().__init__()
        self.cog = cog
        self.recipient_key = recipient_key
        self.anonymous_flag = anonymous_flag

    async def on_submit(self, interaction: Interaction) -> None:
        config = SUGGESTION_OPTIONS[self.recipient_key]
        await self.cog.handle_suggestion(interaction, self.suggestion_input.value, config, anonymous=self.anonymous_flag)


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
