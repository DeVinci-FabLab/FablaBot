"""The draft screen of `/fm brouillon`: the sorted sessions, the announcement's text and role, its preview and Publier."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
import logging
from typing import TYPE_CHECKING, Any, Final, Literal, cast, override

from discord import AllowedMentions, ButtonStyle, ChannelType, Embed, Interaction, SelectOption, TextChannel, TextStyle, ui

from fablabot.helpers.constants import ANNOUNCEMENT_MAX_CHARS, BRAND_COLOUR, FORMATION_ROLES
from fablabot.helpers.formation import announcement_header, announcement_length, grouped, humanize_dt, is_past, render_message
from fablabot.helpers.formation.attendance import trainer_id
from fablabot.helpers.formation.catalogue import (
    find_session,
    has_sessions_to_come,
    remove_session,
    session_digest,
    session_value,
)
from fablabot.helpers.formation.rendering import discord_timestamp
from fablabot.helpers.formation.update import CHANGED_REFUSAL, Step, UpdatePlan, plan_update, posted_reacted_emojis
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
_NOT_PUBLISHED: Final = "Rien n'a été publié."
_EMBED_CHARS: Final = 4096
"""Discord's room for the description of one embed."""
_MESSAGE_EMBED_CHARS: Final = 6000
"""Discord's room for the titles and descriptions of all the embeds of one message; a summary past it cannot be sent."""
_SUMMARY_TOO_LONG: Final = (
    "Le résumé des changements dépasse les 6 000 caractères que Discord accepte dans un message : annule une partie des "
    "changements du brouillon, puis presse de nouveau **Publier**. Rien n'a été modifié."
)
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
    "update": ("🔄 Mettre à jour l'annonce", ButtonStyle.primary),
    "show": ("📝 Voir le brouillon", ButtonStyle.secondary),
}

Action = Literal["add", "edit", "remove", "text", "preview", "publish", "show", "update"]
PickAction = Literal["edit", "remove"]


def _length(draft: FmMessageDraft) -> str:
    """Write the announcement's length, every seat taken, with a no-break space as thousands separator."""
    return grouped(announcement_length(draft))


def stored_draft(guild_id: int) -> FmMessageDraft:
    """Read the server's draft, or an empty one when none is stored."""
    return formations.get_draft(guild_id) or FmMessageDraft(header="", role_id=0, intro="", fms=[], end="")


def live_draft(guild: Guild, draft: FmMessageDraft) -> FmMessageDraft:
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
    elif announcement_length(draft) > ANNOUNCEMENT_MAX_CHARS:
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
    draft = live_draft(guild, draft)
    sessions = sorted(draft.fms, key=lambda fm: fm.start_dt)
    role = f"<@&{draft.role_id}>" if draft.role_id else "aucun"
    lines = [f"**Intro** : {draft.intro.strip() or '_(vide)_'}", ""]
    lines += [_line(session) for session in sessions] or ["_Aucune session pour l'instant._"]
    lines.append("")
    if announcement_length(draft) > ANNOUNCEMENT_MAX_CHARS:
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


def republish_message(channel_id: int) -> tuple[str, ui.View]:
    """Build the warning that an announcement is published with sessions to come, with what each way on does.

    Args:
        channel_id (int): The channel of the published announcement.

    Returns:
        tuple[str, ui.View]: The warning and its three buttons: update the announcement, post a new one, give up.
    """
    view = ui.View()
    view.add_item(DraftButton("update"))
    view.add_item(PublishConfirm())
    view.add_item(PublishKeep())
    warning = (
        f"⚠️ Une annonce de formations est déjà publiée dans <#{channel_id}>, avec des sessions à venir.\n"
        "**Mettre à jour l'annonce** y reporte le brouillon : les inscriptions restent, "
        "et les membres concernés sont prévenus.\n"
        "**Nouvelle annonce** en publie une autre : les inscriptions de l'ancienne ne compteront plus.\n"
        "Rien n'a encore été publié."
    )
    return warning, view


