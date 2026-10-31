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
from discord import PermissionOverwrite, Permissions
from discord.ext import commands
import pytest
import simcord

from fablabot.cogs import guild_access
from fablabot.cogs.guild_access import GuildAccess
import fablabot.guild_config as guild_config  # noqa: PLR0402 (imported after the cogs: guild_config first is a circular import)
from fablabot.helpers.constants import ADMIN_ROLES, RoleNames
from fablabot.helpers.guild_access import GuildAccessPolicy
from fablabot.helpers.server_check import FIXES, INVITE_PERMISSIONS, Problem, check_servers
from fablabot.helpers.state_store import JsonStateStore
from fablabot.logging_handlers import GuildContextFilter
from fablabot.models.message import SUGGESTION_OPTIONS

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping

GUILD = int(LISTED_GUILD)
OTHER = int(OTHER_LISTED_GUILD)
UNLISTED = 333333333333333333
ALL_ROLES = tuple(value for name, value in vars(RoleNames).items() if name.isupper())
OPERATIONS = Path(__file__).resolve().parent.parent / "docs" / "OPERATIONS.md"
HIGH = 1000


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
            ``projet`` has no channel to fall back on), ``commands`` (the commands channel) and ``validation`` (the category).
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
    await env.settle()
    guild = cached(env, guild_id)
    guild.me.top_role.position = HIGH
    guild.me.top_role._permissions = INVITE_PERMISSIONS.value
    return guild


async def add_role(env: simcord.Env, name: str, *, above_bot: bool = False) -> Any:
    """Create a role on the first server and return the bot's cached copy of it."""
    created = env.guild.create_role(name)
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
    await check_servers([cached(env, guild_id) for guild_id in guild_ids or (GUILD,)])


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
    muted = {simcord_env.guild.default_role: PermissionOverwrite(send_messages=False)}
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
    await ready_server(simcord_env, locale="es-ES")
    await add_role(simcord_env, "Pôle Kevin", above_bot=True)
    simcord_env.guild.add_member(simcord_env.create_user("kevin_secret_name"))
    await run(simcord_env)
    lines = [r for r in caplog.records if r.getMessage().startswith("Server check")]
    assert len(lines) >= 3
    assert all(r.__dict__["guild_id"] == GUILD for r in lines)
    assert max(r.levelno for r in lines) < logging.ERROR
    assert "kevin" not in " ".join(r.getMessage() for r in lines).lower()


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


async def test_an_unavailable_server_is_reported_unread(caplog: pytest.LogCaptureFixture) -> None:
    """A server Discord has not delivered gets check_failed and no other check."""
    guild = make_guild(GUILD, [], [])
    guild.unavailable = True
    await check_servers([guild])
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
    check.assert_awaited_once_with([listed])


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
    check.assert_awaited_once_with([guild])


async def test_the_check_holds_up_no_other_ready_listener() -> None:
    """While the check runs, another listener of the same ready event still runs."""
    bot: Any = commands.Bot(command_prefix="!", intents=discord.Intents.none())  # Any: it stands in for Fablabot
    bot.guild_access = GuildAccessPolicy(frozenset({GUILD}))
    blocked, other_ran = asyncio.Event(), asyncio.Event()

    async def never_ends(_guilds: object) -> None:
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
