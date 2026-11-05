"""The attendance check: how a duration is read, who may take it, and what the trainer's message says."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from conftest import make_member, make_role
import pytest

from fablabot.helpers.constants import APPEL_EDIT_WINDOW, MAX_MSG_CHARS, PARIS_TZ, RoleNames
from fablabot.helpers.formation.attendance import (
    MAX_BUTTONS,
    MAX_MENUS,
    MENU_SIZE,
    appel_content,
    appel_view,
    may_take,
    read_duration,
    trainer_id,
)
from fablabot.models import Appel, Registrant

if TYPE_CHECKING:
    from collections.abc import Sequence

START = datetime(2026, 11, 6, 18, 0, tzinfo=PARIS_TZ)
END = START + timedelta(hours=2)
CLOSES = START + APPEL_EDIT_WINDOW
TRAINER = 2222
OPENING = "Salut Camille !"
CODIR_LINE = "Merci de transmettre au **CoDir** la liste des participants à excuser si besoin."


def _stamp(moment: datetime, style: str) -> str:
    """Write a Discord timestamp of a moment."""
    return f"<t:{int(moment.timestamp())}:{style}>"


def _appel(**changes: Any) -> Appel:
    """Build the check of a two-hour training that is not excusable and has not been saved."""
    base = Appel(
        id=7,
        guild_id=111,
        message_id=5000,
        channel_id=42,
        position=0,
        name="Découpe laser",
        duration="2h",
        excusable=False,
        trainer_id=TRAINER,
        starts_at=START,
        ends_at=END,
        closes_at=CLOSES,
        thread_id=None,
        validated_by=None,
        validated_at=None,
        present_count=None,
        absent_count=None,
        alert_sent=False,
        handled_by=None,
        handled_at=None,
        handled_action=None,
    )
    return replace(base, **changes)


def _registrants(count: int) -> list[Registrant]:
    """Build ``count`` registered members with IDs from 3000."""
    return [Registrant(user_id=3000 + i, username=f"user{i}") for i in range(count)]


def _names(registrants: Sequence[Registrant]) -> dict[int, str]:
    """Name each registered member by the handle stored at sign-up."""
    return {r.user_id: r.username for r in registrants}


def _rows(registrants: Sequence[Registrant], present: set[int] | None = None, **changes: Any) -> list[dict[str, Any]]:
    """Build the check's view and return its component rows."""
    view = appel_view(_appel(**changes), registrants, present or set(), _names(registrants), now=START)
    return view.to_components()


@pytest.mark.parametrize(("text", "minutes"), [("2h", 120), ("2h30", 150), ("1 h 30", 90), ("90 min", 90), ("2 heures", 120)])
def test_a_duration_reads_in_hours_and_minutes(text: str, minutes: int) -> None:
    """The ways the announcement writes a duration all read as a length of time."""
    assert read_duration(text) == timedelta(minutes=minutes)


@pytest.mark.parametrize("text", ["toute la journée", "25h", "0 min", "", "2h75"])
def test_an_unreadable_duration_has_no_end(text: str) -> None:
    """Free text, a day or more, nothing at all and zero give no length, so no end is shown."""
    assert read_duration(text) is None


@pytest.mark.parametrize(("mention", "expected"), [("<@2222>", 2222), ("<@!2222>", 2222), ("Camille", None), ("", None)])
def test_the_trainer_is_read_from_a_member_mention(mention: str, expected: int | None) -> None:
    """A member mention, with or without the nickname mark, gives the ID; free text gives none."""
    assert trainer_id(mention) == expected


