"""The template screens of `/fm modeles`: the list, the form, the menus and the confirmation of a deletion."""

from __future__ import annotations

from hashlib import sha256
import logging
from typing import TYPE_CHECKING, Any, Final, Literal, override

from discord import AllowedMentions, ButtonStyle, Embed, Interaction, SelectOption, TextStyle, ui
from discord.utils import escape_markdown, escape_mentions

from fablabot.helpers.constants import BRAND_COLOUR, TEMPLATE_ROLES, ErrorMessages
from fablabot.helpers.formation.catalogue import EXCUSABLE_NO, EXCUSABLE_YES, template_fields
from fablabot.helpers.utils import RefusalKind, counted, log_refusal, right_check
from fablabot.services import catalogue
from fablabot.services.catalogue import TemplateNameTakenError

if TYPE_CHECKING:
    from collections.abc import Callable
    import re
    from types import CoroutineType

    from fablabot.main import Fablabot
    from fablabot.models.catalogue import TemplateFields, TrainingTemplate

logger = logging.getLogger(__name__)

COMMAND: Final = "fm.modeles"
TEMPLATES_PER_PAGE: Final = 10
OPTIONS_PER_PAGE: Final = 25
"""Discord's limit for the options of one menu: a longer list is reached through the page buttons."""
_TITLE: Final = "📚 Modèles de formation"
_FOOTER: Final = "Un modèle pré-remplit /fm brouillon → Ajouter une session"
_EMPTY: Final = "Aucun modèle pour l'instant. **Nouveau modèle** en crée un."
_GONE: Final = "Ce modèle n'existe plus."
_CHANGED: Final = "Ce modèle a changé depuis : rouvre-le pour le modifier."
_CHANGED_DELETE: Final = "Ce modèle a changé depuis : rouvre la liste pour le supprimer."
_NOT_STORED: Final = "Rien n'a été enregistré."
_UNCHANGED: Final = "Rien n'a été modifié."
_BUTTON_CHARS: Final = 80
_OPTION_CHARS: Final = 100
_PICK_VERBS: Final = {"edit": "modifier", "del": "supprimer"}

Action = Literal["edit", "del"]


