"""At start, each served server gets a report of what it lacks, read from what the bot already knows."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
import re
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

from conftest import LISTED_GUILD, OTHER_LISTED_GUILD, make_guild
import discord
from discord import AppCommandPermissionType, PermissionOverwrite, Permissions
from discord.ext import commands
import pytest
import simcord
from simcord.backend.ops.commands import COMMAND_FIELDS
from simcord.http import RequestContext, route

from fablabot.cogs import guild_access
from fablabot.cogs.guild_access import GuildAccess
import fablabot.guild_config as guild_config  # noqa: PLR0402 (imported after the cogs: guild_config first is a circular import)
from fablabot.helpers import server_check
from fablabot.helpers.access import Caller, rule_of
from fablabot.helpers.constants import ADMIN_ROLES, RoleNames
from fablabot.helpers.guild_access import GuildAccessPolicy
from fablabot.helpers.help_page import leaf_commands
from fablabot.helpers.server_check import FIXES, INVITE_PERMISSIONS, Problem, check_servers
from fablabot.helpers.state_store import JsonStateStore
from fablabot.logging_handlers import GuildContextFilter
from fablabot.models.message import SUGGESTION_OPTIONS

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Iterable, Mapping

GUILD = int(LISTED_GUILD)
OTHER = int(OTHER_LISTED_GUILD)
UNLISTED = 333333333333333333
ALL_ROLES = tuple(value for name, value in vars(RoleNames).items() if name.isupper())
OPERATIONS = Path(__file__).resolve().parent.parent / "docs" / "OPERATIONS.md"
HIGH = 1000


# SimCord has no route yet for the bulk read of a server's Integrations settings, so this one serves what it stores.
@route("GET", "/applications/{application_id}/guilds/{guild_id}/commands/permissions")
def _bulk_command_permissions(ctx: RequestContext) -> list[dict[str, Any]]:
    guild_id = ctx.int_arg("guild_id")
    return [
        {"id": str(command_id), "application_id": ctx.args["application_id"], "guild_id": str(guild_id), "permissions": entries}
        for (stored_guild, command_id), entries in ctx.backend.command_permissions.items()
        if stored_guild == guild_id
    ]


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start from an empty config store: the store is a module global and would leak between tests."""
    monkeypatch.setattr(guild_config, "_config_store", JsonStateStore(guild_config.logger, Path("data/guild_config.json")))


@pytest.fixture(autouse=True)
def _info_level(caplog: pytest.LogCaptureFixture) -> None:
    """Record the INFO lines of the report."""
    caplog.set_level(logging.INFO)


def cached(env: simcord.Env, guild_id: int = GUILD) -> Any:
    """Return the bot's cached copy of a guild, the object the check reads."""
    bot: Any = env.bot  # SimCord types ``env.bot`` as a plain client
    return bot.get_guild(guild_id)


def role_entries(role_ids: Iterable[int], *, allowed: bool = True) -> list[dict[str, Any]]:
    """Write the raw Integrations entries that allow (or deny) some roles."""
    return [{"id": str(role_id), "type": AppCommandPermissionType.role.value, "permission": allowed} for role_id in role_ids]


async def global_command(env: simcord.Env, name: str) -> Any:
    """Return a command as Discord holds it."""
    bot: Any = env.bot
    guild = discord.Object(id=bot.dev_guild_id) if bot.dev_guild_id is not None else None
    return next(command for command in await bot.tree.fetch_commands(guild=guild) if command.name == name)


async def show_all(env: simcord.Env, handle: Any) -> None:
    """Allow on each staff group the roles that can use it, as an administrator does in Integrations."""
    bot: Any = env.bot
    names = tuple(name for name in handle.roles if name != "@everyone")
    for group in bot.tree.get_commands():
        if group.default_permissions is None:
            continue
        rules = [rule for leaf in leaf_commands([group]) if (rule := rule_of(leaf)) is not None]
        opening = [
            handle.roles[name].id
            for name in names
            if any(rule.admits(Caller((name,), Permissions.none(), names)) for rule in rules)
        ]
        command = await global_command(env, group.name)
        env.backend.set_command_permissions(handle.id, command.id, role_entries(opening))


async def ready_server(
    env: simcord.Env,
    guild_id: int = GUILD,
    *,
    locale: str = "fr",
    without: Collection[str] = (),
    deny: Mapping[str, str] | None = None,
) -> Any:
    """Build a server with everything the check looks for, and return the bot's cached guild.

    Args:
        env (simcord.Env): The running environment.
        guild_id (int): The ID of the new server.
        locale (str): The language the server declares.
        without (Collection[str]): What to leave out: role names, ``holder`` (nobody holds ``Respo Projet``, and
            ``projet`` has no channel to fall back on), ``commands`` (the commands channel), ``validation`` (the category)
            and ``integrations`` (the settings that show each staff group).
        deny (Mapping[str, str] | None): A permission the bot lacks in the ``commands`` or the ``log`` channel.

    Returns:
        Any: The cached ``discord.Guild``.
    """
    server = env.create_guild(id=guild_id, preferred_locale=locale)
    denied = deny or {}

    def overwrites(channel: str) -> dict[Any, PermissionOverwrite] | None:
        return {server.default_role: PermissionOverwrite(**{denied[channel]: False})} if channel in denied else None

    if "commands" not in without:
        server.create_text_channel("commandes_bot", overwrites=overwrites("commands"))
    log = server.create_text_channel("logs", overwrites=overwrites("log"))
    if "validation" not in without:
        server.create_category("Validation")
    for option in SUGGESTION_OPTIONS.values():
        if option.channel_name:
            server.create_text_channel(option.channel_name)
    created = {name: server.create_role(name) for name in ALL_ROLES if name not in without}
    if "holder" not in without and RoleNames.PROJECTS_MANAGER in created:
        server.add_member(env.create_user(f"holder_{guild_id}"), roles=[created[RoleNames.PROJECTS_MANAGER]])
    guild_config.set_log_channel_id(guild_id, log.id)
    if "integrations" not in without:
        await show_all(env, server)
    await env.settle()
    guild = cached(env, guild_id)
    guild.me.top_role.position = HIGH
    guild.me.top_role._permissions = INVITE_PERMISSIONS.value
    return guild


