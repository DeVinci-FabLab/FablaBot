"""Each server's log lines go to its own file, and an export reads only that file."""

from __future__ import annotations

from datetime import date, datetime, timedelta
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from conftest import make_interaction
import pytest

from fablabot.cogs.log_management import LogManagement
from fablabot.helpers.constants import PARIS_TZ
from fablabot.helpers.logging_setup import configure_logging
from fablabot.logging_handlers import DailyFileHandler, GuildContextFilter, guild_log_context

GUILD_A = 111111111111111111
GUILD_B = 222222222222222222


@pytest.fixture
def handler(tmp_path):
    """Real daily file handler under a temporary directory, tagging records with their guild."""
    file_handler = DailyFileHandler(log_dir=str(tmp_path / "logs"), formatter=logging.Formatter("%(message)s"))
    file_handler.addFilter(GuildContextFilter())
    yield file_handler
    file_handler.close()


@pytest.fixture
def probe(handler):
    """Dedicated logger that writes only to the handler under test."""
    probe_logger = logging.getLogger("fablabot.guild_files_probe")
    probe_logger.propagate = False
    probe_logger.setLevel(logging.DEBUG)
    probe_logger.addHandler(handler)
    yield probe_logger
    probe_logger.removeHandler(handler)


def _today() -> date:
    """Return today's date in Paris time."""
    return datetime.now(PARIS_TZ).date()


def test_guild_line_goes_to_common_and_guild_file(handler, probe) -> None:
    """A line logged inside a guild context lands in the shared file and in that guild's file."""
    with guild_log_context(GUILD_A):
        probe.info("hello-a")

    day = _today()
    assert "hello-a" in (handler.log_dir / f"{day}.log").read_text(encoding="utf-8")
    assert "hello-a" in handler.guild_log_path(GUILD_A, day).read_text(encoding="utf-8")


def test_unattributed_line_goes_to_common_file_only(handler, probe) -> None:
    """A line with no guild stays in the shared file and creates no guild folder."""
    probe.info("no-guild")

    assert "no-guild" in (handler.log_dir / f"{_today()}.log").read_text(encoding="utf-8")
    assert not (handler.log_dir / "guilds").exists()


async def test_export_returns_only_the_calling_guild_file(handler, probe) -> None:
    """The export for one guild holds none of another guild's lines."""
    with guild_log_context(GUILD_A):
        probe.info("secret-of-a")
    with guild_log_context(GUILD_B):
        probe.info("line-of-b")

    file = await handler.export_logs(GUILD_B, _today())

    assert file is not None
    content = file.fp.read().decode("utf-8")
    file.close()
    assert "line-of-b" in content
    assert "secret-of-a" not in content
    assert file.filename == f"{_today().isoformat()}.log"


async def test_export_missing_guild_file_returns_none_even_with_common_file(handler, probe) -> None:
    """A guild with no file gets None although the shared file exists."""
    with guild_log_context(GUILD_A):
        probe.info("only-a")

    assert (handler.log_dir / f"{_today()}.log").exists()
    assert await handler.export_logs(GUILD_B, _today()) is None


def test_no_log_file_is_deleted_at_start_or_day_change(tmp_path, probe) -> None:
    """Old shared and per-guild files survive the handler start and a day change."""
    logs = tmp_path / "logs"
    guild_dir = logs / "guilds" / str(GUILD_A)
    guild_dir.mkdir(parents=True)
    old_common = logs / "2020-01-01.log"
    old_guild = guild_dir / "2020-01-01.log"
    old_common.write_text("old\n", encoding="utf-8")
    old_guild.write_text("old\n", encoding="utf-8")

    file_handler = DailyFileHandler(log_dir=str(logs), formatter=logging.Formatter("%(message)s"))
    file_handler.addFilter(GuildContextFilter())
    probe.addHandler(file_handler)
    try:
        file_handler.current_date = _today() - timedelta(days=1)
        with guild_log_context(GUILD_A):
            probe.info("after-day-change")
    finally:
        probe.removeHandler(file_handler)
        file_handler.close()

    assert old_common.exists()
    assert old_guild.exists()


async def test_export_end_to_end_guild_b_never_gets_guild_a_lines() -> None:
    """With the real logging setup and gateway wrapper, guild B's export holds no line of guild A."""
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    discord_logger = logging.getLogger("discord")
    saved_discord_level = discord_logger.level

    def parser(data: dict) -> None:
        """Log the marker of the payload."""
        logging.getLogger("fablabot.probe").info(f"payload {data['marker']}")

    bot = MagicMock()
    bot._connection.parsers = {"INTERACTION_CREATE": parser}  # noqa: SLF001
    added: list[logging.Handler] = []
    try:
        handlers = configure_logging(bot)
        added.extend(handlers)
        file_handler = handlers[1]
        bot._connection.parsers["INTERACTION_CREATE"]({"guild_id": str(GUILD_A), "marker": "secret-of-a"})  # noqa: SLF001
        bot._connection.parsers["INTERACTION_CREATE"]({"guild_id": str(GUILD_B), "marker": "line-of-b"})  # noqa: SLF001

        bot.file_log_handler = file_handler
        cog = LogManagement(bot)
        interaction = make_interaction(guild_id=GUILD_B)
        with patch("fablabot.cogs.log_management.ensure_command_context", AsyncMock(return_value=True)):
            await LogManagement.log_export.callback(cog, interaction, date=None)
        sent = interaction.response.send_message.await_args.kwargs
        assert sent["ephemeral"] is True
        content = sent["file"].fp.read().decode("utf-8")
        sent["file"].close()
    finally:
        for handler in list(root.handlers):
            root.removeHandler(handler)
        for handler in added:
            handler.close()
        for handler in saved_handlers:
            root.addHandler(handler)
        root.setLevel(saved_level)
        discord_logger.setLevel(saved_discord_level)

    assert "line-of-b" in content
    assert "secret-of-a" not in content
    assert Path("logs", "guilds", str(GUILD_A)).exists()
