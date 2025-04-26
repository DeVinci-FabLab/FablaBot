import os

import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
DISCORD_TOKEN = "" if DISCORD_TOKEN is None else DISCORD_TOKEN


class Client(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix=commands.when_mentioned_or("$"),
            intents=discord.Intents().all(),
        )
        self.cogslist = ["cogs.gestion", "cogs.welcome", "cogs.formation"]  # Add here the new cogs 

    async def setup_hook(self):
        for ext in self.cogslist:
            await self.load_extension(ext)
        # Synchronisation globale des commandes
        await self.tree.sync()

    async def on_command_error(self, ctx, exception):
        await ctx.reply(exception, ephemeral=True)


client = Client()

client.run(DISCORD_TOKEN)
