"""The screens that add a session to the draft: the template picker, the session form and the "Session ajoutée" answer."""

from __future__ import annotations

from datetime import date, datetime, time
import hashlib
import logging
from typing import TYPE_CHECKING, Any, Final, override

from discord import AllowedMentions, ButtonStyle, Interaction, Member, Object, SelectOption, TextStyle, ui

from fablabot.helpers.constants import FORMATION_ROLES, PARIS_TZ, ErrorMessages
from fablabot.helpers.formation import is_past
from fablabot.helpers.formation.attendance import trainer_id
from fablabot.helpers.formation.catalogue import (
    DESCRIPTION_CHARS,
    EXCUSABLE_YES,
    add_session,
    day_text,
    is_in_draft,
    read_date,
    read_date_time,
    read_hour,
    read_seats,
    replace_session,
    session_digest,
    starts_at,
    template_description,
    template_fields,
)
from fablabot.helpers.formation.rendering import discord_timestamp
from fablabot.helpers.utils import RefusalKind, counted, log_refusal
from fablabot.models.catalogue import TemplateFields, TrainingTemplate
from fablabot.models.formation import FmMessageDraft, Formation
from fablabot.services import catalogue, formations
from fablabot.ui.catalogue import OPTIONS_PER_PAGE, click_check, cut, page_count, page_row, plain, show, template_line
from fablabot.ui.draft import _NOT_CHANGED, _SESSION_CHANGED, DraftButton, draft_message, stored_draft
from fablabot.ui.draft import COMMAND as DRAFT_COMMAND

if TYPE_CHECKING:
    import re

    from discord import Guild


logger = logging.getLogger(__name__)

COMMAND: Final = "fm.ajouter"
PICKS_PER_PAGE: Final = OPTIONS_PER_PAGE - 1
"""Templates per page of the picker; the last option of a menu is kept for the free session."""
_NOT_ADDED: Final = "Rien n'a été ajouté."
_GONE: Final = "Ce modèle n'existe plus."
_NAME_CHARS: Final = 100
_DURATION_CHARS: Final = 50
_BUTTON_CHARS: Final = 80
_OPTION_CHARS: Final = 100
_TITLE_CHARS: Final = 45
_MODIFY: Final = "Modifier : "
_QUESTION: Final = (
    "**Quelle formation ?** Choisis un modèle, ou une session libre.\n"
    "-# Plus rapide au clavier : `/fm ajouter modele:` puis les premières lettres."
)
_NO_TEMPLATE: Final = "-# Aucun modèle pour l'instant : un Respo Formation ou un Administrateur les crée avec /fm modeles."
_BAD_TRAINER: Final = "Choisis un formateur parmi les membres du serveur."
_BAD_DATE: Final = "Date illisible : écris JJ/MM, par exemple 12/10."
_BAD_HOUR: Final = "Heure illisible : écris HH:MM, par exemple 18:30."
_BAD_SEATS: Final = "Places : un nombre entier de 1 à 500."
_BAD_DESCRIPTION: Final = f"Description : {DESCRIPTION_CHARS} caractères au plus."
_BAD_DATE_TIME: Final = "Date et heure illisibles : écris JJ/MM HH:MM, par exemple 12/10 18:30."
_FREE: Final = "free"
"""The value of the menu's last option, which no template ID can equal."""
_ALREADY_THERE: Final = "Cette session est déjà dans le brouillon."
_FULL: Final = "Le brouillon compte déjà 20 sessions : Discord n'accepte pas plus de 20 réactions sur un message."


def picker_message(guild_id: int, page: int) -> tuple[str, ui.View]:
    """Build the question that asks which training to add a session of, one page of the server's templates and the free session.

    Args:
        guild_id (int): The server's ID.
        page (int): The page of options, from 0; one past the end shows the last.

    Returns:
        tuple[str, ui.View]: The question, and the menu, which ends with the free session; the page buttons when the
            server has templates.
    """
    templates = catalogue.list_templates(guild_id)
    view = ui.View()
    pages = page_count(len(templates), PICKS_PER_PAGE)
    page = min(max(page, 0), pages - 1)
    shown = templates[page * PICKS_PER_PAGE : (page + 1) * PICKS_PER_PAGE]
    options = [
        SelectOption(
            label=cut(t.fields.name, _OPTION_CHARS),
            description=cut(template_line(t, markdown=False), _OPTION_CHARS),
            value=str(t.id),
        )
        for t in shown
    ]
    options.append(SelectOption(label="Session libre", description="sans modèle, tout à remplir", value=_FREE))
    view.add_item(SessionPick(page, placeholder=f"Choisis un modèle ({page + 1}/{pages})…", options=options, row=0))
    if not templates:
        return f"{_QUESTION}\n{_NO_TEMPLATE}", view
    page_row(
        view,
        previous_id=f"ses:pnav:{max(page - 1, 0)}",
        next_id=f"ses:pnav:{min(page + 1, pages - 1)}",
        label=f"Modèles {page * PICKS_PER_PAGE + 1} à {page * PICKS_PER_PAGE + len(shown)} sur {len(templates)}",
        page=page,
        pages=pages,
        row=1,
    )
    return _QUESTION, view


