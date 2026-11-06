"""Build the ``/info`` embed: how the bot stands, then what this server lacks, in French and within Discord's limits."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import TYPE_CHECKING, Final

from discord import Embed

from fablabot.helpers.help_page import FIELD_VALUE_CHARS
from fablabot.helpers.problem_text import LANGUAGE_NAMES, describe
from fablabot.helpers.server_check import FIXES
from fablabot.helpers.utils import counted

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import timedelta

    from fablabot.helpers.server_check import Problem

GREEN: Final = 0x57F287
ORANGE: Final = 0xF5A623
RED: Final = 0xED4245
"""Embed colours by verdict: nothing to fix, warnings only, anything that blocks."""

REVISION_ENV: Final = "FABLABOT_REVISION"
"""The variable that holds the commit the image was built from."""

_SECONDS_PER_MINUTE: Final = 60
_MINUTES_PER_HOUR: Final = 60
_HOURS_PER_DAY: Final = 24
_BYTES_PER_KILO: Final = 1_000
_BYTES_PER_MEGA: Final = 1_000_000
_REVISION: Final = re.compile(r"[0-9a-f]{7,40}")
_REVISION_CHARS: Final = 7


@dataclass(frozen=True)
class Facts:
    """What the embed says about the bot, its storage, the instance and the server that asked.

    Attributes:
        version (str): The installed release.
        uptime (timedelta): Time since the process started.
        latency_ms (int | None): Discord's gateway latency in milliseconds, None when not measured yet.
        schema_version (int): The database schema version.
        database_bytes (int): The database file's size.
        copies (int): The database copies kept in ``backups/``.
        servers (int): The servers the instance serves, as a count.
        commands (int): The top-level commands the bot synced.
        watched (int): The accounts whose changes are posted in the log channel, as a count.
        commands_channel_id (int | None): This server's commands channel, None when it has none.
        log_channel_id (int | None): This server's log channel, None when it has none that exists.
        language (tuple[str, str]): The language this server declares and the one the bot's texts use.
        revision (str | None): The short commit of the running image, None when the image gives none.
    """

    version: str
    uptime: timedelta
    latency_ms: int | None
    schema_version: int
    database_bytes: int
    copies: int
    servers: int
    commands: int
    watched: int
    commands_channel_id: int | None
    log_channel_id: int | None
    language: tuple[str, str]
    revision: str | None


def short_revision(raw: str | None) -> str | None:
    """Shorten the image's commit hash for the footer.

    Args:
        raw (str | None): The value the image carries, normally a full lower-case commit hash.

    Returns:
        str | None: Its first seven characters, or None when the value is not a commit hash, so that nothing else, a
            pasted secret included, is ever shown.
    """
    return raw[:_REVISION_CHARS] if raw is not None and _REVISION.fullmatch(raw) else None


def _bold_count(count: int, one: str, many: str) -> str:
    """Write a count with its noun in the right number, the number in bold."""
    number, _, noun = counted(count, one, many).partition(" ")
    return f"**{number}** {noun}"


def _duration(elapsed: timedelta) -> str:
    """Write a duration as a person would say it: minutes, hours with minutes, days with hours."""
    minutes = int(elapsed.total_seconds()) // _SECONDS_PER_MINUTE
    hours, minutes = divmod(minutes, _MINUTES_PER_HOUR)
    days, hours = divmod(hours, _HOURS_PER_DAY)
    if days:
        return f"{days} j {hours} h" if hours else f"{days} j"
    if hours:
        return f"{hours} h {minutes} min" if minutes else f"{hours} h"
    return f"{minutes} min" if minutes else "moins d'une minute"


def _size(octets: int) -> str:
    """Write a file size with French units and a decimal comma."""
    if octets < _BYTES_PER_KILO:
        return f"{octets} o"
    value, unit = (octets / _BYTES_PER_MEGA, "Mo") if octets >= _BYTES_PER_MEGA else (octets / _BYTES_PER_KILO, "Ko")
    return f"{value:.1f} {unit}".replace(".", ",")


def _language(declared: str, texts: str) -> str:
    """Name the server's language in words; one the bot lacks says which language its texts use instead."""
    if declared.split("-")[0] == texts:
        return LANGUAGE_NAMES.get(texts, texts)
    return f"{declared}, textes en {LANGUAGE_NAMES.get(texts, texts)}"


