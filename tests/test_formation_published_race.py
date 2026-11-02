"""The reminder tick and the registration update both rewrite the published message: neither undoes the other.

A storage failure loses no registration: the reaction stays on Discord, and the update takes it back from there.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from discord import Forbidden, NotFound, TextChannel
import pytest

from fablabot import db
from fablabot.cogs import formation_management as fm_cog
from fablabot.helpers.constants import PARIS_TZ
from fablabot.helpers.guild_access import GuildAccessPolicy
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
THIRD_MEMBER_ID = 44
NAMES = {MEMBER_ID: "alice", OTHER_MEMBER_ID: "bob", THIRD_MEMBER_ID: "carol"}


class _Env:
    """A published message (one hour away unless told otherwise), a stub guild, and spies on every notification."""

    def __init__(
        self,
        cog: Any,
        monkeypatch: pytest.MonkeyPatch,
        *,
        start_in: timedelta = timedelta(hours=1),
        seats: int = 5,
        reactors: tuple[int, ...] = (MEMBER_ID,),
    ) -> None:
        self.cog = cog
        monkeypatch.setattr(cog.bot, "guild_access", GuildAccessPolicy(frozenset({GUILD_ID, OTHER_GUILD_ID, NEW_GUILD_ID})))
        self.gate = asyncio.Event()
        self.entered = asyncio.Event()
        self.fetch_gate = asyncio.Event()
        self.fetch_entered = asyncio.Event()

        start = datetime.now(PARIS_TZ) + start_in
        fm = Formation(
            emoji=EMOJI, name="Soudure", trainer_mention="<@1>", start_iso=start.isoformat(), duration="2h", seats=seats
        )
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

        users = []
        for reactor in reactors:
            user = MagicMock(display_name=NAMES[reactor].title(), bot=False, id=reactor)
            user.name = NAMES[reactor]
            users.append(user)

        async def _users() -> Any:
            for user in users:
                yield user

        reaction = MagicMock(emoji=EMOJI)
        reaction.users = _users
        message = MagicMock(id=MESSAGE_ID, reactions=[reaction])
        message.edit = AsyncMock()
        self.message = message

        async def _fetch(_message_id: int) -> MagicMock:
            self.fetch_entered.set()
            await self.fetch_gate.wait()
            return message

        channel = MagicMock(spec=TextChannel)
        channel.fetch_message = _fetch
        self.channel = channel
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


@pytest.fixture
async def env_with_two(started_bot: Any, monkeypatch: pytest.MonkeyPatch) -> _Env:
    """The same, with a second member reacting on Discord and no reaction logged yet but the first member's."""
    return _Env(started_bot.get_cog("FormationManagement"), monkeypatch, reactors=(MEMBER_ID, OTHER_MEMBER_ID))


def _add_event(user_id: int) -> ReactionEvent:
    """A member's reaction to the published message, as the listener logs it."""
    return ReactionEvent(
        message_id=MESSAGE_ID,
        user_id=user_id,
        user_name=NAMES[user_id],
        emoji=EMOJI,
        action=ReactionAction.ADD,
        ts_iso=datetime.now(PARIS_TZ).isoformat(timespec="seconds"),
    )


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