def added_message(session: Formation, count: int, template_id: int) -> tuple[str, ui.View]:
    """Build the answer to a sent session form: what was added, and the buttons that go on.

    Args:
        session (Formation): The session as stored, with its emoji.
        count (int): How many sessions the draft holds now.
        template_id (int): The template the session comes from, 0 for a free session.

    Returns:
        tuple[str, ui.View]: The text and its three buttons: another session of the training, another training, the brouillon.
    """
    places = counted(session.seats, "place", "places")
    content = (
        f"✅ **{plain(session.name)}** ajoutée : {discord_timestamp(session.start_dt)} avec {session.trainer_mention}, "
        f"{places}, émoji {session.emoji}.\n"
        f"-# Le brouillon compte maintenant {counted(count, 'session', 'sessions')}."
    )
    view = ui.View()
    view.add_item(
        SessionAgain(
            template_id,
            session.emoji,
            tag=session_tag(session) if template_id == 0 else "",
            label=cut(f"🔁 Encore une session de {session.name}", _BUTTON_CHARS),
        )
    )
    view.add_item(DraftButton("add", label="➕ Autre formation", style=ButtonStyle.secondary))  # noqa: RUF001 - the sign the copy asks for
    view.add_item(DraftButton("show"))
    return content, view


async def _refuse(
    interaction: Interaction, text: str, kind: RefusalKind | None = None, *, outcome: str = _NOT_ADDED, command: str = COMMAND
) -> None:
    """Tell the member privately why nothing was added (or changed), and log the kind when the refusal is not about the text."""
    if kind is not None:
        log_refusal(logger, command, interaction, kind)
    await interaction.response.send_message(f"{text} {outcome}", ephemeral=True, allowed_mentions=AllowedMentions.none())


def _template_of(interaction: Interaction, value: str) -> TrainingTemplate | None:
    """Read a template of the clicker's server from the ID a custom ID or a menu holds; another server's reads as missing."""
    assert interaction.guild is not None
    return catalogue.get_template(interaction.guild.id, int(value)) if value.isdecimal() else None


def _read_start(date_text: str, hour_text: str) -> datetime | str:
    """Read a session's start from the form, or give the text of the first refusal."""
    day = read_date(date_text, datetime.now(PARIS_TZ).date())
    if day is None:
        return _BAD_DATE
    hour = read_hour(hour_text)
    if hour is None:
        return _BAD_HOUR
    start = starts_at(day, hour)
    return ErrorMessages.PAST_DATETIME if is_past(start) else start


def _trainer_of(guild: Guild, select: ui.UserSelect) -> Member | None:
    """Read the member a form's menu chose, or None when it chose nobody, a bot, or someone who left the server."""
    chosen = select.values[0] if select.values else None
    trainer = guild.get_member(chosen.id) if chosen else None
    return None if trainer is None or trainer.bot else trainer


async def _add(interaction: Interaction, session: Formation | str, template_id: int) -> None:
    """Put a read session in the draft and show what was added, or say why not when the form gave a refusal text."""
    assert interaction.guild is not None
    guild = interaction.guild
    if isinstance(session, str):
        await _refuse(interaction, session)
        return
    # A form answered twice, as when a reply was lost, must not put the same session in the draft twice.
    if is_in_draft(guild.id, session):
        await _refuse(interaction, _ALREADY_THERE, RefusalKind.DRAFT_NOT_READY)
        return
    # Nothing is awaited between reading the draft and saving it, so the emoji is free when it is taken.
    added = add_session(guild.id, session)
    if added is None:
        await _refuse(interaction, _FULL, RefusalKind.DRAFT_NOT_READY)
        return
    stored, count = added
    logger.info(f"[{COMMAND}] add guild_id={guild.id} user_id={interaction.user.id} template_id={template_id} sessions={count}")
    content, view = added_message(stored, count, template_id)
    await show(interaction, content, None, view)


