"""The screens to preview, each produced by the bot's own command or view code with sample data."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, tzinfo
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

from discord import Locale, Member, Permissions, Role, TextChannel

import fablabot.cogs as cogs_package
from fablabot.cogs import formation_management, help_management, message_management
from fablabot.helpers.constants import APPEL_EDIT_WINDOW, PARIS_TZ, SESSION_EMOJIS, RoleNames
from fablabot.helpers.formation.attendance import alert_message, appel_content, appel_view, log_line, respo_messages
from fablabot.helpers.formation.update import UpdatePlan, as_draft, plan_update
from fablabot.helpers.help_page import leaf_commands
from fablabot.helpers.info_page import Facts, build_info
from fablabot.helpers.server_check import Problem
from fablabot.models import AlertAction, Appel, Registrant, TemplateFields, TrainingTemplate
from fablabot.models.formation import FmMessageDraft, Formation
from fablabot.ui.catalogue import TemplateForm, deletion_message
from fablabot.ui.draft import (
    DraftTextForm,
    done_message,
    draft_message,
    publish_message,
    removal_message,
    republish_message,
    sessions_message,
    update_message,
)
from fablabot.ui.formation import StartFmModal
from fablabot.ui.message import SuggestionView
from fablabot.ui.session import FreeSessionForm, SessionForm, added_message, picker_message

from .preview import Capture, Sent

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine
    from typing import Any

    from discord import app_commands

ROLE_FORMATIONS = 1111
TRAINER = 2222
NAMES = {ROLE_FORMATIONS: "Formations", TRAINER: "Camille"}


@dataclass
class Screen:
    """What one screen shows: the messages and modals sent, and the names behind mentions."""

    sent: list[Sent]
    names: dict[int, str] = field(default_factory=lambda: dict(NAMES))


async def _invoke(command: object, cog: object, interaction: object, **options: object) -> None:
    """Run a slash command's own callback on ``cog``, as discord.py does when a member runs it."""
    # The callback's declared type binds the cog away, so the checker cannot see the cog argument.
    await command.callback(cog, interaction, **options)  # pyright: ignore[reportAttributeAccessIssue]  # cog is bound away by the declared type


def _allowed() -> AsyncMock:
    """Stand in for the role and channel check: a preview shows the allowed path."""
    return AsyncMock(return_value=True)


def _draft() -> FmMessageDraft:
    """A draft with two formations, as the staff would fill it."""
    return FmMessageDraft(
        header="# [FORMATIONS] :loudspeaker:",
        role_id=ROLE_FORMATIONS,
        intro="Cette semaine, deux formations pour bien démarrer au FabLab.",
        fms=[
            Formation(
                emoji="🔧",
                name="Découpe laser",
                trainer_mention=f"<@{TRAINER}>",
                start_iso="2026-11-04T18:30:00+01:00",
                duration="2h",
                seats=8,
                description="Prise en main de la découpeuse et règles de sécurité.",
                registered_users=[{"user_id": 1}, {"user_id": 2}, {"user_id": 3}],
            ),
            Formation(
                emoji="🖨️",
                name="Impression 3D",
                trainer_mention=f"<@{TRAINER}>",
                start_iso="2026-11-06T12:15:00+01:00",
                duration="1h30",
                seats=6,
                excusable=False,
            ),
        ],
        end="À très vite !",
    )


def declared_commands() -> list[app_commands.Command[Any, ..., Any]]:
    """Every leaf command declared on a cog class, as the tree holds them once the cogs are loaded."""
    declared = [item for name in cogs_package.__all__ for item in getattr(cogs_package, name).__cog_app_commands__]
    return leaf_commands(declared)


def _member(*role_names: str) -> MagicMock:
    """A member holding the named roles, with no permission of its own."""
    member = MagicMock(spec=Member)
    member.id = 3333
    member.roles = [_role(name) for name in role_names]
    member.guild_permissions = Permissions.none()
    return member


def _role(name: str) -> MagicMock:
    """A role stub with a name."""
    role = MagicMock(spec=Role)
    role.name = name
    return role


