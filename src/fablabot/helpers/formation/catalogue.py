"""Reading the form of a training template: the seats, and the fields as one value or the reason they are refused."""

from __future__ import annotations

import re
from typing import Final

from fablabot.models.catalogue import TemplateFields

MIN_SEATS: Final = 1
MAX_SEATS: Final = 500
_NAME_CHARS: Final = 100
_DURATION_CHARS: Final = 50
_DESCRIPTION_CHARS: Final = 500
_WHOLE_NUMBER: Final = re.compile(r"[0-9]+")
_USER_MENTION: Final = re.compile(r"<@!?([0-9]{1,19})>")
EXCUSABLE_YES: Final = "Oui"
EXCUSABLE_NO: Final = "Non"


def read_seats(text: str) -> int | None:
    """Read the seats of a session as typed.

    Args:
        text (str): What was typed, spaces around it allowed.

    Returns:
        int | None: The seats when it is a whole number from 1 to 500, else None. A sign, an underscore, a decimal
            point or a word is not accepted.
    """
    typed = text.strip()
    if _WHOLE_NUMBER.fullmatch(typed) is None:
        return None
    seats = int(typed)
    return seats if MIN_SEATS <= seats <= MAX_SEATS else None


def trainer_id(mention: str) -> int | None:
    """Read the ID of the member a session's trainer mention names.

    Args:
        mention (str): The stored mention, such as ``<@123>``.

    Returns:
        int | None: The member's ID, or None when the text is not a user mention.
    """
    found = _USER_MENTION.fullmatch(mention.strip())
    return int(found[1]) if found else None


def template_fields(name: str, duration: str, seats: str, description: str, excusable: str) -> TemplateFields | str:
    """Read what the form of a template gives.

    Args:
        name (str): The name typed.
        duration (str): The duration typed, such as ``1h30``.
        seats (str): The seats typed.
        description (str): The description typed, possibly empty.
        excusable (str): ``Oui`` or ``Non``, the menu's choice.

    Returns:
        TemplateFields | str: The fields with the spaces around each text removed, or the text of the first refusal,
            which names the field and its limit.
    """
    name, duration, description = name.strip(), duration.strip(), description.strip()
    count = read_seats(seats)
    checks = (
        (not name, "Nom : à remplir."),
        (len(name) > _NAME_CHARS, f"Nom : {_NAME_CHARS} caractères au plus."),
        (not duration, "Durée : à remplir."),
        (len(duration) > _DURATION_CHARS, f"Durée : {_DURATION_CHARS} caractères au plus."),
        (count is None, f"Places : un nombre entier de {MIN_SEATS} à {MAX_SEATS}."),
        (len(description) > _DESCRIPTION_CHARS, f"Description : {_DESCRIPTION_CHARS} caractères au plus."),
        (excusable not in (EXCUSABLE_YES, EXCUSABLE_NO), "Excusable : à choisir."),
    )
    for failed, refusal in checks:
        if failed:
            return refusal
    assert count is not None
    return TemplateFields(name, duration, count, description, excusable == EXCUSABLE_YES)
