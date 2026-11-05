"""Retention: anonymous counts once a check closes; registrations, checks, history, logs and copies gone after three years."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import logging
from pathlib import Path
import sqlite3
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from fablabot import db
from fablabot.helpers.constants import ANONYMOUS_MIN_REGISTERED, PARIS_TZ, retention_cutoff
from fablabot.helpers.retention import apply_retention, old_copies, old_log_files
from fablabot.models import FmMessageDraft, Formation, PublishedMessage
from fablabot.models.attendance import AlertAction
from fablabot.services import attendance, formations, retention

GUILD = 111
OTHER_GUILD = 222
NOW = datetime(2029, 11, 20, 4, 0, tzinfo=PARIS_TZ)
CUTOFF = retention_cutoff(NOW)
NAME = "member_secret_name"


def _user(user_id: int) -> dict[str, object]:
    """A registration entry with a recognisable name."""
    return {"user_id": user_id, "username": f"{NAME}{user_id}", "ts_iso": "2026-11-01T10:00:00+01:00"}


def _publish(
    message_id: int,
    *starts: datetime,
    registered: tuple[int, ...] = (1, 2, 3),
    waitlisted: tuple[int, ...] = (9,),
    guild: int = GUILD,
) -> None:
    """Publish one message with a training per start, each with the same members."""
    fms = [
        Formation(
            emoji=emoji,
            name=f"Training {index}",
            trainer_mention="<@2222>",
            start_iso=start.isoformat(),
            duration="2h",
            seats=len(registered),
            registered_users=[_user(user_id) for user_id in registered],
            waitlisted_users=[_user(user_id) for user_id in waitlisted],
        )
        for index, (emoji, start) in enumerate(zip(("🔧", "🪚", "🖨️"), starts, strict=False))
    ]
    formations.save_published(guild, PublishedMessage(message_id, 42, FmMessageDraft("", 0, "", fms, "")))


def _react(message_id: int, emoji: str, user_id: int, action: str, at: datetime) -> None:
    """Log one reaction on a training message."""
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO reaction_event (scope, guild_id, message_id, user_id, user_name, emoji, action, ts_iso) "
            "VALUES ('formation', ?, ?, ?, ?, ?, ?, ?)",
            (GUILD, message_id, user_id, NAME, emoji, action, at.isoformat()),
        )


def _react_in_message(at: datetime) -> None:
    """Log one reaction of the other scope, the message management one."""
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO reaction_event (scope, guild_id, message_id, user_id, user_name, emoji, action, ts_iso) "
            "VALUES ('message', ?, 7, 1, ?, '👍', 'add', ?)",
            (GUILD, NAME, at.isoformat()),
        )


def _summaries() -> list[tuple[object, ...]]:
    """Read every summary row, without the server."""
    rows = db.connection().execute("SELECT * FROM training_summary ORDER BY message_id, position").fetchall()
    return [tuple(row)[1:] for row in rows]


def _guild_rows() -> dict[int, tuple[object, ...]]:
    """Read each server's summary as (message, registered, present, absent, outcome)."""
    rows = db.connection().execute(
        "SELECT guild_id, message_id, registered, present, absent, outcome FROM training_summary ORDER BY guild_id"
    )
    return {row["guild_id"]: tuple(row)[1:] for row in rows}


def _count(table: str) -> int:
    """Count the rows of a table."""
    return db.connection().execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def test_a_closed_check_gives_one_anonymous_summary() -> None:
    """Seven days after the start, the counts are written once, with the saved attendance and the withdrawals."""
    start = NOW - timedelta(days=7)
    _publish(1, start)
    appel_id = attendance.open_appel(GUILD, 1, 0, trainer_id=2222, starts_at=start, ends_at=start + timedelta(hours=2))
    attendance.mark(appel_id, [1, 2, 3], [1, 3])
    attendance.validate(appel_id, 2222, start)
    _react(1, "🔧", 5, "add", start - timedelta(days=2))
    _react(1, "🔧", 5, "remove", start - timedelta(days=1))
    _react(1, "🔧", 6, "remove", start - timedelta(days=1))
    _react(1, "🔧", 6, "add", start - timedelta(hours=1))

    assert retention.summarise(NOW) == 1
    assert retention.summarise(NOW) == 0
    assert _summaries() == [(1, 0, "Training 0", start.isoformat(), 3, 1, 1, 2, 1, "done")]


