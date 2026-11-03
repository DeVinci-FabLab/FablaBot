"""/user invite: a fresh invite with the caller's settings, never one the caller could not make in Discord."""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

from conftest import make_interaction, make_member, make_role
from discord import Forbidden, HTTPException, Permissions, TextChannel
import pytest

from fablabot.cogs import user_management
from fablabot.cogs.user_management import UserManagement
from fablabot.helpers.constants import RoleNames


@pytest.fixture(autouse=True)
def _context_passes():
    with patch.object(user_management, "ensure_command_context", AsyncMock(return_value=True)):
        yield


def _channel(*, caller_may_invite: bool = True) -> MagicMock:
    channel = MagicMock(spec=TextChannel)
    channel.id = 42
    channel.mention = "<#42>"
    channel.permissions_for = MagicMock(return_value=Permissions(create_instant_invite=caller_may_invite))
    channel.create_invite = AsyncMock(return_value=MagicMock(url="https://discord.gg/abc"))
    return channel


async def _invite(channel: MagicMock, **options: object) -> MagicMock:
    interaction = make_interaction()
    interaction.user = make_member(1, make_role(RoleNames.PRESIDENT))
    callback = cast("Any", UserManagement.user_invite).callback
    await callback(UserManagement(MagicMock()), interaction, channel=channel, **options)
    return interaction


async def test_invite_uses_the_given_settings_and_answers_in_private() -> None:
    """The invite is new, carries the options picked, and only the caller sees the link."""
    channel = _channel()

    interaction = await _invite(channel, duration=3600, uses=1, temporary=True)

    channel.create_invite.assert_awaited_once()
    sent = channel.create_invite.await_args.kwargs
    assert (sent["max_age"], sent["max_uses"], sent["temporary"], sent["unique"]) == (3600, 1, True, True)
    interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
    text = interaction.followup.send.await_args.args[0]
    assert "https://discord.gg/abc" in text
    assert "1 heure" in text
    assert interaction.followup.send.await_args.kwargs["ephemeral"] is True


async def test_defaults_are_seven_days_and_no_use_limit() -> None:
    """Left alone, the options match Discord's own invite defaults."""
    defaults = {p.name: p.default for p in UserManagement.user_invite.parameters if p.name != "channel"}
    assert defaults == {"duration": 7 * 86400, "uses": 0, "temporary": False}


async def test_caller_who_cannot_invite_there_is_refused() -> None:
    """The bot opens no door its caller could not open in Discord."""
    channel = _channel(caller_may_invite=False)

    interaction = await _invite(channel)

    channel.create_invite.assert_not_awaited()
    interaction.response.send_message.assert_awaited_once_with(user_management._INVITE_REFUSED, ephemeral=True)


async def test_bot_without_the_permission_says_so() -> None:
    """A 403 tells the caller which permission the bot lacks."""
    channel = _channel()
    channel.create_invite.side_effect = Forbidden(MagicMock(status=403), "Missing Permissions")

    interaction = await _invite(channel)

    interaction.followup.send.assert_awaited_once_with(user_management._INVITE_FORBIDDEN, ephemeral=True)


async def test_discord_failure_is_told_in_private() -> None:
    """A 5xx after discord.py's own retries still reaches the caller, privately."""
    channel = _channel()
    channel.create_invite.side_effect = HTTPException(MagicMock(status=503), "unavailable")

    interaction = await _invite(channel)

    assert "invitation" in interaction.followup.send.await_args.args[0]
    assert interaction.followup.send.await_args.kwargs["ephemeral"] is True