async def _help_for(member: MagicMock, locale: Locale) -> Screen:
    """``/help`` run by ``member`` on a server that holds every role the bot knows."""
    capture = Capture()
    interaction = capture.interaction()
    interaction.user = member
    interaction.guild.roles = [_role(value) for key, value in vars(RoleNames).items() if key.isupper()]
    interaction.guild_locale = locale
    bot = MagicMock()
    bot.tree.walk_commands.side_effect = lambda: iter(declared_commands())
    with patch.object(help_management, "ensure_command_context", _allowed()):
        await _invoke(help_management.HelpManagement.show_help, help_management.HelpManagement(bot), interaction)
    return Screen(capture.sent)


async def help_member() -> Screen:
    """``/help`` for a verified member: the commands open to everyone, and how to get more."""
    return await _help_for(_member(RoleNames.MEMBER_VERIFIED), Locale.american_english)


async def help_trainer() -> Screen:
    """``/help`` for a training manager on a server with no ``F - ...`` role: the training commands and the role summary."""
    return await _help_for(_member(RoleNames.TRAININGS_MANAGER), Locale.american_english)


async def help_admin() -> Screen:
    """``/help`` for an administrator who is also Bureau and holds every permission, in French."""
    member = _member(RoleNames.BUREAU, RoleNames.ADMIN)
    member.guild_permissions = Permissions.all()
    return await _help_for(member, Locale.french)


async def fm_edit() -> Screen:
    """``/fm edit``: the draft preview and one button per formation."""
    capture = Capture()
    cog = MagicMock()
    cog.get_guild_draft.return_value = _draft()
    with patch.object(formation_management, "ensure_command_context", _allowed()):
        await _invoke(formation_management.FormationManagement.fm_edit, cog, capture.interaction())
    return Screen(capture.sent)


async def fm_appel() -> Screen:
    """``/fm appel`` on a server with two open checks: the menu of trainings, each with its start."""
    capture = Capture()
    older = _appel(id=6, name="Impression 3D", starts_at=APPEL_START - timedelta(days=2))
    with (
        patch.object(formation_management, "ensure_command_context", _allowed()),
        patch.object(formation_management.attendance, "open_in", return_value=[_appel(), older]),
    ):
        await _invoke(formation_management.FormationManagement.fm_appel, MagicMock(), capture.interaction())
    return Screen(capture.sent)


async def suggest() -> Screen:
    """``/suggest``: the recipient menu, the anonymity button and the button that opens the modal."""
    capture = Capture()
    with patch.object(message_management, "ensure_command_context", _allowed()):
        await _invoke(message_management.MessageManagement.suggest, MagicMock(), capture.interaction())
    return Screen(capture.sent)


async def suggest_modal() -> Screen:
    """The modal that opens when a recipient is chosen and "Rédiger la suggestion" is clicked."""
    capture = Capture()
    view = SuggestionView(MagicMock())
    view.selected_recipient = "formation"
    await view.open_modal_button.callback(capture.interaction())
    return Screen(capture.sent)


async def fm_start_modal() -> Screen:
    """``/fm start``: the modal asking for the intro, the conclusion and an optional forms URL."""
    capture = Capture()
    await capture.interaction().response.send_modal(StartFmModal(MagicMock(), ROLE_FORMATIONS))
    return Screen(capture.sent)


_TEMPLATES = [
    ("SRG + Imp 3D", "45 min", 5, "Formation obligatoire. Ramenez votre PC avec Bambu Studio installé.", True),
    ("Formation GitHub", "1 h 30", 10, "", False),
    ("Formation KiCad", "1 h 30", 10, "", False),
    ("Impression résine", "1 h", 5, "", False),
    ("Formation inserts", "1 h", 5, "", False),
    ("Citrouilles en crochet", "1 h 30", 4, "", False),
    ("Découpe laser", "1 h", 6, "", False),
    ("Soudure", "1 h", 4, "", False),
    ("Fusion 360", "2 h", 8, "", False),
    ("Brodeuse", "1 h", 3, "", False),
    ("Thermoformage", "1 h", 4, "", False),
    ("Reliure", "2 h", 6, "", False),
    ("Sérigraphie", "2 h", 6, "", False),
    ("Tournage", "1 h 30", 3, "", False),
]


