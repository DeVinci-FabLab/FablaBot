"""Each error is posted in its own server's log channel; errors with no server go to the operator channel."""

from __future__ import annotations

from datetime import datetime
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from discord import AllowedMentions, HTTPException, TextChannel
import pytest

from fablabot.cogs.formation_management import FormationManagement
import fablabot.guild_config as guild_config  # noqa: PLR0402 (imported after the cogs: guild_config first is a circular import)
from fablabot.helpers import log_channels
from fablabot.helpers.constants import PARIS_TZ
from fablabot.helpers.logging_setup import configure_logging
from fablabot.helpers.state_store import JsonStateStore
from fablabot.logging_handlers import DiscordLogHandler, GuildContextFilter, current_guild_id, guild_log_context

GUILD_A = 111111111111111111
GUILD_B = 222222222222222222
CHANNEL_A = 555555555555555555
CHANNEL_B = 666666666666666666
OPERATOR = 888888888888888888
WRAPPING_FENCES = 2


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start from an empty config store, no operator channel and a cleared cache."""
    path = Path("data/guild_config.json")
    monkeypatch.setattr(guild_config, "_config_store", JsonStateStore(guild_config.logger, path))
    monkeypatch.delenv(log_channels.OPERATOR_LOG_CHANNEL_ENV, raising=False)
    log_channels.operator_log_channel_id.cache_clear()
    yield
    log_channels.operator_log_channel_id.cache_clear()


def _channel(channel_id: int, guild_id: int) -> MagicMock:
    """Build a text channel stub of a guild whose ``send`` is awaitable."""
    channel = MagicMock(spec=TextChannel)
    channel.id = channel_id
    channel.guild.id = guild_id
    channel.send = AsyncMock()
    return channel


def _bot(*channels: MagicMock) -> MagicMock:
    """Build a bot stub whose ``get_channel`` maps IDs to the given channel stubs."""
    bot = MagicMock()
    by_id = {channel.id: channel for channel in channels}
    bot.get_channel.side_effect = by_id.get
    return bot


def _record(guild_id: int | None, message: str = "boom") -> logging.LogRecord:
    """Build an ERROR record tied to a guild."""
    record = logging.LogRecord("fablabot.probe", logging.ERROR, __file__, 1, message, None, None)
    record.guild_id = guild_id
    return record


async def test_guild_error_goes_to_its_own_log_channel() -> None:
    """An error of a guild is posted in that guild's log channel."""
    guild_config.set_log_channel_id(GUILD_A, CHANNEL_A)
    channel = _channel(CHANNEL_A, GUILD_A)
    handler = DiscordLogHandler(_bot(channel))
    await handler._send(GUILD_A, "boom")  # noqa: SLF001
    channel.send.assert_awaited_once()


async def test_guild_error_without_log_channel_is_not_posted() -> None:
    """A guild with no log channel gets nothing posted anywhere."""
    channel = _channel(CHANNEL_A, GUILD_A)
    handler = DiscordLogHandler(_bot(channel))
    await handler._send(GUILD_B, "boom")  # noqa: SLF001
    channel.send.assert_not_awaited()


async def test_guild_error_never_posted_to_a_channel_of_another_guild() -> None:
    """A hand-edited config pointing at another guild's channel posts nothing."""
    guild_config.set_log_channel_id(GUILD_B, CHANNEL_A)
    channel = _channel(CHANNEL_A, GUILD_A)
    handler = DiscordLogHandler(_bot(channel))
    await handler._send(GUILD_B, "boom")  # noqa: SLF001
    channel.send.assert_not_awaited()


async def test_guild_error_never_goes_to_operator_channel() -> None:
    """An error of a guild with no log channel stays out of the operator channel."""
    operator = _channel(OPERATOR, GUILD_B)
    handler = DiscordLogHandler(_bot(operator), operator_channel_id=OPERATOR)
    await handler._send(GUILD_B, "boom")  # noqa: SLF001
    operator.send.assert_not_awaited()


async def test_guildless_error_goes_to_operator_channel() -> None:
    """An error tied to no guild is posted in the operator channel."""
    operator = _channel(OPERATOR, GUILD_A)
    handler = DiscordLogHandler(_bot(operator), operator_channel_id=OPERATOR)
    await handler._send(None, "boom")  # noqa: SLF001
    operator.send.assert_awaited_once()


async def test_guildless_error_without_operator_is_not_posted() -> None:
    """With no operator channel, an error tied to no guild is not posted."""
    guild_config.set_log_channel_id(GUILD_A, CHANNEL_A)
    channel = _channel(CHANNEL_A, GUILD_A)
    handler = DiscordLogHandler(_bot(channel))
    await handler._send(None, "boom")  # noqa: SLF001
    channel.send.assert_not_awaited()


async def test_posts_without_mentions_and_neutralises_code_fences() -> None:
    """A posted line mentions no one and cannot close its code block."""
    guild_config.set_log_channel_id(GUILD_A, CHANNEL_A)
    channel = _channel(CHANNEL_A, GUILD_A)
    handler = DiscordLogHandler(_bot(channel))
    await handler._send(GUILD_A, "a ```evil``` @everyone")  # noqa: SLF001
    args, kwargs = channel.send.await_args
    mentions = kwargs["allowed_mentions"]
    assert isinstance(mentions, AllowedMentions)
    assert (mentions.everyone, mentions.users, mentions.roles) == (False, False, False)
    assert args[0].count("```") == WRAPPING_FENCES


