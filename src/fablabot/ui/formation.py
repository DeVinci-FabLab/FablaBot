"""User interface components for formation-related features."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, cast

from discord import ButtonStyle, Embed, Interaction, TextStyle, ui
from emoji import emojize

from fablabot.helpers.constants import PARIS_TZ, ErrorMessages
from fablabot.helpers.formation import Emojis, parse_date_time, render_message
from fablabot.helpers.utils import is_valid_emoji, log_request
from fablabot.models.formation import FmCommand, FmMessageDraft, Formation

if TYPE_CHECKING:
    from fablabot.cogs import FormationManagement

logger = logging.getLogger(__name__)

EDIT_EMOJI_BUTTON_ID = 13
EDIT_TRAINER_BUTTON_ID = 14
MAKE_EXCUSABLE_BUTTON_ID = 15
MAKE_NON_EXCUSABLE_BUTTON_ID = 16
MAX_FORMATION_BUTTONS = 25
MIN_SEATS = 1
MAX_SEATS = 500


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

    def __init__(self, cog: FormationManagement, role_id: int) -> None:
        """Initialize the StartFmModal.

        Args:
            cog (FormationManagement): FormationManagement cog instance.
            role_id (int): Role ID to mention in the formation message.
        """
        super().__init__()
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

        content = render_message(draft)
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
    ) -> None:
        """Initialize the StartFmModal.

        Args:
            cog (FormationManagement): FormationManagement cog instance.
            role_id (int): Role ID to mention in the formation message.
            intro (str): Old introduction text.
            end (str): Old conclusion text.
        """
        super().__init__()
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

        content = render_message(draft)

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
        """
        super().__init__()
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

        preview = render_message(draft)
        logger.info(f"Guild {interaction.guild.id} added formation {fm.name!r} ({fm.start_iso}) to draft.")
        await interaction.response.send_message(
            "Formation ajoutée & brouillon mis à jour (trié). "
            "Utilise **/fm add** pour ajouter d'autres formations. **/fm preview** pour voir le rendu.",
            embed=Embed(title=f"Aperçu brouillon — {len(fms)} formation(s)", description=preview or "_(vide)_"),
            ephemeral=True,
        )


class _SelectFormationButton(ui.Button["SelectFormationView"]):
    """Button to select a formation to edit."""

    def __init__(self, formation_index: int, formation: Formation) -> None:
        """Initialize the SelectFormationButton.

        Args:
            formation_index (int): The index of the formation (1-based).
            formation (Formation): The formation object.
        """
        super().__init__(
            label=f"{formation.emoji} {formation.name}",
            style=ButtonStyle.primary,
            custom_id=f"select_fm_{formation_index}",
        )
        self.formation_index = formation_index
        self.formation = formation

    async def callback(self, interaction: Interaction) -> None:
        """Called when the button is clicked.

        Args:
            interaction (Interaction): The interaction that triggered the button click.
        """
        if self.view is None:
            await interaction.response.send_message("Cette vue n'est plus valide. Merci de réessayer.", ephemeral=True)
            return

        view = cast("SelectFormationView", self.view)
        match view.cmd:
            case FmCommand.EDIT:
                await interaction.response.send_message(
                    f"Modification : {self.formation.emoji} {self.formation.name}",
                    view=_EditFormationView(view.cog, self.formation_index, self.formation),
                    ephemeral=True,
                )
            case FmCommand.REMOVE:
                assert interaction.guild is not None
                draft = view.cog.get_guild_draft(interaction.guild.id)

                removed = draft.fms.pop(self.formation_index - 1)

                view.cog.set_guild_draft(interaction.guild.id, draft)

                preview = render_message(draft)
                logger.info(
                    f"Guild {interaction.guild.id} removed formation {removed.name!r} ({removed.start_iso}) from draft.",
                )
                await interaction.response.send_message(
                    f"Supprimé: {removed.emoji} {removed.name}",
                    embed=Embed(title=f"Aperçu brouillon — {len(draft.fms)} formation(s)", description=preview or "_(vide)_"),
                )


