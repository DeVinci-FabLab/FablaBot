"""Pin what the user guide, the operator guide and the README say about /help."""

from __future__ import annotations

import os
from pathlib import Path
import re
import stat
import subprocess
import tarfile
import textwrap
from typing import Any

import pytest

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


def test_user_guide_names_the_commands_outside_the_channel_rule() -> None:
    """The commands the channel rule does not gate say so, and purge says where it aims by default."""
    text = _read("docs/USERGUIDE.md")
    assert "`/suggest` and a private `/help` work outside the commands channel." in _section(text, "## For everyone")
    assert "commands channel only" in " ".join(_section(text, "## /help").split())
    bullet = re.search(r"^- `/msg purge`.*(?:\n  .*)*", text, re.MULTILINE)
    assert bullet
    purge = " ".join(bullet.group().split())
    assert "works outside the commands channel" in purge
    assert "with neither `channel` nor `all_channels` it targets the current channel" in purge


def test_operator_guide_describes_the_commands_channel_rule() -> None:
    """The commands channel section names the commands that do not follow it."""
    section = _section(_read("docs/OPERATIONS.md"), "## Commands channel")
    for command in ("`/suggest`", "`/help`", "`/msg purge`", "`/msg clear`", "`/welcome approve`"):
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
    assert "`/help show:True`, which posts the list for everyone, stays in this channel" in section


def test_operator_guide_gives_a_member_without_the_channel_a_way_out() -> None:
    """The troubleshooting rows name the private ``/help`` and the way out of its public refusal."""
    section = _read("docs/OPERATIONS.md")
    assert "A member who cannot open it uses `/suggest` or a private `/help`" in section
    assert "Run `/help` without `show`" in section


def test_operator_guide_moves_to_the_database_in_order() -> None:
    """The steps come in the only safe order, and the guide says when not to run them."""
    section = _section(_read("docs/OPERATIONS.md"), "## Moving to the database")
    steps = (
        "**Tell each server's admins",
        "**Archive,**",
        "**Rehearse on a copy.**",
        "**Hold the update.**",
        "**Merge the release pull request.**",
        "**Stop the stack.**",
        "**Check.**",
        "**Import.**",
        "**Check again,**",
        "**Start.**",
        "**Read the start.**",
        "**Release the hold.**",
    )
    places = [section.index(step) for step in steps]
    assert places == sorted(places)
    for detail in ("Admin -temp-", "24 hours", "never counts", "about 5 minutes", "--network none", "Server check:"):
        assert detail in section, detail


def test_operator_guide_says_how_a_failed_import_rolls_back() -> None:
    """A failed import ends on the held release, and a killed one is run again."""
    section = _section(_read("docs/OPERATIONS.md"), "### If a step fails")
    for detail in ("still holds the old release", "leaves no row", "Never delete the journal"):
        assert detail in section, detail


def test_operator_guide_warns_of_stale_json_and_exports_first() -> None:
    """A rollback past the database exports first, and the way forward sets the database aside."""
    section = _section(_read("docs/OPERATIONS.md"), "### Rolling back")
    for detail in ("reads the JSON files", "with no error", "fablabot.export_state", "changed since it was imported"):
        assert detail in section, detail
    assert "docs/OPERATIONS.md#back-to-a-release-before-the-database" in _read("README.md")


STAMP = "20261102-120000"
EXPORTED = ("guild_config.json", "formations_state.json", "messages_state.json", "temp_admin_state.json")


def _blocks(text: str) -> list[str]:
    """Return the bash blocks of a piece of the guide, in order, as the operator pastes them."""
    found = re.findall(r"^ *```bash\n(.*?)^ *```", text, re.MULTILINE | re.DOTALL)
    return [textwrap.dedent(block) for block in found]


