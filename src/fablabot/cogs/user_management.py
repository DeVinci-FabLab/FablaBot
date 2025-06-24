"""Channel management commands and permission utilities."""

from __future__ import annotations

from datetime import datetime
import logging
import re
from warnings import deprecated

import discord
from discord import app_commands
from discord.ext import commands

from fablabot.role_loader import can_assign_role, load_roles

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


class ChannelManagement(app_commands.Group, name="channel", description="Gestion des salons"):
    """Manages channel-related commands.

    Args:
        app_commands (app_commands.Group): The app_commands group.
        name (str, optional): The name of the group. Defaults to "channel".
        description (str, optional): The description of the group. Defaults to "Gestion des salons".
    """

    @app_commands.command(name="clear", description="Nettoie le salon actuel de ses derniers 100 messages.")
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

    @app_commands.command(name="create", description="Crée un nouveau salon dans la catégorie spécifiée.")
    @app_commands.describe(
        channel="Le nom du salon à créer",
        category="La catégorie dans laquelle créer le salon",
    )
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

    @app_commands.command(name="op", description="Donne les droits administrateurs temporaires à un utilisateur.")
    @app_commands.describe(user="L'utilisateur à qui donner les droits administrateurs")
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

        assert isinstance(interaction.user, discord.Member)
        if not can_assign_role(interaction.user, admin_role, load_roles()):
            await interaction.response.send_message("Vous n'avez pas la permission d'ajouter ce rôle.", ephemeral=True)
            return
        await user.add_roles(admin_role)
        await interaction.response.send_message(f"Les droits administrateurs on été donnés à {user} !")

    @app_commands.command(name="deop", description="Retire les droits administrateurs temporaires d'un utilisateur.")
    @app_commands.describe(user="L'utilisateur à qui retirer les droits administrateurs")
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
        assert isinstance(interaction.user, discord.Member)
        roles = load_roles()
        if not can_assign_role(interaction.user, admin_role, roles):
            await interaction.response.send_message("Vous n'avez pas la permission de retirer ce rôle.", ephemeral=True)
            return
        await user.remove_roles(admin_role)
        await interaction.response.send_message(f"Les droits administrateurs on été retirés à {user} !")

    @app_commands.command(name="add_role", description="Donne un rôle à un utilisateur.")
    @app_commands.describe(
        user="L'utilisateur à qui donner le rôle",
        role="Le rôle à donner à l'utilisateur",
    )
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
        roles = load_roles()
        assert isinstance(interaction.user, discord.Member)
        if not can_assign_role(interaction.user, role, roles):
            await interaction.response.send_message("Vous n'avez pas la permission d'ajouter ce rôle.", ephemeral=True)
            return
        await user.add_roles(role)
        await interaction.response.send_message(f"Le rôle {role} a été ajouté à {user} !")

    @app_commands.command(name="remove_role", description="Retire un rôle à un utilisateur.")
    @app_commands.describe(
        user="L'utilisateur à qui retirer le rôle",
        role="Le rôle à retirer à l'utilisateur",
    )
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
        roles = load_roles()
        assert isinstance(interaction.user, discord.Member)
        if not can_assign_role(interaction.user, role, roles):
            await interaction.response.send_message("Vous n'avez pas la permission de retirer ce rôle.", ephemeral=True)
            return
        await user.remove_roles(role)
        await interaction.response.send_message(f"Le rôle {role} a été retiré à {user} !")

    @app_commands.command(name="add_roles", description="Donne un rôle à plusieurs utilisateurs.")
    @app_commands.describe(
        users="Les utilisateurs cibles (mentions séparées par espace)",
        role="Le rôle à donner aux utilisateurs",
    )
    async def add_roles(self, interaction: discord.Interaction, users: str, role: discord.Role) -> None:
        """Adds a role to multiple users from mentions."""
        logger.info(
            "%s user_roles_add %s:%s %s %s",
            CURRENT_TIME,
            interaction.user.name,
            interaction.user.id,
            users,
            role,
        )
        assert interaction.guild is not None
        roles = load_roles()
        assert isinstance(interaction.user, discord.Member)
        if not can_assign_role(interaction.user, role, roles):
            await interaction.response.send_message("Vous n'avez pas la permission d'ajouter ce rôle.", ephemeral=True)
            return
        ids = re.findall(r"<@!?(\d+)>", users)
        members: list[discord.Member] = []
        for uid in ids:
            member = interaction.guild.get_member(int(uid))
            if member:
                members.append(member)
        if not members:
            await interaction.response.send_message("Aucun utilisateur valide trouvé dans la liste.", ephemeral=True)
            return
        added: list[str] = []
        for member in members:
            await member.add_roles(role)
            added.append(member.mention)
        await interaction.response.send_message(f"Le rôle {role.mention} a été ajouté à {', '.join(added)} !")

    @app_commands.command(name="remove_roles", description="Retire un rôle à plusieurs utilisateurs.")
    @app_commands.describe(
        users="Les utilisateurs cibles (mentions séparées par espace)",
        role="Le rôle à retirer aux utilisateurs",
    )
    async def remove_roles(self, interaction: discord.Interaction, users: str, role: discord.Role) -> None:
        """Removes a role from multiple users from mentions."""
        logger.info(
            "%s user_roles_remove %s:%s %s %s",
            CURRENT_TIME,
            interaction.user.name,
            interaction.user.id,
            users,
            role,
        )
        assert interaction.guild is not None
        roles = load_roles()
        assert isinstance(interaction.user, discord.Member)
        if not can_assign_role(interaction.user, role, roles):
            await interaction.response.send_message("Vous n'avez pas la permission de retirer ce rôle.", ephemeral=True)
            return
        ids = re.findall(r"<@!?(\d+)>", users)
        members: list[discord.Member] = []
        for uid in ids:
            member = interaction.guild.get_member(int(uid))
            if member:
                members.append(member)
        if not members:
            await interaction.response.send_message("Aucun utilisateur valide trouvé dans la liste.", ephemeral=True)
            return
        removed: list[str] = []
        for member in members:
            await member.remove_roles(role)
            removed.append(member.mention)
        await interaction.response.send_message(f"Le rôle {role.mention} a été retiré de {', '.join(removed)} !")


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
        ):
            self.bot.tree.add_command(group)


@deprecated("Load the cog using `bot.add_cog()` instead.")
async def setup(bot: commands.Bot) -> None:
    """Sets up the user management cog.

    Args:
        bot (commands.Bot): The bot instance.
    """
    await bot.add_cog(UserManagement(bot))
