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
    assert catalogue.delete_template(GUILD_A, theirs.id) is False
    assert catalogue.get_template(GUILD_B, theirs.id) == theirs


def test_a_name_is_unique_on_a_server_whatever_the_case() -> None:
    """A second template named the same, in another case, is refused; another server may use the name."""
    _srg()

    with pytest.raises(TemplateNameTakenError):
        _srg(name="srg + imp 3d")
    assert _srg(GUILD_B).guild_id == GUILD_B


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
    catalogue.delete_template(GUILD_A, gone.id)

    later = _srg(name="Laser")

    assert later.id != gone.id
    assert catalogue.delete_template(GUILD_A, gone.id) is False
    assert catalogue.get_template(GUILD_A, later.id) == later


def test_a_deleted_template_is_gone_and_a_second_delete_says_so() -> None:
    """Delete answers True once, then False."""
    srg = _srg()

    assert catalogue.delete_template(GUILD_A, srg.id) is True
    assert catalogue.get_template(GUILD_A, srg.id) is None
    assert catalogue.delete_template(GUILD_A, srg.id) is False


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


@pytest.mark.parametrize(
    ("column", "value"),
    [("seats", 0), ("seats", 501), ("name", ""), ("name", "x" * 101), ("duration", ""), ("description", "x" * 501)],
)
def test_the_schema_refuses_a_value_out_of_bounds(column: str, value: object) -> None:
    """The bounds the forms check are held by the table too."""
    row = {"guild_id": GUILD_A, "name": "Laser", "duration": "1h", "seats": 5, "description": "", "excusable": 1}
    row[column] = value
    with pytest.raises(sqlite3.IntegrityError), db.transaction() as conn:
        conn.execute(
            "INSERT INTO training_template (guild_id, name, duration, seats, description, excusable) "
            "VALUES (:guild_id, :name, :duration, :seats, :description, :excusable)",
            row,
        )


def test_the_templates_are_counted_for_the_export() -> None:
    """Every server's templates count."""
    _srg()
    _srg(GUILD_B)

    assert catalogue.count_templates() == 2
