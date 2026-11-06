"""Training templates are stored per server: each server reads, changes and deletes its own only."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import pytest

from fablabot import db
from fablabot.models import TemplateFields, TrainingTemplate
from fablabot.services import catalogue
from fablabot.services.catalogue import TemplateNameTakenError

if TYPE_CHECKING:
    from collections.abc import Iterator

GUILD_A = 111111111111111111
GUILD_B = 222222222222222222


@pytest.fixture(autouse=True)
def _database() -> Iterator[None]:
    """Open a fresh database at the latest schema."""
    db.open_database()
    yield
    db.close_database()


def _srg(guild_id: int = GUILD_A, name: str = "SRG + Imp 3D") -> TrainingTemplate:
    """Store the 3D printing safety training."""
    return catalogue.create_template(guild_id, TemplateFields(name, "45 min", 5, "Ramenez votre PC.", True))


def test_a_template_reads_back_whole() -> None:
    """Every field comes back as stored, excusable as a boolean."""
    stored = _srg()

    assert catalogue.get_template(GUILD_A, stored.id) == TrainingTemplate(
        stored.id, GUILD_A, TemplateFields("SRG + Imp 3D", "45 min", 5, "Ramenez votre PC.", True)
    )


def test_templates_are_listed_by_name_whatever_the_case() -> None:
    """The list is alphabetical without regard to case, so a long list is easy to scan."""
    for name in ("Soudure", "brodeuse", "Fusion 360"):
        _srg(name=name)

    assert [t.fields.name for t in catalogue.list_templates(GUILD_A)] == ["brodeuse", "Fusion 360", "Soudure"]


def test_a_server_never_reads_or_changes_another_servers_template() -> None:
    """A template ID from another server reads as missing, and update and delete leave it alone."""
    theirs = _srg(GUILD_B)

    assert catalogue.get_template(GUILD_A, theirs.id) is None
    assert catalogue.list_templates(GUILD_A) == []
    assert catalogue.search_templates(GUILD_A, "") == []
    assert catalogue.find_by_name(GUILD_A, "SRG + Imp 3D") is None
    assert (
        catalogue.update_template(GUILD_A, theirs.id, TemplateFields("Pris", "1h", 1, "", False), expected=theirs.fields)
        is False
    )
    assert catalogue.delete_template(GUILD_A, theirs.id, expected=theirs.fields) is False
    assert catalogue.get_template(GUILD_B, theirs.id) == theirs


def test_a_name_is_unique_on_a_server_whatever_the_case() -> None:
    """A second template named the same, in another case, is refused; another server may use the name."""
    _srg()

    with pytest.raises(TemplateNameTakenError):
        _srg(name="srg + imp 3d")
    assert _srg(GUILD_B).guild_id == GUILD_B


@pytest.mark.parametrize(("stored", "again"), [("Électronique", "électronique"), ("Straße", "STRASSE"), ("Œuvre", "œUVRE")])
def test_a_name_is_unique_whatever_the_case_of_an_accented_letter(stored: str, again: str) -> None:
    """Names that differ only in the case of an accented letter are one name: the second is refused, found as the first."""
    first = _srg(name=stored)
    other = _srg(name="Soudure")

    with pytest.raises(TemplateNameTakenError):
        _srg(name=again)
    with pytest.raises(TemplateNameTakenError):
        catalogue.update_template(GUILD_A, other.id, TemplateFields(again, "1h", 10, "", False), expected=other.fields)

    assert [t.fields.name for t in catalogue.list_templates(GUILD_A)] == sorted([stored, "Soudure"], key=str.casefold)
    assert catalogue.find_by_name(GUILD_A, again) == first
    assert catalogue.get_template(GUILD_A, other.id) == other


def test_renaming_onto_another_template_is_refused_and_changes_nothing() -> None:
    """An update that takes another template's name raises, and both templates stay as they were."""
    srg = _srg()
    github = _srg(name="Formation GitHub")

    with pytest.raises(TemplateNameTakenError):
        catalogue.update_template(
            GUILD_A, github.id, TemplateFields("SRG + IMP 3D", "1h", 10, "", False), expected=github.fields
        )

    assert catalogue.get_template(GUILD_A, github.id) == github
    assert catalogue.get_template(GUILD_A, srg.id) == srg


