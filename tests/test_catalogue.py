"""What a template form accepts, as pure logic: the seats, and the field a refusal names."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, time

import pytest

from fablabot.helpers.constants import MAX_SESSIONS, PARIS_TZ, SESSION_EMOJIS
from fablabot.helpers.formation.catalogue import (
    MAX_SEATS,
    MIN_SEATS,
    day_text,
    find_session,
    pick_emoji,
    read_date,
    read_date_time,
    read_hour,
    read_seats,
    session_value,
    starts_at,
    template_description,
    template_fields,
)
from fablabot.helpers.utils import is_valid_emoji
from fablabot.models import TemplateFields
from fablabot.models.formation import FmMessageDraft, Formation


@pytest.mark.parametrize(
    ("text", "seats"),
    [("5", 5), (" 12 ", 12), ("1", 1), ("500", 500), ("0", None), ("501", None), ("5 places", None), ("x", None), ("", None)],
)
def test_seats_are_a_whole_number_from_one_to_five_hundred(text: str, seats: int | None) -> None:
    """Seats read as a whole number from 1 to 500, spaces around it ignored, and nothing else."""
    assert read_seats(text) == seats


@pytest.mark.parametrize("text", ["1_0", "+5", "-5", "5.0", "٣"])
def test_seats_refuse_what_int_would_still_accept(text: str) -> None:
    """A sign, an underscore, a decimal point or a non-ASCII digit is not a number of seats."""
    assert read_seats(text) is None


def test_the_seat_bounds_are_one_and_five_hundred() -> None:
    """The bounds the form checks are the ones the table enforces."""
    assert (MIN_SEATS, MAX_SEATS) == (1, 500)


def test_a_template_form_gives_its_fields_trimmed() -> None:
    """A valid form gives the fields with the spaces around each text removed and `Oui` read as excusable."""
    fields = template_fields(" Soudure ", " 1 h ", " 4 ", " Apportez vos lunettes. ", "Oui")

    assert fields == TemplateFields("Soudure", "1 h", 4, "Apportez vos lunettes.", True)


def test_a_template_form_reads_non_as_not_excusable() -> None:
    """`Non` is a training whose absence is not excused, and an empty description is allowed."""
    fields = template_fields("Soudure", "1 h", "4", "", "Non")

    assert fields == TemplateFields("Soudure", "1 h", 4, "", False)


@pytest.mark.parametrize(
    ("given", "refusal"),
    [
        (("", "1 h", "4", "", "Oui"), "Nom : à remplir."),
        (("   ", "1 h", "4", "", "Oui"), "Nom : à remplir."),
        (("x" * 101, "1 h", "4", "", "Oui"), "Nom : 100 caractères au plus."),
        (("Soudure", "", "4", "", "Oui"), "Durée : à remplir."),
        (("Soudure", "x" * 51, "4", "", "Oui"), "Durée : 50 caractères au plus."),
        (("Soudure", "1 h", "0", "", "Oui"), "Places : un nombre entier de 1 à 500."),
        (("Soudure", "1 h", "x", "", "Oui"), "Places : un nombre entier de 1 à 500."),
        (("Soudure", "1 h", "4", "x" * 501, "Oui"), "Description : 500 caractères au plus."),
    ],
)
def test_a_template_form_names_the_field_it_refuses(given: tuple[str, str, str, str, str], refusal: str) -> None:
    """A refused form answers with the text naming its field and the limit, and no fields."""
    assert template_fields(*given) == refusal


def test_a_template_form_keeps_the_limits_inclusive() -> None:
    """A name of 100 characters, a duration of 50 and a description of 500 are accepted."""
    fields = template_fields("n" * 100, "d" * 50, "500", "x" * 500, "Oui")

    assert isinstance(fields, TemplateFields)


@pytest.mark.parametrize("chosen", ["", "Peut-être", "oui"])
def test_a_template_form_without_oui_or_non_is_refused_not_read_as_a_choice(chosen: str) -> None:
    """Anything but the menu's Oui or Non is refused with the field named, never defaulted."""
    assert template_fields("Soudure", "1 h", "4", "", chosen) == "Excusable : à choisir."


