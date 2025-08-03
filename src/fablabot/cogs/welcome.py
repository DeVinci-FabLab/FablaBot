"""Manages welcome messages and related features."""

from warnings import deprecated

from discord.ext import commands


@deprecated("This cog does nothing.")
class Welcome(commands.Cog):
    """Manages welcome messages and related features."""

    def __init__(self, bot: commands.Bot) -> None:
        """Initialize the Welcome cog.

        Args:
            bot (commands.Bot): The bot instance.
        """
        self.bot = bot


@deprecated("Load the cog using `bot.add_cog()` instead.")
async def setup(bot: commands.Bot) -> None:
    """Sets up the Welcome cog.

    Args:
        bot (commands.Bot): The bot instance.
    """
    await bot.add_cog(Welcome(bot))
