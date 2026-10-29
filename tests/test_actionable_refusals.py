"""Refusals tell members what to do next and never post role problems in the channel."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

from conftest import make_interaction, make_role
from discord import TextChannel

from fablabot.helpers.help_messages import build_help_message
from fablabot.helpers.utils import ensure_command_context

if TYPE_CHECKING:
    import pytest

LOGGER = logging.getLogger("test")
NEXT_STEP_ROLE = "Bureau"
NEXT_STEP_CHANNEL = "staff"


def _setup(*, member_has_role: bool, in_commands_channel: bool, role_on_server: bool = True) -> MagicMock:
    """Build an interaction in a guild with a commands channel.

    Args:
        member_has_role (bool): Whether the member holds the required role.
        in_commands_channel (bool): Whether the command is used in the commands channel.
        role_on_server (bool): Whether the required role exists on the server.

    Returns:
        MagicMock: The stub interaction.
    """
    interaction = make_interaction()
    role = make_role("Admin", role_id=7)
    commands = MagicMock(spec=TextChannel)
    commands.id = 55
    commands.name = "commandes_bot"
    commands.mention = "<#55>"
    other = MagicMock(spec=TextChannel)
    other.id = 66
    other.name = "general"
    other.send = AsyncMock()
    interaction.channel = commands if in_commands_channel else other
    interaction.guild.channels = [commands]
    interaction.guild.get_channel = MagicMock(return_value=commands)
    interaction.guild.roles = [role] if role_on_server else []
    interaction.user.roles = [role] if member_has_role else []
    return interaction


async def _run(interaction: MagicMock) -> bool:
    return await ensure_command_context(LOGGER, "x", interaction, required_roles={"Admin"})


async def test_missing_server_role_is_not_posted_and_logs_one_warning(caplog: pytest.LogCaptureFixture) -> None:
    """A role absent from the server is logged with IDs only, and the member gets the private refusal."""
    caplog.set_level(logging.WARNING)
    interaction = _setup(member_has_role=False, in_commands_channel=True, role_on_server=False)
    assert await _run(interaction) is False
    interaction.channel.send.assert_not_called() if hasattr(interaction.channel.send, "assert_not_called") else None
    warnings = [r for r in caplog.records if "Missing roles" in r.getMessage()]
    assert len(warnings) == 1
    assert "Admin" in warnings[0].getMessage()
    assert "1234" in warnings[0].getMessage()
    assert "kevin_secret_name" not in caplog.text
    assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True


async def test_member_without_role_in_wrong_channel_gets_role_refusal() -> None:
    """The role check comes first."""
    interaction = _setup(member_has_role=False, in_commands_channel=False)
    assert await _run(interaction) is False
    interaction.response.send_message.assert_awaited_once()
    args, kwargs = interaction.response.send_message.await_args
    assert "Admin" in args[0]
    assert NEXT_STEP_ROLE in args[0]
    assert kwargs["ephemeral"] is True


async def test_member_with_role_in_wrong_channel_gets_channel_refusal() -> None:
    """A member with the right is sent to the commands channel."""
    interaction = _setup(member_has_role=True, in_commands_channel=False)
    assert await _run(interaction) is False
    args, kwargs = interaction.response.send_message.await_args
    assert "<#55>" in args[0]
    assert NEXT_STEP_CHANNEL in args[0]
    assert kwargs["ephemeral"] is True


async def test_member_with_role_in_commands_channel_passes() -> None:
    """Who may run what is unchanged."""
    interaction = _setup(member_has_role=True, in_commands_channel=True)
    assert await _run(interaction) is True


def test_help_footer_mentions_suggest() -> None:
    """Every help message points members to /suggest."""
    assert "/suggest" in build_help_message("Aide", "/x", [("a", "b")])
