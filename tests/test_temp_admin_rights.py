"""The temporary admin role opens only the commands that name it."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

from conftest import make_interaction
from discord import Member, TextChannel, VoiceChannel
import pytest

from fablabot.cogs import formation_management
from fablabot.cogs.channel_management import ChannelManagement
from fablabot.cogs.log_management import LogManagement
from fablabot.cogs.message_management import MessageManagement
from fablabot.cogs.user_management import UserManagement
from fablabot.cogs.welcome_management import WelcomeManagement
from fablabot.helpers.constants import ADMIN_ROLES, RoleNames
from fablabot.helpers.utils import ensure_command_context

USER_ID = 418003123456789012
USER_NAME = "kevin_secret_name"
DISPLAY_NAME = "Kevin Secret"
GUILD_ROLE_NAMES = (
    *ADMIN_ROLES,
    RoleNames.ADMIN_TEMP,
    RoleNames.BUREAU,
    RoleNames.TRAININGS_MANAGER,
    RoleNames.CODIR,
    RoleNames.DIGITAL_MANAGER,
    RoleNames.DIGITAL_POLE,
)


def _role(name: str) -> MagicMock:
    role = MagicMock()
    role.name = name
    return role


def _caller(*role_names: str) -> MagicMock:
    """An interaction whose caller holds exactly ``role_names``, in a guild holding every staff role."""
    interaction = make_interaction()
    interaction.guild.roles = [_role(n) for n in GUILD_ROLE_NAMES]
    interaction.user.roles = [r for r in interaction.guild.roles if r.name in role_names]
    interaction.channel = MagicMock(spec=TextChannel)
    interaction.channel.send = AsyncMock()
    return interaction


@pytest.fixture(autouse=True)
def _in_commands_channel():  # noqa: ANN202
    """Skip the commands-channel check so the real role check runs."""
    with patch("fablabot.helpers.utils._is_in_allowed_channel", AsyncMock(return_value=True)):
        yield


REFUSED = {
    "channel.text_delete": lambda i: ChannelManagement.text_delete.callback(MagicMock(), i, channel=MagicMock(spec=TextChannel)),
    "channel.vocal_delete": lambda i: ChannelManagement.vocal_delete.callback(MagicMock(), i, channel=MagicMock(spec=VoiceChannel)),
    "log.set": lambda i: LogManagement.log_set.callback(MagicMock(), i, channel=MagicMock(spec=TextChannel)),
    "log.export": lambda i: LogManagement.log_export.callback(MagicMock(), i, date=None),
    "welcome.verify": lambda i: WelcomeManagement.welcome_verify.callback(MagicMock(), i, enable=True),
    "msg.list": lambda i: MessageManagement.msg_list.callback(MagicMock(), i),
}


def test_temp_admin_role_is_not_an_admin_role() -> None:
    assert RoleNames.ADMIN_TEMP not in ADMIN_ROLES
    assert RoleNames.ADMIN_TEMP in formation_management.ALLOWED_ROLES


@pytest.mark.parametrize("command", list(REFUSED))
async def test_temp_admin_only_is_refused(command: str) -> None:
    interaction = _caller(RoleNames.ADMIN_TEMP)

    await REFUSED[command](interaction)

    interaction.response.send_message.assert_awaited_once()
    args = interaction.response.send_message.await_args
    assert args.kwargs["ephemeral"] is True
    assert "Demandez ce rôle au staff" in args.args[0]
    interaction.response.defer.assert_not_awaited()


async def test_temp_admin_only_passes_fm_role_check() -> None:
    interaction = _caller(RoleNames.ADMIN_TEMP)
    allowed = await ensure_command_context(
        logging.getLogger("test"),
        "fm.preview",
        interaction,
        required_roles=formation_management.ALLOWED_ROLES,
    )
    assert allowed is True


def test_temp_admin_only_cannot_assign_any_role() -> None:
    interaction = _caller(RoleNames.ADMIN_TEMP)
    member = interaction.user
    assert isinstance(member, Member)
    for name in (RoleNames.PRESIDENT, RoleNames.ADMIN_TEMP, "F - Laser", "Pôle Numérique", RoleNames.SBIRE_BUREAU):
        assert UserManagement._can_assign_role(member, _role(name)) is False  # noqa: SLF001


def test_temp_admin_keeps_rights_of_other_roles() -> None:
    trainer_lead = _caller(RoleNames.ADMIN_TEMP, RoleNames.TRAININGS_MANAGER).user
    president = _caller(RoleNames.ADMIN_TEMP, RoleNames.PRESIDENT).user
    assert UserManagement._can_assign_role(trainer_lead, _role("F - Laser")) is True  # noqa: SLF001
    assert UserManagement._can_assign_role(trainer_lead, _role(RoleNames.PRESIDENT)) is False  # noqa: SLF001
    assert UserManagement._can_assign_role(president, _role(RoleNames.SECRETARY)) is True  # noqa: SLF001


async def test_role_refusal_logs_kind_and_ids_only(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    interaction = _caller(RoleNames.ADMIN_TEMP)

    await REFUSED["log.export"](interaction)

    lines = [r.getMessage() for r in caplog.records if "refused" in r.getMessage()]
    assert len(lines) == 1
    assert "[log.export]" in lines[0]
    assert "kind=missing_right" in lines[0]
    assert "guild_id=1234" in lines[0]
    assert f"user_id={USER_ID}" in lines[0]
    assert USER_NAME not in lines[0]
    assert DISPLAY_NAME not in lines[0]
