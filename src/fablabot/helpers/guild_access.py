"""Allow-list of the servers this bot instance serves."""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
import os
from typing import TYPE_CHECKING, Final, override

import discord
from discord import InteractionType, app_commands

from fablabot.helpers.constants import ErrorMessages

if TYPE_CHECKING:
    from collections.abc import Mapping

    from discord import Interaction

    from fablabot.main import Fablabot  # noqa: F401 -- named by the string in GuildAccessTree's base


logger = logging.getLogger(__name__)

ALLOWED_GUILD_IDS_ENV: Final = "ALLOWED_GUILD_IDS"


def parse_allowed_guild_ids(raw: str | None) -> frozenset[int]:
    """Parse a comma-separated list of guild IDs.

    Args:
        raw (str | None): The raw value of the environment variable.

    Returns:
        frozenset[int]: The listed IDs; empty when the value is absent or blank.

    Raises:
        ValueError: If an entry is not a positive ASCII integer. The message gives the
            entry position, never its value, which could be a pasted secret.
    """
    entries = [entry.strip() for entry in (raw or "").split(",")]
    entries = [entry for entry in entries if entry]
    ids: set[int] = set()
    for position, entry in enumerate(entries, start=1):
        # str.isdigit alone accepts non-ASCII digits.
        if not (entry.isascii() and entry.isdigit() and int(entry) > 0):
            msg = f"{ALLOWED_GUILD_IDS_ENV}: entry {position} is not a numeric guild ID"
            raise ValueError(msg)
        ids.add(int(entry))
    return frozenset(ids)


@dataclass
class GuildAccessPolicy:
    """Which guilds this instance serves.

    Attributes:
        allowed (frozenset[int]): The listed guild IDs.
        tolerated (set[int]): Guilds served while ``allowed`` is empty.
    """

    allowed: frozenset[int]
    tolerated: set[int] = field(default_factory=set)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> GuildAccessPolicy:
        """Build the policy from the environment.

        Args:
            environ (Mapping[str, str] | None): The variables to read; ``os.environ`` when None.

        Returns:
            GuildAccessPolicy: The policy.

        Raises:
            ValueError: If the list is malformed.
        """
        source = os.environ if environ is None else environ
        return cls(allowed=parse_allowed_guild_ids(source.get(ALLOWED_GUILD_IDS_ENV)))

    def is_served(self, guild_id: int | None) -> bool:
        """Tell whether commands from ``guild_id`` are served.

        Args:
            guild_id (int | None): The guild ID, None for a DM.

        Returns:
            bool: True when the guild is listed, or tolerated while the list is empty.
        """
        if guild_id is None:
            return False
        if self.allowed:
            return guild_id in self.allowed
        return guild_id in self.tolerated


class GuildAccessTree(app_commands.CommandTree["Fablabot"]):
    """Command tree that refuses interactions from guilds the bot does not serve."""

    @override
    async def interaction_check(self, interaction: Interaction, /) -> bool:
        """Run the access check before every slash command, context menu and autocomplete.

        Args:
            interaction (Interaction): The incoming interaction.

        Returns:
            bool: True when the guild is served.
        """
        guild_id = interaction.guild_id
        if self.client.guild_access.is_served(guild_id):
            return True

        kind = "no_guild" if guild_id is None else "not_allowed"
        logger.warning(f"Interaction refused: {kind} (guild_id={guild_id}, user_id={interaction.user.id})")
        # An autocomplete cannot carry a message, and Discord shows nothing for it.
        if interaction.type is not InteractionType.autocomplete:
            try:
                await interaction.response.send_message(ErrorMessages.GUILD_NOT_ALLOWED, ephemeral=True)
            except discord.HTTPException as exc:
                logger.warning(f"Could not answer a refused interaction (guild_id={guild_id}): {exc}")
        return False
