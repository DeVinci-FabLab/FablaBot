"""The attendance checks and the anonymous training counts live in the database, apart from the training rows."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import sqlite3

import pytest

from fablabot import db
from fablabot.helpers.constants import APPEL_EDIT_WINDOW, PARIS_TZ
from fablabot.models import AlertAction, Registrant
from fablabot.models.formation import FmMessageDraft, Formation, PublishedMessage
from fablabot.services import attendance, formations

GUILD = 111
MESSAGE = 5000
CHANNEL = 42
START = datetime(2026, 11, 5, 14, 0, tzinfo=PARIS_TZ)
END = START + timedelta(hours=2)


def _user(user_id: int) -> dict[str, object]:
    """Build the stored form of a registered member."""
    return {"user_id": user_id, "username": f"user{user_id}", "ts_iso": START.isoformat()}


def _publish(*, registered: tuple[int, ...] = (1, 2, 3), waitlisted: tuple[int, ...] = (9,), message_id: int = MESSAGE) -> None:
    """Publish a message of two trainings."""
    first = Formation(
        emoji="A",
        name="Laser",
        trainer_mention="<@7>",
        start_iso=START.isoformat(),
        duration="2h",
        seats=3,
        registered_users=[_user(i) for i in registered],
        waitlisted_users=[_user(i) for i in waitlisted],
    )
    second = Formation(emoji="B", name="3D", trainer_mention="<@8>", start_iso=START.isoformat(), duration="1h", seats=2)
    draft = FmMessageDraft(header="h", role_id=1, intro="i", fms=[first, second], end="e")
    formations.save_published(GUILD, PublishedMessage(message_id=message_id, channel_id=CHANNEL, message=draft))


def _open(position: int = 0, *, trainer_id: int | None = 7, starts_at: datetime = START, ends_at: datetime = END) -> int:
    """Open the check of one training of the published message."""
    return attendance.open_appel(GUILD, MESSAGE, position, trainer_id=trainer_id, starts_at=starts_at, ends_at=ends_at)


def _count(table: str) -> int:
    """Count the rows of a table."""
    return db.connection().execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_a_check_opens_once_per_training() -> None:
    """Opening the same training twice gives the same check, and it reads its announcement."""
    _publish()
    first = _open()
    second = _open(trainer_id=99)

    assert first == second
    assert _count("appel") == 1
    appel = attendance.get(first)
    assert appel is not None
    assert (appel.guild_id, appel.message_id, appel.channel_id) == (GUILD, MESSAGE, CHANNEL)
    assert (appel.position, appel.name, appel.duration, appel.trainer_id) == (0, "Laser", "2h", 7)
    assert appel.excusable is True
    assert (appel.starts_at, appel.ends_at) == (START, END)
    assert appel.closes_at == START + APPEL_EDIT_WINDOW
    assert appel.validated_at is None
    assert appel.alert_sent is False
    assert attendance.get(first + 100) is None


def test_a_check_needs_a_published_message() -> None:
    """A check for a message the bot never published is refused and stores nothing."""
    with pytest.raises(LookupError):
        _open()

    assert _count("appel") == 0


def test_a_check_needs_timezone_aware_times() -> None:
    """A start or an end without a timezone is refused and stores nothing."""
    _publish()
    naive = START.replace(tzinfo=None)

    with pytest.raises(ValueError, match="timezone"):
        _open(starts_at=naive)
    with pytest.raises(ValueError, match="timezone"):
        _open(ends_at=naive)

    assert _count("appel") == 0


def test_trainings_whose_twin_in_another_message_already_has_a_check_are_listed_by_place() -> None:
    """A training is checked elsewhere when another message of the server has a check for its name, trainer and start."""

    def training(*, name: str = "Laser", trainer: str = "<@7>", at: datetime = START) -> Formation:
        return Formation(emoji="A", name=name, trainer_mention=trainer, start_iso=at.isoformat(), duration="2h", seats=3)

    def publish(message_id: int, *trainings: Formation, guild_id: int = GUILD) -> None:
        draft = FmMessageDraft(header="h", role_id=1, intro="i", fms=list(trainings), end="e")
        formations.save_published(guild_id, PublishedMessage(message_id=message_id, channel_id=CHANNEL, message=draft))

    publish(MESSAGE, training(), training(name="3D"), training(trainer="<@9>"), training(at=START + timedelta(days=1)))
    publish(
        MESSAGE + 1,
        training(at=START.astimezone(UTC)),
        training(name="3D", trainer="<@8>"),
        training(trainer="<@9>"),
        training(name="Soudure"),
    )
    publish(MESSAGE + 2, training(), guild_id=GUILD + 1)
    for position in (0, 1, 3):
        attendance.open_appel(GUILD, MESSAGE, position, trainer_id=7, starts_at=START, ends_at=END)
    attendance.open_appel(GUILD + 1, MESSAGE + 2, 0, trainer_id=7, starts_at=START, ends_at=END)

    assert attendance.checked_elsewhere(GUILD, MESSAGE + 1) == {0}
    assert attendance.checked_elsewhere(GUILD, MESSAGE) == set()  # its own checks do not count
    assert attendance.checked_elsewhere(GUILD + 1, MESSAGE + 2) == set()  # the other server's check is not this server's
    assert attendance.checked_elsewhere(GUILD, MESSAGE + 3) == set()


def test_a_check_cannot_be_opened_on_another_servers_message() -> None:
    """A message published on one server cannot take a check from another server."""
    _publish()

    with pytest.raises(LookupError):
        attendance.open_appel(GUILD + 1, MESSAGE, 0, trainer_id=7, starts_at=START, ends_at=END)

    assert _count("appel") == 0


def test_the_check_and_its_marks_survive_a_new_save_of_the_training() -> None:
    """Saving the message again rebuilds its training rows, and the check, its marks and its counts stay."""
    _publish()
    appel_id = _open()
    attendance.mark(appel_id, [1, 2], [1])
    attendance.validate(appel_id, 7, START)

    _publish(registered=(1, 2, 3, 4))

    appel = attendance.get(appel_id)
    assert appel is not None
    assert appel.name == "Laser"
    assert attendance.present(appel_id) == {1}
    assert (appel.present_count, appel.absent_count, appel.validated_by) == (1, 2, 7)
    assert [r.user_id for r in attendance.registrants(appel_id)] == [1, 2, 3, 4]


def test_registrants_are_the_registered_members_in_order_without_the_waitlist() -> None:
    """The people to call are the registered ones in their order of sign-up, never the waitlist."""
    _publish(registered=(3, 1, 2), waitlisted=(9, 8))
    appel_id = _open()

    assert attendance.registrants(appel_id) == [Registrant(3, "user3"), Registrant(1, "user1"), Registrant(2, "user2")]
    assert attendance.registrants(_open(1)) == []


def test_a_control_sets_only_the_members_it_showed() -> None:
    """A menu that listed some members changes those and leaves the others as they were."""
    _publish(registered=(1, 2, 3, 4))
    appel_id = _open()
    attendance.mark(appel_id, [1, 2, 3, 4], [1, 4])

    attendance.mark(appel_id, [1, 2], [2, 3])

    assert attendance.present(appel_id) == {2, 4}
    attendance.mark(appel_id, [], [])
    assert attendance.present(appel_id) == {2, 4}


def test_validation_counts_registered_members_only() -> None:
    """A member who left the list since the mark is neither present nor absent."""
    _publish(registered=(1, 2, 3))
    appel_id = _open()
    attendance.mark(appel_id, [1, 2, 3], [1, 2])
    _publish(registered=(2, 3))

    saved = attendance.validate(appel_id, 7, START)

    assert (saved.here, saved.away, saved.first) == ([2], [3], True)
    assert (saved.appel.validated_by, saved.appel.present_count, saved.appel.absent_count) == (7, 1, 1)
    appel = attendance.get(appel_id)
    assert appel is not None
    assert (appel.present_count, appel.absent_count) == (1, 1)
    assert (appel.validated_by, appel.validated_at) == (7, START)
    again_saved = attendance.validate(appel_id, 8, START + timedelta(hours=1))
    assert (again_saved.here, again_saved.away, again_saved.first) == ([2], [3], False)
    again = attendance.get(appel_id)
    assert again is not None
    assert (again.validated_by, again.validated_at) == (8, START + timedelta(hours=1))


def test_an_alert_is_due_once_the_slot_is_over_and_until_the_check_closes() -> None:
    """An unmade check is reported from the end of its slot until it closes, and once."""
    _publish()
    appel_id = _open()
    closes = START + APPEL_EDIT_WINDOW

    assert attendance.due_alerts(END - timedelta(seconds=1)) == []
    assert [a.id for a in attendance.due_alerts(END)] == [appel_id]
    assert [a.id for a in attendance.due_alerts(closes - timedelta(seconds=1))] == [appel_id]
    assert attendance.due_alerts(closes) == []

    attendance.claim_alert(appel_id)

    attendance.record_alert(appel_id, [(10, 100), (11, 101)])

    assert attendance.due_alerts(END) == []
    assert attendance.alert_messages(appel_id) == [(10, 100), (11, 101)]
    appel = attendance.get(appel_id)
    assert appel is not None
    assert appel.alert_sent is True


def test_claiming_the_alert_marks_it_sent_before_any_message_is_kept() -> None:
    """The claim alone ends the alert's due state, once, and keeps no message: the IDs are stored after the sends."""
    _publish()
    appel_id = _open()

    assert attendance.claim_alert(appel_id) is True

    appel = attendance.get(appel_id)
    assert appel is not None
    assert appel.alert_sent is True
    assert attendance.due_alerts(END) == []
    assert attendance.alert_messages(appel_id) == []
    assert attendance.claim_alert(appel_id) is False


