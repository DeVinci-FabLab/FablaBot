"""Staff command groups stay out of members' slash menu, and every command works in servers only."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from fablabot.helpers.command_sync import COMMAND_SYNC_STATE_FILE, command_fingerprint
from fablabot.helpers.i18n import catalog

STAFF_GROUPS = ("text", "vocal", "fm", "log", "msg", "user", "welcome")
MEMBER_COMMANDS = ("suggest", "help")
GUILD_CONTEXT = 0
"""Discord's interaction context type for a server channel."""


@pytest.fixture
async def payloads(started_bot: Any) -> dict[str, dict[str, Any]]:
    """Build the payload of each top-level command from the real bot and its real cogs."""
    return {command.name: command.to_dict(started_bot.tree) for command in started_bot.tree.get_commands()}


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


async def _translated(bot: Any, name: str) -> dict[str, Any]:
    """Return the payload of a top-level command with the catalogue's translations applied."""
    command = bot.tree.get_command(name)
    assert command is not None
    return await command.get_translated_payload(bot.tree, bot.tree.translator)


async def test_help_payload_is_localized(started_bot: Any) -> None:
    """The help command is named and described in French and German, and its option is described too."""
    payload = await _translated(started_bot, "help")
    texts = {code: catalog().texts(code) for code in ("fr", "de")}
    assert payload["name"] == "help"
    assert payload["name_localizations"] == {"fr": "aide", "de": "hilfe"}
    assert payload["description_localizations"] == {code: texts[code].get("help.slash.description") for code in ("fr", "de")}
    (option,) = payload["options"]
    assert option["name"] == "show"
    assert not option.get("name_localizations")
    assert option["description_localizations"] == {code: texts[code].get("help.slash.show") for code in ("fr", "de")}


async def test_other_commands_have_no_translation(started_bot: Any) -> None:
    """Only the help command is translated: every other top-level command keeps a single name."""
    for name in (*STAFF_GROUPS, "suggest"):
        payload = await _translated(started_bot, name)
        assert not payload.get("name_localizations"), name


async def test_commands_sync_with_their_translations(started_bot: Any) -> None:
    """The translator is in place when the commands are synced, so the localized names reach Discord."""
    started_bot.tree.sync.assert_awaited_once()
    recorded = json.loads(Path(COMMAND_SYNC_STATE_FILE).read_text(encoding="utf-8"))["fingerprint"]
    translated = await command_fingerprint(started_bot.tree, guild=None, application_id=started_bot.application_id)
    assert recorded == translated