async def add_role(env: simcord.Env, name: str, *, above_bot: bool = False) -> Any:
    """Create a role on the first server, show the groups it opens, and return the bot's cached copy of it."""
    created = env.guild.create_role(name)
    await show_all(env, env.guild)
    await env.settle()
    role = cached(env).get_role(created.id)
    role.position = HIGH + 1 if above_bot else 1
    return role


def messages(caplog: pytest.LogCaptureFixture, guild_id: int = GUILD) -> list[str]:
    """Return the report lines of a server."""
    return [
        r.getMessage()
        for r in caplog.records
        if r.getMessage().startswith("Server check") and f"guild_id={guild_id}" in r.getMessage()
    ]


def report(caplog: pytest.LogCaptureFixture, guild_id: int = GUILD) -> str:
    """Return the summary line of a server."""
    summaries = [m for m in messages(caplog, guild_id) if m.startswith("Server check: guild_id=")]
    assert len(summaries) == 1
    return summaries[0]


def problems(caplog: pytest.LogCaptureFixture, guild_id: int = GUILD) -> list[str]:
    """Return the problem lines of a server."""
    return [m for m in messages(caplog, guild_id) if m.startswith("Server check problem:")]


def codes(caplog: pytest.LogCaptureFixture, guild_id: int = GUILD) -> list[str]:
    """Return the problem codes of a server, in the order written."""
    return [match.group(1) for line in problems(caplog, guild_id) if (match := re.search(r"code=(\w+)", line))]


async def run(env: simcord.Env, *guild_ids: int) -> None:
    """Run the check on cached servers."""
    bot: Any = env.bot  # SimCord types ``env.bot`` as a plain client
    await check_servers(bot, [cached(env, guild_id) for guild_id in guild_ids or (GUILD,)])


def failing_check(message: str = "") -> Callable[[discord.Guild], list[Problem]]:
    """Build a check that raises, as one does when a Discord read fails."""

    def check(_guild: discord.Guild) -> list[Problem]:
        raise RuntimeError(message)

    return check


async def test_a_ready_server_has_no_problem(simcord_env: simcord.Env, caplog: pytest.LogCaptureFixture) -> None:
    """A server with everything in place gets a clean summary and no problem line."""
    await ready_server(simcord_env)
    await run(simcord_env)
    assert "blocking=0 warnings=0 locale=fr texts=fr" in report(caplog)
    assert problems(caplog) == []


async def test_a_bare_server_reports_each_missing_part(simcord_env: simcord.Env, caplog: pytest.LogCaptureFixture) -> None:
    """A server with nothing set up names each missing part, in the order of the checks."""
    simcord_env.create_guild(id=GUILD)
    await simcord_env.settle()
    await run(simcord_env)
    assert codes(caplog) == [
        "commands_channel_missing",
        "log_channel_missing",
        "roles_missing",
        "suggest_recipient_unreachable",
    ]
    assert "blocking=3 warnings=1" in report(caplog)


async def test_a_channel_found_by_name_is_not_stored(simcord_env: simcord.Env) -> None:
    """The commands channel found by its name is not written to the stored settings."""
    await ready_server(simcord_env)
    stored = Path("data/guild_config.json").read_bytes()
    await run(simcord_env)
    assert Path("data/guild_config.json").read_bytes() == stored
    assert guild_config.get_commands_channel_id(GUILD) is None


async def test_a_stale_stored_channel_stays_stored(simcord_env: simcord.Env, caplog: pytest.LogCaptureFixture) -> None:
    """A stored commands channel that is gone is not erased; the channel named so is used meanwhile."""
    await ready_server(simcord_env)
    guild_config.set_commands_channel_id(GUILD, 999)
    await run(simcord_env)
    assert guild_config.get_commands_channel_id(GUILD) == 999
    assert "commands_channel_missing" not in codes(caplog)


async def test_a_stored_commands_channel_with_another_name_is_the_commands_channel(
    simcord_env: simcord.Env, caplog: pytest.LogCaptureFixture
) -> None:
    """A stored text channel counts under any name, and a post problem there names its ID."""
    await ready_server(simcord_env, without=["commands"])
    muted: dict[Any, PermissionOverwrite] = {simcord_env.guild.default_role: PermissionOverwrite(send_messages=False)}
    created = simcord_env.guild.create_text_channel("salle-des-commandes", overwrites=muted)
    await simcord_env.settle()
    stored = cached(simcord_env).get_channel(created.id)
    guild_config.set_commands_channel_id(GUILD, stored.id)
    await run(simcord_env)
    assert codes(caplog) == ["commands_channel_no_post"]
    assert f"detail=[channel_id={stored.id}]" in problems(caplog)[0]


