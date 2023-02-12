import discord
from discord.ext import commands
from discord import app_commands

class music(commands.Cog):
  def __init__(self, client: commands.Bot):
    self.client = client

  @app_commands.command(name="music", description="Sends hello!")
  async def music(self, interaction: discord.Interaction):
    await interaction.response.send_message(content="Hello!")

async def setup(client:commands.Bot) -> None:
  await client.add_cog(music(client))
