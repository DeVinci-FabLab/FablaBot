"""At start, the bot removes the commands an older release left on a server."""

from __future__ import annotations

import logging
import re
from typing import Any

from conftest import LISTED_GUILD, OTHER_LISTED_GUILD
import discord
from discord import AppCommandPermissionType, app_commands
import pytest
import simcord

from fablabot.helpers.command_sync import remove_leftover_commands

GUILD = int(LISTED_GUILD)
OTHER = int(OTHER_LISTED_GUILD)
LEFTOVERS = [{"name": "user", "description": "x"}, {"name": "bot", "description": "x"}]


@pytest.fixture(autouse=True)
def _info_level(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)


def commands_of(env: simcord.Env, guild_id: int) -> set[str]:
    """Return the names of the commands registered on a server alone."""
    return {name for (name, _kind) in env.backend.commands.get(guild_id, {})}


def writes(env: simcord.Env) -> list[str]:
    """Return the paths of the requests that changed the commands of a server."""
    return [r.path for r in env.http_requests if r.method == "PUT" and "/guilds/" in r.path and r.path.endswith("/commands")]


def global_writes(env: simcord.Env) -> list[str]:
    """Return the paths of the requests that changed the global commands."""
    return [r.path for r in env.http_requests if r.method == "PUT" and "/guilds/" not in r.path and r.path.endswith("/commands")]


def leftover_codes(caplog: pytest.LogCaptureFixture) -> list[str]:
    """Return the problem lines of the server check that report a leftover."""
    return [m for r in caplog.records if "code=leftover_commands" in (m := r.getMessage())]


async def start(bot: Any, *guild_ids: int, leftovers: dict[int, list[dict[str, Any]]] | None = None) -> simcord.Env:
    """Start the real bot on some servers, each holding its leftovers before the first ready."""
    env = simcord.Env(bot)
    for guild_id in guild_ids:
        env.create_guild(id=guild_id)
    for guild_id, payloads in (leftovers or {}).items():
        env.backend.register_commands(guild_id, payloads)
    await env.start()
    await env.settle()
    return env


