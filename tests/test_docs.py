"""Pin what the user guide, the operator guide and the README say about /help."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from fablabot.cogs import user_management
from fablabot.helpers.access import EVERYONE, AnyRole, rule_of
from fablabot.helpers.constants import ADMIN_ROLES, RoleNames
from fablabot.helpers.help_page import leaf_commands

ROOT = Path(__file__).resolve().parent.parent
DOMAINS = (
    "Text channels",
    "Voice channels",
    "Trainings",
    "Bot messages",
    "Members and roles",
    "Welcoming newcomers",
    "Bot logs",
    "For everyone",
)


TABLE_HEADER = "| Command | What it does | Who can run it |"
ADMIN_CELL = "Admin roles"
EVERYONE_CELL = "Everyone"
PERMISSION_CELLS = ("The Manage Messages permission", "The Administrator permission")
ROLE_SUMMARY_CELL = "Depends on the role, see below"
SAME_CELL = "Same"


def _read(relative: str) -> str:
    """Return a repository file as text."""
    return (ROOT / relative).read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    """Return the body of the section that starts at ``heading``, up to the next heading of the same or higher level."""
    assert f"{heading}\n" in text, heading
    depth = len(heading) - len(heading.lstrip("#"))
    body = text.split(f"{heading}\n", 1)[1]
    return re.split(rf"\n#{{1,{depth}}} ", body)[0]


def test_user_guide_documents_help() -> None:
    """The user guide has a /help section, the three menu names and one section per domain."""
    text = _read("docs/USERGUIDE.md")
    assert "## /help" in text
    section = _section(text, "## /help")
    for word in ("`show`", "/aide", "/hilfe", "/suggest"):
        assert word in section, word
    for domain in DOMAINS:
        assert f"\n## {domain}\n" in text, domain


def test_user_guide_keeps_the_old_details() -> None:
    """The details the old helps carried are in the user guide."""
    text = _read("docs/USERGUIDE.md")
    for detail in ("DD/MM/YYYY", "5 minutes", "90", "user_dm", "role_dm", "Manage Messages", "Administrator", "F - "):
        assert detail in text, detail


def test_operator_guide_explains_the_server_language() -> None:
    """The operator guide explains the server language and the new sync line."""
    text = _read("docs/OPERATIONS.md")
    section = _section(text, "## Server language")
    for word in ("Community", "en-US", "src/fablabot/locales"):
        assert word in section, word
    assert text.index("## Commands channel") < text.index("## Server language")
    assert "Commands synced (9, global)." in _section(text, '## "This command is outdated"')


def test_readme_check_names_help() -> None:
    """The slash-menu section of the README names /help next to /suggest, in its paragraph and in step 4."""
    text = _read("README.md")
    section = _section(text, "### Staff commands in the slash menu")
    assert "`/suggest` and `/help` stay visible to everyone" in section
    assert "it sees `/suggest` and `/help` and no staff group" in section


def _audiences(text: str) -> dict[str, frozenset[str] | str]:
    """Return what each domain table says about who can run each command, keyed by ``/group name``.

    A cell naming roles becomes the set of role names (``Admin roles`` stands for the four admin roles, ``Same``
    for the row above); a cell naming a permission or a rule in words stays as written.
    """
    audiences: dict[str, frozenset[str] | str] = {}
    for domain in DOMAINS:
        section = _section(text, f"## {domain}")
        assert TABLE_HEADER in section, domain
        previous: frozenset[str] | str = ""
        for line in section.split(TABLE_HEADER, 1)[1].split("\n")[2:]:
            if not line.startswith("|"):
                break
            command, _summary, cell = (part.strip() for part in line.strip("|").split(" | "))
            name = " ".join(word for word in command.strip("`").split() if not word.startswith(("[", "<")))
            if cell == SAME_CELL:
                audiences[name] = previous
            elif cell in (EVERYONE_CELL, ROLE_SUMMARY_CELL, *PERMISSION_CELLS):
                audiences[name] = previous = cell
            else:
                roles: set[str] = set()
                for part in cell.split(", "):
                    roles |= ADMIN_ROLES if part == ADMIN_CELL else {part}
                audiences[name] = previous = frozenset(roles)
    return audiences


def test_user_guide_tables_have_the_header_of_every_domain() -> None:
    """Every domain section holds a ``Command | What it does | Who can run it`` table with at least one row."""
    text = _read("docs/USERGUIDE.md")
    for domain in DOMAINS:
        section = _section(text, f"## {domain}")
        assert f"{TABLE_HEADER}\n| --- | --- | --- |\n| `/" in section, domain


async def test_user_guide_says_who_can_run_each_command(started_bot: Any) -> None:
    """The audience in each table row is the set of roles the command's own rule holds, for every ruled command."""
    audiences = _audiences(_read("docs/USERGUIDE.md"))
    ruled = {f"/{c.qualified_name}": rule_of(c) for c in leaf_commands(started_bot.tree.walk_commands())}
    ruled = {name: rule for name, rule in ruled.items() if rule is not None}
    assert ruled
    mismatches: list[str] = []
    for name, rule in ruled.items():
        if name not in audiences:
            mismatches.append(f"{name}: missing from the user guide")
        elif rule is EVERYONE:
            if audiences[name] != EVERYONE_CELL:
                mismatches.append(f"{name}: should say {EVERYONE_CELL}, says {audiences[name]!r}")
        elif isinstance(rule, AnyRole) and audiences[name] != frozenset(rule.roles):
            mismatches.append(f"{name}: should name {sorted(rule.roles)}, names {audiences[name]!r}")
    assert not mismatches, "\n".join(mismatches)


