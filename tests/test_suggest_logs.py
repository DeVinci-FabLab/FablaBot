"""The suggestion flow never logs its author or text."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

from conftest import cog_bot, fill_text_input, make_interaction
from discord import Member, SelectOption, TextChannel
import pytest

from fablabot.cogs.message_management import MessageManagement
from fablabot.helpers.utils import ensure_command_context, log_request
from fablabot.models.message import SUGGESTION_OPTIONS
from fablabot.ui.message import SuggestionView

if TYPE_CHECKING:
    from collections.abc import Iterator

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
    # discord.py exposes no public setter for a select choice
    view.recipient_select._values = [key]
    await view.recipient_select.callback(interaction)
    # Anonymous takes one click; signed takes two, so the toggle callback runs in both cases.
    await view.anonymity_button.callback(interaction)
    if not anonymous:
        await view.anonymity_button.callback(interaction)
    assert view.selected_recipient == key
    assert view.anonymous is anonymous
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
    """A suggestion, anonymous or signed, logs neither its author nor its text."""
    await _run_flow(caplog, anonymous=anonymous)
    _assert_no_secret(caplog)


async def test_suggest_delivery_failure_logs_no_author_or_text(caplog: pytest.LogCaptureFixture) -> None:
    """A failed delivery is logged without the author or the text."""
    await _run_flow(caplog, anonymous=False, send_error=Exception("boom"))
    _assert_no_secret(caplog)
    assert "Failed to send suggestion" in caplog.text


async def test_suggest_logs_command_and_guild_only(caplog: pytest.LogCaptureFixture) -> None:
    """The suggest command logs one line with the guild id and no user."""
    caplog.set_level(logging.DEBUG)
    interaction = make_interaction()
    await MessageManagement.suggest.callback(cog_bot(), interaction)
    lines = [r.getMessage() for r in caplog.records if "[suggest]" in r.getMessage()]
    assert len(lines) == 1
    assert "guild_id=1234" in lines[0]
    assert "user=" not in lines[0]
    assert "id=" not in lines[0].replace("guild_id=", "")


async def test_suggest_stops_when_context_check_fails() -> None:
    """The suggest command sends nothing when the context check fails."""
    interaction = make_interaction()
    with patch("fablabot.cogs.message_management.ensure_command_context", AsyncMock(return_value=False)):
        await MessageManagement.suggest.callback(cog_bot(), interaction)
    interaction.response.send_message.assert_not_awaited()


async def test_role_refusal_without_identity_logs_no_user(caplog: pytest.LogCaptureFixture) -> None:
    """A role refusal logs no user identity."""
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


def test_log_request_logs_caller_id_not_name(caplog: pytest.LogCaptureFixture) -> None:
    """The caller shows up by ID only."""
    caplog.set_level(logging.DEBUG)
    log_request(logging.getLogger("test"), "x", make_interaction(), key="v")
    assert f"user_id={USER_ID}" in caplog.text
    assert USER_NAME not in caplog.text


def _defaults(view: SuggestionView) -> list[str]:
    return [option.value for option in view.recipient_select.options if option.default]


async def test_anonymity_button_keeps_recipient_selected() -> None:
    """The re-render after the anonymity button marks the chosen recipient as default."""
    interaction = make_interaction()
    key = list(SUGGESTION_OPTIONS)[-1]
    view = SuggestionView(cog_bot())
    view.recipient_select._values = [key]  # discord.py exposes no public setter
    await view.recipient_select.callback(interaction)
    await view.anonymity_button.callback(interaction)

    edited = interaction.response.edit_message.call_args.kwargs["view"]
    assert _defaults(edited) == [key]
    assert len(edited.recipient_select.options) == len(SUGGESTION_OPTIONS)


async def test_recipient_choice_never_leaks_to_another_view() -> None:
    """A later SuggestionView starts with no default option."""
    interaction = make_interaction()
    key = next(iter(SUGGESTION_OPTIONS))
    view = SuggestionView(cog_bot())
    view.recipient_select._values = [key]
    await view.recipient_select.callback(interaction)
    await view.anonymity_button.callback(interaction)

    assert _defaults(SuggestionView(cog_bot())) == []


async def test_changing_recipient_moves_the_default() -> None:
    """Choosing another recipient leaves only the new one selected."""
    first, *_, last = SUGGESTION_OPTIONS
    view = SuggestionView(cog_bot())
    await _choose_and_toggle(view, first)
    await _choose_and_toggle(view, last)

    assert _defaults(view) == [last]


def _template_options() -> list:
    """The options the decorator hands to every view; they must stay untouched."""
    return SuggestionView.recipient_select.__discord_ui_model_kwargs__["options"]  # type: ignore[attr-defined]


async def _choose_and_toggle(view: SuggestionView, key: str) -> None:
    interaction = make_interaction()
    view.recipient_select._values = [key]
    await view.recipient_select.callback(interaction)
    await view.anonymity_button.callback(interaction)


async def test_views_never_share_option_objects() -> None:
    """Each view holds its own option list and objects, so an in-place change cannot leak."""
    first, second = SuggestionView(cog_bot()), SuggestionView(cog_bot())
    template = _template_options()

    assert first.recipient_select.options is not second.recipient_select.options
    for a, b, shared in zip(first.recipient_select.options, second.recipient_select.options, template, strict=True):
        assert a is not b
        assert a is not shared
        assert b is not shared


async def test_choice_stays_in_its_own_view_when_views_are_live_together() -> None:
    """A view built before another member's choice stays clean, and so does the decorator template."""
    mine, theirs = SuggestionView(cog_bot()), SuggestionView(cog_bot())
    keys = list(SUGGESTION_OPTIONS)

    await _choose_and_toggle(mine, keys[-1])
    await _choose_and_toggle(theirs, keys[0])

    assert _defaults(mine) == [keys[-1]]
    assert _defaults(theirs) == [keys[0]]
    assert not any(option.default for option in _template_options())


@pytest.fixture
def _template_defaults_restored() -> Iterator[None]:
    """Put the decorator template's defaults back after a test, so a leak found there cannot reach another test."""
    options = _template_options()
    saved = [option.default for option in options]
    yield
    for option, default in zip(options, saved, strict=True):
        option.default = default


@pytest.mark.usefixtures("_template_defaults_restored")
async def test_choice_stays_in_its_own_view_without_discord_copying_options(monkeypatch: pytest.MonkeyPatch) -> None:
    """The view keeps a choice to itself even if discord.py stops copying the decorator's options per view."""
    monkeypatch.setattr(SelectOption, "copy", lambda self: self)
    keys = list(SUGGESTION_OPTIONS)
    before, chooser = SuggestionView(cog_bot()), SuggestionView(cog_bot())

    await _choose_and_toggle(chooser, keys[-1])
    after = SuggestionView(cog_bot())

    assert _defaults(chooser) == [keys[-1]]
    assert _defaults(before) == []
    assert _defaults(after) == []
    assert not any(option.default for option in _template_options())


async def test_first_render_ignores_the_decorator_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """A new view starts with no default whatever state the decorator's options are in."""
    monkeypatch.setattr(_template_options()[0], "default", True)

    assert _defaults(SuggestionView(cog_bot())) == []
