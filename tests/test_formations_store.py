"""Training drafts and published messages read back whole, and the newest published message is the current one."""

from __future__ import annotations

from datetime import datetime, timedelta
import sqlite3
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from conftest import make_interaction
from discord import TextChannel
import pytest

from fablabot import db
from fablabot.cogs import formation_management
from fablabot.cogs.formation_management import FormationManagement
from fablabot.helpers.constants import PARIS_TZ, ErrorMessages
from fablabot.helpers.guild_access import GuildAccessPolicy
from fablabot.models import FmMessageDraft, Formation, PublishedMessage
from fablabot.services import formations

GUILD_A = 111111111111111111
GUILD_B = 222222222222222222
FIRST = 333333333333333333
SECOND = 333333333333333334


def full_formation(emoji: str = "🔧") -> Formation:
    """Build a formation with no field at its default."""
    return Formation(
        emoji=emoji,
        name="Découpe laser",
        trainer_mention="<@1>",
        start_iso="2026-12-01T18:30:00+01:00",
        duration="2h",
        seats=2,
        description="Texte\nsur deux lignes",
        excusable=False,
        registered_users=[
            {"user_id": 20, "username": "Zoé (zoe)", "ts_iso": "2026-11-01T10:00:00+01:00"},
            {"user_id": 10, "username": "Ana (ana)", "ts_iso": "2026-11-01T10:00:01+01:00"},
        ],
        waitlisted_users=[{"user_id": 30, "username": "Léo (leo)", "ts_iso": "2026-11-01T10:00:02+01:00"}],
        notified_hour_before=True,
        notified_at_start=True,
    )


def message(*fms: Formation, intro: str = "Bonjour") -> FmMessageDraft:
    """Build a message payload with no field at its default."""
    return FmMessageDraft(
        header="## Formations",
        role_id=5,
        intro=intro,
        fms=list(fms),
        end="À bientôt",
        request_forms_url="https://example.org/form",
    )


def test_a_draft_round_trips() -> None:
    """Every field of a draft comes back, with formations and members in their order."""
    draft = message(full_formation("🅱"), full_formation("🅰"))

    formations.save_draft(GUILD_A, draft)

    assert formations.get_draft(GUILD_A) == draft


def test_a_published_message_round_trips() -> None:
    """Every field of a published message comes back."""
    published = PublishedMessage(message_id=FIRST, channel_id=7, message=message(full_formation()))

    formations.save_published(GUILD_A, published)

    assert formations.current_published(GUILD_A) == published


def test_a_guild_with_nothing_reads_none() -> None:
    """No draft and nothing published read as absent."""
    assert formations.get_draft(GUILD_A) is None
    assert formations.current_published(GUILD_A) is None


def test_a_new_draft_replaces_the_old_one_whole() -> None:
    """No formation or member of the previous draft stays behind."""
    formations.save_draft(GUILD_A, message(full_formation("🅰"), full_formation("🅱")))

    formations.save_draft(GUILD_A, message(full_formation("🇿")))

    assert [fm.emoji for fm in (formations.get_draft(GUILD_A) or message()).fms] == ["🇿"]
    assert db.connection().execute("SELECT count(*) FROM formation").fetchone()[0] == 1
    assert db.connection().execute("SELECT count(*) FROM registration").fetchone()[0] == 3


def test_the_draft_survives_a_publish() -> None:
    """Publishing keeps the draft apart from the published copy."""
    draft = message(full_formation())
    formations.save_draft(GUILD_A, draft)

    formations.save_published(GUILD_A, PublishedMessage(message_id=FIRST, channel_id=7, message=draft))
    edited = formations.current_published(GUILD_A)
    assert edited is not None
    edited.message.fms[0].registered_users = []
    formations.save_published(GUILD_A, edited)

    assert formations.get_draft(GUILD_A) == draft


