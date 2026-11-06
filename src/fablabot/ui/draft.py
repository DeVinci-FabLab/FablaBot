"""The draft screen of `/fm brouillon`: the sorted sessions, the announcement's text and role, its preview and Publier."""

from __future__ import annotations

from dataclasses import replace
import logging
from typing import TYPE_CHECKING, Any, Final, Literal, cast, override

from discord import AllowedMentions, ButtonStyle, ChannelType, Embed, Interaction, SelectOption, TextChannel, TextStyle, ui

from fablabot.helpers.constants import ANNOUNCEMENT_MAX_CHARS, BRAND_COLOUR, FORMATION_ROLES
from fablabot.helpers.formation import announcement_header, humanize_dt, is_past, render_message
from fablabot.helpers.formation.attendance import trainer_id
from fablabot.helpers.formation.catalogue import find_session, remove_session, session_digest, session_value
from fablabot.helpers.formation.rendering import discord_timestamp
from fablabot.helpers.utils import RefusalKind, counted, log_refusal
from fablabot.models.formation import FmMessageDraft
from fablabot.services import formations
from fablabot.ui.catalogue import OPTIONS_PER_PAGE, click_check, cut, page_count, page_row, plain, show

if TYPE_CHECKING:
    import re

    from discord import Guild

    from fablabot.cogs import FormationManagement
    from fablabot.models.formation import Formation

logger = logging.getLogger(__name__)

COMMAND: Final = "fm.brouillon"
_NBSP: Final = "\N{NO-BREAK SPACE}"
_NOT_PUBLISHED: Final = "Rien n'a été publié."
_GONE_CHANNEL: Final = "Ce salon n'existe plus : choisis-en un autre."
_NOT_CHANGED: Final = "Rien n'a été modifié."
_SESSION_CHANGED: Final = "Cette session a changé depuis : rouvre le brouillon."
_OPTION_CHARS: Final = 100
_PLACEHOLDER_CHARS: Final = 150
_UNKNOWN_TRAINER: Final = "formateur inconnu"
_PICK_VERBS: Final = {"edit": "modifier", "remove": "retirer"}
_DEFAULT_URL: Final = FmMessageDraft(header="", role_id=0, intro="", fms=[], end="").request_forms_url
_BUTTONS: Final = {
    "add": ("➕ Ajouter une session", ButtonStyle.primary),  # noqa: RUF001 - the sign the copy asks for
    "edit": ("✏️ Modifier", ButtonStyle.secondary),
    "remove": ("🗑️ Retirer", ButtonStyle.danger),
    "text": ("💬 Intro / conclusion", ButtonStyle.secondary),
    "preview": ("👁️ Aperçu", ButtonStyle.secondary),
    "publish": ("📣 Publier", ButtonStyle.success),
    "show": ("📝 Voir le brouillon", ButtonStyle.secondary),
}

Action = Literal["add", "edit", "remove", "text", "preview", "publish", "show"]
PickAction = Literal["edit", "remove"]


def _length(draft: FmMessageDraft) -> str:
    """Write the announcement's length with a no-break space as thousands separator, as the copy shows it."""
    return f"{len(render_message(draft)):,}".replace(",", _NBSP)


def stored_draft(guild_id: int) -> FmMessageDraft:
    """Read the server's draft, or an empty one when none is stored."""
    return formations.get_draft(guild_id) or FmMessageDraft(header="", role_id=0, intro="", fms=[], end="")


def _live(guild: Guild, draft: FmMessageDraft) -> FmMessageDraft:
    """Give the draft as it would be posted: a role the server no longer has counts as none."""
    return draft if not draft.role_id or guild.get_role(draft.role_id) else replace(draft, role_id=0)


def _line(session: Formation) -> str:
    """Write one session of the list: its emoji, name, start, trainer and seats."""
    seats = counted(session.seats, "place", "places")
    start = discord_timestamp(session.start_dt)
    return f"{session.emoji} **{plain(session.name)}** — {start} · {session.trainer_mention} · {seats}"


