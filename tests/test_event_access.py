"""The allow-list covers events and background tasks: a server the policy refuses gets no write, no message, no DM."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from discord import Guild, Member, Message, TextChannel, VoiceChannel
import pytest

from fablabot.cogs import message_management, welcome_management
from fablabot.cogs.channel_management import DYNAMIC_SUFFIX, ChannelManagement
from fablabot.cogs.formation_management import FormationManagement
from fablabot.cogs.message_management import MessageManagement
from fablabot.cogs.user_management import UserManagement
from fablabot.cogs.welcome_management import WelcomeManagement
from fablabot.helpers.constants import RoleNames
from fablabot.helpers.guild_access import GuildAccessPolicy
from fablabot.services import formations

LISTED = 111111111111111111
UNLISTED = 222222222222222222
CHANNEL_ID = 555
MESSAGE_ID = 666
USER_ID = 777

# One case per server: the served one proves the guard leaves the behaviour alone, the other that it stops it.
BY_SERVER = pytest.mark.parametrize(("guild_id", "acts"), [(LISTED, True), (UNLISTED, False)], ids=["served", "unserved"])


def _bot() -> MagicMock:
    """Build a stub bot carrying a real policy that lists ``LISTED`` only."""
    bot = MagicMock()
    bot.guild_access = GuildAccessPolicy(frozenset({LISTED}))
    return bot


def _expect(mock: MagicMock | AsyncMock, *, acts: bool) -> None:
    """Assert that ``mock`` ran when ``acts``, and never otherwise."""
    assert mock.called is acts


# region ====== Messages and reactions ======


@BY_SERVER
async def test_easter_egg_replies_only_on_a_served_server(
    monkeypatch: pytest.MonkeyPatch, guild_id: int, *, acts: bool
) -> None:
    """The easter egg answers a message on a served server and stays silent on any other."""
    monkeypatch.setattr(message_management, "EASTER_EGGS_ENABLED", True)
    monkeypatch.setattr(message_management.random, "random", lambda: 0.0)
    msg = MagicMock(spec=Message)
    msg.content = "ce n'est pas sorcier"
    msg.author = MagicMock(spec=Member)
    msg.author.bot = False
    msg.guild = MagicMock(spec=Guild)
    msg.guild.id = guild_id
    msg.channel = MagicMock(spec=TextChannel)
    msg.channel.category.name = "General"
    msg.channel.send = AsyncMock()
    msg.add_reaction = AsyncMock()

    await MessageManagement(_bot()).on_message(msg)

    _expect(msg.channel.send, acts=acts)


def _payload(guild_id: int, event_type: str = "REACTION_ADD") -> MagicMock:
    """Build a reaction payload from a human on a message."""
    payload = MagicMock()
    payload.guild_id = guild_id
    payload.message_id = MESSAGE_ID
    payload.user_id = USER_ID
    payload.emoji = "🔧"
    payload.event_type = event_type
    payload.member = MagicMock(spec=Member)
    payload.member.bot = False
    payload.member.id = USER_ID
    payload.member.name = "alice"
    payload.member.display_name = "Alice"
    return payload


@BY_SERVER
async def test_tracked_message_reaction_acts_and_logs_only_on_a_served_server(guild_id: int, *, acts: bool) -> None:
    """A reaction on a tracked message runs its action and is written to the history only on a served server."""
    bot = _bot()
    bot.get_guild.return_value = MagicMock(spec=Guild, id=guild_id)
    cog = MessageManagement(bot)
    tracked = MagicMock()
    tracked.reactions = [MagicMock(emoji="🔧")]
    cog._reaction_logs = MagicMock()
    run_action = AsyncMock()

    with (
        patch.object(cog, "get_tracked_message", return_value=tracked),
        patch.object(cog, "_execute_reaction_action", run_action),
    ):
        await cog.on_raw_reaction_add(_payload(guild_id))

    _expect(run_action, acts=acts)
    _expect(cog._reaction_logs.append, acts=acts)


@pytest.mark.parametrize("event_type", ["REACTION_ADD", "REACTION_REMOVE"])
@BY_SERVER
async def test_formation_reaction_is_recorded_only_on_a_served_server(guild_id: int, event_type: str, *, acts: bool) -> None:
    """A reaction on a formation post is recorded and scheduled for an update only on a served server."""
    cog = FormationManagement(_bot())
    cog._check_upcoming_formations.cancel()
    cog._reaction_logs = MagicMock()
    schedule = MagicMock()

    with (
        patch.object(cog, "_reaction_counts", return_value=True),
        patch.object(cog, "_schedule_update", schedule),
    ):
        await cog.on_raw_reaction_event(_payload(guild_id, event_type))

    _expect(cog._reaction_logs.append, acts=acts)
    _expect(schedule, acts=acts)


# endregion Messages and reactions

# region ====== Member join ======


@BY_SERVER
async def test_welcome_channel_is_created_only_on_a_served_server(
    monkeypatch: pytest.MonkeyPatch, guild_id: int, *, acts: bool
) -> None:
    """A new member gets a welcome channel and its message only on a served server."""
    monkeypatch.setattr(welcome_management, "is_welcome_verify_enabled", lambda _guild_id: True)
    channel = MagicMock(spec=TextChannel)
    channel.send = AsyncMock()
    create = AsyncMock(return_value=(channel, None))
    monkeypatch.setattr(welcome_management, "safe_create_text_channel", create)
    category, codir = MagicMock(), MagicMock()
    category.name = "Validation"
    codir.name = RoleNames.CODIR
    member = MagicMock(spec=Member)
    member.name = "alice"
    member.guild = MagicMock(spec=Guild)
    member.guild.id = guild_id
    member.guild.categories = [category]
    member.guild.roles = [codir]

    await WelcomeManagement(_bot()).on_member_join(member)

    _expect(create, acts=acts)
    _expect(channel.send, acts=acts)


# endregion Member join

# region ====== Channel events ======


def _text_channel() -> MagicMock:
    """Build a text channel the deletion notices apply to."""
    channel = MagicMock(spec=TextChannel)
    channel.name = "commandes_bot"
    channel.id = CHANNEL_ID
    channel.send = AsyncMock()
    return channel


def _audit_entry() -> MagicMock:
    """Build the audit entry of a deletion done by a member."""
    entry = MagicMock()
    entry.user = MagicMock(spec=Member)
    entry.user.id = USER_ID
    entry.user.mention = f"<@{USER_ID}>"
    return entry


def _audit_logs(entry: MagicMock):
    async def audit_logs(**_kwargs: object):
        yield entry

    return audit_logs


@BY_SERVER
async def test_deleted_bot_message_is_reported_only_on_a_served_server(guild_id: int, *, acts: bool) -> None:
    """The notice of a deleted bot message reaches the channel only on a served server."""
    channel = _text_channel()
    msg = MagicMock()
    msg.author.bot = True
    msg.channel = channel
    msg.guild.id = guild_id
    msg.guild.audit_logs = _audit_logs(_audit_entry())

    with patch.object(ChannelManagement, "_get_codir_mention", AsyncMock(return_value="")):
        await ChannelManagement(_bot()).on_message_delete(msg)

    _expect(channel.send, acts=acts)


@BY_SERVER
async def test_uncached_message_deletion_is_reported_only_on_a_served_server(guild_id: int, *, acts: bool) -> None:
    """The notice of an uncached deletion reaches the channel only on a served server."""
    channel = _text_channel()
    guild = MagicMock(spec=Guild)
    guild.id = guild_id
    guild.audit_logs = _audit_logs(_audit_entry())
    bot = _bot()
    bot.get_guild.return_value = guild
    bot.get_channel.return_value = channel
    payload = MagicMock()
    payload.cached_message = None
    payload.guild_id = guild_id
    payload.channel_id = CHANNEL_ID
    payload.message_id = MESSAGE_ID

    with patch.object(ChannelManagement, "_get_codir_mention", AsyncMock(return_value="")):
        await ChannelManagement(bot).on_raw_message_delete(payload)

    _expect(channel.send, acts=acts)


@BY_SERVER
async def test_voice_channels_are_managed_only_on_a_served_server(guild_id: int, *, acts: bool) -> None:
    """A voice state change creates or removes dynamic voice channels only on a served server."""
    base = MagicMock(spec=VoiceChannel)
    base.name = f"Salle{DYNAMIC_SUFFIX}"
    category = MagicMock()
    category.voice_channels = [base]
    member = MagicMock(spec=Member)
    member.id = USER_ID
    member.guild = MagicMock(spec=Guild)
    member.guild.id = guild_id
    member.guild.categories = [category]
    before, after = MagicMock(), MagicMock()
    after.channel = None
    manage = AsyncMock()

    with patch.object(ChannelManagement, "_manage_voice_channels", manage):
        await ChannelManagement(_bot()).on_voice_state_update(member, before, after)

    _expect(manage, acts=acts)


# endregion Channel events

# region ====== Background tasks ======


async def test_reminder_tick_skips_a_server_the_policy_refuses() -> None:
    """The tick sends reminders and retries updates for a served server only."""
    cog = FormationManagement(_bot())
    cog._check_upcoming_formations.cancel()
    cog._stale_guilds = {LISTED, UNLISTED}
    cog._retry_guilds = set()
    schedule = MagicMock()
    remind = AsyncMock()

    with (
        patch.object(formations, "guilds_with_published", return_value=[LISTED, UNLISTED]),
        patch.object(cog, "_schedule_update", schedule),
        patch.object(cog, "_send_due_reminders", remind),
    ):
        await cog._check_upcoming_formations.coro(cog)

    assert [call.args[0] for call in schedule.call_args_list] == [LISTED]
    assert [call.args[0] for call in remind.call_args_list] == [LISTED]


async def test_op_settling_skips_a_server_the_policy_refuses() -> None:
    """The settling round reconciles a served server only, and leaves the temporary role of any other."""
    served, unserved = MagicMock(spec=Guild), MagicMock(spec=Guild)
    served.id, unserved.id = LISTED, UNLISTED
    served.unavailable = unserved.unavailable = False
    bot = _bot()
    bot.guilds = [served, unserved]
    cog = UserManagement(bot)
    reconcile = AsyncMock()

    with patch.object(cog, "_reconcile_guild", reconcile):
        await cog._settle_ops.coro(cog)

    assert [call.args[0] for call in reconcile.call_args_list] == [served]


# endregion Background tasks
