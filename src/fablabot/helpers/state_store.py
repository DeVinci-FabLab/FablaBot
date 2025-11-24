"""Shared helpers for loading and saving JSON-backed guild state."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
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
            return json.load(handle)
    except Exception:
        logger.exception(f"Failed to load state from {path}")
        return {}


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
