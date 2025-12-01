"""Shared helpers for loading and saving JSON-backed guild state."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping
    from logging import Logger
    from pathlib import Path


def load_json_state(logger: Logger, path: Path) -> dict[str, Any]:
    """Load a JSON state file from disk.

    Args:
        logger (Logger): Logger used for diagnostics.
        path (Path): Path to the JSON file.

    Returns:
        dict[str, Any]: The loaded state, or an empty dict on failure.
    """
    if not path.exists():
        logger.debug(f"State file {path} not found; starting with empty state.")
        return {}

    try:
        with path.open("r", encoding="utf-8") as handle:
            state = json.load(handle)
    except Exception:
        logger.exception(f"Failed to load state from {path}")
        return {}

    logger.debug(f"Loaded state from {path}")
    return state


def save_json_state(logger: Logger, path: Path, state: dict[str, Any]) -> None:
    """Persist a JSON state file to disk atomically.

    Args:
        logger (Logger): Logger used for diagnostics.
        path (Path): Path to the JSON file.
        state (dict[str, Any]): State payload to serialize.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(".tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2)
        tmp_path.replace(path)
    except Exception:
        logger.exception(f"Failed to persist state to {path}")
    else:
        logger.debug(f"Saved state to {path}")


class JsonStateStore:
    """Lightweight JSON-backed state container shared across cogs."""

    def __init__(
        self,
        logger: Logger,
        path: Path,
        *,
        guild_default_factory: Callable[[], dict[str, Any]] | None = None,
        initial_state: dict[str, Any] | None = None,
    ) -> None:
        """Initialize the state store and load existing data from disk.

        Args:
            logger (Logger): Logger used for diagnostics.
            path (Path): File path for persisting the state.
            guild_default_factory (Callable[[], dict[str, Any]] | None, optional): Factory
                invoked when a guild has no entry yet. Defaults to an empty dict.
            initial_state (dict[str, Any] | None, optional): Preloaded state (mostly for tests).
                When provided, the JSON file is not read on initialization.
        """
        self.logger = logger
        self.path = path
        self._guild_default_factory = guild_default_factory or dict
        self.state: dict[str, Any] = initial_state or load_json_state(logger, path)

    def save(self) -> None:
        """Persist the current in-memory state to disk."""
        save_json_state(self.logger, self.path, self.state)

    def save_state(self, _: dict[str, Any] | None = None) -> None:
        """Compatibility wrapper so callbacks can ignore the passed state."""
        self.save()

    def ensure_guild(self, guild_id: int) -> dict[str, Any]:
        """Ensure a guild entry exists and return it."""
        key = str(guild_id)
        if key not in self.state:
            self.state[key] = self._guild_default_factory()
        return self.state[key]

    def set_guild(self, guild_id: int, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Replace a guild entry with a sanitized payload and persist it."""
        guild_state = dict(payload)
        self.state[str(guild_id)] = guild_state
        self.save()
        return guild_state

    def update_guild(self, guild_id: int, mutator: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
        """Apply a mutation function to a guild entry and persist changes."""
        guild_state = self.ensure_guild(guild_id)
        mutator(guild_state)
        self.save()
        return guild_state

    def guild_items(self) -> Iterable[tuple[str, Any]]:
        """Iterate over guild_id -> state mappings (string guild ids)."""
        return self.state.items()
