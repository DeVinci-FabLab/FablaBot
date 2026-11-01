"""The reminder tick and the registration update both rewrite the published message: neither undoes the other.

A storage failure on either write loses neither the reminder flags nor a registration.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from discord import TextChannel
import pytest

from fablabot import db
from fablabot.cogs import formation_management as fm_cog
from fablabot.helpers.constants import PARIS_TZ
from fablabot.logging_handlers import guild_log_context
from fablabot.logging_handlers.guild_context import GuildContextFilter
from fablabot.models import FmMessageDraft, Formation, PublishedMessage, ReactionAction, ReactionEvent
from fablabot.services import formations

GUILD_ID = 1234
OTHER_GUILD_ID = 2222
NEW_GUILD_ID = 98765
MESSAGE_ID = 555
OTHER_MESSAGE_ID = 777
EMOJI = "🔧"
MEMBER_ID = 42
OTHER_MEMBER_ID = 43


class _Env:
    """A published message (one hour away unless told otherwise), a stub guild, and spies on every notification."""

    def __init__(self, cog: Any, monkeypatch: pytest.MonkeyPatch, *, start_in: timedelta = timedelta(hours=1)) -> None:
        self.cog = cog
        self.gate = asyncio.Event()
        self.entered = asyncio.Event()
        self.fetch_gate = asyncio.Event()
        self.fetch_entered = asyncio.Event()

        start = datetime.now(PARIS_TZ) + start_in
        fm = Formation(emoji=EMOJI, name="Soudure", trainer_mention="<@1>", start_iso=start.isoformat(), duration="2h", seats=5)
        cog._set_last_published_in_guild(
            GUILD_ID,
            PublishedMessage(
                message_id=MESSAGE_ID,
                channel_id=77,
                message=FmMessageDraft(header="h", role_id=9, intro="i", fms=[fm], end="e"),
            ),
        )
        cog._reaction_logs.append(
            GUILD_ID,
            ReactionEvent(
                message_id=MESSAGE_ID,
                user_id=MEMBER_ID,
                user_name="alice",
                emoji=EMOJI,
                action=ReactionAction.ADD,
                ts_iso=datetime.now(PARIS_TZ).isoformat(timespec="seconds"),
            ),
        )

        user = MagicMock(display_name="Alice", name="alice", bot=False, id=MEMBER_ID)
        user.name = "alice"

        async def _users() -> Any:
            yield user

        reaction = MagicMock(emoji=EMOJI)
        reaction.users = _users
        message = MagicMock(id=MESSAGE_ID, reactions=[reaction])
        message.edit = AsyncMock()

        async def _fetch(_message_id: int) -> MagicMock:
            self.fetch_entered.set()
            await self.fetch_gate.wait()
            return message

        channel = MagicMock(spec=TextChannel)
        channel.fetch_message = _fetch
        guild = MagicMock(id=GUILD_ID)
        guild.get_channel = MagicMock(return_value=channel)
        monkeypatch.setattr(cog.bot, "get_guild", lambda _guild_id: guild)

        async def _held(*_args: Any, **_kwargs: Any) -> None:
            self.entered.set()
            await self.gate.wait()

        self.fetch_gate.set()
        self.gate.set()
        self.notify_trainer = AsyncMock(side_effect=_held)
        self.notify_participants = AsyncMock()
        self.registration_dm = AsyncMock()
        monkeypatch.setattr(fm_cog, "notify_trainer_before_formation", self.notify_trainer)
        monkeypatch.setattr(fm_cog, "notify_responsible_before_formation", AsyncMock())
        monkeypatch.setattr(fm_cog, "notify_participants_before_formation", self.notify_participants)
        monkeypatch.setattr(fm_cog, "send_registration_dm", self.registration_dm)
        monkeypatch.setattr(fm_cog, "send_waitlist_dm", AsyncMock())
        monkeypatch.setattr(fm_cog, "send_promotion_dm", AsyncMock())
        monkeypatch.setattr(fm_cog, "format_respo_contacts", lambda _guild: "")


@pytest.fixture
async def env(started_bot: Any, monkeypatch: pytest.MonkeyPatch) -> _Env:
    """The real formation cog over a published message and stubbed Discord."""
    return _Env(started_bot.get_cog("FormationManagement"), monkeypatch)


async def _let_others_run() -> None:
    for _ in range(20):
        await asyncio.sleep(0)


async def test_reminder_tick_during_an_update_fetch_is_not_sent_twice(env: _Env) -> None:
    """A tick that lands while the update waits on Discord keeps its notified flag; the next tick sends no second reminder."""
    env.fetch_gate.clear()
    update = asyncio.create_task(env.cog._update_published_message(GUILD_ID))
    await env.fetch_entered.wait()
    tick = asyncio.create_task(env.cog._check_upcoming_formations())
    await _let_others_run()
    env.fetch_gate.set()
    await asyncio.gather(update, tick)

    await env.cog._check_upcoming_formations()

    assert env.notify_participants.await_count == 1


async def test_registration_during_the_reminder_dms_is_not_announced_twice(env: _Env) -> None:
    """A registration written while the tick is sending DMs survives the tick; its member gets one registration DM."""
    env.gate.clear()
    tick = asyncio.create_task(env.cog._check_upcoming_formations())
    await env.entered.wait()
    update = asyncio.create_task(env.cog._update_published_message(GUILD_ID))
    await _let_others_run()
    env.gate.set()
    await asyncio.gather(tick, update)

    await env.cog._update_published_message(GUILD_ID)

    assert env.registration_dm.await_count == 1
    pub = env.cog._get_last_published_in_guild(GUILD_ID)
    assert pub is not None
    assert [entry["user_id"] for entry in pub.message.fms[0].registered_users] == [MEMBER_ID]


async def test_tick_waiting_on_the_lock_survives_a_new_guild(started_bot: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """A guild's first state entry written while the tick waits on the lock must not break the tick's walk over the guilds."""
    cog = started_bot.get_cog("FormationManagement")
    _Env(cog, monkeypatch, start_in=timedelta(days=3))  # nothing due: only the wait on the lock yields to the loop

    async with cog._published_lock(GUILD_ID):
        tick = asyncio.create_task(cog._check_upcoming_formations())
        await _let_others_run()
        cog.set_guild_draft(NEW_GUILD_ID, FmMessageDraft(header="", role_id=0, intro="", fms=[], end=""))

    await tick


async def test_a_publish_during_an_update_stays_the_current_message(env: _Env) -> None:
    """An update waiting on Discord for the old message writes it under its own ID: the new message stays current."""
    env.fetch_gate.clear()
    update = asyncio.create_task(env.cog._update_published_message(GUILD_ID))
    await env.fetch_entered.wait()
    fm = Formation(
        emoji=EMOJI, name="Nouvelle", trainer_mention="<@1>", start_iso="2099-06-01T18:00:00+02:00", duration="2h", seats=5
    )
    env.cog._set_last_published_in_guild(
        GUILD_ID,
        PublishedMessage(
            message_id=MESSAGE_ID + 1,
            channel_id=77,
            message=FmMessageDraft(header="h", role_id=9, intro="i", fms=[fm], end="e"),
        ),
    )
    env.fetch_gate.set()
    await update

    pub = env.cog._get_last_published_in_guild(GUILD_ID)
    assert pub is not None
    assert pub.message_id == MESSAGE_ID + 1
    assert pub.message.fms[0].registered_users == []


def _flaky(real: Any, error: Exception, times: int = 1) -> Any:
    """Wrap ``real`` so its first ``times`` calls raise ``error``."""
    failures = [error] * times

    def call(*args: Any, **kwargs: Any) -> Any:
        if failures:
            raise failures.pop()
        return real(*args, **kwargs)

    return call


def _reaction_payload(guild_id: int = GUILD_ID, message_id: int = MESSAGE_ID, user_id: int = MEMBER_ID) -> MagicMock:
    """A member's reaction to the published message of a server."""
    member = MagicMock(bot=False)
    member.name = "alice"
    return MagicMock(
        guild_id=guild_id, member=member, message_id=message_id, emoji=EMOJI, user_id=user_id, event_type="REACTION_ADD"
    )


