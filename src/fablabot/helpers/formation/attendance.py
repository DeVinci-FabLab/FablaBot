"""The attendance check of a training: who may take it, what the trainer's message says, and how it is delivered."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import logging
import re
import sqlite3
from typing import TYPE_CHECKING, Final
from weakref import WeakValueDictionary

from discord import AllowedMentions, HTTPException, SelectOption, TextChannel, ui

from fablabot.guild_config import get_log_channel_id
from fablabot.helpers.constants import APPEL_ROLES, MAX_MSG_CHARS, PARIS_TZ, RoleNames
from fablabot.helpers.formation.rendering import discord_timestamp
from fablabot.helpers.op_report import split_messages
from fablabot.helpers.utils import counted, get_members_by_role, get_or_fetch_member, send_dm_to_member
from fablabot.services import attendance

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from discord import Guild, Member

    from fablabot.models import Appel, PublishedMessage, Registrant, Validation

logger = logging.getLogger(__name__)

SLOT_FALLBACK: Final = timedelta(hours=3)
"""How long a training lasts for the alert when its duration cannot be read."""

MAX_BUTTONS: Final = 20
"""Up to this many registered members, each gets a button."""

MENU_SIZE: Final = 25
"""The options Discord allows in one menu."""

MAX_MENUS: Final = 4
"""Five rows hold the controls: the validation button takes one, so four menus list MENU_SIZE members each.

Ceiling: a hundred listed members. The upgrade path is pages.
"""

_ROW_SIZE: Final = 5
_VALIDATE_ROW: Final = 4
_LABEL_MAX: Final = 80
_MAX_SLOT = timedelta(hours=24)
_DURATION = re.compile(
    r"(?:(?P<hours>\d+)\s*(?:h|heures?)(?:\s*(?P<minutes>\d{1,2})(?:\s*min(?:utes?)?)?)?|(?P<only>\d+)\s*min(?:utes?)?)"
)
_MENTION = re.compile(r"<@!?(\d+)>")
_LIST_CHARS = MAX_MSG_CHARS - 400
"""The most a line of mentions holds, so that a header and a label fit around it."""
_CODIR_LINE = "Merci de transmettre au **CoDir** la liste des participants à excuser si besoin."
_reporting: WeakValueDictionary[int, asyncio.Lock] = WeakValueDictionary()
"""The lock of each check being reported, alive only while a report holds or awaits it: one report of a check at a time."""
_KEPT: Final = 256
_reported: dict[int, tuple[frozenset[int], frozenset[int]]] = {}
"""Who was present and absent in the last report sent of each check, the oldest forgotten past ``_KEPT`` checks.

Kept in memory on purpose: a restart forgets it, so pressing the update button after one sends the lists again.
"""


def read_duration(duration: str) -> timedelta | None:
    """Read the duration an announcement gives as text.

    Args:
        duration (str): The text, such as ``2h``, ``2h30``, ``1 h 30``, ``90 min`` or ``2 heures``.

    Returns:
        timedelta | None: The length, or None when the text is not a length of time between a minute and a day.
    """
    found = _DURATION.fullmatch(duration.strip().lower())
    if found is None:
        return None
    if found["only"] is not None:
        length = timedelta(minutes=int(found["only"]))
    elif found["minutes"] is not None and int(found["minutes"]) >= 60:  # noqa: PLR2004 - minutes past the hour
        return None
    else:
        length = timedelta(hours=int(found["hours"]), minutes=int(found["minutes"] or 0))
    return length if timedelta() < length <= _MAX_SLOT else None


def trainer_id(mention: str) -> int | None:
    """Read the trainer's ID from the mention an announcement holds.

    Args:
        mention (str): The trainer field of the training.

    Returns:
        int | None: The member's ID, or None when the field is not a member mention.
    """
    found = _MENTION.search(mention)
    return int(found[1]) if found else None


def may_take(appel: Appel, member: Member | None) -> bool:
    """Tell whether someone may take or correct a check.

    Args:
        appel (Appel): The check.
        member (Member | None): The clicker as a member of the check's server, None when they are not one.

    Returns:
        bool: True for the trainer while still on the server, and for the holders of ``APPEL_ROLES``.
    """
    if member is None:
        return False
    return member.id == appel.trainer_id or any(role.name in APPEL_ROLES for role in member.roles)


def _slot(appel: Appel) -> str:
    """Write when the training takes place; the end only when the announcement's duration gave one."""
    start = discord_timestamp(appel.starts_at)
    if read_duration(appel.duration) is None:
        return start
    return f"{start} à {discord_timestamp(appel.ends_at, 't')}"