class _Host:
    """The operator's host: the volumes laid out as the guide expects, a `sudo` that can enter the private export folder."""

    def __init__(self, root: Path) -> None:
        self.home = root / "home"
        self.volumes = root / "volumes"
        self.data = self.volumes / "fablabot" / "data"
        self.export = self.data / "exports" / STAMP
        self.sudo = root / "bin" / "sudo"
        for folder in (
            self.home,
            self.data,
            self.volumes / "fablabot" / "logs" / "guilds" / "1",
            self.export,
            self.sudo.parent,
        ):
            folder.mkdir(parents=True)
        (self.volumes / "fablabot" / "logs" / "guilds" / "1" / "day.log").write_text("log", encoding="utf-8")
        for name in EXPORTED[:3]:
            (self.data / name).write_text(f"old {name}", encoding="utf-8")
        (self.data / "fablabot.db").write_text("database", encoding="utf-8")
        for name in EXPORTED:
            (self.export / name).write_text(f"new {name}", encoding="utf-8")
        # The export belongs to the image's user and is mode 0700: the operator's own shell cannot enter it. This
        # `sudo` opens it for the command it runs and shuts it after, so a glob the operator's shell expands finds nothing.
        self.sudo.write_text(
            f'#!/bin/sh\nchmod 700 "{self.export}"\n"$@"\nstatus=$?\nchmod 000 "{self.export}"\nexit $status\n'
        )
        self.sudo.chmod(0o755)
        self.export.chmod(0)

    def run(self, blocks: list[str], **env: str) -> subprocess.CompletedProcess[str]:
        """Run blocks one after the other in one shell, as the operator pastes them, stopping at the first failure."""
        variables = {
            **os.environ,
            "HOME": str(self.home),
            "VOLUMES_PATH": str(self.volumes),
            "PATH": f"{self.sudo.parent}{os.pathsep}{os.environ['PATH']}",
            **env,
        }
        return subprocess.run(
            ["bash", "-e", "-c", "\n".join(blocks)], cwd=self.home, env=variables, capture_output=True, text=True, check=False
        )


@pytest.fixture
def host(tmp_path: Path) -> Any:
    """A host with a private export folder, opened again after the test so the folder can be removed."""
    layout = _Host(tmp_path)
    yield layout
    layout.export.chmod(stat.S_IRWXU)


needs_a_user = pytest.mark.skipif(os.geteuid() == 0, reason="root enters any folder, so there is no private folder to test")


@needs_a_user
def test_the_archive_steps_take_the_private_export_folder(host: _Host) -> None:
    """Once an export exists, the archive holds it and the count check counts it."""
    section = _section(_read("docs/OPERATIONS.md"), "### Before every redeploy")

    done = host.run(_blocks(section))

    assert done.returncode == 0, done.stderr
    archive_count, disk_count = done.stdout.split()
    assert archive_count == disk_count
    archive = next((host.home / "fablabot-archive").glob("*.tar.gz"))
    with tarfile.open(archive) as opened:
        assert f"data/exports/{STAMP}/guild_config.json" in opened.getnames()
    assert stat.S_IMODE(archive.stat().st_mode) == 0o600


@needs_a_user
def test_the_rollback_copies_the_private_export_in_place_of_the_json_files(host: _Host) -> None:
    """The copy is read with the right to enter the export folder, and the old files are kept aside."""
    section = _section(_read("docs/OPERATIONS.md"), "#### Back to a release before the database")
    step = section.split("\n3. ", 1)[1].split("\n4. ", 1)[0]

    done = host.run(_blocks(step), STAMP=STAMP)

    assert done.returncode == 0, done.stderr
    for name in EXPORTED:
        assert (host.data / name).read_text(encoding="utf-8") == f"new {name}"
    for name in EXPORTED[:3]:
        assert (host.data / f"json-before-{STAMP}" / name).read_text(encoding="utf-8") == f"old {name}"
    assert set(done.stdout.split()) >= set(EXPORTED)


@needs_a_user
def test_the_rollback_moves_nothing_when_the_export_is_not_there(host: _Host) -> None:
    """A wrong STAMP shows before the first file moves, so no older release starts on empty state."""
    section = _section(_read("docs/OPERATIONS.md"), "#### Back to a release before the database")
    step = section.split("\n3. ", 1)[1].split("\n4. ", 1)[0]

    first = host.run(_blocks(step)[:1], STAMP="20250101-000000")

    assert first.returncode != 0
    assert sorted(path.name for path in host.data.glob("*.json")) == sorted(EXPORTED[:3])
