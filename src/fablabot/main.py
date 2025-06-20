import os

import discord
from discord.ext import commands
from dotenv import load_dotenv

from fablabot.cogs import formation, user_management

load_dotenv()

DISCORD_TOKEN_FILE = os.environ.get("DISCORD_TOKEN_FILE") or ""
with open(DISCORD_TOKEN_FILE, 'r') as f:
    DISCORD_TOKEN = f.read().strip()


class Fablabot(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=discord.Intents.all(),  # TODO: only enable intents we use, here and on the developer portal, this will make discord happy
        )

    async def setup_hook(self):
        for cog in (
            formation.Formation(self),
            user_management.UserManagement(self),
        ):
            await self.add_cog(cog)

        # Synchronisation globale des commandes
        await self.tree.sync()

    async def on_command_error(self, ctx: commands.Context, exception: Exception):
        await ctx.reply(str(exception), ephemeral=True)


def main():
    bot = Fablabot()
    bot.run(DISCORD_TOKEN)


if __name__ == "__main__":
    main()