@pytest.mark.parametrize(
    ("today", "text", "expected"),
    [
        (date(2026, 11, 6), "12/10", date(2027, 10, 12)),
        (date(2026, 12, 20), "05/01", date(2027, 1, 5)),
        (date(2026, 11, 6), "06/11", date(2026, 11, 6)),
        (date(2026, 11, 6), "07/11", date(2026, 11, 7)),
        (date(2026, 11, 6), "5/1", date(2027, 1, 5)),
        (date(2026, 11, 6), " 12/12 ", date(2026, 12, 12)),
        (date(2027, 3, 1), "29/02", date(2028, 2, 29)),
    ],
)
def test_a_day_and_month_mean_the_next_such_date(today: date, text: str, expected: date) -> None:
    """`JJ/MM` is the next such date counting today, so a date already gone this year means next year."""
    assert read_date(text, today) == expected


def test_a_full_date_and_the_hours_read() -> None:
    """`JJ/MM/AAAA` is taken as written, past dates included, and an hour reads as `HH:MM`, `18h30` or `18h`."""
    assert read_date("12/10/2026", date(2026, 11, 6)) == date(2026, 10, 12)
    assert read_date("01/01/2030", date(2026, 11, 6)) == date(2030, 1, 1)
    assert read_hour("18:30") == time(18, 30)
    assert read_hour(" 8:05 ") == time(8, 5)
    assert read_hour("18h30") == time(18, 30)
    assert read_hour("18h") == time(18, 0)
    assert read_hour("00:00") == time(0, 0)
    assert read_hour("23h59") == time(23, 59)


@pytest.mark.parametrize(
    "text", ["31/02", "12-10", "", "12", "12/13", "0/10", "12/10/26", "29/02/2027", "a/b", "\u0661\u0662/\u0661\u0660"]
)
def test_an_unreadable_date_or_hour_reads_none(text: str) -> None:
    """A date that does not exist or is not written `JJ/MM` or `JJ/MM/AAAA` reads as none."""
    assert read_date(text, date(2026, 11, 6)) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("12/10 18:30", datetime(2027, 10, 12, 18, 30, tzinfo=PARIS_TZ)),
        (" 06/11  9h ", datetime(2026, 11, 6, 9, 0, tzinfo=PARIS_TZ)),
        ("12/10/2026 18:30", datetime(2026, 10, 12, 18, 30, tzinfo=PARIS_TZ)),
        ("01/01/2030 00:00", datetime(2030, 1, 1, 0, 0, tzinfo=PARIS_TZ)),
        ("12/10", None),
        ("18:30", None),
        ("31/02 18:30", None),
        ("12/10 25:00", None),
        ("12/10 18:30 soir", None),
        ("", None),
    ],
)
def test_a_date_and_time_read_in_one_field(text: str, expected: datetime | None) -> None:
    """`JJ/MM HH:MM` and `JJ/MM/AAAA HH:MM` read as one Paris start, and a missing part or a bad one reads as none."""
    assert read_date_time(text, date(2026, 11, 6)) == expected


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (date(2026, 11, 6), "06/11/2026"),
        (date(2026, 11, 7), "07/11"),
        (date(2027, 11, 5), "05/11"),
        (date(2027, 11, 6), "06/11/2027"),
        (date(2030, 1, 1), "01/01/2030"),
    ],
)
def test_a_day_is_written_with_its_year_only_when_the_short_form_would_read_as_another(day: date, expected: str) -> None:
    """`JJ/MM` is written when it reads back as that day and the day is not today, else `JJ/MM/AAAA`."""
    assert day_text(day, date(2026, 11, 6)) == expected
    assert read_date(expected, date(2026, 11, 6)) == day


@pytest.mark.parametrize("text", ["25:00", "18h70", "", "18", "18:5", "18:60", "h30", "6pm", "18h3", "1\u0668:00"])
def test_an_unreadable_hour_reads_none(text: str) -> None:
    """An hour past 23, a minute past 59 or any other shape reads as none."""
    assert read_hour(text) is None