def publish_refusal(draft: FmMessageDraft) -> str | None:
    """Tell why a draft cannot be published, checking in this order: empty, no role, a started session, too long.

    Args:
        draft (FmMessageDraft): The draft as it would be posted.

    Returns:
        str | None: The refusal, ending with the word that nothing was published, or None when it can be posted.
    """
    if not draft.fms:
        reason = "Le brouillon ne contient aucune session."
    elif not draft.role_id:
        reason = "Choisis d'abord le rôle à notifier : **Intro / conclusion**."
    elif started := next((fm for fm in sorted(draft.fms, key=lambda fm: fm.start_dt) if is_past(fm.start_dt)), None):
        reason = f"La session **{started.emoji} {plain(started.name)}** a déjà commencé : retire-la ou change sa date."
    elif len(render_message(draft)) > ANNOUNCEMENT_MAX_CHARS:
        reason = (
            f"L'annonce fait {_length(draft)} caractères, Discord en accepte 2 000 : "
            "retire une session ou raccourcis une description."
        )
    else:
        return None
    return f"{reason} {_NOT_PUBLISHED}"


def _as_drafted(message: FmMessageDraft) -> str:
    """Render the announcement with no sign-ups: the text shows their count, and only a posted copy has any."""
    return render_message(replace(message, fms=[replace(fm, registered_users=[]) for fm in message.fms]))


def _published_refusal(guild_id: int, draft: FmMessageDraft) -> str | None:
    """Tell that the draft is the announcement already published, whose sign-ups a second post would stop counting.

    The posted copy gains sign-ups and reminders the draft never holds, so the two are compared by what they say.
    """
    last = formations.current_published(guild_id)
    if last is None or _as_drafted(last.message) != _as_drafted(draft):
        return None
    return (
        f"Cette annonce est déjà publiée dans <#{last.channel_id}> : change le brouillon pour en publier une autre. "
        f"{_NOT_PUBLISHED}"
    )


def draft_message(guild: Guild, draft: FmMessageDraft, *, note: str = "") -> tuple[str, Embed, ui.View]:
    """Build the brouillon: the sessions sorted by start, the announcement's length and the buttons that act on it.

    Args:
        guild (Guild): The server, which says whether the role to notify still exists.
        draft (FmMessageDraft): The server's draft.
        note (str): A line to put above the brouillon, such as the result of the last action.

    Returns:
        tuple[str, Embed, ui.View]: The text above the brouillon (empty without a note), the brouillon and its buttons.
    """
    draft = _live(guild, draft)
    sessions = sorted(draft.fms, key=lambda fm: fm.start_dt)
    role = f"<@&{draft.role_id}>" if draft.role_id else "aucun"
    lines = [f"**Intro** : {draft.intro.strip() or '_(vide)_'}", ""]
    lines += [_line(session) for session in sessions] or ["_Aucune session pour l'instant._"]
    lines.append("")
    if len(render_message(draft)) > ANNOUNCEMENT_MAX_CHARS:
        lines.append(
            f"⚠️ **L'annonce fait {_length(draft)} caractères**, Discord en accepte 2 000 : "
            "retire une session ou raccourcis une description."
        )
    lines.append(f"-# Triées par date · rôle notifié : {role} · annonce : {_length(draft)} / 2 000 caractères")
    embed = Embed(
        title=f"📝 Brouillon de la semaine · {counted(len(sessions), 'session', 'sessions')}",
        description="\n".join(lines),
        colour=BRAND_COLOUR,
    )
    view = ui.View()
    view.add_item(DraftButton("add", row=0))
    view.add_item(DraftButton("edit", disabled=not sessions, row=0))
    view.add_item(DraftButton("remove", disabled=not sessions, row=0))
    view.add_item(DraftButton("text", row=1))
    view.add_item(DraftButton("preview", row=1))
    view.add_item(DraftButton("publish", disabled=not sessions, row=1))
    return note, embed, view


