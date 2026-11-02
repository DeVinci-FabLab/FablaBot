"""A staff view or modal checks the clicker's roles again, with the rule of the command that opened it."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from conftest import cog_bot, make_interaction, make_member, make_role
import pytest

from fablabot.cogs import formation_management, message_management
from fablabot.helpers.constants import FORMATION_ROLES, MESSAGE_DM_ROLES, MESSAGE_ROLES, RoleNames
from fablabot.helpers.utils import RIGHT_LOST_MESSAGE
from fablabot.models import FmCommand, Formation
from fablabot.models.message import MessageDraft, MsgCommand, MsgReactionEvent
from fablabot.ui import formation as fm_ui
from fablabot.ui import message as msg_ui
from fablabot.ui.user import RIGHT_LOST_MESSAGE as USER_RIGHT_LOST_MESSAGE

if TYPE_CHECKING:
    from collections.abc import Callable

GUILD_ID = 1234


def _formation() -> Formation:
    return Formation(
        emoji="🔧",
        name="Arduino",
        trainer_mention="<@1>",
        start_iso="2099-06-10T18:00:00+02:00",
        duration="2h",
        seats=5,
    )


def _reaction() -> MsgReactionEvent:
    return MsgReactionEvent(emoji="🅰", action_type="user_dm", message_content="Bonjour", target_id=0, target_name="")


def _edit_view() -> fm_ui._EditFormationView:
    return fm_ui._EditFormationView(cog_bot(), 1, _formation())


# Every staff view and modal of the two files, built the way its command builds it, with the rule of that command.
MESSAGE_SURFACES: dict[str, tuple[Callable[[], Any], set[str]]] = {
    "bulk_dm_view": (lambda: msg_ui._BulkDMView(make_member(1), 2, "salut"), MESSAGE_DM_ROLES),
    "bulk_dm_modal": (lambda: msg_ui.BulkDMModal(cog_bot()), MESSAGE_DM_ROLES),
    "start_modal": (lambda: msg_ui.StartMessageModal(cog_bot()), MESSAGE_ROLES),
    "reaction_text_modal": (lambda: msg_ui._ReactionMessageInputModal(cog_bot(), GUILD_ID, _reaction()), MESSAGE_ROLES),
    "reaction_target_view": (lambda: msg_ui._ReactionTargetView(cog_bot(), GUILD_ID, 5, "🅰", "channel"), MESSAGE_ROLES),
    "reaction_type_view": (lambda: msg_ui._ReactionActionTypeSelectView(cog_bot(), GUILD_ID, 5, "🅰"), MESSAGE_ROLES),
    "tracked_select_view": (lambda: msg_ui.TrackedMessageSelectView(cog_bot(), [], MsgCommand.STOP), MESSAGE_ROLES),
    "unlink_view": (
        lambda: msg_ui._UnlinkReactionSelectView(cog_bot(), MessageDraft(content="x", reactions=[])),
        MESSAGE_ROLES,
    ),
}
FORMATION_SURFACES: dict[str, tuple[Callable[[], Any], set[str]]] = {
    "start_modal": (lambda: fm_ui.StartFmModal(cog_bot(), 1), FORMATION_ROLES),
    "edit_text_modal": (lambda: fm_ui.EditTextModal(cog_bot(), 1, "intro", "fin", "https://example.org"), FORMATION_ROLES),
    "add_modal": (lambda: fm_ui.AddFmModal(cog_bot(), _formation()), FORMATION_ROLES),
    "select_view": (lambda: fm_ui.FormationSelectView(cog_bot(), [_formation()], FmCommand.REMOVE), FORMATION_ROLES),
    "edit_view": (_edit_view, FORMATION_ROLES),
    "emoji_modal": (lambda: fm_ui._EditEmojiModal(_edit_view()), FORMATION_ROLES),
    "name_modal": (lambda: fm_ui._EditNameDescriptionModal(_edit_view()), FORMATION_ROLES),
    "datetime_modal": (lambda: fm_ui._EditDatetimeModal(_edit_view()), FORMATION_ROLES),
    "duration_modal": (lambda: fm_ui._EditDurationSeatsModal(_edit_view()), FORMATION_ROLES),
    "trainer_view": (lambda: fm_ui._SelectTrainerView(_edit_view()), FORMATION_ROLES),
}
SURFACES = {f"message.{name}": surface for name, surface in MESSAGE_SURFACES.items()} | {
    f"formation.{name}": surface for name, surface in FORMATION_SURFACES.items()
}


def _clicker(*role_names: str) -> Any:
    """An interaction whose user holds exactly these roles."""
    interaction = make_interaction(guild_id=GUILD_ID)
    interaction.user.roles = [make_role(name) for name in role_names]
    return interaction


@pytest.mark.parametrize("surface", SURFACES)
async def test_a_member_without_the_role_is_refused_privately(surface: str) -> None:
    """Whoever clicks or submits without a role of the command gets the shared refusal, and nothing runs."""
    build, _roles = SURFACES[surface]
    click = _clicker(RoleNames.MEMBER_VERIFIED)

    allowed = await build().interaction_check(click)

    assert allowed is False
    click.response.send_message.assert_awaited_once_with(RIGHT_LOST_MESSAGE, ephemeral=True)


@pytest.mark.parametrize("surface", SURFACES)
async def test_a_member_who_still_holds_a_role_of_the_command_passes(surface: str) -> None:
    """Each role the command accepts keeps the buttons, with no message."""
    build, roles = SURFACES[surface]
    for role in sorted(roles):
        click = _clicker(role)

        assert await build().interaction_check(click) is True
        click.response.send_message.assert_not_awaited()


@pytest.mark.parametrize(
    ("surface", "role"),
    [
        ("message.start_modal", RoleNames.ADMIN_TEMP),
        ("message.unlink_view", RoleNames.ADMIN_TEMP),
        ("message.bulk_dm_view", RoleNames.ADMIN),
        ("message.bulk_dm_modal", RoleNames.PRESIDENT),
        ("formation.select_view", RoleNames.BUREAU),
        ("formation.edit_view", RoleNames.BUREAU),
    ],
)
async def test_the_rule_is_the_one_of_the_command_not_a_neighbour_s(surface: str, role: str) -> None:
    """A temporary admin may not use message views, only Bureau may send bulk DMs, and Bureau has no say on trainings."""
    build, _roles = SURFACES[surface]
    click = _clicker(role)

    assert await build().interaction_check(click) is False


def test_the_views_read_the_constants_of_the_commands() -> None:
    """The cogs and the views share one set each, so a command cannot drift from its views."""
    assert message_management.ALLOWED_ROLES is MESSAGE_ROLES
    assert message_management._DM_ROLES is MESSAGE_DM_ROLES
    assert formation_management.ALLOWED_ROLES is FORMATION_ROLES


def test_the_refusal_is_the_one_of_the_user_confirm() -> None:
    """The message is shared with the `/user` Confirm, not copied."""
    assert RIGHT_LOST_MESSAGE is USER_RIGHT_LOST_MESSAGE


async def test_the_refusal_is_logged_by_kind_without_a_name(caplog: pytest.LogCaptureFixture) -> None:
    """The log line names the command, the kind, the server and the user ID only."""
    click = _clicker(RoleNames.MEMBER_VERIFIED)

    await msg_ui.StartMessageModal(cog_bot()).interaction_check(click)

    user_id = click.user.id
    assert f"refused kind=right_lost_before_confirm guild_id={GUILD_ID} user_id={user_id}" in caplog.text
    assert click.user.name not in caplog.text


async def test_a_public_flow_stays_open() -> None:
    """`/suggest` needs no role: its view and modal have no role gate."""
    click = _clicker()

    assert await msg_ui.SuggestionView(cog_bot()).interaction_check(click) is True
    assert await msg_ui._SuggestionModal(cog_bot(), "communication", anonymous_flag=True).interaction_check(click) is True
    click.response.send_message.assert_not_awaited()
