"""Channel management commands and permission utilities."""

from __future__ import annotations

from datetime import datetime
import logging
from warnings import deprecated

import discord
from discord import app_commands
from discord.ext import commands

logger = logging.getLogger(__name__)

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
overwrite = [discord.PermissionOverwrite() for _ in range(4)]

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


class ChannelManagement(app_commands.Group, name="channel", description="Gestion des salons"):
    """Manages channel-related commands.

    Args:
        app_commands (app_commands.Group): The app_commands group.
        name (str, optional): The name of the group. Defaults to "channel".
        description (str, optional): The description of the group. Defaults to "Gestion des salons".
    """

    @app_commands.command()
    @app_commands.check(is_super_user)
    async def clear(self, interaction: discord.Interaction) -> None:
        """Clears the current channel of its last 100 messages.

        Args:
            interaction: The interaction that triggered the command.
        """
        logger.info("%s clear %s:%s", CURRENT_TIME, interaction.user.name, interaction.user.id)
        if isinstance(
            interaction.channel,
            discord.TextChannel | discord.channel.VocalGuildChannel | discord.Thread,
        ):
            await interaction.channel.purge(limit=100)

    @app_commands.command()
    async def create(
        self,
        interaction: discord.Interaction,
        channel: str,
        category: discord.CategoryChannel,
    ) -> None:
        """Create a new channel in the passed category.

        Args:
            interaction (discord.Interaction): The interaction object.
            channel (str): The name of the channel to create.
            category (discord.CategoryChannel): The category to create the channel in.
        """
        assert interaction.guild is not None  # Satisfies type checker
        assert isinstance(interaction.user, discord.Member)  # Satisfies type checker
        logger.info(
            "%s create %s:%s %s %s",
            CURRENT_TIME,
            interaction.user.name,
            interaction.user.id,
            channel,
            category,
        )
        if channel not in [channel.name for channel in category.channels]:
            new_channel = await interaction.guild.create_text_channel(channel, category=category)
            await new_channel.set_permissions(interaction.user, overwrite=overwrite[0])

            await interaction.response.send_message(f"Le salon {channel!r} a été créé dans {category.name} !")
        else:
            await interaction.response.send_message(f"Un salon {channel!r} existe déjà dans {category.name} !")