def test_an_update_replaces_every_field() -> None:
    """Name, duration, seats, description and excusable all change."""
    srg = _srg()
    changed = TemplateFields("SRG", "1 h", 6, "", False)

    assert catalogue.update_template(GUILD_A, srg.id, changed, expected=srg.fields) is True
    assert catalogue.get_template(GUILD_A, srg.id) == TrainingTemplate(srg.id, GUILD_A, changed)


def test_an_update_made_from_a_stale_copy_changes_nothing() -> None:
    """A form prefilled before another change is refused, whichever field changed, and keeps the newer values."""
    srg = _srg()
    newer = TemplateFields("SRG", "1 h", 6, "", False)
    catalogue.update_template(GUILD_A, srg.id, newer, expected=srg.fields)

    assert (
        catalogue.update_template(GUILD_A, srg.id, TemplateFields("Autre", "2 h", 7, "x", True), expected=srg.fields) is False
    )

    assert catalogue.get_template(GUILD_A, srg.id) == TrainingTemplate(srg.id, GUILD_A, newer)


@pytest.mark.parametrize("field", ["name", "duration", "seats", "description", "excusable"])
def test_an_update_checks_every_field_it_was_prefilled_with(field: str) -> None:
    """A change to any single field since the prefill makes the update refuse."""
    srg = _srg()
    other = {"name": "SRG", "duration": "1 h", "seats": 9, "description": "", "excusable": False}[field]
    changed = TemplateFields(**{**srg.fields.__dict__, field: other})
    catalogue.update_template(GUILD_A, srg.id, changed, expected=srg.fields)

    assert catalogue.update_template(GUILD_A, srg.id, srg.fields, expected=srg.fields) is False
    assert catalogue.get_template(GUILD_A, srg.id) == TrainingTemplate(srg.id, GUILD_A, changed)


def test_a_deleted_templates_id_is_never_given_to_a_new_template() -> None:
    """A button holding a deleted template's ID cannot reach the template created after it."""
    _srg(name="Soudure")
    gone = _srg()
    catalogue.delete_template(GUILD_A, gone.id, expected=gone.fields)

    later = _srg(name="Laser")

    assert later.id != gone.id
    assert catalogue.delete_template(GUILD_A, gone.id, expected=gone.fields) is False
    assert catalogue.get_template(GUILD_A, later.id) == later


def test_a_deleted_template_is_gone_and_a_second_delete_says_so() -> None:
    """Delete answers True once, then False."""
    srg = _srg()

    assert catalogue.delete_template(GUILD_A, srg.id, expected=srg.fields) is True
    assert catalogue.get_template(GUILD_A, srg.id) is None
    assert catalogue.delete_template(GUILD_A, srg.id, expected=srg.fields) is False


def test_a_template_changed_since_it_was_read_is_not_deleted() -> None:
    """A deletion made from a stale copy leaves the template that someone changed in the meantime."""
    srg = _srg()
    changed = TemplateFields("Découpe laser", "3 h", 8, "", False)
    assert catalogue.update_template(GUILD_A, srg.id, changed, expected=srg.fields) is True

    assert catalogue.delete_template(GUILD_A, srg.id, expected=srg.fields) is False
    assert catalogue.get_template(GUILD_A, srg.id) == TrainingTemplate(srg.id, GUILD_A, changed)
    assert catalogue.delete_template(GUILD_A, srg.id, expected=changed) is True


def test_search_finds_a_part_of_the_name_whatever_the_case_and_stops_at_twenty_five() -> None:
    """Typing a few letters finds every template that holds them; Discord shows 25 choices at most."""
    for number in range(30):
        _srg(name=f"Résine {number:02d}")
    _srg(name="Soudure")

    assert [t.fields.name for t in catalogue.search_templates(GUILD_A, "RÉS")][:2] == ["Résine 00", "Résine 01"]
    assert len(catalogue.search_templates(GUILD_A, "rés")) == 25
    assert [t.fields.name for t in catalogue.search_templates(GUILD_A, "oud")] == ["Soudure"]


