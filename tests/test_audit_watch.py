"""A watched account's every change reaches its server's log channel at once; nobody else's does."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from discord import AuditLogAction, TextChannel
import pytest

from fablabot.cogs import audit_watch
from fablabot.cogs.audit_watch import AuditWatch, parse_watched_ids

WATCHED, OTHER, GUILD_ID, LOG_ID = 824534561878048798, 42, 1, 3


def _entry(user_id: int, guild: Any) -> SimpleNamespace:
    return SimpleNamespace(
        user_id=user_id,
        guild=guild,
        action=AuditLogAction.channel_update,
        target=MagicMock(mention="<#9>"),
        changes=SimpleNamespace(before={"name": "a"}, after={"name": "b"}),
    )


@pytest.fixture
def world() -> Any:
    """A served server with a log channel, and one watched account."""
    log = MagicMock(spec=TextChannel)
    log.send = AsyncMock()
    guild = MagicMock(id=GUILD_ID)
    guild.get_channel = MagicMock(side_effect=lambda cid: log if cid == LOG_ID else None)
    bot = MagicMock()
    bot.guild_access.is_served = MagicMock(return_value=True)
    with (
        patch.object(audit_watch, "watched_ids", return_value=frozenset({WATCHED})),
        patch.object(audit_watch, "get_log_channel_id", side_effect=lambda gid: LOG_ID if gid == GUILD_ID else None),
    ):
        yield SimpleNamespace(cog=AuditWatch(bot), guild=guild, log=log, bot=bot)


async def test_a_watched_change_is_posted_with_before_and_after(world: SimpleNamespace) -> None:
    """The log channel gets who, what and the change, pinging nobody."""
    await world.cog.on_audit_log_entry_create(_entry(WATCHED, world.guild))

    text = world.log.send.await_args.args[0]
    assert text.startswith(f"Compte surveillé <@{WATCHED}> : nouvelle action.")
    assert "- salon modifié : <#9>" in text
    assert "  nom : `a` → `b`" in text
    assert world.log.send.await_args.kwargs["allowed_mentions"].to_dict()["parse"] == []


async def test_an_unwatched_account_or_an_unserved_server_posts_nothing(world: SimpleNamespace) -> None:
    """Only the listed accounts, only on served servers."""
    await world.cog.on_audit_log_entry_create(_entry(OTHER, world.guild))
    world.bot.guild_access.is_served.return_value = False
    await world.cog.on_audit_log_entry_create(_entry(WATCHED, world.guild))

    world.log.send.assert_not_awaited()


async def test_a_failed_post_is_logged_not_raised(world: SimpleNamespace, caplog: pytest.LogCaptureFixture) -> None:
    """A refused post ends in the log file, with ids only."""
    world.log.send.side_effect = RuntimeError("boom")

    await world.cog.on_audit_log_entry_create(_entry(WATCHED, world.guild))

    assert f"Watched account alert not posted guild_id={GUILD_ID} user_id={WATCHED}" in caplog.text


def test_the_list_parses_ids_and_drops_the_rest(caplog: pytest.LogCaptureFixture) -> None:
    """Commas and spaces are fine; a name or a negative number is dropped without being echoed."""
    assert parse_watched_ids(" 1, 22 ,,secret-token,-3") == frozenset({1, 22})
    assert "is not an account ID" in caplog.text
    assert "secret-token" not in caplog.text
    assert parse_watched_ids(None) == frozenset()


async def test_another_server_never_posts_in_this_one(world: SimpleNamespace, caplog: pytest.LogCaptureFixture) -> None:
    """The log channel is the entry's own server's: a server with none posts nowhere and says so in the log file."""
    other = MagicMock(id=GUILD_ID + 1)
    other.get_channel = MagicMock(side_effect=lambda cid: world.log if cid == LOG_ID else None)

    await world.cog.on_audit_log_entry_create(_entry(WATCHED, other))

    world.log.send.assert_not_awaited()
    other.get_channel.assert_called_once_with(0)
    assert f"Watched account acted with no log channel guild_id={GUILD_ID + 1} user_id={WATCHED}" in caplog.text
