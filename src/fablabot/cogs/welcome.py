from warnings import deprecated
from discord.ext import commands


@deprecated("This cog does nothing.")
class Welcome(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot


@deprecated("Load the cog using `bot.add_cog()` instead.")
async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Welcome(bot))
