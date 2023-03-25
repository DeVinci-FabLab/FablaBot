import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv
import os

load_dotenv()

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
GUILD_TOKEN = int(os.environ.get("GUILD_TOKEN"))

MY_GUILD = discord.Object(id=GUILD_TOKEN)


class Client(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix=commands.when_mentioned_or('$'), intents=discord.Intents().all())
        self.MY_GUILD= MY_GUILD
        self.cogslist = ["cogs.music","cogs.gestion","cogs.welcome","cogs.formation"]


    async def setup_hook(self):
        for ext in self.cogslist:
            await self.load_extension(ext)
        self.tree.copy_global_to(guild=MY_GUILD)
        await self.tree.sync(guild=MY_GUILD)


    async def on_command_error(self, ctx, exception) :
        await ctx.reply(exception,ephemeral=True)

client = Client()


client.run(DISCORD_TOKEN)