def sessions_message(guild: Guild, draft: FmMessageDraft, *, action: PickAction, page: int) -> tuple[str, ui.View]:
    """Build the menu that asks which session to change or remove, one page of the sessions sorted by start.

    Args:
        guild (Guild): The server, which gives the trainers' names.
        draft (FmMessageDraft): The server's draft, which holds at least one session.
        action (PickAction): ``edit`` or ``remove``.
        page (int): The page of options, from 0; one past the end shows the last.

    Returns:
        tuple[str, ui.View]: The question and the menu with its page buttons.
    """
    sessions = sorted(draft.fms, key=lambda fm: fm.start_dt)
    pages = page_count(len(sessions), OPTIONS_PER_PAGE)
    page = min(max(page, 0), pages - 1)
    shown = sessions[page * OPTIONS_PER_PAGE : (page + 1) * OPTIONS_PER_PAGE]
    label = f"Sessions {page * OPTIONS_PER_PAGE + 1} à {page * OPTIONS_PER_PAGE + len(shown)} sur {len(sessions)}"
    options = [
        SelectOption(
            label=cut(f"{fm.emoji} {fm.name}", _OPTION_CHARS),
            description=cut(f"{humanize_dt(fm.start_dt)} · {_trainer_name(guild, fm)}", _OPTION_CHARS),
            value=session_value(fm),
        )
        for fm in shown
    ]
    view = ui.View()
    view.add_item(SessionList(action, page, placeholder=cut(f"{label}…", _PLACEHOLDER_CHARS), options=options, row=0))
    page_row(
        view,
        previous_id=f"fmd:snav:{action}:{max(page - 1, 0)}",
        next_id=f"fmd:snav:{action}:{min(page + 1, pages - 1)}",
        label=f"Page {page + 1}/{pages}",
        page=page,
        pages=pages,
        row=1,
    )
    return f"**Quelle session {_PICK_VERBS[action]} ?** {counted(len(sessions), 'session', 'sessions')}, triées par date.", view


def _trainer_name(guild: Guild, session: Formation) -> str:
    """Give the trainer's display name on the server, or ``formateur inconnu`` when the session names nobody the server has."""
    found = trainer_id(session.trainer_mention)
    member = guild.get_member(found) if found else None
    return member.display_name if member else _UNKNOWN_TRAINER


def removal_message(session: Formation) -> tuple[str, ui.View]:
    """Build the question that asks for the confirmation of a removal.

    Args:
        session (Formation): The session to remove.

    Returns:
        tuple[str, ui.View]: The question and its two buttons.
    """
    view = ui.View()
    view.add_item(SessionRemove(int(session.start_dt.timestamp()), session.emoji, session_digest(session)))
    view.add_item(SessionKeep())
    content = (
        f"Retirer **{session.emoji} {plain(session.name)}** "
        f"({discord_timestamp(session.start_dt)}, {session.trainer_mention}) du brouillon ?"
    )
    return content, view


def publish_message(channel: TextChannel | None) -> tuple[str, ui.View]:
    """Build the question that asks where to publish.

    Args:
        channel (TextChannel | None): The channel already chosen, or None when there is none yet.

    Returns:
        tuple[str, ui.View]: The question, the channel menu and the Publier button, which holds the chosen channel.
    """
    view = ui.View()
    view.add_item(PublishChannel(channel, row=0))
    view.add_item(PublishGo(channel.id if channel else 0, disabled=channel is None, row=1))
    return "**Où publier ?** Choisis le salon, puis **Publier**.", view


async def _refuse(interaction: Interaction, text: str, kind: RefusalKind) -> None:
    """Tell the member privately why nothing was done, and log the kind."""
    log_refusal(logger, COMMAND, interaction, kind)
    await interaction.response.send_message(text, ephemeral=True, allowed_mentions=AllowedMentions.none())


def _channel_of(guild: Guild, channel_id: int) -> TextChannel | None:
    """Read a text or announcement channel of the server by its ID, or None when it has no such channel."""
    channel = guild.get_channel(channel_id)
    return channel if isinstance(channel, TextChannel) else None


