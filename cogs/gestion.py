#this file introduce the channels management and permissions

import discord
from discord.ext import commands
from discord import app_commands, Permissions
from dotenv import load_dotenv
from discord.ui import Button , View
import os
from datetime import datetime

GUILD_TOKEN = int(os.environ.get("GUILD_TOKEN"))
MY_GUILD = discord.Object(id=GUILD_TOKEN)
PERSONNAL_ID = int(os.environ.get("PERSONNAL_ID"))
CURRENT_TIME = datetime.now().strftime("%Y/%m/%d, %H:%M:%S")


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
def is_a_super_user(user: discord.user) -> bool:
      if user.id == PERSONNAL_ID or [roles for roles in [i.name for i in user.roles] if roles in ["Président.e","Vice-Président.e"]]:
            return True
      return False
def is_a_super_channel_user(user: discord.user, channel : discord.channel) -> bool:
      if [i.name for i in user.roles if channel.overwrites_for(i).manage_messages==True]!=[] or is_a_super_user(user):
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
            print(f"{CURRENT_TIME} channel_creation {ctx.author.name}:{ctx.author.id} {channel} {category}")
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
            print(f"{CURRENT_TIME} permission_role {ctx.author.name}:{ctx.author.id} {channel} {role} {permission}")
            if is_a_super_channel_user(ctx.author,channel):
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
            print(f"{CURRENT_TIME} permission_user {ctx.author.name}:{ctx.author.id} {channel} {user} {permission}")
            #get user with name user
            if is_a_super_channel_user(ctx.author,channel):
                  await channel.set_permissions(user, overwrite=overwrite[permission.value])
                  await ctx.reply(f"Les permissions du salon {channel} ont été modifiées !")
            else:
                  await ctx.reply("Vous n'avez pas la permission de créer un salon !")
                  return

      #reboot th server to update commands
      @commands.hybrid_command(name="reboot", with_app_command=True, description="Reeboot server")
      async def reboot(self, ctx: commands.Context):
            if is_a_super_user(ctx.author):
                  print(f"{CURRENT_TIME} reboot {ctx.author.name}:{ctx.author.id}")
                  os.system("reboot")

      #clear channel
      @commands.hybrid_command(name="clear", with_app_command=True, description="clear channel")
      async def clear(self, ctx: commands.Context):
            if is_a_super_channel_user(ctx.author,ctx.channel):
                  print(f"{CURRENT_TIME} clear {ctx.author.name}:{ctx.author.id}")
                  await ctx.send("Channel will  be cleared")
                  await ctx.channel.purge(limit=100)


      @commands.hybrid_command(name="op", with_app_command=True, description="op a user")
      async def op(self, ctx: commands.Context, user:discord.User):
            print(f"{CURRENT_TIME} op {ctx.author.name}:{ctx.author.id} {user}")

            ADMIN_ROLE=discord.utils.get(ctx.guild.roles,name="Admin -temp-")
            if is_a_super_user(ctx.author):
                  await user.add_roles(ADMIN_ROLE)
                  await ctx.reply(f"Le rôle ADMIN a été ajouté à {user} !")
                  return
            else:
                  await ctx.reply("Vous n'avez pas la permission d'ajouter ce rôle !")
                  return

      @commands.hybrid_command(name="deop", with_app_command=True, description="deop a user")
      async def deop(self, ctx: commands.Context, user:discord.User):
            print(f"{CURRENT_TIME} deop {ctx.author.name}:{ctx.author.id} {user}")
            ADMIN_ROLE=discord.utils.get(ctx.guild.roles,name="Admin -temp-")
            if is_a_super_user(ctx.author):
                  await user.remove_roles(ADMIN_ROLE)
                  await ctx.reply(f"Le rôle ADMIN a été retiré à {user} !")
                  return
            else:
                  await ctx.reply("Vous n'avez pas la permission de retirer ce rôle !")
                  return


      @commands.hybrid_command(name="user_role_add", with_app_command=True, description="add a role to a user")
      async def user_role_add(self, ctx: commands.Context, user:discord.User, role:discord.Role):
            print(f"{CURRENT_TIME} user_role_add {ctx.author.name}:{ctx.author.id} {user} {role}")
            if is_a_super_user(ctx.author):
                  await user.add_roles(role)
                  await ctx.reply(f"Le rôle {role} a été ajouté à {user} !")
                  return
            if role.name[0:3]=="F -" and [roles for roles in [i.name for i in user.roles] if roles in ["Respo Formation"]]!=[]:
                  #add permissions manage roles to user
                  await user.add_roles(role)
                  await ctx.reply(f"Le rôle {role} a été retiré à {user} !")
                  return
            else:
                  await ctx.reply("Vous n'avez pas la permission d'ajouter ce rôle !")
                  return

      @commands.hybrid_command(name="user_role_remove", with_app_command=True, description="remove a role from a user")
      async def user_role_remove(self, ctx: commands.Context, user:discord.User, role:discord.Role):
            print(f"{CURRENT_TIME} user_role_remove {ctx.author.name}:{ctx.author.id} {user} {role}")
            if is_a_super_user(ctx.author):
                  await user.remove_roles(role)
                  await ctx.reply(f"Le rôle {role} a été retiré à {user} !")
                  return
            if role.name[0:3]=="F -" and [roles for roles in [i.name for i in user.roles] if roles in ["Respo Formation"]]!=[]:
                  #add permissions manage roles to user
                  await user.remove_roles(role)
                  await ctx.reply(f"Le rôle {role} a été retiré à {user} !")
                  return
            else:
                  await ctx.reply("Vous n'avez pas la permission de retirer ce rôle !")
                  return



async def setup(client:commands.Bot) -> None:
      await client.add_cog(gestion(client))