def _check(start: datetime, registered: tuple[int, ...], present: list[int]) -> None:
    """Publish one training, take its check and validate it."""
    _publish(1, start, registered=registered)
    appel_id = attendance.open_appel(GUILD, 1, 0, trainer_id=2222, starts_at=start, ends_at=start + timedelta(hours=2))
    attendance.mark(appel_id, list(registered), present)
    attendance.validate(appel_id, 2222, start)


def test_a_training_below_the_threshold_keeps_no_present_and_absent_counts() -> None:
    """With fewer registered members than the threshold the counts could point at a person: they stay empty, the rest stays."""
    start = NOW - timedelta(days=7)
    _check(start, registered=(1, 2), present=[1])
    _react(1, "🔧", 5, "add", start - timedelta(days=2))
    _react(1, "🔧", 5, "remove", start - timedelta(days=1))

    assert retention.summarise(NOW) == 1

    assert _summaries() == [(1, 0, "Training 0", start.isoformat(), 2, 1, 1, None, None, "done")]


def test_a_training_at_the_threshold_keeps_its_counts() -> None:
    """The threshold is the smallest number of registered members whose counts are kept."""
    start = NOW - timedelta(days=7)
    members = tuple(range(1, ANONYMOUS_MIN_REGISTERED + 1))
    _check(start, registered=members, present=[1])

    assert retention.summarise(NOW) == 1

    assert _summaries() == [(1, 0, "Training 0", start.isoformat(), 3, 1, 0, 1, 2, "done")]
    assert ANONYMOUS_MIN_REGISTERED == 3


def test_a_check_validated_on_fewer_members_than_the_threshold_keeps_no_counts() -> None:
    """Members registered after the validation do not make a small check's counts safe to keep."""
    start = NOW - timedelta(days=7)
    _check(start, registered=(1, 2, 3), present=[1])
    with db.transaction() as conn:
        conn.execute("UPDATE appel SET present_count = 1, absent_count = 1")

    assert retention.summarise(NOW) == 1

    assert _summaries()[0][7:9] == (None, None)


def test_a_training_published_again_is_counted_once_on_its_newest_copy() -> None:
    """The older copy is skipped: one real training gives one row, with the newest copy's registrations and the check."""
    start = NOW - timedelta(days=7)
    _publish(1, start)
    _publish(2, start, registered=(3, 4, 5, 6))
    appel_id = attendance.open_appel(GUILD, 2, 0, trainer_id=2222, starts_at=start, ends_at=start + timedelta(hours=2))
    attendance.mark(appel_id, [3, 4, 5, 6], [3, 4])
    attendance.validate(appel_id, 2222, start)

    assert retention.summarise(NOW) == 1

    assert _summaries() == [(2, 0, "Training 0", start.isoformat(), 4, 1, 0, 2, 2, "done")]


def test_the_check_held_by_the_older_copy_is_kept_on_the_newest_row() -> None:
    """The start check was opened on the first message: the one row of the training still says done."""
    start = NOW - timedelta(days=7)
    _publish(1, start)
    _publish(2, start, registered=(3, 4, 5))
    appel_id = attendance.open_appel(GUILD, 1, 0, trainer_id=2222, starts_at=start, ends_at=start + timedelta(hours=2))
    attendance.mark(appel_id, [1, 2, 3], [1, 2])
    attendance.validate(appel_id, 2222, start)

    retention.summarise(NOW)

    assert _summaries() == [(2, 0, "Training 0", start.isoformat(), 3, 1, 0, 2, 1, "done")]


def test_both_copies_of_a_training_published_again_go_after_three_years() -> None:
    """The older copy has no row of its own, and its registrations still go with the newest copy's."""
    old = CUTOFF - timedelta(days=1)
    _publish(1, old)
    _publish(2, old)

    retention.summarise(NOW)

    assert retention.forget(CUTOFF) == 2
    assert _count("registration") == 0