def _templates() -> list[TrainingTemplate]:
    """The 14 templates of the validated mockup, as the list holds them, sorted by name without regard to case."""
    stored = [TrainingTemplate(number, 1234, TemplateFields(*row)) for number, row in enumerate(_TEMPLATES, start=1)]
    return sorted(stored, key=lambda template: template.fields.name.casefold())


async def catalogue_list() -> Screen:
    """``/fm modeles`` on a server with 14 templates: the first page of 10, its page row and the three buttons."""
    capture = Capture()
    with (
        patch.object(formation_management, "ensure_command_context", _allowed()),
        patch("fablabot.services.catalogue.list_templates", return_value=_templates()),
    ):
        await _invoke(formation_management.FormationManagement.fm_modeles, MagicMock(), capture.interaction())
    return Screen(capture.sent)


async def catalogue_form() -> Screen:
    """The form of a new template, filled as the staff would fill it, with Excusable preset to Oui."""
    capture = Capture()
    form = TemplateForm(None)
    form.name_input.default = "SRG + Imp 3D"
    form.duration_input.default = "45 min"
    form.seats_input.default = "5"
    form.description_input.default = _TEMPLATES[0][3]
    await capture.interaction().response.send_modal(form)
    return Screen(capture.sent)


async def catalogue_delete() -> Screen:
    """The question that asks to confirm the deletion of a template, with its two buttons."""
    capture = Capture()
    content, view = deletion_message(_templates()[-4])
    await capture.interaction().response.send_message(content, view=view, ephemeral=True)
    return Screen(capture.sent)


ANNOUNCE_CHANNEL = 5555
_DRAFT_NAMES = {ROLE_FORMATIONS: "Formations", 2222: "lea.mkr", 3333: "hugo3d", 4444: "camille", ANNOUNCE_CHANNEL: "annonces"}
_DRAFT_INTRO = "Oyé oyé habitants du DeVinci Fablab, voici les formations de la semaine !"
_DRAFT_SESSIONS = [
    ("🩷", "SRG + Imp 3D", 2222, "2026-10-12T10:00:00+02:00", 5),
    ("❤️‍🔥", "SRG + Imp 3D", 2222, "2026-10-12T11:00:00+02:00", 5),
    ("🧡", "SRG + Imp 3D", 2222, "2026-10-12T14:00:00+02:00", 5),
    ("😼", "Formation GitHub", 3333, "2026-10-14T11:45:00+02:00", 10),
    ("☣️", "Impression résine", 4444, "2026-10-15T13:45:00+02:00", 5),
]


def _sample_draft() -> FmMessageDraft:
    """The draft of the validated mockup: five sessions, an intro and the role to notify."""
    fms = [
        Formation(emoji=emoji, name=name, trainer_mention=f"<@{trainer}>", start_iso=start, duration="45 min", seats=seats)
        for emoji, name, trainer, start, seats in _DRAFT_SESSIONS
    ]
    return FmMessageDraft(header="# [FORMATIONS]", role_id=ROLE_FORMATIONS, intro=_DRAFT_INTRO, fms=fms, end="À très vite !")


def _sample_guild() -> MagicMock:
    """A server that knows the role to notify and the announcement channel."""
    guild = MagicMock()
    role = MagicMock(spec=Role)
    role.id = ROLE_FORMATIONS
    channel = MagicMock(spec=TextChannel)
    channel.id = ANNOUNCE_CHANNEL
    guild.get_role.return_value = role
    guild.get_channel.return_value = channel
    return guild


async def draft() -> Screen:
    """``/fm brouillon`` on a draft of five sessions sorted by start, with its six buttons."""
    capture = Capture()
    content, embed, view = draft_message(_sample_guild(), _sample_draft())
    await capture.interaction().response.send_message(content or None, embed=embed, view=view, ephemeral=True)
    return Screen(capture.sent, dict(_DRAFT_NAMES))


_LIST_TRAINERS = (2222, 2222, 2222, 3333, 4444, 3333, 4444)


