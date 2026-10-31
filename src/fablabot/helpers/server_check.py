"""Report at start what each served server lacks, from what the bot already knows. Reads only, writes nothing."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import TYPE_CHECKING, Final, Literal

from discord import CategoryChannel, Permissions, TextChannel
from discord.utils import get

from fablabot.guild_config import get_log_channel_id, is_welcome_verify_enabled
from fablabot.helpers.constants import ADMIN_ROLES, RoleNames
from fablabot.helpers.i18n import REFERENCE, catalog
from fablabot.helpers.utils import COMMANDS_CHANNEL_NAME, find_commands_channel
from fablabot.logging_handlers import guild_log_context
from fablabot.models.message import SUGGESTION_OPTIONS

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence

    from discord import Guild, Role

logger = logging.getLogger(__name__)

INVITE_PERMISSIONS: Final = Permissions(268561616)
"""The permissions the invitation link asks for, as written in the operator guide."""

type Severity = Literal["blocking", "warning"]

FIXES: Final[Mapping[str, tuple[Severity, str]]] = {
    "commands_channel_missing": ("blocking", f"create a text channel named {COMMANDS_CHANNEL_NAME}"),
    "commands_channel_no_post": ("warning", "let the bot view and post in the commands channel"),
    "log_channel_missing": ("blocking", "run /log set in the commands channel"),
    "log_channel_no_post": ("blocking", "let the bot view, post and attach files in the log channel, or run /log set"),
    "permissions_missing": ("blocking", "give these permissions to the bot's role, or invite the bot again"),
    "role_order_temp": ("blocking", f"drag the bot's role above {RoleNames.ADMIN_TEMP}"),
    "role_order": ("warning", "drag the bot's role above these roles"),
    "roles_missing": ("blocking", "create the role with this exact name"),
    "welcome_setup_missing": ("blocking", "create what the welcome flow needs, or turn it off with /welcome verify"),
    "suggest_recipient_unreachable": ("warning", "give the role to a member or create the channel"),
    "language_not_shipped": ("warning", "pick a shipped language in Server Settings, Community, Overview"),
    "check_failed": (
        "warning",
        "read the next start; HTTP 403 on commands means the invite lacked applications.commands",
    ),
}
"""Severity and fix text of each code: a code cannot exist without a fix."""

_KNOWN_ROLES: Final = frozenset(value for name, value in vars(RoleNames).items() if name.isupper())
_REQUIRED_ROLES: Final = (RoleNames.CODIR, RoleNames.ADMIN_TEMP)
_ORDER_PREFIXES: Final = ("Pôle ", "F - ")
_ONE_ADMIN: Final = f"one of {' | '.join(sorted(ADMIN_ROLES))}"


@dataclass(frozen=True)
class Problem:
    """One thing a server lacks.

    Attributes:
        code (str): A key of ``FIXES``.
        detail (str): What the code applies to: IDs and names the code gives, never a member name.
    """

    code: str
    detail: str = ""


def _label(role: Role) -> str:
    """Name a role for the log: by name when the code names it, else by ID, since the staff's own names may hold a name.

    Args:
        role (Role): The role.

    Returns:
        str: The name of a role in ``RoleNames``, otherwise ``role_id=<id>``.
    """
    return role.name if role.name in _KNOWN_ROLES else f"role_id={role.id}"


def _check_commands_channel(guild: Guild) -> list[Problem]:
    """Check that commands have a channel to run in, and that the bot can post there."""
    channel = find_commands_channel(guild)
    if channel is None:
        return [Problem("commands_channel_missing")]
    allowed = channel.permissions_for(guild.me)
    if not (allowed.view_channel and allowed.send_messages):
        return [Problem("commands_channel_no_post", f"channel_id={channel.id}")]
    return []


def _check_log_channel(guild: Guild) -> list[Problem]:
    """Check that the stored log channel is a text channel of the server where the bot can post files."""
    stored_id = get_log_channel_id(guild.id)
    channel = guild.get_channel(stored_id) if stored_id is not None else None
    if not isinstance(channel, TextChannel):
        return [Problem("log_channel_missing", f"stored_id={stored_id}")]
    allowed = channel.permissions_for(guild.me)
    if not (allowed.view_channel and allowed.send_messages and allowed.attach_files):
        return [Problem("log_channel_no_post", f"channel_id={channel.id}")]
    return []


def _check_permissions(guild: Guild) -> list[Problem]:
    """Check that the bot has the permissions of the invitation."""
    held = guild.me.guild_permissions
    missing = [name for name, wanted in INVITE_PERMISSIONS if wanted and not getattr(held, name)]
    return [Problem("permissions_missing", ", ".join(missing))] if missing else []


def _check_roles(guild: Guild) -> list[Problem]:
    """Check the roles the bot cannot work without: CoDir, Admin -temp- and one admin role."""
    present = {role.name for role in guild.roles}
    missing = [name for name in _REQUIRED_ROLES if name not in present]
    if not ADMIN_ROLES & present:
        missing.append(_ONE_ADMIN)
    return [Problem("roles_missing", ", ".join(missing))] if missing else []


def _check_role_order(guild: Guild) -> list[Problem]:
    """Check that the roles the bot gives or removes sit below its own role."""
    top = guild.me.top_role
    above = [role for role in guild.roles if role > top]
    problems: list[Problem] = []
    if any(role.name == RoleNames.ADMIN_TEMP for role in above):
        problems.append(Problem("role_order_temp"))
    managed = [
        role
        for role in above
        if role.name not in {RoleNames.ADMIN, RoleNames.ADMIN_TEMP}
        and (role.name in _KNOWN_ROLES or role.name.startswith(_ORDER_PREFIXES))
    ]
    if managed:
        problems.append(Problem("role_order", ", ".join(_label(role) for role in managed)))
    return problems


def _check_welcome(guild: Guild) -> list[Problem]:
    """Check what the welcome flow needs, when it is on."""
    if not is_welcome_verify_enabled(guild.id):
        return []
    missing: list[str] = []
    if not isinstance(get(guild.categories, name="Validation"), CategoryChannel):
        missing.append("category Validation")
    if get(guild.roles, name=RoleNames.MEMBER_VERIFIED) is None:
        missing.append(f"role {RoleNames.MEMBER_VERIFIED}")
    return [Problem("welcome_setup_missing", ", ".join(missing))] if missing else []


def _check_suggestions(guild: Guild) -> list[Problem]:
    """Check that each recipient of ``/suggest`` has a member holding its role or a channel."""
    unreachable: list[str] = []
    for key, option in SUGGESTION_OPTIONS.items():
        role = get(guild.roles, name=option.role_name) if option.role_name else None
        channel = get(guild.text_channels, name=option.channel_name) if option.channel_name else None
        if not (role is not None and role.members) and channel is None:
            unreachable.append(key)
    return [Problem("suggest_recipient_unreachable", ", ".join(unreachable))] if unreachable else []


def _languages(guild: Guild) -> tuple[str, str]:
    """Return the language the server declares and the one the bot's texts will use."""
    locale = str(guild.preferred_locale)
    return locale, catalog().texts(locale).locale