def _changed_parts(before: Formation, after: Formation) -> list[str]:
    """List what differs between a published session and its update, in the order of the copy."""
    parts: list[str] = []
    if before.name != after.name:
        parts.append(f"« {plain(before.name)} » → « {plain(after.name)} »")
    if int(before.start_dt.timestamp()) != int(after.start_dt.timestamp()):
        parts.append(f"{discord_timestamp(before.start_dt)} → {discord_timestamp(after.start_dt)}")
    if before.trainer_mention != after.trainer_mention:
        parts.append(f"{before.trainer_mention} → {after.trainer_mention}")
    if before.duration != after.duration:
        parts.append(f"durée {plain(before.duration)} → {plain(after.duration)}")
    if before.seats != after.seats:
        parts.append(f"{counted(before.seats, 'place', 'places')} → {counted(after.seats, 'place', 'places')}")
    if before.description != after.description:
        parts.append("description modifiée")
    if before.excusable != after.excusable:
        parts.append("excusable → non excusable" if before.excusable else "non excusable → excusable")
    return parts


def _changed_block(step: Step) -> list[str]:
    """Write a changed session of the summary: its line, then who is told and who changes place, one line each."""
    before, after = step.before, step.after
    assert before is not None
    assert after is not None
    block = [f"✏️ {after.emoji} **{plain(after.name)}** : {' · '.join(_changed_parts(before, after))}"]
    registered, waiting = len(before.registered_users), len(before.waitlisted_users)
    moved = (
        int(before.start_dt.timestamp()) != int(after.start_dt.timestamp()) or before.trainer_mention != after.trainer_mention
    )
    if moved and registered + waiting:
        block.append(f"-# À prévenir : {counted(registered, 'inscrit', 'inscrits')}, {waiting} en attente")
    if after.seats < registered:
        block.append(
            f"-# Vers la liste d'attente : {counted(registered - after.seats, 'inscrit', 'inscrits')}, par ordre d'arrivée"
        )
    if after.seats > before.seats and (brought := min(waiting, after.seats - registered)) > 0:
        block.append(f"-# Vers les places : {counted(brought, 'membre en attente', 'membres en attente')}")
    return block


def _added_block(step: Step) -> list[str]:
    """Write an added session of the summary: its line, then the trainer, who is told."""
    after = step.after
    assert after is not None
    return [
        f"➕ {after.emoji} **{plain(after.name)}** — {discord_timestamp(after.start_dt)} · {after.trainer_mention} · "  # noqa: RUF001 - the sign the copy asks for
        f"{counted(after.seats, 'place', 'places')}",
        f"-# À prévenir : {after.trainer_mention}",
    ]


def _removed_block(step: Step) -> list[str]:
    """Write a removed session of the summary: its line, then who is told and whose reactions the bot clears."""
    before = step.before
    assert before is not None
    registered, waiting = len(before.registered_users), len(before.waitlisted_users)
    block = [f"🗑️ {before.emoji} **{plain(before.name)}** — {discord_timestamp(before.start_dt)} · {before.trainer_mention}"]
    if registered + waiting:
        told = f"{counted(registered, 'inscrit', 'inscrits')}, {waiting} en attente"
        block.append(f"-# À prévenir : {told} · réactions {before.emoji} retirées")
    else:
        block.append(f"-# Réactions {before.emoji} retirées")
    return block


def update_message(plan: UpdatePlan, channel_id: int, message_id: int) -> tuple[list[Embed], ui.View]:
    """Build the summary of an update: what changes, who is told, the sessions left as they are, and the way to confirm.

    The summary goes on in another embed of the same message when it outgrows one, between two blocks of lines.

    Args:
        plan (UpdatePlan): The update, worked out from what is stored.
        channel_id (int): The channel of the published announcement.
        message_id (int): The published message, which the confirmation names.

    Returns:
        tuple[list[Embed], ui.View]: The embeds and the buttons that confirm or give up.
    """
    steps = [step for step in plan.steps if step.kind != "frozen"]
    blocks = [[f"Dans <#{channel_id}>. Les inscriptions et leur ordre d'arrivée restent.", ""]]
    block_of = {"added": _added_block, "removed": _removed_block, "changed": _changed_block}
    blocks += [block_of[step.kind](step) for step in steps]
    if plan.text_changes:
        blocks.append([f"💬 Texte de l'annonce : {', '.join(plan.text_changes)}"])
    footer = [""]
    held = [f"{step.before.emoji} {plain(step.before.name)}" for step in plan.steps if step.kind == "frozen" and step.before]
    if len(held) == 1:
        footer.append(f"-# Déjà commencée, laissée telle quelle : {held[0]}")
    elif held:
        footer.append(f"-# Déjà commencées, laissées telles quelles : {' · '.join(held)}")
    footer.append(f"-# Annonce : {grouped(announcement_length(plan.message))} / 2 000 caractères, toutes places prises")
    blocks.append(footer)

    chunks: list[list[str]] = [[]]
    for block in blocks:
        if chunks[-1] and len("\n".join([*chunks[-1], *block])) > _EMBED_CHARS:
            chunks.append([])
            block = block[1:] if block[0] == "" else block  # a blank line does not open an embed
        chunks[-1].extend(block)
    title = f"🔄 Mettre à jour l'annonce · {counted(len(steps) + bool(plan.text_changes), 'changement', 'changements')}"
    embeds = [
        Embed(title=title if n == 0 else None, description="\n".join(chunk), colour=BRAND_COLOUR)
        for n, chunk in enumerate(chunks)
    ]
    view = ui.View()
    view.add_item(UpdateConfirm(message_id, plan.digest(message_id)))
    view.add_item(UpdateKeep())
    return embeds, view