@pytest.mark.parametrize(
    ("member_id", "roles", "allowed"),
    [
        (TRAINER, (), True),
        (None, (), False),
        (99, (RoleNames.TRAININGS_MANAGER,), True),
        (99, (RoleNames.PRESIDENT,), True),
        (99, (RoleNames.ADMIN,), True),
        (99, (RoleNames.ADMIN_TEMP,), False),
        (99, (RoleNames.CODIR,), False),
        (99, (), False),
    ],
    ids=["trainer", "trainer-gone", "respo-formation", "president", "administrator", "admin-temp", "codir", "plain-member"],
)
def test_who_may_take_the_appel(member_id: int | None, roles: tuple[str, ...], allowed: bool) -> None:
    """The trainer while still a member, the Respo Formation and the admins may; the temporary role and the CoDir may not."""
    member = None if member_id is None else make_member(member_id, *[make_role(name) for name in roles])
    assert may_take(_appel(), member) is allowed


def test_the_start_message_reads_as_the_first_mockup() -> None:
    """The start message greets the trainer, gives the slot, says what to do and counts the present."""
    content = appel_content(_appel(), _registrants(5), set(), opening=OPENING, now=START)

    assert content == (
        f"Salut Camille !\nTa formation **Découpe laser** commence maintenant ({_stamp(START, 'F')} à {_stamp(END, 't')}).\n\n"
        "**Fais l'appel** : touche le pseudo de chaque présent, puis **Valider l'appel**. "
        f"Tu peux le corriger jusqu'au {_stamp(CLOSES, 'D')}.\n\n"
        "_0 présent sur 5 inscrits._"
    )


def test_a_slot_without_a_readable_duration_shows_only_its_start() -> None:
    """When the duration does not read, no end is shown: the stored guess is never displayed."""
    content = appel_content(_appel(duration="toute la journée"), _registrants(1), set(), opening=OPENING, now=START)

    assert f"commence maintenant ({_stamp(START, 'F')})." in content
    assert _stamp(END, "t") not in content


def test_a_training_whose_slot_is_over_is_not_starting() -> None:
    """An appel opened after the end of the slot says the training took place."""
    content = appel_content(_appel(), _registrants(1), set(), opening=OPENING, now=END + timedelta(minutes=1))

    assert "Ta formation **Découpe laser** a eu lieu le " in content
    assert "commence maintenant" not in content


@pytest.mark.parametrize(
    ("present", "registered", "counter"),
    [
        (0, 1, "0 présent sur 1 inscrit"),
        (1, 1, "1 présent sur 1 inscrit"),
        (0, 2, "0 présent sur 2 inscrits"),
        (1, 2, "1 présent sur 2 inscrits"),
        (2, 2, "2 présents sur 2 inscrits"),
    ],
)
def test_the_counter_agrees_with_its_number(present: int, registered: int, counter: str) -> None:
    """Each noun of the counter is singular up to one and plural from two."""
    registrants = _registrants(registered)
    marked = {r.user_id for r in registrants[:present]}

    content = appel_content(_appel(), registrants, marked, opening=OPENING, now=START)

    assert content.endswith(f"_{counter}._")


def test_a_mark_on_a_member_who_left_the_list_is_not_counted() -> None:
    """Only registered members count as present."""
    content = appel_content(_appel(), _registrants(2), {3000, 9999}, opening=OPENING, now=START)

    assert content.endswith("_1 présent sur 2 inscrits._")


def test_an_excusable_training_keeps_the_codir_line() -> None:
    """The reminder to hand the CoDir the excused members stays, between the instruction and the counter."""
    excusable = appel_content(_appel(excusable=True), _registrants(2), set(), opening=OPENING, now=START)
    other = appel_content(_appel(), _registrants(2), set(), opening=OPENING, now=START)

    assert f"corriger jusqu'au {_stamp(CLOSES, 'D')}.\n\n{CODIR_LINE}\n\n_0 présent sur 2 inscrits._" in excusable
    assert CODIR_LINE not in other