def test_a_training_published_again_after_its_counts_were_written_is_not_counted_twice() -> None:
    """The row written for the first copy stays the only one, though a later announcement holds the training again."""
    start = NOW - timedelta(days=10)
    _publish(1, start)
    appel_id = attendance.open_appel(GUILD, 1, 0, trainer_id=2222, starts_at=start, ends_at=start + timedelta(hours=2))
    attendance.mark(appel_id, [1, 2, 3], [1, 2])
    attendance.validate(appel_id, 2222, start)
    assert retention.summarise(NOW) == 1

    _publish(2, start, registered=())

    assert retention.summarise(NOW + timedelta(days=1)) == 0
    assert _summaries() == [(1, 0, "Training 0", start.isoformat(), 3, 1, 0, 2, 1, "done")]
    month = db.connection().execute("SELECT trainings, present, absent FROM training_month").fetchall()
    assert [tuple(row) for row in month] == [(1, 2, 1)]


def test_the_later_copy_of_a_counted_training_goes_with_the_first_after_three_years() -> None:
    """A copy published after the counts were written needs no row of its own to be deleted."""
    old = CUTOFF - timedelta(days=1)
    _publish(1, old)
    retention.summarise(NOW)
    _publish(2, old, registered=())

    assert retention.summarise(NOW) == 0
    assert retention.forget(CUTOFF) == 2


def test_a_training_counted_before_its_other_copy_goes_is_not_counted_again() -> None:
    """The newest copy goes first, its older copy stays for a younger training, and the next run adds no second row."""
    a_start, b_start = CUTOFF - timedelta(days=30), NOW - timedelta(days=30)
    _publish(1, a_start, b_start)
    _publish(2, a_start)

    assert retention.summarise(NOW) == 2
    assert retention.forget(CUTOFF) == 1

    assert retention.summarise(NOW) == 0
    assert [row[:3] for row in _summaries()] == [(1, 1, "Training 1"), (2, 0, "Training 0")]
    assert retention.forget(NOW) == 1
    assert _count("registration") == 0
    assert _count("training_summary") == 2


def test_a_training_renamed_after_its_counts_were_written_is_not_counted_again() -> None:
    """The row is found by the training's place in its message too, so an edit of the name leaves one row."""
    _publish(1, NOW - timedelta(days=30))
    retention.summarise(NOW)
    with db.transaction() as conn:
        conn.execute("UPDATE formation SET name = 'Renamed'")

    assert retention.summarise(NOW) == 0
    assert _count("training_summary") == 1
    assert retention.forget(NOW) == 1


def test_a_later_session_of_a_recurring_training_gets_its_own_row() -> None:
    """The same name on the same server at another start is another session: counted apart from the first."""
    _publish(1, NOW - timedelta(days=30))
    assert retention.summarise(NOW - timedelta(days=15)) == 1
    _publish(2, NOW - timedelta(days=20))

    assert retention.summarise(NOW) == 1

    assert [row[:3] for row in _summaries()] == [(1, 0, "Training 0"), (2, 0, "Training 0")]


def test_a_later_session_keeps_its_message_until_it_has_its_own_row() -> None:
    """The first session's counts do not stand for the next one of the same name: its registrations stay."""
    first, second = CUTOFF - timedelta(days=30), CUTOFF - timedelta(days=20)
    _publish(1, first)
    assert retention.summarise(NOW) == 1
    _publish(2, second)

    assert retention.forget(CUTOFF) == 1

    assert _count("registration") == 4
    assert retention.summarise(NOW) == 1
    assert retention.forget(CUTOFF) == 1
    assert _count("registration") == 0


def test_a_training_of_another_server_is_not_a_copy_of_this_one() -> None:
    """The same training published on a second server still gets its own row on each."""
    start = NOW - timedelta(days=30)
    _publish(1, start)
    _publish(2, start, guild=OTHER_GUILD)

    assert retention.summarise(NOW) == 2

    assert _guild_rows() == {GUILD: (1, 3, 0, 0, "not_asked"), OTHER_GUILD: (2, 3, 0, 0, "not_asked")}