def _long_draft() -> FmMessageDraft:
    """The draft of the validated list mockup: 14 sessions over four days, the trainers of the sample."""
    starts = [
        "2026-10-12T10:00:00+02:00",
        "2026-10-12T11:00:00+02:00",
        "2026-10-12T14:00:00+02:00",
        "2026-10-13T11:45:00+02:00",
        "2026-10-13T12:30:00+02:00",
        "2026-10-14T11:45:00+02:00",
        "2026-10-15T13:45:00+02:00",
    ]
    names = ["SRG + Imp 3D"] * 5 + ["Formation GitHub", "Impression résine"]
    emojis = ["🩷", "❤️‍🔥", "🧡", "💛", "💚", "😼", "☣️"]
    fms = [
        Formation(emoji=emoji, name=name, trainer_mention=f"<@{trainer}>", start_iso=start, duration="45 min", seats=5)
        for emoji, name, trainer, start in zip(emojis, names, _LIST_TRAINERS, starts, strict=True)
    ]
    later = [replace(fm, start_iso=(fm.start_dt + timedelta(days=7)).isoformat()) for fm in fms]
    return FmMessageDraft(header="# [FORMATIONS]", role_id=ROLE_FORMATIONS, intro=_DRAFT_INTRO, fms=[*fms, *later], end="")


def _named_guild() -> MagicMock:
    """A server whose members have the pseudos of the sample."""
    guild = _sample_guild()
    guild.get_member.side_effect = lambda member_id: MagicMock(display_name=_DRAFT_NAMES[member_id])
    return guild


async def session_list() -> Screen:
    """The question that asks which session to change, the 14 sessions of a long draft on its one page."""
    capture = Capture()
    content, view = sessions_message(_named_guild(), _long_draft(), action="remove", page=0)
    await capture.interaction().response.send_message(content, view=view, ephemeral=True)
    return Screen(capture.sent, dict(_DRAFT_NAMES))


async def session_remove() -> Screen:
    """The question that asks for the confirmation of a removal."""
    capture = Capture()
    content, view = removal_message(_sample_draft().fms[3])
    await capture.interaction().response.send_message(content, view=view, ephemeral=True)
    return Screen(capture.sent, dict(_DRAFT_NAMES))


async def draft_text_form() -> Screen:
    """The Intro / conclusion form, filled with what the draft holds and the role to notify preset."""
    capture = Capture()
    await capture.interaction().response.send_modal(DraftTextForm(_sample_guild(), _sample_draft()))
    return Screen(capture.sent, dict(_DRAFT_NAMES))


async def draft_publish() -> Screen:
    """The question that asks where to publish, the channel of the last announcement already chosen."""
    capture = Capture()
    guild = _sample_guild()
    content, view = publish_message(guild.get_channel(ANNOUNCE_CHANNEL))
    await capture.interaction().response.send_message(content, view=view, ephemeral=True)
    return Screen(capture.sent, dict(_DRAFT_NAMES))


async def draft_republish() -> Screen:
    """The warning that Publier gives when an announcement is already published and the draft changed since."""
    capture = Capture()
    content, view = republish_message(ANNOUNCE_CHANNEL)
    await capture.interaction().response.send_message(content, view=view, ephemeral=True)
    return Screen(capture.sent, dict(_DRAFT_NAMES))


_UPDATE_NOW = datetime(2026, 11, 9, 12, 0, tzinfo=PARIS_TZ)


def _signed(count: int) -> list[dict[str, int]]:
    """The sign-ups of a session: ``count`` members, as many as the summary says."""
    return [{"user_id": 100 + n} for n in range(count)]


