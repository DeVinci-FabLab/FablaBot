# this file introduce the channels management and permissions

from datetime import datetime
import os
from warnings import deprecated

import discord
from discord import app_commands
from discord.ext import commands

CURRENT_TIME = datetime.now().strftime("%Y/%m/%d, %H:%M:%S")

# define the different permissions [admin, invited, read only , blacklist]
# TODO: replace with named tuple
permissions = {
    "send_messages": [True, True, False, False],
    "read_messages": [True, True, True, False],
    "manage_messages": [True, False, False, False],
    "manage_channels": [True, False, False, False],
    "manage_roles": [True, False, False, False],
    "manage_permissions": [True, False, False, False],
    "manage_emojis": [True, True, False, False],
    "mention_everyone": [True, False, False, False],
    "create_private_threads": [True, True, False, False],
    "create_public_threads": [True, True, False, False],
    "read_message_history": [True, True, True, False],
    "add_reactions": [True, True, True, False],
    "attach_files": [True, True, False, False],
}
overwrite = [discord.PermissionOverwrite() for i in range(4)]

for perm_name in permissions:
    for level in range(len(overwrite)):
        overwrite[level].update(**{perm_name: permissions[perm_name][level]})
overwrite += [None]


# create a function that permit to check if user is a super user
def is_super_user(interaction: discord.Interaction) -> bool:
    assert isinstance(interaction.user, discord.Member)  # Satisfies type checker
    # Vérifie si l'utilisateur a au moins un des rôles requis
    allowed_roles = {"Président.e", "Vice-Président.e"}
    return any(role.name in allowed_roles for role in interaction.user.roles)


# determine if a user is a super user in the channel where the command is executed (for example, if the perms are given manually)
def is_super_channel_user(interaction: discord.Interaction, channel: discord.TextChannel) -> bool:
    assert isinstance(interaction.user, discord.Member)  # Satisfies type checker
    return is_super_user(interaction) or any(channel.overwrites_for(i).manage_messages for i in interaction.user.roles)


class ChannelManagement(app_commands.Group):
    # clear channel
    @app_commands.command(name="clear")  # description="clear channel"
    @commands.check(is_super_user)
    async def clear(self, interaction: discord.Interaction):
        print(f"{CURRENT_TIME} clear {interaction.user.name}:{interaction.user.id}")
        await interaction.response.send_message("Channel will  be cleared")
        await interaction.channel.purge(limit=100)

    # create a command to create a new channel in a selected zone with hybride commands
    @app_commands.command(name="creation", description="Create a new channel in a selected zone")
    @app_commands.guilds()
    async def channelcreation(
        self,
        interaction: discord.Interaction,
        channel: str,
        category: discord.CategoryChannel,
    ):
        print(f"{CURRENT_TIME} creation {interaction.user.name}:{interaction.user.id} {channel} {category}")
        # check if user has permission manage_permissions in channel
        if channel not in [i.name for i in category.channels]:
            await interaction.guild.create_text_channel(channel, category=category)
            await interaction.response.send_message(f'Le salon "{channel}" a été créé dans {category.name} !')
            # find the channel inside category
            channelData = discord.utils.get(category.channels, name=channel)
            await channelData.set_permissions(interaction.user, overwrite=overwrite[0])
        else:
            await interaction.response.send_message(f"Un salon {channel} existe déjà dans {category.name} !")
        # val = discord.utils.get(guild.categories, name=category)