def test_the_counts_of_another_server_do_not_stand_for_this_one() -> None:
    """A row written for one server never covers the same training of another: no skip, no deletion without counts."""
    old = CUTOFF - timedelta(days=1)
    _publish(1, old)
    retention.summarise(NOW)
    _publish(2, old, guild=OTHER_GUILD)

    assert retention.forget(CUTOFF) == 1

    assert retention.summarise(NOW) == 1
    assert _guild_rows() == {GUILD: (1, 3, 0, 0, "not_asked"), OTHER_GUILD: (2, 3, 0, 0, "not_asked")}


def test_a_message_is_not_deleted_for_a_copy_on_another_server() -> None:
    """Without its own counts, a training is kept, though another server published the same one later."""
    old = CUTOFF - timedelta(days=1)
    _publish(1, old)
    _publish(2, old, guild=OTHER_GUILD)

    assert retention.forget(CUTOFF) == 0


def test_each_server_summary_takes_the_outcome_of_its_own_check() -> None:
    """The same training checked on two servers: each row reads the check of its own server."""
    start = NOW - timedelta(days=30)
    _publish(1, start, guild=OTHER_GUILD)
    _publish(2, start)
    other = attendance.open_appel(OTHER_GUILD, 1, 0, trainer_id=2222, starts_at=start, ends_at=start + timedelta(hours=2))
    attendance.handle(other, 4444, AlertAction.CANCEL, start)
    own = attendance.open_appel(GUILD, 2, 0, trainer_id=2222, starts_at=start, ends_at=start + timedelta(hours=2))
    attendance.mark(own, [1, 2, 3], [1, 2])
    attendance.validate(own, 2222, start)

    assert retention.summarise(NOW) == 2

    assert _guild_rows() == {GUILD: (2, 3, 2, 1, "done"), OTHER_GUILD: (1, 3, 0, 0, "cancelled")}


def test_a_message_whose_trainings_close_on_different_days_is_summarised_in_two_runs() -> None:
    """The later training gets its counts when its own check closes, so the message can go in the end."""
    _publish(1, CUTOFF - timedelta(days=20), CUTOFF - timedelta(days=5))

    assert retention.summarise(CUTOFF - timedelta(days=10)) == 1
    assert retention.summarise(NOW) == 1
    assert [row[1] for row in _summaries()] == [0, 1]
    assert retention.forget(CUTOFF) == 1


def test_a_check_still_open_is_not_summarised() -> None:
    """A second before the close, nothing is written."""
    _publish(1, NOW - timedelta(days=7) + timedelta(seconds=1))

    assert retention.summarise(NOW) == 0


@pytest.mark.parametrize(
    ("answer", "outcome"),
    [(None, "not_done"), (AlertAction.IGNORE, "not_done"), (AlertAction.CANCEL, "cancelled")],
)
def test_a_check_never_saved_counts_as_not_done_or_cancelled(answer: AlertAction | None, outcome: str) -> None:
    """No saved attendance gives no present or absent count, and the outcome says why."""
    start = NOW - timedelta(days=8)
    _publish(1, start)
    appel_id = attendance.open_appel(GUILD, 1, 0, trainer_id=2222, starts_at=start, ends_at=start + timedelta(hours=2))
    if answer:
        attendance.handle(appel_id, 4444, answer, start)

    retention.summarise(NOW)

    assert _summaries()[0][-3:] == (0, 0, outcome)


def test_a_training_without_check_counts_as_not_asked() -> None:
    """A training of before the feature, or whose start notice never went out, has no attendance."""
    _publish(1, NOW - timedelta(days=30))

    retention.summarise(NOW)

    assert _summaries()[0][-3:] == (0, 0, "not_asked")