def test_a_saved_or_answered_check_cannot_be_claimed() -> None:
    """A check saved or answered since it was listed takes no alert: the claim says so and marks nothing."""
    _publish()
    saved, answered = _open(), _open(position=1)
    attendance.validate(saved, 7, END)
    attendance.handle(answered, 5, AlertAction.IGNORE, END)

    assert (attendance.claim_alert(saved), attendance.claim_alert(answered)) == (False, False)
    assert [a.alert_sent for a in (attendance.get(saved), attendance.get(answered)) if a] == [False, False]


def test_a_saved_check_needs_no_alert() -> None:
    """A validated check, or one answered already, raises no alert."""
    _publish()
    saved = _open()
    handled = _open(1)
    attendance.validate(saved, 7, END)
    attendance.handle(handled, 5, AlertAction.IGNORE, END)

    assert attendance.due_alerts(END + timedelta(minutes=1)) == []


def test_the_first_answer_to_an_alert_wins() -> None:
    """Two staff members answering the same alert: the first is kept, the second is told no."""
    _publish()
    appel_id = _open()
    at = END + timedelta(minutes=5)

    assert attendance.handle(appel_id, 5, AlertAction.RELAUNCH, at) is True
    assert attendance.handle(appel_id, 6, AlertAction.CANCEL, at + timedelta(minutes=1)) is False

    appel = attendance.get(appel_id)
    assert appel is not None
    assert (appel.handled_by, appel.handled_at, appel.handled_action) == (5, at, AlertAction.RELAUNCH)
    assert appel.cancelled is False


