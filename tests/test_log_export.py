"""Tests for the ``/log export`` and ``/log help`` commands."""

from __future__ import annotations

from datetime import date
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fablabot.cogs.log_management import LogManagement
from fablabot.logging_handlers import DailyFileHandler
from conftest import make_interaction


@pytest.fixture
def handler(tmp_path):  # noqa: ANN001, ANN201
    """Real daily file handler writing under a temporary directory."""
    file_handler = DailyFileHandler(log_dir=str(tmp_path / "logs"), formatter=logging.Formatter())
    yield file_handler
    file_handler.close()


@pytest.fixture
def cog(handler):  # noqa: ANN001, ANN201
    """Log cog whose bot carries the real file handler."""
    bot = MagicMock()
    bot.file_log_handler = handler
    return LogManagement(bot)


@pytest.fixture(autouse=True)
def _allow_command():  # noqa: ANN202
    """Let every caller through the role check."""
    with patch("fablabot.cogs.log_management.ensure_command_context", AsyncMock(return_value=True)):
        yield


async def test_log_export_sends_file_ephemeral(cog, handler) -> None:  # noqa: ANN001
    path = handler.guild_log_path(1234, date(2026, 10, 27))
    path.parent.mkdir(parents=True)
    path.write_text("line\n", encoding="utf-8")
    interaction = make_interaction()

    await LogManagement.log_export.callback(cog, interaction, date="27/10/2026")

    interaction.response.send_message.assert_awaited_once()
    kwargs = interaction.response.send_message.await_args.kwargs
    assert "file" in kwargs
    assert kwargs["ephemeral"] is True


async def test_log_export_missing_file_is_ephemeral(cog) -> None:  # noqa: ANN001
    interaction = make_interaction()

    await LogManagement.log_export.callback(cog, interaction, date="01/01/2020")

    interaction.response.send_message.assert_awaited_once()
    args = interaction.response.send_message.await_args
    assert args.kwargs["ephemeral"] is True
    assert "Aucun log de ce serveur" in args.args[0]


async def test_log_export_does_not_log_the_typed_date(cog, caplog) -> None:
    """The typed date reaches neither the command context details nor the log records."""
    interaction = make_interaction()
    typed = "27/10/2026\nforged"

    with (
        caplog.at_level(logging.DEBUG),
        patch("fablabot.cogs.log_management.ensure_command_context", AsyncMock(return_value=True)) as ensure,
    ):
        await LogManagement.log_export.callback(cog, interaction, date=typed)

    assert "27/10/2026" not in str(ensure.await_args.kwargs.get("log_details"))
    assert typed not in caplog.text


@pytest.mark.parametrize("bad", ["31/02/2026", "2026-10-27", "hier", "27/10"])
async def test_log_export_rejects_bad_date(cog, caplog, bad) -> None:  # noqa: ANN001
    interaction = make_interaction()

    with caplog.at_level(logging.DEBUG):
        await LogManagement.log_export.callback(cog, interaction, date=bad)

    interaction.response.send_message.assert_awaited_once()
    args = interaction.response.send_message.await_args
    assert args.kwargs["ephemeral"] is True
    assert "file" not in args.kwargs
    assert "JJ/MM/AAAA" in args.args[0]
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


async def test_log_help_shows_accepted_date_format(cog) -> None:  # noqa: ANN001
    interaction = make_interaction()

    await LogManagement.log_help.callback(cog, interaction)

    text = interaction.response.send_message.await_args.args[0]
    assert "JJ/MM/AAAA" in text
    assert "YYYY-MM-DD" not in text
