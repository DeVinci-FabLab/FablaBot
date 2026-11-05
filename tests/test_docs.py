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
from fablabot.helpers.constants import ADMIN_ROLES, APPEL_EDIT_WINDOW, APPEL_ROLES, RoleNames
from fablabot.helpers.formation.attendance import MAX_BUTTONS, MAX_MENUS, MENU_SIZE
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
    for detail in ("DD/MM/YYYY", "1 to 5 minutes", "user_dm", "role_dm", "Manage Messages", "Administrator", "F - "):
        assert detail in text, detail


def test_operator_guide_explains_the_server_language() -> None:
    """The operator guide explains the server language and the new sync line."""
    text = _read("docs/OPERATIONS.md")
    section = _section(text, "## Server language")
    for word in ("Community", "en-US", "src/fablabot/locales"):
        assert word in section, word
    assert text.index("## Commands channel") < text.index("## Server language")
    assert "Commands synced (9, global)." in _section(text, '## "This command is outdated"')


def test_operator_guide_slash_menu_names_help() -> None:
    """The slash-menu section of the operator guide names /help next to /suggest, in its paragraph and in step 4."""
    text = _read("docs/OPERATIONS.md")
    section = _section(text, "## Staff commands in the slash menu")
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
    rehearsal = section.split("**Rehearse on a copy.**", 1)[1].split("**Hold the update.**", 1)[0]
    # A retry runs the image the fix built: the tag is pulled before the helper runs, not taken from the local copy.
    assert rehearsal.index("fablabot:edge") < rehearsal.index('docker pull "$IMAGE"') < rehearsal.index("fablabot_state")
    hold = section.split("**Hold the update.**", 1)[1].split("**Merge the release pull request.**", 1)[0]
    assert "docker inspect fablabot --format '{{index .Config.Labels \"org.opencontainers.image.version\"}}'" in hold
    assert "automatic update" in hold.split("cannot be edited", 1)[1]
    # The data folder belongs to the image's user, root included, and each count may have grown since the rehearsal.
    assert "root included" in section
    assert "may have grown" in section.split("**Check.**", 1)[1].split("**Import.**", 1)[0]
    assert "if any are left" in section.split("**Read the start.**", 1)[1]


def test_operator_guide_says_how_a_failed_import_rolls_back() -> None:
    """A failed import ends on the held release, and a killed one is run again."""
    section = _section(_read("docs/OPERATIONS.md"), "### If a step fails")
    for detail in ("still holds the old release", "leaves no row", "Never delete the journal", "`key #3`", "counted from 0"):
        assert detail in section, detail
    rows = {line.split("|")[1].strip(): line for line in section.splitlines() if line.startswith("| ") and line[2].isdigit()}
    assert "8, 9 or 10" not in rows
    assert "leaves no row" in rows["8 or 9"]
    # Step 10 follows a committed import: the old release, if it ran, leaves the database behind the files.
    assert "committed" in rows["10"]
    assert "set `data/fablabot.db` aside" in rows["10"]
    assert "mv" in rows["10"]
    # Step 3 comes before the hold: a release cut now moves `:latest` and can deploy itself over unimported JSON.
    assert "Fix the cause in a release" not in rows["3"]
    assert "pull request on `main`" in rows["3"]
    assert "leave the release pull request open" in rows["3"]
    assert "`:edge`" in rows["3"]
    assert "from step 2" in rows["3"]
    # After a rollback by export the database holds the data: the next import fills it only if none ever committed.
    assert "`mv`" in rows["8 or 9"]


def test_operator_guide_warns_of_stale_json_and_exports_first() -> None:
    """A rollback past the database exports first, and the way forward sets the database aside."""
    section = _section(_read("docs/OPERATIONS.md"), "### Rolling back")
    for detail in ("reads the JSON files", "with no error", "fablabot.export_state", "changed since it was imported"):
        assert detail in section, detail
    # The export brings a fourth file, so the way forward first stops on the file the import never saw.
    assert "temp_admin_state.json not imported" in section
    assert "`Cannot export`" in section
    troubleshooting = _section(_read("docs/OPERATIONS.md"), "## Troubleshooting")
    row = next(line for line in troubleshooting.splitlines() if "not imported into" in line)
    assert "rollback by export" in row
    assert "fablabot.db" in row
    assert "OPERATIONS.md#back-to-a-release-before-the-database" in _read("docs/DEPLOYMENT.md")


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
    archive_count, disk_count, archived_exports, exports = done.stdout.split()
    assert archive_count == disk_count
    assert int(archive_count) > 0
    assert archived_exports == exports == str(len(EXPORTED))
    archive = next((host.home / "fablabot-archive").glob("*.tar.gz"))
    with tarfile.open(archive) as opened:
        assert f"data/exports/{STAMP}/guild_config.json" in opened.getnames()
    assert stat.S_IMODE(archive.stat().st_mode) == 0o600
    assert "above 0" in section
    assert "sudo" in section.split("4. ", 1)[1]


