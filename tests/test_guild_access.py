"""Commands from servers outside the allow-list, or from DMs, are refused before they run."""

from __future__ import annotations

import importlib
import logging
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import discord
from discord import InteractionType
from discord.ext import commands
import pytest

from fablabot.cogs.guild_access import GuildAccess
from fablabot.helpers.constants import ErrorMessages
from fablabot.helpers.guild_access import (
    ALLOWED_GUILD_IDS_ENV,
    GuildAccessPolicy,
    GuildAccessTree,
    parse_allowed_guild_ids,
)

if TYPE_CHECKING:
    from types import ModuleType

LISTED = 111111111111111111
UNLISTED = 222222222222222222
TOLERATED = 333333333333333333
USER_ID = 418003123456789012
USER_NAME = "kevin_secret_name"


@pytest.fixture
def fablabot_main(tmp_path, monkeypatch) -> ModuleType:
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


def _interaction(guild_id: int | None, kind: InteractionType = InteractionType.application_command) -> MagicMock:
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


OWNER_ID = 555555555555555555
STRANGER_ID = 666666666666666666
WRONG_LIST = 999999999999999999
COG_NAMES = (
    "ChannelManagement",
    "FormationManagement",
    "LogManagement",
    "MessageManagement",
    "UserManagement",
    "WelcomeManagement",
)


def _guild(guild_id: int, owner_id: int | None = STRANGER_ID) -> MagicMock:
    """Build a stub guild with an awaitable ``leave``."""
    guild = MagicMock(spec=discord.Guild)
    guild.id = guild_id
    guild.owner_id = owner_id
    guild.leave = AsyncMock()
    return guild


def _cog(allowed: set[int], guilds: list[MagicMock] | None = None, owner_id: int = OWNER_ID) -> GuildAccess:
    """Build the real cog on a stub bot whose application owner is ``owner_id``."""
    bot = MagicMock()
    bot.guild_access = GuildAccessPolicy(frozenset(allowed))
    bot.guilds = guilds or []

    async def is_owner(user: discord.Object) -> bool:
        """Answer like ``Bot.is_owner`` for the stub owner."""
        return user.id == owner_id

    bot.is_owner = AsyncMock(side_effect=is_owner)
    return GuildAccess(bot)


async def test_join_unlisted_guild_leaves(caplog: pytest.LogCaptureFixture) -> None:
    """An unlisted guild is left without looking up its owner."""
    cog = _cog({LISTED})
    guild = _guild(UNLISTED)
    with caplog.at_level(logging.INFO):
        await cog.on_guild_join(guild)
    guild.leave.assert_awaited_once()
    assert "not_allowed" in caplog.text
    assert f"guild_id={UNLISTED}" in caplog.text
    cog.bot.is_owner.assert_not_awaited()


async def test_join_listed_guild_stays() -> None:
    """A listed guild is kept."""
    cog = _cog({LISTED})
    guild = _guild(LISTED)
    await cog.on_guild_join(guild)
    guild.leave.assert_not_awaited()
    assert cog.bot.guild_access.is_served(LISTED)


async def test_join_owner_guild_unlisted_still_leaves() -> None:
    """With a non-empty list, owning the guild does not keep it."""
    cog = _cog({LISTED})
    guild = _guild(UNLISTED, owner_id=OWNER_ID)
    await cog.on_guild_join(guild)
    guild.leave.assert_awaited_once()


async def test_join_empty_list_owner_guild_stays_and_is_served() -> None:
    """With an empty list, a guild owned by the application owner is kept and served."""
    cog = _cog(set())
    guild = _guild(UNLISTED, owner_id=OWNER_ID)
    await cog.on_guild_join(guild)
    guild.leave.assert_not_awaited()
    assert cog.bot.guild_access.is_served(UNLISTED)


async def test_join_empty_list_unproven_leaves(caplog: pytest.LogCaptureFixture) -> None:
    """With an empty list, a guild of another owner is left and not served."""
    cog = _cog(set())
    guild = _guild(UNLISTED)
    with caplog.at_level(logging.INFO):
        await cog.on_guild_join(guild)
    guild.leave.assert_awaited_once()
    assert "owner_not_proven" in caplog.text
    assert not cog.bot.guild_access.is_served(UNLISTED)


async def test_join_empty_list_owner_check_fails_leaves() -> None:
    """With an empty list, a failed owner lookup counts as not proven."""
    cog = _cog(set())
    cog.bot.is_owner.side_effect = discord.HTTPException(MagicMock(status=500), "x")
    guild = _guild(UNLISTED, owner_id=OWNER_ID)
    await cog.on_guild_join(guild)
    guild.leave.assert_awaited_once()
    assert not cog.bot.guild_access.is_served(UNLISTED)


async def test_ready_leaves_unlisted_only(caplog: pytest.LogCaptureFixture) -> None:
    """At start, only the unlisted guild is left, and the summary counts it."""
    listed, unlisted = _guild(LISTED), _guild(UNLISTED)
    cog = _cog({LISTED}, [listed, unlisted])
    with caplog.at_level(logging.INFO):
        await cog.on_ready()
    listed.leave.assert_not_awaited()
    unlisted.leave.assert_awaited_once()
    assert "1 IDs listed, in 2 guilds, leaving 1" in caplog.text


async def test_ready_guard_leaves_none_when_list_matches_nothing(caplog: pytest.LogCaptureFixture) -> None:
    """A list that matches no current guild leaves none and logs an error."""
    first, second = _guild(LISTED), _guild(UNLISTED)
    cog = _cog({WRONG_LIST}, [first, second])
    with caplog.at_level(logging.INFO):
        await cog.on_ready()
    first.leave.assert_not_awaited()
    second.leave.assert_not_awaited()
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert any(ALLOWED_GUILD_IDS_ENV in r.getMessage() for r in errors)
    assert not cog.bot.guild_access.is_served(LISTED)