def _mention(channel_id: int | None) -> str:
    """Show a channel as a mention, or say the server has none."""
    return "aucun salon" if channel_id is None else f"<#{channel_id}>"


def _problems(problems: Sequence[Problem]) -> str:
    """List the problems, blocking ones first, each once, cut to the field's limit with the count of those left out."""
    ordered = sorted(problems, key=lambda problem: FIXES[problem.code][0] != "blocking")
    entries = list(dict.fromkeys(describe(problem) for problem in ordered))
    for kept in range(len(entries), 0, -1):
        tail = [f"… et {counted(len(entries) - kept, 'autre', 'autres')}"] if kept < len(entries) else []
        value = "\n\n".join([*entries[:kept], *tail])
        if len(value) <= FIELD_VALUE_CHARS:
            return value
    return f"… et {counted(len(entries), 'autre', 'autres')}"


def build_info(facts: Facts, problems: Sequence[Problem]) -> Embed:
    """Build the answer of ``/info``: the verdict, the bot, the database, the instance, this server's settings and problems.

    Args:
        facts (Facts): What is known about the bot and the server.
        problems (Sequence[Problem]): What the checks found on this server, and no other.

    Returns:
        Embed: The embed, green, orange or red after the worst problem.
    """
    blocking = sum(FIXES[problem.code][0] == "blocking" for problem in problems)
    warnings = len(problems) - blocking
    if blocking:
        title = f"FablaBot : {counted(blocking, 'point bloquant', 'points bloquants')} sur ce serveur"
        colour, mark = RED, "❌"
    elif warnings:
        title = f"FablaBot : {counted(warnings, 'point', 'points')} à corriger sur ce serveur"
        colour, mark = ORANGE, "⚠️"
    else:
        title, colour, mark = "FablaBot : tout va bien", GREEN, ""
    counts = f"{counted(warnings, 'avertissement', 'avertissements')}, {counted(blocking, 'bloquant', 'bloquants')}"
    verdict = f"{mark} ce serveur a {counts}." if mark else "ce serveur n'a aucun problème."
    embed = Embed(title=f"📊 {title}", description=f"✅ Le bot tourne normalement · {verdict}", colour=colour)

    bot = [f"Version **{facts.version}**", f"En ligne depuis **{_duration(facts.uptime)}**"]
    if facts.latency_ms is not None:
        bot.append(f"Latence **{facts.latency_ms} ms**")
    embed.add_field(name="🤖 Bot", value="\n".join(bot))
    embed.add_field(
        name="🗃️ Base de données",
        value=f"Version **{facts.schema_version}** · {_size(facts.database_bytes)}\n"
        + _bold_count(facts.copies, "sauvegarde", "sauvegardes"),
    )
    embed.add_field(
        name="🛰️ Instance",
        value="\n".join(
            (
                _bold_count(facts.servers, "serveur servi", "serveurs servis"),
                _bold_count(facts.commands, "commande", "commandes"),
                _bold_count(facts.watched, "compte surveillé", "comptes surveillés"),
            )
        ),
    )
    embed.add_field(
        name="⚙️ Réglages de ce serveur",
        value=f"Commandes : {_mention(facts.commands_channel_id)}\nLogs : {_mention(facts.log_channel_id)}\n"
        f"Langue : {_language(*facts.language)}",
        inline=False,
    )
    if problems:
        embed.add_field(name="🔍 À corriger sur ce serveur", value=_problems(problems), inline=False)
    revision = f" · révision {facts.revision}" if facts.revision else ""
    return embed.set_footer(text=f"Contrôle relancé à l'instant{revision}")
