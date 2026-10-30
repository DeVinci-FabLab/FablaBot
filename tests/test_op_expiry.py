"""Each op is recorded per server and member, and ends on time while the bot runs."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from conftest import make_guild, make_interaction, make_member, make_role
from discord import CategoryChannel, Forbidden, ForumChannel, HTTPException, NotFound
import pytest

from fablabot.cogs import user_management
from fablabot.cogs.user_management import UserManagement
from fablabot.helpers.constants import RoleNames
from fablabot.models.user import OpExpiry

NOW = datetime(2026, 10, 28, 15, 0, tzinfo=UTC)
GUILD_A, GUILD_B = 1001, 1002
TARGET_ID = 501
OP_CHANNEL_ID = 777
STATE_FILE = Path("data/temp_admin_state.json")


@pytest.fixture(autouse=True)
def _fixed_clock_and_context():
    """Freeze the clock and let every caller through the shared context check."""
    with (
        patch.object(user_management, "_utcnow", return_value=NOW),
        patch.object(user_management, "ensure_command_context", AsyncMock(return_value=True)),
    ):
        yield


@pytest.fixture
async def cog():
    """User cog over a stub bot; its timers are cancelled at teardown."""
    bot = MagicMock()
    instance = UserManagement(bot)
    yield instance
    for task in instance.deop_tasks.values():
        task.cancel()


def _server(guild_id: int) -> tuple[MagicMock, MagicMock, MagicMock]:
    """A guild with the temp and CoDir roles, a target member and a Président caller."""
    temp = make_role(RoleNames.ADMIN_TEMP, role_id=guild_id + 1)
    codir = make_role(RoleNames.CODIR, role_id=guild_id + 2)
    president = make_role(RoleNames.PRESIDENT, role_id=guild_id + 3)
    target = make_member(TARGET_ID)
    caller = make_member(9000 + guild_id, president)
    guild = make_guild(guild_id, [temp, codir, president], [target, caller])
    return guild, target, caller


def _interaction(guild: MagicMock, caller: MagicMock) -> MagicMock:
    interaction = make_interaction(guild_id=guild.id)
    interaction.guild = guild
    interaction.user = caller
    interaction.channel_id = OP_CHANNEL_ID
    return interaction


async def _op(cog: UserManagement, guild: MagicMock, caller: MagicMock, target: MagicMock, minutes: int) -> MagicMock:
    interaction = _interaction(guild, caller)
    await UserManagement.user_op.callback(cog, interaction, member=target, reason="atelier", time=minutes)
    return interaction


def _saved() -> dict:
    return json.loads(STATE_FILE.read_text(encoding="utf-8"))


async def test_op_records_expiry_before_replying(cog: UserManagement) -> None:
    """An op is recorded with its expiry before the reply goes out."""
    guild, target, caller = _server(GUILD_A)
    interaction = _interaction(guild, caller)
    seen: list[dict] = []
    interaction.response.send_message = AsyncMock(side_effect=lambda *_a, **_k: seen.append(_saved()))

    await UserManagement.user_op.callback(cog, interaction, member=target, reason="atelier", time=30)

    record = seen[0][str(GUILD_A)][str(TARGET_ID)]
    assert record == {"expires_at": "2026-10-28T15:30:00+00:00", "channel_id": OP_CHANNEL_ID}
    assert set(cog.deop_tasks) == {(GUILD_A, TARGET_ID)}


async def test_reop_replaces_expiry_and_keeps_one_timer(cog: UserManagement) -> None:
    """Opping again replaces the expiry and leaves a single timer."""
    guild, target, caller = _server(GUILD_A)
    await _op(cog, guild, caller, target, 30)
    first = cog.deop_tasks[(GUILD_A, TARGET_ID)]

    await _op(cog, guild, caller, target, 10)
    await asyncio.sleep(0)

    assert _saved()[str(GUILD_A)][str(TARGET_ID)]["expires_at"] == "2026-10-28T15:10:00+00:00"
    assert first.cancelled()
    assert len(cog.deop_tasks) == 1


async def test_expiry_removes_role_clears_record_and_posts_in_op_channel(cog: UserManagement) -> None:
    """Expiry removes the role, clears the record and posts in the op channel."""
    guild, target, caller = _server(GUILD_A)
    interaction = await _op(cog, guild, caller, target, 90)
    temp = guild.roles[0]
    target.roles.append(temp)
    cog.bot.get_guild = MagicMock(return_value=guild)

    with patch.object(user_management, "_utcnow", return_value=NOW + timedelta(minutes=90)):
        await cog._expire(GUILD_A, TARGET_ID)

    target.remove_roles.assert_awaited_once()
    assert str(TARGET_ID) not in _saved().get(str(GUILD_A), {})
    guild.get_channel.assert_called_with(OP_CHANNEL_ID)
    notice = guild.get_channel.return_value.send.await_args.args[0]
    assert "<@501>" in notice
    interaction.followup.send.assert_not_awaited()


async def test_expiry_twice_posts_one_notice(cog: UserManagement) -> None:
    """An expiry that runs twice posts one notice."""
    guild, target, caller = _server(GUILD_A)
    await _op(cog, guild, caller, target, 5)
    target.roles.append(guild.roles[0])
    cog.bot.get_guild = MagicMock(return_value=guild)

    with patch.object(user_management, "_utcnow", return_value=NOW + timedelta(minutes=5)):
        await cog._expire(GUILD_A, TARGET_ID)
        await cog._expire(GUILD_A, TARGET_ID)

    guild.get_channel.return_value.send.assert_awaited_once()


async def test_expiry_for_member_who_left_clears_record_quietly(cog: UserManagement, caplog: pytest.LogCaptureFixture) -> None:
    """A member who left has the record cleared without a notice."""
    guild, target, caller = _server(GUILD_A)
    await _op(cog, guild, caller, target, 5)
    guild.members.remove(target)
    cog.bot.get_guild = MagicMock(return_value=guild)

    with patch.object(user_management, "_utcnow", return_value=NOW + timedelta(minutes=5)):
        await cog._expire(GUILD_A, TARGET_ID)

    assert str(TARGET_ID) not in _saved().get(str(GUILD_A), {})
    assert not [r for r in caplog.records if r.levelname == "ERROR"]


async def test_failed_expiry_keeps_record_and_warns_op_channel(cog: UserManagement, caplog: pytest.LogCaptureFixture) -> None:
    """A failed expiry keeps the record and warns the op channel."""
    guild, target, caller = _server(GUILD_A)
    await _op(cog, guild, caller, target, 5)
    target.roles.append(guild.roles[0])
    target.remove_roles = AsyncMock(side_effect=Forbidden(MagicMock(status=403), "missing permissions"))
    cog.bot.get_guild = MagicMock(return_value=guild)

    with patch.object(user_management, "_utcnow", return_value=NOW + timedelta(minutes=5)):
        await cog._expire(GUILD_A, TARGET_ID)

    assert str(TARGET_ID) in _saved()[str(GUILD_A)]
    guild.get_channel.return_value.send.assert_awaited_once()
    errors = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
    assert any(f"guild_id={GUILD_A}" in m and f"member_id={TARGET_ID}" in m for m in errors)


async def test_deop_cancels_timer_and_clears_record(cog: UserManagement) -> None:
    """Deopping cancels the timer and clears the record."""
    guild, target, caller = _server(GUILD_A)
    await _op(cog, guild, caller, target, 30)
    task = cog.deop_tasks[(GUILD_A, TARGET_ID)]

    await UserManagement.user_deop.callback(cog, _interaction(guild, caller), member=target)
    await asyncio.sleep(0)

    assert task.cancelled()
    assert (GUILD_A, TARGET_ID) not in cog.deop_tasks
    assert str(TARGET_ID) not in _saved().get(str(GUILD_A), {})


async def test_servers_are_kept_apart(cog: UserManagement) -> None:
    """Ops on one server never touch another server's records."""
    guild_a, target_a, caller_a = _server(GUILD_A)
    guild_b, target_b, caller_b = _server(GUILD_B)
    await _op(cog, guild_a, caller_a, target_a, 30)
    await _op(cog, guild_b, caller_b, target_b, 60)
    task_b = cog.deop_tasks[(GUILD_B, TARGET_ID)]

    await UserManagement.user_deop.callback(cog, _interaction(guild_a, caller_a), member=target_a)
    await asyncio.sleep(0)

    assert not task_b.done()
    assert _saved()[str(GUILD_B)][str(TARGET_ID)]["expires_at"] == "2026-10-28T16:00:00+00:00"
    assert str(TARGET_ID) not in _saved().get(str(GUILD_A), {})