def _counter(here: int, registered: int) -> str:
    """Write how many registered members are present."""
    return f"{counted(here, 'présent', 'présents')} sur {counted(registered, 'inscrit', 'inscrits')}"


def appel_content(appel: Appel, registrants: Sequence[Registrant], present: set[int], *, opening: str, now: datetime) -> str:
    """Write the message of a check from what is stored.

    Args:
        appel (Appel): The check.
        registrants (Sequence[Registrant]): The registered members.
        present (set[int]): The members marked present.
        opening (str): The first line, which greets the person the message is for.
        now (datetime): The current time, to tell a training that starts from one that is over.

    Returns:
        str: The text to show above the controls.
    """
    registered = {r.user_id for r in registrants}
    here = len(registered & present)
    absent = len(registered) - here
    counter = _counter(here, len(registered))
    closes = discord_timestamp(appel.closes_at, "D")
    if appel.validated_at is None:
        timing = f"commence maintenant ({_slot(appel)})" if now < appel.ends_at else f"a eu lieu le {_slot(appel)}"
        codir = f"{_CODIR_LINE}\n\n" if appel.excusable else ""
        text = (
            f"{opening}\nTa formation **{appel.name}** {timing}.\n\n"
            "**Fais l'appel** : touche le pseudo de chaque présent, puis **Valider l'appel**. "
            f"Tu peux le corriger jusqu'au {closes}.\n\n{codir}_{counter}._"
        )
    else:
        text = (
            f"{opening}\nTa formation **{appel.name}** a eu lieu le {_slot(appel)}.\n\n"
            f"✅ **Appel enregistré** : {counted(appel.present_count or 0, 'présent', 'présents')}, "
            f"{counted(appel.absent_count or 0, 'absent', 'absents')}. Tu peux encore le corriger jusqu'au {closes}."
        )
        if (here, absent) != (appel.present_count, appel.absent_count):
            text += f"\n\n_{counter}, pas encore enregistré._"
    if len(registrants) > MENU_SIZE * MAX_MENUS:
        text += (
            f"\n\n_Au-delà de {MENU_SIZE * MAX_MENUS} inscrits, l'appel ne liste que les {MENU_SIZE * MAX_MENUS} premiers ; "
            "les autres comptent absents._"
        )
    return text