class DraftTextForm(ui.Modal):
    """The form of the announcement's intro, conclusion, link and role to notify, filled with what the draft holds.

    Held in memory only: a form left open across a restart cannot be sent. Upgrade path: dispatch modal submits by custom
    ID.

    Attributes:
        intro_input (ui.TextInput): The introduction.
        end_input (ui.TextInput): The conclusion.
        url_input (ui.TextInput): The link of the form where members propose a training, optional.
        role_select (ui.RoleSelect): The role to notify, preset to the draft's.
    """

    interaction_check = click_check(COMMAND, FORMATION_ROLES)

    def __init__(self, guild: Guild, draft: FmMessageDraft) -> None:
        """Build the form.

        Args:
            guild (Guild): The server, which says whether the draft's role still exists.
            draft (FmMessageDraft): The draft the form opens on.
        """
        super().__init__(title="Intro et conclusion")
        self.intro_input = ui.TextInput(label="Intro", style=TextStyle.paragraph, max_length=300, default=draft.intro or None)
        self.end_input = ui.TextInput(label="Conclusion", style=TextStyle.paragraph, max_length=200, default=draft.end or None)
        self.url_input = ui.TextInput(
            label="URL du formulaire (optionnel)", required=False, max_length=500, default=draft.request_forms_url or None
        )
        role = guild.get_role(draft.role_id) if draft.role_id else None
        self.role_select = ui.RoleSelect(default_values=[role] if role else [], required=True)
        for item in (self.intro_input, self.end_input, self.url_input):
            self.add_item(item)
        self.add_item(ui.Label(text="Rôle à notifier", component=self.role_select))

    @override
    async def on_submit(self, interaction: Interaction) -> None:
        """Save the text, the role and the header into the draft, sessions kept, and show the brouillon."""
        assert interaction.guild is not None
        guild = interaction.guild
        # Read, changed and saved with no await in between, so a session added meanwhile is not lost.
        current = stored_draft(guild.id)
        draft = replace(
            current,
            header=current.header or announcement_header(guild),
            role_id=self.role_select.values[0].id,
            intro=self.intro_input.value.strip(),
            end=self.end_input.value.strip(),
            request_forms_url=self.url_input.value.strip() or _DEFAULT_URL,
        )
        formations.save_draft(guild.id, draft)
        logger.info(f"[{COMMAND}] text guild_id={guild.id} user_id={interaction.user.id} role_id={draft.role_id}")
        content, embed, view = draft_message(guild, draft, note="✅ Brouillon mis à jour.")
        await show(interaction, content, embed, view)


class DraftButton(ui.DynamicItem[ui.Button[ui.View]], template=r"fmd:(?P<action>add|edit|remove|text|preview|publish|show)"):
    """A button of the brouillon; its custom ID names the action and the draft is read at each click.

    Attributes:
        action (Action): What the button does: ``add``, ``edit``, ``remove``, ``text``, ``preview``, ``publish`` or ``show``.
    """

    interaction_check = click_check(COMMAND, FORMATION_ROLES)

    def __init__(
        self,
        action: Action,
        *,
        label: str | None = None,
        style: ButtonStyle | None = None,
        disabled: bool = False,
        row: int | None = None,
    ) -> None:
        """Build the button.

        Args:
            action (Action): ``add``, ``edit``, ``remove``, ``text``, ``preview``, ``publish`` or ``show``.
            label (str | None): The text to show instead of the action's own, as "Autre formation" does for ``add``.
            style (ButtonStyle | None): The style to show instead of the action's own.
            disabled (bool): Whether the button takes no click, as Publier does with no session.
            row (int | None): The row of the message.
        """
        own_label, own_style = _BUTTONS[action]
        button = ui.Button(label=label or own_label, style=style or own_style, custom_id=f"fmd:{action}", disabled=disabled)
        super().__init__(button, row=row)
        self.action: Action = action

    @override
    @classmethod
    async def from_custom_id(cls, interaction: Interaction[Any], item: ui.Item[Any], match: re.Match[str], /) -> DraftButton:
        """Rebuild the button from its custom ID."""
        return cls(cast("Action", match["action"]))

    @override
    async def callback(self, interaction: Interaction[Any]) -> None:
        """Ask which training to add or which session to change or remove, open the text form, preview, publish, or show."""
        assert interaction.guild is not None
        guild = interaction.guild
        draft = stored_draft(guild.id)
        if self.action == "add":
            # Imported here: the session screens return to the brouillon through this very item.
            from fablabot.ui.session import picker_message

            content, view = picker_message(guild.id, 0)
            await show(interaction, content, None, view)
        elif self.action in ("edit", "remove") and draft.fms:
            content, view = sessions_message(guild, draft, action=self.action, page=0)
            await show(interaction, content, None, view)
        elif self.action == "text":
            await interaction.response.send_modal(DraftTextForm(guild, draft))
        elif self.action == "preview":
            await self._preview(interaction, _live(guild, draft))
        elif self.action == "publish":
            await self._ask_channel(interaction, draft)
        else:
            content, embed, view = draft_message(guild, draft)
            await show(interaction, content, embed, view)

    async def _preview(self, interaction: Interaction[Any], draft: FmMessageDraft) -> None:
        """Show the announcement as Publier posts it, privately and notifying nobody, or say it is too long."""
        mentions = AllowedMentions.none()
        if len(render_message(draft)) > ANNOUNCEMENT_MAX_CHARS:
            text = (
                f"L'annonce fait {_length(draft)} caractères, Discord en accepte 2 000 : elle ne peut pas être publiée ainsi."
            )
            await interaction.response.send_message(text, ephemeral=True, allowed_mentions=mentions)
            return
        await interaction.response.send_message(
            render_message(draft), ephemeral=True, allowed_mentions=mentions, suppress_embeds=True
        )

    async def _ask_channel(self, interaction: Interaction[Any], draft: FmMessageDraft) -> None:
        """Ask where to publish, the channel of the last announcement already chosen, unless the draft cannot be published."""
        assert interaction.guild is not None
        guild = interaction.guild
        live = _live(guild, draft)
        if (refusal := publish_refusal(live) or _published_refusal(guild.id, live)) is not None:
            await _refuse(interaction, refusal, RefusalKind.DRAFT_NOT_READY)
            return
        last = formations.current_published(guild.id)
        content, view = publish_message(_channel_of(guild, last.channel_id) if last else None)
        await show(interaction, content, None, view)