def test_three_years_after_the_start_the_personal_rows_go_and_the_counts_stay() -> None:
    """Registrations, waitlist, check and marks go with their message; the summary has no member or trainer."""
    old = CUTOFF - timedelta(days=1)
    _publish(1, old)
    appel_id = attendance.open_appel(GUILD, 1, 0, trainer_id=2222, starts_at=old, ends_at=old + timedelta(hours=2))
    attendance.mark(appel_id, [1], [1])
    _publish(2, NOW - timedelta(days=30))

    retention.summarise(NOW)
    assert retention.forget(CUTOFF) == 1

    assert [row[0] for row in _summaries()] == [1, 2]
    assert [_count(t) for t in ("appel", "appel_present")] == [0, 0]
    assert formations.current_published(GUILD) is not None
    users = {row[0] for row in db.connection().execute("SELECT user_id FROM registration")}
    assert users == {1, 2, 3, 9}
    assert _count("registration") == 4
    dump = "\n".join(db.connection().iterdump())
    assert "2222" not in dump.split("CREATE TABLE training_summary", 1)[1].split("CREATE", 1)[0]


def test_a_message_waits_for_its_youngest_training() -> None:
    """A message is kept while one of its trainings is younger than three years."""
    _publish(1, CUTOFF - timedelta(days=10), CUTOFF + timedelta(days=1))

    retention.summarise(NOW)

    assert retention.forget(CUTOFF) == 0


def test_a_training_whose_start_cannot_be_read_keeps_its_message() -> None:
    """Nothing is deleted on a date SQLite cannot read, and nothing is summarised from it."""
    _publish(1, CUTOFF - timedelta(days=10))
    with db.transaction() as conn:
        conn.execute("UPDATE formation SET start_iso = 'soon'")

    assert retention.summarise(NOW) == 0
    assert retention.forget(CUTOFF) == 0


def test_a_draft_is_never_deleted() -> None:
    """A draft is not published: even with an old training, it stays."""
    old = Formation(
        emoji="🔧", name="x", trainer_mention="<@2222>", start_iso="2020-01-01T10:00:00+01:00", duration="1h", seats=1
    )
    formations.save_draft(GUILD, FmMessageDraft("", 0, "", [old], ""))

    retention.forget(NOW)

    assert formations.get_draft(GUILD) is not None


def test_a_draft_holding_a_published_training_is_never_deleted() -> None:
    """Publishing leaves the draft as it was: its training matches the counted one, and the draft still stays."""
    old = CUTOFF - timedelta(days=1)
    _publish(1, old)
    draft_training = Formation(
        emoji="🔧", name="Training 0", trainer_mention="<@2222>", start_iso=old.isoformat(), duration="2h", seats=3
    )
    formations.save_draft(GUILD, FmMessageDraft("My header", 0, "My intro", [draft_training], "My end"))
    assert retention.summarise(NOW) == 1

    assert retention.forget(CUTOFF) == 1

    draft = formations.get_draft(GUILD)
    assert draft is not None
    assert (draft.header, draft.intro, draft.end, len(draft.fms)) == ("My header", "My intro", "My end", 1)


def test_an_empty_draft_is_never_deleted() -> None:
    """A draft with no training yet stays too."""
    formations.save_draft(GUILD, FmMessageDraft("", 0, "", [], ""))

    assert retention.forget(NOW) == 0

    assert formations.get_draft(GUILD) is not None


def test_a_training_without_its_counts_keeps_its_message() -> None:
    """Nothing personal goes before its anonymous counts are written."""
    _publish(1, CUTOFF - timedelta(days=10))

    assert retention.forget(CUTOFF) == 0


def test_log_files_of_a_day_older_than_three_years_go() -> None:
    """Shared and per-server files go by the day in their name; other files stay."""
    cutoff = (CUTOFF).date()
    logs = Path("logs")
    old = [logs / f"{cutoff - timedelta(days=1)}.log", logs / "guilds" / "111" / f"{cutoff - timedelta(days=1)}.log"]
    kept = [logs / f"{cutoff}.log", logs / "guilds" / "111" / f"{cutoff}.log", logs / "notes.txt", logs / "2019-13-45.log"]
    for path in old + kept:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")

    assert old_log_files(logs, cutoff) == sorted(old)


