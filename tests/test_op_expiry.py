"""Each op is recorded per server and member, and ends on time while the bot runs."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from conftest import make_guild, make_interaction, make_member, make_role
from discord import Forbidden
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
def _fixed_clock_and_context():  # noqa: ANN202
    """Freeze the clock and let every caller through the shared context check."""
    with (
        patch.object(user_management, "_utcnow", return_value=NOW),
        patch.object(user_management, "ensure_command_context", AsyncMock(return_value=True)),
    ):
        yield


@pytest.fixture
async def cog():  # noqa: ANN201
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
    guild, target, caller = _server(GUILD_A)
    interaction = _interaction(guild, caller)
    seen: list[dict] = []
    interaction.response.send_message = AsyncMock(side_effect=lambda *_a, **_k: seen.append(_saved()))

    await UserManagement.user_op.callback(cog, interaction, member=target, reason="atelier", time=30)

    record = seen[0][str(GUILD_A)][str(TARGET_ID)]
    assert record == {"expires_at": "2026-10-28T15:30:00+00:00", "channel_id": OP_CHANNEL_ID}
    assert set(cog.deop_tasks) == {(GUILD_A, TARGET_ID)}


async def test_reop_replaces_expiry_and_keeps_one_timer(cog: UserManagement) -> None:
    guild, target, caller = _server(GUILD_A)
    await _op(cog, guild, caller, target, 30)
    first = cog.deop_tasks[(GUILD_A, TARGET_ID)]

    await _op(cog, guild, caller, target, 10)
    await asyncio.sleep(0)

    assert _saved()[str(GUILD_A)][str(TARGET_ID)]["expires_at"] == "2026-10-28T15:10:00+00:00"
    assert first.cancelled()
    assert len(cog.deop_tasks) == 1


async def test_expiry_removes_role_clears_record_and_posts_in_op_channel(cog: UserManagement) -> None:
    guild, target, caller = _server(GUILD_A)
    interaction = await _op(cog, guild, caller, target, 90)
    temp = guild.roles[0]
    target.roles.append(temp)
    cog.bot.get_guild = MagicMock(return_value=guild)

    with patch.object(user_management, "_utcnow", return_value=NOW + timedelta(minutes=90)):
        await cog._expire(GUILD_A, TARGET_ID)  # noqa: SLF001

    target.remove_roles.assert_awaited_once()
    assert str(TARGET_ID) not in _saved().get(str(GUILD_A), {})
    guild.get_channel.assert_called_with(OP_CHANNEL_ID)
    notice = guild.get_channel.return_value.send.await_args.args[0]
    assert "<@501>" in notice
    interaction.followup.send.assert_not_awaited()


async def test_expiry_twice_posts_one_notice(cog: UserManagement) -> None:
    guild, target, caller = _server(GUILD_A)
    await _op(cog, guild, caller, target, 5)
    target.roles.append(guild.roles[0])
    cog.bot.get_guild = MagicMock(return_value=guild)

    with patch.object(user_management, "_utcnow", return_value=NOW + timedelta(minutes=5)):
        await cog._expire(GUILD_A, TARGET_ID)  # noqa: SLF001
        await cog._expire(GUILD_A, TARGET_ID)  # noqa: SLF001

    guild.get_channel.return_value.send.assert_awaited_once()


async def test_expiry_for_member_who_left_clears_record_quietly(cog: UserManagement, caplog: pytest.LogCaptureFixture) -> None:
    guild, target, caller = _server(GUILD_A)
    await _op(cog, guild, caller, target, 5)
    guild.members.remove(target)
    cog.bot.get_guild = MagicMock(return_value=guild)

    with patch.object(user_management, "_utcnow", return_value=NOW + timedelta(minutes=5)):
        await cog._expire(GUILD_A, TARGET_ID)  # noqa: SLF001

    assert str(TARGET_ID) not in _saved().get(str(GUILD_A), {})
    assert not [r for r in caplog.records if r.levelname == "ERROR"]


async def test_failed_expiry_keeps_record_and_warns_op_channel(cog: UserManagement, caplog: pytest.LogCaptureFixture) -> None:
    guild, target, caller = _server(GUILD_A)
    await _op(cog, guild, caller, target, 5)
    target.roles.append(guild.roles[0])
    target.remove_roles = AsyncMock(side_effect=Forbidden(MagicMock(status=403), "missing permissions"))
    cog.bot.get_guild = MagicMock(return_value=guild)

    with patch.object(user_management, "_utcnow", return_value=NOW + timedelta(minutes=5)):
        await cog._expire(GUILD_A, TARGET_ID)  # noqa: SLF001

    assert str(TARGET_ID) in _saved()[str(GUILD_A)]
    guild.get_channel.return_value.send.assert_awaited_once()
    errors = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
    assert any(f"guild_id={GUILD_A}" in m and f"member_id={TARGET_ID}" in m for m in errors)


async def test_deop_cancels_timer_and_clears_record(cog: UserManagement) -> None:
    guild, target, caller = _server(GUILD_A)
    await _op(cog, guild, caller, target, 30)
    task = cog.deop_tasks[(GUILD_A, TARGET_ID)]

    await UserManagement.user_deop.callback(cog, _interaction(guild, caller), member=target)
    await asyncio.sleep(0)

    assert task.cancelled()
    assert (GUILD_A, TARGET_ID) not in cog.deop_tasks
    assert str(TARGET_ID) not in _saved().get(str(GUILD_A), {})


async def test_servers_are_kept_apart(cog: UserManagement) -> None:
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
    expiry = OpExpiry(expires_at=NOW, channel_id=OP_CHANNEL_ID)
    assert OpExpiry.from_dict(expiry.to_dict()) == expiry
    with pytest.raises(ValueError, match="timezone"):
        OpExpiry.from_dict({"expires_at": "2026-10-28T15:00:00", "channel_id": 1})


def test_delay_counts_from_the_stored_time() -> None:
    assert user_management._delay_until(NOW + timedelta(minutes=7)) == 420  # noqa: SLF001
    assert user_management._delay_until(NOW - timedelta(minutes=7)) == 0  # noqa: SLF001