@needs_a_user
def test_the_rollback_copies_the_private_export_in_place_of_the_json_files(host: _Host) -> None:
    """The copy is read with the right to enter the export folder, and the old files are kept aside."""
    section = _section(_read("docs/OPERATIONS.md"), "#### Back to a release before the database")
    step = section.split("\n3. ", 1)[1].split("\n4. ", 1)[0]

    blocks = _blocks(step)
    # The first block only lists the export and moves nothing: its output is the start of the full run's.
    listing = host.run(blocks[:1], STAMP=STAMP)
    done = host.run(blocks, STAMP=STAMP)

    assert listing.returncode == 0, listing.stderr
    assert done.returncode == 0, done.stderr
    for name in EXPORTED:
        assert (host.data / name).read_text(encoding="utf-8") == f"new {name}"
    for name in EXPORTED[:3]:
        assert (host.data / f"json-before-{STAMP}" / name).read_text(encoding="utf-8") == f"old {name}"
    assert set(listing.stdout.split()) >= set(EXPORTED)
    # The check after the copy is what the second block prints: the four files, as the guide says it must.
    after_copy = done.stdout.removeprefix(listing.stdout)
    assert sorted(line.split()[-1] for line in after_copy.splitlines()) == sorted(EXPORTED)


@needs_a_user
def test_the_rollback_moves_nothing_when_the_export_is_not_there(host: _Host) -> None:
    """A wrong STAMP shows before the first file moves, so no older release starts on empty state."""
    section = _section(_read("docs/OPERATIONS.md"), "#### Back to a release before the database")
    step = section.split("\n3. ", 1)[1].split("\n4. ", 1)[0]

    first = host.run(_blocks(step)[:1], STAMP="20250101-000000")

    assert first.returncode != 0
    assert sorted(path.name for path in host.data.glob("*.json")) == sorted(EXPORTED[:3])


NON_ROOT = "## Moving a host to the non-root image"
IMAGE_PR = "feat(docker)!: run the bot as an unprivileged user in a hardened container"
MOVE_STEPS = (
    "**Archive.**",
    "**Hold the update.**",
    "**Merge the pull requests.**",
    "**Stop the stack.**",
    "**Give the folders to the bot's user.**",
    "**Give the token file to the bot's user.**",
    "**Add the hardening lines.**",
    "**Deploy.**",
    "**Read the start.**",
    "**Release the hold.**",
)


def _non_root_move() -> str:
    """Return the one list that moves a host to the non-root image, in the deployment guide."""
    return _section(_read("docs/DEPLOYMENT.md"), NON_ROOT)


def test_deployment_guide_moves_a_host_to_the_non_root_image_in_order() -> None:
    """The host on the database holds, merges, stops, gives the files away, hardens, deploys and only then follows `:latest`."""
    section = _non_root_move()
    places = [section.index(step) for step in MOVE_STEPS]
    assert places == sorted(places)
    hold = section.split("**Hold the update.**", 1)[1].split("**Merge the pull requests.**", 1)[0]
    assert "org.opencontainers.image.version" in hold
    assert "every 30 minutes" in hold
    assert "sudo chown -R 10001:10001" in section.split("**Give the folders", 1)[1].split("**Give the token", 1)[0]
    token = section.split("**Give the token file", 1)[1].split("**Add the hardening lines.**", 1)[0]
    assert "chown 10001:10001" in token
    assert "chmod 400" in token
    hardening = section.split("**Add the hardening lines.**", 1)[1].split("**Deploy.**", 1)[0]
    assert "new version" in hardening
    start = section.split("**Read the start.**", 1)[1].split("**Release the hold.**", 1)[0]
    assert "Server check" in start
    assert "State.Health.Status" in start
    assert ":latest" in section.split("**Release the hold.**", 1)[1].split("\n\n", 1)[0]


