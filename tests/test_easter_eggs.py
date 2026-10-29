"""The easter-egg listener no longer reacts to offensive words and no longer gates on a fixed guild."""

from __future__ import annotations

from pathlib import Path
import re
from unittest.mock import AsyncMock, MagicMock

from discord import Guild, Member, Message, TextChannel
import pytest

from fablabot.cogs import message_management
from fablabot.cogs.message_management import MessageManagement

REMOVED_WORDS = [
    "autisme",
    "autiste",
    "AUTISME",
    "Autiste",
    "tu es autiste",
    "c'est de l'autisme",
    "baptiste",
    "Baptiste",
    "BAPTISTE",
    "salut baptiste !",
    "pas net",
    "Pas Net",
    "c'est pas net ce truc",
]

SRC = Path(message_management.__file__).parent.parent


def make_message(content: str, guild_id: int = 987654321) -> MagicMock:
    """Build a message stub from a human in a text channel with the given content."""
    msg = MagicMock(spec=Message)
    msg.content = content
    msg.author = MagicMock(spec=Member)
    msg.author.bot = False
    msg.guild = MagicMock(spec=Guild)
    msg.guild.id = guild_id
    msg.channel = MagicMock(spec=TextChannel)
    msg.channel.category.name = "General"
    msg.channel.send = AsyncMock()
    msg.add_reaction = AsyncMock()
    return msg


@pytest.fixture
def cog(monkeypatch: pytest.MonkeyPatch) -> MessageManagement:
    """Return a message cog with easter eggs on and the random draw always hitting."""
    monkeypatch.setattr(message_management, "EASTER_EGGS_ENABLED", True)
    monkeypatch.setattr(message_management.random, "random", lambda: 0.0)
    return MessageManagement(MagicMock())


@pytest.mark.parametrize("content", REMOVED_WORDS)
async def test_removed_words_get_no_reply(cog: MessageManagement, content: str) -> None:
    """A message holding a removed trigger word gets no reply and no reaction."""
    msg = make_message(content)

    await cog.on_message(msg)

    msg.channel.send.assert_not_called()
    msg.add_reaction.assert_not_called()


async def test_remaining_easter_egg_replies_on_any_guild(cog: MessageManagement) -> None:
    """A remaining easter egg still replies, whatever the guild."""
    msg = make_message("ce n'est pas sorcier", guild_id=987654321)

    await cog.on_message(msg)

    msg.channel.send.assert_awaited_once()


@pytest.mark.parametrize("name", ["models/message.py", "cogs/message_management.py"])
def test_no_guild_id_literal_remains(name: str) -> None:
    """The message sources hold no guild id literal and no main guild constant."""
    text = (SRC / name).read_text(encoding="utf-8")

    assert "MAIN_GUILD_ID" not in text
    # Only bare integers count: emoji tags (<:Name:123...>) and GIF URLs embed long IDs that are not guilds.
    assert not re.search(r"(?<![\w:\-])\d{17,20}(?![\w\-])", text)
