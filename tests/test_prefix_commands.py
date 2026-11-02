"""The bot has no prefix command: a mention followed by a word gets no reply and leaves no log, whatever the server."""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from conftest import LISTED_GUILD
from discord import Guild, Member, Message, TextChannel
from discord.ext import commands
import pytest

from fablabot.cogs import message_management

BOT_ID = 999
UNLISTED_GUILD = 222222222222222222
SECRET_WORD = "mon-mot-secret"

# One case per server: a served server must not answer either, since no prefix command exists to answer with.
ON_ANY_SERVER = pytest.mark.parametrize("guild_id", [int(LISTED_GUILD), UNLISTED_GUILD], ids=["served", "unserved"])


def _mention(guild_id: int) -> MagicMock:
    """Build a message from a member that mentions the bot, then writes a word of a private sentence."""
    msg = MagicMock(spec=Message)
    msg.content = f"<@{BOT_ID}> {SECRET_WORD} reste du message"
    msg.author = MagicMock(spec=Member)
    msg.author.bot = False
    msg.author.id = 777
    msg.guild = MagicMock(spec=Guild)
    msg.guild.id = guild_id
    msg.channel = MagicMock(spec=TextChannel)
    msg.channel.send = AsyncMock()
    return msg


@ON_ANY_SERVER
async def test_a_mention_followed_by_a_word_gets_no_reply_and_no_log(
    started_bot: Any, caplog: pytest.LogCaptureFixture, guild_id: int
) -> None:
    """Without a prefix command, the unknown word is not an error: nothing is sent, and no part of the message is logged."""
    started_bot._connection.user = MagicMock(id=BOT_ID)
    caplog.set_level(logging.DEBUG)

    with patch.object(commands.Context, "send", AsyncMock()) as send:
        await started_bot.on_message(_mention(guild_id))
        await asyncio.sleep(0.05)

    send.assert_not_awaited()
    assert SECRET_WORD not in caplog.text


async def test_a_message_still_reaches_the_cog_listeners(started_bot: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """The bot's own handler is silent, and the cogs' on_message listeners still get the message from the dispatcher."""
    monkeypatch.setattr(message_management, "EASTER_EGGS_ENABLED", True)
    monkeypatch.setattr(message_management.random, "random", lambda: 0.0)
    msg = _mention(int(LISTED_GUILD))
    msg.content = "ce n'est pas sorcier"
    msg.channel.category.name = "General"
    msg.add_reaction = AsyncMock()

    started_bot.dispatch("message", msg)
    await asyncio.sleep(0.05)

    msg.channel.send.assert_awaited()
