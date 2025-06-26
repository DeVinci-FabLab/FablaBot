"""User management commands and permission utilities."""

from __future__ import annotations

import asyncio
from datetime import datetime
import logging
import re
from warnings import deprecated

import discord
from discord import app_commands
from discord.ext import commands

logger = logging.getLogger(__name__)

CURRENT_TIME = datetime.now().strftime("%Y/%m/%d, %H:%M:%S")


def can_assign_role(member: discord.Member, target_role: discord.Role):
    """Checks if a member can assign a specific role.

    Args:
        member (discord.Member): The member attempting to assign the role.
        target_role (discord.Role): The role to be assigned.

    Returns:
        bool: True if the member can assign the role, False otherwise.
    """
    if target_role.name == "Administrateur":
        return False

    return (
        _is_user_server_admin(member)
        or _is_user_responsible_for_pole(member, target_role)
        or _is_user_responsible_for_trainers(member, target_role)
    )


def _is_user_server_admin(member: discord.Member) -> bool:
    """Checks if a member is a server admin.

    Args:
        member (discord.Member): The member to check.

    Returns:
        bool: `True` if the member is a server admin, `False` otherwise.
    """
    member_role_names = [role.name for role in member.roles]
    return (
        "Admin -temp-" in member_role_names
        or "Administrateur" in member_role_names
        or "Président.e" in member_role_names
        or "Vice-Président.e" in member_role_names
        or "Secrétaire Général" in member_role_names
    )


def _is_user_responsible_for_pole(member: discord.Member, target_role: discord.Role) -> bool:
    """Checks if a member is responsible for a specific pole.

    Args:
        member (discord.Member): The member to check.
        target_role (discord.Role): The role to check against.

    Returns:
        bool: `True` if the member is responsible for the pole, `False` otherwise.
    """
    member_role_names = [role.name for role in member.roles]

    if target_role.name == "Sbire Bureau" and "Bureau" in member_role_names:
        return True

    if target_role.name.startswith("Pôle "):
        suffix = target_role.name[len("Pôle ") :]
        if f"Respo {suffix}" in member_role_names:
            return True

    return False


def _is_user_responsible_for_trainers(member: discord.Member, target_role: discord.Role) -> bool:
    """Checks if a member is responsible for a specific formation.

    Args:
        member (discord.Member): The member to check.
        target_role (discord.Role): The role to check against.

    Returns:
        bool: `True` if the member is responsible for the formation, `False` otherwise.
    """
    member_role_names = [role.name for role in member.roles]

    return "Respo Formations" in member_role_names and target_role.name.startswith("F - ")


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
    @app_commands.describe(
        user="L'utilisateur à qui donner les droits administrateurs",
        raison="La raison pour laquelle les droits sont donnés",
        time="Durée en minutes pour laquelle les droits sont donnés (par défaut 5)",
    )
    async def op(self, interaction: discord.Interaction, user: discord.Member, raison: str, time: int = 5) -> None:
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
        codir_role = discord.utils.get(interaction.guild.roles, name="CoDir")
        assert isinstance(codir_role, discord.Role)
        if admin_role is None:
            raise RuntimeError("Temporary admin role not found.")

        assert isinstance(interaction.user, discord.Member)
        if not can_assign_role(interaction.user, admin_role) and "Respo Numérique" not in [role.name for role in user.roles]:
            await interaction.response.send_message("Vous n'avez pas la permission d'ajouter ce rôle.", ephemeral=True)
            return
        await user.add_roles(admin_role)
        await interaction.response.send_message(
            f"{codir_role.mention} Les droits administrateurs on été donnés à {user} ! \nRaison: {raison} \nTemps: {time} minutes"
        )
        await asyncio.sleep(time * 60)  # Wait for the specified time in minutes
        await user.remove_roles(admin_role)

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
        if not can_assign_role(interaction.user, admin_role) and "Respo Numérique" not in [role.name for role in user.roles]:
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
        assert isinstance(interaction.user, discord.Member)
        if not can_assign_role(interaction.user, role):
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
        assert isinstance(interaction.user, discord.Member)
        if not can_assign_role(interaction.user, role):
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
        assert isinstance(interaction.user, discord.Member)
        if not can_assign_role(interaction.user, role):
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
            added.append(member.name)
        await interaction.response.send_message(f"Le rôle {role} a été ajouté à {', '.join(added)} !")

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
        assert isinstance(interaction.user, discord.Member)
        if not can_assign_role(interaction.user, role):
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
            removed.append(member.name)
        await interaction.response.send_message(f"Le rôle {role} a été retiré de {', '.join(removed)} !")


class UserManagement(commands.Cog):
    """Manages user permissions and roles."""

    def __init__(self, bot: commands.Bot) -> None:
        """Initialize the cog and register its command groups.

        Args:
            bot: The bot instance.
        """
        self.bot = bot
        for group in (UserManagementGroup(),):
            self.bot.tree.add_command(group)


@deprecated("Load the cog using `bot.add_cog()` instead.")
async def setup(bot: commands.Bot) -> None:
    """Sets up the user management cog.

    Args:
        bot (commands.Bot): The bot instance.
    """
    await bot.add_cog(UserManagement(bot))