class PublishChannel(ui.DynamicItem[ui.ChannelSelect[ui.View]], template=r"fmd:pub-ch"):
    """The menu that chooses the channel to publish in."""

    interaction_check = click_check(COMMAND, FORMATION_ROLES)

    def __init__(self, channel: TextChannel | None = None, *, row: int | None = None) -> None:
        """Build the menu.

        Args:
            channel (TextChannel | None): The channel chosen already, or None for no choice.
            row (int | None): The row of the message.
        """
        super().__init__(
            ui.ChannelSelect(
                custom_id="fmd:pub-ch",
                channel_types=[ChannelType.text, ChannelType.news],
                placeholder="Salon de l'annonce",
                default_values=[channel] if channel else [],
            ),
            row=row,
        )

    @override
    @classmethod
    async def from_custom_id(cls, interaction: Interaction[Any], item: ui.Item[Any], match: re.Match[str], /) -> PublishChannel:
        """Rebuild the menu from its custom ID."""
        return cls()

    @override
    async def callback(self, interaction: Interaction[Any]) -> None:
        """Show the question again with the Publier button holding the chosen channel, or say the channel is gone."""
        assert interaction.guild is not None
        channel = _channel_of(interaction.guild, self.item.values[0].id)
        if channel is None:
            await _refuse(interaction, f"{_GONE_CHANNEL} {_NOT_PUBLISHED}", RefusalKind.CHANNEL_UNKNOWN)
            return
        content, view = publish_message(channel)
        await show(interaction, content, None, view)


class PublishGo(ui.DynamicItem[ui.Button[ui.View]], template=r"fmd:pub-go:(?P<channel>[0-9]{1,19})"):
    """The Publier button of the channel question; its custom ID holds the chosen channel.

    Attributes:
        channel_id (int): The channel to publish in; 0 while none is chosen.
    """

    interaction_check = click_check(COMMAND, FORMATION_ROLES)

    def __init__(self, channel_id: int, *, disabled: bool = False, row: int | None = None) -> None:
        """Build the button.

        Args:
            channel_id (int): The channel to publish in, 0 for none.
            disabled (bool): Whether the button takes no click, as when no channel is chosen.
            row (int | None): The row of the message.
        """
        super().__init__(
            ui.Button(label="📣 Publier", style=ButtonStyle.success, custom_id=f"fmd:pub-go:{channel_id}", disabled=disabled),
            row=row,
        )
        self.channel_id = channel_id

    @override
    @classmethod
    async def from_custom_id(cls, interaction: Interaction[Any], item: ui.Item[Any], match: re.Match[str], /) -> PublishGo:
        """Rebuild the button from its custom ID."""
        return cls(int(match["channel"]))

    @override
    async def callback(self, interaction: Interaction[Any]) -> None:
        """Publish the draft in the chosen channel as `/fm publish` does, unless it can no longer be published."""
        assert interaction.guild is not None
        guild = interaction.guild
        draft = _live(guild, stored_draft(guild.id))
        if (refusal := publish_refusal(draft) or _published_refusal(guild.id, draft)) is not None:
            await _refuse(interaction, refusal, RefusalKind.DRAFT_NOT_READY)
            return
        channel = _channel_of(guild, self.channel_id)
        if channel is None:
            await _refuse(interaction, f"{_GONE_CHANNEL} {_NOT_PUBLISHED}", RefusalKind.CHANNEL_UNKNOWN)
            return
        logger.info(
            f"[{COMMAND}] publish guild_id={guild.id} user_id={interaction.user.id} channel_id={channel.id} "
            f"sessions={len(draft.fms)}"
        )
        cog = cast("FormationManagement", interaction.client.get_cog("FormationManagement"))
        await cog.publish_draft(interaction, channel)


