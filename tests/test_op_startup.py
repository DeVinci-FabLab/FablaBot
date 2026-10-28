"""At start, and on every reconnect, each recorded op is replayed once and no unrecorded one survives."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from conftest import make_guild, make_member, make_role
from discord import Forbidden
import pytest

from fablabot.cogs import user_management
from fablabot.cogs.user_management import UserManagement
from fablabot.helpers.constants import RoleNames

NOW = datetime(2026, 10, 28, 15, 0, tzinfo=UTC)
GUILD_ID, OTHER_GUILD_ID = 1001, 1002
HOLDER_ID = 501
STATE_FILE = Path("data/temp_admin_state.json")


@pytest.fixture(autouse=True)
def _fixed_clock():  # noqa: ANN202
    with patch.object(user_management, "_utcnow", return_value=NOW):
        yield


def _write_state(payload: object) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")


def _record(guild_id: int, member_id: int, expires_at: datetime) -> dict:
    return {str(guild_id): {str(member_id): {"expires_at": expires_at.isoformat(), "channel_id": 777}}}


def _saved() -> dict:
    return json.loads(STATE_FILE.read_text(encoding="utf-8")) if STATE_FILE.exists() else {}


def _holder_guild() -> tuple[MagicMock, MagicMock, MagicMock]:
    temp = make_role(RoleNames.ADMIN_TEMP)
    holder = make_member(HOLDER_ID, temp)
    guild = make_guild(GUILD_ID, [temp], [holder])
    return guild, holder, temp


async def _start(*guilds: MagicMock) -> UserManagement:
    bot = MagicMock()
    bot.guilds = list(guilds)
    bot.get_guild = lambda gid: next((g for g in guilds if g.id == gid), None)
    cog = UserManagement(bot)
    await cog.on_ready()
    return cog


def _stop(cog: UserManagement) -> None:
    for task in cog.deop_tasks.values():
        task.cancel()


async def test_start_removes_holder_without_record(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    guild, holder, temp = _holder_guild()

    _stop(await _start(guild))

    holder.remove_roles.assert_awaited_once()
    assert holder.remove_roles.await_args.args == (temp,)
    assert "no recorded expiry" in holder.remove_roles.await_args.kwargs["reason"]
    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert any(f"guild_id={GUILD_ID}" in m and f"member_id={HOLDER_ID}" in m for m in warnings)


async def test_start_removes_holder_with_past_expiry_and_clears_record() -> None:
    _write_state(_record(GUILD_ID, HOLDER_ID, NOW - timedelta(minutes=1)))
    guild, holder, _temp = _holder_guild()

    _stop(await _start(guild))

    assert "expiry passed" in holder.remove_roles.await_args.kwargs["reason"]
    assert str(GUILD_ID) not in _saved()


async def test_start_keeps_future_holder_until_stored_time() -> None:
    due = NOW + timedelta(minutes=12)
    _write_state(_record(GUILD_ID, HOLDER_ID, due))
    guild, holder, _temp = _holder_guild()

    with patch.object(UserManagement, "_arm_deop") as arm:
        await _start(guild)

    holder.remove_roles.assert_not_awaited()
    arm.assert_called_once_with(GUILD_ID, HOLDER_ID, due)


async def test_restart_and_reconnect_keep_one_timer_and_the_record() -> None:
    _write_state(_record(GUILD_ID, HOLDER_ID, NOW + timedelta(minutes=12)))
    guild, holder, _temp = _holder_guild()

    first = await _start(guild)
    _stop(first)
    cog = await _start(guild)
    task = cog.deop_tasks[GUILD_ID, HOLDER_ID]
    await cog.on_ready()

    assert list(cog.deop_tasks) == [(GUILD_ID, HOLDER_ID)]
    assert cog.deop_tasks[GUILD_ID, HOLDER_ID] is task
    assert str(HOLDER_ID) in _saved()[str(GUILD_ID)]
    holder.remove_roles.assert_not_awaited()
    _stop(cog)


async def test_start_clears_records_of_members_without_the_role() -> None:
    _write_state(_record(GUILD_ID, 999, NOW + timedelta(minutes=12)))
    guild, _holder, _temp = _holder_guild()

    _stop(await _start(guild))

    assert str(GUILD_ID) not in _saved()


async def test_start_keeps_records_of_unreachable_guilds() -> None:
    state = _record(OTHER_GUILD_ID, HOLDER_ID, NOW - timedelta(minutes=1))
    _write_state(state)
    guild, _holder, _temp = _holder_guild()
    offline = make_guild(OTHER_GUILD_ID, [], [])
    offline.unavailable = True

    _stop(await _start(guild, offline))

    assert _saved()[str(OTHER_GUILD_ID)] == state[str(OTHER_GUILD_ID)]


async def test_start_ignores_guild_without_temp_role(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    member = make_member(HOLDER_ID)
    guild = make_guild(GUILD_ID, [make_role("Membre")], [member])

    _stop(await _start(guild))

    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


async def test_failed_start_removal_keeps_record(caplog: pytest.LogCaptureFixture) -> None:
    _write_state(_record(GUILD_ID, HOLDER_ID, NOW - timedelta(minutes=1)))
    guild, holder, _temp = _holder_guild()
    holder.remove_roles = AsyncMock(side_effect=Forbidden(MagicMock(status=403), "missing permissions"))

    _stop(await _start(guild))

    assert str(HOLDER_ID) in _saved()[str(GUILD_ID)]
    errors = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
    assert any(f"guild_id={GUILD_ID}" in m and f"member_id={HOLDER_ID}" in m for m in errors)


@pytest.mark.parametrize(
    "payload",
    [
        "{not json",
        "[1, 2]",
        json.dumps({str(GUILD_ID): {str(HOLDER_ID): {"expires_at": "soon", "channel_id": 777}}}),
        json.dumps({str(GUILD_ID): {str(HOLDER_ID): {"expires_at": "2026-10-28T18:00:00", "channel_id": 777}}}),
    ],
    ids=["not-json", "not-a-mapping", "bad-time", "naive-time"],
)
async def test_unreadable_state_fails_closed(caplog: pytest.LogCaptureFixture, payload: str) -> None:
    _write_state(payload)
    guild, holder, _temp = _holder_guild()

    _stop(await _start(guild))

    holder.remove_roles.assert_awaited_once()
    assert [r for r in caplog.records if r.levelname == "ERROR"]


async def test_op_landing_during_the_sweep_keeps_its_record_and_timer() -> None:
    guild, holder, _temp = _holder_guild()
    due = NOW + timedelta(minutes=30)
    cog_box: list[UserManagement] = []

    async def op_lands_during_removal(*_args: object, **_kwargs: object) -> None:
        # A /user op served while the removal is awaited: the record and the timer exist, the cache does not show the role yet.
        cog = cog_box[0]
        cog._record_op(GUILD_ID, 777, user_management.OpExpiry(due, 123))  # noqa: SLF001
        cog._arm_deop(GUILD_ID, 777, due)  # noqa: SLF001

    holder.remove_roles = AsyncMock(side_effect=op_lands_during_removal)
    bot = MagicMock()
    bot.guilds = [guild]
    cog = UserManagement(bot)
    cog_box.append(cog)

    await cog.on_ready()

    assert str(777) in _saved()[str(GUILD_ID)]
    assert (GUILD_ID, 777) in cog.deop_tasks
    _stop(cog)


async def test_start_with_partial_member_cache_keeps_future_record_and_arms_it() -> None:
    due = NOW + timedelta(minutes=12)
    _write_state(_record(GUILD_ID, 999, due))
    guild, _holder, _temp = _holder_guild()
    guild.chunked = False

    with patch.object(UserManagement, "_arm_deop") as arm:
        await _start(guild)

    arm.assert_called_once_with(GUILD_ID, 999, due)
    assert str(999) in _saved()[str(GUILD_ID)]


async def test_start_with_partial_member_cache_ends_a_past_op_of_a_member_the_cache_misses() -> None:
    _write_state(_record(GUILD_ID, 999, NOW - timedelta(minutes=1)))
    guild, _holder, temp = _holder_guild()
    guild.chunked = False
    missing = make_member(999, temp)
    temp.members.remove(missing)  # holds the role on Discord, absent from the cache
    guild.fetch_member = AsyncMock(return_value=missing)

    cog = await _start(guild)
    await cog.deop_tasks[GUILD_ID, 999]

    missing.remove_roles.assert_awaited_once()
    assert str(GUILD_ID) not in _saved() or "999" not in _saved()[str(GUILD_ID)]
    _stop(cog)


async def test_guild_back_from_an_outage_is_reconciled() -> None:
    due = NOW + timedelta(minutes=12)
    _write_state(_record(GUILD_ID, HOLDER_ID, due))
    guild, holder, _temp = _holder_guild()
    guild.unavailable = True
    cog = await _start(guild)
    assert not cog.deop_tasks

    guild.unavailable = False
    await cog.on_guild_available(guild)

    assert (GUILD_ID, HOLDER_ID) in cog.deop_tasks
    holder.remove_roles.assert_not_awaited()
    _stop(cog)


@pytest.mark.parametrize("entry", [[], None, "x"], ids=["list", "null", "string"])
async def test_start_resets_a_malformed_guild_entry(caplog: pytest.LogCaptureFixture, entry: object) -> None:
    _write_state({str(GUILD_ID): entry, str(OTHER_GUILD_ID): _record(OTHER_GUILD_ID, HOLDER_ID, NOW)[str(OTHER_GUILD_ID)]})
    guild, _holder, _temp = _holder_guild()

    _stop(await _start(guild))

    assert str(GUILD_ID) not in _saved()
    assert str(OTHER_GUILD_ID) in _saved()
    errors = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
    assert any(f"guild_id={GUILD_ID}" in m for m in errors)
