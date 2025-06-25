"""Utility functions for loading role configuration and rights."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import discord

ROLE_FILE = Path(__file__).resolve().parent / "cogs" / "data" / "rolev3.json"

logger = logging.getLogger(__name__)


def load_roles(path: Path = ROLE_FILE) -> dict[str, dict[str, int | list[int]]]:
    """Load the role hierarchy from the given JSON file."""
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        logger.error("Role configuration file not found: %s", path)
        return {}


def can_assign_role(member: discord.Member, target_role: discord.Role, roles: dict[str, dict[str, int | list[int]]]) -> bool:
    """Check if `member` can assign `target_role`."""
    target_role_id = roles[target_role.name]["id"]
    member_right_on: set[int] = set()
    for role in member.roles:
        if role.name in roles:
            role_rights = roles[role.name]["right_on"]
            assert isinstance(role_rights, list)
            member_right_on.update(role_rights)
    logger.debug(
        "Checking if member %s with permissions on %s can assign role %s (ID: %d)",
        member.name,
        member_right_on.__str__().replace(",", ", "),
        target_role.name,
        target_role_id,
    )
    if target_role_id in member_right_on:
        return True
    logger.debug(
        "Member %s cannot assign role %s: insufficient permissions.",
        member.name,
        target_role.name,
    )
    return False
