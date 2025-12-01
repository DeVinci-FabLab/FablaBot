"""Shared helpers for building consistent slash-command help messages."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable


def _ensure_sentence(text: str) -> str:
    """Ensure a piece of text ends with sentence punctuation."""
    cleaned = text.strip()
    if cleaned and cleaned[-1] not in ".":
        cleaned += "."
    return cleaned


def build_help_message(
    title: str,
    prefix: str,
    commands: Iterable[tuple[str, str]],
    *,
    footer: str | None = None,
) -> str:
    """Build a formatted help message with consistent structure.

    Args:
        title (str): The title of the help section.
        prefix (str): The command prefix to use (e.g., "/formation").
        commands (Iterable[tuple[str, str]]): An iterable of (command, description) pairs.
        footer (str | None): An optional footer message to append.
    """
    prefix = prefix.strip()
    if not prefix.startswith("/"):
        prefix = f"/{prefix}"

    lines = [f"**{title.rstrip(' :')} :**"]
    for command, description in commands:
        lines.append(f"- `{prefix} {command.strip()}` : {_ensure_sentence(description)}")
    lines.append(f"- `{prefix} help [show]` : Afficher cette aide (privée par défaut)")
    if footer:
        lines.append("")
        lines.append(_ensure_sentence(footer))
    lines.append("")
    lines.append("Assurez-vous d'avoir les permissions nécessaires pour utiliser ces commandes.")
    return "\n".join(lines)