def test_a_start_is_a_paris_time() -> None:
    """The start joins a day and an hour in Paris time, summer and winter."""
    assert starts_at(date(2026, 7, 1), time(18, 30)) == datetime(2026, 7, 1, 18, 30, tzinfo=PARIS_TZ)
    assert starts_at(date(2026, 7, 1), time(18, 30)).utcoffset() == datetime(2026, 7, 1, tzinfo=PARIS_TZ).utcoffset()
    assert starts_at(date(2026, 1, 1), time(18, 30)).isoformat() == "2026-01-01T18:30:00+01:00"


def test_the_emoji_pool_holds_twenty_distinct_single_code_point_emojis() -> None:
    """The pool has twenty emojis, none twice, each one code point that Discord takes as a reaction."""
    assert len(SESSION_EMOJIS) == MAX_SESSIONS == 20
    assert len(set(SESSION_EMOJIS)) == 20
    assert all(len(emoji) == 1 and is_valid_emoji(emoji) for emoji in SESSION_EMOJIS)


def _session(emoji: str, name: str = "Soudure") -> Formation:
    """A session of the draft."""
    return Formation(
        emoji=emoji, name=name, trainer_mention="<@1>", start_iso="2026-11-10T18:00:00+01:00", duration="1 h", seats=5
    )


def _draft(*sessions: Formation) -> FmMessageDraft:
    """A draft holding these sessions."""
    return FmMessageDraft(header="h", role_id=1, intro="i", fms=list(sessions), end="e")


def test_the_first_unused_emoji_is_picked() -> None:
    """The emoji is the first of the pool no session holds, whatever order the sessions are in."""
    assert pick_emoji(_draft()) == SESSION_EMOJIS[0]
    assert pick_emoji(_draft(_session(SESSION_EMOJIS[0]), _session(SESSION_EMOJIS[2]))) == SESSION_EMOJIS[1]
    assert pick_emoji(_draft(_session("🔧"), _session(SESSION_EMOJIS[0]))) == SESSION_EMOJIS[1]


def test_no_emoji_is_picked_when_the_pool_is_used_up() -> None:
    """With every emoji of the pool held by a session there is none left to pick."""
    assert pick_emoji(_draft(*[_session(emoji) for emoji in SESSION_EMOJIS])) is None


def test_only_the_first_session_of_a_training_gets_its_description() -> None:
    """The template's description comes with the first session of that name and not with the next ones."""
    fields = TemplateFields("Soudure", "1 h", 5, "Apportez vos lunettes.", True)

    assert template_description(_draft(), fields) == "Apportez vos lunettes."
    assert template_description(_draft(_session("🧡", "Résine")), fields) == "Apportez vos lunettes."
    assert template_description(_draft(_session("🧡")), fields) == ""


def test_a_session_is_found_by_its_start_and_emoji() -> None:
    """A session is found by the start and emoji its menu value holds, and a duplicate emoji resolves to the first."""
    first, other = _session("🧡"), replace(_session("💛"), start_iso="2026-11-10T19:00:00+01:00")
    draft = _draft(first, other)

    assert session_value(first) == f"{int(first.start_dt.timestamp())}:🧡"
    assert find_session(draft, int(first.start_dt.timestamp()), "🧡") == 0
    assert find_session(draft, int(other.start_dt.timestamp()), "💛") == 1
    twin = replace(first, name="Résine")
    assert find_session(_draft(first, twin), int(first.start_dt.timestamp()), "🧡") == 0


def test_a_changed_or_removed_session_is_not_found() -> None:
    """A start that moved, an emoji that changed or a session that left the draft finds nothing."""
    session = _session("🧡")
    start = int(session.start_dt.timestamp())
    draft = _draft(session)

    assert find_session(draft, start + 60, "🧡") is None
    assert find_session(draft, start, "💛") is None
    assert find_session(_draft(), start, "🧡") is None