class SelectFormationView(ui.View):
    """View to select which formation to edit."""

    def __init__(self, cog: FormationManagement, formations: list[Formation], cmd: FmCommand) -> None:
        """Initialize the SelectFormationView.

        Args:
            cog (FormationManagement): FormationManagement cog instance.
            formations (list[Formation]): List of formations to choose from.
            cmd (FmCommand): The command that triggered this view.
        """
        super().__init__()
        self.cog = cog
        self.cmd = cmd

        for idx, fm in enumerate(formations[:MAX_FORMATION_BUTTONS], start=1):
            self.add_item(_SelectFormationButton(idx, fm))


# region ====== EditFormationView and its modals ======


class _EditEmojiModal(ui.Modal, title="Modifier l'émoji"):
    """Modal to edit formation emoji.

    Attributes:
        emoji_input (ui.TextInput): Text input for the formation emoji.
    """

    emoji_input: ui.TextInput[_EditEmojiModal] = ui.TextInput(
        label="Émoji",
        style=TextStyle.short,
        placeholder="ex: 🔧",
        required=True,
        max_length=30,
    )

    def __init__(self, view: _EditFormationView) -> None:
        """Initialize the EditEmojiModal.

        Args:
            view (EditFormationView): The parent view.
        """
        super().__init__()
        self.view_ref = view
        self.emoji_input.default = view.current_emoji

    async def on_submit(self, interaction: Interaction) -> None:
        """Called when the modal is submitted.

        Args:
            interaction (Interaction): The interaction that triggered the modal submission.
        """
        assert interaction.guild is not None
        candidate_raw = self.emoji_input.value.strip()
        draft = self.view_ref.cog.get_guild_draft(interaction.guild.id)
        fms = sorted(draft.fms, key=lambda x: x.start_dt)

        candidate = emojize(candidate_raw, language="alias")
        if not is_valid_emoji(candidate):
            logger.warning(f"Guild {interaction.guild.id} tried to edit formation with invalid emoji: {candidate!r}.")
            await interaction.response.send_message(ErrorMessages.INVALID_EMOJI, ephemeral=True)
            return

        if any(i != self.view_ref.formation_index - 1 and fm.emoji == candidate for i, fm in enumerate(fms)):
            logger.warning(f"Guild {interaction.guild.id} tried to reuse emoji {candidate!r} while editing formation.")
            await interaction.response.send_message(ErrorMessages.EMOJI_ALREADY_USED, ephemeral=True)
            return

        self.view_ref.current_emoji = candidate
        edit_emoji_button = self.view_ref.get_button(EDIT_EMOJI_BUTTON_ID)
        edit_emoji_button.label = f"Modifier émoji : {self.view_ref.current_emoji}"

        await self.view_ref.refresh_main_message(interaction)


class _EditNameDescriptionModal(ui.Modal, title="Modifier nom et description"):
    """Modal to edit formation name and description.

    Attributes:
        name_input (ui.TextInput): Text input for the formation name.
        description_input (ui.TextInput): Text input for the description.
    """

    name_input: ui.TextInput[_EditNameDescriptionModal] = ui.TextInput(
        label="Nom de la formation",
        style=TextStyle.short,
        placeholder="ex: Formation Arduino",
        required=True,
        max_length=100,
    )
    description_input: ui.TextInput[_EditNameDescriptionModal] = ui.TextInput(
        label="Description",
        style=TextStyle.paragraph,
        placeholder="Description de la formation...",
        required=False,
        max_length=500,
    )

    def __init__(self, view: _EditFormationView) -> None:
        """Initialize the EditNameDescriptionModal.

        Args:
            view (EditFormationView): The parent view.
        """
        super().__init__()
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

        await self.view_ref.refresh_main_message(interaction)


