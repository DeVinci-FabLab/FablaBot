"""Each server keeps its own log channel; the old instance-wide one moves to its server at start."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from discord import TextChannel
from discord.ext import commands
import pytest

from fablabot.cogs.log_management import LogManagement
import fablabot.guild_config as guild_config  # noqa: PLR0402 (imported after the cogs: guild_config first is a circular import)
from fablabot.helpers.state_store import JsonStateStore

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

GUILD_A = 111111111111111111
GUILD_B = 222222222222222222
LEGACY_CHANNEL = 555555555555555555
CHANNEL_B = 666666666666666666
COMMANDS_CHANNEL = 777777777777777777


@pytest.fixture
def config_file(monkeypatch: pytest.MonkeyPatch) -> Callable[[dict | None], Path]:
    """Point the config store at a JSON file under ``data/`` and return a writer for its content."""

    def write(content: dict | None = None) -> Path:
        """Write ``content`` as the config file and reload the store from it."""
        path = guild_config.Path("data/guild_config.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        if content is not None:
            path.write_text(json.dumps(content), encoding="utf-8")
        monkeypatch.setattr(guild_config, "_config_store", JsonStateStore(guild_config.logger, path))
        return path

    return write


def _read(path: Path) -> dict:
    """Read the config file back from disk."""
    return json.loads(path.read_text(encoding="utf-8"))


def _resolver(mapping: dict[int, int]) -> Callable[[int], int | None]:
    """Build a channel-to-guild resolver from ``mapping``."""
    return mapping.get


def test_set_log_channel_changes_only_that_guild(config_file: Callable[[dict | None], Path]) -> None:
    """Setting the channel of one guild leaves the other guild without one."""
    config_file(None)
    guild_config.set_log_channel_id(GUILD_B, CHANNEL_B)

    assert guild_config.get_log_channel_id(GUILD_B) == CHANNEL_B
    assert guild_config.get_log_channel_id(GUILD_A) is None


def test_guild_without_log_channel_reads_none_even_with_legacy(config_file: Callable[[dict | None], Path]) -> None:
    """The legacy entry never serves as a guild's log channel."""
    config_file({"__global__": {"log_channel_id": LEGACY_CHANNEL}})

    assert guild_config.get_log_channel_id(GUILD_A) is None
    assert guild_config.get_legacy_log_channel_id() == LEGACY_CHANNEL


def test_commands_channel_change_keeps_log_channel(config_file: Callable[[dict | None], Path]) -> None:
    """Changing the commands channel or the welcome flag keeps the log channel."""
    path = config_file(None)
    guild_config.set_log_channel_id(GUILD_A, LEGACY_CHANNEL)
    guild_config.set_commands_channel_id(GUILD_A, COMMANDS_CHANNEL)
    guild_config.set_welcome_verify_enabled(GUILD_A, enabled=True)

    assert _read(path) == {
        str(GUILD_A): {
            "log_channel_id": LEGACY_CHANNEL,
            "commands_channel_id": COMMANDS_CHANNEL,
            "welcome_verify_enabled": True,
        },
    }


def test_migration_moves_legacy_channel_to_its_guild_and_keeps_legacy(config_file: Callable[[dict | None], Path]) -> None:
    """The legacy channel lands in its guild's entry next to the other keys, and the legacy entry stays."""
    path = config_file(
        {
            "__global__": {"log_channel_id": LEGACY_CHANNEL},
            str(GUILD_A): {"commands_channel_id": COMMANDS_CHANNEL, "welcome_verify_enabled": True},
        },
    )

    assert guild_config.migrate_legacy_log_channel(_resolver({LEGACY_CHANNEL: GUILD_A})) == GUILD_A
    assert _read(path) == {
        "__global__": {"log_channel_id": LEGACY_CHANNEL},
        str(GUILD_A): {
            "commands_channel_id": COMMANDS_CHANNEL,
            "welcome_verify_enabled": True,
            "log_channel_id": LEGACY_CHANNEL,
        },
    }


def test_migration_is_idempotent(config_file: Callable[[dict | None], Path], monkeypatch: pytest.MonkeyPatch) -> None:
    """A second run changes nothing and does not write the file."""
    config_file({"__global__": {"log_channel_id": LEGACY_CHANNEL}})
    resolver = _resolver({LEGACY_CHANNEL: GUILD_A})
    guild_config.migrate_legacy_log_channel(resolver)

    save = MagicMock()
    monkeypatch.setattr(guild_config._config_store, "save", save)

    assert guild_config.migrate_legacy_log_channel(resolver) is None
    save.assert_not_called()


def test_migration_keeps_a_newer_guild_channel(config_file: Callable[[dict | None], Path]) -> None:
    """A channel the guild chose itself is not overwritten by the legacy one."""
    path = config_file(
        {"__global__": {"log_channel_id": LEGACY_CHANNEL}, str(GUILD_A): {"log_channel_id": CHANNEL_B}},
    )

    assert guild_config.migrate_legacy_log_channel(_resolver({LEGACY_CHANNEL: GUILD_A})) is None
    assert _read(path)[str(GUILD_A)] == {"log_channel_id": CHANNEL_B}


def test_migration_channel_not_found_warns_and_writes_nothing(
    config_file: Callable[[dict | None], Path],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An unresolvable legacy channel gives a warning with its ID and leaves the file alone."""
    content = {"__global__": {"log_channel_id": LEGACY_CHANNEL}}
    path = config_file(content)

    with caplog.at_level(logging.WARNING):
        assert guild_config.migrate_legacy_log_channel(_resolver({})) is None

    assert f"Legacy log channel not found (channel_id={LEGACY_CHANNEL}): not migrated, kept in place." in caplog.text
    assert _read(path) == content


def test_migration_without_legacy_entry_does_nothing(config_file: Callable[[dict | None], Path]) -> None:
    """With no legacy entry the resolver is never asked and nothing is written."""
    path = config_file({str(GUILD_A): {"commands_channel_id": COMMANDS_CHANNEL}})
    resolver = MagicMock()

    assert guild_config.migrate_legacy_log_channel(resolver) is None
    resolver.assert_not_called()
    assert _read(path) == {str(GUILD_A): {"commands_channel_id": COMMANDS_CHANNEL}}


async def test_ready_listener_migrates_through_the_bot_cache(config_file: Callable[[dict | None], Path]) -> None:
    """The ready listener resolves the legacy channel's guild from the bot cache."""
    path = config_file({"__global__": {"log_channel_id": LEGACY_CHANNEL}})
    channel = MagicMock(spec=TextChannel)
    channel.guild.id = GUILD_A
    bot = MagicMock(spec=commands.Bot)
    bot.get_channel.return_value = channel

    await LogManagement(bot).on_ready()

    bot.get_channel.assert_called_once_with(LEGACY_CHANNEL)
    assert _read(path)[str(GUILD_A)] == {"log_channel_id": LEGACY_CHANNEL}