def _update_plan() -> UpdatePlan:
    """The plan of an update: a start moved, seats cut, a session added and one removed, the started session left as it is."""
    held = Formation(
        emoji="🧡", name="SRG + Imp 3D", trainer_mention=f"<@{TRAINER}>", start_iso="2026-11-09T10:30:00+01:00",
        duration="45 min", seats=5, registered_users=_signed(3), notified_at_start=True,
    )  # fmt: skip
    moved = Formation(
        emoji="💛", name="SRG + Imp 3D", trainer_mention=f"<@{TRAINER}>", start_iso="2026-11-10T18:00:00+01:00",
        duration="45 min", seats=5, registered_users=_signed(4), waitlisted_users=_signed(1),
    )  # fmt: skip
    cut = Formation(
        emoji="💚", name="Formation GitHub", trainer_mention="<@3333>", start_iso="2026-11-12T11:45:00+01:00",
        duration="45 min", seats=10, registered_users=_signed(8),
    )  # fmt: skip
    dropped = Formation(
        emoji="💙", name="Impression résine", trainer_mention="<@4444>", start_iso="2026-11-12T13:45:00+01:00",
        duration="45 min", seats=5, registered_users=_signed(3), waitlisted_users=_signed(1),
    )  # fmt: skip
    published = FmMessageDraft(
        header="# [FORMATIONS]",
        role_id=ROLE_FORMATIONS,
        intro=_DRAFT_INTRO,
        fms=[held, moved, cut, dropped],
        end="À très vite !",
    )
    draft = as_draft(published)
    draft.fms[1] = replace(moved, start_iso="2026-11-11T18:00:00+01:00", registered_users=[], waitlisted_users=[])
    draft.fms[2] = replace(cut, seats=6, registered_users=[])
    draft.fms.pop()
    draft.fms.append(
        Formation(emoji="💜", name="Découpe laser", trainer_mention="<@2222>", start_iso="2026-11-13T14:00:00+01:00",
        duration="45 min", seats=5)
    )  # fmt: skip
    plan = plan_update(published, draft, now=_UPDATE_NOW, retired=frozenset())
    assert isinstance(plan, UpdatePlan), plan
    return plan


async def update_summary() -> Screen:
    """The summary of an update: a start moved, seats cut, a session added and one removed, the started one left as it is."""
    embeds, view = update_message(_update_plan(), ANNOUNCE_CHANNEL, 5000)
    capture = Capture()
    await capture.interaction().response.send_message(None, embeds=embeds, view=view, ephemeral=True)
    return Screen(capture.sent, dict(_DRAFT_NAMES))


async def update_done() -> Screen:
    """The answer to a confirmed update: how many sessions changed, the reactions Discord refused to clear, the draft button."""
    content, view = done_message(_update_plan(), ANNOUNCE_CHANNEL, uncleared=("💙",))
    capture = Capture()
    await capture.interaction().response.send_message(content, view=view, ephemeral=True)
    return Screen(capture.sent, dict(_DRAFT_NAMES))


class _October(datetime):
    """A datetime whose clock stands on 1 October 2026, so that ``JJ/MM`` days are written the same on every run."""

    @classmethod
    def now(cls, tz: tzinfo | None = None) -> _October:
        return cls(2026, 10, 1, 12, 0, tzinfo=tz)


async def session_pick() -> Screen:
    """The question that asks which training to add a session of, the 14 templates on its one page."""
    capture = Capture()
    with patch("fablabot.services.catalogue.list_templates", return_value=_templates()):
        content, view = picker_message(1234, 0)
    await capture.interaction().response.send_message(content, view=view, ephemeral=True)
    return Screen(capture.sent)


async def session_form() -> Screen:
    """The form of a session of the first template: the trainer still to choose, the day, the seats and the description."""
    capture = Capture()
    template = next(t for t in _templates() if t.fields.name == _TEMPLATES[0][0])
    with patch("fablabot.ui.session.datetime", _October):
        form = SessionForm(template=template, trainer_id=None, day=date(2026, 10, 12), description=template.fields.description)
    await capture.interaction().response.send_modal(form)
    return Screen(capture.sent)


async def session_free_form() -> Screen:
    """The form of a one-off session, as Session libre opens it: five empty fields and their hints."""
    capture = Capture()
    await capture.interaction().response.send_modal(FreeSessionForm())
    return Screen(capture.sent)


async def session_added() -> Screen:
    """The answer to a sent session form: the session, its emoji, the count and the three buttons that go on."""
    capture = Capture()
    template = next(t for t in _templates() if t.fields.name == _TEMPLATES[0][0])
    session = Formation(
        emoji=SESSION_EMOJIS[5],  # the sixth, as the count says
        name=template.fields.name,
        trainer_mention="<@2222>",
        start_iso="2026-10-12T10:00:00+02:00",
        duration=template.fields.duration,
        seats=template.fields.seats,
    )
    content, view = added_message(session, 6, template.id)
    await capture.interaction().response.send_message(content, view=view, ephemeral=True)
    return Screen(capture.sent, dict(_DRAFT_NAMES))