def _forget_logged_reactions() -> None:
    """Empty the reaction log, so the member's reaction is the one under test."""
    with db.transaction() as conn:
        conn.execute("DELETE FROM reaction_event")


async def test_a_reaction_whose_log_write_fails_is_kept_and_registers_once_storage_answers(
    env: _Env, monkeypatch: pytest.MonkeyPatch, storage_error: Exception
) -> None:
    """A reaction that could not be logged is neither raised nor lost: the next update writes it and registers the member."""
    _forget_logged_reactions()
    monkeypatch.setattr(env.cog._reaction_logs, "append", _flaky(env.cog._reaction_logs.append, storage_error))

    await env.cog.on_raw_reaction_event(_reaction_payload())

    assert GUILD_ID in env.cog._pending_update_tasks
    await env.cog._update_published_message(GUILD_ID)
    pub = env.cog._get_last_published_in_guild(GUILD_ID)
    assert pub is not None
    assert [entry["user_id"] for entry in pub.message.fms[0].registered_users] == [MEMBER_ID]
    assert env.registration_dm.await_count == 1
    assert len(env.cog._reaction_logs.history(GUILD_ID, MESSAGE_ID)) == 1


async def test_a_member_whose_reaction_stays_unlogged_is_registered_once_the_log_answers(
    env: _Env, monkeypatch: pytest.MonkeyPatch, storage_error: Exception
) -> None:
    """While the log still cannot be written the update goes on without the member; the next one registers them."""
    _forget_logged_reactions()
    real = env.cog._reaction_logs.append
    monkeypatch.setattr(env.cog._reaction_logs, "append", _flaky(real, storage_error, times=2))
    await env.cog.on_raw_reaction_event(_reaction_payload())

    await env.cog._update_published_message(GUILD_ID)

    pub = env.cog._get_last_published_in_guild(GUILD_ID)
    assert pub is not None
    assert pub.message.fms[0].registered_users == []
    assert env.registration_dm.await_count == 0

    await env.cog._update_published_message(GUILD_ID)

    pub = env.cog._get_last_published_in_guild(GUILD_ID)
    assert pub is not None
    assert [entry["user_id"] for entry in pub.message.fms[0].registered_users] == [MEMBER_ID]