def test_deployment_guide_rolls_the_non_root_move_back_with_the_old_image_and_no_hardening() -> None:
    """The rollback needs both: the previous image, and the stack without the hardening lines."""
    rollback = _non_root_move().split("To roll back", 1)[1]
    assert "previous image" in rollback
    assert "without the hardening lines" in rollback or "the seven lines must go" in rollback


def test_the_non_root_move_uses_the_guides_archive_and_the_real_container() -> None:
    """The archive is `~/fablabot-archive`, never a folder inside the volumes, and the container is `fablabot`."""
    section = _non_root_move()
    assert "fablabot/archive" not in section
    assert "~/fablabot-archive" in section
    assert "password" in section
    assert "fablabot-prod" not in section
    assert "docker inspect --format '{{.State.Health.Status}}' fablabot" in section
    for guide in ("README.md", "docs/OPERATIONS.md", "docs/DEPLOYMENT.md"):
        assert "fablabot/archive" not in _read(guide)


def test_the_non_root_move_keeps_the_whole_folder_rule_and_the_other_owner() -> None:
    """The files belong to another user than 10001, and `data/` goes whole."""
    section = _non_root_move()
    assert "`data/` whole" in section
    assert "another user" in section
    assert "the stack without the hardening lines" in section


def test_moving_to_the_database_keeps_the_whole_folder_rule_for_the_later_move() -> None:
    """A move to 10001 after the database takes `data/` whole, the database and its backups with it."""
    section = _section(_read("docs/OPERATIONS.md"), "## Moving to the database")
    assert "`chown -R`" in section
    assert "`data/` whole" in section
    assert "Moving a host to the non-root image" in section


def test_the_hold_comes_before_the_pull_request_that_brings_the_image_then_the_release() -> None:
    """The image's own pull request cuts the release: it waits for the hold, and the release pull request follows it."""
    section = _non_root_move()
    merge = section.split("**Merge the pull requests.**", 1)[1].split("**Stop the stack.**", 1)[0]
    assert IMAGE_PR in merge
    assert "minor release" in merge
    assert merge.index(IMAGE_PR) < merge.index("chore(main): release X.Y.Z")
    assert "Build and Push Docker Image" in merge
    assert "pull request that brings the image" in merge
    assert merge.index("pull request that brings the image") < merge.index("release pull request")


def test_the_volumes_path_is_set_once_before_step_one() -> None:
    """Steps 1 and 4 use `$VOLUMES_PATH`: it is set before them, once, and not again at step 5."""
    section = _non_root_move()
    assert section.count("VOLUMES_PATH=/path/to/volumes") == 1
    assert section.index("VOLUMES_PATH=/path/to/volumes") < section.index("1. **Archive.**")
    assert "VOLUMES_PATH=" not in section.split("**Give the folders to the bot's user.**", 1)[1]


def test_archive_then_hold_are_steps_one_and_two() -> None:
    """The archive is step 1 and the hold step 2."""
    section = _non_root_move()
    assert re.search(r"^1\. \*\*Archive", section, re.MULTILINE)
    assert re.search(r"^2\. \*\*Hold the update", section, re.MULTILINE)
    assert not re.search(r"^0\. ", section, re.MULTILINE)


def test_the_hold_saves_the_server_check_lines_for_the_comparison() -> None:
    """The lines are saved while the old container runs, before the hold's deploy, and read back after the move."""
    section = _non_root_move()
    hold = section.split("**Hold the update.**", 1)[1].split("**Merge the pull requests.**", 1)[0]
    command = "docker logs fablabot 2>&1 | grep 'Server check:'"
    assert command in hold
    assert "~/fablabot-archive/" in hold.split(command, 1)[1].split("\n", 1)[0]
    assert hold.index(command) < hold.index("deploy")
    assert "saved in step 2" in section.split("**Read the start.**", 1)[1].split("**Release the hold.**", 1)[0]


def test_the_non_root_move_has_no_fallback_for_an_image_that_cannot_be_edited() -> None:
    """Step 7 edits `image:`, so the hold does not offer a way around editing it."""
    section = _non_root_move()
    assert "cannot be edited" not in section
    assert "automatic update off" not in section
    assert "turned it off" not in section