async def info() -> Screen:
    """``/info`` on a server with two warnings: the bot, the database and the instance, the settings and two problems to fix."""
    capture = Capture()
    facts = Facts(
        version="0.14.0",
        uptime=timedelta(hours=3),
        latency_ms=42,
        schema_version=4,
        database_bytes=1_200_000,
        copies=6,
        servers=4,
        commands=9,
        watched=1,
        commands_channel_id=1,
        log_channel_id=2,
        language=("fr", "fr"),
        revision="8822a20",
    )
    problems = [
        Problem("integrations_role_missing", "/log: Pôle Numérique"),
        Problem("integrations_role_missing", "/user: CoDir"),
    ]
    await capture.interaction().followup.send(embed=build_info(facts, problems), ephemeral=True)
    return Screen(capture.sent, {1: "commandes-bot", 2: "logs-bot"})


APPEL_START = datetime(2026, 11, 5, 18, 0, tzinfo=PARIS_TZ)
PSEUDOS = ["lea.mkr", "hugo3d", "ines_lab", "tom_r", "sarah.fab"]


def _appel(**changes: Any) -> Appel:
    """The check of the laser cutting training, two hours from 18:00, as the trainer's message shows it."""
    appel = Appel(
        id=7,
        guild_id=1234,
        message_id=5000,
        channel_id=42,
        position=0,
        name="Découpe laser",
        duration="2h",
        excusable=False,
        trainer_id=TRAINER,
        starts_at=APPEL_START,
        ends_at=APPEL_START + timedelta(hours=2),
        closes_at=APPEL_START + APPEL_EDIT_WINDOW,
        thread_id=None,
        validated_by=None,
        validated_at=None,
        present_count=None,
        absent_count=None,
        alert_sent=False,
        handled_by=None,
        handled_at=None,
        handled_action=None,
    )
    return replace(appel, **changes)


async def _appel_screen(registrants: list[Registrant], present: set[int], *, thread: bool = False, **changes: Any) -> Screen:
    """The start message of a check, as the trainer sees it, with these members registered and these marked present."""
    capture = Capture()
    appel = _appel(**changes)
    now = APPEL_START + timedelta(minutes=30)
    names = {r.user_id: r.username for r in registrants}
    content = appel_content(appel, registrants, present, opening="Salut Camille !", now=now, thread=thread)
    view = appel_view(appel, registrants, present, names, now=now)
    await capture.interaction().response.send_message(content, view=view)
    return Screen(capture.sent)


def _five() -> list[Registrant]:
    """Five registered members."""
    return [Registrant(user_id=3333 + i, username=pseudo) for i, pseudo in enumerate(PSEUDOS)]


async def appel_start() -> Screen:
    """The trainer's message when the training starts: a button per registered member, nobody ticked."""
    return await _appel_screen(_five(), set())


async def appel_thread() -> Screen:
    """The message the trainer finds in the private thread when their DMs are closed."""
    return await _appel_screen(_five(), set(), thread=True)


async def appel_ongoing() -> Screen:
    """The same message with three members ticked and the counter up to date."""
    return await _appel_screen(_five(), {3333, 3335, 3337})


async def appel_validated() -> Screen:
    """The message after the validation: the saved counts and the update button."""
    saved = {
        "validated_by": TRAINER,
        "validated_at": APPEL_START + timedelta(minutes=45),
        "present_count": 3,
        "absent_count": 2,
    }
    return await _appel_screen(_five(), {3333, 3335, 3337}, **saved)


async def appel_menus() -> Screen:
    """A training of thirty members: two menus of twenty-five and five, a few members ticked."""
    registrants = [Registrant(user_id=4000 + i, username=f"membre{i + 1:02}") for i in range(30)]
    return await _appel_screen(registrants, {4000, 4001, 4026})