def test_a_saved_appel_reads_its_counts_and_a_change_shows_the_pending_counter() -> None:
    """After a validation the message gives the saved counts; a different live count adds the unsaved counter."""
    saved = _appel(validated_by=TRAINER, validated_at=START, present_count=3, absent_count=2)
    registrants = _registrants(5)
    marked = {r.user_id for r in registrants[:3]}

    unchanged = appel_content(saved, registrants, marked, opening=OPENING, now=START)
    changed = appel_content(saved, registrants, {*marked, registrants[3].user_id}, opening=OPENING, now=START)

    head = (
        f"Salut Camille !\nTa formation **Découpe laser** a eu lieu le {_stamp(START, 'F')} à {_stamp(END, 't')}.\n\n"
        f"✅ **Appel enregistré** : 3 présents, 2 absents. Tu peux encore le corriger jusqu'au {_stamp(CLOSES, 'D')}."
    )
    assert unchanged == head
    assert changed == f"{head}\n\n_4 présents sur 5 inscrits, pas encore enregistré._"


def test_up_to_twenty_registrants_get_one_button_each() -> None:
    """Twenty members fit in buttons, five to a row, then the validation button alone."""
    rows = _rows(_registrants(MAX_BUTTONS))

    assert [len(row["components"]) for row in rows] == [5, 5, 5, 5, 1]
    assert rows[-1]["components"][0]["label"] == "Valider l'appel"
    assert rows[-1]["components"][0]["style"] == 1


def test_a_present_member_is_ticked_and_green() -> None:
    """A present member shows a tick and the success style; an absent one the plain style."""
    registrants = _registrants(2)

    first, second = _rows(registrants, {3000})[0]["components"]

    assert (first["label"], first["style"]) == ("✓ user0", 3)
    assert (second["label"], second["style"]) == ("user1", 2)


def test_a_saved_appel_offers_the_update() -> None:
    """Once saved, the validation button reads "Mettre à jour l'appel"."""
    rows = _rows(_registrants(2), validated_by=TRAINER, validated_at=START, present_count=0, absent_count=2)

    assert rows[-1]["components"][0]["label"] == "Mettre à jour l'appel"


def test_more_than_twenty_registrants_get_menus_of_twenty_five() -> None:
    """From twenty-one members the buttons give way to menus of twenty-five, each with its range and its ticked members."""
    registrants = _registrants(30)
    rows = _rows(registrants, {3000, 3026})

    menus = [row["components"][0] for row in rows[:-1]]
    assert [len(menu["options"]) for menu in menus] == [MENU_SIZE, 5]
    assert [menu["placeholder"] for menu in menus] == ["Présents, inscrits 1 à 25", "Présents, inscrits 26 à 30"]
    assert [menu["custom_id"] for menu in menus] == ["appel:7:s:0", "appel:7:s:1"]
    assert all(menu["min_values"] == 0 and menu["max_values"] == len(menu["options"]) for menu in menus)
    ticked = {option["value"] for menu in menus for option in menu["options"] if option["default"]}
    assert ticked == {"3000", "3026"}
    assert rows[-1]["components"][0]["label"] == "Valider l'appel"


def test_past_a_hundred_registrants_the_message_says_so() -> None:
    """The fifth row is the validation button: a hundred members are listed, and the message says the rest are absent."""
    registrants = _registrants(120)

    content = appel_content(_appel(), registrants, set(), opening=OPENING, now=START)
    rows = _rows(registrants)

    assert content.endswith(
        "_0 présent sur 120 inscrits._\n\n"
        "_Au-delà de 100 inscrits, l'appel ne liste que les 100 premiers ; les autres comptent absents._"
    )
    assert len(rows) == MAX_MENUS + 1
    assert sum(len(row["components"][0]["options"]) for row in rows[:-1]) == MENU_SIZE * MAX_MENUS


def test_a_hundred_registrants_need_no_warning() -> None:
    """Exactly a hundred members are all listed, so the message has nothing to add."""
    content = appel_content(_appel(), _registrants(100), set(), opening=OPENING, now=START)

    assert "Au-delà" not in content


def test_the_longest_message_stays_under_the_limit() -> None:
    """Even with the warning, the CoDir line and the saved counter, the message fits a Discord message."""
    saved = _appel(excusable=True, validated_by=TRAINER, validated_at=START, present_count=1, absent_count=119)

    content = appel_content(saved, _registrants(120), {3000, 3001}, opening=OPENING, now=START)

    assert len(content) < MAX_MSG_CHARS