def test_op_expiry_round_trips_and_rejects_naive_time() -> None:
    """An expiry survives a round trip and a time without timezone is rejected."""
    expiry = OpExpiry(expires_at=NOW, channel_id=OP_CHANNEL_ID)
    assert OpExpiry.from_dict(expiry.to_dict()) == expiry
    with pytest.raises(ValueError, match="timezone"):
        OpExpiry.from_dict({"expires_at": "2026-10-28T15:00:00", "channel_id": 1})


def test_delay_counts_from_the_stored_time() -> None:
    """The delay counts from the stored time and never goes below zero."""
    assert user_management._delay_until(NOW + timedelta(minutes=7)) == 420
    assert user_management._delay_until(NOW - timedelta(minutes=7)) == 0


async def test_timer_that_wakes_before_the_clock_is_due_waits_again(cog: UserManagement) -> None:
    """A timer that wakes before the clock is due waits again."""
    guild, target, _caller = _server(GUILD_A)
    target.roles.append(guild.roles[0])
    cog.bot.get_guild = MagicMock(return_value=guild)
    expires_at = NOW + timedelta(milliseconds=5)
    cog._record_op(GUILD_A, TARGET_ID, OpExpiry(expires_at=expires_at, channel_id=OP_CHANNEL_ID))
    clock = {"now": NOW}
    real_sleep = asyncio.sleep
    sleeps: list[float] = []

    async def early_wake(delay: float) -> None:
        sleeps.append(delay)
        if len(sleeps) > 1:
            clock["now"] = expires_at
        await real_sleep(0)

    with (
        patch.object(user_management, "_utcnow", side_effect=lambda: clock["now"]),
        patch.object(user_management.asyncio, "sleep", early_wake),
    ):
        cog._arm_deop(GUILD_A, TARGET_ID, expires_at)
        await cog.deop_tasks[GUILD_A, TARGET_ID]

    assert len(sleeps) == 2
    target.remove_roles.assert_awaited_once()
    assert str(TARGET_ID) not in _saved().get(str(GUILD_A), {})


