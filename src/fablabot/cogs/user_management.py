"""User management commands and permission utilities for Discord Bot. Provides slash commands for temporary admin and role assignments."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any
from warnings import deprecated

from discord import Guild, Interaction, Member, Role, TextChannel, app_commands
from discord.ext import commands
from discord.utils import get

logger = logging.getLogger(__name__)

COMMANDS_CHANNEL_NAME = "commandes_bot"

ADMIN_ROLES = {
    "Admin -temp-",
    "Administrateur",
    "Président.e",
    "Vice-Président.e",
    "Secrétaire Général",
}


def log_request(command_name: str, interaction: Interaction, **kwargs: Any) -> None:
    """Logs a request made to a command.

    Args:
        command_name (str): The name of the command.
        interaction (Interaction): The interaction object representing the command invocation.
        **kwargs (Any): Additional details to log.
    """
    details = " ".join(f"{k}={v}" for k, v in kwargs.items())
    logger.info(f"[{command_name}] user={interaction.user} id={interaction.user.id} {details}")


async def is_in_allowed_channel(interaction: Interaction) -> bool:
    assert isinstance(interaction.guild, Guild)
    assert isinstance(interaction.channel, TextChannel)
    commands_channel = get(interaction.guild.channels, name=COMMANDS_CHANNEL_NAME)
    assert isinstance(commands_channel, TextChannel)
    if interaction.channel != commands_channel:
        logger.warning(
            f"Attempt to use command in a different channel than {COMMANDS_CHANNEL_NAME}: {interaction.channel.name}"
        )
        await interaction.response.send_message(
            f"Vous ne pouvez pas utiliser de commandes en dehors du salon {commands_channel.mention}.",
            ephemeral=True,
        )
        return False
    return True


def can_assign_role(member: Member, target_role: Role) -> bool:
    """Checks if the member can assign a specific role.

    Args:
        member (Member): The member attempting to assign the role.
        target_role (Role): The role to be assigned.

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


def _is_user_server_admin(member: Member) -> bool:
    """Checks if the member has an administrative role.

    Args:
        member (Member): The member to check.

    Returns:
        bool: `True` if the member is a server admin, `False` otherwise.
    """
    member_role_names = {role.name for role in member.roles}
    return bool(member_role_names & ADMIN_ROLES)


def _is_user_responsible_for_pole(member: Member, target_role: Role) -> bool:
    """Checks if member is responsible for the 'pole' of the target role.

    Args:
        member (Member): The member to check.
        target_role (Role): The role to check against.

    Returns:
        bool: `True` if the member is responsible for the pole, `False` otherwise.
    """
    role_names = {role.name for role in member.roles}
    if target_role.name == "Sbire Bureau" and "Bureau" in role_names:
        return True
    if target_role.name.startswith("Pôle "):
        suffix = target_role.name.split("Pôle ", 1)[1]
        if f"Respo {suffix}" in role_names:
            return True
    return False


def _is_user_responsible_for_trainers(member: Member, target_role: Role) -> bool:
    """Checks if member manages formations for the target role.

    Args:
        member (Member): The member to check.
        target_role (Role): The role to check against.

    Returns:
        bool: `True` if the member is responsible for the formation, `False` otherwise.
    """
    role_names = {role.name for role in member.roles}
    return "Respo Formations" in role_names and target_role.name.startswith("F - ")