class SessionList(ui.DynamicItem[ui.Select[ui.View]], template=r"fmd:ses:(?P<action>edit|remove):(?P<page>[0-9]+)"):
    """The menu that picks the session to change or remove; each option's value holds the session's start and emoji.

    Attributes:
        action (PickAction): ``edit`` or ``remove``.
        page (int): The page of options it lists, from 0.
    """

    interaction_check = click_check(COMMAND, FORMATION_ROLES)

    def __init__(
        self,
        action: PickAction,
        page: int,
        *,
        placeholder: str | None = None,
        options: list[SelectOption] | None = None,
        row: int | None = None,
    ) -> None:
        """Build the menu.

        Args:
            action (PickAction): ``edit`` or ``remove``.
            page (int): The page of options it lists, from 0.
            placeholder (str | None): The text shown while nothing is chosen.
            options (list[SelectOption] | None): The sessions it lists.
            row (int | None): The row of the message.
        """
        super().__init__(
            ui.Select(custom_id=f"fmd:ses:{action}:{page}", placeholder=placeholder, options=options or []), row=row
        )
        self.action: PickAction = action
        self.page = page

    @override
    @classmethod
    async def from_custom_id(cls, interaction: Interaction[Any], item: ui.Item[Any], match: re.Match[str], /) -> SessionList:
        """Rebuild the menu from its custom ID."""
        return cls(cast("PickAction", match["action"]), int(match["page"]))

    @override
    async def callback(self, interaction: Interaction[Any]) -> None:
        """Open the form of the chosen session, or ask for the confirmation of its removal; refuse a session that is gone."""
        assert interaction.guild is not None
        draft = stored_draft(interaction.guild.id)
        start, _, emoji = self.item.values[0].partition(":")
        position = find_session(draft, int(start), emoji) if start.isdecimal() else None
        if position is None:
            await _refuse(interaction, f"{_SESSION_CHANGED} {_NOT_CHANGED}", RefusalKind.SESSION_CHANGED)
        elif self.action == "edit":
            # Imported here: the session screens return to the brouillon through this module.
            from fablabot.ui.session import open_edit_form

            await open_edit_form(interaction, draft.fms[position])
        else:
            content, view = removal_message(draft.fms[position])
            await show(interaction, content, None, view)


class SessionListPage(ui.DynamicItem[ui.Button[ui.View]], template=r"fmd:snav:(?P<action>edit|remove):(?P<page>[0-9]+)"):
    """A page button of the menu that picks a session.

    Attributes:
        action (PickAction): ``edit`` or ``remove``.
        page (int): The page of options it shows, from 0.
    """

    interaction_check = click_check(COMMAND, FORMATION_ROLES)

    def __init__(self, action: PickAction, page: int) -> None:
        """Build the button.

        Args:
            action (PickAction): ``edit`` or ``remove``.
            page (int): The page of options it shows, from 0.
        """
        super().__init__(ui.Button(label="Page", custom_id=f"fmd:snav:{action}:{page}"))
        self.action: PickAction = action
        self.page = page

    @override
    @classmethod
    async def from_custom_id(
        cls, interaction: Interaction[Any], item: ui.Item[Any], match: re.Match[str], /
    ) -> SessionListPage:
        """Rebuild the button from its custom ID."""
        return cls(cast("PickAction", match["action"]), int(match["page"]))

    @override
    async def callback(self, interaction: Interaction[Any]) -> None:
        """Show that page of options, or the brouillon when the draft has no session left."""
        assert interaction.guild is not None
        guild = interaction.guild
        draft = stored_draft(guild.id)
        if not draft.fms:
            content, embed, view = draft_message(guild, draft)
            await show(interaction, content, embed, view)
            return
        content, view = sessions_message(guild, draft, action=self.action, page=self.page)
        await show(interaction, content, None, view)


