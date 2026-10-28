"""Logging setup keeps the Discord library's debug traces out of the log file."""

from __future__ import annotations

from datetime import datetime
import logging
from pathlib import Path
from unittest.mock import MagicMock

from fablabot.helpers.constants import PARIS_TZ
from fablabot.helpers.logging_setup import configure_logging


def test_configure_logging_drops_discord_debug_traces() -> None:
    """Library DEBUG records stay out of the file while the bot's own DEBUG records stay in."""
    root = logging.getLogger()
    discord_logger = logging.getLogger("discord")
    saved_handlers = list(root.handlers)
    saved_root_level = root.level
    saved_discord_level = discord_logger.level
    discord_logger.setLevel(logging.NOTSET)
    added: list[logging.Handler] = []
    try:
        handlers = configure_logging(MagicMock())
        added.extend(handlers)
        logging.getLogger("discord.http").debug("SECRET-HTTP-BODY")
        logging.getLogger("fablabot.probe").debug("bot-debug-kept")
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
        root.setLevel(saved_root_level)
        discord_logger.setLevel(saved_discord_level)

    assert "SECRET-HTTP-BODY" not in content
    assert "bot-debug-kept" in content
