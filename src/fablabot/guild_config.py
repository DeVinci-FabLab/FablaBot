"""Utilities to persist and retrieve guild-specific bot configuration."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

CONFIG_FILE = Path("data/guild_config.json")
_GUILD_KEY_COMMANDS = "commands_channel_id"
_KEY_LOG = "log_channel_id"
_GUILD_KEY_WELCOME = "welcome_verify_enabled"
_GLOBAL_SECTION = "__global__"


def _load_all() -> dict[str, dict[str, Any]]:
    """Load all guild configuration data from the JSON file.

    Returns:
        dict[str, dict[str, Any]]: The loaded configuration data, keyed by guild ID.
    """
    if not CONFIG_FILE.exists():
        return {}
    try:
        with CONFIG_FILE.open("r", encoding="utf-8") as stream:
            data = json.load(stream)
    except json.JSONDecodeError:
        logger.exception("Failed to decode guild configuration JSON; ignoring contents.")
        return {}
    if not isinstance(data, dict):
        logger.warning("Guild configuration file does not contain an object; ignoring contents.")
        return {}

    return {guild_id: payload for guild_id, payload in data.items() if isinstance(payload, dict)}


def _save_all(data: dict[str, dict[str, Any]]) -> None:
    """Save all guild configuration data to the JSON file.

    Args:
        data (dict[str, dict[str, Any]]): The configuration data to save, keyed by guild ID.
    """
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = CONFIG_FILE.with_suffix(".tmp")
    with tmp_path.open("w", encoding="utf-8") as stream:
        json.dump(data, stream, indent=2, sort_keys=True)
        stream.write("\n")
    tmp_path.replace(CONFIG_FILE)


def _update_entry(guild_id: int | None, **updates: int | bool | None) -> None:
    """Update the configuration entry for a specific guild or the global section.

    Args:
        guild_id (int | None): The ID of the guild to update.
        **updates (int | bool | None): Key-value pairs to update in the guild's configuration.
    """
    key = _GLOBAL_SECTION if guild_id is None else str(guild_id)
    data = _load_all()
    entry = dict(data.get(key, {}))
    changed = False
    for field, value in updates.items():
        if value is None:
            if field in entry:
                entry.pop(field)
                changed = True
        elif entry.get(field) != value:
            entry[field] = value
            changed = True
    if not changed:
        return
    if entry:
        data[key] = entry
    elif key in data:
        data.pop(key)
    _save_all(data)


def get_commands_channel_id(guild_id: int) -> int | None:
    """Retrieve the commands channel ID for a specific guild.

    Args:
        guild_id (int): The ID of the guild to retrieve the commands channel ID for.

    Returns:
        int | None: The commands channel ID if it exists, otherwise None.
    """
    entry = _load_all().get(str(guild_id), {})
    value = entry.get(_GUILD_KEY_COMMANDS)
    return int(value) if isinstance(value, int) else None


def set_commands_channel_id(guild_id: int, channel_id: int | None) -> None:
    """Set the commands channel ID for a specific guild.

    Args:
        guild_id (int): The ID of the guild to set the commands channel ID for.
        channel_id (int | None): The channel ID to set, or None to unset it.
    """
    _update_entry(guild_id, **{_GUILD_KEY_COMMANDS: channel_id})


def get_log_channel_id() -> int | None:
    """Retrieve the log channel ID from the global configuration.

    Returns:
        int | None: The log channel ID if it exists, otherwise None.
    """
    data = _load_all()
    entry = data.get(_GLOBAL_SECTION, {})
    value = entry.get(_KEY_LOG)
    if isinstance(value, int):
        return int(value)
    return None


def set_log_channel_id(channel_id: int) -> None:
    """Set the log channel ID.

    Args:
        channel_id (int): The channel ID to set.
    """
    data = _load_all()
    updated = False

    current_global = dict(data.get(_GLOBAL_SECTION, {}))
    if current_global.get(_KEY_LOG) != channel_id:
        current_global[_KEY_LOG] = channel_id
        data[_GLOBAL_SECTION] = current_global
        updated = True

    if updated:
        _save_all(data)


def is_welcome_verify_enabled(guild_id: int) -> bool:
    """Check if welcome verification is enabled for a specific guild.

    Args:
        guild_id (int): The ID of the guild to check.

    Returns:
        bool: True if welcome verification is enabled, False otherwise.
    """
    entry = _load_all().get(str(guild_id), {})
    value = entry.get(_GUILD_KEY_WELCOME)
    return bool(value) if isinstance(value, bool) else False


def set_welcome_verify_enabled(guild_id: int, *, enabled: bool) -> None:
    """Set the welcome verification status for a specific guild.

    Args:
        guild_id (int): The ID of the guild to set the status for.
        enabled (bool): Whether to enable or disable the feature.
    """
    _update_entry(guild_id, **{_GUILD_KEY_WELCOME: bool(enabled)})