async def test_expiry_keeps_record_and_asks_for_a_retry_while_the_guild_is_unavailable(
    cog: UserManagement, caplog: pytest.LogCaptureFixture
) -> None:
    """While the guild is unavailable, expiry keeps the record and asks for a retry."""
    guild, target, caller = _server(GUILD_A)
    await _op(cog, guild, caller, target, 31)
    target.roles.append(guild.roles[0])
    guild.unavailable = True
    cog.bot.get_guild = MagicMock(return_value=guild)

    with patch.object(user_management, "_utcnow", return_value=NOW + timedelta(minutes=31)):
        settled = await cog._expire(GUILD_A, TARGET_ID)

    assert settled is False
    assert str(TARGET_ID) in _saved()[str(GUILD_A)]
    target.remove_roles.assert_not_awaited()
    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert any(f"guild_id={GUILD_A}" in m and f"member_id={TARGET_ID}" in m and "record kept" in m for m in warnings)


async def test_timer_waits_and_tries_again_until_the_guild_is_back(cog: UserManagement) -> None:
    """The timer waits and tries again until the guild is back."""
    guild, target, _caller = _server(GUILD_A)
    target.roles.append(guild.roles[0])
    guild.unavailable = True
    cog.bot.get_guild = MagicMock(return_value=guild)
    cog._record_op(GUILD_A, TARGET_ID, OpExpiry(expires_at=NOW, channel_id=OP_CHANNEL_ID))
    clock = {"now": NOW}
    real_sleep = asyncio.sleep
    sleeps: list[float] = []

    async def guild_comes_back(delay: float) -> None:
        sleeps.append(delay)
        guild.unavailable = False
        clock["now"] += timedelta(seconds=delay)
        await real_sleep(0)

    with (
        patch.object(user_management, "_utcnow", side_effect=lambda: clock["now"]),
        patch.object(user_management.asyncio, "sleep", guild_comes_back),
    ):
        cog._arm_deop(GUILD_A, TARGET_ID, NOW)
        await cog.deop_tasks[GUILD_A, TARGET_ID]

    assert sleeps == [60.0]
    target.remove_roles.assert_awaited_once()
    assert str(TARGET_ID) not in _saved().get(str(GUILD_A), {})
    assert cog.deop_tasks == {}


