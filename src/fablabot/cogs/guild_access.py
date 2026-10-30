"""Leave the servers outside the allow-list, on join and at start."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

from fablabot.helpers.guild_access import ALLOWED_GUILD_IDS_ENV

if TYPE_CHECKING:
    from fablabot.main import Fablabot

logger = logging.getLogger(__name__)


class GuildAccess(commands.Cog):
    """Keep the bot in the servers it is meant to serve."""

    def __init__(self, bot: Fablabot) -> None:
        """Initialize the cog.

        Args:
            bot (Fablabot): The bot carrying the access policy.
        """
        self.bot = bot
        # Guilds present at the first ready predate this process; later ones went through the join rule.
        self._first_ready_done = False

    async def _owner_owns(self, guild: discord.Guild) -> bool:
        """Tell whether the application owner (or a team admin or developer) owns the guild.

        Args:
            guild (discord.Guild): The guild to check.

        Returns:
            bool: True when ownership is proven; False when it is not, or cannot be checked.
        """
        if guild.owner_id is None:
            return False
        try:
            return await self.bot.is_owner(discord.Object(id=guild.owner_id))  # pyright: ignore[reportArgumentType] -- is_owner only reads .id
        except discord.HTTPException as exc:
            logger.warning(f"Could not check the application owner (guild_id={guild.id}): {exc}")
            return False

    async def _leave(self, guild: discord.Guild, reason: str) -> bool:
        """Leave a guild; a failure is logged and does not stop the caller.

        Args:
            guild (discord.Guild): The guild to leave.
            reason (str): Why the bot leaves: ``not_allowed`` or ``owner_not_proven``.

        Returns:
            bool: True when the bot left.
        """
        logger.warning(f"Leaving guild: {reason} (guild_id={guild.id})")
        try:
            await guild.leave()
        except discord.HTTPException as exc:
            # No traceback: the HTTP error text is enough, and a retry comes with the next start.
            logger.error(f"Could not leave guild (guild_id={guild.id}): {exc}")  # noqa: TRY400
            return False
        return True

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
        """Leave a guild the bot should not serve.

        Args:
            guild (discord.Guild): The guild the bot joined.
        """
        policy = self.bot.guild_access
        # A fresh join starts over: a guild tolerated at start and re-added since is judged again.
        policy.tolerated.discard(guild.id)
        if policy.allowed:
            if guild.id not in policy.allowed:
                await self._leave(guild, "not_allowed")
        else:
            await self._admit_or_leave(guild)

    async def _admit_or_leave(self, guild: discord.Guild) -> bool:
        """Apply the empty-list join rule: keep a guild owned by the application owner, else leave.

        Args:
            guild (discord.Guild): The guild to decide on.

        Returns:
            bool: True when the guild is left.
        """
        if await self._owner_owns(guild):
            self.bot.guild_access.tolerated.add(guild.id)
            return False
        await self._leave(guild, "owner_not_proven")
        return True

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        """Reconcile the current guilds with the allow-list, on every full connection."""
        policy = self.bot.guild_access
        guilds = list(self.bot.guilds)
        leaving = 0

        if not policy.allowed:
            logger.warning(
                f"{ALLOWED_GUILD_IDS_ENV} is empty: staying in every current guild; "
                "set it to the guild IDs this instance serves.",
            )
            for guild in guilds:
                if not self._first_ready_done:
                    # Only at start: ready also fires after a reconnect, and a guild refused since
                    # (failed leave, or joined while disconnected) must not become served then.
                    policy.tolerated.add(guild.id)
                    if not await self._owner_owns(guild):
                        logger.warning(
                            f"Staying in guild without proof of ownership: allow-list empty (guild_id={guild.id})",
                        )
                elif guild.id not in policy.tolerated and await self._admit_or_leave(guild):
                    leaving += 1
        elif guilds and not any(guild.id in policy.allowed for guild in guilds):
            # A list matching nothing is more likely a typo than a wish to leave everywhere, and a
            # departure is costly to undo: commands stay refused by the tree.
            logger.error(
                f"{ALLOWED_GUILD_IDS_ENV} matches none of the {len(guilds)} current guilds: "
                "leaving none and refusing their commands; check the IDs.",
            )
        else:
            unlisted = [guild for guild in guilds if guild.id not in policy.allowed]
            leaving = len(unlisted)
            for guild in unlisted:
                await self._leave(guild, "not_allowed")

        self._first_ready_done = True
        logger.info(f"Guild allow-list: {len(policy.allowed)} IDs listed, in {len(guilds)} guilds, leaving {leaving}.")