def test_a_save_closes_the_alert_in_its_own_transaction() -> None:
    """Saving a check whose alert nobody answered records the answer done with the save, and says it closed the alert."""
    _publish()
    appel_id = _open()
    attendance.claim_alert(appel_id)
    attendance.record_alert(appel_id, [(10, 100)])
    at = END + timedelta(minutes=5)

    saved = attendance.validate(appel_id, 7, at)

    assert saved.alert_closed is True
    assert (saved.appel.handled_by, saved.appel.handled_at, saved.appel.handled_action) == (7, at, AlertAction.DONE)


@pytest.mark.parametrize("alerted", [False, True])
def test_a_save_leaves_an_answer_or_a_missing_alert_alone(alerted: bool) -> None:
    """A save records no answer when no alert went out, and keeps the answer an alert already got."""
    _publish()
    appel_id = _open()
    if alerted:
        attendance.claim_alert(appel_id)
        attendance.record_alert(appel_id, [(10, 100)])
        attendance.handle(appel_id, 5, AlertAction.IGNORE, END)

    saved = attendance.validate(appel_id, 7, END + timedelta(minutes=5))

    assert saved.alert_closed is False
    expected = (5, AlertAction.IGNORE) if alerted else (None, None)
    assert (saved.appel.handled_by, saved.appel.handled_action) == expected


@pytest.mark.parametrize("answer", [AlertAction.RELAUNCH, AlertAction.IGNORE, AlertAction.CANCEL])
def test_a_saved_check_takes_no_answer_but_done(answer: AlertAction) -> None:
    """Once the check was saved, no relaunch, ignore or cancel is recorded; only the answer done can be."""
    _publish()
    appel_id = _open()
    attendance.validate(appel_id, 7, END)

    assert attendance.handle(appel_id, 5, answer, END + timedelta(minutes=1)) is False

    appel = attendance.get(appel_id)
    assert appel is not None
    assert appel.handled_action is None
    assert appel.cancelled is False
    assert attendance.handle(appel_id, 7, AlertAction.DONE, END) is True


def test_a_cancelled_training_closes_its_check() -> None:
    """Cancelling the training closes the check, takes it out of the open list and out of the alerts."""
    _publish()
    appel_id = _open()
    assert [a.id for a in attendance.open_in(GUILD, START)] == [appel_id]

    attendance.handle(appel_id, 5, AlertAction.CANCEL, END)

    appel = attendance.get(appel_id)
    assert appel is not None
    assert appel.cancelled is True
    assert appel.is_open(START) is False
    assert attendance.open_in(GUILD, START) == []
    assert attendance.due_alerts(END) == []


