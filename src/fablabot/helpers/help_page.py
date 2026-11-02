"""Build the ``/help`` embed from the commands a caller may run."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from discord import Embed, app_commands
from discord.utils import escape_markdown, escape_mentions

from fablabot.helpers.access import Caller, rule_of
from fablabot.helpers.constants import BRAND_COLOUR

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from fablabot.helpers.i18n import Texts

EVERYONE_DOMAIN: Final = "everyone"
HELP_COMMAND: Final = "help"

# Discord's per-message limits: https://discord.com/developers/docs/resources/message#embed-object-embed-limits
MESSAGE_CHARS: Final = 6000
FIELDS: Final = 25
FIELD_VALUE_CHARS: Final = 1024

DOMAINS: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    ("text", ("create", "rename", "delete")),
    ("vocal", ("create", "rename", "delete")),
    ("fm", ("start", "edit_text", "add", "edit", "remove", "clear", "preview", "publish", "export")),
    (
        "msg",
        (
            "start",
            "preview",
            "publish",
            "follow",
            "link_reaction",
            "unlink_reaction",
            "list",
            "export",
            "stop",
            "clear",
            "purge",
            "dm",
        ),
    ),
    ("user", ("op", "deop", "add_role", "remove_role", "add_roles", "remove_roles", "with_roles")),
    ("welcome", ("verify", "approve")),
    ("log", ("set", "export")),
    (EVERYONE_DOMAIN, ("suggest", HELP_COMMAND)),
)
"""The help's domains in reply order, each with its commands in line order (names within the group)."""


def line_key(qualified_name: str) -> str:
    """Return the catalogue key of a command's line.

    Args:
        qualified_name (str): The command's full name, such as ``fm start``.

    Returns:
        str: The key, such as ``help.cmd.fm.start``.
    """
    return "help.cmd." + qualified_name.replace(" ", ".")


def leaf_commands(items: Iterable[Any]) -> list[app_commands.Command[Any, ..., Any]]:
    """Keep the commands that run something: groups are replaced by their commands, other items dropped.

    Args:
        items (Iterable[Any]): Commands and groups, as a tree or a cog declares them.

    Returns:
        list[app_commands.Command[Any, ..., Any]]: The runnable commands.
    """
    leaves: list[app_commands.Command[Any, ..., Any]] = []
    for item in items:
        if isinstance(item, app_commands.Group):
            leaves.extend(leaf_commands(item.commands))
        elif isinstance(item, app_commands.Command):
            leaves.append(item)
    return leaves


def _pieces(name: str, lines: Sequence[str]) -> list[tuple[str, str]]:
    """Cut a domain's lines into field values of at most ``FIELD_VALUE_CHARS``, at line ends, under the same heading."""
    pieces: list[tuple[str, str]] = []
    current: list[str] = []
    size = 0
    for line in lines:
        if current and size + 1 + len(line) > FIELD_VALUE_CHARS:
            pieces.append((name, "\n".join(current)))
            current, size = [], 0
        size += len(line) + (1 if current else 0)
        current.append(line)
    if current:
        pieces.append((name, "\n".join(current)))
    return pieces


def paginate(title: str, description: str, fields: Sequence[tuple[str, Sequence[str]]], footer: str) -> list[Embed]:
    """Lay fields out in as few messages as Discord's limits allow.

    The first embed holds the title and the description, the last one the footer; the footer's length is kept free in
    every embed since any of them may end up last.

    Args:
        title (str): The first embed's title.
        description (str): The first embed's description.
        fields (Sequence[tuple[str, Sequence[str]]]): Each field's heading and its lines.
        footer (str): The last embed's footer.

    Returns:
        list[Embed]: The embeds, one per message.
    """
    embeds = [Embed(title=title, description=description, colour=BRAND_COLOUR)]
    for name, lines in fields:
        for _, value in _pieces(name, lines):
            last = embeds[-1]
            if len(last.fields) >= FIELDS or len(last) + len(name) + len(value) + len(footer) > MESSAGE_CHARS:
                last = Embed(colour=BRAND_COLOUR)
                embeds.append(last)
            last.add_field(name=name, value=value, inline=False)
    embeds[-1].set_footer(text=footer)
    return embeds


def _bold(name: str) -> str:
    """Return a role name in bold, with its markdown and mentions made harmless."""
    return f"**{escape_mentions(escape_markdown(name))}**"


def _summary(staff: Iterable[app_commands.Command[Any, ..., Any]], caller: Caller, texts: Texts) -> str:
    """Say which of the caller's roles open the staff commands, and how many commands that is."""
    commands = list(staff)
    roles = [
        _bold(role)
        for role in caller.roles
        if any(rule_of(command).admits(caller.only(role)) for command in commands)  # pyright: ignore[reportOptionalMemberAccess]  # listed commands have a rule
    ]
    if not roles:
        return texts.plural("help.summary.permissions", len(commands))
    if len(roles) == 1:
        return texts.plural("help.summary.one_role", len(commands), roles=roles[0])
    joined = texts.get("help.roles_join", head=", ".join(roles[:-1]), last=roles[-1])
    return texts.plural("help.summary.many_roles", len(commands), roles=joined)


def build_help(commands: Iterable[app_commands.Command[Any, ..., Any]], caller: Caller, texts: Texts) -> list[Embed]:
    """Build the help of one caller: the commands their roles open, by domain.

    A command without an access rule is never listed. A caller who can run staff commands gets a summary of the roles
    that open them; the others get how to get more.

    Args:
        commands (Iterable[app_commands.Command[Any, ..., Any]]): The runnable commands of the bot.
        caller (Caller): The member asking.
        texts (Texts): The texts of the reply's locale.

    Returns:
        list[Embed]: The help messages, more than one when the list is long.
    """
    admitted: dict[str, app_commands.Command[Any, ..., Any]] = {}
    for command in commands:
        rule = rule_of(command)
        if rule is not None and rule.admits(caller):
            admitted[command.qualified_name] = command

    fields: list[tuple[str, list[str]]] = []
    staff: list[app_commands.Command[Any, ..., Any]] = []
    for domain, names in DOMAINS:
        lines: list[str] = []
        for name in names:
            qualified = name if domain == EVERYONE_DOMAIN else f"{domain} {name}"
            if qualified not in admitted:
                continue
            if domain != EVERYONE_DOMAIN:
                staff.append(admitted[qualified])
            shown = texts.get("help.slash.name") if qualified == HELP_COMMAND else qualified
            lines.append(texts.get("help.line", command=f"`/{shown}`", text=texts.get(line_key(qualified))))
        if lines:
            fields.append((texts.get(f"help.domain.{domain}"), lines))
    if staff:
        description = _summary(staff, caller, texts)
    else:
        description = texts.get("help.member_intro")
        fields.append((texts.get("help.more.name"), [texts.get("help.more.value")]))
    return paginate(texts.get("help.title"), description, fields, texts.get("help.footer"))