class UserManagementGroup(app_commands.Group, name="user", description="Gestion des utilisateurs"):
    """Manages user-related commands.

    Args:
        app_commands (app_commands.Group): The app_commands group.
        name (str, optional): The name of the group. Defaults to "user".
        description (str, optional): The description of the group. Defaults to "Gestion des utilisateurs".

    Raises:
        RuntimeError: If the temporary admin role is not found.
        RuntimeError: If the user is not found.
    """

    @app_commands.command()
    async def op(self, interaction: discord.Interaction, user: discord.Member) -> None:
        """Gives a user temporary administrator privileges.

        Args:
            interaction (discord.Interaction): The interaction object.
            user (discord.Member): The user to give privileges to.

        Raises:
            RuntimeError: If the temporary admin role is not found.
        """
        assert interaction.guild is not None  # Satisfies type checker
        logger.info(
            "%s op %s:%s %s",
            CURRENT_TIME,
            interaction.user.name,
            interaction.user.id,
            user,
        )

        admin_role = discord.utils.get(interaction.guild.roles, name="Admin -temp-")
        if admin_role is None:
            raise RuntimeError("Temporary admin role not found.")

        await user.add_roles(admin_role)
        await interaction.response.send_message(f"Les droits administrateurs on été donnés à {user} !")

    @app_commands.command()
    async def deop(self, interaction: discord.Interaction, user: discord.Member) -> None:
        """Removes a user's temporary administrator privileges.

        Args:
            interaction (discord.Interaction): The interaction object.
            user (discord.Member): The user to remove privileges from.

        Raises:
            RuntimeError: If the temporary admin role is not found.
        """
        assert interaction.guild is not None  # Satisfies type checker
        logger.info(
            "%s deop %s:%s %s",
            CURRENT_TIME,
            interaction.user.name,
            interaction.user.id,
            user,
        )

        admin_role = discord.utils.get(interaction.guild.roles, name="Admin -temp-")
        if admin_role is None:
            raise RuntimeError("Temporary admin role not found.")

        await user.remove_roles(admin_role)
        await interaction.response.send_message(f"Les droits administrateurs on été retirés à {user} !")

    @app_commands.command()
    async def add_role(self, interaction: discord.Interaction, user: discord.Member, role: discord.Role) -> None:
        """Adds a role to a user.

        Args:
            interaction (discord.Interaction): The interaction object.
            user (discord.Member): The user to add the role to.
            role (discord.Role): The role to add to the user.
        """
        logger.info(
            "%s user_role_add %s:%s %s %s",
            CURRENT_TIME,
            interaction.user.name,
            interaction.user.id,
            user,
            role,
        )
        await user.add_roles(role)
        await interaction.response.send_message(f"Le rôle {role} a été ajouté à {user} !")

    @app_commands.command()
    async def remove_role(self, interaction: discord.Interaction, user: discord.Member, role: discord.Role) -> None:
        """Removes a role from a user.

        Args:
            interaction (discord.Interaction): The interaction object.
            user (discord.Member): The user to remove the role from.
            role (discord.Role): The role to remove from the user.
        """
        logger.info(
            "%s user_role_remove %s:%s %s %s",
            CURRENT_TIME,
            interaction.user.name,
            interaction.user.id,
            user,
            role,
        )
        await user.remove_roles(role)
        await interaction.response.send_message(f"Le rôle {role} a été retiré à {user} !")

    @app_commands.command()
    @app_commands.describe(permission="permission chosen")
    @app_commands.choices(
        permission=[
            app_commands.Choice(name="Admin", value=0),
            app_commands.Choice(name="Invited User", value=1),
            app_commands.Choice(name="Read Only", value=2),
            app_commands.Choice(name="Blacklisted", value=3),
            app_commands.Choice(name="Remove Permissions", value=4),
        ]
    )
    @app_commands.check(is_super_user)
    async def permission_channel(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        channel: discord.TextChannel,
        permission: discord.app_commands.Choice[int],
    ):
        """Changes the permissions for a user in a given channel."""
        print(f"{CURRENT_TIME} permission_channel {interaction.user.name}:{interaction.user.id} {channel} {user} {permission}")
        await channel.set_permissions(user, overwrite=overwrite[permission.value])
        await interaction.response.send_message(f"Les permissions du salon {channel} ont été modifiées !")


class ChannelPermissionsManager(app_commands.Group, name="role", description="Gestion des rôles"):
    # Command that change the permissions for a certain role
    @app_commands.command()
    @app_commands.describe(permission="permission chosen")
    @app_commands.choices(
        permission=[
            app_commands.Choice(name="Admin", value=0),
            app_commands.Choice(name="Invited User", value=1),
            app_commands.Choice(name="Read Only", value=2),
            app_commands.Choice(name="Blacklisted", value=3),
            app_commands.Choice(name="Remove Permissions", value=4),
        ]
    )
    @app_commands.check(is_super_user)
    async def channel_permission(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        role: discord.Role,
        permission: discord.app_commands.Choice[int],
    ):
        """Changes the permissions of a user in a channel."""
        print(f"{CURRENT_TIME} channel_permission {interaction.user.name}:{interaction.user.id} {channel} {role} {permission}")
        await channel.set_permissions(role, overwrite=overwrite[permission.value])
        await interaction.response.send_message(f"Les permissions du salon {channel} ont été modifiées !")


class UserManagement(commands.Cog):
    """Manages user permissions and roles."""

    def __init__(self, bot: commands.Bot) -> None:
        """Initialize the cog and register its command groups.

        Args:
            bot: The bot instance.
        """
        self.bot = bot
        for group in (
            ChannelManagement(),
            UserManagementGroup(),
            ChannelPermissionsManager(),
        ):
            self.bot.tree.add_command(group)


@deprecated("Load the cog using `bot.add_cog()` instead.")
async def setup(bot: commands.Bot) -> None:
    """Sets up the user management cog.

    Args:
        bot (commands.Bot): The bot instance.
    """
    await bot.add_cog(UserManagement(bot))