async def test_a_leftover_goes_at_start_and_the_global_commands_stay(
    simcord_bot: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """Leftovers go at start, the global commands stay, and the server check sees no leftover."""
    env = await start(simcord_bot, GUILD, leftovers={GUILD: LEFTOVERS})
    try:
        bot: Any = env.bot
        remaining = commands_of(env, GUILD)
        globals_ = await bot.tree.fetch_commands()
    finally:
        await env.shutdown()
    assert remaining == set()
    assert globals_
    assert {command.name for command in globals_} == {command.name for command in bot.tree.get_commands()}
    assert leftover_codes(caplog) == []
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Removed leftover commands")]
    assert len(lines) == 1
    assert f"guild_id={GUILD}" in lines[0]
    assert re.search(r"/bot command_id=\d+, /user command_id=\d+", lines[0])


async def test_the_integrations_setting_of_a_global_command_survives(simcord_bot: Any) -> None:
    """A removal that runs after the setting exists leaves the global commands and the setting as they were."""
    env = await start(simcord_bot, GUILD)
    try:
        bot: Any = env.bot
        before = await bot.tree.fetch_commands()
        entries = [{"id": "1", "type": AppCommandPermissionType.role.value, "permission": True}]
        env.backend.set_command_permissions(GUILD, before[0].id, entries)
        env.backend.register_commands(GUILD, LEFTOVERS)
        writes_before, global_writes_before = len(writes(env)), len(global_writes(env))
        await remove_leftover_commands(bot.tree, [bot.get_guild(GUILD)], dev_guild_id=None)
        removal_writes = len(writes(env)) - writes_before
        removal_global_writes = len(global_writes(env)) - global_writes_before
        after = await bot.tree.fetch_commands()
        remaining = commands_of(env, GUILD)
        kept = env.backend.get_command_permissions(GUILD, before[0].id)
    finally:
        await env.shutdown()
    assert removal_writes == 1
    assert removal_global_writes == 0
    assert remaining == set()
    assert before
    assert [(c.id, c.name) for c in after] == [(c.id, c.name) for c in before]
    assert kept == entries


async def test_a_server_command_held_by_the_local_tree_is_not_sent_back(simcord_bot: Any) -> None:
    """The removal empties the server, even when the local tree holds a command scoped to it."""
    env = await start(simcord_bot, GUILD)
    try:
        bot: Any = env.bot

        async def held(interaction: discord.Interaction) -> None:
            """Do nothing."""

        bot.tree.add_command(app_commands.Command(name="held", description="x", callback=held), guild=discord.Object(id=GUILD))
        env.backend.register_commands(GUILD, LEFTOVERS)
        await remove_leftover_commands(bot.tree, [bot.get_guild(GUILD)], dev_guild_id=None)
        remaining = commands_of(env, GUILD)
    finally:
        await env.shutdown()
    assert remaining == set()


async def test_a_server_present_but_not_served_keeps_its_commands(simcord_bot: Any) -> None:
    """With an allow-list matching none of the servers, the bot stays and neither reads nor writes their commands."""
    stranger = 333333333333333333
    env = await start(simcord_bot, stranger, leftovers={stranger: LEFTOVERS})
    try:
        bot: Any = env.bot
        present = bot.get_guild(stranger) is not None
        remaining = commands_of(env, stranger)
        touching = [r for r in env.http_requests if f"/guilds/{stranger}/commands" in r.path]
    finally:
        await env.shutdown()
    assert present
    assert remaining == {"user", "bot"}
    assert touching == []


async def test_nothing_is_touched_with_a_development_server(simcord_bot: Any) -> None:
    """With DEV_GUILD_ID set, no server-scoped command is removed."""
    simcord_bot.dev_guild_id = GUILD
    env = await start(simcord_bot, GUILD, OTHER, leftovers={OTHER: LEFTOVERS})
    try:
        remaining = commands_of(env, OTHER)
        removing = [r for r in env.http_requests if OTHER_LISTED_GUILD in r.path and r.method != "GET"]
    finally:
        await env.shutdown()
    assert remaining == {"user", "bot"}
    assert removing == []


async def test_a_clean_start_makes_no_server_write(simcord_bot: Any) -> None:
    """With no leftover, the removal only reads."""
    env = await start(simcord_bot, GUILD, OTHER)
    try:
        assert writes(env) == []
    finally:
        await env.shutdown()


async def test_no_server_served_means_no_server_read(simcord_bot: Any) -> None:
    """With no server served, the removal makes no server-scoped call."""
    env = await start(simcord_bot)
    try:
        assert not [r for r in env.http_requests if "/guilds/" in r.path]
    finally:
        await env.shutdown()


async def test_a_failure_on_one_server_does_not_stop_the_next(simcord_bot: Any, caplog: pytest.LogCaptureFixture) -> None:
    """One server failing is a warning with the error type, and the next server is cleaned."""
    env = await start(simcord_bot, GUILD, OTHER)
    try:
        bot: Any = env.bot
        env.backend.register_commands(GUILD, LEFTOVERS)
        env.backend.register_commands(OTHER, LEFTOVERS)
        real = bot.tree.fetch_commands

        async def fetch(*, guild: Any = None) -> Any:
            if guild is not None and guild.id == GUILD:
                msg = "secret text"
                raise RuntimeError(msg)
            return await real(guild=guild)

        bot.tree.fetch_commands = fetch
        caplog.clear()
        await remove_leftover_commands(bot.tree, [bot.get_guild(GUILD), bot.get_guild(OTHER)], dev_guild_id=None)  # pyright: ignore[reportArgumentType] -- both are cached
        kept, cleaned = commands_of(env, GUILD), commands_of(env, OTHER)
    finally:
        await env.shutdown()
    assert kept == {"user", "bot"}
    assert cleaned == set()
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert f"guild_id={GUILD}" in warnings[0].getMessage()
    assert "RuntimeError" in warnings[0].getMessage()
    assert "secret text" not in caplog.text