def done_message(
    plan: UpdatePlan,
    channel_id: int,
    *,
    posted: bool = True,
    unreacted: tuple[str, ...] = (),
    uncleared: tuple[str, ...] = (),
) -> tuple[str, ui.View]:
    """Build the answer to a confirmed update: how much changed, and the way to the draft, which now says the same.

    Args:
        plan (UpdatePlan): The update that was done.
        channel_id (int): The channel of the announcement.
        posted (bool): False when the announcement is stored but Discord has not taken the edit of its message yet,
            which the next five-minute check retries.
        unreacted (tuple[str, ...]): The emojis of added sessions the bot could not react with, for the staff to add.
        uncleared (tuple[str, ...]): The emojis of removed sessions whose reactions Discord refused to clear.

    Returns:
        tuple[str, ui.View]: The text and the button that shows the draft.
    """
    kinds = [step.kind for step in plan.steps]
    parts = [
        counted(kinds.count("changed"), "session modifiée", "sessions modifiées"),
        counted(kinds.count("added"), "ajoutée", "ajoutées"),
        counted(kinds.count("removed"), "retirée", "retirées"),
        "texte modifié",
    ]
    wanted = [kinds.count("changed"), kinds.count("added"), kinds.count("removed"), len(plan.text_changes)]
    shown = ", ".join(part for part, count in zip(parts, wanted, strict=True) if count)
    view = ui.View()
    view.add_item(DraftButton("show"))
    late = "" if posted else "⚠️ Discord n'a pas accepté la modification du message : elle sera refaite d'ici 5 minutes.\n"
    refused = (
        f"⚠️ Discord a refusé de retirer les réactions {' '.join(uncleared)} : le bot a retiré les siennes, "
        "celles des membres restent visibles mais ne comptent plus.\n"
        if uncleared
        else ""
    )
    missing = (
        f"⚠️ La réaction {' '.join(unreacted)} n'a pas pu être ajoutée : ajoute-la à la main sur l'annonce.\n"
        if unreacted
        else ""
    )
    content = (
        f"✅ **Annonce mise à jour** dans <#{channel_id}> : {shown}.\n"
        f"{late}{refused}{missing}-# Le brouillon reprend l'annonce telle qu'elle est maintenant."
    )
    return content, view


async def _refuse(interaction: Interaction, text: str, kind: RefusalKind) -> None:
    """Tell the member privately why nothing was done, and log the kind."""
    log_refusal(logger, COMMAND, interaction, kind)
    await interaction.response.send_message(text, ephemeral=True, allowed_mentions=AllowedMentions.none())


def _channel_of(guild: Guild, channel_id: int) -> TextChannel | None:
    """Read a text or announcement channel of the server by its ID, or None when it has no such channel."""
    channel = guild.get_channel(channel_id)
    return channel if isinstance(channel, TextChannel) else None


