"""Send the slash commands to Discord only when they changed since the last sync."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from discord import app_commands
    from discord.abc import Snowflake

    from fablabot.helpers.state_store import JsonStateStore

logger = logging.getLogger(__name__)

COMMAND_SYNC_STATE_FILE: Final = Path("data/command_sync_state.json")


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
    tree: app_commands.CommandTree, store: JsonStateStore, *, application_id: int | None
) -> bool:
    """Sync the global commands unless they match the fingerprint of the last sync.

    The fingerprint is recorded only after Discord accepted the commands, so a failed sync is retried
    at the next start.

    Args:
        tree (app_commands.CommandTree): The tree holding the commands.
        store (JsonStateStore): The store holding the fingerprint of the last sync.
        application_id (int | None): The application the commands belong to.

    Returns:
        bool: True when the commands were synced, False when the sync was skipped.
    """
    fingerprint = await command_fingerprint(tree, guild=None, application_id=application_id)
    recorded = store.state.get("fingerprint") if isinstance(store.state, dict) else None
    if recorded == fingerprint:
        logger.info("Commands unchanged, sync skipped (global).")
        return False

    synced = await tree.sync(guild=None)
    store.state = {"fingerprint": fingerprint}
    store.save()
    logger.info(f"Commands synced ({len(synced)}, global).")
    return True