# A server emoji (<:name:id>) holds colons: the emoji runs up to the digest, which ends the ID.
class SessionRemove(
    ui.DynamicItem[ui.Button[ui.View]], template=r"fmd:rm-ok:(?P<start>[0-9]+):(?P<emoji>.+):(?P<digest>[0-9a-f]{12})"
):
    """The button that confirms the removal of a session, named by its start and emoji and the digest of what it held.

    Attributes:
        start_unix (int): The start of the session to remove, in seconds since the epoch.
        emoji (str): The emoji of the session to remove.
        digest (str): The digest of the session when the question was asked, so a changed session is not removed.
    """

    interaction_check = click_check(COMMAND, FORMATION_ROLES)

    def __init__(self, start_unix: int, emoji: str, digest: str) -> None:
        """Build the button.

        Args:
            start_unix (int): The start of the session to remove.
            emoji (str): The emoji of the session to remove.
            digest (str): The digest of the session as the question names it.
        """
        label, style = _BUTTONS["remove"]
        super().__init__(ui.Button(label=label, style=style, custom_id=f"fmd:rm-ok:{start_unix}:{emoji}:{digest}"))
        self.start_unix = start_unix
        self.emoji = emoji
        self.digest = digest

    @override
    @classmethod
    async def from_custom_id(cls, interaction: Interaction[Any], item: ui.Item[Any], match: re.Match[str], /) -> SessionRemove:
        """Rebuild the button from its custom ID."""
        return cls(int(match["start"]), match["emoji"], match["digest"])

    @override
    async def callback(self, interaction: Interaction[Any]) -> None:
        """Remove the session if the draft still holds it as asked about and show the brouillon; else tell why it was kept."""
        assert interaction.guild is not None
        guild = interaction.guild
        # Read, changed and saved with no await in between: the session is found by its start, emoji and digest or refused.
        removed = remove_session(guild.id, self.start_unix, self.emoji, self.digest)
        if removed is None:
            await _refuse(interaction, f"{_SESSION_CHANGED} {_NOT_CHANGED}", RefusalKind.SESSION_CHANGED)
            return
        draft = stored_draft(guild.id)
        logger.info(f"[{COMMAND}] remove guild_id={guild.id} user_id={interaction.user.id} sessions={len(draft.fms)}")
        content, embed, view = draft_message(guild, draft, note=f"Session **{removed.emoji} {plain(removed.name)}** retirée.")
        await show(interaction, content, embed, view)


class SessionKeep(ui.DynamicItem[ui.Button[ui.View]], template=r"fmd:rm-no"):
    """The button that gives up a removal."""

    interaction_check = click_check(COMMAND, FORMATION_ROLES)

    def __init__(self) -> None:
        """Build the button."""
        super().__init__(ui.Button(label="Annuler", style=ButtonStyle.secondary, custom_id="fmd:rm-no"))

    @override
    @classmethod
    async def from_custom_id(cls, interaction: Interaction[Any], item: ui.Item[Any], match: re.Match[str], /) -> SessionKeep:
        """Rebuild the button from its custom ID."""
        return cls()

    @override
    async def callback(self, interaction: Interaction[Any]) -> None:
        """Show the brouillon again, with the word that nothing was removed."""
        assert interaction.guild is not None
        content, embed, view = draft_message(interaction.guild, stored_draft(interaction.guild.id), note="Rien n'a été retiré.")
        await show(interaction, content, embed, view)


DRAFT_ITEMS: Final = (DraftButton, PublishChannel, PublishGo, SessionList, SessionListPage, SessionRemove, SessionKeep)