async def test_the_reminder_tick_schedules_the_update_of_kept_reactions(
    env: _Env, monkeypatch: pytest.MonkeyPatch, storage_error: Exception
) -> None:
    """A member whose reaction could not be logged is registered without waiting for another reaction."""
    _forget_logged_reactions()
    monkeypatch.setattr(env.cog._reaction_logs, "append", _flaky(env.cog._reaction_logs.append, storage_error))
    await env.cog.on_raw_reaction_event(_reaction_payload())
    env.cog._pending_update_tasks.pop(GUILD_ID).cancel()

    await env.cog._check_upcoming_formations()

    assert GUILD_ID in env.cog._pending_update_tasks


async def test_a_reminder_is_not_sent_again_when_saving_its_flag_fails(
    env: _Env, monkeypatch: pytest.MonkeyPatch, storage_error: Exception
) -> None:
    """A failed save of the notified flag keeps the flag in memory: the next tick sends nothing and saves it."""
    monkeypatch.setattr(formations, "save_published", _flaky(formations.save_published, storage_error))

    await env.cog._check_upcoming_formations()
    await env.cog._check_upcoming_formations()

    assert env.notify_participants.await_count == 1
    stored = formations.current_published(GUILD_ID)
    assert stored is not None
    assert stored.message.fms[0].notified_hour_before is True