class SessionForm(ui.Modal):
    """The form of a session of a template: who trains, when, how many seats, and an optional description.

    Held in memory only: a form left open across a restart cannot be sent. Upgrade path: dispatch modal submits by custom
    ID, with the template's ID in it.

    The same form changes a session already in the draft (Modifier): ``replace`` names that session, and sending it
    changes the trainer, start, seats and description of that session only.

    Attributes:
        template (TrainingTemplate): The template the form opened on; for Modifier, the session's own values in one.
        replace (tuple[int, str, str] | None): The start, emoji and digest of the session to change, or None.
        trainer_select (ui.UserSelect): The trainer, preset when the form comes from "Encore une session".
        date_input (ui.TextInput): The day, ``JJ/MM``.
        hour_input (ui.TextInput): The hour, ``HH:MM``.
        seats_input (ui.TextInput): The seats, those of the template to start with.
        description_input (ui.TextInput): The description, optional.
    """

    interaction_check = click_check(COMMAND, FORMATION_ROLES)

    def __init__(  # noqa: PLR0913 - what a form opens with, by name
        self,
        *,
        template: TrainingTemplate,
        trainer_id: int | None,
        day: date | None,
        description: str,
        hour: time | None = None,
        replace: tuple[int, str, str] | None = None,
    ) -> None:
        """Build the form.

        Args:
            template (TrainingTemplate): The template the session comes from.
            trainer_id (int | None): The member to preset as trainer, or None for none.
            day (date | None): The day to preset, or None for an empty date.
            description (str): The description to start with, empty for none.
            hour (time | None): The hour to preset, or None for an empty hour.
            replace (tuple[int, str, str] | None): The start, emoji and digest of the session to change, or None to add one.
        """
        name = cut(template.fields.name, _TITLE_CHARS if replace is None else _TITLE_CHARS - len(_MODIFY))
        super().__init__(title=name if replace is None else f"{_MODIFY}{name}")
        self.template = template
        self.replace = replace
        self.trainer_select = ui.UserSelect(
            placeholder="Choisis le formateur", required=True, default_values=[Object(trainer_id)] if trainer_id else []
        )
        self.date_input = ui.TextInput(
            label="Date",
            placeholder="JJ/MM",
            max_length=10,
            default=day_text(day, datetime.now(PARIS_TZ).date()) if day else None,
        )
        self.hour_input = ui.TextInput(
            label="Heure", placeholder="HH:MM", max_length=8, default=hour.strftime("%H:%M") if hour else None
        )
        self.seats_input = ui.TextInput(label="Places", max_length=20, default=str(template.fields.seats))
        self.description_input = ui.TextInput(
            label="Description",
            style=TextStyle.paragraph,
            required=False,
            max_length=DESCRIPTION_CHARS,
            default=description or None,
        )
        self.add_item(ui.Label(text="Formateur", component=self.trainer_select))
        for item in (self.date_input, self.hour_input, self.seats_input, self.description_input):
            self.add_item(item)

    @override
    async def on_submit(self, interaction: Interaction) -> None:
        """Add the session to the draft, or change the one this form was opened on, when the form is valid; else say why not."""
        assert interaction.guild is not None
        guild = interaction.guild
        if self.replace is not None:
            await self._change(interaction, *self.replace)
            return
        template = catalogue.get_template(guild.id, self.template.id)
        if template is None:
            await _refuse(interaction, _GONE, RefusalKind.TEMPLATE_UNKNOWN)
            return
        await _add(interaction, self._session(guild, template), template.id)

    async def _change(self, interaction: Interaction, start_unix: int, emoji: str, digest: str) -> None:
        """Change the session this form was opened on, in the draft as it is now, and show the brouillon."""
        assert interaction.guild is not None
        guild = interaction.guild
        read = self._read(guild)
        if isinstance(read, str):
            await _refuse(interaction, read, outcome=_NOT_CHANGED)
            return
        trainer, start, seats = read
        # Read, changed and saved with no await in between: the session is found by its start, emoji and digest or refused.
        changed = replace_session(
            guild.id,
            start_unix,
            emoji,
            digest,
            trainer_mention=trainer.mention,
            start_iso=start.isoformat(),
            seats=seats,
            description=self.description_input.value.strip(),
        )
        if changed is None:
            await _refuse(
                interaction, _SESSION_CHANGED, RefusalKind.SESSION_CHANGED, outcome=_NOT_CHANGED, command=DRAFT_COMMAND
            )
            return
        logger.info(f"[{DRAFT_COMMAND}] change guild_id={guild.id} user_id={interaction.user.id}")
        content, embed, view = draft_message(guild, stored_draft(guild.id))
        await show(interaction, content, embed, view)

    def _read(self, guild: Guild) -> tuple[Member, datetime, int] | str:
        """Read the trainer, start and seats of the form, or give the text of the first refusal."""
        trainer = _trainer_of(guild, self.trainer_select)
        if trainer is None:
            return _BAD_TRAINER
        start = _read_start(self.date_input.value, self.hour_input.value)
        if isinstance(start, str):
            return start
        seats = read_seats(self.seats_input.value)
        if seats is None:
            return _BAD_SEATS
        # The form caps the field at the same length; this holds when a form is sent past what Discord allows.
        return _BAD_DESCRIPTION if len(self.description_input.value.strip()) > DESCRIPTION_CHARS else (trainer, start, seats)

    def _session(self, guild: Guild, template: TrainingTemplate) -> Formation | str:
        """Read the form into a session of the template as it is now, or give the text of the first refusal."""
        read = self._read(guild)
        if isinstance(read, str):
            return read
        trainer, start, seats = read
        fields = template.fields
        return Formation(
            emoji="",
            name=fields.name,
            trainer_mention=trainer.mention,
            start_iso=start.isoformat(),
            duration=fields.duration,
            seats=seats,
            description=self.description_input.value.strip(),
            excusable=fields.excusable,
        )


