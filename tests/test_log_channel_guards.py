"""``/log set`` is per server, and the log channels are protected from ``/msg clear`` and deletions."""

from __future__ import annotations

import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

from conftest import make_interaction
from discord import Permissions, TextChannel
import pytest

from fablabot.cogs.channel_management import ChannelManagement
from fablabot.cogs.log_management import LogManagement
from fablabot.cogs.message_management import MessageManagement
import fablabot.guild_config as guild_config  # noqa: PLR0402 (imported after the cogs: guild_config first is a circular import)
from fablabot.helpers import log_channels
from fablabot.helpers.state_store import JsonStateStore

GUILD_A = 111111111111111111
GUILD_B = 222222222222222222
CHANNEL_A = 555555555555555555
CHANNEL_B = 666666666666666666
OPERATOR = 888888888888888888
USER_NAME = "kevin_secret_name"


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start from an empty config store, no operator channel and a cleared cache."""
    path = guild_config.Path("data/guild_config.json")
    monkeypatch.setattr(guild_config, "_config_store", JsonStateStore(guild_config.logger, path))
    monkeypatch.delenv(log_channels.OPERATOR_LOG_CHANNEL_ENV, raising=False)
    log_channels.operator_log_channel_id.cache_clear()
    yield
    log_channels.operator_log_channel_id.cache_clear()


def _file() -> dict:
    """Read the config file back from disk."""
    return json.loads(guild_config.Path("data/guild_config.json").read_text(encoding="utf-8"))


def _text_channel(channel_id: int, guild_id: int, name: str = "salon") -> MagicMock:
    """Build a stub text channel of ``guild_id``."""
    channel = MagicMock(spec=TextChannel)
    channel.id = channel_id
    channel.name = name
    channel.guild.id = guild_id
    channel.mention = f"<#{channel_id}>"
    return channel


def _set_operator(monkeypatch: pytest.MonkeyPatch, value: int) -> None:
    """Declare ``value`` as the operator channel."""
    monkeypatch.setenv(log_channels.OPERATOR_LOG_CHANNEL_ENV, str(value))
    log_channels.operator_log_channel_id.cache_clear()


async def test_log_set_in_b_changes_only_b() -> None:
    """``/log set`` from server B writes B's entry and leaves A's."""
    guild_config.set_log_channel_id(GUILD_A, CHANNEL_A)
    interaction = make_interaction(guild_id=GUILD_B)
    channel = _text_channel(CHANNEL_B, GUILD_B)

    with patch("fablabot.cogs.log_management.ensure_command_context", AsyncMock(return_value=True)):
        await LogManagement.log_set.callback(LogManagement(MagicMock()), interaction, channel=channel)

    assert guild_config.get_log_channel_id(GUILD_B) == CHANNEL_B
    assert guild_config.get_log_channel_id(GUILD_A) == CHANNEL_A
    interaction.response.send_message.assert_awaited_once()
    assert interaction.response.send_message.call_args.kwargs.get("ephemeral") is not True


async def test_log_set_refuses_a_channel_of_another_guild(caplog: pytest.LogCaptureFixture) -> None:
    """A channel of another server is refused, ephemerally, with IDs only in the log."""
    interaction = make_interaction(guild_id=GUILD_B, user_name=USER_NAME)
    channel = _text_channel(CHANNEL_A, GUILD_A)

    with (
        patch("fablabot.cogs.log_management.ensure_command_context", AsyncMock(return_value=True)),
        caplog.at_level(logging.INFO),
    ):
        await LogManagement.log_set.callback(LogManagement(MagicMock()), interaction, channel=channel)

    assert guild_config.get_log_channel_id(GUILD_B) is None
    interaction.response.send_message.assert_awaited_once_with("Ce salon n'appartient pas à ce serveur.", ephemeral=True)
    assert "kind=other_guild_channel" in caplog.text
    assert f"guild_id={GUILD_B}" in caplog.text
    assert USER_NAME not in caplog.text


async def test_log_set_does_not_touch_the_legacy_entry() -> None:
    """``/log set`` never writes the legacy section."""
    path = guild_config.Path("data/guild_config.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"__global__": {"log_channel_id": CHANNEL_A}}), encoding="utf-8")
    guild_config._config_store = JsonStateStore(guild_config.logger, path)
    interaction = make_interaction(guild_id=GUILD_B)

    with patch("fablabot.cogs.log_management.ensure_command_context", AsyncMock(return_value=True)):
        await LogManagement.log_set.callback(
            LogManagement(MagicMock()),
            interaction,
            channel=_text_channel(CHANNEL_B, GUILD_B),
        )

    assert _file() == {"__global__": {"log_channel_id": CHANNEL_A}, str(GUILD_B): {"log_channel_id": CHANNEL_B}}


def test_is_log_channel_is_per_guild() -> None:
    """A guild's log channel counts for that guild only."""
    guild_config.set_log_channel_id(GUILD_A, CHANNEL_A)

    assert log_channels.is_log_channel(GUILD_A, CHANNEL_A)
    assert not log_channels.is_log_channel(GUILD_B, CHANNEL_A)