async def test_ready_empty_list_stays_serves_and_warns(caplog: pytest.LogCaptureFixture) -> None:
    """With an empty list, the bot stays, serves every guild and warns about unproven owners."""
    owned, other = _guild(LISTED, owner_id=OWNER_ID), _guild(UNLISTED)
    cog = _cog(set(), [owned, other])
    with caplog.at_level(logging.INFO):
        await cog.on_ready()
    owned.leave.assert_not_awaited()
    other.leave.assert_not_awaited()
    assert cog.bot.guild_access.is_served(LISTED)
    assert cog.bot.guild_access.is_served(UNLISTED)
    assert f"{ALLOWED_GUILD_IDS_ENV} is empty" in caplog.text
    staying = [r.getMessage() for r in caplog.records if "without proof of ownership" in r.getMessage()]
    assert len(staying) == 1
    assert f"guild_id={UNLISTED}" in staying[0]


async def test_ready_empty_list_owner_check_fails_stays() -> None:
    """With an empty list, a failed owner lookup keeps the guild and serves it."""
    guild = _guild(LISTED, owner_id=OWNER_ID)
    cog = _cog(set(), [guild])
    cog.bot.is_owner.side_effect = discord.HTTPException(MagicMock(status=500), "x")
    await cog.on_ready()
    guild.leave.assert_not_awaited()
    assert cog.bot.guild_access.is_served(LISTED)


async def test_leave_failure_logged_and_continues(caplog: pytest.LogCaptureFixture) -> None:
    """A failed leave is logged with the guild ID, the next guild is still left."""
    failing, next_one, listed = _guild(UNLISTED), _guild(TOLERATED), _guild(LISTED)
    failing.leave.side_effect = discord.HTTPException(MagicMock(status=500), "x")
    cog = _cog({LISTED}, [failing, next_one, listed])
    with caplog.at_level(logging.INFO):
        await cog.on_ready()
    next_one.leave.assert_awaited_once()
    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert any(f"guild_id={UNLISTED}" in message for message in errors)
    assert not cog.bot.guild_access.is_served(UNLISTED)


async def test_ready_again_reconciles_new_guild() -> None:
    """A second ready leaves a guild added since the first."""
    cog = _cog({LISTED}, [_guild(LISTED)])
    await cog.on_ready()
    late = _guild(UNLISTED)
    cog.bot.guilds.append(late)
    await cog.on_ready()
    late.leave.assert_awaited_once()


async def test_ready_again_empty_list_leaves_unproven_new_guild() -> None:
    """With an empty list, a later ready leaves a new guild whose owner is not proven."""
    owned = _guild(LISTED, owner_id=OWNER_ID)
    cog = _cog(set(), [owned])
    await cog.on_ready()
    late = _guild(UNLISTED)
    cog.bot.guilds.append(late)
    await cog.on_ready()
    late.leave.assert_awaited_once()
    owned.leave.assert_not_awaited()
    assert cog.bot.guild_access.is_served(LISTED)
    assert not cog.bot.guild_access.is_served(UNLISTED)


async def test_ready_again_empty_list_keeps_new_owned_guild() -> None:
    """With an empty list, a later ready serves a new guild owned by the application owner."""
    cog = _cog(set(), [_guild(LISTED)])
    await cog.on_ready()
    late = _guild(UNLISTED, owner_id=OWNER_ID)
    cog.bot.guilds.append(late)
    await cog.on_ready()
    late.leave.assert_not_awaited()
    assert cog.bot.guild_access.is_served(UNLISTED)


async def test_ready_again_empty_list_retries_failed_leave() -> None:
    """A guild refused on join but not left is not served by a later ready, and the leave is retried."""
    cog = _cog(set())
    await cog.on_ready()
    stuck = _guild(UNLISTED)
    stuck.leave.side_effect = discord.HTTPException(MagicMock(status=500), "x")
    await cog.on_guild_join(stuck)
    cog.bot.guilds.append(stuck)
    await cog.on_ready()
    assert stuck.leave.await_count == 2
    assert not cog.bot.guild_access.is_served(UNLISTED)


async def test_join_empty_list_forgets_tolerated_guild_when_leave_fails() -> None:
    """A guild tolerated at start and re-added by a stranger is not served when the leave fails."""
    guild = _guild(UNLISTED)
    cog = _cog(set(), [guild])
    await cog.on_ready()
    assert cog.bot.guild_access.is_served(UNLISTED)
    guild.leave.side_effect = discord.HTTPException(MagicMock(status=500), "x")
    await cog.on_guild_join(guild)
    assert not cog.bot.guild_access.is_served(UNLISTED)


async def test_setup_hook_loads_guild_access(fablabot_main: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """The real setup hook loads the access cog with both listeners."""
    monkeypatch.setenv(ALLOWED_GUILD_IDS_ENV, str(LISTED))
    monkeypatch.setattr(fablabot_main, "configure_logging", lambda _bot: (None, None))
    for name in COG_NAMES:
        monkeypatch.setattr(fablabot_main, name, type(name, (commands.Cog,), {}))
    bot = fablabot_main.Fablabot()
    bot.tree.sync = AsyncMock()
    await bot.setup_hook()
    cog = bot.get_cog("GuildAccess")
    assert isinstance(cog, GuildAccess)
    assert {name for name, _ in cog.get_listeners()} >= {"on_guild_join", "on_ready"}
