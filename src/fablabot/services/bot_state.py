"""What the bot remembers for itself between two starts: one text value per key, for no server."""

from __future__ import annotations

from fablabot import db


def get(key: str) -> str | None:
    """Read a value.

    Args:
        key (str): The name it was recorded under.

    Returns:
        str | None: The value, or None when nothing was recorded.
    """
    row = db.connection().execute("SELECT value FROM bot_state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def put(key: str, value: str) -> None:
    """Record a value, replacing the previous one.

    Args:
        key (str): The name to record it under.
        value (str): The value.
    """
    with db.transaction() as conn:
        conn.execute("INSERT OR REPLACE INTO bot_state (key, value) VALUES (?, ?)", (key, value))