def page_count(total: int, size: int) -> int:
    """Count the pages of a list, at least one so that an empty list still has its page.

    Args:
        total (int): How many items the list holds.
        size (int): How many fit on a page.

    Returns:
        int: The number of pages.
    """
    return max(1, -(-total // size))


def cut(text: str, limit: int) -> str:
    """Cut a text to Discord's limit, with an ellipsis where it stops."""
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def _digest(fields: TemplateFields) -> str:
    """Summarise what a template holds in 12 hex digits, so that a confirmation can tell it from a rewritten one."""
    text = "\x1f".join(
        str(value) for value in (fields.name, fields.duration, fields.seats, fields.description, fields.excusable)
    )
    return sha256(text.encode()).hexdigest()[:12]


def plain(text: str) -> str:
    """Make a typed text harmless in a message: no markdown, no mention."""
    return escape_mentions(escape_markdown(text))


def page_row(  # noqa: PLR0913 - the three buttons of a page row are built from these
    view: ui.View, *, previous_id: str, next_id: str, label: str, page: int, pages: int, row: int = 0
) -> None:
    """Add the row of page buttons to a view: previous, the disabled middle label, next.

    The buttons only carry custom IDs: the dynamic item that owns those IDs answers the click, so the row keeps working
    after a restart. A disabled end gets its own ID so that two buttons of a message never share one.

    Args:
        view (ui.View): The view to add the row to.
        previous_id (str): The custom ID of the previous button.
        next_id (str): The custom ID of the next button.
        label (str): The text of the middle button, such as ``Page 1/2``.
        page (int): The page shown, from 0.
        pages (int): How many pages there are.
        row (int): The row of the message.
    """
    first, last = page <= 0, page >= pages - 1
    view.add_item(
        ui.Button(
            label="◀ Précédent",
            custom_id=f"{previous_id}:first" if first else previous_id,
            disabled=first,
            row=row,
        )
    )
    view.add_item(ui.Button(label=cut(label, _BUTTON_CHARS), custom_id=f"{previous_id}:label", disabled=True, row=row))
    view.add_item(ui.Button(label="Suivant ▶", custom_id=f"{next_id}:last" if last else next_id, disabled=last, row=row))


def click_check(command: str, roles: set[str]) -> Callable[[Any, Interaction], CoroutineType[Any, Any, bool]]:
    """Build the ``interaction_check`` of a staff item or form: the server is served, then the roles are read again.

    Args:
        command (str): The command that opened the screen, for the refusal lines.
        roles (set[str]): The role names of that command, the very constant it checks.

    Returns:
        Callable[[Any, Interaction], CoroutineType[Any, Any, bool]]: A check that refuses privately, logs the kind and
            lets nothing run when the server is not served or the member holds none of the roles.
    """
    rights = right_check(logger, command, roles)

    async def check(item: Any, interaction: Interaction) -> bool:
        client: Fablabot = interaction.client  # pyright: ignore[reportAssignmentType]  # the bot is the only client
        if interaction.guild is None or not client.guild_access.is_served(interaction.guild.id):
            log_refusal(logger, command, interaction, RefusalKind.SERVER_NOT_SERVED)
            await interaction.response.send_message(f"{ErrorMessages.GUILD_NOT_ALLOWED} {_UNCHANGED}", ephemeral=True)
            return False
        return await rights(item, interaction)

    return check


def _places(template: TrainingTemplate, *, markdown: bool = True) -> str:
    """Write a template's duration and seats, and whether an absence may be excused.

    Args:
        template (TrainingTemplate): The template.
        markdown (bool): Whether the line is shown as markdown, so the typed duration is escaped; a menu shows plain text.

    Returns:
        str: The line.
    """
    fields = template.fields
    excusable = " · excusable" if fields.excusable else ""
    duration = plain(fields.duration) if markdown else fields.duration
    return f"{duration} · {counted(fields.seats, 'place', 'places')}{excusable}"


def templates_message(guild_id: int, page: int, *, note: str = "") -> tuple[str, Embed, ui.View]:
    """Build the list of a server's templates, one page of it, with the buttons that act on it.

    Args:
        guild_id (int): The server's ID.
        page (int): The page to show, from 0; one past the end shows the last.
        note (str): A line to put above the list, such as the result of the last action.

    Returns:
        tuple[str, Embed, ui.View]: The text above the list (empty without a note), the list and its buttons.
    """
    templates = catalogue.list_templates(guild_id)
    pages = page_count(len(templates), TEMPLATES_PER_PAGE)
    page = min(max(page, 0), pages - 1)
    shown = templates[page * TEMPLATES_PER_PAGE : (page + 1) * TEMPLATES_PER_PAGE]
    if templates:
        lines = [f"**{plain(t.fields.name)}** — {_places(t)}" for t in shown]
        footer = f"-# Page {page + 1}/{pages} · {counted(len(templates), 'modèle', 'modèles')}"
        description = "\n".join([*lines, "", footer])
    else:
        description = _EMPTY
    embed = Embed(title=_TITLE, description=description, colour=BRAND_COLOUR).set_footer(text=_FOOTER)
    view = ui.View()
    page_row(
        view,
        previous_id=f"cat:page:{max(page - 1, 0)}",
        next_id=f"cat:page:{min(page + 1, pages - 1)}",
        label=f"Page {page + 1}/{pages}",
        page=page,
        pages=pages,
    )
    view.add_item(TemplateNew(row=1))
    view.add_item(TemplateAction("edit", disabled=not templates, row=1))
    view.add_item(TemplateAction("del", disabled=not templates, row=1))
    return note, embed, view


def pick_message(guild_id: int, action: Action, page: int) -> tuple[str, ui.View]:
    """Build the menu that asks which template to change or delete, one page of the server's templates.

    Args:
        guild_id (int): The server's ID.
        action (Action): ``edit`` or ``del``.
        page (int): The page of options, from 0; one past the end shows the last.

    Returns:
        tuple[str, ui.View]: The question and the menu with its page buttons.
    """
    templates = catalogue.list_templates(guild_id)
    pages = page_count(len(templates), OPTIONS_PER_PAGE)
    page = min(max(page, 0), pages - 1)
    shown = templates[page * OPTIONS_PER_PAGE : (page + 1) * OPTIONS_PER_PAGE]
    label = f"Modèles {page * OPTIONS_PER_PAGE + 1} à {page * OPTIONS_PER_PAGE + len(shown)} sur {len(templates)}"
    options = [
        SelectOption(
            label=cut(t.fields.name, _OPTION_CHARS),
            description=cut(_places(t, markdown=False), _OPTION_CHARS),
            value=str(t.id),
        )
        for t in shown
    ]
    view = ui.View()
    view.add_item(TemplatePick(action, page, placeholder=f"{label}…", options=options, row=0))
    page_row(
        view,
        previous_id=f"cat:pnav:{action}:{max(page - 1, 0)}",
        next_id=f"cat:pnav:{action}:{min(page + 1, pages - 1)}",
        label=label,
        page=page,
        pages=pages,
        row=1,
    )
    return f"**Quel modèle {_PICK_VERBS[action]} ?**", view


def deletion_message(template: TrainingTemplate) -> tuple[str, ui.View]:
    """Build the question that asks for the confirmation of a deletion.

    Args:
        template (TrainingTemplate): The template to delete.

    Returns:
        tuple[str, ui.View]: The question and its two buttons.
    """
    view = ui.View()
    view.add_item(TemplateDelete(template.id, _digest(template.fields)))
    view.add_item(TemplateKeep())
    content = (
        f"Supprimer le modèle **{plain(template.fields.name)}** ? "
        "Les sessions déjà ajoutées au brouillon ou publiées ne changent pas."
    )
    return content, view


async def show(interaction: Interaction, content: str, embed: Embed | None, view: ui.View) -> None:
    """Show a screen in place of the message the click came from, or in a new private message when there is none."""
    mentions = AllowedMentions.none()
    if interaction.message is not None:
        await interaction.response.edit_message(content=content or None, embed=embed, view=view, allowed_mentions=mentions)
        return
    extra: dict[str, Any] = {} if embed is None else {"embed": embed}
    await interaction.response.send_message(content or None, view=view, ephemeral=True, allowed_mentions=mentions, **extra)


async def _show_list(interaction: Interaction, page: int = 0, *, note: str = "") -> None:
    """Show the list of the server's templates, one page of it."""
    assert interaction.guild is not None
    content, embed, view = templates_message(interaction.guild.id, page, note=note)
    await show(interaction, content, embed, view)


async def _refuse(interaction: Interaction, text: str, kind: RefusalKind | None = None) -> None:
    """Tell the member privately why nothing was done, and log the kind when the refusal is not about the form's text."""
    if kind is not None:
        log_refusal(logger, COMMAND, interaction, kind)
    await interaction.response.send_message(text, ephemeral=True, allowed_mentions=AllowedMentions.none())


def _template_of(interaction: Interaction, value: str) -> TrainingTemplate | None:
    """Read a template of the clicker's server from the ID a custom ID or a menu holds; another server's reads as missing."""
    assert interaction.guild is not None
    return catalogue.get_template(interaction.guild.id, int(value)) if value.isdecimal() else None


class TemplateForm(ui.Modal):
    """The form of a template, empty for a new one and filled with the stored values for a change.

    Held in memory only: a form left open across a restart cannot be sent. Upgrade path: dispatch modal submits by custom
    ID, with the template's ID in it.

    Attributes:
        template (TrainingTemplate | None): The template being changed, as read when the form opened; None for a new one.
        name_input (ui.TextInput): The name.
        duration_input (ui.TextInput): The duration.
        seats_input (ui.TextInput): The seats.
        description_input (ui.TextInput): The description, optional.
        excusable_select (ui.Select): Oui or Non.
    """

    interaction_check = click_check(COMMAND, TEMPLATE_ROLES)

    def __init__(self, template: TrainingTemplate | None) -> None:
        """Build the form.

        Args:
            template (TrainingTemplate | None): The template to change, or None for a new one.
        """
        super().__init__(title="Modifier le modèle" if template else "Nouveau modèle")
        self.template = template
        fields = template.fields if template else None
        self.name_input = ui.TextInput(label="Nom", max_length=100, default=fields.name if fields else None)
        self.duration_input = ui.TextInput(
            label="Durée", placeholder="ex. 45 min, 1h30", max_length=50, default=fields.duration if fields else None
        )
        self.seats_input = ui.TextInput(label="Places", max_length=20, default=str(fields.seats) if fields else None)
        self.description_input = ui.TextInput(
            label="Description",
            style=TextStyle.paragraph,
            required=False,
            max_length=500,
            default=fields.description if fields and fields.description else None,
        )
        excusable = fields.excusable if fields else True
        self.excusable_select = ui.Select(
            options=[
                SelectOption(label=EXCUSABLE_YES, value=EXCUSABLE_YES, default=excusable),
                SelectOption(label=EXCUSABLE_NO, value=EXCUSABLE_NO, default=not excusable),
            ],
            required=True,
        )
        for item in (self.name_input, self.duration_input, self.seats_input, self.description_input):
            self.add_item(item)
        self.add_item(ui.Label(text="Excusable", component=self.excusable_select))

    @override
    async def on_submit(self, interaction: Interaction) -> None:
        """Store the template when the form is valid, else say why not, and show the list."""
        assert interaction.guild is not None
        parsed = template_fields(
            self.name_input.value,
            self.duration_input.value,
            self.seats_input.value,
            self.description_input.value,
            self.excusable_select.values[0] if self.excusable_select.values else "",
        )
        if isinstance(parsed, str):
            await _refuse(interaction, f"{parsed} {_NOT_STORED}")
            return
        refusal = self._store(interaction.guild.id, interaction.user.id, parsed)
        if refusal is not None:
            await _refuse(interaction, f"{refusal[0]} {_NOT_STORED}", refusal[1])
            return
        await _show_list(interaction, note=f"✅ Modèle **{plain(parsed.name)}** enregistré.")

    def _store(self, guild_id: int, user_id: int, fields: TemplateFields) -> tuple[str, RefusalKind | None] | None:
        """Create or change the template; give the refusal text and its log kind, or None once it is stored."""
        try:
            if self.template is None:
                stored = catalogue.create_template(guild_id, fields)
                logger.info(f"[{COMMAND}] create guild_id={guild_id} user_id={user_id} template_id={stored.id}")
            elif catalogue.update_template(guild_id, self.template.id, fields, expected=self.template.fields):
                logger.info(f"[{COMMAND}] update guild_id={guild_id} user_id={user_id} template_id={self.template.id}")
            elif catalogue.get_template(guild_id, self.template.id) is None:
                return _GONE, RefusalKind.TEMPLATE_UNKNOWN
            else:
                return _CHANGED, RefusalKind.TEMPLATE_CHANGED
        except TemplateNameTakenError:
            return f"Un modèle s'appelle déjà **{plain(fields.name)}** sur ce serveur.", None
        return None


class TemplatesPage(ui.DynamicItem[ui.Button[ui.View]], template=r"cat:page:(?P<page>[0-9]+)"):
    """A page button of the list of templates."""

    interaction_check = click_check(COMMAND, TEMPLATE_ROLES)

    def __init__(self, page: int) -> None:
        """Build the button.

        Args:
            page (int): The page it shows, from 0.
        """
        super().__init__(ui.Button(label="Page", custom_id=f"cat:page:{page}"))
        self.page = page

    @override
    @classmethod
    async def from_custom_id(cls, interaction: Interaction[Any], item: ui.Item[Any], match: re.Match[str], /) -> TemplatesPage:
        """Rebuild the button from its custom ID."""
        return cls(int(match["page"]))

    @override
    async def callback(self, interaction: Interaction[Any]) -> None:
        """Show that page of the list."""
        await _show_list(interaction, self.page)


class TemplateNew(ui.DynamicItem[ui.Button[ui.View]], template=r"cat:new"):
    """The button that opens the form of a new template."""

    interaction_check = click_check(COMMAND, TEMPLATE_ROLES)

    def __init__(self, *, row: int | None = None) -> None:
        """Build the button.

        Args:
            row (int | None): The row of the message.
        """
        button = ui.Button(label="➕ Nouveau modèle", style=ButtonStyle.primary, custom_id="cat:new")  # noqa: RUF001 - the sign the copy asks for
        super().__init__(button, row=row)

    @override
    @classmethod
    async def from_custom_id(cls, interaction: Interaction[Any], item: ui.Item[Any], match: re.Match[str], /) -> TemplateNew:
        """Rebuild the button from its custom ID."""
        return cls()

    @override
    async def callback(self, interaction: Interaction[Any]) -> None:
        """Open the empty form."""
        await interaction.response.send_modal(TemplateForm(None))


class TemplateAction(ui.DynamicItem[ui.Button[ui.View]], template=r"cat:(?P<action>edit|del)"):
    """The Modifier and Supprimer buttons of the list: each asks which template."""

    interaction_check = click_check(COMMAND, TEMPLATE_ROLES)

    def __init__(self, action: Action, *, disabled: bool = False, row: int | None = None) -> None:
        """Build the button.

        Args:
            action (Action): ``edit`` or ``del``.
            disabled (bool): Whether the button takes no click, as when there is no template.
            row (int | None): The row of the message.
        """
        label, style = ("✏️ Modifier", ButtonStyle.secondary) if action == "edit" else ("🗑️ Supprimer", ButtonStyle.danger)
        super().__init__(ui.Button(label=label, style=style, custom_id=f"cat:{action}", disabled=disabled), row=row)
        self.action: Action = action

    @override
    @classmethod
    async def from_custom_id(cls, interaction: Interaction[Any], item: ui.Item[Any], match: re.Match[str], /) -> TemplateAction:
        """Rebuild the button from its custom ID."""
        return cls("edit" if match["action"] == "edit" else "del")

    @override
    async def callback(self, interaction: Interaction[Any]) -> None:
        """Ask which template, or show the empty list when the server has none any more."""
        assert interaction.guild is not None
        if not catalogue.list_templates(interaction.guild.id):
            await _show_list(interaction)
            return
        content, view = pick_message(interaction.guild.id, self.action, 0)
        await show(interaction, content, None, view)


class TemplatePick(ui.DynamicItem[ui.Select[ui.View]], template=r"cat:pick:(?P<action>edit|del):(?P<page>[0-9]+)"):
    """The menu that picks the template to change or delete."""

    interaction_check = click_check(COMMAND, TEMPLATE_ROLES)

    def __init__(
        self,
        action: Action,
        page: int,
        *,
        placeholder: str | None = None,
        options: list[SelectOption] | None = None,
        row: int | None = None,
    ) -> None:
        """Build the menu.

        Args:
            action (Action): ``edit`` or ``del``.
            page (int): The page of options it lists, from 0.
            placeholder (str | None): The text shown while nothing is chosen.
            options (list[SelectOption] | None): The templates it lists.
            row (int | None): The row of the message.
        """
        super().__init__(
            ui.Select(custom_id=f"cat:pick:{action}:{page}", placeholder=placeholder, options=options or []), row=row
        )
        self.action: Action = action
        self.page = page

    @override
    @classmethod
    async def from_custom_id(cls, interaction: Interaction[Any], item: ui.Item[Any], match: re.Match[str], /) -> TemplatePick:
        """Rebuild the menu from its custom ID."""
        return cls("edit" if match["action"] == "edit" else "del", int(match["page"]))

    @override
    async def callback(self, interaction: Interaction[Any]) -> None:
        """Open the form of the chosen template, or ask for the confirmation of its deletion."""
        template = _template_of(interaction, self.item.values[0])
        if template is None:
            await _refuse(interaction, f"{_GONE} {_UNCHANGED}", RefusalKind.TEMPLATE_UNKNOWN)
        elif self.action == "edit":
            await interaction.response.send_modal(TemplateForm(template))
        else:
            content, view = deletion_message(template)
            await show(interaction, content, None, view)


class TemplatePickPage(ui.DynamicItem[ui.Button[ui.View]], template=r"cat:pnav:(?P<action>edit|del):(?P<page>[0-9]+)"):
    """A page button of the menu that picks a template."""

    interaction_check = click_check(COMMAND, TEMPLATE_ROLES)

    def __init__(self, action: Action, page: int) -> None:
        """Build the button.

        Args:
            action (Action): ``edit`` or ``del``.
            page (int): The page of options it shows, from 0.
        """
        super().__init__(ui.Button(label="Page", custom_id=f"cat:pnav:{action}:{page}"))
        self.action: Action = action
        self.page = page

    @override
    @classmethod
    async def from_custom_id(
        cls, interaction: Interaction[Any], item: ui.Item[Any], match: re.Match[str], /
    ) -> TemplatePickPage:
        """Rebuild the button from its custom ID."""
        return cls("edit" if match["action"] == "edit" else "del", int(match["page"]))

    @override
    async def callback(self, interaction: Interaction[Any]) -> None:
        """Show that page of options."""
        assert interaction.guild is not None
        if not catalogue.list_templates(interaction.guild.id):
            await _show_list(interaction)
            return
        content, view = pick_message(interaction.guild.id, self.action, self.page)
        await show(interaction, content, None, view)


class TemplateDelete(ui.DynamicItem[ui.Button[ui.View]], template=r"cat:del-ok:(?P<template>[0-9]+):(?P<digest>[0-9a-f]{12})"):
    """The button that confirms the deletion of a template, as it was when the question was asked.

    Attributes:
        template_id (int): The template to delete.
        digest (str): A summary of what the template held when the question was asked.
    """

    interaction_check = click_check(COMMAND, TEMPLATE_ROLES)

    def __init__(self, template_id: int, digest: str) -> None:
        """Build the button.

        Args:
            template_id (int): The template to delete.
            digest (str): A summary of what it holds now; a template that holds something else when clicked is kept.
        """
        super().__init__(
            ui.Button(label="🗑️ Supprimer", style=ButtonStyle.danger, custom_id=f"cat:del-ok:{template_id}:{digest}")
        )
        self.template_id = template_id
        self.digest = digest

    @override
    @classmethod
    async def from_custom_id(cls, interaction: Interaction[Any], item: ui.Item[Any], match: re.Match[str], /) -> TemplateDelete:
        """Rebuild the button from its custom ID."""
        return cls(int(match["template"]), match["digest"])

    @override
    async def callback(self, interaction: Interaction[Any]) -> None:
        """Delete the template of this server if it is still as asked and show the list; else tell why it was kept."""
        assert interaction.guild is not None
        guild_id = interaction.guild.id
        template = catalogue.get_template(guild_id, self.template_id)
        if template is None:
            await _refuse(interaction, f"{_GONE} {_UNCHANGED}", RefusalKind.TEMPLATE_UNKNOWN)
            return
        if _digest(template.fields) != self.digest or not catalogue.delete_template(
            guild_id, self.template_id, expected=template.fields
        ):
            await _refuse(interaction, f"{_CHANGED_DELETE} {_UNCHANGED}", RefusalKind.TEMPLATE_CHANGED)
            return
        logger.info(f"[{COMMAND}] delete guild_id={guild_id} user_id={interaction.user.id} template_id={self.template_id}")
        await _show_list(interaction, note=f"Modèle **{plain(template.fields.name)}** supprimé.")


class TemplateKeep(ui.DynamicItem[ui.Button[ui.View]], template=r"cat:del-no"):
    """The button that gives up a deletion."""

    interaction_check = click_check(COMMAND, TEMPLATE_ROLES)

    def __init__(self) -> None:
        """Build the button."""
        super().__init__(ui.Button(label="Annuler", style=ButtonStyle.secondary, custom_id="cat:del-no"))

    @override
    @classmethod
    async def from_custom_id(cls, interaction: Interaction[Any], item: ui.Item[Any], match: re.Match[str], /) -> TemplateKeep:
        """Rebuild the button from its custom ID."""
        return cls()

    @override
    async def callback(self, interaction: Interaction[Any]) -> None:
        """Show the list again, with the word that nothing was deleted."""
        await _show_list(interaction, note="Rien n'a été supprimé.")


CATALOGUE_ITEMS: Final = (
    TemplatesPage,
    TemplateNew,
    TemplateAction,
    TemplatePick,
    TemplatePickPage,
    TemplateDelete,
    TemplateKeep,
)