class _SelectTrainerView(ui.View):
    """View to select a trainer using UserSelect."""

    def __init__(self, edit_formation_view: _EditFormationView) -> None:
        """Initialize the SelectTrainerView.

        Args:
            edit_formation_view (EditFormationView): The parent EditFormationView.
        """
        super().__init__()
        self.edit_formation_view = edit_formation_view

    @ui.select(cls=ui.UserSelect, placeholder="Sélectionne lae formateur·ice", min_values=1, max_values=1)
    async def select_trainer(self, interaction: Interaction, select: ui.UserSelect) -> None:
        """Called when a user is selected.

        Args:
            interaction (Interaction): The interaction that triggered the selection.
            select (ui.UserSelect): The select component.
        """
        selected_user = select.values[0]
        self.edit_formation_view.current_trainer_mention = selected_user.mention

        edit_trainer_button = self.edit_formation_view.get_button(EDIT_TRAINER_BUTTON_ID)
        edit_trainer_button.label = f"Modifier lae formateur·ice : {selected_user.display_name}"

        await self.edit_formation_view.refresh_main_message(interaction)


class _EditDatetimeModal(ui.Modal, title="Modifier date et heure"):
    """Modal to edit formation datetime.

    Attributes:
        datetime_input (ui.TextInput): Text input for the formation datetime.
    """

    datetime_input: ui.TextInput[_EditDatetimeModal] = ui.TextInput(
        label="Date et heure (DD/MM/YYYY HH:MM)",
        style=TextStyle.short,
        placeholder="ex: 15/12/2024 18:30",
        required=True,
        max_length=16,
    )

    def __init__(self, view: _EditFormationView) -> None:
        """Initialize the EditDatetimeModal.

        Args:
            view (EditFormationView): The parent view.
        """
        super().__init__()
        self.view_ref = view
        self.datetime_input.default = f"{view.current_start_dt:%d/%m/%Y %H:%M}"

    async def on_submit(self, interaction: Interaction) -> None:
        """Called when the modal is submitted.

        Args:
            interaction (Interaction): The interaction that triggered the modal submission.
        """
        assert interaction.guild is not None
        candidate = self.datetime_input.value.strip()

        try:
            date_str, hour_str = candidate.split()
            start_dt = parse_date_time(date_str, hour_str, PARIS_TZ)
        except Exception:
            logger.warning(f"Guild {interaction.guild.id} provided invalid datetime format: {candidate!r}.")
            await interaction.response.send_message(ErrorMessages.INVALID_DATETIME, ephemeral=True)
            return

        self.view_ref.current_start_dt = start_dt
        await self.view_ref.refresh_main_message(interaction)


class _EditDurationSeatsModal(ui.Modal, title="Modifier durée et places"):
    """Modal to edit formation duration and seats.

    Attributes:
        duration_seats_input (ui.TextInput): Text input for the formation duration and seats.
    """

    duration_seats_input: ui.TextInput[_EditDurationSeatsModal] = ui.TextInput(
        label="Durée - Places",
        style=TextStyle.short,
        placeholder="ex: 2h - 10",
        required=True,
        max_length=20,
    )

    def __init__(self, view: _EditFormationView) -> None:
        """Initialize the EditDurationSeatsModal.

        Args:
            view (EditFormationView): The parent view.
        """
        super().__init__()
        self.view_ref = view
        self.duration_seats_input.default = f"{view.current_duration} - {view.current_seats}"

    async def on_submit(self, interaction: Interaction) -> None:
        """Called when the modal is submitted.

        Args:
            interaction (Interaction): The interaction that triggered the modal submission.

        Raises:
            ValueError: If the seats number is not an integer or out of bounds.
        """
        assert interaction.guild is not None
        candidate = self.duration_seats_input.value.strip()
        parts = [part.strip() for part in candidate.split("-")]

        if len(parts) != 2:
            logger.warning(f"Guild {interaction.guild.id} provided invalid duration/seats format: {candidate!r}.")
            await interaction.response.send_message(ErrorMessages.INVALID_DURATION_SEATS_FORMAT, ephemeral=True)
            return

        new_duration, seats_str = parts
        if not new_duration:
            logger.warning(f"Guild {interaction.guild.id} provided an empty duration while editing a formation.")
            await interaction.response.send_message(ErrorMessages.INVALID_DURATION, ephemeral=True)
            return

        try:
            new_seats = int(seats_str)
            if not (MIN_SEATS <= new_seats <= MAX_SEATS):
                msg = "Seats number out of valid range."
                raise ValueError(msg)
        except ValueError:
            logger.warning(f"Guild {interaction.guild.id} provided out-of-bounds seats number: {seats_str!r}.")
            await interaction.response.send_message(ErrorMessages.INVALID_SEATS, ephemeral=True)
            return

        self.view_ref.current_duration = new_duration
        self.view_ref.current_seats = new_seats
        await self.view_ref.refresh_main_message(interaction)


