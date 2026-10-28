"""The suggestion flow never logs its author or text."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

from conftest import cog_bot, fill_text_input, make_interaction
from discord import Member, TextChannel
import pytest

from fablabot.cogs.message_management import MessageManagement
from fablabot.helpers.utils import ensure_command_context, log_request
from fablabot.models.message import SUGGESTION_OPTIONS
from fablabot.ui.message import SuggestionView

SECRET_TEXT = "SECRET-SUGGESTION-TEXT"
USER_ID = 418003123456789012
USER_NAME = "kevin_secret_name"
DISPLAY_NAME = "Kevin Secret"
SECRETS = (str(USER_ID), USER_NAME, DISPLAY_NAME, SECRET_TEXT)


def _assert_no_secret(caplog: pytest.LogCaptureFixture) -> None:
    for secret in SECRETS:
        assert secret not in caplog.text
        assert all(secret not in record.getMessage() for record in caplog.records)


async def _run_flow(caplog: pytest.LogCaptureFixture, *, anonymous: bool, send_error: Exception | None = None) -> MagicMock:
    caplog.set_level(logging.DEBUG)
    interaction = make_interaction()
    cog = cog_bot()
    key = next(iter(SUGGESTION_OPTIONS))
    config = SUGGESTION_OPTIONS[key]

    await MessageManagement.suggest.callback(cog, interaction)

    view = SuggestionView(cog)
    view.selected_recipient = key
    view.anonymous = anonymous
    interaction.message = MagicMock()
    await view.open_modal_button.callback(interaction)
    modal = interaction.response.send_modal.call_args.args[0]
    fill_text_input(modal.suggestion_input, SECRET_TEXT)

    channel = MagicMock(spec=TextChannel)
    channel.name = config.channel_name
    channel.id = 555
    channel.send = AsyncMock(side_effect=send_error)
    interaction.guild.text_channels = [channel]
    interaction.user.mention = "<@418003123456789012>"
    interaction.user.display_avatar.url = "https://example.invalid/a.png"

    await modal.on_submit(interaction)
    return interaction


@pytest.mark.parametrize("anonymous", [True, False], ids=["anonymous", "signed"])
async def test_suggest_flow_logs_no_author_or_text(caplog: pytest.LogCaptureFixture, *, anonymous: bool) -> None:
    await _run_flow(caplog, anonymous=anonymous)
    _assert_no_secret(caplog)


async def test_suggest_delivery_failure_logs_no_author_or_text(caplog: pytest.LogCaptureFixture) -> None:
    await _run_flow(caplog, anonymous=False, send_error=Exception("boom"))
    _assert_no_secret(caplog)
    assert "Failed to send suggestion" in caplog.text


async def test_suggest_logs_command_and_guild_only(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    interaction = make_interaction()
    await MessageManagement.suggest.callback(cog_bot(), interaction)
    lines = [r.getMessage() for r in caplog.records if "[suggest]" in r.getMessage()]
    assert len(lines) == 1
    assert "guild_id=1234" in lines[0]
    assert "user=" not in lines[0]
    assert "id=" not in lines[0].replace("guild_id=", "")


async def test_suggest_stops_when_context_check_fails() -> None:
    interaction = make_interaction()
    with patch("fablabot.cogs.message_management.ensure_command_context", AsyncMock(return_value=False)):
        await MessageManagement.suggest.callback(cog_bot(), interaction)
    interaction.response.send_message.assert_not_awaited()


async def test_role_refusal_without_identity_logs_no_user(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    interaction = make_interaction()
    bureau = MagicMock()
    bureau.name = "Bureau"
    interaction.guild.roles = [bureau]
    interaction.channel = MagicMock(spec=TextChannel)
    assert isinstance(interaction.user, Member)

    result = await ensure_command_context(
        logging.getLogger("test"),
        "x",
        interaction,
        required_roles={"Bureau"},
        enforce_commands_channel=False,
        log_identity=False,
    )

    assert result is False
    assert USER_NAME not in caplog.text
    assert str(USER_ID) not in caplog.text


def test_log_request_default_keeps_identity(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    log_request(logging.getLogger("test"), "x", make_interaction(), key="v")
    assert f"user={USER_NAME}" in caplog.text
    assert f"id={USER_ID}" in caplog.text
