"""User management commands and permission utilities for Discord Bot.

Provides slash commands for temporary admin and role assignments.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal
from warnings import deprecated

from discord import (
    ButtonStyle,
    Interaction,
    Member,
    Role,
    User,
    app_commands,
    ui,
)
from discord.ext import commands
from discord.utils import get

from fablabot.cogs.helpers import (
    ADMIN_ROLES,
    ErrorMessages,
    RoleNames,
    escape_md,
    format_member_mention,
    format_role_mention,
    is_in_allowed_channel,
    log_request,
    safe_add_roles,
    safe_remove_roles,
)
from fablabot.cogs.helpers.utils import get_members_by_role

logger = logging.getLogger(__name__)


class UserManagement(commands.Cog):
    """Cog to register user management commands.

    Commands:
        - /user help: Display help for user management commands.
        - /user op: Grant temporary admin privileges to a user.
        - /user deop: Revoke temporary admin privileges from a user.
        - /user add_role: Add a role to a single user.
        - /user remove_role: Remove a role from a single user.
        - /user add_roles: Add a role to multiple users via a selector.
        - /user remove_roles: Remove a role from multiple users via a selector.
        - /user with_roles: Get members with specific roles.

    Attributes:
        user_group (app_commands.Group): Command group for user management commands.
    """

    def __init__(self, bot: commands.Bot) -> None:
        """Initialize the cog and register its command groups.

        Args:
            bot (commands.Bot): The bot instance.
        """
        self.bot = bot
        self.deop_tasks: dict[int, asyncio.Task[None]] = {}
        logger.info("UserManagement initialized")

    # region ====== User Slash Commands Group ======
    # -- Help & Admin Access --

    user_group = app_commands.Group(name="user", description="Gestion des utilisateurs")

    @user_group.command(name="help", description="Affiche l'aide pour les commandes de gestion des utilisateurs.")
    @app_commands.describe(show="Afficher l'aide publiquement ou non")
    async def user_help(self, interaction: Interaction, show: bool = False) -> None:
        """Display help for user management commands.

        Args:
            interaction (Interaction): The Discord interaction context.
            show (bool): Whether to show the help publicly or not.
        """
        help_message = (
            "**Commandes de gestion des utilisateurs :**\n"
            "- `/user op <user>` : Donne des droits admin temporaires à un utilisateur.\n"
            "- `/user deop <user>` : Retire les droits admin temporaires d'un utilisateur.\n"
            "- `/user add_role <user> <role>` : Donne un rôle à un utilisateur.\n"
            "- `/user remove_role <user> <role>` : Retire un rôle à un utilisateur.\n"
            "- `/user add_roles <role>` : Donne un rôle à plusieurs utilisateurs via un sélecteur.\n"
            "- `/user remove_roles <role>` : Retire un rôle à plusieurs utilisateurs via un sélecteur.\n"
            "- `/user with_roles` : Obtenir les membres avec des rôles spécifiques.\n"
            "- `/user help [show]` : Affiche cette aide. Par défaut, elle est affichée secrètement.\n"
            "\n"
            "Assurez-vous d'avoir les permissions nécessaires pour utiliser ces commandes."
        )
        await interaction.response.send_message(help_message, ephemeral=not show)

    @user_group.command(name="op", description="Donne des droits admin temporaires à un utilisateur.")
    @app_commands.describe(
        member="L'utilisateur cible",
        reason="Raison de l'attribution",
        time="Durée en minutes (par défaut 5)",
    )
    async def user_op(
        self,
        interaction: Interaction,
        member: Member,
        reason: str,
        time: app_commands.Range[int, 1, 90] = 5,
    ) -> None:
        """Grant temporary admin privileges to a user.

        Args:
            interaction (Interaction): The Discord interaction context.
            member (Member): The user to give privileges to.
            reason (str): The reason for granting privileges.
            time (app_commands.Range[int, 1, 90], optional):
                The duration in minutes for which privileges are granted. Defaults to 5.
        """
        log_request(logger, "user.op", interaction, target=member, reason=reason, duration=time)
        if not await is_in_allowed_channel(logger, interaction):
            return

        assert interaction.guild is not None
        admin_role = get(interaction.guild.roles, name=RoleNames.ADMIN_TEMP)
        codir_role = get(interaction.guild.roles, name=RoleNames.CODIR)
        if admin_role is None or codir_role is None:
            logger.error(f"Required role not found: {RoleNames.ADMIN_TEMP} or {RoleNames.CODIR}")
            await interaction.response.send_message("Rôles administratifs manquants sur le serveur.", ephemeral=True)
            return
        assert isinstance(interaction.user, Member)
        if not self._can_assign_role(interaction.user, admin_role) and RoleNames.DIGITAL_MANAGER not in (
            r.name for r in interaction.user.roles
        ):
            logger.warning(f"Unauthorized op attempt by {interaction.user}")
            await interaction.response.send_message(ErrorMessages.INSUFFICIENT_PERMISSIONS, ephemeral=True)
            return

        success, error = await safe_add_roles(
            logger,
            member,
            admin_role,
            reason=f"Add with op command by {interaction.user} for {reason}",
        )
        if not success:
            await interaction.response.send_message(error, ephemeral=True)
            return

        old_task = self.deop_tasks.pop(member.id, None)
        if old_task:
            old_task.cancel()
        task = asyncio.create_task(self._schedule_deop(interaction, member, time, admin_role))
        self.deop_tasks[member.id] = task
        logger.info(f"Granted {member} temporary admin for {time} minutes for reason: {reason}")
        await interaction.response.send_message(
            f"{codir_role.mention} Droits admin donnés à {format_member_mention(member)} pour {time} minutes. "
            f"Raison: {escape_md(reason)}",
        )

    @user_group.command(name="deop", description="Retire les droits admin temporaires d'un utilisateur.")
    @app_commands.describe(member="L'utilisateur cible")
    async def user_deop(self, interaction: Interaction, member: Member) -> None:
        """Revoke temporary admin privileges from a user.

        Args:
            interaction (Interaction): The Discord interaction context.
            member (Member): The user to remove privileges from.

        """
        log_request(logger, "user.deop", interaction, target=member)
        if not await is_in_allowed_channel(logger, interaction):
            return

        assert interaction.guild is not None
        admin_role = get(interaction.guild.roles, name=RoleNames.ADMIN_TEMP)
        if admin_role is None:
            logger.error(f"Role {RoleNames.ADMIN_TEMP} not found")
            await interaction.response.send_message(
                ErrorMessages.ROLE_NOT_FOUND.format(role_name=RoleNames.ADMIN_TEMP),
                ephemeral=True,
            )
            return
        assert isinstance(interaction.user, Member)
        if not self._can_assign_role(interaction.user, admin_role) and RoleNames.DIGITAL_MANAGER not in (
            r.name for r in interaction.user.roles
        ):
            logger.warning(f"Unauthorized deop attempt by {interaction.user}")
            await interaction.response.send_message(ErrorMessages.INSUFFICIENT_PERMISSIONS, ephemeral=True)
            return

        success, error = await safe_remove_roles(
            logger,
            member,
            admin_role,
            reason=f"Remove with op command by {interaction.user}",
        )
        if not success:
            await interaction.response.send_message(error, ephemeral=True)
            return

        task = self.deop_tasks.pop(member.id, None)
        if task:
            task.cancel()

        logger.info(f"Revoked temporary admin from {member}")
        await interaction.response.defer(thinking=True)
        await interaction.followup.send(content=f"Droits admin retirés de {format_member_mention(member)}")

    # -- Role Management --

    @user_group.command(name="add_role", description="Donne un rôle à un utilisateur.")
    @app_commands.describe(member="L'utilisateur cible", role="Le rôle à attribuer")
    async def user_add_role(self, interaction: Interaction, member: Member, role: Role) -> None:
        """Add a role to a single user.

        Args:
            interaction (Interaction): The Discord interaction context.
            member (Member): The user to add the role to.
            role (Role): The role to add to the user.
        """
        log_request(logger, "user.add_role", interaction, target=member, role=role)
        if not await is_in_allowed_channel(logger, interaction):
            return

        if not self._can_assign_role(interaction.user, role):
            logger.warning(f"Unauthorized add_role by {interaction.user}")
            await interaction.response.send_message(ErrorMessages.NO_PERMISSION_ADD_ROLE, ephemeral=True)
            return

        success, error = await safe_add_roles(logger, member, role, reason=f"Add with add_role command by {interaction.user}")
        if not success:
            await interaction.response.send_message(error, ephemeral=True)
            return

        logger.info(f"Added role {role} to {member}")
        await interaction.response.defer(thinking=True)
        await interaction.followup.send(
            content=f"Le rôle {format_role_mention(role)} a été ajouté à {format_member_mention(member)}.",
        )

    @user_group.command(name="remove_role", description="Retire un rôle à un utilisateur.")
    @app_commands.describe(member="L'utilisateur cible", role="Le rôle à retirer")
    async def user_remove_role(self, interaction: Interaction, member: Member, role: Role) -> None:
        """Remove a role from a single user.

        Args:
            interaction (Interaction): The Discord interaction context.
            member (Member): The user to remove the role from.
            role (Role): The role to remove from the user.
        """
        log_request(logger, "user.remove_role", interaction, target=member, role=role)
        if not await is_in_allowed_channel(logger, interaction):
            return

        if not self._can_assign_role(interaction.user, role):
            logger.warning(f"Unauthorized remove_role by {interaction.user}")
            await interaction.response.send_message(ErrorMessages.NO_PERMISSION_REMOVE_ROLE, ephemeral=True)
            return

        success, error = await safe_remove_roles(
            logger,
            member,
            role,
            reason=f"Remove with remove_role command by {interaction.user}",
        )
        if not success:
            await interaction.response.send_message(error, ephemeral=True)
            return

        logger.info(f"Removed role {role} from {member}")
        await interaction.response.defer(thinking=True)
        await interaction.followup.send(
            content=f"Le rôle {format_role_mention(role)} a été retiré à {format_member_mention(member)}.",
        )

    @user_group.command(
        name="add_roles",
        description="Donne un rôle à plusieurs utilisateurs via un sélecteur.",
    )
    @app_commands.describe(role="Le rôle à attribuer")
    async def user_add_roles(self, interaction: Interaction, role: Role) -> None:
        """Open a multi-user selector to add a role in bulk.

        Args:
            interaction (Interaction): The Discord interaction context.
            role (Role): The role to add to the users.
        """
        log_request(logger, "user.add_roles", interaction, role=role)
        if not await is_in_allowed_channel(logger, interaction):
            return

        assert isinstance(interaction.user, Member)
        if not self._can_assign_role(interaction.user, role):
            logger.warning(f"Unauthorized add_roles by {interaction.user}")
            await interaction.response.send_message(ErrorMessages.NO_PERMISSION_ADD_ROLE, ephemeral=True)
            return

        await interaction.response.defer(thinking=True)
        followup_mes = await interaction.followup.send("Sélection des membres en cours...", wait=True)

        view = BulkRoleAssignmentView(role, interaction.user, followup_mes.id, action="add")
        await interaction.followup.send(
            f"Sélectionnez les membres à qui ajouter {escape_md(role.name)} puis cliquez sur **Confirmer**.",
            view=view,
            ephemeral=True,
        )

    @user_group.command(name="remove_roles", description="Retire un rôle à plusieurs utilisateurs via un sélecteur.")
    @app_commands.describe(role="Le rôle à retirer")
    async def user_remove_roles(self, interaction: Interaction, role: Role) -> None:
        """Open a multi-user selector to remove a role in bulk.

        Args:
            interaction (Interaction): The Discord interaction context.
            role (Role): The role to remove from the users.
        """
        log_request(logger, "user.remove_roles", interaction, role=role)
        if not await is_in_allowed_channel(logger, interaction):
            return

        assert isinstance(interaction.user, Member)
        if not self._can_assign_role(interaction.user, role):
            logger.warning(f"Unauthorized remove_roles by {interaction.user}")
            await interaction.response.send_message(ErrorMessages.NO_PERMISSION_REMOVE_ROLE, ephemeral=True)
            return

        await interaction.response.defer(thinking=True)
        followup_mes = await interaction.followup.send("Sélection des membres en cours...", wait=True)

        view = BulkRoleAssignmentView(role, interaction.user, followup_mes.id, action="remove")
        await interaction.followup.send(
            f"Sélectionnez les membres à qui retirer {escape_md(role.name)} puis cliquez sur **Confirmer**.",
            view=view,
            ephemeral=True,
        )

    @user_group.command(name="with_roles", description="Obtenir les membres avec des rôles spécifiques.")
    async def user_with_roles(self, interaction: Interaction) -> None:
        """Get members with specific roles.

        Args:
            interaction (Interaction): The Discord interaction context.
        """
        log_request(logger, "user.with_roles", interaction)
        if not await is_in_allowed_channel(logger, interaction):
            return

        await interaction.response.defer(thinking=True)
        followup_mes = await interaction.followup.send("Sélection des rôles en cours...", wait=True)

        view = MultiRoleSelectorView(followup_mes.id)
        await interaction.followup.send(
            "Sélectionnez les rôles que vous recherchez puis cliquez sur **Confirmer**.",
            view=view,
            ephemeral=True,
        )

    # endregion User Slash Commands Group

    # region ====== Helpers ======
    # -- Scheduling --

    async def _schedule_deop(
        self,
        interaction: Interaction,
        member: Member,
        time: int,
        admin_role: Role,
    ) -> None:
        """Schedule removal of temporary admin role after timeout.

        Args:
            interaction (Interaction): The interaction that triggered the deop.
            member (Member): The user to remove the role from.
            time (int): The time in minutes to wait before removing the role.
            admin_role (Role): The admin role to remove.
        """
        logger.debug(f"Scheduling deop for {member} after {time} minutes")
        try:
            await asyncio.sleep(time * 60)
            if member.id in self.deop_tasks:
                success, error = await safe_remove_roles(
                    logger,
                    member,
                    admin_role,
                    reason="Scheduled removal of temporary admin role",
                )
                if not success:
                    await interaction.followup.send(str(error))
                    return

                logger.info(f"Revoked temporary admin from {member} after {time} minutes")
                await interaction.followup.send(
                    f"Droits admin retirés de {format_member_mention(member)} après {time} minutes.",
                )
            self.deop_tasks.pop(member.id, None)
        except asyncio.CancelledError:
            logger.info(f"Deop timer cancelled for {member}")
        except Exception as e:
            logger.exception(f"Error in deop task for {member}: {e}")  # TODO: redondant
            # FIXME: exception quand deop est appelé manuellement avant la fin du timer

    # -- Permission Checks --

    @staticmethod
    def _can_assign_role(user: User | Member, target_role: Role) -> bool:
        """Checks if the member can assign a specific role.

        Args:
            user (User | Member): The user attempting to assign the role.
            target_role (Role): The role to be assigned.

        Returns:
            bool: True if the user can assign the role, False otherwise.
        """
        if target_role.name == RoleNames.ADMIN:
            return False
        assert isinstance(user, Member)

        return (
            UserManagement._is_user_server_admin(user)
            or UserManagement._is_user_responsible_for_pole(user, target_role)
            or UserManagement._is_user_responsible_for_trainers(user, target_role)
        )

    @staticmethod
    def _is_user_server_admin(member: Member) -> bool:
        """Checks if the member has an administrative role.

        Args:
            member (Member): The member to check.

        Returns:
            bool: `True` if the member is a server admin, `False` otherwise.
        """
        member_role_names = {role.name for role in member.roles}
        return bool(member_role_names & ADMIN_ROLES)

    @staticmethod
    def _is_user_responsible_for_pole(member: Member, target_role: Role) -> bool:
        """Checks if member is responsible for the 'pole' of the target role.

        Args:
            member (Member): The member to check.
            target_role (Role): The role to check against.

        Returns:
            bool: `True` if the member is responsible for the pole, `False` otherwise.
        """
        role_names = {role.name for role in member.roles}
        if target_role.name == RoleNames.SBIRE_BUREAU and RoleNames.BUREAU in role_names:
            return True
        if target_role.name.startswith("Pôle "):
            suffix = target_role.name.split("Pôle ", 1)[1]
            if f"Respo {suffix}" in role_names:
                return True
        return False

    @staticmethod
    def _is_user_responsible_for_trainers(member: Member, target_role: Role) -> bool:
        """Checks if member manages formations for the target role.

        Args:
            member (Member): The member to check.
            target_role (Role): The role to check against.

        Returns:
            bool: `True` if the member is responsible for the formation, `False` otherwise.
        """
        role_names = {role.name for role in member.roles}
        return RoleNames.TRAININGS_MANAGER in role_names and target_role.name.startswith("F - ")

    # endregion Helpers


@deprecated("Load the cog using `bot.add_cog()` instead.")
async def setup(bot: commands.Bot) -> None:
    """Setup function to load the UserManagement cog.

    Args:
        bot (commands.Bot): The bot instance.
    """
    await bot.add_cog(UserManagement(bot))


# region ====== UI View ======


class BulkRoleAssignmentView(ui.View):
    """View for bulk role assignment/removal."""

    def __init__(
        self,
        role: Role,
        user: Member,
        followup_id: int,
        *,
        action: Literal["add", "remove"],
    ) -> None:
        """Initialize the view for bulk role assignment/removal.

        Args:
            role (Role): The role to assign.
            user (Member): The member initiating the role assignment.
            followup_id (int): The ID of the follow-up message to edit with results.
            action (Literal["add", "remove"]): "add" to add the role, "remove" to remove it.
        """
        super().__init__()
        self.role = role
        self.user = user
        self.followup_id = followup_id
        self.action = action

        async def _on_select(interaction: Interaction) -> None:
            if not interaction.response.is_done():
                await interaction.response.defer()

        self.select: ui.UserSelect[Any] = ui.UserSelect(
            placeholder="Sélectionne les membres…",
            min_values=1,
            max_values=25,
        )
        self.select.callback = _on_select  # type: ignore

        self.confirm_button: ui.Button[Any] = ui.Button(label="Confirmer", style=ButtonStyle.primary)
        self.confirm_button.callback = self.confirm  # type: ignore

        self.add_item(self.select)
        self.add_item(self.confirm_button)

    async def confirm(self, interaction: Interaction) -> None:
        """Confirm the bulk role assignment/removal.

        Args:
            interaction (Interaction): The Discord interaction triggered by the confirm button.
        """
        members: list[Member] = [m for m in self.select.values if isinstance(m, Member)]
        if not members:
            await interaction.response.send_message("Aucun membre sélectionné.", ephemeral=True)
            return

        for child in self.children:
            if isinstance(child, ui.Button | ui.UserSelect):
                child.disabled = True
        await interaction.response.edit_message(view=self)

        modified: list[Member] = []
        already: list[Member] = []
        failed: list[Member] = []

        if self.action == "add":
            for m in members:
                if self.role in m.roles:
                    logger.info(f"User {m} already has role {self.role}")
                    already.append(m)
                    continue

                success, _error = await safe_add_roles(logger, m, self.role, reason=f"Bulk add by {self.user}")
                if success:
                    modified.append(m)
                else:
                    failed.append(m)

        elif self.action == "remove":
            for m in members:
                if self.role not in m.roles:
                    logger.info(f"User {m} does not have role {self.role}")
                    already.append(m)
                    continue

                success, _error = await safe_remove_roles(logger, m, self.role, reason=f"Bulk remove by {self.user}")
                if success:
                    modified.append(m)
                else:
                    failed.append(m)

        logger.info(
            f"Members to {self.action} role {self.role}: {members},"
            f"\nSuccess:{modified},\nAlready: {already},\nFailed: {failed}",
        )

        action_str = {"add": "Ajouté", "remove": "Retiré"}
        already_str = {"add": "présent", "remove": "absent"}

        lines: list[str] = [f"Rôle {escape_md(self.role.name)} :"]
        if modified:
            lines.append(f"{action_str[self.action]} avec succès : {', '.join(format_member_mention(m) for m in modified)}")
        if already:
            lines.append(f"Déjà {already_str[self.action]} chez : {', '.join(format_member_mention(m) for m in already)}")
        if failed:
            lines.append(f"Échec : {', '.join(format_member_mention(m) for m in failed)}")

        await interaction.followup.edit_message(self.followup_id, content="\n".join(lines))
        await interaction.delete_original_response()


class MultiRoleSelectorView(ui.View):
    """View for multi role selection."""

    def __init__(
        self,
        followup_id: int,
    ) -> None:
        """Initialize the view for multi role selection.

        Args:
            followup_id (int): The ID of the follow-up message to edit with results.
        """
        super().__init__()
        self.followup_id = followup_id

        async def _on_select(interaction: Interaction) -> None:
            if not interaction.response.is_done():
                await interaction.response.defer()

        self.select: ui.RoleSelect[Any] = ui.RoleSelect(
            placeholder="Sélectionne les rôles…",
            min_values=1,
            max_values=25,
        )
        self.select.callback = _on_select  # type: ignore

        self.confirm_button: ui.Button[Any] = ui.Button(label="Confirmer", style=ButtonStyle.primary)
        self.confirm_button.callback = self.confirm  # type: ignore

        self.add_item(self.select)
        self.add_item(self.confirm_button)

    async def confirm(self, interaction: Interaction) -> None:
        """Confirm the bulk role assignment/removal.

        Args:
            interaction (Interaction): The Discord interaction triggered by the confirm button.
        """
        roles: list[Role] = [r for r in self.select.values if isinstance(r, Role)]
        if not roles:
            await interaction.response.send_message("Aucun rôle sélectionné.", ephemeral=True)
            return

        logger.info(f"Roles selected: {roles}")

        for child in self.children:
            if isinstance(child, ui.Button | ui.UserSelect):
                child.disabled = True
        await interaction.response.edit_message(view=self)

        assert interaction.guild is not None
        members: set[Member] = set(interaction.guild.members)

        lines: list[str] = ["Rôles sélectionnés :"]
        for role in roles:
            role_members = get_members_by_role(role=role)
            lines.append(f"- {format_role_mention(role)} : {len(role_members)} membre{'s' if len(role_members) != 1 else ''}")
            members &= role_members

        lines.append(f"\nMembre(s) avec tous les rôles sélectionnés ({len(members)}) :")
        if not members:
            lines.append("_(Aucun membre)_")
        for member in members:
            lines.append(f"- {format_member_mention(member)}")

        await interaction.followup.edit_message(self.followup_id, content="\n".join(lines))
        await interaction.delete_original_response()


# endregion UI View