def _check_language(guild: Guild) -> list[Problem]:
    """Check that the declared language has a shipped file; the help is in English otherwise."""
    locale, texts = _languages(guild)
    if texts == REFERENCE and locale.split("-")[0] != REFERENCE:
        return [Problem("language_not_shipped", f"locale={locale}")]
    return []


_LOCAL_CHECKS: Final[tuple[Callable[[Guild], list[Problem]], ...]] = (
    _check_commands_channel,
    _check_log_channel,
    _check_permissions,
    _check_roles,
    _check_role_order,
    _check_welcome,
    _check_suggestions,
    _check_language,
)


def _check_server(guild: Guild) -> list[Problem]:
    """Run every check on a server; one that fails is reported and the others still run.

    Args:
        guild (Guild): The server to read.

    Returns:
        list[Problem]: What the server lacks, in the order of the checks.
    """
    if guild.unavailable:
        return [Problem("check_failed", "step=unavailable")]
    problems: list[Problem] = []
    for check in _LOCAL_CHECKS:
        try:
            problems.extend(check(guild))
        except Exception as exc:  # a broken check must not hide the other servers
            problems.append(Problem("check_failed", f"step=local status={type(exc).__name__}"))
    return problems


def _report(guild: Guild, problems: Sequence[Problem]) -> None:
    """Write the summary, the optional-roles note and one warning per problem, all tied to the server."""
    blocking = sum(FIXES[problem.code][0] == "blocking" for problem in problems)
    locale, texts = ("unknown", "unknown") if guild.unavailable else _languages(guild)
    with guild_log_context(guild.id):
        logger.info(
            f"Server check: guild_id={guild.id} blocking={blocking} warnings={len(problems) - blocking} "
            f"locale={locale} texts={texts}",
        )
        if not guild.unavailable:
            optional = _KNOWN_ROLES - {role.name for role in guild.roles} - ADMIN_ROLES - set(_REQUIRED_ROLES)
            if optional:
                logger.info(f"Server check: optional roles absent (guild_id={guild.id}): {', '.join(sorted(optional))}")
        for problem in problems:
            severity, fix = FIXES[problem.code]
            logger.warning(
                f"Server check problem: guild_id={guild.id} severity={severity} code={problem.code} "
                f"detail=[{problem.detail}] fix: {fix}",
            )


async def check_servers(guilds: Iterable[Guild]) -> None:
    """Read each server and write its report to the log; nothing is changed on Discord or in the stored settings.

    Args:
        guilds (Iterable[Guild]): The servers the bot serves.
    """
    for guild in guilds:
        _report(guild, _check_server(guild))