def appel_view(
    appel: Appel, registrants: Sequence[Registrant], present: set[int], names: Mapping[int, str], *, now: datetime
) -> ui.View:
    """Build the controls of a check: a button per member up to ``MAX_BUTTONS``, else menus, then the validation button.

    Args:
        appel (Appel): The check.
        registrants (Sequence[Registrant]): The registered members, in order.
        present (set[int]): The members marked present.
        names (Mapping[int, str]): The label of each member; a member missing from it reads by their ID.
        now (datetime): The current time; a check that no longer takes changes gets disabled controls.

    Returns:
        ui.View: A view that never times out, whose items carry all their state in their custom ID.
    """
    # Imported here: the components call back into this module for the rights and the message.
    from fablabot.ui.attendance import AppelMenu, AppelToggle, AppelValidate

    closed = not appel.is_open(now)
    listed = list(registrants)[: MENU_SIZE * MAX_MENUS]
    view = ui.View(timeout=None)
    if len(registrants) <= MAX_BUTTONS:
        for index, registrant in enumerate(listed):
            name = names.get(registrant.user_id, str(registrant.user_id))
            label = f"✓ {name}" if registrant.user_id in present else name
            view.add_item(
                AppelToggle(
                    appel.id,
                    registrant.user_id,
                    label[:_LABEL_MAX],
                    present=registrant.user_id in present,
                    disabled=closed,
                    row=index // _ROW_SIZE,
                )
            )
    else:
        for index in range(0, len(listed), MENU_SIZE):
            members = listed[index : index + MENU_SIZE]
            options = [
                SelectOption(
                    label=names.get(m.user_id, str(m.user_id))[:100], value=str(m.user_id), default=m.user_id in present
                )
                for m in members
            ]
            view.add_item(
                AppelMenu(
                    appel.id,
                    index // MENU_SIZE,
                    placeholder=f"Présents, inscrits {index + 1} à {index + len(members)}",
                    options=options,
                    disabled=closed,
                    row=index // MENU_SIZE,
                )
            )
    view.add_item(AppelValidate(appel.id, updated=appel.validated_at is not None, disabled=closed, row=_VALIDATE_ROW))
    return view


def appel_message(guild: Guild, appel: Appel, now: datetime) -> tuple[str, ui.View]:
    """Read a check from the database and build its message as it should show now.

    Args:
        guild (Guild): The check's server, to name the members.
        appel (Appel): The check.
        now (datetime): The current time.

    Returns:
        tuple[str, ui.View]: The text and the controls.
    """
    registrants = attendance.registrants(appel.id)
    present = attendance.present(appel.id)
    names: dict[int, str] = {}
    for registrant in registrants:
        member = guild.get_member(registrant.user_id)
        names[registrant.user_id] = member.display_name if member else registrant.username
    trainer = guild.get_member(appel.trainer_id) if appel.trainer_id is not None else None
    opening = f"Salut {trainer.display_name} !" if trainer else "Salut !"
    content = appel_content(appel, registrants, present, opening=opening, now=now)
    return content, appel_view(appel, registrants, present, names, now=now)


async def deliver(guild: Guild, appel: Appel) -> bool:
    """Send a check to its trainer in private.

    Args:
        guild (Guild): The check's server.
        appel (Appel): The check.

    Returns:
        bool: True when the trainer got it, False when there is no trainer to send it to or no way to reach them.
    """
    if appel.trainer_id is None:
        logger.warning(f"No trainer to send appel {appel.id} to in guild {guild.id}.")
        return False
    trainer = await get_or_fetch_member(guild, appel.trainer_id)
    if trainer is None:
        logger.error(f"Failed to fetch trainer {appel.trainer_id} for appel {appel.id} in guild {guild.id}.")
        return False
    content, view = appel_message(guild, appel, datetime.now(PARIS_TZ))
    sent = await send_dm_to_member(logger, guild, trainer, content, view=view, dm_type=f"attendance check {appel.id}")
    return sent is not None


async def start_appel(guild: Guild, published: PublishedMessage, position: int) -> bool:
    """Open the check of a training that starts now, once, and send it to its trainer.

    Args:
        guild (Guild): The server.
        published (PublishedMessage): The announcement the training belongs to.
        position (int): The training's place in it.

    Returns:
        bool: True when the check exists, new or already there, even if its trainer could not be reached; False when
            it could not be opened, so that the start is tried again.
    """
    training = published.message.fms[position]
    length = read_duration(training.duration) or SLOT_FALLBACK
    try:
        appel_id = attendance.open_appel(
            guild.id,
            published.message_id,
            position,
            trainer_id=trainer_id(training.trainer_mention),
            starts_at=training.start_dt,
            ends_at=training.start_dt + length,
        )
        appel = attendance.get(appel_id)
    except (LookupError, sqlite3.Error):
        logger.exception(f"Could not open the appel of message {published.message_id} in guild {guild.id}.")
        return False
    if appel is None:
        logger.error(f"Appel {appel_id} of message {published.message_id} in guild {guild.id} vanished after opening.")
        return False
    await deliver(guild, appel)
    return True