def _validated_appel() -> Appel:
    """The check of the laser cutting training, saved by the trainer with three of five present."""
    return _appel(validated_by=TRAINER, validated_at=APPEL_START + timedelta(minutes=45), present_count=3, absent_count=2)


def _people() -> dict[int, str]:
    """The names behind the mentions of the Respo message and the log line."""
    return {**NAMES, **{r.user_id: r.username for r in _five()}}


async def appel_respo() -> Screen:
    """The private message each Respo Formation gets after the validation: who was present and who was not."""
    capture = Capture()
    ids = [r.user_id for r in _five()]
    (text,) = respo_messages(_validated_appel(), [ids[0], ids[2], ids[4]], [ids[1], ids[3]], first=True)
    await capture.interaction().response.send_message(text)
    return Screen(capture.sent, _people())


async def appel_log() -> Screen:
    """The line the log channel gets after the validation: the counts, nobody else named."""
    capture = Capture()
    appel = _validated_appel()
    await capture.interaction().response.send_message(log_line(appel, 3, 2, first=True))
    return Screen(capture.sent, _people())


RESPO = 4444


async def _alert_screen(**changes: Any) -> Screen:
    """The alert a Respo Formation gets when nobody made the check, as it stands with these changes."""
    capture = Capture()
    content, view = alert_message(_appel(**changes))
    await capture.interaction().response.send_message(content, view=view)
    return Screen(capture.sent, {**NAMES, RESPO: "Morgan"})


def _answered(action: AlertAction, **changes: Any) -> dict[str, Any]:
    """The fields an answer to the alert sets: Morgan, seven minutes after the end of the slot."""
    return {"handled_by": RESPO, "handled_at": APPEL_START + timedelta(hours=2, minutes=7), "handled_action": action, **changes}


async def appel_alert() -> Screen:
    """The alert the Respo Formation get when the slot is over and nobody made the check, with its three answers."""
    return await _alert_screen()


async def appel_alert_relaunched() -> Screen:
    """The alert once a Respo Formation relaunched the trainer: the answer in place of the question, buttons off."""
    return await _alert_screen(**_answered(AlertAction.RELAUNCH))


async def appel_alert_ignored() -> Screen:
    """The alert once a Respo Formation ignored it."""
    return await _alert_screen(**_answered(AlertAction.IGNORE))


async def appel_alert_cancelled() -> Screen:
    """The alert once a Respo Formation cancelled the training: it counts as not held."""
    return await _alert_screen(**_answered(AlertAction.CANCEL))


async def appel_alert_done() -> Screen:
    """The alert once the trainer saved the check: the Respo Formation read who did it."""
    return await _alert_screen(**_answered(AlertAction.DONE, handled_by=TRAINER))


SCREENS: dict[str, Callable[[], Coroutine[Any, Any, Screen]]] = {
    "help-member": help_member,
    "help-trainer": help_trainer,
    "help-admin": help_admin,
    "info": info,
    "fm-edit": fm_edit,
    "fm-appel": fm_appel,
    "catalogue-list": catalogue_list,
    "catalogue-form": catalogue_form,
    "catalogue-delete": catalogue_delete,
    "draft": draft,
    "draft-text-form": draft_text_form,
    "draft-publish": draft_publish,
    "draft-republish": draft_republish,
    "update-summary": update_summary,
    "update-done": update_done,
    "session-pick": session_pick,
    "session-form": session_form,
    "session-free-form": session_free_form,
    "session-added": session_added,
    "session-list": session_list,
    "session-remove": session_remove,
    "suggest": suggest,
    "suggest-modal": suggest_modal,
    "fm-start-modal": fm_start_modal,
    "appel-start": appel_start,
    "appel-thread": appel_thread,
    "appel-ongoing": appel_ongoing,
    "appel-validated": appel_validated,
    "appel-menus": appel_menus,
    "appel-respo": appel_respo,
    "appel-log": appel_log,
    "appel-alert": appel_alert,
    "appel-alert-relaunched": appel_alert_relaunched,
    "appel-alert-ignored": appel_alert_ignored,
    "appel-alert-cancelled": appel_alert_cancelled,
    "appel-alert-done": appel_alert_done,
}