def test_operator_channel_is_a_log_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    """The operator channel is protected in every guild."""
    _set_operator(monkeypatch, OPERATOR)

    assert log_channels.is_log_channel(GUILD_A, OPERATOR)
    assert log_channels.is_log_channel(GUILD_B, OPERATOR)


@pytest.mark.parametrize("raw", ["abc-secret", "-5", "0", "١٢٣", "12,34"])
def test_operator_channel_malformed_is_ignored_without_echo(raw: str, caplog: pytest.LogCaptureFixture) -> None:
    """A malformed value gives None and a warning that does not repeat it."""
    with caplog.at_level(logging.WARNING):
        assert log_channels.parse_operator_channel_id(raw) is None

    assert "OPERATOR_LOG_CHANNEL_ID is not a numeric channel ID" in caplog.text
    assert raw not in caplog.text


@pytest.mark.parametrize("raw", [None, "", "  "])
def test_operator_channel_absent_is_none(raw: str | None, caplog: pytest.LogCaptureFixture) -> None:
    """An absent or blank value gives None without a warning."""
    with caplog.at_level(logging.WARNING):
        assert log_channels.parse_operator_channel_id(raw) is None

    assert caplog.text == ""


async def test_msg_clear_refuses_the_guild_log_channel() -> None:
    """``/msg clear`` refuses the calling server's log channel."""
    guild_config.set_log_channel_id(GUILD_A, CHANNEL_A)
    interaction = make_interaction(guild_id=GUILD_A)
    interaction.permissions = Permissions(manage_messages=True)
    interaction.channel = _text_channel(CHANNEL_A, GUILD_A)
    interaction.channel.purge = AsyncMock()

    await MessageManagement.msg_clear.callback(MessageManagement(MagicMock()), interaction, messages=5)

    interaction.channel.purge.assert_not_awaited()
    assert interaction.response.send_message.call_args.kwargs["ephemeral"] is True


async def test_msg_clear_allows_a_channel_that_is_another_guild_log_channel() -> None:
    """A channel that only is another server's log channel is not protected."""
    guild_config.set_log_channel_id(GUILD_B, CHANNEL_A)
    interaction = make_interaction(guild_id=GUILD_A)
    interaction.permissions = Permissions(manage_messages=True)
    interaction.channel = _text_channel(CHANNEL_A, GUILD_A)
    interaction.channel.purge = AsyncMock(return_value=[])
    interaction.edit_original_response = AsyncMock()

    await MessageManagement.msg_clear.callback(MessageManagement(MagicMock()), interaction, messages=5)

    interaction.channel.purge.assert_awaited_once()


@pytest.mark.parametrize(("owner", "reported"), [(GUILD_A, True), (GUILD_B, False)])
async def test_deletion_in_a_log_channel_is_reported_per_guild(owner: int, reported: bool) -> None:
    """A raw deletion is reported in the server's own log channel only."""
    guild_config.set_log_channel_id(owner, CHANNEL_A)
    channel = _text_channel(CHANNEL_A, GUILD_A, name="journal")
    guild = MagicMock()
    guild.id = GUILD_A

    async def audit_logs(**_kwargs: object):
        return
        yield

    guild.audit_logs = audit_logs
    bot = MagicMock()
    bot.get_guild.return_value = guild
    bot.get_channel.return_value = channel
    payload = MagicMock(cached_message=None, guild_id=GUILD_A, channel_id=CHANNEL_A, message_id=99)

    with patch.object(ChannelManagement, "_get_codir_mention", AsyncMock(return_value="")) as mention:
        await ChannelManagement(bot).on_raw_message_delete(payload)

    assert mention.await_count == (1 if reported else 0)
