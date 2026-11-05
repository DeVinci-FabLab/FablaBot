"""The screens to preview, each produced by the bot's own command or view code with sample data."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

from discord import Locale, Member, Permissions, Role

import fablabot.cogs as cogs_package
from fablabot.cogs import formation_management, help_management, message_management
from fablabot.helpers.constants import APPEL_EDIT_WINDOW, PARIS_TZ, RoleNames
from fablabot.helpers.formation.attendance import appel_content, appel_view
from fablabot.helpers.help_page import leaf_commands
from fablabot.models import Appel, Registrant
from fablabot.models.formation import FmMessageDraft, Formation
from fablabot.ui.formation import StartFmModal
from fablabot.ui.message import SuggestionView

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


async def _appel_screen(registrants: list[Registrant], present: set[int], **changes: Any) -> Screen:
    """The start message of a check, as the trainer sees it, with these members registered and these marked present."""
    capture = Capture()
    appel = _appel(**changes)
    now = APPEL_START + timedelta(minutes=30)
    names = {r.user_id: r.username for r in registrants}
    content = appel_content(appel, registrants, present, opening="Salut Camille !", now=now)
    view = appel_view(appel, registrants, present, names, now=now)
    await capture.interaction().response.send_message(content, view=view)
    return Screen(capture.sent)


def _five() -> list[Registrant]:
    """Five registered members."""
    return [Registrant(user_id=3333 + i, username=pseudo) for i, pseudo in enumerate(PSEUDOS)]


async def appel_start() -> Screen:
    """The trainer's message when the training starts: a button per registered member, nobody ticked."""
    return await _appel_screen(_five(), set())


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


SCREENS: dict[str, Callable[[], Coroutine[Any, Any, Screen]]] = {
    "help-member": help_member,
    "help-trainer": help_trainer,
    "help-admin": help_admin,
    "fm-edit": fm_edit,
    "suggest": suggest,
    "suggest-modal": suggest_modal,
    "fm-start-modal": fm_start_modal,
    "appel-start": appel_start,
    "appel-ongoing": appel_ongoing,
    "appel-validated": appel_validated,
    "appel-menus": appel_menus,
}
