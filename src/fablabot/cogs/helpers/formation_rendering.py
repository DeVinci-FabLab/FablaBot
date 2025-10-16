"""Message rendering and formatting for formations."""

from __future__ import annotations

from datetime import datetime
import io
import logging
from typing import TYPE_CHECKING, Any

from discord import Guild
from discord.utils import get

from fablabot.cogs.helpers.constants import MAX_MSG_CHARS, Emojis, RoleNames
from fablabot.cogs.helpers.utils import escape_md

if TYPE_CHECKING:
    from fablabot.cogs.helpers.formation_models import Formation

logger = logging.getLogger(__name__)

FM_REQUEST_FORMS = "https://forms.office.com/e/MqVdQujzjf"


def parse_date_time(date_str: str, hour_str: str, timezone: Any) -> datetime:
    """Parse date and time strings into a timezone-aware datetime object.

    Args:
        date_str (str): Date string in 'DD/MM/YYYY' format.
        hour_str (str): Hour string in 'HH:MM' format.
        timezone (Any): The timezone to use (e.g., ZoneInfo("Europe/Paris")).

    Returns:
        datetime: A timezone-aware datetime object.
    """
    d, m, y = map(int, date_str.split("/"))
    hh, mm = map(int, hour_str.split(":"))
    dt = datetime(y, m, d, hh, mm, tzinfo=timezone)
    logger.debug(f"Parsed formation schedule {date_str} {hour_str} -> {dt.isoformat()}")
    return dt


def humanize_dt(dt: datetime) -> str:
    """Humanize a datetime object.

    Args:
        dt (datetime): The datetime object to humanize.

    Returns:
        str: The humanized date/time string.
    """
    days = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
    day_name = days[dt.weekday()]
    date_part = dt.strftime("%d/%m")
    time_part = dt.strftime("%Hh%M")
    return f"**{day_name} {date_part} à {time_part}**"


def render_message(
    header: str,
    role_id: int,
    intro: str,
    fms: list[Formation],
    end: str,
) -> str:
    """Render the message for the formations.

    Args:
        header (str): The header line (e.g. title).
        role_id (int): The role mention to prepend.
        intro (str): The introduction text.
        fms (list[Formation]): The list of formations to include in the message.
        end (str): The ending text.

    Returns:
        str: The rendered message.
    """
    logger.debug("Rendering formations message.")
    lines: list[str] = []
    header_text = header.strip()
    intro_block = intro.strip()
    lines.append(header_text)
    lines.append(f"Hey <@&{role_id}> !")
    if intro_block:
        lines.append(intro_block)
    lines.append("")

    for fm in fms:
        line_block = [
            f"{fm.emoji} **{fm.name}** avec {fm.trainer_mention}",
            f"{Emojis.DATE} {humanize_dt(fm.start_dt)}  — "
            f"{Emojis.HOURGLASS} {fm.duration}  — "
            f"{Emojis.PEOPLE} {len(fm.registered_users)}/{fm.seats} place(s)",
        ]
        line_block += [fm.description] if fm.description else []
        lines.append("\n".join(line_block))
        lines.append("")

    end_lines = [
        f"{Emojis.ARROW_RIGHT} Pour s'inscrire, réagis avec les émojis des formations correspondantes.",
        f"{Emojis.WARNING} Si tu ne peux plus venir, n'oublie pas de retirer ta réaction pour libérer la place.",
        "",
        f"Tu veux apprendre autre chose ? [**Propose une formation ici**]({FM_REQUEST_FORMS})",
    ]
    lines.append("\n".join(end_lines))
    lines.append("")

    if end.strip():
        lines.append(end.strip())
        lines.append("")

    if lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def format_respo_contacts(guild: Guild) -> str:
    """Build the contact string for formation managers.

    Args:
        guild (Guild): The guild to get the role from.

    Returns:
        str: The contact string.
    """
    logger.debug(f"Resolving formation contacts for guild {guild.id}.")
    role = get(guild.roles, name=RoleNames.RESPO_FORMATIONS)
    if role is None:
        logger.debug(f"Role '{RoleNames.RESPO_FORMATIONS}' missing in guild {guild.id}; using fallback contacts.")
        return "un·e membre du Pôle Formations"
    members = [member for member in role.members if not member.bot]
    if not members:
        logger.debug(f"Role '{RoleNames.RESPO_FORMATIONS}' has no human members in guild {guild.id}; using fallback contacts.")
        return "un·e membre du Pôle Formations"
    mentions = [member.mention for member in members]
    logger.debug(f"Resolved {len(mentions)} formation manager contacts for guild {guild.id}.")
    return " ou ".join(mentions)


def format_formation_export(formation: Formation) -> str:
    """Format the export of a single formation with registered and waitlisted users.

    Args:
        formation (Formation): The formation to export.

    Returns:
        str: The formatted export string.
    """
    lines: list[str] = []

    lines.append(f"{formation.emoji} **{formation.name}** ({formation.seats} place{'s' if formation.seats != 1 else ''})")

    if formation.registered_users:
        for idx, user_entry in enumerate(formation.registered_users, start=1):
            user_id = user_entry.get("user_id")
            username = user_entry.get("username", "Utilisateur inconnu")
            ts = user_entry.get("ts_iso", datetime.min.isoformat(timespec="seconds"))
            dt = datetime.fromisoformat(ts)
            when = humanize_dt(dt).lower()[2:-2] if dt != datetime.min else "n/a"
            lines.append(f"{idx}. <@{user_id}> ({escape_md(username)}) · inscrit·e le {when}")
    else:
        lines.append("_(Aucune inscription)_")

    if formation.waitlisted_users:
        for idx, user_entry in enumerate(formation.waitlisted_users, start=len(formation.registered_users) + 1):
            user_id = user_entry.get("user_id")
            username = user_entry.get("username", "Utilisateur inconnu")
            ts = user_entry.get("ts_iso", datetime.min)
            dt = datetime.fromisoformat(ts)
            when = humanize_dt(dt).lower()[2:-2] if dt != datetime.min else "n/a"
            lines.append(f"{idx}. <@{user_id}> ({escape_md(username)}) · inscrit·e le {when} (en attente)")

    return "\n".join(lines)


def format_current_registrations(formations: list[Formation]) -> tuple[str, io.BytesIO | None]:
    """Format the current registrations for each formation using data from guild state.

    Args:
        formations (list[Formation]): The list of formations with up-to-date registration data.

    Returns:
        tuple[str, io.BytesIO | None]: The formatted message and an optional file object.
    """
    lines: list[str] = []
    lines.append("**Inscriptions actuelles par formation (ordre d'inscription)**")
    lines.append("")

    for formation in formations:
        formation_export = format_formation_export(formation)
        lines.append(formation_export)
        lines.append("")

    text = "\n".join(lines).strip()

    text_length = len(text)
    file_obj: io.BytesIO | None = None
    if text_length > MAX_MSG_CHARS:
        file_obj = io.BytesIO(text.encode("utf-8"))
        logger.debug(f"Registrations export exceeded {MAX_MSG_CHARS} chars ({text_length}); switching to attachment.")
        text = "**Inscriptions actuelles par formation (extrait)**\nLe contenu complet est joint en fichier texte."
    else:
        logger.debug(f"Registrations export length: {text_length} characters.")

    return text, file_obj