async def test_an_unsaved_message_is_current_until_a_newer_one_is_saved(
    env: _Env, monkeypatch: pytest.MonkeyPatch, storage_error: Exception
) -> None:
    """A message kept in memory after a failed save stays current against a late write of an older one, not a newer one."""
    fm = Formation(
        emoji=EMOJI, name="Soudure", trainer_mention="<@1>", start_iso="2099-06-01T18:00:00+02:00", duration="2h", seats=5
    )

    def message(message_id: int) -> PublishedMessage:
        return PublishedMessage(
            message_id=message_id,
            channel_id=77,
            message=FmMessageDraft(header="h", role_id=9, intro="i", fms=[fm], end="e"),
        )

    def current_id() -> int | None:
        current = env.cog._get_last_published_in_guild(GUILD_ID)
        return current.message_id if current else None

    monkeypatch.setattr(formations, "save_published", _flaky(formations.save_published, storage_error))
    env.cog._set_last_published_in_guild(GUILD_ID, message(MESSAGE_ID + 1))
    assert current_id() == MESSAGE_ID + 1

    env.cog._set_last_published_in_guild(GUILD_ID, message(MESSAGE_ID))
    assert current_id() == MESSAGE_ID + 1

    env.cog._set_last_published_in_guild(GUILD_ID, message(MESSAGE_ID + 2))
    assert current_id() == MESSAGE_ID + 2


def _message(message_id: int, *, start_iso: str = "2099-06-01T18:00:00+02:00") -> PublishedMessage:
    """A published message with one formation, under the given message ID."""
    fm = Formation(emoji=EMOJI, name="Soudure", trainer_mention="<@1>", start_iso=start_iso, duration="2h", seats=5)
    return PublishedMessage(
        message_id=message_id,
        channel_id=77,
        message=FmMessageDraft(header="h", role_id=9, intro="i", fms=[fm], end="e"),
    )


def _current_id(cog: Any, guild_id: int = GUILD_ID) -> int | None:
    """The ID of the message the cog treats as current on a server."""
    current = cog._get_last_published_in_guild(guild_id)
    return current.message_id if current else None


async def test_a_failed_write_of_an_older_message_leaves_a_newer_unsaved_one_current(
    env: _Env, monkeypatch: pytest.MonkeyPatch, storage_error: Exception
) -> None:
    """A late write of the old message that fails too must not replace the newer message that exists only in memory."""
    monkeypatch.setattr(formations, "save_published", _flaky(formations.save_published, storage_error, times=2))
    env.cog._set_last_published_in_guild(GUILD_ID, _message(MESSAGE_ID + 1))

    env.cog._set_last_published_in_guild(GUILD_ID, _message(MESSAGE_ID))

    assert _current_id(env.cog) == MESSAGE_ID + 1


async def test_a_failed_write_of_an_older_message_does_not_hide_a_newer_saved_one(
    env: _Env, monkeypatch: pytest.MonkeyPatch, storage_error: Exception
) -> None:
    """A late write of the old message that fails leaves the saved newer message current, and its reactions still count."""
    env.cog._set_last_published_in_guild(GUILD_ID, _message(MESSAGE_ID + 1))
    monkeypatch.setattr(formations, "save_published", _flaky(formations.save_published, storage_error))
    env.cog._set_last_published_in_guild(GUILD_ID, _message(MESSAGE_ID))
    _forget_logged_reactions()

    assert _current_id(env.cog) == MESSAGE_ID + 1
    payload = _reaction_payload()
    payload.message_id = MESSAGE_ID + 1
    await env.cog.on_raw_reaction_event(payload)

    assert len(env.cog._reaction_logs.history(GUILD_ID, MESSAGE_ID + 1)) == 1


