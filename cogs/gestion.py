import discord
from discord.ext import commands
from discord import app_commands, Permissions
from dotenv import load_dotenv
from discord.ui import Button , View
import os

GUILD_TOKEN = int(os.environ.get("GUILD_TOKEN"))
MY_GUILD = discord.Object(id=GUILD_TOKEN)

overwrite=[discord.PermissionOverwrite() for i in range(3)]
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
      async def channel(self, ctx: commands.Context, channel : str, category: discord.CategoryChannel):
            guild = ctx.guild
            if channel not in [i.name for i in category.channels]:
                  await guild.create_text_channel (channel,category=category)
                  await ctx.reply(f'Le salon "{channel}" a été créé dans {category.name} !')
            else:
                  await ctx.reply(f"Un salon {channel} existe déjà dans {category.name} !")
            #val = discord.utils.get(guild.categories, name=category)
            
      @commands.hybrid_command(name="role_perm", with_app_command=True, description="Change the role permissions of a channel")
      @app_commands.describe(permission='permission chosen')
      @app_commands.choices(permission=[
         app_commands.Choice(name="Admin", value=0),
         app_commands.Choice(name="Peut Modifier", value=1),
         app_commands.Choice(name="Invité", value=2),
         app_commands.Choice(name="Perona Non Gratta", value=3),
      ])
      @commands.has_permissions(administrator=True)
      async def role_perm(self, ctx: commands.Context ,channel : discord.TextChannel, role : discord.Role, perm : discord.app_commands.Choice[int] ):#personne : discord.Member=None
            #get role with name role
            await channel.set_permissions(role, overwrite=overwrite[perm])
            await ctx.reply(f"Les permissions du salon {channel} ont été modifiées !")

      @commands.hybrid_command(name="user_perm", with_app_command=True, description="Change permission of a user in a channel")
      @app_commands.describe(permission='permission chosen')
      @app_commands.choices(permission=[
         app_commands.Choice(name="Admin", value=0),
         app_commands.Choice(name="Peut Modifier", value=1),
         app_commands.Choice(name="Invité", value=2),
         app_commands.Choice(name="Perona Non Gratta", value=3),
      ])
      @commands.has_permissions(administrator=True)
      async def user_perm(self, ctx: commands.Context ,channel : discord.TextChannel, user : discord.User, permission : discord.app_commands.Choice[int] ):#personne : discord.Member=None
            #get user with name user
            await channel.set_permissions(user, overwrite=overwrite[permission.value])
            await ctx.reply(f"Les permissions du salon {channel} ont été modifiées !")


      

      @commands.hybrid_command(name="hello", with_app_command=True, description="Create a new role")
      async def hello(self, ctx: commands.Context):
            button = Button(style=discord.ButtonStyle.green, label="Hello")
            view=View()
            view.add_item(button)
            message = await ctx.send("Cliquez sur ce bouton pour recevoir le rôle Validé", view=view)

      @commands.hybrid_command(name="reboot", with_app_command=True, description="Reeboot server")
      async def reboot(self, ctx: commands.Context):
          os.system("reboot")

async def setup(client:commands.Bot) -> None:
         await client.add_cog(gestion(client))