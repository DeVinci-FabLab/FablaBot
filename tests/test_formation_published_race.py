"""The reminder tick and the registration update both rewrite the published message: neither undoes the other."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from discord import TextChannel
import pytest

from fablabot.cogs import formation_management as fm_cog
from fablabot.helpers.constants import PARIS_TZ
from fablabot.models import FmMessageDraft, Formation, PublishedMessage, ReactionAction, ReactionEvent

GUILD_ID = 1234
MESSAGE_ID = 555
EMOJI = "🔧"
MEMBER_ID = 42


class _Env:
    """A published message one hour away, a stub guild, and spies on every notification."""

    def __init__(self, cog: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        self.cog = cog
        self.gate = asyncio.Event()
        self.entered = asyncio.Event()
        self.fetch_gate = asyncio.Event()
        self.fetch_entered = asyncio.Event()

        start = datetime.now(PARIS_TZ) + timedelta(hours=1)
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
