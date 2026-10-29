"""Every log record carries the server whose event is being handled."""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import Any
from unittest.mock import MagicMock

import discord
import pytest

import fablabot.helpers  # noqa: F401  (loads the helpers first: the handlers package imports them back)
from fablabot.logging_handlers.guild_context import (
    GuildContextFilter,
    SingleLineFormatter,
    current_guild_id,
    guild_log_context,
    install_gateway_guild_context,
    payload_guild_id,
)

GUILD = 111111111111111111


def _record(name: str = "fablabot.probe", msg: str = "hello", exc_info: Any = None) -> logging.LogRecord:
    """Build a log record for the given logger name."""
    return logging.LogRecord(name, logging.INFO, __file__, 1, msg, None, exc_info)


def test_filter_tags_record_with_current_guild() -> None:
    """A record built inside a guild context gets that guild's ID."""
    record = _record()
    with guild_log_context(GUILD):
        assert GuildContextFilter().filter(record) is True
    assert record.guild_id == GUILD  # type: ignore[attr-defined]


def test_filter_tags_none_outside_any_guild() -> None:
    """A record built outside any guild context is tagged None."""
    record = _record()
    assert GuildContextFilter().filter(record) is True
    assert record.guild_id is None  # type: ignore[attr-defined]


def test_context_restores_previous_guild_on_exit() -> None:
    """Nested contexts restore the outer guild, also after an exception."""
    with guild_log_context(GUILD):
        with guild_log_context(222222222222222222):
            assert current_guild_id.get() == 222222222222222222
        assert current_guild_id.get() == GUILD
        with pytest.raises(RuntimeError), guild_log_context(None):
            raise RuntimeError("boom")
        assert current_guild_id.get() == GUILD
    assert current_guild_id.get() is None


@pytest.mark.parametrize("name", ["asyncio", "asyncio.events"])
def test_filter_never_tags_asyncio_records(name: str) -> None:
    """The event loop's own records stay untied to any guild."""
    record = _record(name)
    with guild_log_context(GUILD):
        GuildContextFilter().filter(record)
    assert record.guild_id is None  # type: ignore[attr-defined]


def test_task_started_in_guild_context_keeps_it() -> None:
    """A task created inside a guild context still sees it after the context ends."""
    seen: list[int | None] = []

    async def scenario() -> None:
        """Start a task in the context and let it run after the context exits."""

        async def work() -> None:
            """Record the guild seen by the task."""
            seen.append(current_guild_id.get())

        with guild_log_context(GUILD):
            task = asyncio.create_task(work())
        await task

    asyncio.run(scenario())
    assert seen == [GUILD]


def test_formatter_escapes_line_breaks_in_message() -> None:
    """A message holding line breaks and a forged entry stays on one line."""
    formatter = SingleLineFormatter("%(message)s")
    out = formatter.format(_record(msg="a\n[2026-10-28 00:00:00,000] | forged\r\nb"))
    assert "\n" not in out
    assert "\r" not in out
    assert out == "a\\n[2026-10-28 00:00:00,000] | forged\\r\\nb"


def test_formatter_keeps_traceback_and_defaults_guild_field() -> None:
    """The traceback keeps its lines and a record without guild_id formats as None."""
    formatter = SingleLineFormatter("%(guild_id)s | %(message)s")

    def _raise_bad() -> None:
        """Raise the error whose traceback the record carries."""
        raise ValueError("bad")

    try:
        _raise_bad()
    except ValueError:
        record = _record(exc_info=sys.exc_info())
    out = formatter.format(record)
    assert out.startswith("None | hello\nTraceback")
    assert "ValueError: bad" in out


@pytest.mark.parametrize(
    ("event", "data", "expected"),
    [
        ("MESSAGE_CREATE", {"guild_id": "111111111111111111"}, GUILD),
        ("GUILD_CREATE", {"id": "111111111111111111"}, GUILD),
        ("GUILD_DELETE", {"id": "111111111111111111"}, GUILD),
        ("READY", {"user": {}}, None),
        ("MESSAGE_CREATE", {"channel_id": "5"}, None),
        ("MESSAGE_CREATE", [1, 2], None),
        ("MESSAGE_CREATE", {"guild_id": "abc"}, None),
        ("MESSAGE_CREATE", {"guild_id": "9" * 5000}, None),
        ("MESSAGE_CREATE", {"guild_id": -5}, None),
    ],
)
def test_payload_guild_id_reads_guild_id_and_guild_events(event: str, data: object, expected: int | None) -> None:
    """The guild comes from guild_id, or from id on the three guild events."""
    assert payload_guild_id(event, data) == expected


