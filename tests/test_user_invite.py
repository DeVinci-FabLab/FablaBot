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


def _channel(*, caller_sees: bool = True) -> MagicMock:
    channel = MagicMock(spec=TextChannel)
    channel.id = 42
    channel.mention = "<#42>"
    channel.permissions_for = MagicMock(return_value=Permissions(view_channel=caller_sees))
    channel.create_invite = AsyncMock(return_value=MagicMock(url="https://discord.gg/abc"))
    return channel


async def _invite(channel: MagicMock, log: MagicMock | None = None, **options: object) -> MagicMock:
    interaction = make_interaction()
    interaction.guild.get_channel = MagicMock(return_value=log)
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


async def test_caller_who_cannot_see_the_channel_is_refused() -> None:
    """An invite never lands a newcomer in a channel its caller cannot see."""
    channel = _channel(caller_sees=False)

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


async def test_discord_create_invite_is_not_needed_and_the_invite_is_logged() -> None:
    """The server can keep Create Invite off for every role: the bot creates it and posts who made it, not the link."""
    channel = _channel()
    log = MagicMock(spec=TextChannel)
    log.send = AsyncMock()

    with patch.object(user_management, "get_log_channel_id", return_value=7):
        await _invite(channel, log)

    channel.create_invite.assert_awaited_once()
    posted = log.send.await_args.args[0]
    assert posted.startswith("Invitation créée par <@1> vers <#42> (7 jours")
    assert "discord.gg" not in posted
    assert log.send.await_args.kwargs["allowed_mentions"].to_dict()["parse"] == []
