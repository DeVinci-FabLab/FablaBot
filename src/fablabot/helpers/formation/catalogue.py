"""Reading the forms of the catalogue: the seats, a template's fields, a session's date and hour, and its place in the draft."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, time
from hashlib import sha256
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
DESCRIPTION_CHARS: Final = 500
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
        (len(description) > DESCRIPTION_CHARS, f"Description : {DESCRIPTION_CHARS} caractères au plus."),
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
    """Write a day so that ``read_date`` gives it back, even from a form sent after midnight.

    Args:
        day (date): The day to write.
        today (date): The day it is, in Paris.

    Returns:
        str: ``JJ/MM`` for a later day this year that ``JJ/MM`` reads back; else ``JJ/MM/AAAA``, today included, since
            ``JJ/MM`` sent the day after would be read as next year.
    """
    short = day.strftime("%d/%m")
    return short if day > today and read_date(short, today) == day else day.strftime("%d/%m/%Y")


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


def is_in_draft(guild_id: int, session: Formation) -> bool:
    """Tell whether the server's draft holds a session with the same name, start and trainer.

    Args:
        guild_id (int): The server's ID.
        session (Formation): The session about to be added.

    Returns:
        bool: True when one is there already, which a form answered twice would add a second time.
    """
    draft = formations.get_draft(guild_id)
    return draft is not None and any(
        fm.name == session.name and fm.start_dt == session.start_dt and fm.trainer_mention == session.trainer_mention
        for fm in draft.fms
    )


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


def session_value(session: Formation) -> str:
    """Write what a menu holds to name a session: its start as a Unix time, a colon and its emoji."""
    return f"{int(session.start_dt.timestamp())}:{session.emoji}"


def session_digest(session: Formation) -> str:
    """Summarise what a session holds in 12 hex digits, so that a form or a confirmation can tell it from a changed one.

    Args:
        session (Formation): The session, as the draft holds it.

    Returns:
        str: The digest of its emoji, name, trainer, start, duration, seats, description and excusable.
    """
    text = "\x1f".join(
        str(value)
        for value in (
            session.emoji,
            session.name,
            session.trainer_mention,
            int(session.start_dt.timestamp()),
            session.duration,
            session.seats,
            session.description,
            session.excusable,
        )
    )
    return sha256(text.encode()).hexdigest()[:12]


def find_session(draft: FmMessageDraft, start_unix: int, emoji: str) -> int | None:
    """Find a session of the draft by its start and its emoji, as a menu value or a custom ID names it.

    Both must match, so a session whose start moved, or whose emoji now belongs to another, is not found. Of two
    sessions with the same start and emoji, left by old forms, the first is taken.

    Args:
        draft (FmMessageDraft): The draft.
        start_unix (int): The start, in seconds since the epoch.
        emoji (str): The emoji.

    Returns:
        int | None: The position in the draft, or None when no session has both.
    """
    return next((n for n, fm in enumerate(draft.fms) if fm.emoji == emoji and int(fm.start_dt.timestamp()) == start_unix), None)


def replace_session(  # noqa: PLR0913 - the four values the form changes, by name
    guild_id: int,
    start_unix: int,
    emoji: str,
    digest: str,
    *,
    trainer_mention: str,
    start_iso: str,
    seats: int,
    description: str,
) -> Formation | None:
    """Change the trainer, start, seats and description of a session of the draft, the draft kept sorted by start.

    The draft is read, changed and saved with no await in between. The emoji, name, duration and excusable stay. A
    session that is not the one the form opened on, whatever changed in it since, is left as it is.

    Args:
        guild_id (int): The server's ID.
        start_unix (int): The start of the session to change, as the menu named it.
        emoji (str): The emoji of the session to change.
        digest (str): The digest of the session as the form opened on it.
        trainer_mention (str): The new trainer's mention.
        start_iso (str): The new start.
        seats (int): The new seats.
        description (str): The new description.

    Returns:
        Formation | None: The session as stored, or None when no session has that start, emoji and digest any more.
    """
    draft = formations.get_draft(guild_id)
    position = None if draft is None else find_session(draft, start_unix, emoji)
    if draft is None or position is None or session_digest(draft.fms[position]) != digest:
        return None
    changed = replace(
        draft.fms[position], trainer_mention=trainer_mention, start_iso=start_iso, seats=seats, description=description
    )
    sessions = sorted([*draft.fms[:position], changed, *draft.fms[position + 1 :]], key=lambda fm: fm.start_dt)
    formations.save_draft(guild_id, replace(draft, fms=sessions))
    return changed


def remove_session(guild_id: int, start_unix: int, emoji: str, digest: str) -> Formation | None:
    """Take a session out of the draft, the rest kept as it is.

    The draft is read, changed and saved with no await in between. A session that is not the one the confirmation named,
    whatever changed in it since, is left in the draft.

    Args:
        guild_id (int): The server's ID.
        start_unix (int): The start of the session to remove, as the confirmation named it.
        emoji (str): The emoji of the session to remove.
        digest (str): The digest of the session as the confirmation was asked.

    Returns:
        Formation | None: The session removed, or None when no session has that start, emoji and digest any more.
    """
    draft = formations.get_draft(guild_id)
    position = None if draft is None else find_session(draft, start_unix, emoji)
    if draft is None or position is None or session_digest(draft.fms[position]) != digest:
        return None
    removed = draft.fms[position]
    formations.save_draft(guild_id, replace(draft, fms=[*draft.fms[:position], *draft.fms[position + 1 :]]))
    return removed
