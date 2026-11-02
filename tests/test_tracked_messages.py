"""Tracked messages and message drafts read back as they were stored, server by server."""

from __future__ import annotations

import sqlite3
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from conftest import make_interaction
from discord import TextChannel
import pytest

from fablabot import db
from fablabot.cogs import message_management
from fablabot.cogs.message_management import MessageManagement
from fablabot.helpers.constants import ErrorMessages
from fablabot.models.message import MessageDraft, MsgReactionEvent, TrackedMessage
from fablabot.services import messages

GUILD_A = 111111111111111111
GUILD_B = 222222222222222222
MESSAGE = 333333333333333333
OTHER_MESSAGE = 333333333333333334


def _reaction(emoji: str = "🔧", **overrides: Any) -> MsgReactionEvent:
    """Build a reaction action."""
    return MsgReactionEvent(emoji=emoji, action_type="role_dm", message_content="Bonjour\n{user}", **overrides)


def _tracked(message_id: int = MESSAGE, *reactions: MsgReactionEvent) -> TrackedMessage:
    """Build a tracked message with every field set."""
    return TrackedMessage(
        message_id=message_id,
        channel_id=7,
        content="Annonce",
        reactions=list(reactions),
        created_by=9,
        created_at_iso="2026-11-01T10:00:00+01:00",
    )


def test_a_tracked_message_round_trips() -> None:
    """Every field of a message and of its actions comes back, in order."""
    tracked = _tracked(
        MESSAGE,
        _reaction("🅱", target_id=5, target_name="Bureau", message_id=MESSAGE),
        _reaction("🅰"),
    )

    messages.save_tracked(GUILD_A, tracked)

    assert messages.tracked_messages(GUILD_A) == {MESSAGE: tracked}


def test_saving_again_replaces_and_keeps_the_place() -> None:
    """A changed message keeps its rank, so the list order does not move."""
    messages.save_tracked(GUILD_A, _tracked(MESSAGE, _reaction("🅰")))
    messages.save_tracked(GUILD_A, _tracked(OTHER_MESSAGE))

    messages.save_tracked(GUILD_A, _tracked(MESSAGE, _reaction("🅱")))

    assert list(messages.tracked_messages(GUILD_A)) == [MESSAGE, OTHER_MESSAGE]
    assert [r.emoji for r in messages.tracked_messages(GUILD_A)[MESSAGE].reactions] == ["🅱"]


def test_servers_stay_apart() -> None:
    """One message ID on two servers is two records, and removing one leaves the other."""
    messages.save_tracked(GUILD_A, _tracked())
    messages.save_tracked(GUILD_B, _tracked())

    assert messages.remove_tracked(GUILD_A, MESSAGE) is True
    assert messages.remove_tracked(GUILD_A, MESSAGE) is False
    assert MESSAGE in messages.tracked_messages(GUILD_B)


def test_each_server_lists_only_its_own_tracked_messages() -> None:
    """A server's list holds its messages and their action texts, never another server's."""
    messages.save_tracked(GUILD_A, _tracked(MESSAGE, _reaction("🅰")))
    messages.save_tracked(GUILD_B, _tracked(OTHER_MESSAGE, _reaction("🅱")))

    assert list(messages.tracked_messages(GUILD_A)) == [MESSAGE]
    assert list(messages.tracked_messages(GUILD_B)) == [OTHER_MESSAGE]
    assert messages.tracked_messages(GUILD_A) != messages.tracked_messages(GUILD_B)


def test_each_server_keeps_its_own_draft() -> None:
    """Saving, reading and clearing a draft touches one server: the other keeps its own."""
    draft_a = MessageDraft(content="Pour A", reactions=[_reaction("🅰")])
    draft_b = MessageDraft(content="Pour B", reactions=[_reaction("🅱")])
    messages.save_draft(GUILD_A, draft_a)
    messages.save_draft(GUILD_B, draft_b)

    assert messages.get_draft(GUILD_A) == draft_a
    assert messages.get_draft(GUILD_B) == draft_b

    messages.clear_draft(GUILD_A)

    assert messages.get_draft(GUILD_A) is None
    assert messages.get_draft(GUILD_B) == draft_b


def test_a_server_without_a_draft_does_not_read_another_servers() -> None:
    """A server that never saved a draft reads none, whatever the others hold."""
    messages.save_draft(GUILD_A, MessageDraft(content="Pour A", reactions=[]))

    assert messages.get_draft(GUILD_B) is None


def test_removing_a_message_removes_its_actions() -> None:
    """No orphan action stays behind."""
    messages.save_tracked(GUILD_A, _tracked(MESSAGE, _reaction()))

    messages.remove_tracked(GUILD_A, MESSAGE)

    assert db.connection().execute("SELECT count(*) FROM tracked_reaction").fetchone()[0] == 0


def test_a_draft_round_trips_and_is_no_tracked_message() -> None:
    """A draft comes back whole and never shows among the tracked messages."""
    draft = MessageDraft(content="Texte", reactions=[_reaction()])

    messages.save_draft(GUILD_A, draft)

    assert messages.get_draft(GUILD_A) == draft
    assert messages.tracked_messages(GUILD_A) == {}


def test_a_new_draft_replaces_the_old_one() -> None:
    """A server has one draft; clearing it leaves none."""
    messages.save_draft(GUILD_A, MessageDraft(content="Un", reactions=[_reaction()]))
    messages.save_draft(GUILD_A, MessageDraft(content="Deux", reactions=[]))

    assert messages.get_draft(GUILD_A) == MessageDraft(content="Deux", reactions=[])

    messages.clear_draft(GUILD_A)

    assert messages.get_draft(GUILD_A) is None


