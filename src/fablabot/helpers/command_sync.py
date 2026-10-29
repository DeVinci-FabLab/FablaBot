"""Send the slash commands to Discord only when they changed since the last sync."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

import discord

from fablabot.helpers.guild_access import ALLOWED_GUILD_IDS_ENV

if TYPE_CHECKING:
    from discord import app_commands
    from discord.abc import Snowflake

    from fablabot.helpers.state_store import JsonStateStore

logger = logging.getLogger(__name__)

COMMAND_SYNC_STATE_FILE: Final = Path("data/command_sync_state.json")
DEV_GUILD_ID_ENV: Final = "DEV_GUILD_ID"


def parse_dev_guild_id(raw: str | None, *, allowed: frozenset[int]) -> int | None:
    """Parse the development server ID.

    Args:
        raw (str | None): The raw value of the environment variable.
        allowed (frozenset[int]): The listed guild IDs; empty when the list is not set.

    Returns:
        int | None: The guild ID, or None when the value is absent or blank.

    Raises:
        ValueError: If the value is not a positive ASCII integer, or the server is not in a
            non-empty allow-list. The message never holds the value, which could be a pasted secret.
    """
    value = (raw or "").strip()
    if not value:
        return None
    # str.isdigit alone accepts non-ASCII digits.
    if not (value.isascii() and value.isdigit() and int(value) > 0):
        msg = f"{DEV_GUILD_ID_ENV}: not a numeric guild ID"
        raise ValueError(msg)
    guild_id = int(value)
    if allowed and guild_id not in allowed:
        msg = f"{DEV_GUILD_ID_ENV}: the server is not in {ALLOWED_GUILD_IDS_ENV}"
        raise ValueError(msg)
    return guild_id


async def command_fingerprint(tree: app_commands.CommandTree, *, guild: Snowflake | None, application_id: int | None) -> str:
    """Compute a fingerprint of what a sync of ``tree`` would send, without calling Discord.

    The payload is built as ``CommandTree.sync`` builds it, translations included, then sorted so the
    order commands were added in does not matter.

    Args:
        tree (app_commands.CommandTree): The tree holding the commands.
        guild (Snowflake | None): The guild to fingerprint, or None for the global commands.
        application_id (int | None): The application the commands belong to.

    Returns:
        str: A SHA-256 hex digest.
    """
    commands = tree.get_commands(guild=guild)
    translator = tree.translator
    if translator:
        payload = [await command.get_translated_payload(tree, translator) for command in commands]
    else:
        payload = [command.to_dict(tree) for command in commands]
    payload.sort(key=lambda item: (item.get("type", 1), item["name"]))
    document: dict[str, Any] = {
        "application_id": application_id,
        "guild_id": None if guild is None else guild.id,
        "commands": payload,
    }
    text = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def sync_commands_if_changed(
    tree: app_commands.CommandTree,
    store: JsonStateStore,
    *,
    application_id: int | None,
    dev_guild_id: int | None = None,
) -> bool:
    """Sync the commands unless they match the fingerprint of the last sync.

    The fingerprint is recorded only after Discord accepted the commands, so a failed sync is retried
    at the next start. With ``dev_guild_id``, the global commands are copied to that guild and only
    that guild is synced; the global commands are left as they are.

    Args:
        tree (app_commands.CommandTree): The tree holding the commands.
        store (JsonStateStore): The store holding the fingerprint of the last sync.
        application_id (int | None): The application the commands belong to.
        dev_guild_id (int | None): The development server to sync to, or None for the global commands.

    Returns:
        bool: True when the commands were synced, False when the sync was skipped.
    """
    guild: Snowflake | None = None
    target = "global"
    if dev_guild_id is not None:
        guild = discord.Object(id=dev_guild_id)
        target = f"guild_id={dev_guild_id}"
        logger.warning(f"{DEV_GUILD_ID_ENV} is set: commands sync to {target} only; global commands are left as they are.")
        tree.copy_global_to(guild=guild)

    fingerprint = await command_fingerprint(tree, guild=guild, application_id=application_id)
    recorded = store.state.get("fingerprint") if isinstance(store.state, dict) else None
    if recorded == fingerprint:
        logger.info(f"Commands unchanged, sync skipped ({target}).")
        return False

    synced = await tree.sync(guild=guild)
    store.state = {"fingerprint": fingerprint}
    store.save()
    logger.info(f"Commands synced ({len(synced)}, {target}).")
    return True
