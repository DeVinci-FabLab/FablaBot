"""Bureau may run /vocal create for a temporary channel only."""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

from conftest import make_interaction
from discord import CategoryChannel, TextChannel, VoiceChannel
import pytest

from fablabot.cogs.channel_management import ChannelManagement
from fablabot.helpers.constants import ADMIN_ROLES, RoleNames

ROLE_NAMES = (*ADMIN_ROLES, RoleNames.BUREAU, RoleNames.MEMBER_VERIFIED)


def _caller(*held: str) -> MagicMock:
    """An interaction whose caller holds exactly ``held``, in a guild holding every role used here."""
    interaction = make_interaction()
    roles = []
    for name in ROLE_NAMES:
        role = MagicMock()
        role.name = name
        roles.append(role)
    interaction.guild.roles = roles
    interaction.user.roles = [r for r in roles if r.name in held]
    interaction.channel = MagicMock(spec=TextChannel)
    return interaction


def _category() -> MagicMock:
    category = MagicMock(spec=CategoryChannel)
    category.name = "Atelier"
    category.voice_channels = []
    return category


@pytest.fixture(autouse=True)
def _in_commands_channel():
    """Skip the commands-channel check so the real role check runs."""
    with patch("fablabot.helpers.utils._is_in_allowed_channel", AsyncMock(return_value=True)):
        yield


@pytest.fixture
def create():
    """The channel creation call, faked."""
    with patch(
        "fablabot.cogs.channel_management.safe_create_voice_channel",
        AsyncMock(return_value=(MagicMock(spec=VoiceChannel), None)),
    ) as mock:
        yield mock


async def _vocal_create(interaction: MagicMock, **kwargs) -> None:
    await cast("Any", ChannelManagement.vocal_create.callback)(
        MagicMock(), interaction, name="Salle", category=_category(), **kwargs
    )


async def test_bureau_creates_a_temporary_channel(create: AsyncMock) -> None:
    """A Bureau member creates a temporary voice channel."""
    interaction = _caller(RoleNames.BUREAU)

    await _vocal_create(interaction)

    create.assert_awaited_once()
    assert create.await_args is not None
    assert create.await_args.args[2] == "Salle-temp"


async def test_bureau_is_refused_a_permanent_channel(create: AsyncMock) -> None:
    """A Bureau member asking for a permanent channel is refused and nothing is created."""
    interaction = _caller(RoleNames.BUREAU)

    await _vocal_create(interaction, is_temporary=False)

    create.assert_not_awaited()
    interaction.response.send_message.assert_awaited_once()
    assert "Demandez ce rôle au staff" in interaction.response.send_message.await_args.args[0]


@pytest.mark.parametrize("is_temporary", [True, False])
async def test_administrator_creates_both(create: AsyncMock, is_temporary: bool) -> None:
    """An administrator creates a temporary and a permanent channel."""
    interaction = _caller(RoleNames.ADMIN)

    await _vocal_create(interaction, is_temporary=is_temporary)

    create.assert_awaited_once()


@pytest.mark.parametrize("is_temporary", [True, False])
async def test_plain_member_is_refused(create: AsyncMock, is_temporary: bool) -> None:
    """A member without a staff role is refused, temporary or not."""
    interaction = _caller(RoleNames.MEMBER_VERIFIED)

    await _vocal_create(interaction, is_temporary=is_temporary)

    create.assert_not_awaited()
    assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True


async def test_bureau_is_still_refused_rename_and_delete() -> None:
    """Bureau keeps being refused /vocal rename and /vocal delete."""
    for call in (
        lambda i: cast("Any", ChannelManagement.vocal_rename.callback)(
            MagicMock(), i, channel=MagicMock(spec=VoiceChannel), new_name="x"
        ),
        lambda i: cast("Any", ChannelManagement.vocal_delete.callback)(MagicMock(), i, channel=MagicMock(spec=VoiceChannel)),
    ):
        interaction = _caller(RoleNames.BUREAU)
        await call(interaction)
        assert "Demandez ce rôle au staff" in interaction.response.send_message.await_args.args[0]
