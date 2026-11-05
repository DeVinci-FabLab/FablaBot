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
from fablabot.helpers.constants import PARIS_TZ, RETENTION
from fablabot.helpers.retention import apply_retention, old_copies, old_log_files
from fablabot.models import FmMessageDraft, Formation, PublishedMessage
from fablabot.models.attendance import AlertAction
from fablabot.services import attendance, formations, retention

GUILD = 111
NOW = datetime(2029, 11, 20, 4, 0, tzinfo=PARIS_TZ)
NAME = "member_secret_name"


def _user(user_id: int) -> dict[str, object]:
    """A registration entry with a recognisable name."""
    return {"user_id": user_id, "username": f"{NAME}{user_id}", "ts_iso": "2026-11-01T10:00:00+01:00"}


def _publish(
    message_id: int, *starts: datetime, registered: tuple[int, ...] = (1, 2, 3), waitlisted: tuple[int, ...] = (9,)
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
    formations.save_published(GUILD, PublishedMessage(message_id, 42, FmMessageDraft("", 0, "", fms, "")))


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


def test_a_message_whose_trainings_close_on_different_days_is_summarised_in_two_runs() -> None:
    """The later training gets its counts when its own check closes, so the message can go in the end."""
    _publish(1, NOW - RETENTION - timedelta(days=20), NOW - RETENTION - timedelta(days=5))

    assert retention.summarise(NOW - RETENTION - timedelta(days=10)) == 1
    assert retention.summarise(NOW) == 1
    assert [row[1] for row in _summaries()] == [0, 1]
    assert retention.forget(NOW - RETENTION) == 1


def test_a_check_still_open_is_not_summarised() -> None:
    """A second before the close, nothing is written."""
    _publish(1, NOW - timedelta(days=7) + timedelta(seconds=1))

    assert retention.summarise(NOW) == 0


@pytest.mark.parametrize(
    ("answer", "saved", "outcome"),
    [(None, False, "not_done"), (AlertAction.IGNORE, False, "not_done"), (AlertAction.CANCEL, False, "cancelled")],
)
def test_a_check_never_saved_counts_as_not_done_or_cancelled(answer: AlertAction | None, saved: bool, outcome: str) -> None:
    """No saved attendance gives no present or absent count, and the outcome says why."""
    start = NOW - timedelta(days=8)
    _publish(1, start)
    appel_id = attendance.open_appel(GUILD, 1, 0, trainer_id=2222, starts_at=start, ends_at=start + timedelta(hours=2))
    if answer:
        attendance.handle(appel_id, 4444, answer, start)
    assert not saved

    retention.summarise(NOW)

    assert _summaries()[0][-3:] == (0, 0, outcome)


def test_a_training_without_check_counts_as_not_asked() -> None:
    """A training of before the feature, or whose start notice never went out, has no attendance."""
    _publish(1, NOW - timedelta(days=30))

    retention.summarise(NOW)

    assert _summaries()[0][-3:] == (0, 0, "not_asked")


def test_three_years_after_the_start_the_personal_rows_go_and_the_counts_stay() -> None:
    """Registrations, waitlist, check and marks go with their message; the summary has no member or trainer."""
    old = NOW - RETENTION - timedelta(days=1)
    _publish(1, old)
    appel_id = attendance.open_appel(GUILD, 1, 0, trainer_id=2222, starts_at=old, ends_at=old + timedelta(hours=2))
    attendance.mark(appel_id, [1], [1])
    _publish(2, NOW - timedelta(days=30))

    retention.summarise(NOW)
    assert retention.forget(NOW - RETENTION) == 1

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
    _publish(1, NOW - RETENTION - timedelta(days=10), NOW - RETENTION + timedelta(days=1))

    retention.summarise(NOW)

    assert retention.forget(NOW - RETENTION) == 0


def test_a_training_whose_start_cannot_be_read_keeps_its_message() -> None:
    """Nothing is deleted on a date SQLite cannot read, and nothing is summarised from it."""
    _publish(1, NOW - RETENTION - timedelta(days=10))
    with db.transaction() as conn:
        conn.execute("UPDATE formation SET start_iso = 'soon'")

    assert retention.summarise(NOW) == 0
    assert retention.forget(NOW - RETENTION) == 0


def test_a_draft_is_never_deleted() -> None:
    """A draft is not published: even with an old training, it stays."""
    old = Formation(
        emoji="🔧", name="x", trainer_mention="<@2222>", start_iso="2020-01-01T10:00:00+01:00", duration="1h", seats=1
    )
    formations.save_draft(GUILD, FmMessageDraft("", 0, "", [old], ""))

    retention.forget(NOW)

    assert formations.get_draft(GUILD) is not None


def test_an_empty_draft_is_never_deleted() -> None:
    """A draft with no training yet stays too."""
    formations.save_draft(GUILD, FmMessageDraft("", 0, "", [], ""))

    assert retention.forget(NOW) == 0

    assert formations.get_draft(GUILD) is not None


def test_a_training_without_its_counts_keeps_its_message() -> None:
    """Nothing personal goes before its anonymous counts are written."""
    _publish(1, NOW - RETENTION - timedelta(days=10))

    assert retention.forget(NOW - RETENTION) == 0


def test_log_files_of_a_day_older_than_three_years_go() -> None:
    """Shared and per-server files go by the day in their name; other files stay."""
    cutoff = (NOW - RETENTION).date()
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
    cutoff = NOW - RETENTION
    stamp = (cutoff - timedelta(seconds=1)).astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    young = cutoff.astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    old = [folder / f"fablabot-v1-{stamp}.db", folder / f"fablabot-0.12.2-{stamp}.db"]
    for path in [*old, folder / f"fablabot-v1-{young}.db", folder / "fablabot-v1.partial", folder / "kept.db"]:
        path.write_bytes(b"")

    assert old_copies(folder, cutoff) == sorted(old)


def test_a_run_does_everything_and_names_nobody(caplog: pytest.LogCaptureFixture) -> None:
    """One run summarises, deletes rows, history, logs and copies, and logs counts only."""
    caplog.set_level(logging.DEBUG)
    old = NOW - RETENTION - timedelta(days=1)
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
        try:
            assert not old.exists()
            assert simcord_bot.retention.is_running()
            assert simcord_bot.retention.hours == 24
        finally:
            simcord_bot.retention.cancel()
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
        try:
            assert simcord_bot.retention.is_running()
            assert "Retention stopped by a storage failure" in caplog.text
        finally:
            simcord_bot.retention.cancel()
            for name in list(simcord_bot.cogs):
                await simcord_bot.remove_cog(name)
