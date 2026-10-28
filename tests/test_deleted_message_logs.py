"""A deleted bot message is reported without its body in the logs."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

from discord import Member, TextChannel

from fablabot.cogs.channel_management import ChannelManagement

if TYPE_CHECKING:
    import pytest

SECRET_BODY = "SECRET-DELETED-BODY"


async def test_deleted_bot_message_log_has_no_body(caplog: pytest.LogCaptureFixture) -> None:
    """The warning names channel and deleter, the channel notice keeps the body."""
    deleter = MagicMock(spec=Member)
    deleter.name = "moderator_name"
    deleter.mention = "<@42>"
    entry = MagicMock()
    entry.user = deleter

    async def audit_logs(**_kwargs: object):
        yield entry

    channel = MagicMock(spec=TextChannel)
    channel.name = "commandes_bot"
    channel.id = 555
    channel.send = AsyncMock()

    msg = MagicMock()
    msg.id = 99
    msg.author.bot = True
    msg.content = SECRET_BODY
    msg.channel = channel
    msg.guild.id = 1234
    msg.guild.audit_logs = audit_logs

    cog = ChannelManagement(MagicMock())
    with patch.object(ChannelManagement, "_get_codir_mention", AsyncMock(return_value="")):
        await cog.on_message_delete(msg)

    assert SECRET_BODY not in caplog.text
    assert "'commandes_bot'" in caplog.text
    assert "'moderator_name'" in caplog.text
    channel.send.assert_awaited_once()
    assert SECRET_BODY in channel.send.call_args.args[0]
