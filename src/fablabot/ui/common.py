"""Shared UI helpers used across modals and views."""

from __future__ import annotations

from discord import Embed


def build_preview_embed(title: str, content: str | None) -> Embed:
    """Create a consistent preview embed with empty-state handling."""
    description = (content or "").strip() or "_(vide)_"
    return Embed(title=title, description=description)
