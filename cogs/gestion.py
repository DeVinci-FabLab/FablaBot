#this file introduce the channels management and permissions

import discord
from discord.ext import commands
from discord import app_commands, Permissions
from dotenv import load_dotenv
from discord.ui import Button , View
import os

GUILD_TOKEN = int(os.environ.get("GUILD_TOKEN"))
MY_GUILD = discord.Object(id=GUILD_TOKEN)
PERSONNAL_ID = int(os.environ.get("PERSONNAL_ID"))


#define the different permissions [admin, invited, read only , blacklist]
overwrite=[discord.PermissionOverwrite() for i in range(4)]
permissions={
      "send_messages":[True,True,False,False],
      "read_messages":[True,True,True,False],
      "manage_messages":[True,False,False,False],
      "manage_channels":[True,False,False,False],
      "manage_roles":[True,False,False,False],
      "manage_permissions":[True,False,False,False],
      "manage_emojis":[True,True,False,False],
      "mention_everyone":[True,False,False,False],
      "create_private_threads":[True,True,False,False],
      "create_public_threads":[True,True,False,False],
      "read_message_history":[True,True,True,False],
      "add_reactions":[True,True,True,False],
      "attach_files":[True,True,False,False]
}
for i in overwrite:
      for j in permissions:
            i.__setattr__(j,permissions[j][overwrite.index(i)])
overwrite+=[None]


#create a function that permit to check if user is a super user
def is_a_super_user(user: discord.user, channel : discord.channel) -> bool:
      if user.id == PERSONNAL_ID or [roles for roles in [i.name for i in user.roles] if roles in ["Président.e","Vice-Président.e"]]:
            return True
      if [i.name for i in user.roles if channel.overwrites_for(i).manage_messages==True]!=[]:
            return True
      return False


class gestion(commands.Cog):
      def __init__(self, client: commands.Bot):
            self.client = client
            client.tree.copy_global_to(guild=MY_GUILD)

      #create a command to create a new channel in a selected zone with hybride commands
      @commands.hybrid_command(name='channel_creation', with_app_command=True,description="Create a new channel in a selected zone")
      @app_commands.guilds(MY_GUILD)
      async def channel_creation(self, ctx: commands.Context, channel : str, category: discord.CategoryChannel):
            #check if user has permission manage_permissions in channel
            if channel not in [i.name for i in category.channels]:
                  await ctx.guild.create_text_channel (channel,category=category)
                  await ctx.reply(f'Le salon "{channel}" a été créé dans {category.name} !')
                  #find the channel inside category
                  channelData = discord.utils.get(category.channels, name=channel)
                  await channelData.set_permissions(ctx.author, overwrite=overwrite[0])
            else:
                  await ctx.reply(f"Un salon {channel} existe déjà dans {category.name} !")
            #val = discord.utils.get(guild.categories, name=category)

      #Command that change the permissions for a certain role
      @commands.hybrid_command(name="permission_role", with_app_command=True, description="Change permission of a user in a channel")
      @app_commands.describe(permission='permission chosen')
      #definition of the different permissions category
      @app_commands.choices(permission=[
         app_commands.Choice(name="Admin", value=0),
         app_commands.Choice(name="Invited User", value=1),
         app_commands.Choice(name="Read Only", value=2),
         app_commands.Choice(name="Blacklisted", value=3),
         app_commands.Choice(name="Remove Permissions", value=4),
      ])
      async def permission_role(self, ctx: commands.Context ,channel : discord.TextChannel, role : discord.Role, permission : discord.app_commands.Choice[int] ):
            if is_a_super_user(ctx.author,channel):
                  await channel.set_permissions(role, overwrite=overwrite[permission.value])
                  await ctx.reply(f"Les permissions du salon {channel} ont été modifiées !")
            else:
                  await ctx.reply("Vous n'avez pas la permission de créer un salon !")
                  return


      #Command that change the permissions for a certain user
      @commands.hybrid_command(name="permission_user", with_app_command=True, description="Change permission of a user in a channel")
      @app_commands.describe(permission='permission chosen')
      #definition of the different permissions category
      @app_commands.choices(permission=[
         app_commands.Choice(name="Admin", value=0),
         app_commands.Choice(name="Peut Modifier", value=1),
         app_commands.Choice(name="Invité", value=2),
         app_commands.Choice(name="Blacklisted", value=3),
         app_commands.Choice(name="Remove Permissions", value=4),
      ])
      async def permission_user(self, ctx: commands.Context ,channel : discord.TextChannel, user : discord.User, permission : discord.app_commands.Choice[int] ):#personne : discord.Member=None
            #get user with name user
            if is_a_super_user(ctx.author,channel):
                  await channel.set_permissions(user, overwrite=overwrite[permission.value])
                  await ctx.reply(f"Les permissions du salon {channel} ont été modifiées !")
            else:
                  await ctx.reply("Vous n'avez pas la permission de créer un salon !")
                  return

      #reboot th server to update commands
      @commands.hybrid_command(name="reboot", with_app_command=True, description="Reeboot server")
      async def reboot(self, ctx: commands.Context):
          os.system("reboot")

      #clear channel
      @commands.hybrid_command(name="clear", with_app_command=True, description="clear channel")
      async def clear(self, ctx: commands.Context):
         await ctx.send("Channel will  be cleared")
         await ctx.channel.purge(limit=100)


async def setup(client:commands.Bot) -> None:
      await client.add_cog(gestion(client))