async def _summarise_update(interaction: Interaction[Any]) -> None:
    """Show what updating the published announcement with the draft would change, or say why it cannot be done.

    Nothing is changed: the summary is worked out from what is stored, and the buttons act on it later.
    """
    assert interaction.guild is not None
    guild = interaction.guild
    last = formations.current_published(guild.id)
    if last is None:
        await _refuse(interaction, CHANGED_REFUSAL, RefusalKind.ANNOUNCEMENT_CHANGED)
        return
    reacted = await posted_reacted_emojis(guild, last)
    if not isinstance(reacted, frozenset):
        await _refuse(interaction, *reacted)
        return
    plan = plan_update(
        last.message,
        live_draft(guild, stored_draft(guild.id)),
        now=datetime.now(UTC),
        retired=frozenset(formations.reacted_emojis(guild.id, last.message_id)) | reacted,
    )
    if isinstance(plan, str):
        await _refuse(interaction, plan, RefusalKind.DRAFT_NOT_READY)
        return
    logger.info(
        f"[{COMMAND}] update summary guild_id={guild.id} user_id={interaction.user.id} message_id={last.message_id} "
        f"steps={len(plan.steps)} text_changes={len(plan.text_changes)}"
    )
    embeds, view = update_message(plan, last.channel_id, last.message_id)
    if sum(len(embed) for embed in embeds) > _MESSAGE_EMBED_CHARS:
        await _refuse(interaction, _SUMMARY_TOO_LONG, RefusalKind.DRAFT_NOT_READY)
        return
    await show(interaction, "", None, view, embeds=embeds)


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