async def test_expiry_removes_the_role_of_a_cached_member_in_a_guild_whose_cache_looks_incomplete(
    cog: UserManagement,
) -> None:
    """A cached member loses the role even when the guild cache looks incomplete."""
    guild, target, caller = _server(GUILD_A)
    await _op(cog, guild, caller, target, 31)
    target.roles.append(guild.roles[0])
    guild.chunked = False
    cog.bot.get_guild = MagicMock(return_value=guild)

    with patch.object(user_management, "_utcnow", return_value=NOW + timedelta(minutes=31)):
        settled = await cog._expire(GUILD_A, TARGET_ID)

    assert settled is True
    target.remove_roles.assert_awaited_once()
    guild.fetch_member.assert_not_awaited()
    assert str(TARGET_ID) not in _saved().get(str(GUILD_A), {})


async def test_expiry_asks_discord_for_a_member_missing_from_an_incomplete_cache(cog: UserManagement) -> None:
    """A member missing from an incomplete cache is fetched from Discord."""
    guild, target, caller = _server(GUILD_A)
    await _op(cog, guild, caller, target, 31)
    target.roles.append(guild.roles[0])
    guild.chunked = False
    guild.members.remove(target)
    guild.fetch_member = AsyncMock(return_value=target)
    cog.bot.get_guild = MagicMock(return_value=guild)

    with patch.object(user_management, "_utcnow", return_value=NOW + timedelta(minutes=31)):
        settled = await cog._expire(GUILD_A, TARGET_ID)

    assert settled is True
    guild.fetch_member.assert_awaited_once_with(TARGET_ID)
    target.remove_roles.assert_awaited_once()
    assert str(TARGET_ID) not in _saved().get(str(GUILD_A), {})


async def test_expiry_clears_quietly_a_member_discord_does_not_know(
    cog: UserManagement, caplog: pytest.LogCaptureFixture
) -> None:
    """A member Discord does not know has the record cleared without a notice."""
    guild, target, caller = _server(GUILD_A)
    await _op(cog, guild, caller, target, 31)
    guild.chunked = False
    guild.members.remove(target)
    guild.fetch_member = AsyncMock(side_effect=NotFound(MagicMock(status=404), "unknown member"))
    cog.bot.get_guild = MagicMock(return_value=guild)

    with patch.object(user_management, "_utcnow", return_value=NOW + timedelta(minutes=31)):
        settled = await cog._expire(GUILD_A, TARGET_ID)

    assert settled is True
    assert str(TARGET_ID) not in _saved().get(str(GUILD_A), {})
    assert not [r for r in caplog.records if r.levelname == "ERROR"]