def test_gateway_wrapper_sets_guild_during_parse_only() -> None:
    """The parser sees the guild while it runs; the context is clear afterwards."""
    seen: list[int | None] = []
    parsers = {"MESSAGE_CREATE": lambda _data: seen.append(current_guild_id.get())}
    install_gateway_guild_context(parsers)
    parsers["MESSAGE_CREATE"]({"guild_id": str(GUILD)})
    assert seen == [GUILD]
    assert current_guild_id.get() is None


def test_gateway_wrapper_resets_after_parser_error() -> None:
    """A parser that raises still leaves the context clear."""

    def failing(_data: object) -> None:
        """Raise like a broken parser."""
        raise RuntimeError("boom")

    parsers = {"MESSAGE_CREATE": failing}
    install_gateway_guild_context(parsers)
    with pytest.raises(RuntimeError):
        parsers["MESSAGE_CREATE"]({"guild_id": str(GUILD)})
    assert current_guild_id.get() is None


def test_gateway_wrapper_installs_once() -> None:
    """Installing twice does not wrap a parser a second time."""
    original = MagicMock()
    parsers = {"MESSAGE_CREATE": original}
    install_gateway_guild_context(parsers)
    install_gateway_guild_context(parsers)
    parsers["MESSAGE_CREATE"]({"guild_id": str(GUILD)})
    assert original.call_count == 1


def test_install_reports_missing_interaction_parser() -> None:
    """The install reports False when the interaction parser is absent or the table is not a dict."""
    assert install_gateway_guild_context({"MESSAGE_CREATE": MagicMock()}) is False
    assert install_gateway_guild_context(MagicMock()) is False
    assert install_gateway_guild_context({"INTERACTION_CREATE": MagicMock()}) is True


def _interaction_payload(*, guild: bool) -> dict[str, Any]:
    """Build an application command interaction payload, in a guild or in a DM."""
    user = {"id": "42", "username": "u", "discriminator": "0", "avatar": None}
    payload: dict[str, Any] = {
        "id": "1",
        "application_id": "9",
        "type": 2,
        "token": "t",
        "version": 1,
        "channel_id": "555555555555555555",
        "data": {"id": "3", "name": "ping", "type": 1},
        "locale": "fr",
        "guild_locale": "fr",
        "entitlements": [],
        "authorizing_integration_owners": {},
        "app_permissions": "0",
        "attachment_size_limit": 0,
        "context": 0,
    }
    if guild:
        payload["guild_id"] = str(GUILD)
        payload["member"] = {
            "user": user,
            "roles": [],
            "joined_at": "2020-01-01T00:00:00+00:00",
            "deaf": False,
            "mute": False,
            "permissions": "0",
            "flags": 0,
        }
    else:
        payload["user"] = user
    return payload


async def _run_real_parser(payload: dict[str, Any]) -> list[int | None]:
    """Feed a payload to the real interaction parser and return what the command task saw."""
    client = discord.Client(intents=discord.Intents.none())
    state = client._connection
    state.user = discord.ClientUser(
        state=state,
        data={"id": "9", "username": "bot", "discriminator": "0", "avatar": None, "bot": True},
    )
    seen: list[int | None] = []

    async def record() -> None:
        """Record the guild seen by the command task."""
        seen.append(current_guild_id.get())

    tree = MagicMock()
    tree._from_interaction = lambda _interaction: asyncio.create_task(record())
    state._command_tree = tree
    assert install_gateway_guild_context(state.parsers) is True
    state.parsers["INTERACTION_CREATE"](payload)
    await asyncio.sleep(0)
    assert current_guild_id.get() is None
    return seen


def test_real_interaction_parser_hands_guild_to_command_task() -> None:
    """The real parser starts the command task inside the guild's context."""
    assert asyncio.run(_run_real_parser(_interaction_payload(guild=True))) == [GUILD]


def test_real_interaction_parser_dm_has_no_guild() -> None:
    """A DM interaction starts its command task with no guild."""
    assert asyncio.run(_run_real_parser(_interaction_payload(guild=False))) == [None]