async def test_a_voice_channel_named_like_the_commands_channel_counts_as_missing(
    simcord_env: simcord.Env, caplog: pytest.LogCaptureFixture
) -> None:
    """A channel named commandes_bot that is not a text channel is no commands channel."""
    simcord_env.create_guild(id=GUILD).create_voice_channel("commandes_bot")
    await simcord_env.settle()
    await run(simcord_env)
    assert "commands_channel_missing" in codes(caplog)


async def test_a_commands_channel_of_another_server_counts_as_missing(
    simcord_env: simcord.Env, caplog: pytest.LogCaptureFixture
) -> None:
    """A commands channel stored for a server but belonging to another one is no commands channel here."""
    server = await ready_server(simcord_env, without=["commands"])
    other = await ready_server(simcord_env, OTHER)
    foreign = discord.utils.get(other.text_channels, name="commandes_bot")
    assert foreign is not None
    assert server.get_channel(foreign.id) is None
    guild_config.set_commands_channel_id(GUILD, foreign.id)
    await run(simcord_env)
    assert codes(caplog) == ["commands_channel_missing"]


async def test_a_log_channel_of_another_server_counts_as_missing(
    simcord_env: simcord.Env, caplog: pytest.LogCaptureFixture
) -> None:
    """A log channel stored for a server but belonging to another one is no log channel here."""
    server = await ready_server(simcord_env)
    other = await ready_server(simcord_env, OTHER)
    foreign = discord.utils.get(other.text_channels, name="logs")
    assert foreign is not None
    assert server.get_channel(foreign.id) is None
    guild_config.set_log_channel_id(GUILD, foreign.id)
    await run(simcord_env)
    assert codes(caplog) == ["log_channel_missing"]
    assert f"detail=[stored_id={foreign.id}]" in problems(caplog)[0]


@pytest.mark.parametrize(
    ("commands_deny", "log_deny", "code"),
    [
        ("view_channel", None, "commands_channel_no_post"),
        ("send_messages", None, "commands_channel_no_post"),
        (None, "view_channel", "log_channel_no_post"),
        (None, "send_messages", "log_channel_no_post"),
        (None, "attach_files", "log_channel_no_post"),
    ],
)
async def test_a_channel_the_bot_cannot_post_in_is_reported(
    simcord_env: simcord.Env, caplog: pytest.LogCaptureFixture, commands_deny: str | None, log_deny: str | None, code: str
) -> None:
    """A channel where the bot cannot view, post or attach a file is reported with its ID."""
    denied = {channel: permission for channel, permission in (("commands", commands_deny), ("log", log_deny)) if permission}
    await ready_server(simcord_env, deny=denied)
    await run(simcord_env)
    assert codes(caplog) == [code]
    assert "detail=[channel_id=" in problems(caplog)[0]


async def test_a_log_channel_of_another_kind_counts_as_missing(
    simcord_env: simcord.Env, caplog: pytest.LogCaptureFixture
) -> None:
    """A stored log channel that is not a text channel counts as no log channel."""
    server = await ready_server(simcord_env)
    voice = simcord_env.guild.create_voice_channel("vocal")
    await simcord_env.settle()
    guild_config.set_log_channel_id(GUILD, voice.id)
    await run(simcord_env)
    assert server.get_channel(voice.id) is not None
    assert codes(caplog) == ["log_channel_missing"]
    assert f"detail=[stored_id={voice.id}]" in problems(caplog)[0]


async def test_a_missing_permission_is_named(simcord_env: simcord.Env, caplog: pytest.LogCaptureFixture) -> None:
    """A permission of the invitation that the bot lost is named by its flag."""
    server = await ready_server(simcord_env)
    server.me.top_role._permissions = INVITE_PERMISSIONS.value & ~Permissions(manage_roles=True).value
    await run(simcord_env)
    assert codes(caplog) == ["permissions_missing"]
    assert "detail=[manage_roles]" in problems(caplog)[0]


def test_the_invite_integer_matches_the_guide() -> None:
    """The permissions the check expects are the ones the operator guide has the invite ask for."""
    guide = OPERATIONS.read_text(encoding="utf-8")
    listed = {
        "manage_roles",
        "manage_channels",
        "view_audit_log",
        "view_channel",
        "send_messages",
        "manage_messages",
        "embed_links",
        "attach_files",
        "read_message_history",
        "add_reactions",
    }
    assert Permissions(**dict.fromkeys(listed, True)) == INVITE_PERMISSIONS
    assert f"permissions={INVITE_PERMISSIONS.value}" in guide


@pytest.mark.parametrize(
    ("name", "code"),
    [
        (RoleNames.ADMIN_TEMP, "role_order_temp"),
        (RoleNames.CODIR, "role_order"),
        ("F - Laser", "role_order"),
        ("Pôle Com", "role_order"),
        (RoleNames.ADMIN, None),
        ("Owner", None),
    ],
)
async def test_roles_above_the_bot(
    simcord_env: simcord.Env, caplog: pytest.LogCaptureFixture, name: str, code: str | None
) -> None:
    """Only the roles the bot hands out count when they sit above its own role."""
    server = await ready_server(simcord_env)
    role = discord.utils.get(server.roles, name=name) or await add_role(simcord_env, name)
    role.position = HIGH + 1
    await run(simcord_env)
    assert codes(caplog) == ([code] if code else [])


