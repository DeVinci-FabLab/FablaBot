"""Utility functions for loading role configuration and rights."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import discord

ROLE_FILE = Path(__file__).resolve().parent / "cogs" / "data" / "rolev2.json"

logger = logging.getLogger(__name__)


def load_roles(path: Path = ROLE_FILE) -> dict[str, list[str]]:
    """Load the role hierarchy from the given JSON file."""
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        logger.error("Role configuration file not found: %s", path)
        return {}


def can_assign_role(member: discord.Member, target_role: discord.Role, roles: dict[str, list[str]]) -> bool:
    """Check if `member` can assign `target_role`."""
    for role in member.roles:
        if role.name in roles and target_role.name in roles[role.name]:
            return True
    logger.debug(
        "Member %s cannot assign role %s: insufficient permissions.",
        member.name,
        target_role.name,
    )
    return False