def test_user_guide_says_who_can_run_the_member_commands() -> None:
    """The ``/user`` rows that differ from the admin set name the roles their checks name, and the others say so."""
    audiences = _audiences(_read("docs/USERGUIDE.md"))
    op_roles = frozenset(ADMIN_ROLES | {RoleNames.DIGITAL_MANAGER})
    assert audiences["/user op"] == op_roles
    assert audiences["/user deop"] == op_roles
    assert audiences["/user with_roles"] == frozenset(user_management._WITH_ROLES_ALLOWED)
    for name in ("add_role", "remove_role", "add_roles", "remove_roles"):
        assert audiences[f"/user {name}"] == ROLE_SUMMARY_CELL, name
    assert audiences["/msg clear"] == PERMISSION_CELLS[0]
    assert audiences["/msg purge"] == PERMISSION_CELLS[1]


def test_user_guide_says_suggest_and_purge_work_outside_the_commands_channel() -> None:
    """The commands the channel rule does not gate say so, and purge says where it aims by default."""
    text = _read("docs/USERGUIDE.md")
    assert "`/suggest` and a private `/help` work outside the commands channel." in _section(text, "## For everyone")
    bullet = re.search(r"^- `/msg purge`.*(?:\n  .*)*", text, re.MULTILINE)
    assert bullet
    purge = " ".join(bullet.group().split())
    assert "works outside the commands channel" in purge
    assert "with neither `channel` nor `all_channels` it targets the current channel" in purge


def test_operator_guide_describes_the_commands_channel_rule() -> None:
    """The commands channel section names the four commands that do not follow it."""
    section = _section(_read("docs/OPERATIONS.md"), "## Commands channel")
    for command in ("`/suggest`", "`/msg purge`", "`/msg clear`", "`/welcome approve`"):
        assert command in section


def test_user_guide_does_not_say_a_rule_can_be_roles_and_a_permission() -> None:
    """No command needs both a role and a permission, and the ``/user`` rule per role is named."""
    section = _section(_read("docs/USERGUIDE.md"), "## /help")
    assert "or both" not in section
    assert "a rule per role" in section


def test_operator_guide_says_members_do_not_need_the_commands_channel_for_help() -> None:
    """Only the public ``/help`` is held to the commands channel."""
    section = " ".join(_section(_read("docs/OPERATIONS.md"), "## Commands channel").split())
    assert "members do not need access to this one" in section
    assert "`/help show:True`" in section
