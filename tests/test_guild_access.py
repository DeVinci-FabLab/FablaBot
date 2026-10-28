"""Commands from servers outside the allow-list, or from DMs, are refused before they run."""

from __future__ import annotations

import importlib
import logging
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock

import discord
from discord import InteractionType
import pytest

from fablabot.helpers.constants import ErrorMessages
from fablabot.helpers.guild_access import (
    ALLOWED_GUILD_IDS_ENV,
    GuildAccessPolicy,
    GuildAccessTree,
    parse_allowed_guild_ids,
)

LISTED = 111111111111111111
UNLISTED = 222222222222222222
TOLERATED = 333333333333333333
USER_ID = 418003123456789012
USER_NAME = "kevin_secret_name"


@pytest.fixture
def fablabot_main(tmp_path, monkeypatch) -> ModuleType:  # noqa: ANN001
    """Import ``fablabot.main`` with a fake token file in place."""
    token = tmp_path / "token"
    token.write_text("fake-token")
    monkeypatch.setenv("DISCORD_TOKEN_FILE", str(token))
    import fablabot.main

    return importlib.reload(fablabot.main)


def _make_tree(policy: GuildAccessPolicy) -> GuildAccessTree:
    """Build a real guarded tree on a stub client carrying ``policy``."""
    client = MagicMock()
    client._connection._command_tree = None
    client.guild_access = policy
    return GuildAccessTree(client)


def _interaction(
    guild_id: int | None, kind: InteractionType = InteractionType.application_command
) -> MagicMock:
    """Build a stub interaction from ``guild_id`` with an awaitable reply."""
    interaction = MagicMock()
    interaction.guild_id = guild_id
    interaction.type = kind
    interaction.user.id = USER_ID
    interaction.user.name = USER_NAME
    interaction.user.__str__.return_value = USER_NAME
    interaction.response.send_message = AsyncMock()
    return interaction


def test_parse_reads_ids_commas_spaces_and_blanks() -> None:
    """Blanks and empty entries are dropped around valid IDs."""
    assert parse_allowed_guild_ids(" 111111111111111111 , 222222222222222222,,") == {LISTED, UNLISTED}


@pytest.mark.parametrize("raw", [None, "", "  "])
def test_parse_absent_or_blank_is_empty(raw: str | None) -> None:
    """An absent or blank value gives an empty set."""
    assert parse_allowed_guild_ids(raw) == frozenset()


def test_parse_rejects_non_numeric_without_echoing_value() -> None:
    """The error names the variable and the entry position, never the value."""
    with pytest.raises(ValueError, match=ALLOWED_GUILD_IDS_ENV) as exc:
        parse_allowed_guild_ids("111111111111111111,abc-secret")
    assert "entry 2" in str(exc.value)
    assert "abc-secret" not in str(exc.value)


@pytest.mark.parametrize("raw", ["-5", "١٢٣", "0"])
def test_parse_rejects_signs_and_non_ascii_digits(raw: str) -> None:
    """Signs, non-ASCII digits and zero are not guild IDs."""
    with pytest.raises(ValueError, match=ALLOWED_GUILD_IDS_ENV):
        parse_allowed_guild_ids(raw)


async def test_tree_serves_listed_guild(caplog: pytest.LogCaptureFixture) -> None:
    """A listed guild passes, without reply or warning."""
    interaction = _interaction(LISTED)
    with caplog.at_level(logging.WARNING):
        assert await _make_tree(GuildAccessPolicy(frozenset({LISTED}))).interaction_check(interaction) is True
    interaction.response.send_message.assert_not_awaited()
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


async def test_tree_refuses_unlisted_guild(caplog: pytest.LogCaptureFixture) -> None:
    """An unlisted guild gets an ephemeral refusal and a log line without the user name."""
    interaction = _interaction(UNLISTED)
    with caplog.at_level(logging.WARNING):
        assert await _make_tree(GuildAccessPolicy(frozenset({LISTED}))).interaction_check(interaction) is False
    interaction.response.send_message.assert_awaited_once_with(ErrorMessages.GUILD_NOT_ALLOWED, ephemeral=True)
    assert "not_allowed" in caplog.text
    assert f"guild_id={UNLISTED}" in caplog.text
    assert str(USER_ID) in caplog.text
    assert USER_NAME not in caplog.text


async def test_tree_refuses_dm(caplog: pytest.LogCaptureFixture) -> None:
    """A DM has no guild and is refused as ``no_guild``."""
    interaction = _interaction(None)
    with caplog.at_level(logging.WARNING):
        assert await _make_tree(GuildAccessPolicy(frozenset({LISTED}))).interaction_check(interaction) is False
    assert "no_guild" in caplog.text


async def test_tree_refuses_autocomplete_silently() -> None:
    """An autocomplete from an unlisted guild is refused without a reply."""
    interaction = _interaction(UNLISTED, InteractionType.autocomplete)
    assert await _make_tree(GuildAccessPolicy(frozenset({LISTED}))).interaction_check(interaction) is False
    interaction.response.send_message.assert_not_awaited()


async def test_tree_refuses_context_menu() -> None:
    """A context-menu interaction from an unlisted guild is refused."""
    interaction = _interaction(UNLISTED)
    interaction.data = {"type": 2, "name": "Some menu"}
    assert await _make_tree(GuildAccessPolicy(frozenset({LISTED}))).interaction_check(interaction) is False
    interaction.response.send_message.assert_awaited_once_with(ErrorMessages.GUILD_NOT_ALLOWED, ephemeral=True)


async def test_tree_empty_list_serves_tolerated_only() -> None:
    """With an empty list, only tolerated guilds pass."""
    tree = _make_tree(GuildAccessPolicy(frozenset(), tolerated={TOLERATED}))
    assert await tree.interaction_check(_interaction(TOLERATED)) is True
    assert await tree.interaction_check(_interaction(UNLISTED)) is False


async def test_tree_refusal_survives_expired_interaction() -> None:
    """A reply that fails on an expired interaction still refuses the command."""
    interaction = _interaction(UNLISTED)
    interaction.response.send_message.side_effect = discord.NotFound(MagicMock(status=404), "unknown")
    assert await _make_tree(GuildAccessPolicy(frozenset({LISTED}))).interaction_check(interaction) is False


def test_bot_uses_guarded_tree_and_reads_env(fablabot_main: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """The bot builds a guarded tree and reads the list from the environment."""
    monkeypatch.setenv(ALLOWED_GUILD_IDS_ENV, str(LISTED))
    bot = fablabot_main.Fablabot()
    assert isinstance(bot.tree, GuildAccessTree)
    assert bot.guild_access.allowed == {LISTED}


def test_bot_refuses_to_start_on_malformed_list(fablabot_main: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """A malformed list stops the bot before it starts."""
    monkeypatch.setenv(ALLOWED_GUILD_IDS_ENV, "abc")
    with pytest.raises(ValueError, match=ALLOWED_GUILD_IDS_ENV):
        fablabot_main.Fablabot()