def test_the_newest_published_message_is_the_current_one() -> None:
    """A second publish takes over; the first keeps its rows."""
    formations.save_published(GUILD_A, PublishedMessage(message_id=FIRST, channel_id=7, message=message(intro="un")))
    formations.save_published(GUILD_A, PublishedMessage(message_id=SECOND, channel_id=7, message=message(intro="deux")))

    current = formations.current_published(GUILD_A)
    assert current is not None
    assert current.message_id == SECOND
    assert db.connection().execute("SELECT count(*) FROM fm_message").fetchone()[0] == 2


def test_a_late_write_for_an_older_message_leaves_the_current_one() -> None:
    """A write under an older ID changes that message and does not take the place of the newer one."""
    older = PublishedMessage(message_id=FIRST, channel_id=7, message=message(intro="un"))
    formations.save_published(GUILD_A, older)
    formations.save_published(GUILD_A, PublishedMessage(message_id=SECOND, channel_id=7, message=message(intro="deux")))

    older.message.intro = "mis à jour"
    formations.save_published(GUILD_A, older)

    current = formations.current_published(GUILD_A)
    assert current is not None
    assert (current.message_id, current.message.intro) == (SECOND, "deux")


def test_a_message_first_written_after_a_newer_one_is_not_the_current_one() -> None:
    """An older message that reaches the database late is stored, and the newer message stays current."""
    formations.save_published(GUILD_A, PublishedMessage(message_id=SECOND, channel_id=7, message=message(intro="deux")))

    formations.save_published(GUILD_A, PublishedMessage(message_id=FIRST, channel_id=7, message=message(intro="un")))

    current = formations.current_published(GUILD_A)
    assert current is not None
    assert (current.message_id, current.message.intro) == (SECOND, "deux")


def test_servers_stay_apart() -> None:
    """One server's draft and message are not another's, even under one message ID."""
    formations.save_draft(GUILD_A, message(intro="a"))
    formations.save_published(GUILD_A, PublishedMessage(message_id=FIRST, channel_id=7, message=message(intro="a")))

    assert formations.get_draft(GUILD_B) is None
    assert formations.current_published(GUILD_B) is None

    formations.save_published(GUILD_B, PublishedMessage(message_id=FIRST, channel_id=8, message=message(intro="b")))

    current = formations.current_published(GUILD_A)
    assert current is not None
    assert current.message.intro == "a"


def test_only_guilds_with_a_publish_are_listed() -> None:
    """A draft alone gives the reminder tick nothing to look at."""
    formations.save_draft(GUILD_A, message())
    formations.save_published(GUILD_B, PublishedMessage(message_id=FIRST, channel_id=7, message=message()))

    assert formations.guilds_with_published() == [GUILD_B]


def test_published_messages_awaiting_their_start_are_listed_by_start_time() -> None:
    """Only a message with a training that started inside the range and was not handled is listed, whatever its age."""
    now = datetime(2026, 12, 1, 12, 0, tzinfo=PARIS_TZ)

    def training(start: datetime, *, handled: bool = False) -> Formation:
        return Formation(
            emoji="🔧", name="Laser", trainer_mention="<@1>", start_iso=start.isoformat(), duration="2h", seats=2,
            notified_at_start=handled,
        )  # fmt: skip

    for message_id, fm in (
        (FIRST, training(now - timedelta(minutes=30))),
        (FIRST + 1, training(now - timedelta(minutes=30), handled=True)),
        (FIRST + 2, training(now - timedelta(days=8))),
        (FIRST + 3, training(now + timedelta(hours=2))),
        (FIRST + 4, training(now + timedelta(minutes=5))),
    ):
        formations.save_published(GUILD_A, PublishedMessage(message_id=message_id, channel_id=7, message=message(fm)))
    formations.save_published(GUILD_B, PublishedMessage(message_id=FIRST + 5, channel_id=7, message=message(training(now))))

    found = formations.published_awaiting_start(GUILD_A, now - timedelta(days=7), now + timedelta(minutes=5))

    assert [pub.message_id for pub in found] == [FIRST, FIRST + 4]