class _EditFormationView(ui.View):
    """View to edit a formation with selects and modals."""

    def __init__(self, cog: FormationManagement, formation_index: int, original: Formation) -> None:
        """Initialize the EditFormationView.

        Args:
            cog (FormationManagement): FormationManagement cog instance.
            formation_index (int): The index of the formation (1-based).
            original (Formation): The original formation being edited.
        """
        super().__init__()
        self.cog = cog
        self.formation_index = formation_index

        self.current_emoji = original.emoji
        self.current_name = original.name
        self.current_description = original.description
        self.current_trainer_mention = original.trainer_mention
        self.current_start_dt = original.start_dt
        self.current_duration = original.duration
        self.current_seats = original.seats
        self.current_excusable = original.excusable

        edit_emoji_button = self.get_button(EDIT_EMOJI_BUTTON_ID)
        edit_emoji_button.label = f"Modifier émoji : {self.current_emoji}"

        if self.current_excusable:
            self.get_button(MAKE_EXCUSABLE_BUTTON_ID).disabled = True
        else:
            self.get_button(MAKE_NON_EXCUSABLE_BUTTON_ID).disabled = True

    # region ====== Button callbacks ======

    @ui.button(label="Modifier émoji", style=ButtonStyle.secondary, row=0, id=EDIT_EMOJI_BUTTON_ID)
    async def edit_emoji_button(self, interaction: Interaction, _button: ui.Button) -> None:
        """Button to edit emoji.

        Args:
            interaction (Interaction): The interaction that triggered the button click.
            _button (ui.Button): The button that was clicked.
        """
        await interaction.response.send_modal(_EditEmojiModal(self))

    @ui.button(label="Modifier nom & description", style=ButtonStyle.secondary, row=0)
    async def edit_name_desc_button(self, interaction: Interaction, _button: ui.Button) -> None:
        """Button to open modal for editing name and description.

        Args:
            interaction (Interaction): The interaction that triggered the button click.
            _button (ui.Button): The button that was clicked.
        """
        await interaction.response.send_modal(_EditNameDescriptionModal(self))

    @ui.button(label="Modifier lae formateur·ice", style=ButtonStyle.secondary, row=1, id=EDIT_TRAINER_BUTTON_ID)
    async def edit_trainer_button(self, interaction: Interaction, _button: ui.Button) -> None:
        """Button to open view for selecting trainer.

        Args:
            interaction (Interaction): The interaction that triggered the button click.
            _button (ui.Button): The button that was clicked.
        """
        await interaction.response.send_message(
            "Sélectionne lae formateur·ice :", view=_SelectTrainerView(self), ephemeral=True
        )

    @ui.button(label="Date & Heure", style=ButtonStyle.secondary, row=2)
    async def edit_datetime_button(self, interaction: Interaction, _button: ui.Button) -> None:
        """Button to edit datetime.

        Args:
            interaction (Interaction): The interaction that triggered the button click.
            _button (ui.Button): The button that was clicked.
        """
        await interaction.response.send_modal(_EditDatetimeModal(self))

    @ui.button(label="Durée & Places", style=ButtonStyle.secondary, row=2)
    async def edit_duration_seats_button(self, interaction: Interaction, _button: ui.Button) -> None:
        """Button to edit duration and seats.

        Args:
            interaction (Interaction): The interaction that triggered the button click.
            _button (ui.Button): The button that was clicked.
        """
        await interaction.response.send_modal(_EditDurationSeatsModal(self))

    @ui.button(label="Rendre la formation excusable", style=ButtonStyle.secondary, row=3, id=MAKE_EXCUSABLE_BUTTON_ID)
    async def make_excusable_button(self, interaction: Interaction, button: ui.Button) -> None:
        """Button to make the formation excusable.

        Args:
            interaction (Interaction): The interaction that triggered the button click.
            button (ui.Button): The button that was clicked.
        """
        self.current_excusable = True
        self.get_button(MAKE_NON_EXCUSABLE_BUTTON_ID).disabled = False
        button.disabled = True
        await self.refresh_main_message(interaction)

    @ui.button(label="Rendre la formation non excusable", style=ButtonStyle.secondary, row=3, id=MAKE_NON_EXCUSABLE_BUTTON_ID)
    async def make_non_excusable_button(self, interaction: Interaction, button: ui.Button) -> None:
        """Button to make the formation non excusable.

        Args:
            interaction (Interaction): The interaction that triggered the button click.
            button (ui.Button): The button that was clicked.
        """
        self.current_excusable = False
        self.get_button(MAKE_EXCUSABLE_BUTTON_ID).disabled = False
        button.disabled = True
        await self.refresh_main_message(interaction)

    @ui.button(label="Valider les modifications", style=ButtonStyle.success, row=4)
    async def validate_button(self, interaction: Interaction, _button: ui.Button) -> None:
        """Button to validate and save all modifications.

        Args:
            interaction (Interaction): The interaction that triggered the button click.
            _button (ui.Button): The button that was clicked.
        """
        assert interaction.guild is not None
        draft = self.cog.get_guild_draft(interaction.guild.id)
        fms = sorted(draft.fms, key=lambda x: x.start_dt)
        original = fms[self.formation_index - 1]

        log_request(
            logger,
            "fm.edit",
            interaction,
            index=self.formation_index,
            emoji=self.current_emoji,
            name=self.current_name,
            description=self.current_description,
            trainer=self.current_trainer_mention,
            datetime=self.current_start_dt,
            duration=self.current_duration,
            seats=self.current_seats,
            excusable=self.current_excusable,
        )

        updated = Formation(
            emoji=self.current_emoji,
            name=self.current_name,
            trainer_mention=self.current_trainer_mention,
            start_iso=self.current_start_dt.isoformat(),
            duration=self.current_duration,
            seats=self.current_seats,
            description=self.current_description,
            excusable=self.current_excusable,
        )

        fms[self.formation_index - 1] = updated
        fms.sort(key=lambda x: x.start_dt)

        draft = FmMessageDraft(
            header=draft.header,
            role_id=draft.role_id,
            intro=draft.intro,
            fms=fms,
            end=draft.end,
        )
        self.cog.set_guild_draft(interaction.guild.id, draft)

        preview = render_message(draft)
        new_position = fms.index(updated) + 1

        logger.info(
            f"Guild {interaction.guild.id} edited formation {original.name!r} -> "
            f"{updated.name!r} (index {self.formation_index} → {new_position}).",
        )

        await interaction.response.send_message(
            f"Mise à jour: {updated.emoji} {updated.name} (position {new_position}).",
            embed=Embed(title=f"Aperçu brouillon — {len(fms)} formation(s)", description=preview or "_(vide)_"),
            ephemeral=True,
        )

    # endregion Button callbacks

    # region ====== Helpers ======

    def get_button(self, button_id: int) -> ui.Button[_EditFormationView]:
        """Get a button by its ID.

        Args:
            button_id (int): The ID of the button.

        Returns:
            ui.Button[EditFormationView]: The button with the given ID.
        """
        item = self.find_item(button_id)
        assert isinstance(item, ui.Button)
        return item

    async def refresh_main_message(self, interaction: Interaction) -> None:
        """Refresh the main edit message with current formation data.

        Args:
            interaction (Interaction): The interaction to respond to.
        """
        await interaction.response.edit_message(
            content=f"Modification : {self.current_emoji} {self.current_name}",
            view=self,
        )

    # endregion Helpers


# endregion EditFormationView and its modals
