import os

import discord
from discord.ext import commands
from dotenv import load_dotenv

from src.fablabot.cogs import formation, usermanagement, welcome

load_dotenv()

DISCORD_TOKEN: str = os.environ.get("DISCORD_TOKEN") or ""


class Client(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix=commands.when_mentioned_or("$"),
            intents=discord.Intents.all(),  # TODO: only enable intents we use, here and on the developer portal, this will make discord happy
        )
        self.cogs_list = [
            usermanagement.__name__,
            welcome.__name__,
            formation.__name__,
        ]  # Add here the new cogs

    async def setup_hook(self):
        for ext in self.cogs_list:
            await self.load_extension(ext)
        # Synchronisation globale des commandes
        await self.tree.sync()

    async def on_command_error(self, ctx: commands.Context, exception: Exception):
        await ctx.reply(str(exception), ephemeral=True)


def main():
    client = Client()
    client.run(DISCORD_TOKEN)


if __name__ == "__main__":
    main()
