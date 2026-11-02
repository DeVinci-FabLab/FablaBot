"""Each server keeps its own log channel; the old instance-wide one moves to its server at start."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from conftest import store_settings, stored_settings
from discord import TextChannel
from discord.ext import commands

from fablabot import db
from fablabot.cogs.log_management import LogManagement
import fablabot.guild_config as guild_config  # noqa: PLR0402 (imported after the cogs: guild_config first is a circular import)

if TYPE_CHECKING:
    from collections.abc import Callable

    import pytest

GUILD_A = 111111111111111111
GUILD_B = 222222222222222222
LEGACY_CHANNEL = 555555555555555555
CHANNEL_B = 666666666666666666
COMMANDS_CHANNEL = 777777777777777777


def _resolver(mapping: dict[int, int]) -> Callable[[int], int | None]:
    """Build a channel-to-guild resolver from ``mapping``."""
    return mapping.get


def test_set_log_channel_changes_only_that_guild() -> None:
    """Setting the channel of one guild leaves the other guild without one."""
    guild_config.set_log_channel_id(GUILD_B, CHANNEL_B)

    assert guild_config.get_log_channel_id(GUILD_B) == CHANNEL_B
    assert guild_config.get_log_channel_id(GUILD_A) is None


def test_guild_without_log_channel_reads_none_even_with_legacy() -> None:
    """The legacy entry never serves as a guild's log channel."""
    store_settings({"__global__": {"log_channel_id": LEGACY_CHANNEL}})

    assert guild_config.get_log_channel_id(GUILD_A) is None
    assert guild_config.get_legacy_log_channel_id() == LEGACY_CHANNEL


def test_commands_channel_change_keeps_log_channel() -> None:
    """Changing the commands channel or the welcome flag keeps the log channel."""
    guild_config.set_log_channel_id(GUILD_A, LEGACY_CHANNEL)
    guild_config.set_commands_channel_id(GUILD_A, COMMANDS_CHANNEL)
    guild_config.set_welcome_verify_enabled(GUILD_A, enabled=True)

    assert stored_settings() == {
        str(GUILD_A): {
            "log_channel_id": LEGACY_CHANNEL,
            "commands_channel_id": COMMANDS_CHANNEL,
            "welcome_verify_enabled": 1,
        },
    }


def test_a_guild_with_no_setting_left_keeps_no_row() -> None:
    """Unsetting the last value forgets the guild."""
    guild_config.set_commands_channel_id(GUILD_A, COMMANDS_CHANNEL)
    guild_config.set_commands_channel_id(GUILD_A, None)

    assert stored_settings() == {}


def test_unsetting_one_setting_keeps_the_others() -> None:
    """Unsetting the commands channel leaves the log channel and the welcome flag where they were."""
    guild_config.set_commands_channel_id(GUILD_A, COMMANDS_CHANNEL)
    guild_config.set_log_channel_id(GUILD_A, LEGACY_CHANNEL)
    guild_config.set_welcome_verify_enabled(GUILD_A, enabled=True)
    guild_config.set_commands_channel_id(GUILD_B, COMMANDS_CHANNEL)

    guild_config.set_commands_channel_id(GUILD_A, None)

    assert stored_settings() == {
        str(GUILD_A): {"log_channel_id": LEGACY_CHANNEL, "welcome_verify_enabled": 1},
        str(GUILD_B): {"commands_channel_id": COMMANDS_CHANNEL},
    }


def test_welcome_off_stays_stored_and_reads_false() -> None:
    """Turning the welcome flow off is a value, not an absence."""
    guild_config.set_welcome_verify_enabled(GUILD_A, enabled=True)
    guild_config.set_welcome_verify_enabled(GUILD_A, enabled=False)

    assert guild_config.is_welcome_verify_enabled(GUILD_A) is False
    assert stored_settings() == {str(GUILD_A): {"welcome_verify_enabled": 0}}


def test_migration_moves_legacy_channel_to_its_guild_and_keeps_legacy() -> None:
    """The legacy channel lands in its guild's entry next to the other keys, and the legacy entry stays."""
    store_settings(
        {
            "__global__": {"log_channel_id": LEGACY_CHANNEL},
            str(GUILD_A): {"commands_channel_id": COMMANDS_CHANNEL, "welcome_verify_enabled": 1},
        },
    )

    assert guild_config.migrate_legacy_log_channel(_resolver({LEGACY_CHANNEL: GUILD_A})) == GUILD_A
    assert stored_settings() == {
        "__global__": {"log_channel_id": LEGACY_CHANNEL},
        str(GUILD_A): {
            "commands_channel_id": COMMANDS_CHANNEL,
            "welcome_verify_enabled": 1,
            "log_channel_id": LEGACY_CHANNEL,
        },
    }


def test_migration_is_idempotent() -> None:
    """A second run changes nothing and writes nothing."""
    store_settings({"__global__": {"log_channel_id": LEGACY_CHANNEL}})
    resolver = _resolver({LEGACY_CHANNEL: GUILD_A})
    guild_config.migrate_legacy_log_channel(resolver)
    written = db.connection().total_changes

    assert guild_config.migrate_legacy_log_channel(resolver) is None
    assert db.connection().total_changes == written


def test_migration_keeps_a_newer_guild_channel() -> None:
    """A channel the guild chose itself is not overwritten by the legacy one."""
    store_settings({"__global__": {"log_channel_id": LEGACY_CHANNEL}, str(GUILD_A): {"log_channel_id": CHANNEL_B}})

    assert guild_config.migrate_legacy_log_channel(_resolver({LEGACY_CHANNEL: GUILD_A})) is None
    assert stored_settings()[str(GUILD_A)] == {"log_channel_id": CHANNEL_B}


def test_migration_channel_not_found_warns_and_writes_nothing(caplog: pytest.LogCaptureFixture) -> None:
    """An unresolvable legacy channel gives a warning with its ID and changes nothing."""
    content = {"__global__": {"log_channel_id": LEGACY_CHANNEL}}
    store_settings(content)

    with caplog.at_level(logging.WARNING):
        assert guild_config.migrate_legacy_log_channel(_resolver({})) is None

    assert f"Legacy log channel not found (channel_id={LEGACY_CHANNEL}): not migrated, kept in place." in caplog.text
    assert stored_settings() == content


def test_migration_without_legacy_entry_does_nothing() -> None:
    """With no legacy entry the resolver is never asked and nothing is written."""
    store_settings({str(GUILD_A): {"commands_channel_id": COMMANDS_CHANNEL}})
    resolver = MagicMock()

    assert guild_config.migrate_legacy_log_channel(resolver) is None
    resolver.assert_not_called()
    assert stored_settings() == {str(GUILD_A): {"commands_channel_id": COMMANDS_CHANNEL}}


async def test_ready_listener_migrates_through_the_bot_cache() -> None:
    """The ready listener resolves the legacy channel's guild from the bot cache."""
    store_settings({"__global__": {"log_channel_id": LEGACY_CHANNEL}})
    channel = MagicMock(spec=TextChannel)
    channel.guild.id = GUILD_A
    bot = MagicMock(spec=commands.Bot)
    bot.get_channel.return_value = channel

    await LogManagement(bot).on_ready()

    bot.get_channel.assert_called_once_with(LEGACY_CHANNEL)
    assert stored_settings()[str(GUILD_A)] == {"log_channel_id": LEGACY_CHANNEL}
