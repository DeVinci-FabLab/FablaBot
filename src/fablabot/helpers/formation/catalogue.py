"""Reading the forms of the catalogue: the seats, a template's fields, a session's date and hour, and its place in the draft."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, time
import re
from typing import Final

from fablabot.helpers.constants import MAX_SESSIONS, PARIS_TZ, SESSION_EMOJIS
from fablabot.models.catalogue import TemplateFields
from fablabot.models.formation import FmMessageDraft, Formation
from fablabot.services import formations

MIN_SEATS: Final = 1
MAX_SEATS: Final = 500
_NAME_CHARS: Final = 100
_DURATION_CHARS: Final = 50
_DESCRIPTION_CHARS: Final = 500
_WHOLE_NUMBER: Final = re.compile(r"[0-9]+")
_DAY_MONTH: Final = re.compile(r"([0-9]{1,2})/([0-9]{1,2})(?:/([0-9]{4}))?")
_HOUR: Final = re.compile(r"([0-9]{1,2})(?::([0-9]{2})|h([0-9]{2})?)")
_LEAP_CYCLE: Final = 8
"""The most years between two 29 February."""
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


def read_date(text: str, today: date) -> date | None:
    """Read the date of a session as typed.

    Args:
        text (str): ``JJ/MM`` or ``JJ/MM/AAAA``, spaces around it allowed.
        today (date): The day it is, in Paris.

    Returns:
        date | None: For ``JJ/MM`` the next such date, today included; for ``JJ/MM/AAAA`` that date, past or not; None
            when the text is not a date that exists.
    """
    found = _DAY_MONTH.fullmatch(text.strip())
    if found is None:
        return None
    day, month = int(found[1]), int(found[2])
    if found[3] is not None:
        return _date_of(int(found[3]), month, day)
    for year in range(today.year, today.year + _LEAP_CYCLE):
        candidate = _date_of(year, month, day)
        if candidate is not None and candidate >= today:
            return candidate
    return None


def _date_of(year: int, month: int, day: int) -> date | None:
    """Make a date, or None when the month has no such day."""
    try:
        return date(year, month, day)
    except ValueError:
        return None


def read_hour(text: str) -> time | None:
    """Read the hour of a session as typed.

    Args:
        text (str): ``HH:MM``, ``18h30`` or ``18h``, spaces around it allowed.

    Returns:
        time | None: The hour, or None when it is not written so or does not exist.
    """
    found = _HOUR.fullmatch(text.strip())
    if found is None:
        return None
    try:
        return time(int(found[1]), int(found[2] or found[3] or 0))
    except ValueError:
        return None


def read_date_time(text: str, today: date) -> datetime | None:
    """Read a session's start as typed in one field.

    Args:
        text (str): The day, then the hour, such as ``12/10 18:30`` or ``12/10/2026 18h``, spaces around them allowed.
        today (date): The day it is, in Paris.

    Returns:
        datetime | None: The start in Paris time, or None when the text is not a day and an hour that exist.
    """
    parts = text.split()
    if len(parts) != 2:  # noqa: PLR2004 - a day and an hour
        return None
    day, hour = read_date(parts[0], today), read_hour(parts[1])
    return None if day is None or hour is None else starts_at(day, hour)


def day_text(day: date, today: date) -> str:
    """Write a day so that ``read_date`` gives it back: ``JJ/MM`` when that means this very day, else with the year.

    Args:
        day (date): The day to write.
        today (date): The day it is, in Paris.

    Returns:
        str: ``JJ/MM``, or ``JJ/MM/AAAA`` when ``JJ/MM`` would be read as an earlier year.
    """
    short = day.strftime("%d/%m")
    return short if read_date(short, today) == day else day.strftime("%d/%m/%Y")


def starts_at(day: date, hour: time) -> datetime:
    """Join a day and an hour as Paris time."""
    return datetime.combine(day, hour, tzinfo=PARIS_TZ)


def pick_emoji(draft: FmMessageDraft) -> str | None:
    """Choose the emoji of the next session: the first of the pool no session of the draft holds.

    Args:
        draft (FmMessageDraft): The draft, whose sessions may hold emojis from other forms.

    Returns:
        str | None: The emoji, or None when every emoji of the pool is taken.
    """
    taken = {session.emoji for session in draft.fms}
    return next((emoji for emoji in SESSION_EMOJIS if emoji not in taken), None)


def template_description(draft: FmMessageDraft, fields: TemplateFields) -> str:
    """Give the description a new session of a template starts with.

    The announcement is one message of 2 000 characters, so the description comes with the first session of a training
    only.

    Args:
        draft (FmMessageDraft): The draft.
        fields (TemplateFields): What the template holds.

    Returns:
        str: The template's description when no session of that name is in the draft, else an empty text.
    """
    return "" if any(session.name == fields.name for session in draft.fms) else fields.description


def add_session(guild_id: int, session: Formation) -> tuple[Formation, int] | None:
    """Put a session in the server's draft with the next free emoji, the draft kept sorted by start.

    The draft is read, changed and saved with no await in between, and the emoji is chosen inside, so two forms sent
    one after the other never get the same one and a session added meanwhile is not lost.

    Args:
        guild_id (int): The server's ID.
        session (Formation): The session; its emoji is replaced.

    Returns:
        tuple[Formation, int] | None: The session as stored and how many the draft holds now, or None when the draft is full.
    """
    draft = formations.get_draft(guild_id) or FmMessageDraft(header="", role_id=0, intro="", fms=[], end="")
    emoji = pick_emoji(draft)
    if emoji is None or len(draft.fms) >= MAX_SESSIONS:
        return None
    stored = replace(session, emoji=emoji)
    sessions = sorted([*draft.fms, stored], key=lambda fm: fm.start_dt)
    formations.save_draft(guild_id, replace(draft, fms=sessions))
    return stored, len(sessions)