def test_releasing_the_hold_pulls_latest_and_reads_the_result() -> None:
    """The last step pulls `:latest` before the deploy, then reads the version label and the health."""
    section = _non_root_move()
    release = section.split("**Release the hold.**", 1)[1].split("To roll back", 1)[0]
    pull = "docker pull ghcr.io/devinci-fablab/fablabot:latest"
    label = "org.opencontainers.image.version"
    assert pull in release
    assert release.index(pull) < release.index("deploy") < release.index(label, release.index(pull))
    assert "State.Health.Status" in release.split(label, 1)[1]
    assert "version label" in release
    assert "health" in release


def test_the_rollback_stays_on_the_tag_and_the_guide_names_the_hardening_lines() -> None:
    """`:latest` brings the non-root image back, so a rollback stays on the previous tag."""
    rollback = _non_root_move().split("To roll back", 1)[1]
    assert "stays on the tag" in rollback
    assert "`:latest` brings the non-root image back" in rollback
    rolling = _section(_read("docs/OPERATIONS.md"), "### Rolling back").split("#### Back to a release", 1)[0]
    assert "hardening lines" in rolling
    assert "Moving a host to the non-root image" in rolling


def test_the_troubleshooting_names_the_real_symptoms_of_a_rollback_with_the_hardening_lines() -> None:
    """The older image as root without capabilities fails on the token and on the database, with these messages."""
    troubleshooting = _section(_read("docs/OPERATIONS.md"), "## Troubleshooting")
    row = next(line for line in troubleshooting.splitlines() if line.startswith("| After a rollback"))
    assert "registrations are lost" not in troubleshooting
    assert "PermissionError: ... '/run/secrets/discord_token'" in row
    assert "Cannot start: data/fablabot.db cannot be used: attempt to write a readonly database." in row
    assert "hardening lines" in row


def test_the_owner_of_a_folder_or_the_token_is_another_user_not_root() -> None:
    """The files may belong to any other user on the host: the guide does not say root."""
    guide = _read("docs/OPERATIONS.md")
    assert "belong to root" not in guide
    troubleshooting = _section(guide, "## Troubleshooting")
    for start in ("| Bot restarts in a loop with", "| `Cannot start: the token file"):
        row = next(line for line in troubleshooting.splitlines() if line.startswith(start))
        assert "another user" in row
        assert "root" not in row
    assert "belong to root" not in _read("docs/DEPLOYMENT.md")


def test_a_refused_start_may_create_a_missing_folder() -> None:
    """The check creates `data/` or `logs/` when absent: the deployment guide does not say it writes nothing."""
    text = _read("docs/DEPLOYMENT.md")
    assert "writes and deletes nothing meanwhile" not in text
    assert "creates a missing `data/` or `logs/` folder" in text


GUIDES = ("USERGUIDE.md", "OPERATIONS.md", "DEPLOYMENT.md")


def test_the_readme_is_a_landing_page_that_links_every_guide() -> None:
    """The README stays short and points at each guide; `docs/` lists them too."""
    readme = _read("README.md")
    assert len(readme.splitlines()) <= 80
    docs_index = _read("docs/README.md")
    for guide in GUIDES:
        assert f"(./docs/{guide})" in readme, guide
        assert f"({guide})" in docs_index, guide
    assert "(./CONTRIBUTING.md)" in readme
    assert "(../CONTRIBUTING.md)" in docs_index


def test_the_long_guides_open_with_a_table_of_contents() -> None:
    """The operator guide, the deployment guide and the contributor guide list their sections before the first one."""
    for name in ("docs/OPERATIONS.md", "docs/DEPLOYMENT.md", "CONTRIBUTING.md"):
        text = _read(name)
        first = text.index("\n## ")
        assert "\n- [" in text[:first], name


def _slug(heading: str) -> str:
    """Return the anchor GitHub gives a heading."""
    text = re.sub(r"[^\w\- ]", "", heading.strip().lower().replace("`", ""))
    return text.replace(" ", "-")


def _anchors(text: str) -> set[str]:
    """Return the anchors of the headings of a Markdown file, leaving out those in code blocks."""
    outside = re.sub(r"^ *```.*?^ *```", "", text, flags=re.MULTILINE | re.DOTALL)
    return {_slug(match["title"]) for match in re.finditer(r"^#{1,6} (?P<title>.+)$", outside, re.MULTILINE)}