def test_a_cancelled_check_takes_no_save() -> None:
    """A save landing on a cancelled check is refused and stores nothing: no validator, no counts, the answer kept."""
    _publish()
    appel_id = _open()
    attendance.mark(appel_id, [1, 2, 3], [1, 2])
    attendance.handle(appel_id, 5, AlertAction.CANCEL, END)

    with pytest.raises(attendance.AppelCancelledError) as refused:
        attendance.validate(appel_id, 7, END + timedelta(minutes=1))

    assert refused.value.appel.id == appel_id
    appel = attendance.get(appel_id)
    assert appel is not None
    assert (appel.validated_by, appel.validated_at, appel.present_count, appel.absent_count) == (None, None, None, None)
    assert (appel.handled_by, appel.handled_at, appel.handled_action) == (5, END, AlertAction.CANCEL)


def test_a_check_is_open_until_seven_days_after_the_start() -> None:
    """The check can be corrected for seven days, listed newest start first, per server."""
    assert timedelta(days=7) == APPEL_EDIT_WINDOW
    _publish()
    early = _open()
    later = _open(1, starts_at=START + timedelta(days=1), ends_at=END + timedelta(days=1))
    appel = attendance.get(early)
    assert appel is not None
    closes = START + timedelta(days=7)

    assert appel.closes_at == closes
    assert appel.is_open(closes - timedelta(seconds=1)) is True
    assert appel.is_open(closes) is False
    assert [a.id for a in attendance.open_in(GUILD, START)] == [later, early]
    assert [a.id for a in attendance.open_in(GUILD, closes)] == [later]
    assert attendance.open_in(GUILD + 1, START) == []


def test_a_check_goes_with_its_message() -> None:
    """Deleting the announcement deletes its checks, marks and alert messages."""
    _publish()
    appel_id = _open()
    attendance.mark(appel_id, [1], [1])
    attendance.claim_alert(appel_id)
    attendance.record_alert(appel_id, [(10, 100)])

    with db.transaction() as conn:
        conn.execute("DELETE FROM fm_message WHERE message_id = ?", (MESSAGE,))

    assert (_count("appel"), _count("appel_present"), _count("appel_alert")) == (0, 0, 0)


def test_an_unknown_answer_is_refused_by_the_schema() -> None:
    """The database refuses an answer or an outcome it does not know."""
    _publish()
    appel_id = _open()

    with pytest.raises(sqlite3.IntegrityError), db.transaction() as conn:
        conn.execute("UPDATE appel SET handled_action = 'shrug' WHERE id = ?", (appel_id,))
    with pytest.raises(sqlite3.IntegrityError), db.transaction() as conn:
        conn.execute("INSERT INTO training_summary VALUES (1, 2, 0, 'x', ?, 0, 0, 0, 0, 0, 'maybe')", (START.isoformat(),))


def test_summaries_are_counted_by_month() -> None:
    """The monthly view sums the anonymous counts of each server and month, and counts the missed trainings."""
    rows = [
        (GUILD, 1, 0, "Laser", "2026-11-05T14:00:00+01:00", 5, 1, 1, 3, 2, "done"),
        (GUILD, 1, 1, "3D", "2026-11-20T10:00:00+01:00", 4, 0, 0, 0, 0, "not_done"),
        (GUILD, 2, 0, "Laser", "2026-11-25T10:00:00+01:00", 2, 0, 1, 0, 0, "cancelled"),
        (GUILD, 3, 0, "Laser", "2026-12-02T10:00:00+01:00", 3, 2, 0, 0, 0, "not_asked"),
        (GUILD + 1, 4, 0, "Laser", "2026-11-06T10:00:00+01:00", 9, 0, 0, 9, 0, "done"),
    ]
    with db.transaction() as conn:
        conn.executemany("INSERT INTO training_summary VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)

    view = db.connection().execute("SELECT * FROM training_month WHERE guild_id = ? ORDER BY month", (GUILD,)).fetchall()

    assert [tuple(r) for r in view] == [
        (GUILD, "2026-11", 3, 11, 1, 2, 3, 2, 1, 1),
        (GUILD, "2026-12", 1, 3, 2, 0, 0, 0, 0, 0),
    ]
    assert view[0].keys() == [
        "guild_id",
        "month",
        "trainings",
        "registered",
        "waitlisted",
        "withdrawn",
        "present",
        "absent",
        "not_done",
        "cancelled",
    ]


def test_a_second_thread_for_one_check_is_refused() -> None:
    """The first thread stays: a later one is reported as not stored."""
    _publish()
    appel_id = _open()

    assert attendance.set_thread(appel_id, 901) is True
    assert attendance.set_thread(appel_id, 902) is False

    stored = attendance.get(appel_id)
    assert stored is not None
    assert stored.thread_id == 901
