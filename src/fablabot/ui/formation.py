"""User interface components for formation-related features."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from discord import ButtonStyle, Embed, Interaction, TextStyle, ui

from fablabot.helpers.constants import PARIS_TZ, ErrorMessages
from fablabot.helpers.formation import Emojis, parse_date_time, render_message
from fablabot.helpers.utils import is_valid_emoji, log_request
from fablabot.models.formation import FmMessageDraft, Formation

if TYPE_CHECKING:
    from fablabot.cogs import FormationManagement

logger = logging.getLogger(__name__)

EDIT_EMOJI_BUTTON_ID = 13
MAKE_EXCUSABLE_BUTTON_ID = 14
MAKE_NON_EXCUSABLE_BUTTON_ID = 15


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

        description = self.description_input.value.strip()
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


class SelectFormationButton(ui.Button):
    """Button to select a formation to edit."""

    def __init__(self, formation_index: int, formation: Formation, *args, **kwargs) -> None:
        """Initialize the SelectFormationButton.

        Args:
            formation_index (int): The index of the formation (1-based).
            formation (Formation): The formation object.
            *args: Additional positional arguments.
            **kwargs: Additional keyword arguments.
        """
        super().__init__(
            label=f"{formation.emoji} {formation.name}",
            style=ButtonStyle.primary,
            custom_id=f"select_fm_{formation_index}",
            *args,
            **kwargs,
        )
        self.formation_index = formation_index
        self.formation = formation

    async def callback(self, interaction: Interaction) -> None:
        """Called when the button is clicked.

        Args:
            interaction (Interaction): The interaction that triggered the button click.
        """
        view: SelectFormationView = self.view  # type: ignore[assignment]
        await interaction.response.send_message(
            f"Sélection: {self.formation.emoji} {self.formation.name}",
            view=EditFormationView(view.cog, self.formation_index, self.formation),
            ephemeral=True,
        )


class SelectFormationView(ui.View):
    """View to select which formation to edit."""

    def __init__(self, cog: FormationManagement, formations: list[Formation], *args, **kwargs) -> None:
        """Initialize the SelectFormationView.

        Args:
            cog (FormationManagement): FormationManagement cog instance.
            formations (list[Formation]): List of formations to choose from.
            *args: Additional positional arguments.
            **kwargs: Additional keyword arguments.
        """
        super().__init__(*args, **kwargs)
        self.cog = cog

        for i, fm in enumerate(formations[:25], start=1):
            button = SelectFormationButton(i, fm)
            self.add_item(button)


class EditNameDescriptionModal(ui.Modal, title="Modifier nom et description"):
    """Modal to edit formation name and description.

    Attributes:
        name_input (ui.TextInput): Text input for the formation name.
        description_input (ui.TextInput): Text input for the description.
    """

    name_input: ui.TextInput = ui.TextInput(
        label="Nom de la formation",
        style=TextStyle.short,
        placeholder="ex: Formation Arduino",
        required=True,
        max_length=100,
    )
    description_input: ui.TextInput = ui.TextInput(
        label="Description",
        style=TextStyle.paragraph,
        placeholder="Description de la formation...",
        required=False,
        max_length=500,
    )

    def __init__(self, view: EditFormationView, *args, **kwargs) -> None:
        """Initialize the EditNameDescriptionModal.

        Args:
            view (EditFormationView): The parent view.
            *args: Additional positional arguments.
            **kwargs: Additional keyword arguments.
        """
        super().__init__(*args, **kwargs)
        self.view_ref = view

        self.name_input.default = view.current_name
        self.description_input.default = view.current_description

    async def on_submit(self, interaction: Interaction) -> None:
        """Called when the modal is submitted.

        Args:
            interaction (Interaction): The interaction that triggered the modal submission.
        """
        self.view_ref.current_name = self.name_input.value.strip()
        self.view_ref.current_description = self.description_input.value.strip()

        await interaction.response.edit_message(
            content=f"Modification: {self.view_ref.original.emoji} {self.view_ref.current_name}",
            view=self.view_ref,
        )


class EditFormationView(ui.View):
    """View to edit a formation with selects and an optional modal for name/description."""

    def __init__(self, cog: FormationManagement, formation_index: int, original: Formation, *args, **kwargs) -> None:
        """Initialize the EditFormationView.

        Args:
            cog (FormationManagement): FormationManagement cog instance.
            formation_index (int): The index of the formation (1-based).
            original (Formation): The original formation being edited.
            *args: Additional positional arguments.
            **kwargs: Additional keyword arguments.
        """
        super().__init__(*args, **kwargs)
        self.cog = cog
        self.formation_index = formation_index
        self.original = original

        self.current_emoji = original.emoji
        self.current_name = original.name
        self.current_description = original.description
        self.current_trainer_mention = original.trainer_mention
        self.current_datetime = f"{original.start_dt.strftime('%d/%m/%Y')} {original.start_dt.strftime('%H:%M')}"
        self.current_duration_seats = f"{original.duration} - {original.seats}"
        self.current_excusable = original.excusable

        edit_emoji_button = self.find_item(EDIT_EMOJI_BUTTON_ID)
        assert isinstance(edit_emoji_button, ui.Button)
        edit_emoji_button.label = f"Émoji: {self.current_emoji}"

        if self.current_excusable:
            make_excusable_button = self.find_item(MAKE_EXCUSABLE_BUTTON_ID)
            assert isinstance(make_excusable_button, ui.Button)
            make_excusable_button.disabled = True
        else:
            make_non_excusable_button = self.find_item(MAKE_NON_EXCUSABLE_BUTTON_ID)
            assert isinstance(make_non_excusable_button, ui.Button)
            make_non_excusable_button.disabled = True

    @ui.button(label="Modifier émoji", style=ButtonStyle.secondary, row=0, id=EDIT_EMOJI_BUTTON_ID)
    async def edit_emoji_button(self, interaction: Interaction, button: ui.Button) -> None:
        """Button to edit emoji.

        Args:
            interaction (Interaction): The interaction that triggered the button click.
            button (ui.Button): The button that was clicked.
        """
        # TODO: avec view textinput

    @ui.button(label="Modifier nom & description", style=ButtonStyle.secondary, row=0)
    async def edit_name_desc_button(self, interaction: Interaction, _button: ui.Button) -> None:
        """Button to open modal for editing name and description.

        Args:
            interaction (Interaction): The interaction that triggered the button click.
            button (ui.Button): The button that was clicked.
        """
        await interaction.response.send_modal(EditNameDescriptionModal(self))

    @ui.button(label="Modifier lae formateur·ice", style=ButtonStyle.secondary, row=1)
    async def edit_trainer_button(self, interaction: Interaction, button: ui.Button) -> None:
        """Button to open modal for editing name and description.

        Args:
            interaction (Interaction): The interaction that triggered the button click.
            button (ui.Button): The button that was clicked.
        """
        # TODO: avec view member selector

    @ui.button(label="Date & Heure", style=ButtonStyle.secondary, row=2)
    async def edit_datetime_button(self, interaction: Interaction, _button: ui.Button) -> None:
        """Button to edit datetime.

        Args:
            interaction (Interaction): The interaction that triggered the button click.
            button (ui.Button): The button that was clicked.
        """
        modal = ui.Modal(title="Modifier date et heure")
        datetime_input: ui.TextInput[Any] = ui.TextInput(
            label="Date et heure (DD/MM/YYYY HH:MM)",
            style=TextStyle.short,
            placeholder="ex: 15/12/2024 18:30",
            default=self.current_datetime,
            required=True,
            max_length=16,
        )
        modal.add_item(datetime_input)

        async def on_submit_datetime(interaction_modal: Interaction) -> None:
            self.current_datetime = datetime_input.value.strip()
            await interaction_modal.response.edit_message(
                content=f"Modification: {self.current_emoji} {self.current_name}",
                view=self,
            )

        modal.on_submit = on_submit_datetime  # type: ignore[method-assign]
        await interaction.response.send_modal(modal)

    @ui.button(label="Durée & Places", style=ButtonStyle.secondary, row=2)
    async def edit_duration_seats_button(self, interaction: Interaction, _button: ui.Button) -> None:
        """Button to edit duration and seats.

        Args:
            interaction (Interaction): The interaction that triggered the button click.
            button (ui.Button): The button that was clicked.
        """
        modal = ui.Modal(title="Modifier durée et places")
        duration_seats_input: ui.TextInput[Any] = ui.TextInput(
            label="Durée - Places",
            style=TextStyle.short,
            placeholder="ex: 2h - 10",
            default=self.current_duration_seats,
            required=True,
            max_length=20,
        )
        modal.add_item(duration_seats_input)

        async def on_submit_duration_seats(interaction_modal: Interaction) -> None:
            self.current_duration_seats = duration_seats_input.value.strip()
            await interaction_modal.response.edit_message(
                content=f"Modification: {self.current_emoji} {self.current_name}",
                view=self,
            )

        modal.on_submit = on_submit_duration_seats  # type: ignore[method-assign]
        await interaction.response.send_modal(modal)

    @ui.button(label="Rendre la formation excusable", style=ButtonStyle.secondary, row=3, id=MAKE_EXCUSABLE_BUTTON_ID)
    async def make_excusable_button(self, interaction: Interaction, button: ui.Button) -> None:
        """Button to make the formation excusable.

        Args:
            interaction (Interaction): The interaction that triggered the button click.
            button (ui.Button): The button that was clicked.
        """
        self.current_excusable = True
        make_non_excusable_button = self.find_item(MAKE_NON_EXCUSABLE_BUTTON_ID)
        assert isinstance(make_non_excusable_button, ui.Button)
        make_non_excusable_button.disabled = False
        button.disabled = True
        await interaction.response.edit_message(
            content=f"Modification: {self.current_emoji} {self.current_name}",
            view=self,
        )

    @ui.button(label="Rendre la formation non excusable", style=ButtonStyle.secondary, row=3, id=MAKE_NON_EXCUSABLE_BUTTON_ID)
    async def make_non_excusable_button(self, interaction: Interaction, button: ui.Button) -> None:
        """Button to make the formation non excusable.

        Args:
            interaction (Interaction): The interaction that triggered the button click.
            button (ui.Button): The button that was clicked.
        """
        self.current_excusable = False
        make_excusable_button = self.find_item(MAKE_EXCUSABLE_BUTTON_ID)
        assert isinstance(make_excusable_button, ui.Button)
        make_excusable_button.disabled = False
        button.disabled = True
        await interaction.response.edit_message(
            content=f"Modification: {self.current_emoji} {self.current_name}",
            view=self,
        )

    @ui.button(label="Valider les modifications", style=ButtonStyle.success, row=4)
    async def validate_button(self, interaction: Interaction, button: ui.Button) -> None:
        """Button to validate and save all modifications.

        Args:
            interaction (Interaction): The interaction that triggered the button click.
            button (ui.Button): The button that was clicked.
        """
        # TODO: à faire