@pytest.mark.parametrize(
    ("name", "code"),
    [(RoleNames.ADMIN_TEMP, "role_order_temp"), (RoleNames.CODIR, "role_order"), (RoleNames.ADMIN, None)],
)
async def test_a_role_the_bot_holds_as_its_top_role_is_not_below_it(
    simcord_env: simcord.Env, caplog: pytest.LogCaptureFixture, name: str, code: str | None
) -> None:
    """The bot cannot hand out a role equal to its own highest one, so that role counts as not below the bot's."""
    server = await ready_server(simcord_env)
    role = discord.utils.get(server.roles, name=name)
    role.position = HIGH + 1
    server.me._roles.add(role.id)
    assert server.me.top_role == role
    await run(simcord_env)
    assert codes(caplog) == ([code] if code else [])


async def test_a_server_typed_role_name_stays_out_of_the_log(
    simcord_env: simcord.Env, caplog: pytest.LogCaptureFixture
) -> None:
    """A role the code does not name is given by its ID, never by the name the staff typed."""
    await ready_server(simcord_env)
    role = await add_role(simcord_env, "F - Kevin Dupont", above_bot=True)
    await run(simcord_env)
    assert f"detail=[role_id={role.id}]" in problems(caplog)[0]
    assert "Kevin" not in caplog.text


@pytest.mark.parametrize("missing", [RoleNames.CODIR, RoleNames.ADMIN_TEMP])
async def test_a_required_role_missing_blocks(simcord_env: simcord.Env, caplog: pytest.LogCaptureFixture, missing: str) -> None:
    """A server without CoDir, or without Admin -temp-, is blocked and the role is named."""
    await ready_server(simcord_env, without={missing})
    await run(simcord_env)
    assert codes(caplog) == ["roles_missing"]
    assert f"detail=[{missing}]" in problems(caplog)[0]
    assert "blocking=1" in report(caplog)


async def test_a_server_needs_one_admin_role(simcord_env: simcord.Env, caplog: pytest.LogCaptureFixture) -> None:
    """One of the four admin roles is enough; none of them blocks."""
    await ready_server(simcord_env, without=ADMIN_ROLES)
    await ready_server(simcord_env, OTHER, without=ADMIN_ROLES - {RoleNames.PRESIDENT})
    await run(simcord_env, GUILD, OTHER)
    assert codes(caplog, GUILD) == ["roles_missing"]
    assert "detail=[one of Administrateur | Président | Secrétaire Général | Vice-Président]" in problems(caplog, GUILD)[0]
    assert codes(caplog, OTHER) == []


async def test_optional_roles_are_a_note_not_a_problem(simcord_env: simcord.Env, caplog: pytest.LogCaptureFixture) -> None:
    """A missing optional role is one information line and counts for nothing."""
    kept = {RoleNames.CODIR, RoleNames.ADMIN_TEMP, RoleNames.ADMIN, RoleNames.PROJECTS_MANAGER, RoleNames.BUREAU}
    await ready_server(simcord_env, without=set(ALL_ROLES) - kept)
    await run(simcord_env)
    assert codes(caplog) == []
    assert "blocking=0 warnings=0" in report(caplog)
    notes = [m for m in messages(caplog) if m.startswith("Server check: optional roles absent")]
    assert len(notes) == 1
    absent = notes[0].split("): ", 1)[1].split(", ")
    assert RoleNames.EVENTS_MANAGER in absent
    assert RoleNames.BUREAU not in absent
    assert not ADMIN_ROLES & set(absent)


@pytest.mark.parametrize(
    ("locale", "texts", "warned"),
    [("fr", "fr", False), ("de", "de", False), ("en-GB", "en", False), ("es-ES", "en", True)],
)
async def test_the_language_shows_in_the_summary(
    simcord_env: simcord.Env, caplog: pytest.LogCaptureFixture, locale: str, texts: str, warned: bool
) -> None:
    """The summary gives both languages; only a language with no shipped file warns."""
    await ready_server(simcord_env, locale=locale)
    await run(simcord_env)
    assert f"locale={locale} texts={texts}" in report(caplog)
    assert codes(caplog) == (["language_not_shipped"] if warned else [])


async def test_the_welcome_flow_is_checked_only_when_on(simcord_env: simcord.Env, caplog: pytest.LogCaptureFixture) -> None:
    """A server without the Validation category and Membre ✓ is blocked only when the welcome flow is on."""
    await ready_server(simcord_env, without={RoleNames.MEMBER_VERIFIED, "validation"})
    await run(simcord_env)
    assert codes(caplog) == []
    guild_config.set_welcome_verify_enabled(GUILD, enabled=True)
    caplog.clear()
    await run(simcord_env)
    assert codes(caplog) == ["welcome_setup_missing"]
    assert "detail=[category Validation, role Membre ✓]" in problems(caplog)[0]


async def test_a_role_without_holder_and_channel_is_unreachable(
    simcord_env: simcord.Env, caplog: pytest.LogCaptureFixture
) -> None:
    """A recipient with neither a member holding its role nor its channel is reported by its key."""
    await ready_server(simcord_env, without={"holder"})
    await run(simcord_env)
    assert codes(caplog) == ["suggest_recipient_unreachable"]
    assert "detail=[projet]" in problems(caplog)[0]


async def test_lines_name_nobody_and_never_reach_a_channel(simcord_env: simcord.Env, caplog: pytest.LogCaptureFixture) -> None:
    """Every line is tied to its server, below ERROR (so never posted), and names no member or typed role."""
    caplog.handler.addFilter(GuildContextFilter())
    await ready_server(simcord_env, locale="es-ES", without={RoleNames.EVENTS_MANAGER})
    await add_role(simcord_env, "Pôle Kevin", above_bot=True)
    simcord_env.guild.add_member(simcord_env.create_user("kevin_secret_name"))
    await run(simcord_env)
    lines = [r for r in caplog.records if r.getMessage().startswith("Server check")]
    assert len(lines) >= 3
    assert any(r.getMessage().startswith("Server check: optional roles absent") for r in lines)
    assert all(r.__dict__["guild_id"] == GUILD for r in lines)
    assert max(r.levelno for r in lines) < logging.ERROR
    assert "kevin" not in " ".join(r.getMessage() for r in lines).lower()


