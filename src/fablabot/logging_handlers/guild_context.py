"""Tie each log record to the server whose gateway event is being handled."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import copy
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

current_guild_id: ContextVar[int | None] = ContextVar("fablabot_log_guild_id", default=None)

_GUILD_EVENTS = frozenset({"GUILD_CREATE", "GUILD_UPDATE", "GUILD_DELETE"})
_WRAPPED_MARK = "_fablabot_guild_context"


@contextmanager
def guild_log_context(guild_id: int | None) -> Iterator[None]:
    """Hold a server as the current one, then restore the previous value.

    Args:
        guild_id: The server being handled, or None for no server.

    Yields:
        None, while the server is set.
    """
    token = current_guild_id.set(guild_id)
    try:
        yield
    finally:
        current_guild_id.reset(token)


class GuildContextFilter(logging.Filter):
    """Copy the current server onto each record as ``guild_id``."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Set ``record.guild_id``; asyncio's own records stay untied to any server.

        Args:
            record: The record being logged.

        Returns:
            Always True: the filter tags records, it never drops them.
        """
        is_asyncio = record.name == "asyncio" or record.name.startswith("asyncio.")
        record.guild_id = None if is_asyncio else current_guild_id.get()
        return True


class SingleLineFormatter(logging.Formatter):
    """Keep each entry on one line by escaping line breaks in the message."""

    def formatMessage(self, record: logging.LogRecord) -> str:  # noqa: N802
        """Format the line from a copy of the record, with line breaks in the message escaped.

        The traceback text is appended later by ``format`` and is left as it is.

        Args:
            record: The record being formatted.

        Returns:
            The formatted line.
        """
        clone = copy.copy(record)
        clone.message = record.message.replace("\r", "\\r").replace("\n", "\\n")
        if not hasattr(clone, "guild_id"):
            clone.guild_id = None
        return super().formatMessage(clone)


def payload_guild_id(event: str, data: object) -> int | None:
    """Read the server a gateway payload belongs to.

    Args:
        event: The gateway event name.
        data: The event payload.

    Returns:
        The server ID, or None for a non-dict payload, no server or a value that is not a plain server ID.
    """
    if not isinstance(data, dict):
        return None
    raw = data.get("id") if event in _GUILD_EVENTS else data.get("guild_id")
    if isinstance(raw, int) and not isinstance(raw, bool):
        return raw if raw >= 0 else None
    if isinstance(raw, str) and raw.isascii() and raw.isdigit():
        try:
            return int(raw)
        except ValueError:  # a run of thousands of digits exceeds Python's int conversion limit
            return None
    return None


def _wrap(event: str, parser: Callable[[Any], Any]) -> Callable[[Any], Any]:
    """Wrap a gateway parser so it runs inside the payload's server context."""

    def wrapper(data: Any) -> Any:
        """Run the parser with the payload's server as the current one."""
        with guild_log_context(payload_guild_id(event, data)):
            return parser(data)

    setattr(wrapper, _WRAPPED_MARK, True)
    return wrapper


def install_gateway_guild_context(parsers: object) -> bool:
    """Wrap each gateway parser, once, so tasks it starts inherit the server.

    Args:
        parsers: The discord.py parser table, mapping event names to callables.

    Returns:
        True when the table holds ``INTERACTION_CREATE``, False otherwise.
    """
    if not isinstance(parsers, dict):
        return False
    for event, parser in list(parsers.items()):
        if callable(parser) and not getattr(parser, _WRAPPED_MARK, False):
            parsers[event] = _wrap(event, parser)
    return "INTERACTION_CREATE" in parsers
