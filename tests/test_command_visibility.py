"""Staff command groups stay out of members' slash menu, and every command works in servers only."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest

from fablabot.helpers.guild_access import ALLOWED_GUILD_IDS_ENV

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

STAFF_GROUPS = ("text", "vocal", "fm", "log", "msg", "user", "welcome")
MEMBER_COMMANDS = ("suggest",)
GUILD_CONTEXT = 0
"""Discord's interaction context type for a server channel."""
LISTED_GUILD = "111111111111111111"


@pytest.fixture
async def payloads(tmp_path, monkeypatch) -> AsyncIterator[dict[str, dict[str, Any]]]:
    """Build the payload of each top-level command from the real bot and its real cogs."""
    token = tmp_path / "token"
    token.write_text("fake-token")
    monkeypatch.setenv("DISCORD_TOKEN_FILE", str(token))
    monkeypatch.setenv(ALLOWED_GUILD_IDS_ENV, LISTED_GUILD)
    import fablabot.main

    main = importlib.reload(fablabot.main)
    monkeypatch.setattr(main, "configure_logging", lambda _bot: (None, None))
    bot = main.Fablabot()
    bot.tree.sync = AsyncMock(return_value=[])
    # Entering the client sets up its loop state without logging in, so the cogs' tasks wait instead of failing.
    async with bot:
        await bot.setup_hook()
        yield {command.name: command.to_dict(bot.tree) for command in bot.tree.get_commands()}
        for name in list(bot.cogs):
            await bot.remove_cog(name)


def test_every_top_level_command_is_classified(payloads: dict[str, dict[str, Any]]) -> None:
    """Each top-level command is either a staff group or a member command."""
    assert sorted(payloads) == sorted(STAFF_GROUPS + MEMBER_COMMANDS)


@pytest.mark.parametrize("name", STAFF_GROUPS)
def test_staff_group_is_hidden_from_members(payloads: dict[str, dict[str, Any]], name: str) -> None:
    """A staff group asks for an empty default permission set, which hides it from members."""
    assert payloads[name]["default_member_permissions"] == 0


@pytest.mark.parametrize("name", MEMBER_COMMANDS)
def test_member_command_is_visible_to_everyone(payloads: dict[str, dict[str, Any]], name: str) -> None:
    """A member command sets no default permission, so everyone sees it."""
    assert payloads[name]["default_member_permissions"] is None


@pytest.mark.parametrize("name", STAFF_GROUPS + MEMBER_COMMANDS)
def test_command_is_usable_in_servers_only(payloads: dict[str, dict[str, Any]], name: str) -> None:
    """A command is offered in server channels only, never in direct messages."""
    assert payloads[name]["contexts"] == [GUILD_CONTEXT]