async def test_expiry_keeps_record_and_asks_for_a_retry_when_discord_cannot_say_who_the_member_is(
    cog: UserManagement, caplog: pytest.LogCaptureFixture
) -> None:
    """When Discord cannot say who the member is, expiry keeps the record and asks for a retry."""
    guild, target, caller = _server(GUILD_A)
    await _op(cog, guild, caller, target, 31)
    target.roles.append(guild.roles[0])
    guild.chunked = False
    guild.members.remove(target)
    guild.fetch_member = AsyncMock(side_effect=HTTPException(MagicMock(status=503), "unavailable"))
    cog.bot.get_guild = MagicMock(return_value=guild)

    with patch.object(user_management, "_utcnow", return_value=NOW + timedelta(minutes=31)):
        settled = await cog._expire(GUILD_A, TARGET_ID)

    assert settled is False
    assert str(TARGET_ID) in _saved()[str(GUILD_A)]
    target.remove_roles.assert_not_awaited()
    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert any(f"guild_id={GUILD_A}" in m and f"member_id={TARGET_ID}" in m and "record kept" in m for m in warnings)


async def test_deop_does_not_erase_an_op_made_while_it_was_in_flight(cog: UserManagement) -> None:
    """A deop does not erase an op made while it was in flight."""
    guild, target, caller = _server(GUILD_A)
    await _op(cog, guild, caller, target, 30)
    gate = asyncio.Event()

    async def gated_remove(*_args: object, **_kwargs: object) -> None:
        await gate.wait()

    target.remove_roles = AsyncMock(side_effect=gated_remove)
    deop = asyncio.create_task(UserManagement.user_deop.callback(cog, _interaction(guild, caller), member=target))
    for _ in range(5):
        await asyncio.sleep(0)
    assert not deop.done()

    with patch.object(user_management, "_utcnow", return_value=NOW + timedelta(minutes=1)):
        await _op(cog, guild, caller, target, 60)
    newer = cog.deop_tasks[GUILD_A, TARGET_ID]
    gate.set()
    await deop

    assert _saved()[str(GUILD_A)][str(TARGET_ID)]["expires_at"] == "2026-10-28T16:01:00+00:00"
    assert cog.deop_tasks[GUILD_A, TARGET_ID] is newer
    assert not newer.done()


async def test_expiry_with_the_op_channel_gone_logs_the_member(cog: UserManagement, caplog: pytest.LogCaptureFixture) -> None:
    """With the op channel gone, expiry logs the member instead."""
    guild, target, caller = _server(GUILD_A)
    await _op(cog, guild, caller, target, 5)
    target.roles.append(guild.roles[0])
    cog.bot.get_guild = MagicMock(return_value=guild)
    guild.get_channel.return_value = None

    with patch.object(user_management, "_utcnow", return_value=NOW + timedelta(minutes=5)):
        await cog._expire(GUILD_A, TARGET_ID)

    lines = [r.getMessage() for r in caplog.records if "gone" in r.getMessage()]
    assert len(lines) == 1
    assert f"guild_id={GUILD_A}" in lines[0]
    assert f"member_id={TARGET_ID}" in lines[0]


@pytest.mark.parametrize("channel_type", [ForumChannel, CategoryChannel])
async def test_notice_for_channel_that_cannot_receive_posts_nothing_and_warns(
    channel_type: type, caplog: pytest.LogCaptureFixture
) -> None:
    """A forum or category op channel gets no post and the existing warning."""
    channel = MagicMock(spec=channel_type)
    channel.send = AsyncMock()
    guild = make_guild(GUILD_A, [], [], channel=channel)
    expiry = OpExpiry(expires_at=NOW, channel_id=OP_CHANNEL_ID)

    await UserManagement._post_op_notice(guild, TARGET_ID, expiry, "text")

    channel.send.assert_not_awaited()
    assert f"Op channel {OP_CHANNEL_ID} gone in guild_id={GUILD_A} member_id={TARGET_ID}; notice not posted" in caplog.text


async def test_notice_for_text_channel_is_posted() -> None:
    """A text op channel receives the notice."""
    guild = make_guild(GUILD_A, [], [])
    expiry = OpExpiry(expires_at=NOW, channel_id=OP_CHANNEL_ID)

    await UserManagement._post_op_notice(guild, TARGET_ID, expiry, "text")

    guild.get_channel.return_value.send.assert_awaited_once_with("text")
