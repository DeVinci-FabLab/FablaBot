"""The unlink view removes the action it showed from what is stored now, and never writes back its own copy."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from conftest import make_interaction
from discord import TextChannel
import pytest

from fablabot.cogs.message_management import MessageManagement
from fablabot.helpers.constants import ErrorMessages
from fablabot.models.message import MessageDraft, MsgReactionEvent, TrackedMessage
from fablabot.services import messages
from fablabot.ui.message import _UnlinkReactionSelectView

GUILD_ID = 1234
MESSAGE_ID = 333333333333333333
CHANNEL_ID = 7


def _link(emoji: str) -> MsgReactionEvent:
    """Build an action linked to an emoji."""
    return MsgReactionEvent(emoji=emoji, action_type="role_dm", message_content=f"Bonjour {emoji}", message_id=MESSAGE_ID)


def _tracked(*emojis: str) -> TrackedMessage:
    """Build a tracked message holding one action per emoji."""
    return TrackedMessage(
        message_id=MESSAGE_ID,
        channel_id=CHANNEL_ID,
        content="Annonce",
        reactions=[_link(emoji) for emoji in emojis],
        created_by=9,
        created_at_iso="2026-11-01T10:00:00+01:00",
    )


def _stored_emojis() -> list[str]:
    """List the emojis linked on the stored message."""
    return [r.emoji for r in messages.tracked_messages(GUILD_ID)[MESSAGE_ID].reactions]


def _button(view: _UnlinkReactionSelectView, emoji: str) -> Any:
    """Find the button that removes the action of an emoji."""
    return next(item for item in view.children if emoji in str(getattr(item, "label", "")))


def _click(view: _UnlinkReactionSelectView, emoji: str) -> tuple[Any, Any]:
    """Build the click on the button of an emoji and return the interaction with the coroutine to await."""
    interaction = make_interaction(guild_id=GUILD_ID)
    message = MagicMock()
    message.remove_reaction = AsyncMock()
    channel = MagicMock(spec=TextChannel)
    channel.fetch_message = AsyncMock(return_value=message)
    interaction.guild.fetch_channel = AsyncMock(return_value=channel)
    interaction.message = message
    return interaction, _button(view, emoji).callback(interaction)


@pytest.fixture
def cog() -> MessageManagement:
    """The message cog over a stub bot."""
    return MessageManagement(MagicMock())


async def test_a_link_added_since_the_view_opened_survives_the_removal(cog: MessageManagement) -> None:
    """A colleague links a new action while the selector is open: removing another one keeps it."""
    messages.save_tracked(GUILD_ID, _tracked("🅰", "🅱"))
    view = _UnlinkReactionSelectView(cog, messages.tracked_messages(GUILD_ID)[MESSAGE_ID])
    messages.save_tracked(GUILD_ID, _tracked("🅰", "🅱", "🅲"))
    _interaction, click = _click(view, "🅰")

    await click

    assert _stored_emojis() == ["🅱", "🅲"]


async def test_a_late_click_after_the_tracking_stopped_does_not_bring_it_back(cog: MessageManagement) -> None:
    """The message is no longer followed: the click says the view expired and stores nothing."""
    messages.save_tracked(GUILD_ID, _tracked("🅰", "🅱"))
    view = _UnlinkReactionSelectView(cog, messages.tracked_messages(GUILD_ID)[MESSAGE_ID])
    messages.remove_tracked(GUILD_ID, MESSAGE_ID)
    interaction, click = _click(view, "🅰")

    await click

    interaction.response.send_message.assert_awaited_once_with(ErrorMessages.EXPIRED_VIEW_MESSAGE, ephemeral=True)
    assert messages.tracked_messages(GUILD_ID) == {}


async def test_a_stale_draft_view_does_not_replace_a_newer_draft(cog: MessageManagement) -> None:
    """The draft was started again while the selector was open: its new text stays, minus the action removed."""
    messages.save_draft(GUILD_ID, MessageDraft(content="ancien", reactions=[_link("🅰")]))
    draft = messages.get_draft(GUILD_ID)
    assert draft is not None
    view = _UnlinkReactionSelectView(cog, draft)
    messages.save_draft(GUILD_ID, MessageDraft(content="nouveau texte", reactions=[_link("🅰"), _link("🅱")]))
    _interaction, click = _click(view, "🅰")

    await click

    assert messages.get_draft(GUILD_ID) == MessageDraft(content="nouveau texte", reactions=[_link("🅱")])


async def test_a_draft_published_since_is_not_brought_back(cog: MessageManagement) -> None:
    """The draft is gone: the click says the view expired and stores no draft."""
    messages.save_draft(GUILD_ID, MessageDraft(content="x", reactions=[_link("🅰")]))
    draft = messages.get_draft(GUILD_ID)
    assert draft is not None
    view = _UnlinkReactionSelectView(cog, draft)
    messages.clear_draft(GUILD_ID)
    interaction, click = _click(view, "🅰")

    await click

    interaction.response.send_message.assert_awaited_once_with(ErrorMessages.EXPIRED_VIEW_MESSAGE, ephemeral=True)
    assert messages.get_draft(GUILD_ID) is None


async def test_an_action_already_removed_is_not_removed_twice(cog: MessageManagement) -> None:
    """Two staff open the same selector: the second click removes nothing more and says the action is gone."""
    messages.save_tracked(GUILD_ID, _tracked("🅰", "🅱"))
    view = _UnlinkReactionSelectView(cog, messages.tracked_messages(GUILD_ID)[MESSAGE_ID])
    other_view = _UnlinkReactionSelectView(cog, messages.tracked_messages(GUILD_ID)[MESSAGE_ID])
    _first, first_click = _click(view, "🅰")
    await first_click
    second, second_click = _click(other_view, "🅰")

    await second_click

    assert second.response.edit_message.await_args.kwargs["content"] == "Action introuvable."
    assert _stored_emojis() == ["🅱"]


async def test_the_last_action_of_an_emoji_takes_the_bot_reaction_off_the_message(cog: MessageManagement) -> None:
    """The removal still cleans the message: no action left on the emoji, no reaction of the bot."""
    messages.save_tracked(GUILD_ID, _tracked("🅰", "🅱"))
    view = _UnlinkReactionSelectView(cog, messages.tracked_messages(GUILD_ID)[MESSAGE_ID])
    interaction, click = _click(view, "🅰")

    await click

    channel = interaction.guild.fetch_channel.return_value
    channel.fetch_message.return_value.remove_reaction.assert_awaited_once_with("🅰", cog.bot.user)
    assert _stored_emojis() == ["🅱"]
