"""Message rendering and formatting for formations."""

from __future__ import annotations

from datetime import datetime, timedelta
import io
import logging
from typing import TYPE_CHECKING, Any

from discord.utils import get

from fablabot.helpers.constants import MAX_MSG_CHARS, PARIS_TZ, RoleNames
from fablabot.helpers.utils import escape_md, get_members_by_role

if TYPE_CHECKING:
    from discord import Guild

    from fablabot.models.formation import Formation

logger = logging.getLogger(__name__)

_FM_REQUEST_FORMS = "https://forms.office.com/e/MqVdQujzjf"


class Emojis:
    """Discord emojis used throughout the bot."""

    PEOPLE = ":busts_in_silhouette:"
    ARROW_RIGHT = ":arrow_right:"
    WARNING = ":warning:"
    LOUDSPEAKER = ":loudspeaker:"

    @staticmethod
    def get_clock_emoji(dt: datetime) -> str:
        """Get the clock emoji corresponding to the given hour and minute.

        Args:
            dt (datetime): The datetime to get the clock emoji for.

        Returns:
            str: The corresponding clock emoji.
        """
        dt = Emojis.round_hour(dt)
        clock_emojis = {
            (0, 0): ":clock12:",
            (0, 30): ":clock1230:",
            (1, 0): ":clock1:",
            (1, 30): ":clock130:",
            (2, 0): ":clock2:",
            (2, 30): ":clock230:",
            (3, 0): ":clock3:",
            (3, 30): ":clock330:",
            (4, 0): ":clock4:",
            (4, 30): ":clock430:",
            (5, 0): ":clock5:",
            (5, 30): ":clock530:",
            (6, 0): ":clock6:",
            (6, 30): ":clock630:",
            (7, 0): ":clock7:",
            (7, 30): ":clock730:",
            (8, 0): ":clock8:",
            (8, 30): ":clock830:",
            (9, 0): ":clock9:",
            (9, 30): ":clock930:",
            (10, 0): ":clock10:",
            (10, 30): ":clock1030:",
            (11, 0): ":clock11:",
            (11, 30): ":clock1130:",
        }
        return clock_emojis.get((dt.hour % 12, dt.minute), ":clock12:")

    @staticmethod
    def round_hour(dt: datetime) -> datetime:
        """Round a datetime to the nearest hour emoji.

        Args:
            dt (datetime): The datetime to round.

        Returns:
            datetime: The rounded datetime.
        """
        if dt.minute >= 45:
            dt += timedelta(hours=1)
            dt = dt.replace(minute=0, second=0, microsecond=0)
        elif dt.minute < 15:
            dt = dt.replace(minute=0, second=0, microsecond=0)
        else:
            dt = dt.replace(minute=30, second=0, microsecond=0)
        return dt


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
            f"{fm.emoji} **{fm.name}** avec {fm.trainer_mention}{' (excusable)' if fm.excusable else ''}",
            f"> {humanize_dt(fm.start_dt)}  – "  # noqa: RUF001
            f"{Emojis.get_clock_emoji(fm.start_dt)} {fm.duration}  – "  # noqa: RUF001
            f"{Emojis.PEOPLE} {len(fm.registered_users)}/{fm.seats} places",
        ]
        line_block += [f"> {fm.description}"] if fm.description else []
        lines.append("\n".join(line_block))
        lines.append("")

    end_lines = [
        f"{Emojis.ARROW_RIGHT} Pour s'inscrire, réagis avec les émojis des formations correspondantes.",
        f"{Emojis.WARNING} Si tu ne peux plus venir, n'oublie pas de retirer ta réaction pour libérer la place.",
        "",
        f"Tu veux apprendre autre chose ? [**Propose une formation ici**]({_FM_REQUEST_FORMS})",
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
    role = get(guild.roles, name=RoleNames.TRAININGS_MANAGER)
    if role is None:
        logger.debug(f"Role '{RoleNames.TRAININGS_MANAGER}' missing in guild {guild.id}; using fallback contacts.")
        return "un·e membre du Pôle Formations"
    members = get_members_by_role(role=role)
    if not members:
        logger.debug(f"Role '{RoleNames.TRAININGS_MANAGER}' has no human members in guild {guild.id}; using fallback contacts.")
        return "un·e membre du Pôle Formations"
    mentions = [m.mention for m in members]
    logger.debug(f"Resolved {len(mentions)} formation contacts for guild {guild.id}.")
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
            ts = user_entry.get("ts_iso", datetime.min.replace(tzinfo=PARIS_TZ).isoformat(timespec="seconds"))
            dt = datetime.fromisoformat(ts)
            when = humanize_dt(dt).lower()[2:-2] if dt != datetime.min.replace(tzinfo=PARIS_TZ) else "n/a"
            lines.append(f"{idx}. <@{user_id}> ({escape_md(username)}) · inscrit·e le {when}")
    else:
        lines.append("_(Aucune inscription)_")

    if formation.waitlisted_users:
        for idx, user_entry in enumerate(formation.waitlisted_users, start=len(formation.registered_users) + 1):
            user_id = user_entry.get("user_id")
            username = user_entry.get("username", "Utilisateur inconnu")
            ts = user_entry.get("ts_iso", datetime.min.replace(tzinfo=PARIS_TZ).isoformat(timespec="seconds"))
            dt = datetime.fromisoformat(ts)
            when = humanize_dt(dt).lower()[2:-2] if dt != datetime.min.replace(tzinfo=PARIS_TZ) else "n/a"
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