def test_find_by_name_ignores_case_and_surrounding_spaces() -> None:
    """A name typed by hand finds its template."""
    srg = _srg()

    assert catalogue.find_by_name(GUILD_A, "  srg + imp 3d ") == srg


_VALID_ROW = {
    "guild_id": GUILD_A,
    "name": "Laser",
    "name_key": "laser",
    "duration": "1h",
    "seats": 5,
    "description": "",
    "excusable": 1,
}
_INSERT_ROW = (
    "INSERT INTO training_template (guild_id, name, name_key, duration, seats, description, excusable) "
    "VALUES (:guild_id, :name, :name_key, :duration, :seats, :description, :excusable)"
)


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("seats", 0),
        ("seats", 501),
        ("name", ""),
        ("name", "x" * 101),
        ("name_key", ""),
        ("duration", ""),
        ("duration", "x" * 51),
        ("description", "x" * 501),
        ("excusable", 2),
    ],
)
def test_the_schema_refuses_a_value_out_of_bounds(column: str, value: object) -> None:
    """The bounds the forms check are held by the table too, each by its own check."""
    row = dict(_VALID_ROW)
    row[column] = value
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"), db.transaction() as conn:
        conn.execute(_INSERT_ROW, row)


def test_the_bounds_test_starts_from_a_row_the_table_takes() -> None:
    """The row the bounds test changes one value of is itself accepted."""
    with db.transaction() as conn:
        conn.execute(_INSERT_ROW, _VALID_ROW)


def test_the_templates_are_counted_for_the_export() -> None:
    """Every server's templates count."""
    _srg()
    _srg(GUILD_B)

    assert catalogue.count_templates() == 2


def test_a_name_is_stored_without_the_spaces_around_it() -> None:
    """A name is stored trimmed on creation and on update, so "Soudure" and "Soudure " cannot both exist."""
    created = _srg(name="  Soudure ")

    assert created.fields.name == "Soudure"
    assert catalogue.get_template(GUILD_A, created.id) == created
    with pytest.raises(TemplateNameTakenError):
        _srg(name="Soudure ")
    other = _srg(name="Laser")
    assert catalogue.update_template(
        GUILD_A, other.id, TemplateFields(" Brodeuse  ", "1h", 5, "", False), expected=other.fields
    )
    assert [t.fields.name for t in catalogue.list_templates(GUILD_A)] == ["Brodeuse", "Soudure"]


@pytest.mark.parametrize("name", ["", "   "])
def test_a_blank_name_is_refused_on_creation_and_on_update(name: str) -> None:
    """A name of nothing or of spaces raises a ValueError and stores nothing."""
    srg = _srg()

    with pytest.raises(ValueError, match="name"):
        _srg(name=name)
    with pytest.raises(ValueError, match="name"):
        catalogue.update_template(GUILD_A, srg.id, TemplateFields(name, "1h", 5, "", False), expected=srg.fields)
    assert catalogue.list_templates(GUILD_A) == [srg]


def test_a_check_failure_is_not_a_taken_name() -> None:
    """Seats of 0 through the service raise the database's IntegrityError, not TemplateNameTakenError."""
    with pytest.raises(sqlite3.IntegrityError) as raised:
        catalogue.create_template(GUILD_A, TemplateFields("Laser", "1h", 0, "", False))

    assert not isinstance(raised.value, TemplateNameTakenError)
    assert catalogue.list_templates(GUILD_A) == []


def test_a_name_is_unique_whatever_the_unicode_form_of_its_accents() -> None:
    """A name typed with a composed accent and the same name with a combining accent are one name."""
    composed, combining = "Électronique", "Électronique"
    first = _srg(name=composed)

    with pytest.raises(TemplateNameTakenError):
        _srg(name=combining)

    assert catalogue.find_by_name(GUILD_A, combining) == first
    assert catalogue.search_templates(GUILD_A, "Élec") == [first]