def test_every_relative_link_and_anchor_in_the_docs_resolves() -> None:
    """A link in the README, CONTRIBUTING or a guide points at a file that exists and at a heading it holds."""
    files = [ROOT / "README.md", ROOT / "CONTRIBUTING.md", *sorted((ROOT / "docs").glob("*.md"))]
    broken: list[str] = []
    for source in files:
        text = re.sub(r"^ *```.*?^ *```", "", source.read_text(encoding="utf-8"), flags=re.MULTILINE | re.DOTALL)
        for target in re.findall(r"\]\(([^)\s]+)\)", text):
            if re.match(r"[a-z]+:", target):
                continue
            path, _, anchor = target.partition("#")
            found = (source.parent / path).resolve() if path else source
            has_anchors = found.exists() and found.suffix == ".md"
            if not found.exists() or (anchor and has_anchors and anchor not in _anchors(found.read_text(encoding="utf-8"))):
                broken.append(f"{source.name}: {target}")
    assert not broken, "\n".join(broken)


def test_operator_guide_says_a_rollback_past_the_attendance_schema_needs_the_v1_copy() -> None:
    """The guide names the attendance data, the two copies of a schema change, and the way back through the v1 copy."""
    stored = _section(_read("docs/OPERATIONS.md"), "## Stored data")
    for detail in ("attendance", "anonymous counts", "two copies", "`v1`"):
        assert detail in stored, detail
    rolling = _section(_read("docs/OPERATIONS.md"), "### Rolling back")
    for detail in ("newer than this release", "fablabot-v1-*.db", "data/fablabot.db", "stop", "archive"):
        assert detail in rolling, detail
    troubleshooting = _section(_read("docs/OPERATIONS.md"), "## Troubleshooting")
    row = next(line for line in troubleshooting.splitlines() if "first start of a release" in line)
    assert "Cannot start: data/fablabot.db cannot be used: <cause>." in row
    assert "previous image" in row


def test_operator_guide_goes_back_past_the_attendance_schema_without_losing_data() -> None:
    """The way back takes the schema down with a command and keeps the v1 copy for a damaged file."""
    rolling = _section(_read("docs/OPERATIONS.md"), "### Rolling back")
    schema = rolling.split("#### Back to a release before the database")[0]
    for detail in ("fablabot.downgrade_schema", "`Cannot go back`", "temporary admins", "in one transaction", "damaged"):
        assert detail in schema, detail
    assert schema.index("fablabot.downgrade_schema") < schema.index("fablabot-v1-*.db")
    assert "def main" in (ROOT / "src" / "fablabot" / "downgrade_schema.py").read_text(encoding="utf-8")


def test_operator_guide_says_the_export_leaves_the_attendance_data_in_the_database() -> None:
    """The export step and the way forward both name the attendance checks and counts the JSON files cannot hold."""
    rolling = _section(_read("docs/OPERATIONS.md"), "### Rolling back")
    export_step = rolling.split("2. Export,")[1].split("3. Look at the export")[0]
    assert "attendance checks and training counts" in export_step
    forward = rolling.split("To move forward again")[1]
    assert "attendance checks" in forward
    assert "training counts" in forward


def test_user_guide_says_who_may_take_the_attendance_check_and_for_how_long() -> None:
    """The trainings section names every role that may take a check, the two that may not, and the correction window."""
    section = _section(_read("docs/USERGUIDE.md"), "### Attendance check")
    for role in APPEL_ROLES:
        assert role in section, role
    assert f"`{RoleNames.ADMIN_TEMP}`" in section.split("Not ")[1]
    assert RoleNames.CODIR in section.split("Not ")[1]
    assert f"{APPEL_EDIT_WINDOW.days} days after the start" in section
    assert f"{MAX_BUTTONS} registered members" in section
    assert f"{MENU_SIZE * MAX_MENUS} members" in section


def test_user_guide_says_a_missed_start_still_sends_the_attendance_check() -> None:
    """The trainings section says a check missed at the start is sent at the next five-minute check, within the window."""
    section = _section(_read("docs/USERGUIDE.md"), "### Attendance check")
    assert "bot was off at the start" in section
    assert "next five-minute check" in section


def test_user_guide_says_a_training_published_twice_gets_one_attendance_check() -> None:
    """The trainings section says a training published again in a newer announcement gets one check, from the newer one."""
    section = _section(_read("docs/USERGUIDE.md"), "### Attendance check")
    assert "published again in a newer announcement gets one check, from the newer one" in section
