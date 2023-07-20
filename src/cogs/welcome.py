import os

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

GUILD_TOKEN = int(os.environ.get("GUILD_TOKEN"))
MY_GUILD = discord.Object(id=GUILD_TOKEN)


class Welcome(commands.Cog):
    def __init__(self, client: commands.Bot):
        self.client = client
        client.tree.copy_global_to(guild=MY_GUILD)

    # @commands.Cog.listener()
    # async def on_member_join(self, member):
    #         if discord.utils.get(member.guild.categories, name=member.name) is None:
    #                 await member.guild.create_category (member.name)


async def setup(client: commands.Bot) -> None:
    await client.add_cog(Welcome(client))