@pytest.mark.parametrize("run", ["```", "````", "`````", "``````", "```````"])
async def test_runs_of_backticks_never_close_the_code_block(run: str) -> None:
    """Any run of backticks, however long, leaves only the two wrapping fences."""
    guild_config.set_log_channel_id(GUILD_A, CHANNEL_A)
    channel = _channel(CHANNEL_A, GUILD_A)
    handler = DiscordLogHandler(_bot(channel))
    await handler._send(GUILD_A, f"x{run}[click](https://evil.example) # Heading")  # noqa: SLF001
    args, _ = channel.send.await_args
    assert args[0].count("```") == WRAPPING_FENCES


async def test_send_failure_logs_a_warning_and_never_reposts(caplog: pytest.LogCaptureFixture) -> None:
    """A refused post is logged once as a warning, never as an error."""
    guild_config.set_log_channel_id(GUILD_A, CHANNEL_A)
    channel = _channel(CHANNEL_A, GUILD_A)
    channel.send.side_effect = HTTPException(MagicMock(status=403), "x")
    handler = DiscordLogHandler(_bot(channel))
    with caplog.at_level(logging.DEBUG):
        await handler._send(GUILD_A, "boom")  # noqa: SLF001
    assert channel.send.await_count == 1
    assert [r.levelno for r in caplog.records] == [logging.WARNING]
    assert caplog.records[0].getMessage() == f"Could not post a log line (guild_id={GUILD_A}, channel_id={CHANNEL_A}): HTTP 403"


def test_send_task_runs_outside_the_record_guild() -> None:
    """The send task starts from a blank context, so its own lines belong to no guild."""
    bot = MagicMock()
    bot.loop.create_task = MagicMock()
    handler = DiscordLogHandler(bot)
    with guild_log_context(GUILD_A):
        handler.emit(_record(GUILD_A))
    context = bot.loop.create_task.call_args.kwargs["context"]
    assert context.get(current_guild_id) is None
    bot.loop.create_task.call_args.args[0].close()


def test_configure_logging_tags_file_lines_and_keeps_one_line_per_entry() -> None:
    """A line logged while a gateway payload is parsed carries its guild ID, on one line."""
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    discord_logger = logging.getLogger("discord")
    saved_discord_level = discord_logger.level

    def parser(_data: object) -> None:
        """Log a two-line message."""
        logging.getLogger("fablabot.probe").info("x\ny")

    bot = MagicMock()
    bot._connection.parsers = {"INTERACTION_CREATE": parser}  # noqa: SLF001
    added: list[logging.Handler] = []
    try:
        handlers = configure_logging(bot)
        added.extend(handlers)
        bot._connection.parsers["INTERACTION_CREATE"]({"guild_id": str(GUILD_A)})  # noqa: SLF001
        for handler in handlers:
            handler.flush()
        content = (Path("logs") / f"{datetime.now(PARIS_TZ).date()}.log").read_text(encoding="utf-8")
    finally:
        for handler in list(root.handlers):
            root.removeHandler(handler)
        for handler in added:
            handler.close()
        for handler in saved_handlers:
            root.addHandler(handler)
        root.setLevel(saved_level)
        discord_logger.setLevel(saved_discord_level)

    lines = [line for line in content.splitlines() if "x\\ny" in line]
    assert len(lines) == 1
    assert f"guild_id={GUILD_A}" in lines[0]


def test_configure_logging_turns_operator_off_without_attribution(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the parser table cannot be wrapped, the operator channel stays off and an error says so."""
    monkeypatch.setenv(log_channels.OPERATOR_LOG_CHANNEL_ENV, str(OPERATOR))
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    discord_logger = logging.getLogger("discord")
    saved_discord_level = discord_logger.level
    bot = MagicMock()
    bot._connection.parsers = {}  # noqa: SLF001
    added: list[logging.Handler] = []
    try:
        handlers = configure_logging(bot)
        added.extend(handlers)
        operator_channel_id = handlers[0].operator_channel_id
        for handler in handlers:
            handler.flush()
        content = (Path("logs") / f"{datetime.now(PARIS_TZ).date()}.log").read_text(encoding="utf-8")
    finally:
        for handler in list(root.handlers):
            root.removeHandler(handler)
        for handler in added:
            handler.close()
        for handler in saved_handlers:
            root.addHandler(handler)
        root.setLevel(saved_level)
        discord_logger.setLevel(saved_discord_level)

    assert operator_channel_id is None
    assert "ERROR" in content
    assert "Log lines cannot be tied to their server" in content


async def test_formation_loop_tags_each_guild_turn() -> None:
    """Each guild's turn of the reminder loop runs inside that guild's log context."""
    bot = MagicMock()
    bot.get_guild.side_effect = lambda guild_id: MagicMock(id=guild_id)
    cog = FormationManagement(bot)
    cog._check_upcoming_formations.cancel()  # noqa: SLF001
    cog._state_store.state.clear()  # noqa: SLF001
    cog._state_store.state.update({str(GUILD_A): {}, str(GUILD_B): {}})  # noqa: SLF001

    def failing(_guild_id: int) -> None:
        """Log an error from inside the guild's turn."""
        logging.getLogger("fablabot.cogs.formation_management").error("loop boom")

    seen: list[int | None] = []

    class Capture(logging.Handler):
        """Collect the guild ID of each record."""

        def emit(self, record: logging.LogRecord) -> None:
            """Remember the record's guild ID."""
            seen.append(record.guild_id)

    capture = Capture()
    capture.addFilter(GuildContextFilter())
    target = logging.getLogger("fablabot.cogs.formation_management")
    target.addHandler(capture)
    try:
        with patch.object(cog, "_get_last_published_in_guild", side_effect=lambda gid: failing(gid)):
            await cog._check_upcoming_formations.coro(cog)  # noqa: SLF001
    finally:
        target.removeHandler(capture)
    assert seen == [GUILD_A, GUILD_B]