async def open_session_form(
    interaction: Interaction, template: TrainingTemplate, *, trainer_id: int | None = None, day: date | None = None
) -> None:
    """Open the form of a session of a template, with the template's seats and, for the first session of it, its description.

    Args:
        interaction (Interaction): The click that asks for the form.
        template (TrainingTemplate): The template.
        trainer_id (int | None): The member to preset as trainer.
        day (date | None): The day to preset.
    """
    assert interaction.guild is not None
    draft = formations.get_draft(interaction.guild.id) or FmMessageDraft(header="", role_id=0, intro="", fms=[], end="")
    form = SessionForm(
        template=template, trainer_id=trainer_id, day=day, description=template_description(draft, template.fields)
    )
    await interaction.response.send_modal(form)


class FreeSessionForm(ui.Modal):
    """The form of a one-off session, which no template fills in: its name, trainer, start, duration and seats.

    The session is excusable and has no description; both are changed from the brouillon, with Modifier. Held in memory
    only: a form left open across a restart cannot be sent. Upgrade path: dispatch modal submits by custom ID.

    Attributes:
        name_input (ui.TextInput): The name of the training.
        trainer_select (ui.UserSelect): The trainer, preset when the form comes from "Encore une session".
        when_input (ui.TextInput): The day and the hour, ``JJ/MM HH:MM``.
        duration_input (ui.TextInput): The duration, displayed as typed.
        seats_input (ui.TextInput): The seats.
    """

    interaction_check = click_check(COMMAND, FORMATION_ROLES)

    def __init__(
        self,
        *,
        name: str = "",
        duration: str = "",
        seats: int | None = None,
        trainer_id: int | None = None,
        day: date | None = None,
    ) -> None:
        """Build the form, empty or with what a session just added gives.

        Args:
            name (str): The name to start with, empty for none.
            duration (str): The duration to start with, empty for none.
            seats (int | None): The seats to start with, or None for none.
            trainer_id (int | None): The member to preset as trainer, or None for none.
            day (date | None): The day to preset, which leaves the hour to write, or None for an empty field.
        """
        super().__init__(title="Session libre")
        self.name_input = ui.TextInput(label="Nom", max_length=_NAME_CHARS, default=name or None)
        self.trainer_select = ui.UserSelect(
            placeholder="Choisis le formateur", required=True, default_values=[Object(trainer_id)] if trainer_id else []
        )
        self.when_input = ui.TextInput(
            label="Date et heure",
            placeholder="JJ/MM HH:MM",
            max_length=20,
            default=f"{day_text(day, datetime.now(PARIS_TZ).date())} " if day else None,
        )
        self.duration_input = ui.TextInput(
            label="Durée", placeholder="ex. 45 min, 1h30", max_length=_DURATION_CHARS, default=duration or None
        )
        self.seats_input = ui.TextInput(label="Places", max_length=20, default=str(seats) if seats else None)
        self.add_item(self.name_input)
        self.add_item(ui.Label(text="Formateur", component=self.trainer_select))
        for item in (self.when_input, self.duration_input, self.seats_input):
            self.add_item(item)

    @override
    async def on_submit(self, interaction: Interaction) -> None:
        """Add the session to the draft when the form is valid, else say why not, and show what was added."""
        assert interaction.guild is not None
        await _add(interaction, self._session(interaction.guild), 0)

    def _session(self, guild: Guild) -> Formation | str:
        """Read the form into a session, or give the text of the first refusal."""
        trainer = _trainer_of(guild, self.trainer_select)
        if trainer is None:
            return _BAD_TRAINER
        start = read_date_time(self.when_input.value, datetime.now(PARIS_TZ).date())
        if start is None:
            return _BAD_DATE_TIME
        if is_past(start):
            return ErrorMessages.PAST_DATETIME
        # The template's checks on a name, a duration and seats are the ones of a free session.
        fields = template_fields(self.name_input.value, self.duration_input.value, self.seats_input.value, "", EXCUSABLE_YES)
        if isinstance(fields, str):
            return fields
        return Formation(
            emoji="",
            name=fields.name,
            trainer_mention=trainer.mention,
            start_iso=start.isoformat(),
            duration=fields.duration,
            seats=fields.seats,
            description="",
            excusable=True,
        )


