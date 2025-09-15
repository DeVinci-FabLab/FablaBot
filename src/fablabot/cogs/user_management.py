"""User management commands and permission utilities for Discord Bot. Provides slash commands for temporary admin and role assignments."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal
from warnings import deprecated

from discord import (
    ButtonStyle,
    Forbidden,
    HTTPException,
    Interaction,
    Member,
    Role,
    TextChannel,
    app_commands,
    ui,
)
from discord.ext import commands
from discord.utils import get

from .utils import is_in_allowed_channel, log_request

logger = logging.getLogger(__name__)

ADMIN_ROLES = {
    "Admin -temp-",
    "Administrateur",
    "Président.e",
    "Vice-Président.e",
    "Secrétaire Général",
}


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
    @app_commands.describe(
        user="L'utilisateur cible",
        reason="Raison de l'attribution",
        time="Durée en minutes (par défaut 5)",
    )
    async def op(self, interaction: Interaction, user: Member, reason: str, time: int = 5) -> None:
        """Grant temporary admin privileges to a user.

        Args:
            interaction (Interaction): The interaction object.
            user (Member): The user to give privileges to.
            reason (str): The reason for granting privileges.
            time (int, optional): The duration in minutes for which privileges are granted. Defaults to 5.
        """
        log_request(logger, "user.op", interaction, target=user, reason=reason, duration=time)
        if not await is_in_allowed_channel(logger, interaction):
            return

        assert interaction.guild is not None
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

        try:
            await user.add_roles(admin_role, reason=f"Add with op command by {interaction.user} for {reason}")
        except Forbidden:
            logger.error(f"Forbidden to add role {admin_role} to {user}")
            await interaction.response.send_message("Impossible d'ajouter le rôle.", ephemeral=True)
            return
        except HTTPException as e:
            logger.error(f"Failed to add role {admin_role} to {user}: {e}")
            await interaction.response.send_message("Une erreur est survenue lors de l'ajout du rôle.", ephemeral=True)
            return

        old_task = self.deop_tasks.pop(user.id, None)
        if old_task:
            old_task.cancel()
        task = asyncio.create_task(self._schedule_deop(interaction, user, time, admin_role, codir_role))
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
        log_request(logger, "user.deop", interaction, target=user)
        if not await is_in_allowed_channel(logger, interaction):
            return

        assert interaction.guild is not None
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

        try:
            await user.remove_roles(admin_role, reason=f"Remove with op command by {interaction.user}")
        except Forbidden:
            logger.error(f"Forbidden to remove role {admin_role} from {user}")
            await interaction.response.send_message("Impossible de retirer le rôle.", ephemeral=True)
            return
        except HTTPException as e:
            logger.error(f"Failed to remove role {admin_role} from {user}: {e}")
            await interaction.response.send_message("Une erreur est survenue lors du retrait du rôle.", ephemeral=True)
            return

        task = self.deop_tasks.pop(user.id, None)
        if task:
            task.cancel()

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
        log_request(logger, "user.add_role", interaction, target=user, role=role)
        if not await is_in_allowed_channel(logger, interaction):
            return

        assert isinstance(interaction.user, Member)
        if not can_assign_role(interaction.user, role):
            logger.warning(f"Unauthorized add_role by {interaction.user}")
            await interaction.response.send_message("Vous n'avez pas la permission d'ajouter ce rôle.", ephemeral=True)
            return

        try:
            await user.add_roles(role, reason=f"Add with add_role command by {interaction.user}")
        except Forbidden:
            logger.error(f"Forbidden to add role {role} to {user}")
            await interaction.response.send_message("Impossible d'ajouter le rôle.", ephemeral=True)
            return
        except HTTPException as e:
            logger.error(f"Failed to add role {role} to {user}: {e}")
            await interaction.response.send_message("Une erreur est survenue lors de l'ajout du rôle.", ephemeral=True)
            return

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
        log_request(logger, "user.remove_role", interaction, target=user, role=role)
        if not await is_in_allowed_channel(logger, interaction):
            return

        assert isinstance(interaction.user, Member)
        if not can_assign_role(interaction.user, role):
            logger.warning(f"Unauthorized remove_role by {interaction.user}")
            await interaction.response.send_message("Vous n'avez pas la permission de retirer ce rôle.", ephemeral=True)
            return

        try:
            await user.remove_roles(role, reason=f"Remove with remove_role command by {interaction.user}")
        except Forbidden:
            logger.error(f"Forbidden to remove role {role} from {user}")
            await interaction.response.send_message("Impossible de retirer le rôle.", ephemeral=True)
            return
        except HTTPException as e:
            logger.error(f"Failed to remove role {role} from {user}: {e}")
            await interaction.response.send_message("Une erreur est survenue lors du retrait du rôle.", ephemeral=True)
            return

        logger.info(f"Removed role {role} from {user}")
        await interaction.response.send_message(f"Le rôle {role.name!r} a été retiré à {user.name!r}.")

    @app_commands.command(
        name="add_roles",
        description="Donne un rôle à plusieurs utilisateurs via un sélecteur.",
    )
    @app_commands.describe(role="Le rôle à attribuer")
    async def add_roles(self, interaction: Interaction, role: Role) -> None:
        """Open a multi-user selector to add a role in bulk.

        Args:
            interaction (Interaction): The interaction object.
            role (Role): The role to add to the users.
        """
        log_request(logger, "user.add_roles", interaction, role=role)
        if not await is_in_allowed_channel(logger, interaction):
            return

        assert isinstance(interaction.user, Member)
        if not can_assign_role(interaction.user, role):
            logger.warning(f"Unauthorized add_roles by {interaction.user}")
            await interaction.response.send_message("Vous n'avez pas la permission d'ajouter ce rôle.", ephemeral=True)
            return

        view = BulkRoleView(role, interaction.user, action="add")
        await interaction.response.send_message(
            f"Sélectionnez les membres à qui ajouter {role.mention} puis cliquez sur **Confirmer**.", view=view
        )

    @app_commands.command(name="remove_roles", description="Retire un rôle à plusieurs utilisateurs via un sélecteur.")
    @app_commands.describe(role="Le rôle à retirer")
    async def remove_roles(self, interaction: Interaction, role: Role) -> None:
        """Open a multi-user selector to remove a role in bulk.

        Args:
            interaction (Interaction): The interaction object.
            role (Role): The role to remove from the users.
        """
        log_request(logger, "user.remove_roles", interaction, role=role)
        if not await is_in_allowed_channel(logger, interaction):
            return

        assert isinstance(interaction.user, Member)
        if not can_assign_role(interaction.user, role):
            logger.warning(f"Unauthorized remove_roles by {interaction.user}")
            await interaction.response.send_message("Vous n'avez pas la permission de retirer ce rôle.", ephemeral=True)
            return

        view = BulkRoleView(role, interaction.user, action="remove")
        await interaction.response.send_message(
            f"Sélectionnez les membres à qui retirer {role.mention} puis cliquez sur **Confirmer**.", view=view
        )

    async def _schedule_deop(
        self, interaction: Interaction, user: Member, time: int, admin_role: Role, codir_role: Role
    ) -> None:
        """Schedule removal of temporary admin role after timeout.

        Args:
            interaction (Interaction): The interaction that triggered the deop.
            user (Member): The user to remove the role from.
            time (int): The time in minutes to wait before removing the role.
            admin_role (Role): The admin role to remove.
            codir_role (Role): The CoDir role to prevent if there is an error.
        """
        logger.debug(f"Scheduling deop for {user} after {time} minutes")
        try:
            await asyncio.sleep(time * 60)
            if user.id in self.deop_tasks:
                try:
                    await user.remove_roles(admin_role, reason="Remove op after time")
                except Forbidden:
                    logger.error(f"Forbidden to remove admin role from {user}")
                    await interaction.followup.send(
                        f"{codir_role.mention} Je ne peux pas retirer le rôle admin de {user.mention}."
                    )
                    return
                except HTTPException as e:
                    logger.error(f"HTTP error while removing admin role from {user}: {e}")
                    await interaction.followup.send(
                        f"{codir_role.mention} Erreur HTTP lors de la suppression du rôle admin de {user.mention}."
                    )
                    return

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


class BulkRoleView(ui.View):
    """View for bulk role assignment/removal."""

    def __init__(
        self,
        role: Role,
        user: Member,
        *,
        action: Literal["add", "remove"],
        timeout: float = 180.0,
    ) -> None:
        """View for bulk role assignment/removal.

        Args:
            role (Role): The role to assign.
            user (Member): The member initiating the role assignment.
            action (Literal["add", "remove"]): "add" to add the role, "remove" to remove it.
            timeout (float, optional): The timeout duration in seconds. Defaults to 180.0.
        """
        super().__init__(timeout=timeout)
        self.role = role
        self.user = user
        self.action = action

        async def _on_select(interaction: Interaction) -> None:
            if not interaction.response.is_done():
                await interaction.response.defer()

        self.select: ui.UserSelect[Any] = ui.UserSelect(
            placeholder="Sélectionne les membres…",
            min_values=1,
            max_values=25,
        )
        self.select.callback = _on_select

        self.confirm_button = ui.Button(label="Confirmer", style=ButtonStyle.primary)
        self.confirm_button.callback = self.confirm

        self.add_item(self.select)
        self.add_item(self.confirm_button)

    async def confirm(self, interaction: Interaction) -> None:
        """Confirm the bulk role assignment/removal.

        Args:
            interaction (Interaction): The interaction instance.
        """
        members: list[Member] = [m for m in self.select.values if isinstance(m, Member)]
        if not members:
            await interaction.response.send_message("Aucun membre sélectionné.", ephemeral=True)
            return

        channel = interaction.channel
        assert isinstance(channel, TextChannel)

        modified: list[Member] = []
        already: list[Member] = []
        failed: list[Member] = []

        if self.action == "add":
            for m in members:
                if self.role in m.roles:
                    logger.info(f"User {m} already has role {self.role}")
                    already.append(m)
                    continue

                try:
                    await m.add_roles(self.role, reason=f"Bulk add by {self.user}")
                    modified.append(m)
                except Forbidden:
                    failed.append(m)
                except HTTPException as e:
                    logger.error(f"HTTP error while adding {self.role} to {m}: {e}")
                    failed.append(m)

        elif self.action == "remove":
            for m in members:
                if self.role not in m.roles:
                    logger.info(f"User {m} does not have role {self.role}")
                    already.append(m)
                    continue
                try:
                    await m.remove_roles(self.role, reason=f"Bulk remove by {self.user}")
                    modified.append(m)
                except Forbidden:
                    failed.append(m)
                except HTTPException as e:
                    logger.error(f"HTTP error while removing {self.role} from {m}: {e}")
                    failed.append(m)

        logger.info(
            f"Members to {self.action} role {self.role}: {members},\nSuccess:{modified},\nAlready: {already},\nFailed: {failed}",
        )

        lines: list[str] = [f"Rôle {self.role.name!r} :"]
        if modified:
            if self.action == "add":
                lines.append(f"Ajouté avec succès : {', '.join(m.mention for m in modified)}")
            else:
                lines.append(f"Retiré avec succès : {', '.join(m.mention for m in modified)}")
        if already:
            if self.action == "add":
                lines.append(f"Déjà présent chez : {', '.join(m.mention for m in already)}")
            else:
                lines.append(f"Déjà absent chez : {', '.join(m.mention for m in already)}")
        if failed:
            lines.append(f"Échec : {', '.join(m.mention for m in failed)}")

        for child in self.children:
            if isinstance(child, ui.Button | ui.UserSelect):
                child.disabled = True
        await interaction.response.edit_message(view=self)

        await interaction.edit_original_response(content="\n".join(lines), view=None)