class UserManagementGroup(app_commands.Group, name="user", description="Gestion des utilisateurs"):
    """Manages user-related commands."""

    def __init__(self) -> None:
        """Initialize the UserManagementGroup with the specified name and description."""
        super().__init__(name="user", description="Gestion des utilisateurs")
        self.deop_tasks: dict[int, asyncio.Task[None]] = {}
        logger.info("UserManagementGroup initialized")

    @app_commands.command(name="op", description="Donne des droits admin temporaires à un utilisateur.")
    @app_commands.describe(user="L'utilisateur cible", reason="Raison de l'attribution", time="Durée en minutes (par défaut 5)")
    async def op(self, interaction: Interaction, user: Member, reason: str, time: int = 5) -> None:
        """Grant temporary admin privileges to a user.

        Args:
            interaction (Interaction): The interaction object.
            user (Member): The user to give privileges to.
            reason (str): The reason for granting privileges.
            time (int, optional): The duration in minutes for which privileges are granted. Defaults to 5.
        """
        log_request("user.op", interaction, target=user, reason=reason, duration=time)
        if not await is_in_allowed_channel(interaction):
            return

        assert isinstance(interaction.guild, Guild)
        admin_role = get(interaction.guild.roles, name="Admin -temp-")
        codir_role = get(interaction.guild.roles, name="CoDir")
        if admin_role is None or codir_role is None:
            logger.error("Required role not found: Admin -temp- or CoDir")
            await interaction.response.send_message("Rôles administratifs manquants sur le serveur.", ephemeral=True)
            return
        assert isinstance(interaction.user, Member)
        if not can_assign_role(interaction.user, admin_role) and "Respo Numérique" not in (
            r.name for r in interaction.user.roles
        ):
            logger.warning(f"Unauthorized op attempt by {interaction.user}")
            await interaction.response.send_message("Permissions insuffisantes.", ephemeral=True)
            return
        await user.add_roles(admin_role)
        old_task = self.deop_tasks.pop(user.id, None)
        if old_task:
            old_task.cancel()
        task = asyncio.create_task(self._schedule_deop(user, time, admin_role, interaction))
        self.deop_tasks[user.id] = task
        logger.info(f"Granted {user} temporary admin for {time} minutes for reason: {reason}")
        await interaction.response.send_message(
            f"{codir_role.mention} Droits admin donnés à {user.mention} pour {time} minutes. Raison: {reason}"
        )

    @app_commands.command(name="deop", description="Retire les droits admin temporaires d'un utilisateur.")
    @app_commands.describe(user="L'utilisateur cible")
    async def deop(self, interaction: Interaction, user: Member) -> None:
        """Revoke temporary admin privileges from a user.

        Args:
            interaction (Interaction): The interaction object.
            user (Member): The user to remove privileges from.

        """
        log_request("user.deop", interaction, target=user)
        if not await is_in_allowed_channel(interaction):
            return

        assert isinstance(interaction.guild, Guild)
        admin_role = get(interaction.guild.roles, name="Admin -temp-")
        if admin_role is None:
            logger.error("Role Admin -temp- not found")
            await interaction.response.send_message("Rôle temporaire admin introuvable.", ephemeral=True)
            return
        assert isinstance(interaction.user, Member)
        if not can_assign_role(interaction.user, admin_role) and "Respo Numérique" not in (
            r.name for r in interaction.user.roles
        ):
            logger.warning(f"Unauthorized deop attempt by {interaction.user}")
            await interaction.response.send_message("Permissions insuffisantes.", ephemeral=True)
            return
        task = self.deop_tasks.pop(user.id, None)
        if task:
            task.cancel()
        await user.remove_roles(admin_role)
        logger.info(f"Revoked temporary admin from {user}")
        await interaction.response.send_message(f"Droits admin retirés de {user.mention} !")

    @app_commands.command(name="add_role", description="Donne un rôle à un utilisateur.")
    @app_commands.describe(user="L'utilisateur cible", role="Le rôle à attribuer")
    async def add_role(self, interaction: Interaction, user: Member, role: Role) -> None:
        """Add a role to a single user.

        Args:
            interaction (Interaction): The interaction object.
            user (Member): The user to add the role to.
            role (Role): The role to add to the user.
        """
        log_request("user.add_role", interaction, target=user, role=role)
        if not await is_in_allowed_channel(interaction):
            return

        assert isinstance(interaction.user, Member)
        if not can_assign_role(interaction.user, role):
            logger.warning(f"Unauthorized add_role by {interaction.user}")
            await interaction.response.send_message("Vous n'avez pas la permission d'ajouter ce rôle.", ephemeral=True)
            return
        await user.add_roles(role)
        logger.info(f"Added role {role} to {user}")
        await interaction.response.send_message(f"Le rôle {role.name!r} a été ajouté à {user.mention}.")

    @app_commands.command(name="remove_role", description="Retire un rôle à un utilisateur.")
    @app_commands.describe(user="L'utilisateur cible", role="Le rôle à retirer")
    async def remove_role(self, interaction: Interaction, user: Member, role: Role) -> None:
        """Remove a role from a single user.

        Args:
            interaction (Interaction): The interaction object.
            user (Member): The user to remove the role from.
            role (Role): The role to remove from the user.
        """
        log_request("user.remove_role", interaction, target=user, role=role)
        if not await is_in_allowed_channel(interaction):
            return

        assert isinstance(interaction.user, Member)
        if not can_assign_role(interaction.user, role):
            logger.warning(f"Unauthorized remove_role by {interaction.user}")
            await interaction.response.send_message("Vous n'avez pas la permission de retirer ce rôle.", ephemeral=True)
            return
        await user.remove_roles(role)
        logger.info(f"Removed role {role} from {user}")
        await interaction.response.send_message(f"Le rôle {role.name!r} a été retiré à {user.name!r}.")

    @app_commands.command(name="add_roles", description="Donne un rôle à plusieurs utilisateurs.")
    @app_commands.describe(users="Les utilisateurs cibles (mentions à la suite)", role="Le rôle à attribuer")
    async def add_roles(self, interaction: Interaction, users: str, role: Role) -> None:
        """Adds a role to multiple users from mentions.

        Args:
            interaction (Interaction): The interaction object.
            users (str): The users to add the role to, specified as mentions separated by spaces.
            role (Role): The role to add to the users.
        """
        log_request("user.add_roles", interaction, mentions=users, role=role)
        if not await is_in_allowed_channel(interaction):
            return

        assert isinstance(interaction.guild, Guild)
        assert isinstance(interaction.user, Member)
        if not can_assign_role(interaction.user, role):
            logger.warning(f"Unauthorized add_roles by {interaction.user}")
            await interaction.response.send_message("Vous n'avez pas la permission d'ajouter ce rôle.", ephemeral=True)
            return
        ids = re.findall(r"<@!?(\d+)>", users)
        members: list[Member] = []
        for uid in ids:
            member = interaction.guild.get_member(int(uid))
            if member:
                members.append(member)
            else:
                logger.warning(f"User with ID {uid} not found in guild {interaction.guild.name}")
        added: list[Member] = []
        already_has_role: list[Member] = []
        for m in members:
            if role in m.roles:
                logger.warning(f"User {m} already has role {role}")
                already_has_role.append(m)
                continue
            await m.add_roles(role)
            added.append(m)
        logger.debug(f"Members to add role: {members},\nAdded: {added},\nAlready has role: {already_has_role}")
        if not members or not added:
            logger.info("No valid users found for role addition")
            await interaction.response.send_message("Aucun utilisateur valide trouvé.", ephemeral=True)
            return
        logger.info(f"Added role {role} to multiple users: {added}")
        await interaction.response.send_message(f"Rôle {role.name!r} ajouté à {', '.join(m.mention for m in added)}.")
        if already_has_role:
            await interaction.followup.send(
                f"Rôle {role.mention} déjà attribué à {', '.join(m.mention for m in already_has_role)}.", ephemeral=True
            )

    @app_commands.command(name="remove_roles", description="Retire un rôle à plusieurs utilisateurs.")
    @app_commands.describe(users="Les utilisateurs cibles (mentions à la suite)", role="Le rôle à retirer")
    async def remove_roles(self, interaction: Interaction, users: str, role: Role) -> None:
        """Removes a role from multiple users from mentions.

        Args:
            interaction (Interaction): The interaction object.
            users (str): The users to remove the role from, specified as mentions separated by spaces.
            role (Role): The role to remove from the users.
        """
        log_request("user.remove_roles", interaction, mentions=users, role=role)
        if not await is_in_allowed_channel(interaction):
            return

        assert isinstance(interaction.guild, Guild)
        assert isinstance(interaction.user, Member)
        if not can_assign_role(interaction.user, role):
            logger.warning(f"Unauthorized remove_roles by {interaction.user}")
            await interaction.response.send_message("Vous n'avez pas la permission de retirer ce rôle.", ephemeral=True)
            return
        ids = re.findall(r"<@!?(\d+)>", users)
        members: list[Member] = []
        for uid in ids:
            member = interaction.guild.get_member(int(uid))
            if member:
                members.append(member)
            else:
                logger.warning(f"User with ID {uid} not found in guild {interaction.guild.name}")
        removed: list[Member] = []
        users_without_role: list[Member] = []
        for m in members:
            if role not in m.roles:
                logger.warning(f"User {m} does not have role {role}")
                users_without_role.append(m)
                continue
            await m.remove_roles(role)
            removed.append(m)
        if not members or not removed:
            logger.info("No valid users found for role removal")
            await interaction.response.send_message("Aucun utilisateur valide trouvé.", ephemeral=True)
            return
        logger.info(f"Removed role {role} from multiple users: {removed}")
        await interaction.response.send_message(f"Rôle {role.name!r} retiré de {', '.join(m.mention for m in removed)}.")
        if users_without_role:
            await interaction.followup.send(
                f"Rôle {role.mention} déjà absent chez {', '.join(m.mention for m in users_without_role)}.", ephemeral=True
            )

    async def _schedule_deop(self, user: Member, time: int, admin_role: Role, interaction: Interaction) -> None:
        """Schedule removal of temporary admin role after timeout.

        Args:
            user (Member): The user to remove the role from.
            time (int): The time in minutes to wait before removing the role.
            admin_role (Role): The admin role to remove.
            interaction (Interaction): The interaction that triggered the deop.
        """
        logger.debug(f"Scheduling deop for {user} after {time} minutes")
        try:
            await asyncio.sleep(time * 60)
            if user.id in self.deop_tasks:
                await user.remove_roles(admin_role)
                logger.info(f"Revoked temporary admin from {user} after {time} minutes")
                await interaction.followup.send(f"Droits admin retirés de {user.mention} après {time} minutes.")
            self.deop_tasks.pop(user.id, None)
        except asyncio.CancelledError:
            logger.info(f"Deop timer cancelled for {user}")


class UserManagement(commands.Cog):
    """Cog to register user management commands."""

    def __init__(self, bot: commands.Bot) -> None:
        """Initialize the cog and register its command groups.

        Args:
            bot (commands.Bot): The bot instance.
        """
        self.bot = bot
        self.bot.tree.add_command(UserManagementGroup())


@deprecated("Load the cog using `bot.add_cog()` instead.")
async def setup(bot: commands.Bot) -> None:
    """Setup function to load the UserManagement cog.

    Args:
        bot (commands.Bot): The bot instance.
    """
    await bot.add_cog(UserManagement(bot))