async def open_edit_form(interaction: Interaction, session: Formation) -> None:
    """Open the form of a session already in the draft, filled with its trainer, day, hour, seats and description.

    Args:
        interaction (Interaction): The click that asks for the form.
        session (Formation): The session to change, as the draft holds it now.
    """
    assert interaction.guild is not None
    start = session.start_dt.astimezone(PARIS_TZ)
    fields = TemplateFields(session.name, session.duration, session.seats, session.description, session.excusable)
    form = SessionForm(
        template=TrainingTemplate(0, interaction.guild.id, fields),
        trainer_id=trainer_id(session.trainer_mention),
        day=start.date(),
        description=session.description,
        hour=start.time(),
        replace=(int(session.start_dt.timestamp()), session.emoji, session_digest(session)),
    )
    await interaction.response.send_modal(form)


async def open_free_form(interaction: Interaction, session: Formation | None = None) -> None:
    """Open the form of a one-off session, empty or with the name, duration, seats, day and trainer of a session.

    Args:
        interaction (Interaction): The click that asks for the form.
        session (Formation | None): The session just added, whose values the form keeps, or None for an empty form.
    """
    if session is None:
        await interaction.response.send_modal(FreeSessionForm())
        return
    day = session.start_dt.date()
    # A day already gone would be refused as past, so it is not offered.
    form = FreeSessionForm(
        name=session.name,
        duration=session.duration,
        seats=session.seats,
        trainer_id=trainer_id(session.trainer_mention),
        day=day if day >= datetime.now(PARIS_TZ).date() else None,
    )
    await interaction.response.send_modal(form)


class SessionPick(ui.DynamicItem[ui.Select[ui.View]], template=r"ses:pick:(?P<page>[0-9]+)"):
    """The menu that picks the template to add a session of, or the free session.

    Attributes:
        page (int): The page of options it lists, from 0.
    """

    interaction_check = click_check(COMMAND, FORMATION_ROLES)

    def __init__(
        self, page: int, *, placeholder: str | None = None, options: list[SelectOption] | None = None, row: int | None = None
    ) -> None:
        """Build the menu.

        Args:
            page (int): The page of options it lists, from 0.
            placeholder (str | None): The text shown while nothing is chosen.
            options (list[SelectOption] | None): The templates it lists.
            row (int | None): The row of the message.
        """
        super().__init__(ui.Select(custom_id=f"ses:pick:{page}", placeholder=placeholder, options=options or []), row=row)
        self.page = page

    @override
    @classmethod
    async def from_custom_id(cls, interaction: Interaction[Any], item: ui.Item[Any], match: re.Match[str], /) -> SessionPick:
        """Rebuild the menu from its custom ID."""
        return cls(int(match["page"]))

    @override
    async def callback(self, interaction: Interaction[Any]) -> None:
        """Open the form of the chosen template or the free form, or say the template is gone."""
        if self.item.values[0] == _FREE:
            await open_free_form(interaction)
            return
        template = _template_of(interaction, self.item.values[0])
        if template is None:
            await _refuse(interaction, _GONE, RefusalKind.TEMPLATE_UNKNOWN)
            return
        await open_session_form(interaction, template)