def _mention_blocks(label: str, user_ids: Sequence[int]) -> list[str]:
    """Write a labelled list of mentions as lines short enough for one message each; ``aucun`` when it is empty."""
    title = f"**{label} ({len(user_ids)})** :"
    if not user_ids:
        return [f"{title} aucun"]
    lines: list[str] = []
    current = ""
    for user_id in user_ids:
        mention = f"<@{user_id}>"
        if current and len(current) + 2 + len(mention) > _LIST_CHARS:
            lines.append(current)
            current = mention
        else:
            current = f"{current}, {mention}" if current else mention
    lines.append(current)
    return [f"{title} {lines[0]}", *lines[1:]]


def respo_messages(appel: Appel, here: Sequence[int], away: Sequence[int], *, first: bool) -> list[str]:
    """Write what the Respo Formation read after a validation, in as many messages as the lists need."""
    verb = "a été fait par" if first else "a été mis à jour par"
    header = f"Salut !\nL'appel de **{appel.name}** ({_slot(appel)}) {verb} <@{appel.validated_by}>.\n"
    return split_messages(header, [*_mention_blocks("Présents", here), *_mention_blocks("Absents", away)])


def log_line(appel: Appel, here: int, away: int, *, first: bool) -> str:
    """Write the line the log channel gets after a validation: the counts, and nobody who registered."""
    verb = "validé par" if first else "mis à jour par"
    return (
        f"Appel de **{appel.name}** ({discord_timestamp(appel.starts_at)}) {verb} <@{appel.validated_by}> : "
        f"{counted(here, 'présent', 'présents')}, {counted(away, 'absent', 'absents')}."
    )


async def notify_validation(guild: Guild, saved: Validation) -> None:
    """Tell each Respo Formation who came, then leave the counts in the log channel.

    Everything comes from what the validation saved, never from a later read, so a mark made afterwards or a second
    validation is not reported as this one. The reports of one check go out one after the other, and a report whose
    save a newer validation replaced is dropped, so the last one the Respo read is the newest. A Respo who cannot be
    reached does not stop the others, and a missing or refusing log channel stops nothing. A validation that leaves the
    present and absent members as the last report had them sends nothing: the click changed nothing to tell.

    Args:
        guild (Guild): The check's server.
        saved (Validation): What the validation saved.
    """
    appel = saved.appel
    if appel.validated_by is None:
        return
    async with _reporting.setdefault(appel.id, asyncio.Lock()):
        stored = attendance.get(appel.id)
        if stored is not None and stored.validated_at != appel.validated_at:
            logger.info(f"Dropped the report of appel {appel.id} in guild {guild.id}: a newer validation replaced it.")
            return
        lists = (frozenset(saved.here), frozenset(saved.away))
        if _reported.get(appel.id) == lists:
            logger.info(f"Skipped the report of appel {appel.id} in guild {guild.id}: nobody's presence changed.")
            return
        _reported.pop(appel.id, None)
        _reported[appel.id] = lists
        while len(_reported) > _KEPT:
            del _reported[next(iter(_reported))]
        messages = respo_messages(appel, saved.here, saved.away, first=saved.first)
        for respo in sorted(get_members_by_role(logger, guild, role=RoleNames.TRAININGS_MANAGER), key=lambda m: m.id):
            for text in messages:
                if await send_dm_to_member(logger, guild, respo, text, dm_type=f"attendance lists {appel.id}") is None:
                    break
        line = log_line(appel, len(saved.here), len(saved.away), first=saved.first)
        try:
            channel = guild.get_channel(get_log_channel_id(guild.id) or 0)
            if not isinstance(channel, TextChannel):
                logger.warning(f"No log channel for the counts of appel {appel.id} in guild {guild.id}.")
                return
            await channel.send(line, allowed_mentions=AllowedMentions.none())
        except (HTTPException, sqlite3.Error):
            logger.exception(f"Could not post the counts of appel {appel.id} in guild {guild.id}.")
