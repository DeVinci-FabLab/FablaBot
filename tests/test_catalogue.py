"""What a template form accepts, as pure logic: the seats, and the field a refusal names."""

from __future__ import annotations

import pytest

from fablabot.helpers.formation.catalogue import MAX_SEATS, MIN_SEATS, read_seats, template_fields
from fablabot.models import TemplateFields


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