async def test_the_report_lines_keep_their_level_and_text(simcord_env: simcord.Env, caplog: pytest.LogCaptureFixture) -> None:
    """The summary and the note are INFO, each problem is a WARNING, and the text is the one the operator guide shows."""
    await ready_server(simcord_env, locale="es-ES", without={"commands", RoleNames.EVENTS_MANAGER})
    await run(simcord_env)
    lines = [(r.levelname, r.getMessage()) for r in caplog.records if r.name == "fablabot.helpers.server_check"]
    assert lines == [
        ("INFO", f"Server check: guild_id={GUILD} blocking=1 warnings=1 locale=es-ES texts=en"),
        ("INFO", f"Server check: optional roles absent (guild_id={GUILD}): {RoleNames.EVENTS_MANAGER}"),
        (
            "WARNING",
            f"Server check problem: guild_id={GUILD} severity=blocking code=commands_channel_missing detail=[] "
            "fix: create a text channel named commandes_bot",
        ),
        (
            "WARNING",
            f"Server check problem: guild_id={GUILD} severity=warning code=language_not_shipped detail=[locale=es-ES] "
            "fix: pick a shipped language in Server Settings, Community, Overview",
        ),
    ]


async def test_a_failure_on_one_server_does_not_stop_the_next(
    simcord_env: simcord.Env, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A check that raises is reported as check_failed and the next server is still checked."""
    await ready_server(simcord_env)
    await ready_server(simcord_env, OTHER)

    def flaky(guild: discord.Guild) -> list[Problem]:
        if guild.id == GUILD:
            raise RuntimeError
        return []

    monkeypatch.setattr("fablabot.helpers.server_check._LOCAL_CHECKS", (flaky,))
    await run(simcord_env, GUILD, OTHER)
    assert codes(caplog, GUILD) == ["check_failed"]
    assert "detail=[step=local status=RuntimeError]" in problems(caplog, GUILD)[0]
    assert "blocking=0 warnings=0" in report(caplog, OTHER)


async def test_a_failed_check_does_not_hide_the_others(
    simcord_env: simcord.Env, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A check that raises is reported where it stands, and the checks before and after it still report."""
    simcord_env.create_guild(id=GUILD)
    await simcord_env.settle()
    first, *rest = server_check._LOCAL_CHECKS
    monkeypatch.setattr(server_check, "_LOCAL_CHECKS", (first, failing_check(), *rest))
    await run(simcord_env)
    assert codes(caplog) == [
        "commands_channel_missing",
        "check_failed",
        "log_channel_missing",
        "roles_missing",
        "suggest_recipient_unreachable",
    ]
    assert "blocking=3 warnings=2" in report(caplog)


async def test_the_text_of_a_failure_stays_out_of_the_lines(
    simcord_env: simcord.Env, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A check that raises with a message is reported by its error type only: the message may hold a name or an ID."""
    simcord_env.create_guild(id=GUILD)
    await simcord_env.settle()
    monkeypatch.setattr(server_check, "_LOCAL_CHECKS", (failing_check("kevin_secret_name 418003123456789012"),))
    await run(simcord_env)
    assert problems(caplog) == [
        f"Server check problem: guild_id={GUILD} severity=warning code=check_failed "
        f"detail=[step=local status=RuntimeError] fix: {FIXES['check_failed'][1]}"
    ]
    assert "kevin" not in caplog.text
    assert "418003123456789012" not in caplog.text


async def test_a_failing_report_does_not_stop_the_next_server(
    simcord_env: simcord.Env, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A server whose report cannot be written gets check_failed, tied to it, and the next server is still reported."""
    caplog.handler.addFilter(GuildContextFilter())
    await ready_server(simcord_env)
    await ready_server(simcord_env, OTHER)
    read = server_check._languages

    def languages(guild: discord.Guild) -> tuple[str, str]:
        if guild.id == GUILD:
            raise RuntimeError("kevin_secret_name")
        return read(guild)

    monkeypatch.setattr(server_check, "_languages", languages)
    await run(simcord_env, GUILD, OTHER)
    assert problems(caplog, GUILD) == [
        f"Server check problem: guild_id={GUILD} severity=warning code=check_failed "
        f"detail=[step=report status=RuntimeError] fix: {FIXES['check_failed'][1]}"
    ]
    failures = [r for r in caplog.records if "step=report" in r.getMessage()]
    assert [(r.levelno, r.__dict__["guild_id"]) for r in failures] == [(logging.WARNING, GUILD)]
    assert "kevin" not in caplog.text
    assert "blocking=0 warnings=0" in report(caplog, OTHER)


async def test_an_unavailable_server_is_reported_unread(caplog: pytest.LogCaptureFixture) -> None:
    """A server Discord has not delivered gets check_failed and no other check."""
    guild = make_guild(GUILD, [], [])
    guild.unavailable = True
    await check_servers(MagicMock(dev_guild_id=GUILD), [guild])
    assert codes(caplog) == ["check_failed"]
    assert "detail=[step=unavailable]" in problems(caplog)[0]
    assert "warnings=1" in report(caplog)
    assert len(messages(caplog)) == 2


def stub_cog(guilds: list[MagicMock]) -> GuildAccess:
    """Build the real cog on a stub bot listing ``GUILD`` only."""
    bot = MagicMock()
    bot.guild_access = GuildAccessPolicy(frozenset({GUILD}))
    bot.guilds = guilds
    return GuildAccess(bot)


def stub_guild(guild_id: int) -> MagicMock:
    """Build a stub guild that can be left."""
    guild = MagicMock(spec=discord.Guild)
    guild.id = guild_id
    guild.leave = AsyncMock()
    return guild


async def test_ready_twice_checks_once() -> None:
    """A reconnection fires ready again and does not run the check again."""
    cog = stub_cog([stub_guild(GUILD)])
    with patch.object(guild_access, "check_servers", AsyncMock()) as check:
        await cog.on_ready()
        await cog.on_ready()
    check.assert_awaited_once()


async def test_a_server_being_left_is_not_checked() -> None:
    """The check gets the served servers only, after the allow-list has been applied."""
    listed, unlisted = stub_guild(GUILD), stub_guild(UNLISTED)
    cog = stub_cog([listed, unlisted])
    with patch.object(guild_access, "check_servers", AsyncMock()) as check:
        await cog.on_ready()
    unlisted.leave.assert_awaited_once()
    check.assert_awaited_once_with(cog.bot, [listed])


async def test_a_tolerated_server_is_checked_when_the_allow_list_is_empty() -> None:
    """With no allow-list, the servers the bot stays in are served and reach the check."""
    guild = stub_guild(UNLISTED)
    cog = stub_cog([guild])
    cog.bot.guild_access = GuildAccessPolicy(frozenset())
    with (
        patch.object(cog, "_owner_owns", AsyncMock(return_value=False)),
        patch.object(guild_access, "check_servers", AsyncMock()) as check,
    ):
        await cog.on_ready()
    guild.leave.assert_not_awaited()
    check.assert_awaited_once_with(cog.bot, [guild])


async def test_the_check_holds_up_no_other_ready_listener() -> None:
    """While the check runs, another listener of the same ready event still runs."""
    bot: Any = commands.Bot(command_prefix="!", intents=discord.Intents.none())  # Any: it stands in for Fablabot
    bot.guild_access = GuildAccessPolicy(frozenset({GUILD}))
    blocked, other_ran = asyncio.Event(), asyncio.Event()

    async def never_ends(_bot: object, _guilds: object) -> None:
        await blocked.wait()

    async def other() -> None:
        other_ran.set()

    bot.add_listener(other, "on_ready")
    async with bot:
        await bot.add_cog(GuildAccess(bot))
        with patch.object(guild_access, "check_servers", never_ends):
            bot.dispatch("ready")
            await asyncio.wait_for(other_ran.wait(), 1)
        blocked.set()
        await asyncio.sleep(0)


async def test_the_real_ready_runs_the_check_on_served_guilds_only(simcord_bot: Any, caplog: pytest.LogCaptureFixture) -> None:
    """At start, the real bot reports on the listed server and leaves, without a line, the one outside the list."""
    env = simcord.Env(simcord_bot)
    env.create_guild(id=GUILD)
    env.create_guild(id=UNLISTED)
    await env.start()
    try:
        await env.settle()
    finally:
        await env.shutdown()
    assert report(caplog, GUILD)
    assert messages(caplog, UNLISTED) == []
    assert cached(env, UNLISTED) is None


async def test_the_real_ready_reports_a_tolerated_server_when_the_allow_list_is_empty(
    simcord_bot: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """At start with no allow-list, the real bot reports on the server it stays in."""
    simcord_bot.guild_access = GuildAccessPolicy(frozenset())
    env = simcord.Env(simcord_bot)
    env.create_guild(id=GUILD)
    await env.start()
    try:
        await env.settle()
    finally:
        await env.shutdown()
    assert report(caplog, GUILD)


def test_every_problem_has_its_fix_in_the_operator_guide() -> None:
    """Each code has a row in the guide with the severity and the fix the log line gives."""
    guide = OPERATIONS.read_text(encoding="utf-8")
    section = guide.split("## Server check at start", 1)[1].split("\n## ", 1)[0]
    rows = {
        cells[0].strip("`"): cells
        for line in section.splitlines()
        if line.startswith("| `") and (cells := [cell.strip() for cell in line.strip("|").split("|")])
    }
    for code, (severity, fix) in FIXES.items():
        assert code in rows, code
        assert rows[code][1] == severity, code
        assert rows[code][3] == fix, code


def commands_of(env: simcord.Env, guild_id: int = GUILD) -> dict[str, Any]:
    """Return the commands registered on a server, by name."""
    return {name: command for (name, _kind), command in env.backend.commands.get(guild_id, {}).items()}


def lines_about(caplog: pytest.LogCaptureFixture, code: str, guild_id: int = GUILD) -> list[str]:
    """Return the problem lines of a code."""
    return [line for line in problems(caplog, guild_id) if f"code={code} " in line]


async def test_reads_are_gets_one_global_and_two_per_server(simcord_env: simcord.Env) -> None:
    """The check reads the global commands once, then the commands and the Integrations settings of each server."""
    await ready_server(simcord_env)
    await ready_server(simcord_env, OTHER)
    before = len(simcord_env.http_requests)
    await run(simcord_env, GUILD, OTHER)
    reads = simcord_env.http_requests[before:]
    assert [read.method for read in reads] == ["GET"] * 5
    assert sum(read.path.endswith("/commands/permissions") for read in reads) == 2


async def test_every_server_scoped_command_is_a_leftover(simcord_env: simcord.Env, caplog: pytest.LogCaptureFixture) -> None:
    """A command registered on the server is reported once, by name and ID, and is left registered."""
    await ready_server(simcord_env)
    simcord_env.backend.register_commands(GUILD, [{"name": "user", "description": "x"}, {"name": "bot", "description": "x"}])
    registered = commands_of(simcord_env)
    await run(simcord_env)
    assert codes(caplog) == ["leftover_commands"]
    expected = ", ".join(f"/{name} command_id={registered[name]['id']}" for name in ("bot", "user"))
    assert f"detail=[{expected}]" in problems(caplog)[0]
    assert set(commands_of(simcord_env)) == {"user", "bot"}


async def test_a_group_shown_to_nobody_blocks(simcord_env: simcord.Env, caplog: pytest.LogCaptureFixture) -> None:
    """A staff group with no role allowed in Integrations blocks, and the line names the group."""
    await ready_server(simcord_env, without={"integrations"})
    await run(simcord_env)
    assert any(
        line.startswith("Server check problem") and "severity=blocking" in line
        for line in lines_about(caplog, "integrations_no_role")
    )
    assert "detail=[/fm]" in " ".join(lines_about(caplog, "integrations_no_role"))
    assert "integrations_role_missing" not in codes(caplog)


async def test_a_group_missing_some_roles_warns(simcord_env: simcord.Env, caplog: pytest.LogCaptureFixture) -> None:
    """A group shown to some of its roles warns, naming the roles still missing."""
    await ready_server(simcord_env, without={"integrations"})
    handle = simcord_env.guild
    command = await global_command(simcord_env, "fm")
    simcord_env.backend.set_command_permissions(GUILD, command.id, role_entries([handle.roles[RoleNames.ADMIN].id]))
    await run(simcord_env)
    [line] = [line for line in lines_about(caplog, "integrations_role_missing") if "detail=[/fm: " in line]
    assert "severity=warning" in line
    assert RoleNames.CODIR in line
    assert RoleNames.ADMIN not in line


async def test_a_role_with_administrator_needs_no_setting(simcord_env: simcord.Env, caplog: pytest.LogCaptureFixture) -> None:
    """A role that holds the Administrator permission sees every command, so no setting is expected for it."""
    server = await ready_server(simcord_env, without={"integrations"})
    for role in server.roles:
        if not role.is_default():
            role._permissions = Permissions(administrator=True).value
    await run(simcord_env)
    assert not [code for code in codes(caplog) if code.startswith("integrations_")]


async def test_a_respo_counts_only_where_its_pole_exists(simcord_env: simcord.Env, caplog: pytest.LogCaptureFixture) -> None:
    """A manager can give the role of its own pole only, so it is expected on /user once that pole exists."""
    await ready_server(simcord_env, without={"integrations"})
    handle = simcord_env.guild
    command = await global_command(simcord_env, "user")
    others = [role.id for name, role in handle.roles.items() if name not in {"@everyone", RoleNames.COMMUNICATION_MANAGER}]
    simcord_env.backend.set_command_permissions(GUILD, command.id, role_entries(others))
    await run(simcord_env)
    assert not [line for line in lines_about(caplog, "integrations_role_missing") if "detail=[/user" in line]
    handle.create_role("Pôle Communication")
    await simcord_env.settle()
    caplog.clear()
    await run(simcord_env)
    [line] = [line for line in lines_about(caplog, "integrations_role_missing") if "detail=[/user: " in line]
    assert RoleNames.COMMUNICATION_MANAGER in line


async def test_everyone_allowed_on_a_group_warns(simcord_env: simcord.Env, caplog: pytest.LogCaptureFixture) -> None:
    """A staff group allowed to @everyone in Integrations warns, since the whole server then sees it."""
    await ready_server(simcord_env)
    handle = simcord_env.guild
    command = await global_command(simcord_env, "fm")
    ids = [role.id for name, role in handle.roles.items() if name != "@everyone"]
    simcord_env.backend.set_command_permissions(GUILD, command.id, role_entries([GUILD, *ids]))
    await run(simcord_env)
    assert codes(caplog) == ["integrations_everyone_allowed"]
    assert "severity=warning" in problems(caplog)[0]
    assert "detail=[/fm]" in problems(caplog)[0]


async def test_a_bot_wide_allow_does_not_show_a_group(simcord_env: simcord.Env, caplog: pytest.LogCaptureFixture) -> None:
    """Only the setting on the group itself counts: roles allowed for the whole bot leave the group shown to nobody."""
    await ready_server(simcord_env, without={"integrations"})
    bot: Any = simcord_env.bot
    ids = [role.id for name, role in simcord_env.guild.roles.items() if name != "@everyone"]
    simcord_env.backend.set_command_permissions(GUILD, bot.application_id, role_entries(ids))
    await run(simcord_env)
    assert "detail=[/fm]" in " ".join(lines_about(caplog, "integrations_no_role"))


@pytest.mark.parametrize("on_application", [False, True])
@pytest.mark.parametrize(("kind", "label"), [(1, "@everyone"), (3, "All Channels")])
async def test_a_deny_on_member_commands_is_reported(
    simcord_env: simcord.Env, caplog: pytest.LogCaptureFixture, *, on_application: bool, kind: int, label: str
) -> None:
    """A deny for @everyone or All Channels, on the command or on the whole bot, blocks a command for every member."""
    await ready_server(simcord_env)
    bot: Any = simcord_env.bot
    command = await global_command(simcord_env, "suggest")
    entry = {"id": str(GUILD if kind == 1 else GUILD - 1), "type": kind, "permission": False}
    simcord_env.backend.set_command_permissions(GUILD, bot.application_id if on_application else command.id, [entry])
    await run(simcord_env)
    assert f"severity=blocking code=member_command_denied detail=[/suggest denied for {label}]" in " ".join(problems(caplog))
    assert set(codes(caplog)) == {"member_command_denied"}


async def test_a_command_setting_wins_over_the_bot_wide_one(simcord_env: simcord.Env, caplog: pytest.LogCaptureFixture) -> None:
    """An allow on the command stands even when the whole bot is denied to @everyone."""
    await ready_server(simcord_env)
    bot: Any = simcord_env.bot
    command = await global_command(simcord_env, "suggest")
    simcord_env.backend.set_command_permissions(GUILD, bot.application_id, role_entries([GUILD], allowed=False))
    await run(simcord_env)
    assert "/suggest denied for @everyone" in " ".join(lines_about(caplog, "member_command_denied"))
    simcord_env.backend.set_command_permissions(GUILD, command.id, role_entries([GUILD]))
    caplog.clear()
    await run(simcord_env)
    assert "/suggest" not in " ".join(lines_about(caplog, "member_command_denied"))


async def test_a_refused_read_is_reported_and_the_next_server_goes_on(
    simcord_env: simcord.Env, caplog: pytest.LogCaptureFixture
) -> None:
    """A server whose commands Discord refuses to give gets check_failed with the HTTP status, and the next server is read."""
    await ready_server(simcord_env)
    await ready_server(simcord_env, OTHER)
    simcord_env.inject_error("GET", f"/applications/*/guilds/{GUILD}/commands", status=403, times=None)
    await run(simcord_env, GUILD, OTHER)
    assert codes(caplog, GUILD) == ["check_failed"]
    assert "detail=[step=server commands status=HTTP 403]" in problems(caplog, GUILD)[0]
    assert "blocking=0 warnings=0" in report(caplog, OTHER)


async def test_a_refused_bulk_read_keeps_the_leftovers(simcord_env: simcord.Env, caplog: pytest.LogCaptureFixture) -> None:
    """When the Integrations read is refused, the leftovers already found are still reported."""
    await ready_server(simcord_env)
    simcord_env.backend.register_commands(GUILD, [{"name": "user", "description": "x"}])
    simcord_env.inject_error("GET", f"/applications/*/guilds/{GUILD}/commands/permissions", status=403, times=None)
    await run(simcord_env)
    assert codes(caplog) == ["leftover_commands", "check_failed"]
    assert "detail=[step=integrations status=HTTP 403]" in problems(caplog)[1]


async def test_a_failed_global_read_reports_each_server(
    simcord_env: simcord.Env, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A global read that fails with a plain error is reported on every server, which still gets its other checks."""
    await ready_server(simcord_env)
    await ready_server(simcord_env, OTHER)
    bot: Any = simcord_env.bot
    read = bot.tree.fetch_commands

    async def fetch(*, guild: Any = None) -> Any:
        if guild is None:
            raise TimeoutError
        return await read(guild=guild)

    monkeypatch.setattr(bot.tree, "fetch_commands", fetch)
    await run(simcord_env, GUILD, OTHER)
    for guild_id in (GUILD, OTHER):
        assert codes(caplog, guild_id) == ["check_failed"]
        assert "detail=[step=global commands status=TimeoutError]" in problems(caplog, guild_id)[0]


async def test_dev_mode_reads_only_the_dev_server(simcord_bot: Any, caplog: pytest.LogCaptureFixture) -> None:
    """With a development server, only that server is read for commands: no global read, no read of the other server."""
    simcord_bot.dev_guild_id = GUILD
    env = simcord.Env(simcord_bot)
    env.create_guild(id=GUILD)
    env.create_guild(id=OTHER)
    await env.start()
    try:
        await env.settle()
        caplog.clear()
        before = len(env.http_requests)
        await run(env, GUILD, OTHER)
        reads = env.http_requests[before:]
    finally:
        await env.shutdown()
    assert [read.path for read in reads if read.method == "GET"] == [
        f"/applications/{simcord_bot.application_id}/guilds/{GUILD}/commands",
        f"/applications/{simcord_bot.application_id}/guilds/{GUILD}/commands/permissions",
    ]
    assert not [code for code in codes(caplog, GUILD) if code == "leftover_commands"]
    assert not [code for code in codes(caplog, OTHER) if code in {"leftover_commands", "check_failed"}]


async def test_dev_mode_still_reports_a_stray_command(simcord_bot: Any, caplog: pytest.LogCaptureFixture) -> None:
    """On the development server, a command outside the bot's own list is still a leftover."""
    simcord_bot.dev_guild_id = GUILD
    env = simcord.Env(simcord_bot)
    env.create_guild(id=GUILD)
    await env.start()
    try:
        await env.settle()
        kept = [{key: command[key] for key in COMMAND_FIELDS if key in command} for command in commands_of(env).values()]
        env.backend.register_commands(GUILD, [*kept, {"name": "stray", "description": "x"}])
        stray = commands_of(env)["stray"]
        caplog.clear()
        await run(env, GUILD)
    finally:
        await env.shutdown()
    assert "leftover_commands" in codes(caplog)
    assert f"detail=[/stray command_id={stray['id']}]" in " ".join(problems(caplog))