def test_the_cog_answers_none_for_an_id_too_large_for_the_database() -> None:
    """A member can paste up to 25 digits as a message link: nothing matches, nothing fails."""
    cog = MessageManagement(MagicMock())

    assert cog.get_tracked_message(GUILD_A, 10**24) is None


@pytest.mark.parametrize(
    ("delete_error", "reply"),
    [(None, ErrorMessages.PUBLISH_NOT_SAVED), (OSError(), ErrorMessages.PUBLISH_NOT_SAVED_STILL_UP)],
    ids=["post-deleted", "post-still-up"],
)
@pytest.mark.parametrize("failing", ["save_tracked", "clear_draft"])
async def test_msg_publish_the_database_refuses_retracts_the_post_and_says_so(
    failing: str, delete_error: Exception | None, reply: str, storage_error: sqlite3.Error
) -> None:
    """A post that cannot be recorded is taken down, or the caller is told to; no reaction is synced on it."""
    interaction = make_interaction(guild_id=1234)
    cog = MessageManagement(MagicMock())
    posted = MagicMock(id=999)
    posted.delete = AsyncMock(side_effect=delete_error)
    channel = MagicMock(spec=TextChannel)
    channel.send = AsyncMock(return_value=posted)
    cog._sync_reactions_on_message = AsyncMock()
    messages.save_draft(1234, MessageDraft(content="x", reactions=[_reaction()]))

    with (
        patch.object(message_management, "ensure_command_context", AsyncMock(return_value=True)),
        patch.object(messages, failing, side_effect=storage_error),
    ):
        await MessageManagement.msg_publish.callback(cog, interaction, channel=channel)  # pyright: ignore[reportCallIssue]

    posted.delete.assert_awaited_once()
    cog._sync_reactions_on_message.assert_not_awaited()
    interaction.response.send_message.assert_awaited_once_with(reply, ephemeral=True)


def _follow_setup(draft: MessageDraft | None) -> tuple[MagicMock, MessageManagement, MagicMock]:
    """Build a `/msg follow` call on server 1234, over a message of that server, with an optional draft stored first."""
    interaction = make_interaction(guild_id=1234)
    cog = MessageManagement(MagicMock())
    cog._sync_reactions_on_message = AsyncMock()
    fetched = MagicMock(id=MESSAGE, content="Annonce")
    channel = MagicMock(spec=TextChannel, id=7)
    channel.fetch_message = AsyncMock(return_value=fetched)
    if draft is not None:
        messages.save_draft(1234, draft)
    return interaction, cog, channel


async def _follow(interaction: MagicMock, cog: MessageManagement, channel: MagicMock) -> None:
    """Run `/msg follow` on the message of `_follow_setup`."""
    with patch.object(message_management, "ensure_command_context", AsyncMock(return_value=True)):
        await MessageManagement.msg_follow.callback(cog, interaction, channel=channel, message=str(MESSAGE))  # pyright: ignore[reportCallIssue]


async def test_msg_follow_moves_the_links_of_the_draft_to_the_message() -> None:
    """The message takes the draft's links and the draft keeps its text, with none left."""
    interaction, cog, channel = _follow_setup(MessageDraft(content="x", reactions=[_reaction("🔧")]))

    await _follow(interaction, cog, channel)

    assert [(r.emoji, r.message_id) for r in messages.tracked_messages(1234)[MESSAGE].reactions] == [("🔧", MESSAGE)]
    assert messages.get_draft(1234) == MessageDraft(content="x", reactions=[])
    cog._sync_reactions_on_message.assert_awaited_once()


@pytest.mark.parametrize(
    ("trigger", "table", "when"),
    [
        ("refuse_tracked", "tracked_message", "NEW.message_id IS NOT NULL"),
        (
            "refuse_draft_links",
            "tracked_reaction",
            "OLD.tracked_message_id IN (SELECT id FROM tracked_message WHERE message_id IS NULL)",
        ),
    ],
    ids=["message-write-refused", "draft-write-refused"],
)
async def test_msg_follow_a_refused_write_leaves_the_links_in_the_draft_and_tracks_nothing(
    trigger: str, table: str, when: str
) -> None:
    """The links move in one write: refused at either end, they stay in the draft and no message is tracked."""
    draft = MessageDraft(content="x", reactions=[_reaction("🔧")])
    interaction, cog, channel = _follow_setup(draft)
    event = "INSERT" if table == "tracked_message" else "DELETE"
    db.connection().execute(
        f"CREATE TRIGGER {trigger} BEFORE {event} ON {table} WHEN {when} BEGIN SELECT RAISE(ABORT, 'refused'); END"
    )

    with pytest.raises(sqlite3.Error):
        await _follow(interaction, cog, channel)

    assert messages.get_draft(1234) == draft
    assert messages.tracked_messages(1234) == {}
    cog._sync_reactions_on_message.assert_not_awaited()
    interaction.response.send_message.assert_not_awaited()


@pytest.mark.parametrize(
    ("draft_links", "expected"), [([], ["🅰"]), (["🅱"], ["🅰", "🅱"])], ids=["empty-draft", "draft-with-a-link"]
)
async def test_msg_follow_on_a_message_already_tracked_keeps_its_links(draft_links: list[str], expected: list[str]) -> None:
    """Following again adds the draft's links to the message's own; it never wipes them."""
    messages.save_tracked(1234, _tracked(MESSAGE, _reaction("🅰", message_id=MESSAGE)))
    interaction, cog, channel = _follow_setup(MessageDraft(content="x", reactions=[_reaction(emoji) for emoji in draft_links]))

    await _follow(interaction, cog, channel)

    assert [r.emoji for r in messages.tracked_messages(1234)[MESSAGE].reactions] == expected