def test_database_copies_older_than_three_years_go() -> None:
    """A copy goes by the time in its name, whatever its label; other files stay."""
    folder = Path("backups")
    folder.mkdir()
    cutoff = CUTOFF
    stamp = (cutoff - timedelta(seconds=1)).astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    young = cutoff.astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    old = [folder / f"fablabot-v1-{stamp}.db", folder / f"fablabot-0.12.2-{stamp}.db"]
    for path in [*old, folder / f"fablabot-v1-{young}.db", folder / "fablabot-v1.partial", folder / "kept.db"]:
        path.write_bytes(b"")

    assert old_copies(folder, cutoff) == sorted(old)


def test_a_copy_with_an_impossible_date_is_kept_and_named_in_the_log(caplog: pytest.LogCaptureFixture) -> None:
    """A name shaped like a copy but holding a date that does not exist is left alone, and the run goes on."""
    folder = Path("backups")
    folder.mkdir()
    cutoff = CUTOFF
    stamp = (cutoff - timedelta(seconds=1)).astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    odd = folder / "fablabot-v1-20261399T000000000000Z.db"
    old = folder / f"fablabot-v1-{stamp}.db"
    for path in (odd, old):
        path.write_bytes(b"")

    assert old_copies(folder, cutoff) == [old]
    assert odd.name in caplog.text


@pytest.mark.parametrize(
    ("now", "cutoff"),
    [
        (datetime(2029, 11, 20, 4, 0, tzinfo=PARIS_TZ), datetime(2026, 11, 20, 4, 0, tzinfo=PARIS_TZ)),
        (datetime(2031, 3, 1, 9, 30, tzinfo=PARIS_TZ), datetime(2028, 3, 1, 9, 30, tzinfo=PARIS_TZ)),
        (datetime(2031, 2, 28, 9, 30, tzinfo=PARIS_TZ), datetime(2028, 2, 28, 9, 30, tzinfo=PARIS_TZ)),
        (datetime(2028, 2, 29, 23, 59, tzinfo=PARIS_TZ), datetime(2025, 2, 28, 23, 59, tzinfo=PARIS_TZ)),
        (datetime(2032, 2, 29, 0, 0, tzinfo=UTC), datetime(2029, 2, 28, 0, 0, tzinfo=UTC)),
    ],
    ids=["plain", "into-a-leap-year", "28-february", "29-february", "29-february-utc"],
)
def test_the_cutoff_is_the_same_instant_three_calendar_years_back(now: datetime, cutoff: datetime) -> None:
    """Three years by the calendar, not 1095 days; 29 February reads 28 February; the time and the zone stay."""
    assert retention_cutoff(now) == cutoff
    assert retention_cutoff(now).tzinfo is now.tzinfo


def test_every_purge_goes_by_the_calendar_cutoff() -> None:
    """A training, a reaction, a log file and a copy 12 hours inside three years stay, and 12 hours outside go.

    NOW is 20 November 2029 at 04:00; 1095 days back lands on 21 November 2026, since 2028 has a 29 February.
    """
    inside, outside = datetime(2026, 11, 20, 16, 0, tzinfo=PARIS_TZ), datetime(2026, 11, 19, 16, 0, tzinfo=PARIS_TZ)
    _publish(1, inside)
    _publish(2, outside)
    _react(1, "🔧", 1, "add", inside)
    _react(2, "🔧", 1, "add", outside)
    logs, backups = Path("logs"), Path("backups")
    logs.mkdir()
    backups.mkdir()
    for moment in (inside, outside):
        (logs / f"{moment.date()}.log").write_text("x", encoding="utf-8")
        (backups / f"fablabot-v1-{moment.astimezone(UTC):%Y%m%dT%H%M%S%fZ}.db").write_bytes(b"")

    report = apply_retention(NOW, log_dir=logs, backups=backups)

    assert (report.messages, report.reactions, report.log_files, report.copies) == (1, 1, 1, 1)
    assert [row["message_id"] for row in db.connection().execute("SELECT message_id FROM fm_message")] == [1]
    assert [row["message_id"] for row in db.connection().execute("SELECT message_id FROM reaction_event")] == [1]
    assert [path.name for path in logs.iterdir()] == [f"{inside.date()}.log"]
    assert len(list(backups.iterdir())) == 1


