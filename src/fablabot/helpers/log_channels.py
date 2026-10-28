"""Which channels receive the bot's error logs: one per server, plus an optional operator channel."""

from __future__ import annotations

from functools import cache
import logging
import os
from typing import Final

from fablabot.guild_config import get_log_channel_id

logger = logging.getLogger(__name__)

OPERATOR_LOG_CHANNEL_ENV: Final = "OPERATOR_LOG_CHANNEL_ID"


def parse_operator_channel_id(raw: str | None) -> int | None:
    """Parse the operator log channel ID.

    Args:
        raw (str | None): The raw value of the environment variable.

    Returns:
        int | None: The channel ID; None when the value is absent, blank or malformed.
            A malformed value is reported without being echoed, as it could be a pasted secret.
    """
    value = (raw or "").strip()
    if not value:
        return None
    # str.isdigit alone accepts non-ASCII digits.
    if not (value.isascii() and value.isdigit() and int(value) > 0):
        logger.warning(f"{OPERATOR_LOG_CHANNEL_ENV} is not a numeric channel ID: errors without a server stay in the log file.")
        return None
    return int(value)


@cache
def operator_log_channel_id() -> int | None:
    """Read the operator log channel from the environment, once.

    Returns:
        int | None: The operator channel ID, or None when it is not usable.
    """
    return parse_operator_channel_id(os.environ.get(OPERATOR_LOG_CHANNEL_ENV))


def is_log_channel(guild_id: int, channel_id: int) -> bool:
    """Tell whether a channel is the log channel of a guild or the operator channel.

    Args:
        guild_id (int): The guild the channel is used from.
        channel_id (int): The channel to check.

    Returns:
        bool: True for the guild's own log channel or the operator channel.
    """
    return channel_id in {get_log_channel_id(guild_id), operator_log_channel_id()} - {None}
