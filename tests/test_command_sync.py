"""The bot sends its slash commands to Discord only when they changed since the last sync."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import discord
from discord import app_commands
from discord.ext import commands
import pytest

from fablabot.helpers.command_sync import COMMAND_SYNC_STATE_FILE, command_fingerprint, sync_commands_if_changed
from fablabot.helpers.guild_access import ALLOWED_GUILD_IDS_ENV
from fablabot.helpers.state_store import JsonStateStore

if TYPE_CHECKING:
    from types import ModuleType

APP_ID = 999999999999999999
OTHER_APP_ID = 999999999999999998
LISTED = 111111111111111111
COG_NAMES = (
    "ChannelManagement",
    "FormationManagement",
    "LogManagement",
    "MessageManagement",
    "UserManagement",
    "WelcomeManagement",
)
LOGGER_NAME = "fablabot.helpers.command_sync"


async def _hello(interaction: discord.Interaction) -> None:
    """Command callback without option."""


async def _hello_with_option(interaction: discord.Interaction, value: str) -> None:
    """Command callback with one option."""


async def _other(interaction: discord.Interaction) -> None:
    """Second command callback."""


class _Translator(app_commands.Translator):
    """Translator answering for French only."""

    def __init__(self, word: str) -> None:
        """Remember the word returned for every French string."""
        super().__init__()
        self.word = word

    async def translate(
        self,
        string: app_commands.locale_str,
        locale: discord.Locale,
        context: app_commands.TranslationContext,
    ) -> str | None:
        """Return the word for French, nothing for any other locale."""
        if locale is not discord.Locale.french:
            return None
        return f"{self.word} {context.location.name} {string.message}"


def _command(name: str = "hello", description: str = "Say hello", *, option: bool = False) -> app_commands.Command:
    """Build one slash command."""
    callback = _hello_with_option if option else _hello
    return app_commands.Command(name=name, description=description, callback=callback)


def _tree(*items: app_commands.Command, word: str | None = None) -> app_commands.CommandTree:
    """Build a real tree holding ``items`` (one default command when empty), with a recording ``sync``."""
    client = MagicMock()
    client._connection._command_tree = None
    client._connection._translator = None if word is None else _Translator(word)
    tree = app_commands.CommandTree(client)
    for item in items or (_command(),):
        tree.add_command(item)
    tree.sync = AsyncMock(side_effect=lambda *, guild=None: tree.get_commands(guild=guild))
    return tree


async def _start(tree: app_commands.CommandTree, *, application_id: int = APP_ID) -> bool:
    """Run one start: a fresh store read from disk, then the sync decision."""
    store = JsonStateStore(logging.getLogger("test"), COMMAND_SYNC_STATE_FILE)
    return await sync_commands_if_changed(tree, store, application_id=application_id)


async def test_first_sync_records_fingerprint(caplog: pytest.LogCaptureFixture) -> None:
    """With no record, the commands are synced once for the global scope and the fingerprint is kept."""
    tree = _tree()
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        assert await _start(tree) is True
    tree.sync.assert_awaited_once_with(guild=None)
    assert COMMAND_SYNC_STATE_FILE.exists()
    assert "Commands synced (1, global)." in caplog.messages


async def test_same_commands_skip_sync(caplog: pytest.LogCaptureFixture) -> None:
    """A second start with the same commands sends nothing."""
    await _start(_tree())
    tree = _tree()
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        assert await _start(tree) is False
    tree.sync.assert_not_awaited()
    assert "Commands unchanged, sync skipped (global)." in caplog.messages


@pytest.mark.parametrize(
    "changed",
    [
        pytest.param(lambda: _command(description="Say bye"), id="description"),
        pytest.param(lambda: _command(option=True), id="option"),
        pytest.param(lambda: _command(name="bye"), id="name"),
    ],
)
async def test_changed_definition_syncs_again(changed) -> None:
    """A changed description, option or name syncs again."""
    await _start(_tree())
    tree = _tree(changed())
    assert await _start(tree) is True
    tree.sync.assert_awaited_once_with(guild=None)


async def test_changed_translation_syncs_again() -> None:
    """A changed translation of the same command syncs again."""
    await _start(_tree(word="bonjour"))
    tree = _tree(word="salut")
    assert await _start(tree) is True
    tree.sync.assert_awaited_once()


async def test_load_order_does_not_change_fingerprint() -> None:
    """Adding the same commands in another order gives the same fingerprint."""
    first = _tree(_command(), _command(name="other"))
    second = _tree(_command(name="other"), _command())
    assert await command_fingerprint(first, guild=None, application_id=APP_ID) == await command_fingerprint(
        second, guild=None, application_id=APP_ID
    )


async def test_other_application_syncs_again() -> None:
    """The same commands under another application are synced."""
    await _start(_tree())
    tree = _tree()
    assert await _start(tree, application_id=OTHER_APP_ID) is True
    tree.sync.assert_awaited_once()


async def test_failed_sync_records_nothing() -> None:
    """A failed sync leaves no record, so the next start syncs."""
    tree = _tree()
    tree.sync.side_effect = discord.HTTPException(MagicMock(status=500, reason="boom"), "boom")
    with pytest.raises(discord.HTTPException):
        await _start(tree)
    assert not COMMAND_SYNC_STATE_FILE.exists()
    retry = _tree()
    assert await _start(retry) is True
    retry.sync.assert_awaited_once()


@pytest.mark.parametrize("content", ["not json", "[]", "{}", '{"fingerprint": 3}', '{"fingerprint": "abc"}'])
async def test_doubtful_record_syncs(content: str) -> None:
    """A record that cannot be trusted triggers a sync and is replaced by a clean one."""
    COMMAND_SYNC_STATE_FILE.parent.mkdir(parents=True)
    COMMAND_SYNC_STATE_FILE.write_text(content)
    tree = _tree()
    assert await _start(tree) is True
    tree.sync.assert_awaited_once()
    saved = json.loads(COMMAND_SYNC_STATE_FILE.read_text())
    assert isinstance(saved, dict)
    assert set(saved) == {"fingerprint"}


async def test_record_is_one_small_file() -> None:
    """The record is one file holding a SHA-256 hex digest, with no temporary file left."""
    await _start(_tree())
    assert [path.name for path in Path("data").iterdir()] == ["command_sync_state.json"]
    saved = json.loads(COMMAND_SYNC_STATE_FILE.read_text())
    assert isinstance(saved["fingerprint"], str)
    assert len(saved["fingerprint"]) == 64
    assert set(saved["fingerprint"]) <= set("0123456789abcdef")


async def test_setup_hook_syncs_once_then_skips(fablabot_main: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """The real setup hook syncs on the first start and skips on the second."""
    monkeypatch.setenv(ALLOWED_GUILD_IDS_ENV, str(LISTED))
    monkeypatch.setattr(fablabot_main, "configure_logging", lambda _bot: (None, None))
    for name in COG_NAMES:
        monkeypatch.setattr(fablabot_main, name, type(name, (commands.Cog,), {}))
    monkeypatch.setattr(fablabot_main.Fablabot, "application_id", APP_ID)

    first = fablabot_main.Fablabot()
    first.tree.sync = AsyncMock(return_value=[])
    await first.setup_hook()
    first.tree.sync.assert_awaited_once()

    second = fablabot_main.Fablabot()
    second.tree.sync = AsyncMock(return_value=[])
    await second.setup_hook()
    second.tree.sync.assert_not_awaited()