def test_a_run_does_everything_and_names_nobody(caplog: pytest.LogCaptureFixture) -> None:
    """One run summarises, deletes rows, history, logs and copies, and logs counts only."""
    caplog.set_level(logging.DEBUG)
    old = CUTOFF - timedelta(days=1)
    _publish(1, old)
    _react(1, "🔧", 1, "add", old - timedelta(days=1))
    _react_in_message(old - timedelta(days=1))
    old_draft = Formation(emoji="🔧", name="x", trainer_mention="<@2222>", start_iso=old.isoformat(), duration="1h", seats=1)
    formations.save_draft(GUILD, FmMessageDraft("", 0, "", [old_draft], ""))
    logs, backups = Path("logs"), Path("backups")
    (logs / "guilds" / "111").mkdir(parents=True)
    (logs / "guilds" / "111" / f"{old.date()}.log").write_text(NAME, encoding="utf-8")
    backups.mkdir()
    (backups / f"fablabot-v1-{old.astimezone(UTC):%Y%m%dT%H%M%S%fZ}.db").write_bytes(b"")

    report = apply_retention(NOW, log_dir=logs, backups=backups)

    assert (report.summarised, report.messages, report.log_files, report.copies) == (1, 1, 1, 1)
    assert [_count(t) for t in ("registration", "reaction_event", "fm_message")] == [0, 0, 1]
    assert formations.get_draft(GUILD) is not None
    assert list(logs.rglob("*.log")) == []
    assert list(backups.iterdir()) == []
    assert NAME not in caplog.text
    assert "1 trainings summarised" in caplog.text


async def test_the_bot_applies_the_retention_at_start_then_daily(simcord_bot: Any) -> None:
    """The start runs it at once, before any wait, and then it repeats every 24 hours."""
    old = Path("logs") / "2020-01-01.log"
    old.parent.mkdir()
    old.write_text(NAME, encoding="utf-8")
    simcord_bot.tree.sync = AsyncMock(return_value=[])

    async with simcord_bot:
        await simcord_bot.setup_hook()
        await asyncio.sleep(0.05)
        assert not old.exists()
        assert simcord_bot.retention.is_running()
        assert simcord_bot.retention.hours == 24
        for name in list(simcord_bot.cogs):
            await simcord_bot.remove_cog(name)


async def test_a_storage_failure_in_a_run_is_logged_and_the_loop_goes_on(
    simcord_bot: Any, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A step the database refuses does not end the daily loop: it is logged and the next run tries again."""
    monkeypatch.setattr("fablabot.main.apply_retention", Mock(side_effect=sqlite3.OperationalError("disk full")))
    simcord_bot.tree.sync = AsyncMock(return_value=[])

    async with simcord_bot:
        await simcord_bot.setup_hook()
        await asyncio.sleep(0.05)
        assert simcord_bot.retention.is_running()
        assert "Retention stopped by a storage failure" in caplog.text
        for name in list(simcord_bot.cogs):
            await simcord_bot.remove_cog(name)


async def test_any_other_failure_in_a_run_is_logged_and_the_loop_goes_on(
    simcord_bot: Any, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """An error that is not the database's does not end the daily loop either."""
    monkeypatch.setattr("fablabot.main.apply_retention", Mock(side_effect=RuntimeError("unexpected")))
    simcord_bot.tree.sync = AsyncMock(return_value=[])

    async with simcord_bot:
        await simcord_bot.setup_hook()
        await asyncio.sleep(0.05)
        assert simcord_bot.retention.is_running()
        assert "Retention stopped by an unexpected failure" in caplog.text
        for name in list(simcord_bot.cogs):
            await simcord_bot.remove_cog(name)


async def test_closing_the_bot_cancels_the_retention_loop(simcord_bot: Any) -> None:
    """The daily loop does not outlive the bot."""
    simcord_bot.tree.sync = AsyncMock(return_value=[])

    async with simcord_bot:
        await simcord_bot.setup_hook()
        await asyncio.sleep(0.05)
        assert simcord_bot.retention.is_running()
        for name in list(simcord_bot.cogs):
            await simcord_bot.remove_cog(name)

    assert not simcord_bot.retention.is_running()
