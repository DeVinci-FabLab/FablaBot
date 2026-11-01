"""Tracked messages and message drafts read back as they were stored, server by server."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from fablabot import db
from fablabot.cogs.message_management import MessageManagement
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

    assert cog._get_tracked_message(GUILD_A, 10**24) is None