class UserManagementGroup(app_commands.Group):  # le nom de la fonction n'a aucun sens
    @app_commands.command(name="op", description="op a user")
    async def op(self, interaction: discord.Interaction, user: discord.User):
        print(f"{CURRENT_TIME} op {interaction.user.name}:{interaction.user.id} {user}")

        ADMIN_ROLE = discord.utils.get(interaction.guild.roles, name="Admin -temp-")
        if is_super_user(interaction):
            await user.add_roles(ADMIN_ROLE)
            await interaction.response.send_message(f"Le rôle ADMIN a été ajouté à {user} !")
            return
        else:
            await interaction.response.send_message("Vous n'avez pas la permission d'ajouter ce rôle !")
            return

    @app_commands.command(name="deop", description="deop a user")
    async def deop(self, interaction: discord.Interaction, user: discord.User):
        print(f"{CURRENT_TIME} deop {interaction.user.name}:{interaction.user.id} {user}")
        ADMIN_ROLE = discord.utils.get(interaction.guild.roles, name="Admin -temp-")
        if is_super_user(interaction):
            await user.remove_roles(ADMIN_ROLE)
            await interaction.response.send_message(f"Le rôle ADMIN a été retiré à {user} !")
            return
        else:
            await interaction.response.send_message("Vous n'avez pas la permission de retirer ce rôle !")
            return

    @app_commands.command(name="add_role", description="add a role to a user")
    async def user_add_role(self, interaction: discord.Interaction, user: discord.User, role: discord.Role):
        print(f"{CURRENT_TIME} user_role_add {interaction.user.name}:{interaction.user.id} {user} {role}")
        if is_super_user(interaction):
            await user.add_roles(role)
            await interaction.response.send_message(f"Le rôle {role} a été ajouté à {user} !")
            return
        if (
            role.name[0:3] == "F -"
            and [roles for roles in [i.name for i in interaction.user.roles] if roles in ["Respo Formation"]] != []
        ):
            # add permissions manage roles to user
            await user.add_roles(role)
            await interaction.response.send_message(f"Le rôle {role} a été ajouté à {user} !")
            return
        else:
            await interaction.response.send_message("Vous n'avez pas la permission d'ajouter ce rôle !")
            return

    @app_commands.command(name="remove_role", description="remove a role from a user")
    async def user_remove_role(self, interaction: discord.Interaction, user: discord.User, role: discord.Role):
        print(f"{CURRENT_TIME} user_role_remove {interaction.user.name}:{interaction.user.id} {user} {role}")
        if is_super_user(interaction):
            await user.remove_roles(role)
            await interaction.response.send_message(f"Le rôle {role} a été retiré à {user} !")
            return
        if role.name[0:3] == "F -" and [roles for roles in [i.name for i in user.roles] if roles in ["Respo Formation"]] != []:
            # add permissions manage roles to user
            await user.remove_roles(role)
            await interaction.response.send_message(f"Le rôle {role} a été retiré à {user} !")
            return
        else:
            await interaction.response.send_message("Vous n'avez pas la permission de retirer ce rôle !")
            return

    @app_commands.command(
        name="permission_channel",
        description="Change permission of a user in a channel",
    )
    @app_commands.describe(permission="permission chosen")
    # definition of the different permissions category
    @app_commands.choices(
        permission=[
            app_commands.Choice(name="Admin", value=0),
            app_commands.Choice(name="Peut Modifier", value=1),
            app_commands.Choice(name="Invité", value=2),
            app_commands.Choice(name="Blacklisted", value=3),
            app_commands.Choice(name="Remove Permissions", value=4),
        ]
    )
    async def user_permission_channel(
        self,
        interaction: discord.Interaction,
        user: discord.User,
        channel: discord.TextChannel,
        permission: discord.app_commands.Choice[int],
    ):  # personne : discord.Member=None
        print(
            f"{CURRENT_TIME} user_permission_channel {interaction.user.name}:{interaction.user.id} {channel} {user} {permission}"
        )
        # get user with name user
        if is_super_channel_user(interaction, channel):
            await channel.set_permissions(user, overwrite=overwrite[permission.value])
            await interaction.response.send_message(f"Les permissions du salon {channel} ont été modifiées !")
        else:
            await interaction.response.send_message("Vous n'avez pas la permission de créer un salon !")
            return


class ServerManagement(app_commands.Group):
    # reboot the server to update commands
    @app_commands.command(name="reboot", description="Reboot server")
    @app_commands.check(is_super_user)
    async def reboot(self, interaction: discord.Interaction):
        print(f"{CURRENT_TIME} reboot {interaction.user.name}:{interaction.user.id}")
        os.system("reboot")
        return


class ChannelPermissionsManager(app_commands.Group):
    # Command that change the permissions for a certain role
    @app_commands.command(
        name="channel_permission",
        description="Change permission of a user in a channel",
    )
    @app_commands.describe(permission="permission chosen")
    # definition of the different permissions category
    @app_commands.choices(
        permission=[
            app_commands.Choice(name="Admin", value=0),
            app_commands.Choice(name="Invited User", value=1),
            app_commands.Choice(name="Read Only", value=2),
            app_commands.Choice(name="Blacklisted", value=3),
            app_commands.Choice(name="Remove Permissions", value=4),
        ]
    )
    async def channel_permission(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        role: discord.Role,
        permission: discord.app_commands.Choice[int],
    ):
        print(f"{CURRENT_TIME} channel_permission {interaction.user.name}:{interaction.user.id} {channel} {role} {permission}")
        if is_super_channel_user(interaction, channel):
            await channel.set_permissions(role, overwrite=overwrite[permission.value])
            await interaction.response.send_message(f"Les permissions du salon {channel} ont été modifiées !")
        else:
            await interaction.response.send_message("Vous n'avez pas la permission de créer un salon !")
            return


class UserManagement(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        for group in (
            ChannelManagement(name="channel", description="Gestion des salons"),
            UserManagementGroup(name="user", description="Gestion des utilisateurs"),
            ChannelPermissionsManager(name="role", description="Gestion des rôles"),
            ServerManagement(name="bot", description="Gestion du bot"),
        ):
            self.bot.tree.add_command(group)


@deprecated("Load the cog using `bot.add_cog()` instead.")
async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(UserManagement(bot))