def test_a_failed_save_keeps_the_previous_message() -> None:
    """A write that fails halfway leaves the draft as it was."""
    draft = message(full_formation())
    formations.save_draft(GUILD_A, draft)
    broken = message(full_formation())
    broken.fms[0].registered_users[1]["user_id"] = None

    with pytest.raises(sqlite3.IntegrityError):
        formations.save_draft(GUILD_A, broken)

    assert formations.get_draft(GUILD_A) == draft


@pytest.fixture
async def cog() -> Any:
    """The formation cog over a stub bot, with its loop stopped."""
    bot = MagicMock()
    bot.guild_access = GuildAccessPolicy(frozenset({GUILD_A, GUILD_B}))
    instance = FormationManagement(bot)
    instance._check_upcoming_formations.cancel()
    return instance


async def test_a_storage_failure_keeps_the_reminder_tick_alive(cog: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed read or write is logged and reaches neither the loop nor the next server."""
    start = (datetime.now(PARIS_TZ) + timedelta(days=3)).isoformat()
    fm = Formation(emoji="🔧", name="", trainer_mention="", start_iso=start, duration="", seats=1)
    for guild_id in (GUILD_A, GUILD_B):
        cog._set_last_published_in_guild(guild_id, PublishedMessage(message_id=FIRST, channel_id=7, message=message(fm)))
    seen: list[int] = []

    def fail_then_count(guild_id: int, _published: PublishedMessage) -> None:
        """Fail on the first server and record the others."""
        seen.append(guild_id)
        if guild_id == GUILD_A:
            raise sqlite3.OperationalError

    cog.bot.get_guild.side_effect = lambda guild_id: MagicMock(id=guild_id)
    monkeypatch.setattr(cog, "_set_last_published_in_guild", fail_then_count)
    monkeypatch.setattr("fablabot.cogs.formation_management.format_respo_contacts", lambda _guild: "")

    await cog._check_upcoming_formations.coro(cog)

    assert seen == [GUILD_A, GUILD_B]


async def test_a_failed_guild_list_skips_the_tick(cog: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """A read that fails before the first server ends this tick only."""
    monkeypatch.setattr(formations, "guilds_with_published", MagicMock(side_effect=sqlite3.OperationalError))

    await cog._check_upcoming_formations.coro(cog)


@pytest.mark.parametrize(
    ("delete_error", "reply"),
    [(None, ErrorMessages.PUBLISH_NOT_SAVED), (OSError(), ErrorMessages.PUBLISH_NOT_SAVED_STILL_UP)],
    ids=["post-deleted", "post-still-up"],
)
async def test_a_publish_the_database_refuses_adds_no_reaction_and_says_how_it_ended(
    cog: Any, delete_error: Exception | None, reply: str
) -> None:
    """The save comes before the reactions, the post is taken down or the caller told to, and the draft stays."""
    draft = message(full_formation())
    cog.set_guild_draft(GUILD_A, draft)
    interaction = make_interaction(guild_id=GUILD_A)
    posted = MagicMock(id=FIRST)
    posted.delete = AsyncMock(side_effect=delete_error)
    posted.add_reaction = AsyncMock()
    channel = MagicMock(spec=TextChannel)
    channel.send = AsyncMock(return_value=posted)

    with (
        patch.object(formation_management, "ensure_command_context", AsyncMock(return_value=True)),
        patch.object(formations, "save_published", side_effect=sqlite3.OperationalError("full")),
    ):
        await FormationManagement.fm_publish.callback(cog, interaction, channel=channel)  # pyright: ignore[reportCallIssue]

    posted.add_reaction.assert_not_awaited()
    posted.delete.assert_awaited_once()
    interaction.followup.send.assert_awaited_once_with(reply)
    assert formations.get_draft(GUILD_A) == draft