def _log_events(env: _Env, *events: tuple[int, ReactionAction, int]) -> None:
    """Replace the reaction log by events given as (member, action, minutes ago), oldest first."""
    _forget_logged_reactions()
    now = datetime.now(PARIS_TZ)
    for user_id, action, minutes_ago in events:
        env.cog._reaction_logs.append(
            GUILD_ID,
            ReactionEvent(
                message_id=MESSAGE_ID,
                user_id=user_id,
                user_name=NAMES[user_id],
                emoji=EMOJI,
                action=action,
                ts_iso=(now - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds"),
            ),
        )


def _seats(env: _Env) -> tuple[list[int], list[int]]:
    """The registered and the waitlisted member IDs of the published message."""
    pub = env.cog._get_last_published_in_guild(GUILD_ID)
    assert pub is not None
    fm = pub.message.fms[0]
    return [e["user_id"] for e in fm.registered_users], [e["user_id"] for e in fm.waitlisted_users]


async def _tick_update(env: _Env) -> None:
    """Run a reminder tick and let the update it schedules finish."""
    await env.cog._check_upcoming_formations()
    task = env.cog._pending_update_tasks.get(GUILD_ID)
    if task:
        await task


async def test_a_reaction_the_log_refused_registers_the_member_at_the_tick_after_storage_answers(
    env: _Env, monkeypatch: pytest.MonkeyPatch, storage_error: Exception
) -> None:
    """A refused reaction is logged, stays on Discord, and registers the member with one DM once storage answers."""
    _forget_logged_reactions()
    monkeypatch.setattr(env.cog._reaction_logs, "append", _flaky(env.cog._reaction_logs.append, storage_error))

    await env.cog.on_raw_reaction_event(_reaction_payload())

    assert GUILD_ID not in env.cog._pending_update_tasks
    assert env.registration_dm.await_count == 0
    await _tick_update(env)

    pub = env.cog._get_last_published_in_guild(GUILD_ID)
    assert pub is not None
    assert [entry["user_id"] for entry in pub.message.fms[0].registered_users] == [MEMBER_ID]
    assert env.registration_dm.await_count == 1
    assert len(env.cog._reaction_logs.history(GUILD_ID, MESSAGE_ID)) == 1
    await _tick_update(env)
    assert env.registration_dm.await_count == 1


async def test_a_member_who_left_and_came_back_during_an_outage_queues_behind_the_one_who_took_the_seat(
    started_bot: Any, monkeypatch: pytest.MonkeyPatch, storage_error: Exception
) -> None:
    """A refused re-add is logged at the tick, as of then: the member does not take back the place left."""
    env = _Env(started_bot.get_cog("FormationManagement"), monkeypatch, seats=1, reactors=(MEMBER_ID, OTHER_MEMBER_ID))
    _log_events(
        env,
        (MEMBER_ID, ReactionAction.ADD, 30),
        (MEMBER_ID, ReactionAction.REMOVE, 20),
        (OTHER_MEMBER_ID, ReactionAction.ADD, 10),
    )
    monkeypatch.setattr(env.cog._reaction_logs, "append", _flaky(env.cog._reaction_logs.append, storage_error))

    await env.cog.on_raw_reaction_event(_reaction_payload())
    await _tick_update(env)

    pub = env.cog._get_last_published_in_guild(GUILD_ID)
    assert pub is not None
    assert [entry["user_id"] for entry in pub.message.fms[0].registered_users] == [OTHER_MEMBER_ID]
    assert [entry["user_id"] for entry in pub.message.fms[0].waitlisted_users] == [MEMBER_ID]


async def test_a_reaction_during_a_running_update_gets_its_own_update(env_with_two: _Env) -> None:
    """The reaction is logged after the running update read the history: one more update seats the member, once."""
    env = env_with_two
    sending = asyncio.Event()
    release = asyncio.Event()

    async def _held_dm(*_args: Any, **_kwargs: Any) -> None:
        sending.set()
        await release.wait()

    env.registration_dm.side_effect = _held_dm
    env.cog._schedule_update(GUILD_ID, delay=0)
    running = env.cog._pending_update_tasks[GUILD_ID]
    await sending.wait()

    env.cog._reaction_logs.append(GUILD_ID, _add_event(OTHER_MEMBER_ID))
    env.cog._schedule_update(GUILD_ID, delay=0)
    release.set()
    await running

    pub = env.cog._get_last_published_in_guild(GUILD_ID)
    assert pub is not None
    assert [entry["user_id"] for entry in pub.message.fms[0].registered_users] == [MEMBER_ID, OTHER_MEMBER_ID]
    assert env.registration_dm.await_count == 2
    assert GUILD_ID not in env.cog._pending_update_tasks


async def test_reactions_before_an_update_reads_the_history_share_that_one_update(
    env: _Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A burst of reactions inside the debounce still costs one update."""
    update = AsyncMock()
    monkeypatch.setattr(env.cog, "_update_published_message", update)

    env.cog._schedule_update(GUILD_ID, delay=0)
    env.cog._schedule_update(GUILD_ID, delay=0)
    await env.cog._pending_update_tasks[GUILD_ID]

    update.assert_awaited_once()


async def test_a_reaction_whose_check_could_not_read_is_taken_back_the_same_way(
    env: _Env, monkeypatch: pytest.MonkeyPatch, storage_error: Exception
) -> None:
    """A read that fails in the listener ends the same way as a refused write."""
    _forget_logged_reactions()
    monkeypatch.setattr(formations, "current_published", _flaky(formations.current_published, storage_error))

    await env.cog.on_raw_reaction_event(_reaction_payload())
    await _tick_update(env)

    pub = env.cog._get_last_published_in_guild(GUILD_ID)
    assert pub is not None
    assert [entry["user_id"] for entry in pub.message.fms[0].registered_users] == [MEMBER_ID]


async def test_the_update_retries_at_each_tick_while_storage_refuses_the_taken_back_reaction(
    env: _Env, monkeypatch: pytest.MonkeyPatch, storage_error: Exception
) -> None:
    """While storage stays down nothing is announced, the update comes back at the next tick."""
    _forget_logged_reactions()
    monkeypatch.setattr(env.cog._reaction_logs, "append", _flaky(env.cog._reaction_logs.append, storage_error, times=2))

    await env.cog.on_raw_reaction_event(_reaction_payload())
    await _tick_update(env)
    assert env.registration_dm.await_count == 0
    await _tick_update(env)
    assert env.registration_dm.await_count == 1


async def test_a_reaction_on_discord_the_log_misses_is_left_out_without_a_storage_failure(env: _Env) -> None:
    """Without a failure the log stays the rule: a reaction it does not hold, made while the bot was away, does not count."""
    _forget_logged_reactions()

    await env.cog._update_published_message(GUILD_ID)

    pub = env.cog._get_last_published_in_guild(GUILD_ID)
    assert pub is not None
    assert pub.message.fms[0].registered_users == []
    assert env.cog._reaction_logs.history(GUILD_ID, MESSAGE_ID) == []


async def test_seats_taken_before_the_journal_retention_keep_their_order_and_a_logged_removal_still_removes(
    started_bot: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The journal forgot the old sign-ups: the stored time ranks them, and the one who left in the journal stays out."""
    env = _Env(
        started_bot.get_cog("FormationManagement"),
        monkeypatch,
        seats=1,
        reactors=(MEMBER_ID, OTHER_MEMBER_ID, THIRD_MEMBER_ID),
    )
    waitlist_dm = AsyncMock()
    monkeypatch.setattr(fm_cog, "send_waitlist_dm", waitlist_dm)
    _log_events(env, (THIRD_MEMBER_ID, ReactionAction.REMOVE, 5))
    pub = env.cog._get_last_published_in_guild(GUILD_ID)
    assert pub is not None
    old = datetime.now(PARIS_TZ) - timedelta(days=45)

    def _stored(user_id: int, days_later: int) -> dict[str, Any]:
        return {"user_id": user_id, "username": NAMES[user_id], "ts_iso": (old + timedelta(days=days_later)).isoformat()}

    # The higher ID signed up first: only the stored time, not the ID, puts it ahead.
    pub.message.fms[0].registered_users = [_stored(OTHER_MEMBER_ID, 0)]
    pub.message.fms[0].waitlisted_users = [_stored(MEMBER_ID, 1), _stored(THIRD_MEMBER_ID, 2)]
    env.cog._set_last_published_in_guild(GUILD_ID, pub)

    await env.cog._update_published_message(GUILD_ID)

    assert _seats(env) == ([OTHER_MEMBER_ID], [MEMBER_ID])
    assert env.registration_dm.await_count == 0
    waitlist_dm.assert_not_awaited()


async def test_a_stored_seat_without_a_reaction_on_discord_is_dropped_past_the_retention(
    started_bot: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keeping an old seat needs the reaction to still stand: a member who took theirs off leaves."""
    env = _Env(started_bot.get_cog("FormationManagement"), monkeypatch, seats=2, reactors=(OTHER_MEMBER_ID,))
    _forget_logged_reactions()
    pub = env.cog._get_last_published_in_guild(GUILD_ID)
    assert pub is not None
    old = (datetime.now(PARIS_TZ) - timedelta(days=45)).isoformat()
    pub.message.fms[0].registered_users = [
        {"user_id": MEMBER_ID, "username": "alice", "ts_iso": old},
        {"user_id": OTHER_MEMBER_ID, "username": "bob", "ts_iso": old},
    ]
    env.cog._set_last_published_in_guild(GUILD_ID, pub)

    await env.cog._update_published_message(GUILD_ID)

    assert _seats(env) == ([OTHER_MEMBER_ID], [])


async def test_a_reaction_taken_back_after_the_deadline_stays_out(started_bot: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Storage that answers after the registration deadline leaves the refused reaction out."""
    env = _Env(started_bot.get_cog("FormationManagement"), monkeypatch, start_in=timedelta(minutes=-30))
    _forget_logged_reactions()
    env.cog._stale_guilds.add(GUILD_ID)

    await _tick_update(env)

    pub = env.cog._get_last_published_in_guild(GUILD_ID)
    assert pub is not None
    assert pub.message.fms[0].registered_users == []
    assert env.cog._reaction_logs.history(GUILD_ID, MESSAGE_ID) == []


async def test_a_failed_save_of_an_update_announces_nothing_and_the_tick_redoes_it(
    env: _Env, monkeypatch: pytest.MonkeyPatch, storage_error: Exception
) -> None:
    """The log holds the reaction, the save fails: no edit, no DM, and the next tick finishes the job."""
    monkeypatch.setattr(formations, "save_published", _flaky(formations.save_published, storage_error))

    env.cog._schedule_update(GUILD_ID, delay=0)
    await env.cog._pending_update_tasks[GUILD_ID]

    assert env.registration_dm.await_count == 0
    await _tick_update(env)
    assert env.registration_dm.await_count == 1


async def test_a_refused_reaction_marks_only_its_own_server(
    env: _Env, monkeypatch: pytest.MonkeyPatch, storage_error: Exception
) -> None:
    """The mark names one server: another server's update takes nothing back."""
    monkeypatch.setattr(env.cog._reaction_logs, "append", _flaky(env.cog._reaction_logs.append, storage_error))

    await env.cog.on_raw_reaction_event(_reaction_payload())

    assert env.cog._stale_guilds == {GUILD_ID}


async def test_a_reminder_is_sent_again_when_saving_its_flag_fails(
    env: _Env, monkeypatch: pytest.MonkeyPatch, storage_error: Exception
) -> None:
    """A failed save of the notified flag keeps the state before: the next tick, still in the window, sends it again."""
    monkeypatch.setattr(formations, "save_published", _flaky(formations.save_published, storage_error))

    await env.cog._check_upcoming_formations()
    await env.cog._check_upcoming_formations()

    assert env.notify_participants.await_count == 2
    stored = formations.current_published(GUILD_ID)
    assert stored is not None
    assert stored.message.fms[0].notified_hour_before is True


async def test_a_reaction_taken_back_at_the_tick_is_logged_under_its_own_server(
    env: _Env, monkeypatch: pytest.MonkeyPatch, storage_error: Exception, caplog: pytest.LogCaptureFixture
) -> None:
    """The tick belongs to no server, and the taken-back reaction's line still lands in its server's log."""
    caplog.set_level(logging.DEBUG)
    caplog.handler.addFilter(GuildContextFilter())
    _forget_logged_reactions()
    monkeypatch.setattr(env.cog._reaction_logs, "append", _flaky(env.cog._reaction_logs.append, storage_error))
    await env.cog.on_raw_reaction_event(_reaction_payload())
    caplog.clear()

    await _tick_update(env)

    lines = [r for r in caplog.records if "Logged reaction" in r.getMessage()]
    assert lines
    assert {r.guild_id for r in lines} == {GUILD_ID}  # type: ignore[attr-defined]


async def test_a_member_whose_last_logged_event_is_leaving_is_not_seated_by_an_update(
    started_bot: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reaction on Discord that the log never saw, after a logged removal, does not take the old place back."""
    env = _Env(started_bot.get_cog("FormationManagement"), monkeypatch, seats=1, reactors=(MEMBER_ID, OTHER_MEMBER_ID))
    _log_events(
        env,
        (MEMBER_ID, ReactionAction.ADD, 30),
        (MEMBER_ID, ReactionAction.REMOVE, 20),
        (OTHER_MEMBER_ID, ReactionAction.ADD, 10),
    )

    await env.cog._update_published_message(GUILD_ID)

    assert _seats(env) == ([OTHER_MEMBER_ID], [])


async def test_a_re_add_past_the_deadline_does_not_give_the_old_place_back_at_the_tick(
    started_bot: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pass leaves out a re-add the listener would have refused, and the removal before it still stands."""
    env = _Env(
        started_bot.get_cog("FormationManagement"),
        monkeypatch,
        seats=1,
        start_in=timedelta(minutes=-30),
        reactors=(MEMBER_ID, OTHER_MEMBER_ID),
    )
    _log_events(
        env,
        (MEMBER_ID, ReactionAction.ADD, 60),
        (MEMBER_ID, ReactionAction.REMOVE, 50),
        (OTHER_MEMBER_ID, ReactionAction.ADD, 40),
    )
    env.cog._stale_guilds.add(GUILD_ID)

    await _tick_update(env)

    assert _seats(env) == ([OTHER_MEMBER_ID], [])
    assert len(env.cog._reaction_logs.history(GUILD_ID, MESSAGE_ID)) == 3


async def test_a_member_who_left_and_came_back_in_the_log_keeps_the_new_place_through_the_pass(
    started_bot: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Add, remove, add are all logged: the pass logs nothing again and the place is the last add's."""
    env = _Env(started_bot.get_cog("FormationManagement"), monkeypatch, seats=1, reactors=(MEMBER_ID, OTHER_MEMBER_ID))
    _log_events(
        env,
        (MEMBER_ID, ReactionAction.ADD, 30),
        (MEMBER_ID, ReactionAction.REMOVE, 20),
        (MEMBER_ID, ReactionAction.ADD, 10),
        (OTHER_MEMBER_ID, ReactionAction.ADD, 5),
    )
    env.cog._stale_guilds.add(GUILD_ID)

    await _tick_update(env)

    assert _seats(env) == ([MEMBER_ID], [OTHER_MEMBER_ID])
    assert len(env.cog._reaction_logs.history(GUILD_ID, MESSAGE_ID)) == 4


def _discord_shows(env: _Env, *reactors: int) -> None:
    """Make the published message show the reactions of these members and no others."""
    users = []
    for reactor in reactors:
        user = MagicMock(display_name=NAMES[reactor].title(), bot=False, id=reactor)
        user.name = NAMES[reactor]
        users.append(user)

    async def _users() -> Any:
        for user in users:
            yield user

    env.message.reactions[0].users = _users


async def test_a_removal_past_the_deadline_is_logged_so_that_the_member_does_not_take_the_old_place_back(
    started_bot: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only an add is closed by the deadline: a member who leaves late is out, and their late re-add changes nothing."""
    env = _Env(
        started_bot.get_cog("FormationManagement"),
        monkeypatch,
        seats=1,
        start_in=timedelta(minutes=-30),
        reactors=(OTHER_MEMBER_ID,),
    )
    _log_events(env, (MEMBER_ID, ReactionAction.ADD, 60), (OTHER_MEMBER_ID, ReactionAction.ADD, 50))
    schedule = MagicMock()
    monkeypatch.setattr(env.cog, "_schedule_update", schedule)
    removal = _reaction_payload()
    removal.event_type = "REACTION_REMOVE"

    await env.cog.on_raw_reaction_event(removal)

    assert [event.action for event in env.cog._reaction_logs.history(GUILD_ID, MESSAGE_ID)][-1] == ReactionAction.REMOVE
    schedule.assert_called_once_with(GUILD_ID)
    await env.cog._update_published_message(GUILD_ID)
    assert _seats(env) == ([OTHER_MEMBER_ID], [])

    _discord_shows(env, MEMBER_ID, OTHER_MEMBER_ID)
    await env.cog.on_raw_reaction_event(_reaction_payload())

    assert len(env.cog._reaction_logs.history(GUILD_ID, MESSAGE_ID)) == 3
    schedule.assert_called_once()
    await env.cog._update_published_message(GUILD_ID)
    assert _seats(env) == ([OTHER_MEMBER_ID], [])


async def test_an_update_leaves_the_marks_of_another_server_alone(started_bot: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """The update of one server clears its own marks only, and a mark of another server does not keep it running."""
    cog = started_bot.get_cog("FormationManagement")
    update = AsyncMock()
    monkeypatch.setattr(cog, "_update_published_message", update)
    cog._update_wanted.add(OTHER_GUILD_ID)
    cog._retry_guilds.add(OTHER_GUILD_ID)
    cog._stale_guilds.add(NEW_GUILD_ID)

    cog._schedule_update(GUILD_ID, delay=0)
    await asyncio.wait_for(cog._pending_update_tasks[GUILD_ID], timeout=5)  # a mark of another server must not loop it

    assert update.await_count == 1
    assert cog._update_wanted == {OTHER_GUILD_ID}
    assert cog._retry_guilds == {OTHER_GUILD_ID}
    assert cog._stale_guilds == {NEW_GUILD_ID}


async def test_the_tick_runs_the_update_of_every_marked_server(started_bot: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """A server marked stale and another marked for a retry both get their update at the same tick."""
    cog = started_bot.get_cog("FormationManagement")
    monkeypatch.setattr(cog.bot, "guild_access", GuildAccessPolicy(frozenset({OTHER_GUILD_ID, NEW_GUILD_ID})))
    schedule = MagicMock()
    monkeypatch.setattr(cog, "_schedule_update", schedule)
    cog._stale_guilds.add(NEW_GUILD_ID)
    cog._retry_guilds.add(OTHER_GUILD_ID)

    await cog._check_upcoming_formations()

    assert {call.args[0] for call in schedule.call_args_list} == {NEW_GUILD_ID, OTHER_GUILD_ID}


@pytest.mark.parametrize("reverse_in_log", [False, True], ids=["log-order", "text-order"])
async def test_two_members_in_the_hour_the_clock_repeats_keep_their_real_order(
    started_bot: Any, monkeypatch: pytest.MonkeyPatch, *, reverse_in_log: bool
) -> None:
    """Alice at 02:50+02:00 came 15 minutes before Bob at 02:05+01:00, though the wall clock reads the other way."""
    env = _Env(started_bot.get_cog("FormationManagement"), monkeypatch, seats=1, reactors=(MEMBER_ID, OTHER_MEMBER_ID))
    _forget_logged_reactions()
    events = [(MEMBER_ID, "2026-10-25T02:50:00+02:00"), (OTHER_MEMBER_ID, "2026-10-25T02:05:00+01:00")]
    for user_id, ts_iso in reversed(events) if reverse_in_log else events:
        env.cog._reaction_logs.append(
            GUILD_ID,
            ReactionEvent(
                message_id=MESSAGE_ID,
                user_id=user_id,
                user_name=NAMES[user_id],
                emoji=EMOJI,
                action=ReactionAction.ADD,
                ts_iso=ts_iso,
            ),
        )

    env.cog._schedule_update(GUILD_ID, delay=0)
    await env.cog._pending_update_tasks[GUILD_ID]

    assert _seats(env) == ([MEMBER_ID], [OTHER_MEMBER_ID])


async def test_a_member_who_left_and_came_back_in_the_hour_the_clock_repeats_keeps_the_seat(
    started_bot: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The re-add at 02:05+01:00 comes 15 minutes after the removal at 02:50+02:00, though its text sorts before."""
    env = _Env(started_bot.get_cog("FormationManagement"), monkeypatch)
    monkeypatch.setattr(env.cog._reaction_logs, "_retention", timedelta(days=36500))  # fixed dates must not expire
    _forget_logged_reactions()
    for action, ts_iso in (
        (ReactionAction.ADD, "2026-10-24T20:00:00+02:00"),
        (ReactionAction.REMOVE, "2026-10-25T02:50:00+02:00"),
        (ReactionAction.ADD, "2026-10-25T02:05:00+01:00"),
    ):
        event = ReactionEvent(
            message_id=MESSAGE_ID, user_id=MEMBER_ID, user_name="alice", emoji=EMOJI, action=action, ts_iso=ts_iso
        )
        env.cog._reaction_logs.append(GUILD_ID, event)

    await env.cog._update_published_message(GUILD_ID)

    assert _seats(env) == ([MEMBER_ID], [])


async def test_an_update_that_raises_is_run_again_at_the_next_tick(env: _Env) -> None:
    """The member's reaction is logged, the update stops before saving: the next tick seats the member, once."""
    reaction = env.message.reactions[0]
    reaction.users = _flaky(reaction.users, RuntimeError("Discord refused"))

    env.cog._schedule_update(GUILD_ID, delay=0)
    await env.cog._pending_update_tasks[GUILD_ID]

    assert _seats(env) == ([], [])
    await _tick_update(env)
    assert _seats(env) == ([MEMBER_ID], [])
    assert env.registration_dm.await_count == 1
    await _tick_update(env)
    assert env.message.edit.await_count == 1


async def test_a_reaction_during_an_update_that_then_raises_is_seated_at_the_next_tick(env_with_two: _Env) -> None:
    """The request made during the running update dies with it: the tick still gives the second member a place."""
    env = env_with_two
    sending = asyncio.Event()
    release = asyncio.Event()
    failures = [RuntimeError("DM failed")]

    async def _held_dm_that_fails(*_args: Any, **_kwargs: Any) -> None:
        if failures:
            sending.set()
            await release.wait()
            raise failures.pop()

    env.registration_dm.side_effect = _held_dm_that_fails
    env.cog._schedule_update(GUILD_ID, delay=0)
    running = env.cog._pending_update_tasks[GUILD_ID]
    await sending.wait()
    env.cog._reaction_logs.append(GUILD_ID, _add_event(OTHER_MEMBER_ID))
    env.cog._schedule_update(GUILD_ID, delay=0)
    release.set()
    await running

    assert _seats(env) == ([MEMBER_ID], [])
    await _tick_update(env)
    assert _seats(env) == ([MEMBER_ID, OTHER_MEMBER_ID], [])


async def test_a_published_message_that_cannot_be_fetched_is_tried_again_at_the_next_tick(env: _Env) -> None:
    """A failed read of the post leaves the logged reaction without a place until the tick redoes the update."""
    env.channel.fetch_message = _flaky(env.channel.fetch_message, OSError("connection reset"))

    env.cog._schedule_update(GUILD_ID, delay=0)
    await env.cog._pending_update_tasks[GUILD_ID]

    assert _seats(env) == ([], [])
    await _tick_update(env)
    assert _seats(env) == ([MEMBER_ID], [])
    assert env.registration_dm.await_count == 1


async def test_a_deleted_published_message_is_not_fetched_again(env: _Env) -> None:
    """Nothing is left to update on a deleted post: the tick does not keep asking for it."""
    env.channel.fetch_message = AsyncMock(side_effect=NotFound(MagicMock(status=404, reason="Not Found"), "gone"))

    env.cog._schedule_update(GUILD_ID, delay=0)
    await env.cog._pending_update_tasks[GUILD_ID]
    await _tick_update(env)

    env.channel.fetch_message.assert_awaited_once()


async def test_a_post_the_bot_may_not_read_is_retried_at_the_tick(env: _Env) -> None:
    """An admin can repair a refused read, so the tick keeps asking until the update runs."""
    env.channel.fetch_message = AsyncMock(side_effect=Forbidden(MagicMock(status=403, reason="Forbidden"), "missing access"))

    env.cog._schedule_update(GUILD_ID, delay=0)
    await env.cog._pending_update_tasks[GUILD_ID]
    assert env.cog._retry_guilds == {GUILD_ID}


async def test_a_published_message_in_a_deleted_channel_is_not_tried_again(env: _Env) -> None:
    """A channel the server no longer has holds nothing to update: no retry mark, no error at each tick."""
    env.cog.bot.get_guild(GUILD_ID).get_channel = MagicMock(return_value=None)

    env.cog._schedule_update(GUILD_ID, delay=0)
    await env.cog._pending_update_tasks[GUILD_ID]
    await _tick_update(env)

    assert env.cog._retry_guilds == set()
    assert GUILD_ID not in env.cog._pending_update_tasks


async def test_one_servers_update_neither_holds_back_nor_drops_anothers(
    started_bot: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each server has its own update: a request on one is not merged into the other's, nor forgotten when it ends."""
    cog = started_bot.get_cog("FormationManagement")
    gates = {GUILD_ID: asyncio.Event(), OTHER_GUILD_ID: asyncio.Event()}
    started: list[int] = []

    async def _update(guild_id: int) -> None:
        started.append(guild_id)
        await gates[guild_id].wait()

    monkeypatch.setattr(cog, "_update_published_message", _update)
    cog._schedule_update(GUILD_ID, delay=0)
    await _let_others_run()
    cog._schedule_update(OTHER_GUILD_ID, delay=0)
    await _let_others_run()

    assert started == [GUILD_ID, OTHER_GUILD_ID]
    assert cog._update_wanted == set()
    gates[GUILD_ID].set()
    await _let_others_run()
    assert set(cog._pending_update_tasks) == {OTHER_GUILD_ID}
    gates[OTHER_GUILD_ID].set()
    await _let_others_run()
    assert cog._pending_update_tasks == {}


async def test_a_stale_mark_is_used_and_cleared_by_its_own_server_only(env: _Env) -> None:
    """A stale mark names one server: this server's update takes nothing back for it, and clears no other's."""
    _forget_logged_reactions()
    env.cog._stale_guilds.add(OTHER_GUILD_ID)

    await env.cog._update_published_message(GUILD_ID)
    assert env.cog._reaction_logs.history(GUILD_ID, MESSAGE_ID) == []

    env.cog._stale_guilds.add(GUILD_ID)
    await env.cog._update_published_message(GUILD_ID)
    assert env.cog._stale_guilds == {OTHER_GUILD_ID}


async def test_a_removal_long_after_the_training_promotes_nobody_by_dm(
    started_bot: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A place freed once registration has closed moves the list and tells no one a seat is theirs."""
    env = _Env(
        started_bot.get_cog("FormationManagement"),
        monkeypatch,
        seats=1,
        start_in=timedelta(days=-7),
        reactors=(MEMBER_ID, OTHER_MEMBER_ID),
    )
    _log_events(env, (MEMBER_ID, ReactionAction.ADD, 10140), (OTHER_MEMBER_ID, ReactionAction.ADD, 10130))
    await env.cog._update_published_message(GUILD_ID)
    promotion = AsyncMock()
    monkeypatch.setattr(fm_cog, "send_promotion_dm", promotion)
    _discord_shows(env, OTHER_MEMBER_ID)
    removal = _reaction_payload()
    removal.event_type = "REACTION_REMOVE"

    await env.cog.on_raw_reaction_event(removal)
    await env.cog._pending_update_tasks[GUILD_ID]

    assert _seats(env) == ([OTHER_MEMBER_ID], [])
    promotion.assert_not_awaited()