async def test_an_unsaved_message_written_late_after_a_newer_one_does_not_become_current(
    env: _Env, monkeypatch: pytest.MonkeyPatch, storage_error: Exception
) -> None:
    """A message that was only in memory and reaches the database after a newer one is stored without taking its place."""
    monkeypatch.setattr(formations, "save_published", _flaky(formations.save_published, storage_error))
    env.cog._set_last_published_in_guild(GUILD_ID, _message(MESSAGE_ID + 1))
    env.cog._set_last_published_in_guild(GUILD_ID, _message(MESSAGE_ID + 2))

    env.cog._set_last_published_in_guild(GUILD_ID, _message(MESSAGE_ID + 1))

    stored = formations.current_published(GUILD_ID)
    assert stored is not None
    assert stored.message_id == MESSAGE_ID + 2
    assert _current_id(env.cog) == MESSAGE_ID + 2


async def test_a_message_kept_in_memory_is_not_current_on_another_server(
    env: _Env, monkeypatch: pytest.MonkeyPatch, storage_error: Exception
) -> None:
    """The copy kept after a failed save belongs to its server: another server still sees its own message, or none."""
    monkeypatch.setattr(formations, "save_published", _flaky(formations.save_published, storage_error))
    env.cog._set_last_published_in_guild(OTHER_GUILD_ID, _message(MESSAGE_ID + 5))

    assert _current_id(env.cog, OTHER_GUILD_ID) == MESSAGE_ID + 5
    assert _current_id(env.cog, GUILD_ID) == MESSAGE_ID
    assert _current_id(env.cog, NEW_GUILD_ID) is None


def _two_servers_with_kept_reactions(
    env: _Env, monkeypatch: pytest.MonkeyPatch, storage_error: Exception, *, refusals: int = 2
) -> Any:
    """Publish on a second server, then let the reaction log refuse its first ``refusals`` writes.

    Returns:
        The recorded calls of ``_schedule_update``, as ``(guild_id, delay)``.
    """
    _forget_logged_reactions()
    env.cog._set_last_published_in_guild(OTHER_GUILD_ID, _message(OTHER_MESSAGE_ID))
    scheduled: list[tuple[int, float]] = []
    monkeypatch.setattr(env.cog, "_schedule_update", lambda guild_id, delay=2.0: scheduled.append((guild_id, delay)))
    monkeypatch.setattr(env.cog._reaction_logs, "append", _flaky(env.cog._reaction_logs.append, storage_error, times=refusals))
    return scheduled


async def test_a_reaction_kept_for_one_server_is_written_and_scheduled_when_another_servers_reaction_arrives(
    env: _Env, monkeypatch: pytest.MonkeyPatch, storage_error: Exception
) -> None:
    """Whoever writes the shared queue updates the server whose reaction it wrote, or its member stays unregistered."""
    scheduled = _two_servers_with_kept_reactions(env, monkeypatch, storage_error, refusals=1)
    await env.cog.on_raw_reaction_event(_reaction_payload())
    scheduled.clear()

    await env.cog.on_raw_reaction_event(_reaction_payload(OTHER_GUILD_ID, OTHER_MESSAGE_ID, OTHER_MEMBER_ID))

    assert {guild_id for guild_id, _delay in scheduled} == {GUILD_ID, OTHER_GUILD_ID}
    await env.cog._update_published_message(GUILD_ID)
    pub = env.cog._get_last_published_in_guild(GUILD_ID)
    assert pub is not None
    assert [entry["user_id"] for entry in pub.message.fms[0].registered_users] == [MEMBER_ID]


async def test_an_update_that_writes_another_servers_kept_reaction_schedules_that_servers_update(
    env: _Env, monkeypatch: pytest.MonkeyPatch, storage_error: Exception
) -> None:
    """The update of one server writes the whole queue: the server whose reaction it wrote gets its own update."""
    scheduled = _two_servers_with_kept_reactions(env, monkeypatch, storage_error, refusals=1)
    await env.cog.on_raw_reaction_event(_reaction_payload(OTHER_GUILD_ID, OTHER_MESSAGE_ID, OTHER_MEMBER_ID))
    scheduled.clear()

    await env.cog._update_published_message(GUILD_ID)

    assert [guild_id for guild_id, _delay in scheduled] == [OTHER_GUILD_ID]
    assert len(env.cog._reaction_logs.history(OTHER_GUILD_ID, OTHER_MESSAGE_ID)) == 1


