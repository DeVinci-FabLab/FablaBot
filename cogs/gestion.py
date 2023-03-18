import discord
from discord.ext import commands
from discord import app_commands, Permissions
from dotenv import load_dotenv
from discord.ui import Button , View
import os

GUILD_TOKEN = int(os.environ.get("GUILD_TOKEN"))
MY_GUILD = discord.Object(id=GUILD_TOKEN)

overwrite=[discord.PermissionOverwrite() for i in range(4)]
overwrite[0].send_messages = True
overwrite[0].read_messages = True
overwrite[1].send_messages = False
overwrite[1].read_messages = True
overwrite[2].send_messages = False
overwrite[2].read_messages = False
 

class gestion(commands.Cog):
      def __init__(self, client: commands.Bot):
            self.client = client
            client.tree.copy_global_to(guild=MY_GUILD)
            
      #create a command to create a new channel in a selected zone with hybride commands
      @commands.hybrid_command(name='channel', with_app_command=True,description="Create a new channel in a selected zone")
      @app_commands.guilds(MY_GUILD)
      @commands.has_permissions(administrator=True)
      async def channel(self, ctx: commands.Context, channel : str, category : str):
            guild = ctx.guild
            val = discord.utils.get(guild.categories, name=category) 
            if (True if channel not in [i.name for i in val.channels] else False) if val is not None else False:
                  await guild.create_text_channel (channel,category=val)
                  await ctx.reply(f'Le salon "{channel}" a été créé dans {category} !')
            elif val is None:
                  await ctx.reply(f'La catégorie "{category}" n\'existe pas !')
            else:
                  await ctx.reply(f"Un salon {channel} existe déjà dans {category}!")


      @commands.hybrid_command(name="perm", with_app_command=True, description="Change the permissions of a channel")
      @app_commands.guilds(MY_GUILD)
      @commands.has_permissions(administrator=True)
      async def perm(self, ctx: commands.Context, channel : discord.TextChannel, role : discord.Role, perm : int, personne : discord.Member=None):
            #get channel with name channel
            channel = discord.utils.get(ctx.guild.channels, name=channel)

            #get role with name role
            role = discord.utils.get(ctx.guild.roles, name=role)
            await channel.set_permissions(role, overwrite=overwrite[perm])
            await ctx.reply(f"Les permissions du salon {channel} ont été modifiées !")


      @commands.hybrid_command(name="hello", with_app_command=True, description="Create a new role")
      async def hello(self, ctx: commands.Context):
            button = Button(style=discord.ButtonStyle.green, label="Hello")
            view=View()
            view.add_item(button)
            message = await ctx.send("Cliquez sur ce bouton pour recevoir le rôle Validé", view=view)


async def setup(client:commands.Bot) -> None:
         await client.add_cog(gestion(client))