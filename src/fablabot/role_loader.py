"""Utility functions for loading role configuration and rights."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import discord

ROLE_FILE = Path(__file__).resolve().parent / "cogs" / "data" / "role.json"

logger = logging.getLogger(__name__)


def load_roles(path: Path = ROLE_FILE) -> dict[str, dict[str, int]]:
    """Load the role hierarchy from the given JSON file."""
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        logger.error("Role configuration file not found: %s", path)
        return {}


def can_assign_role(member: discord.Member, target_role: discord.Role, roles: dict[str, dict[str, int]]) -> bool:
    """Check if `member` can assign `target_role`."""
    target_role_id = roles[target_role.name]["id"]
    member_permissions_integers = (roles[role.name]["permissions_integer"] for role in member.roles if role.name in roles)
    for permission_integer in member_permissions_integers:
        if permission_integer == 8189 or (target_role_id % 2 * permission_integer) // permission_integer == 1:
            return True
    logger.debug(
        "Member %s cannot assign role %s: insufficient permissions",
        member.name,
        target_role.name,
    )
    return False
