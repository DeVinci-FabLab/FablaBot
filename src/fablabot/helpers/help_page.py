"""Build the ``/help`` embed from the commands a caller may run."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from discord import Embed, app_commands

from fablabot.helpers.access import Caller, rule_of
from fablabot.helpers.constants import BRAND_COLOUR

if TYPE_CHECKING:
    from collections.abc import Iterable

    from fablabot.helpers.i18n import Texts

EVERYONE_DOMAIN: Final = "everyone"
HELP_COMMAND: Final = "help"

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


def build_help(commands: Iterable[app_commands.Command[Any, ..., Any]], caller: Caller, texts: Texts) -> list[Embed]:
    """Build the help of one caller: the commands their roles open, by domain, then how to get more.

    A command without an access rule is never listed.

    Args:
        commands (Iterable[app_commands.Command[Any, ..., Any]]): The runnable commands of the bot.
        caller (Caller): The member asking.
        texts (Texts): The texts of the reply's locale.

    Returns:
        list[Embed]: The help messages; one embed here.
    """
    admitted: dict[str, app_commands.Command[Any, ..., Any]] = {}
    for command in commands:
        rule = rule_of(command)
        if rule is not None and rule.admits(caller):
            admitted[command.qualified_name] = command

    embed = Embed(title=texts.get("help.title"), description=texts.get("help.member_intro"), colour=BRAND_COLOUR)
    for domain, names in DOMAINS:
        lines: list[str] = []
        for name in names:
            qualified = name if domain == EVERYONE_DOMAIN else f"{domain} {name}"
            if qualified not in admitted:
                continue
            shown = texts.get("help.slash.name") if qualified == HELP_COMMAND else qualified
            lines.append(texts.get("help.line", command=f"`/{shown}`", text=texts.get(line_key(qualified))))
        if lines:
            embed.add_field(name=texts.get(f"help.domain.{domain}"), value="\n".join(lines), inline=False)
    embed.add_field(name=texts.get("help.more.name"), value=texts.get("help.more.value"), inline=False)
    embed.set_footer(text=texts.get("help.footer"))
    return [embed]