async def test_kept_reactions_of_two_servers_are_each_written_under_their_own_server(
    env: _Env, monkeypatch: pytest.MonkeyPatch, storage_error: Exception
) -> None:
    """A queue holding two servers' reactions writes each one under the server it came from."""
    _two_servers_with_kept_reactions(env, monkeypatch, storage_error)
    await env.cog.on_raw_reaction_event(_reaction_payload())
    await env.cog.on_raw_reaction_event(_reaction_payload(OTHER_GUILD_ID, OTHER_MESSAGE_ID, OTHER_MEMBER_ID))

    written = env.cog._log_kept_reactions()

    assert written == {GUILD_ID, OTHER_GUILD_ID}
    assert [ev.user_id for ev in env.cog._reaction_logs.history(GUILD_ID, MESSAGE_ID)] == [MEMBER_ID]
    assert [ev.user_id for ev in env.cog._reaction_logs.history(OTHER_GUILD_ID, OTHER_MESSAGE_ID)] == [OTHER_MEMBER_ID]
    assert env.cog._reaction_logs.history(GUILD_ID, OTHER_MESSAGE_ID) == []
    assert env.cog._reaction_logs.history(OTHER_GUILD_ID, MESSAGE_ID) == []


async def test_the_reminder_tick_schedules_the_update_of_every_server_with_kept_reactions(
    env: _Env, monkeypatch: pytest.MonkeyPatch, storage_error: Exception
) -> None:
    """Each server holding a kept reaction gets its update at the tick, not only the first one."""
    scheduled = _two_servers_with_kept_reactions(env, monkeypatch, storage_error)
    await env.cog.on_raw_reaction_event(_reaction_payload())
    await env.cog.on_raw_reaction_event(_reaction_payload(OTHER_GUILD_ID, OTHER_MESSAGE_ID, OTHER_MEMBER_ID))
    scheduled.clear()

    await env.cog._check_upcoming_formations()

    assert {guild_id for guild_id, delay in scheduled if delay == 0} == {GUILD_ID, OTHER_GUILD_ID}


@pytest.mark.parametrize("log_works", [True, False], ids=["written", "still-refused"])
async def test_a_kept_reaction_is_logged_under_its_own_server_when_another_server_drains_it(
    env: _Env, monkeypatch: pytest.MonkeyPatch, storage_error: Exception, caplog: pytest.LogCaptureFixture, log_works: bool
) -> None:
    """The log line of a kept reaction, written or failing, never lands in the log of the server that happened to drain it."""
    caplog.set_level(logging.DEBUG)
    caplog.handler.addFilter(GuildContextFilter())
    _forget_logged_reactions()
    env.cog._set_last_published_in_guild(OTHER_GUILD_ID, _message(OTHER_MESSAGE_ID))
    real = env.cog._reaction_logs.append
    refusals = 1 if log_works else 3
    monkeypatch.setattr(env.cog._reaction_logs, "append", _flaky(real, storage_error, times=refusals))
    await env.cog.on_raw_reaction_event(_reaction_payload())
    caplog.clear()

    with guild_log_context(OTHER_GUILD_ID):
        await env.cog.on_raw_reaction_event(_reaction_payload(OTHER_GUILD_ID, OTHER_MESSAGE_ID, OTHER_MEMBER_ID))

    wanted = "Logged reaction" if log_works else "Reaction log write failed"
    lines = [r for r in caplog.records if wanted in r.getMessage() and f"{GUILD_ID}" in r.getMessage()]
    assert lines
    assert {r.guild_id for r in lines} == {GUILD_ID}  # type: ignore[attr-defined]
