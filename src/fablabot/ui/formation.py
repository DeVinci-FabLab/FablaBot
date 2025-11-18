"""User interface components for formation-related features."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from discord import Embed, Interaction, TextStyle, ui

from fablabot.helpers.formation import Emojis, render_message
from fablabot.helpers.utils import log_request
from fablabot.models.formation import FmMessageDraft, Formation

if TYPE_CHECKING:
    from fablabot.cogs import FormationManagement

logger = logging.getLogger(__name__)


class StartFmModal(ui.Modal, title="Commencer une annonce de formation"):
    """Modal to start a new formation message draft.

    Attributes:
        intro_input (ui.TextInput): Text input for the introduction.
        end_input (ui.TextInput): Text input for the conclusion.
    """

    intro_input: ui.TextInput = ui.TextInput(
        label="Intro",
        style=TextStyle.paragraph,
        placeholder="Entrez l'introduction...",
        required=True,
        max_length=300,
    )
    end_input: ui.TextInput = ui.TextInput(
        label="Conclusion",
        style=TextStyle.paragraph,
        placeholder="Entrez la conclusion...",
        required=True,
        max_length=200,
    )

    def __init__(self, cog: FormationManagement, role_id: int, *args, **kwargs) -> None:
        """Initialize the StartFmModal.

        Args:
            cog (FormationManagement): FormationManagement cog instance.
            role_id (int): Role ID to mention in the formation message.
            *args: Additional positional arguments.
            **kwargs: Additional keyword arguments.
        """
        super().__init__(*args, **kwargs)
        self.cog = cog
        self.role_id = role_id

    async def on_submit(self, interaction: Interaction) -> None:
        """Called when the modal is submitted.

        Args:
            interaction (Interaction): The interaction that triggered the modal submission.
        """
        log_request(
            logger,
            "fm.start",
            interaction,
            role_id=self.role_id,
            intro_input=self.intro_input.value,
            end_input=self.end_input.value,
        )
        assert interaction.guild is not None
        emoji_set = {emoji for emoji in interaction.guild.emojis if emoji.name == "dvfl"}
        emoji = emoji_set.pop() if emoji_set else Emojis.LOUDSPEAKER
        header = f"# [FORMATIONS] {emoji}"

        intro_body = self.intro_input.value.strip()
        end_body = self.end_input.value.strip()

        draft = FmMessageDraft(
            header=header,
            role_id=self.role_id,
            intro=intro_body,
            fms=[],
            end=end_body,
        )
        self.cog.set_guild_draft(interaction.guild.id, draft)

        content = render_message(
            draft.header,
            draft.role_id,
            draft.intro,
            draft.fms,
            draft.end,
        )
        logger.info(f"Guild {interaction.guild.id} started a new formations draft.")
        await interaction.response.send_message(
            "Brouillon initialisé.\nUtilise **/fm add** pour ajouter des formations. **/fm preview** pour voir le rendu.",
            embed=Embed(
                title="Aperçu brouillon — 0 formation",
                description=f"{content or '_(vide)_'}",
            ),
            ephemeral=True,
        )


class EditTextModal(ui.Modal, title="Modifier le texte de l'annonce de formation"):
    """Modal to edit the introduction and conclusion of a formation message draft.

    Attributes:
        intro_input (ui.TextInput): Text input for the introduction.
        end_input (ui.TextInput): Text input for the conclusion.
    """

    intro_input: ui.TextInput = ui.TextInput(
        label="Intro",
        style=TextStyle.paragraph,
        placeholder="Entrez la nouvelle introduction...",
        required=True,
        max_length=300,
    )
    end_input: ui.TextInput = ui.TextInput(
        label="Conclusion",
        style=TextStyle.paragraph,
        placeholder="Entrez la nouvelle conclusion...",
        required=True,
        max_length=200,
    )

    def __init__(
        self,
        cog: FormationManagement,
        role_id: int,
        intro: str,
        end: str,
        *args,
        **kwargs,
    ) -> None:
        """Initialize the StartFmModal.

        Args:
            cog (FormationManagement): FormationManagement cog instance.
            role_id (int): Role ID to mention in the formation message.
            intro (str): Old introduction text.
            end (str): Old conclusion text.
            *args: Additional positional arguments.
            **kwargs: Additional keyword arguments.
        """
        super().__init__(*args, **kwargs)
        self.cog = cog
        self.role_id = role_id
        self.intro_input.default = intro
        self.end_input.default = end

    async def on_submit(self, interaction: Interaction) -> None:
        """Called when the modal is submitted.

        Args:
            interaction (Interaction): The interaction that triggered the modal submission.
        """
        log_request(
            logger,
            "fm.edit_text",
            interaction,
            role_id=self.role_id,
            intro_input=self.intro_input.value,
            end_input=self.end_input.value,
        )
        assert interaction.guild is not None
        draft = self.cog.get_guild_draft(interaction.guild.id)

        current_intro = draft.intro
        current_end = draft.end
        current_role_id = draft.role_id

        updated_intro = self.intro_input.value.strip()
        updated_end = self.end_input.value.strip()

        if updated_intro == current_intro and updated_end == current_end and self.role_id == current_role_id:
            await interaction.response.send_message("Aucune modification détectée.", ephemeral=True)
            return

        draft = FmMessageDraft(
            header=draft.header,
            role_id=self.role_id,
            intro=updated_intro,
            fms=draft.fms,
            end=updated_end,
        )
        self.cog.set_guild_draft(interaction.guild.id, draft)

        content = render_message(
            draft.header,
            draft.role_id,
            draft.intro,
            draft.fms,
            draft.end,
        )

        role_changed = self.role_id != current_role_id
        intro_changed = updated_intro != current_intro
        end_changed = updated_end != current_end
        logger.info(
            f"Guild {interaction.guild.id} updated draft intro/end "
            f"(role_changed={role_changed}, intro_changed={intro_changed}, end_changed={end_changed}).",
        )

        await interaction.response.send_message(
            "Brouillon mis à jour.",
            embed=Embed(
                title=f"Aperçu brouillon — {len(draft.fms)} formation(s)",
                description=f"{content or '_(vide)_'}",
            ),
            ephemeral=True,
        )


class AddFmModal(ui.Modal, title="Ajouter une formation"):
    """Modal to add a new formation to the draft.

    Attributes:
        name_input (ui.TextInput): Text input for the formation name.
        description_input (ui.TextInput): Text input for the description (optional).
    """

    name_input: ui.TextInput = ui.TextInput(
        label="Nom de la formation",
        style=TextStyle.short,
        placeholder="ex: Formation Arduino",
        required=True,
        max_length=100,
    )
    description_input: ui.TextInput = ui.TextInput(
        label="Description (optionnel)",
        style=TextStyle.paragraph,
        placeholder="Description de la formation...",
        required=False,
        max_length=500,
    )

    def __init__(
        self,
        cog: FormationManagement,
        emoji: str,
        trainer_mention: str,
        start_iso: str,
        duration: str,
        seats: int,
        excusable: bool,
        *args,
        **kwargs,
    ) -> None:
        """Initialize the AddFmModal.

        Args:
            cog (FormationManagement): FormationManagement cog instance.
            emoji (str): Pre-filled emoji value.
            trainer_mention (str): The trainer mention for this formation.
            start_iso (str): The start date and time in ISO format.
            duration (str): The duration of the formation.
            seats (int): The number of seats available.
            excusable (bool): Whether absences are excusable for this formation.
            *args: Additional positional arguments.
            **kwargs: Additional keyword arguments.
        """
        super().__init__(*args, **kwargs)
        self.cog = cog
        self.emoji = emoji
        self.trainer_mention = trainer_mention
        self.start_iso = start_iso
        self.duration = duration
        self.seats = seats
        self.excusable = excusable

    async def on_submit(self, interaction: Interaction) -> None:
        """Called when the modal is submitted.

        Args:
            interaction (Interaction): The interaction that triggered the modal submission.
        """
        assert interaction.guild is not None
        draft = self.cog.get_guild_draft(interaction.guild.id)

        description = self.description_input.value.strip() if self.description_input.value else ""
        name = self.name_input.value.strip()

        log_request(
            logger,
            "fm.add",
            interaction,
            name=name,
            description=description,
        )

        fm = Formation(
            emoji=self.emoji,
            name=name,
            trainer_mention=self.trainer_mention,
            start_iso=self.start_iso,
            duration=self.duration,
            seats=self.seats,
            description=description,
            excusable=self.excusable,
        )

        fms = list(draft.fms)
        fms.append(fm)
        fms.sort(key=lambda x: x.start_dt)

        draft = FmMessageDraft(
            header=draft.header,
            role_id=draft.role_id,
            intro=draft.intro,
            fms=fms,
            end=draft.end,
        )
        self.cog.set_guild_draft(interaction.guild.id, draft)

        preview = render_message(
            draft.header,
            draft.role_id,
            draft.intro,
            draft.fms,
            draft.end,
        )
        logger.info(f"Guild {interaction.guild.id} added formation {fm.name!r} ({fm.start_iso}) to draft.")
        await interaction.response.send_message(
            "Formation ajoutée & brouillon mis à jour (trié). "
            "Utilise **/fm add** pour ajouter d'autres formations. **/fm preview** pour voir le rendu.",
            embed=Embed(
                title=f"Aperçu brouillon — {len(fms)} formation(s)",
                description=f"{preview or '_(vide)_'}",
            ),
            ephemeral=True,
        )
