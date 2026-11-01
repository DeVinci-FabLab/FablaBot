"""The reaction history keeps its order, its scopes and its servers apart, and forgets past retention."""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
from zoneinfo import ZoneInfo

import pytest

from fablabot import db
from fablabot.helpers.constants import PARIS_TZ
from fablabot.helpers.reaction_log import ReactionLogManager, ReactionScope
from fablabot.models import ReactionAction, ReactionEvent

GUILD_A = 111111111111111111
GUILD_B = 222222222222222222
MESSAGE = 333333333333333333
USER = 444444444444444444
RETENTION = timedelta(days=30)
logger = logging.getLogger(__name__)


def _log(scope: ReactionScope = "formation") -> ReactionLogManager:
    """Build the manager of one scope."""
    return ReactionLogManager(scope, retention=RETENTION, logger=logger)


def _event(ts_iso: str, *, user_id: int = USER, user_name: str | None = "alice", message_id: int = MESSAGE) -> ReactionEvent:
    """Build an added reaction."""
    return ReactionEvent(
        message_id=message_id, user_id=user_id, user_name=user_name, emoji="🔧", action=ReactionAction.ADD, ts_iso=ts_iso
    )


def _days_ago(days: float) -> str:
    """Write a time ``days`` ago, as the bot writes it."""
    return (datetime.now(PARIS_TZ) - timedelta(days=days)).isoformat(timespec="seconds")


def _count() -> int:
    """Count the stored events."""
    return db.connection().execute("SELECT count(*) FROM reaction_event").fetchone()[0]


def test_every_field_round_trips() -> None:
    """What goes in comes out the same."""
    event = ReactionEvent(
        message_id=MESSAGE, user_id=USER, user_name=None, emoji="<:kiwi:42>", action=ReactionAction.REMOVE, ts_iso=_days_ago(1)
    )

    _log().append(GUILD_A, event)

    assert _log().history(GUILD_A, MESSAGE) == [event]


def test_history_follows_time_then_arrival() -> None:
    """Events sort by their time, and two in the same second keep their order of arrival."""
    same = _days_ago(1)
    log = _log()
    log.append(GUILD_A, _event(_days_ago(2), user_id=3))
    log.append(GUILD_A, _event(same, user_id=1))
    log.append(GUILD_A, _event(same, user_id=2))
    log.append(GUILD_A, _event(_days_ago(3), user_id=4))

    assert [event.user_id for event in log.history(GUILD_A, MESSAGE)] == [4, 3, 1, 2]


def test_scopes_and_servers_stay_apart() -> None:
    """A training's history is not a tracked message's, and no server reads another's."""
    _log("formation").append(GUILD_A, _event(_days_ago(1)))

    assert _log("message").history(GUILD_A, MESSAGE) == []
    assert _log("formation").history(GUILD_B, MESSAGE) == []


def test_a_name_is_filled_from_the_last_one_known() -> None:
    """A reaction without a name takes this server's last known name for that member."""
    log = _log()
    log.append(GUILD_A, _event(_days_ago(3), user_name="old"))
    log.append(GUILD_A, _event(_days_ago(2), user_name="new"))
    log.append(GUILD_B, _event(_days_ago(1), user_name="elsewhere"))

    log.append(GUILD_A, _event(_days_ago(1), user_name=None))

    assert log.history(GUILD_A, MESSAGE)[-1].user_name == "new"


def test_purge_forgets_what_is_past_retention_only() -> None:
    """Older than 30 days goes; younger stays, in this scope and on every server."""
    log = _log()
    log.append(GUILD_A, _event(_days_ago(29)))
    log.append(GUILD_B, _event(_days_ago(31)))

    log.purge_all()

    assert len(log.history(GUILD_A, MESSAGE)) == 1
    assert log.history(GUILD_B, MESSAGE) == []


def test_purge_leaves_the_other_scope() -> None:
    """Each scope purges its own history."""
    _log("message").append(GUILD_A, _event(_days_ago(31)))

    _log("formation").purge_all()

    assert _count() == 1


def test_purge_reads_the_offset_of_each_date() -> None:
    """Two dates either side of the cutoff sort by their instant, not by their text."""
    now = datetime.now(PARIS_TZ)
    kept = (now - RETENTION + timedelta(minutes=30)).astimezone(ZoneInfo("America/New_York")).isoformat(timespec="seconds")
    gone = (now - RETENTION - timedelta(minutes=30)).astimezone(ZoneInfo("Asia/Tokyo")).isoformat(timespec="seconds")
    log = _log()
    log.append(GUILD_A, _event(kept))
    log.append(GUILD_A, _event(gone))

    log.purge_all()

    assert [event.ts_iso for event in log.history(GUILD_A, MESSAGE)] == [kept]


@pytest.mark.parametrize("ts_iso", ["", "soon"], ids=["empty", "unreadable"])
def test_a_date_that_cannot_be_read_stays(ts_iso: str) -> None:
    """Purging never removes what it cannot date."""
    log = _log()
    log.append(GUILD_A, _event(ts_iso))

    log.purge_all()

    assert _count() == 1


def test_an_id_too_large_for_the_database_has_no_history() -> None:
    """A member can type any number as a message ID: it reads as an empty history, not an error."""
    assert _log().history(GUILD_A, 10**30) == []
    assert _log().history(GUILD_A, -(10**30)) == []