class DraftButton(
    ui.DynamicItem[ui.Button[ui.View]], template=r"fmd:(?P<action>add|edit|remove|text|preview|publish|show|update)"
):
    """A button of the brouillon; its custom ID names the action and the draft is read at each click.

    Attributes:
        action (Action): What the button does: ``add``, ``edit``, ``remove``, ``text``, ``preview``, ``publish``, ``show`` or
            ``update``.
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
            action (Action): ``add``, ``edit``, ``remove``, ``text``, ``preview``, ``publish``, ``show`` or ``update``.
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
        """Ask which training to add or which session to change or remove, open the text form, preview, publish, or show.

        Update shows what an update of the published announcement would change.
        """
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
            await self._preview(interaction, live_draft(guild, draft))
        elif self.action == "publish":
            await ask_channel(interaction, warn=True)
        elif self.action == "update":
            await _summarise_update(interaction)
        else:
            content, embed, view = draft_message(guild, draft)
            await show(interaction, content, embed, view)

    async def _preview(self, interaction: Interaction[Any], draft: FmMessageDraft) -> None:
        """Show the announcement as Publier posts it, privately and notifying nobody, or say it is too long."""
        mentions = AllowedMentions.none()
        if announcement_length(draft) > ANNOUNCEMENT_MAX_CHARS:
            text = (
                f"L'annonce fait {_length(draft)} caractères, Discord en accepte 2 000 : elle ne peut pas être publiée ainsi."
            )
            await interaction.response.send_message(text, ephemeral=True, allowed_mentions=mentions)
            return
        await interaction.response.send_message(
            render_message(draft), ephemeral=True, allowed_mentions=mentions, suppress_embeds=True
        )


async def ask_channel(interaction: Interaction[Any], *, warn: bool) -> None:
    """Ask where to publish, the channel of the last announcement already chosen, unless the draft cannot be published.

    The draft is read again here, so both the Publier button and the confirmation of the warning run the same checks.

    Args:
        interaction (Interaction[Any]): The click, on a server.
        warn (bool): Whether to warn first when the published announcement still has sessions to come; the confirmation
            passes False.
    """
    assert interaction.guild is not None
    guild = interaction.guild
    live = live_draft(guild, stored_draft(guild.id))
    if (refusal := publish_refusal(live) or _published_refusal(guild.id, live)) is not None:
        await _refuse(interaction, refusal, RefusalKind.DRAFT_NOT_READY)
        return
    last = formations.current_published(guild.id)
    # Only an announcement with sessions still to come loses sign-ups: last week's, all held, is no cause to warn.
    if warn and last is not None and has_sessions_to_come(guild.id):
        content, view = republish_message(last.channel_id)
    else:
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
        draft = live_draft(guild, stored_draft(guild.id))
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


class PublishConfirm(ui.DynamicItem[ui.Button[ui.View]], template=r"fmd:pub-ok"):
    """The button that goes on with a new announcement despite the warning: it asks where to publish, after the usual checks."""

    interaction_check = click_check(COMMAND, FORMATION_ROLES)

    def __init__(self) -> None:
        """Build the button."""
        super().__init__(ui.Button(label="📣 Nouvelle annonce", style=ButtonStyle.danger, custom_id="fmd:pub-ok"))

    @override
    @classmethod
    async def from_custom_id(cls, interaction: Interaction[Any], item: ui.Item[Any], match: re.Match[str], /) -> PublishConfirm:
        """Rebuild the button from its custom ID."""
        return cls()

    @override
    async def callback(self, interaction: Interaction[Any]) -> None:
        """Ask where to publish, or say why the draft can no longer be published."""
        await ask_channel(interaction, warn=False)


class PublishKeep(ui.DynamicItem[ui.Button[ui.View]], template=r"fmd:pub-no"):
    """The button that gives up Publier after the warning."""

    interaction_check = click_check(COMMAND, FORMATION_ROLES)

    def __init__(self) -> None:
        """Build the button."""
        super().__init__(ui.Button(label="Annuler", style=ButtonStyle.secondary, custom_id="fmd:pub-no"))

    @override
    @classmethod
    async def from_custom_id(cls, interaction: Interaction[Any], item: ui.Item[Any], match: re.Match[str], /) -> PublishKeep:
        """Rebuild the button from its custom ID."""
        return cls()

    @override
    async def callback(self, interaction: Interaction[Any]) -> None:
        """Show the brouillon again, with the word that nothing was published."""
        assert interaction.guild is not None
        content, embed, view = draft_message(interaction.guild, stored_draft(interaction.guild.id), note=_NOT_PUBLISHED)
        await show(interaction, content, embed, view)


class UpdateKeep(ui.DynamicItem[ui.Button[ui.View]], template=r"fmd:upd-no"):
    """The button that gives up the update after its summary."""

    interaction_check = click_check(COMMAND, FORMATION_ROLES)

    def __init__(self) -> None:
        """Build the button."""
        super().__init__(ui.Button(label="Annuler", style=ButtonStyle.secondary, custom_id="fmd:upd-no"))

    @override
    @classmethod
    async def from_custom_id(cls, interaction: Interaction[Any], item: ui.Item[Any], match: re.Match[str], /) -> UpdateKeep:
        """Rebuild the button from its custom ID."""
        return cls()

    @override
    async def callback(self, interaction: Interaction[Any]) -> None:
        """Show the brouillon again, with the word that nothing was changed."""
        assert interaction.guild is not None
        content, embed, view = draft_message(interaction.guild, stored_draft(interaction.guild.id), note=_NOT_CHANGED)
        await show(interaction, content, embed, view)


class UpdateConfirm(
    ui.DynamicItem[ui.Button[ui.View]], template=r"fmd:upd-ok:(?P<message>[0-9]{1,20}):(?P<digest>[0-9a-f]{12})"
):
    """The button that confirms the update of the published announcement, named by the message and the plan it was shown.

    Attributes:
        message_id (int): The published message the summary was made for.
        digest (str): The digest of the plan the summary showed, so a plan that changed since is not applied.
    """

    interaction_check = click_check(COMMAND, FORMATION_ROLES)

    def __init__(self, message_id: int, digest: str) -> None:
        """Build the button.

        Args:
            message_id (int): The published message the summary was made for.
            digest (str): The digest of the plan the summary showed.
        """
        super().__init__(
            ui.Button(
                label="✅ Confirmer la mise à jour",
                style=ButtonStyle.success,
                custom_id=f"fmd:upd-ok:{message_id}:{digest}",
            )
        )
        self.message_id = message_id
        self.digest = digest

    @override
    @classmethod
    async def from_custom_id(cls, interaction: Interaction[Any], item: ui.Item[Any], match: re.Match[str], /) -> UpdateConfirm:
        """Rebuild the button from its custom ID."""
        return cls(int(match["message"]), match["digest"])

    @override
    async def callback(self, interaction: Interaction[Any]) -> None:
        """Update the published announcement as the cog does, unless the plan or the announcement changed since the summary."""
        cog = cast("FormationManagement", interaction.client.get_cog("FormationManagement"))
        await cog.update_announcement(interaction, self.message_id, self.digest)


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


DRAFT_ITEMS: Final = (
    DraftButton,
    PublishChannel,
    PublishGo,
    PublishConfirm,
    PublishKeep,
    UpdateKeep,
    UpdateConfirm,
    SessionList,
    SessionListPage,
    SessionRemove,
    SessionKeep,
)