class SessionPickPage(ui.DynamicItem[ui.Button[ui.View]], template=r"ses:pnav:(?P<page>[0-9]+)"):
    """A page button of the menu that picks a template to add a session of.

    Attributes:
        page (int): The page of options it shows, from 0.
    """

    interaction_check = click_check(COMMAND, FORMATION_ROLES)

    def __init__(self, page: int) -> None:
        """Build the button.

        Args:
            page (int): The page of options it shows, from 0.
        """
        super().__init__(ui.Button(label="Page", custom_id=f"ses:pnav:{page}"))
        self.page = page

    @override
    @classmethod
    async def from_custom_id(
        cls, interaction: Interaction[Any], item: ui.Item[Any], match: re.Match[str], /
    ) -> SessionPickPage:
        """Rebuild the button from its custom ID."""
        return cls(int(match["page"]))

    @override
    async def callback(self, interaction: Interaction[Any]) -> None:
        """Show that page of options."""
        assert interaction.guild is not None
        content, view = picker_message(interaction.guild.id, self.page)
        await show(interaction, content, None, view)


def session_tag(session: Formation) -> str:
    """Shorten a session's name and start to eight hexadecimal digits, which a custom ID can carry to tell it from another."""
    return hashlib.blake2s(f"{session.name}\n{int(session.start_dt.timestamp())}".encode(), digest_size=4).hexdigest()


class SessionAgain(
    ui.DynamicItem[ui.Button[ui.View]], template=r"ses:again:(?P<template>[0-9]+):(?:(?P<tag>[0-9a-f]{8}):)?(?P<emoji>.+)"
):
    """The "Encore une session" button: it reopens the form on the date and trainer of the session just added.

    Attributes:
        template_id (int): The template of the session just added, 0 for a free session.
        emoji (str): The emoji of that session, which finds it again in the stored draft.
        tag (str): For a free session, its ``session_tag``: it tells the session from one that took its emoji since.
    """

    interaction_check = click_check(COMMAND, FORMATION_ROLES)

    def __init__(self, template_id: int, emoji: str, *, tag: str = "", label: str = "🔁 Encore une session") -> None:
        """Build the button.

        Args:
            template_id (int): The template of the session just added, 0 for a free session.
            emoji (str): The emoji of that session.
            tag (str): For a free session, its ``session_tag``; empty for a template's session.
            label (str): The text of the button, which names the training.
        """
        custom_id = f"ses:again:{template_id}:{tag}:{emoji}" if tag else f"ses:again:{template_id}:{emoji}"
        super().__init__(ui.Button(label=label, style=ButtonStyle.primary, custom_id=custom_id))
        self.template_id = template_id
        self.emoji = emoji
        self.tag = tag

    @override
    @classmethod
    async def from_custom_id(cls, interaction: Interaction[Any], item: ui.Item[Any], match: re.Match[str], /) -> SessionAgain:
        """Rebuild the button from its custom ID."""
        return cls(int(match["template"]), match["emoji"], tag=match["tag"] or "")

    @override
    async def callback(self, interaction: Interaction[Any]) -> None:
        """Open the form on the template, or the free form, with what the session just added gives if it is still there."""
        assert interaction.guild is not None
        draft = formations.get_draft(interaction.guild.id)
        session = next((fm for fm in draft.fms if fm.emoji == self.emoji), None) if draft else None
        if self.template_id == 0:
            # The emoji may have passed to another session since, whose values are not the ones asked for.
            same = session is not None and session_tag(session) == self.tag
            await open_free_form(interaction, session if same else None)
            return
        template = _template_of(interaction, str(self.template_id))
        if template is None:
            await _refuse(interaction, _GONE, RefusalKind.TEMPLATE_UNKNOWN)
            return
        # The emoji may have passed to another training since; its date and trainer would then be someone else's.
        if session is None or session.name != template.fields.name:
            await open_session_form(interaction, template)
            return
        day = session.start_dt.date()
        # A day already gone would be read as the same day next year, so it is not offered.
        await open_session_form(
            interaction,
            template,
            trainer_id=trainer_id(session.trainer_mention),
            day=day if day >= datetime.now(PARIS_TZ).date() else None,
        )


SESSION_ITEMS: Final = (SessionPick, SessionPickPage, SessionAgain)
