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
    - /user dm: Send a direct message to multiple users.
    """

    def __init__(self, bot: commands.Bot) -> None:
        """Initialize the cog and register its command groups.

        Args:
            bot (commands.Bot): The bot instance.
        """
        self.bot = bot
        self.deop_tasks: dict[int, asyncio.Task[None]] = {}
        logger.info("UserManagement initialized")

    # region ====== User Slash Group ======
    user_group = app_commands.Group(name="user", description="Gestion des utilisateurs")

    @user_group.command(name="help", description="Affiche l'aide pour les commandes de gestion des utilisateurs.")
    async def user_help(self, interaction: Interaction) -> None:
        """Display help for user management commands.

        Args:
            interaction (Interaction): The interaction object.
        """
        help_text = (
            "**Commandes de gestion des utilisateurs :**\n"
            "- `/user op <user>` : Donne des droits admin temporaires à un utilisateur.\n"
            "- `/user deop <user>` : Retire les droits admin temporaires d'un utilisateur.\n"
            "- `/user add_role <user> <role>` : Donne un rôle à un utilisateur.\n"
            "- `/user remove_role <user> <role>` : Retire un rôle à un utilisateur.\n"
            "- `/user add_roles <role>` : Donne un rôle à plusieurs utilisateurs via un sélecteur.\n"
            "- `/user remove_roles <role>` : Retire un rôle à plusieurs utilisateurs via un sélecteur.\n"
            "- `/user dm <message>` : Envoie un message privé à plusieurs utilisateurs via un sélecteur.\n"
            "- `/user help` : Affiche cette aide pour les commandes de gestion des utilisateurs.\n"
            "\n"
            "Assurez-vous d'avoir les permissions nécessaires pour utiliser ces commandes."
        )
        await interaction.response.send_message(help_text, ephemeral=True)

    @user_group.command(name="op", description="Donne des droits admin temporaires à un utilisateur.")
    @app_commands.describe(
        user="L'utilisateur cible",
        reason="Raison de l'attribution",
        time="Durée en minutes (par défaut 5)",
    )
    async def user_op(self, interaction: Interaction, user: Member, reason: str, time: int = 5) -> None:
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
        if not self._can_assign_role(interaction.user, admin_role) and "Respo Numérique" not in (
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
            f"{codir_role.mention} Droits admin donnés à {user.mention}({user.name!r}) pour {time} minutes. Raison: {reason}"
        )

    @user_group.command(name="deop", description="Retire les droits admin temporaires d'un utilisateur.")
    @app_commands.describe(user="L'utilisateur cible")
    async def user_deop(self, interaction: Interaction, user: Member) -> None:
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
        if not self._can_assign_role(interaction.user, admin_role) and "Respo Numérique" not in (
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
        await interaction.response.send_message("Retrait des droits admin en cours...")
        await interaction.edit_original_response(content=f"Droits admin retirés de {user.mention}({user.name!r}) !")

    @user_group.command(name="add_role", description="Donne un rôle à un utilisateur.")
    @app_commands.describe(user="L'utilisateur cible", role="Le rôle à attribuer")
    async def user_add_role(self, interaction: Interaction, user: Member, role: Role) -> None:
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
        if not self._can_assign_role(interaction.user, role):
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
        await interaction.response.send_message("Le rôle est en cours d'ajout...")
        await interaction.edit_original_response(
            content=f"Le rôle {role.mention}({role.name!r}) a été ajouté à {user.mention}({user.name!r})."
        )

    @user_group.command(name="remove_role", description="Retire un rôle à un utilisateur.")
    @app_commands.describe(user="L'utilisateur cible", role="Le rôle à retirer")
    async def user_remove_role(self, interaction: Interaction, user: Member, role: Role) -> None:
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
        if not self._can_assign_role(interaction.user, role):
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
        await interaction.response.send_message("Le rôle est en cours de retrait...")
        await interaction.edit_original_response(
            content=f"Le rôle {role.mention}({role.name!r}) a été retiré à {user.mention}({user.name!r})."
        )

    @user_group.command(
        name="add_roles",
        description="Donne un rôle à plusieurs utilisateurs via un sélecteur.",
    )
    @app_commands.describe(role="Le rôle à attribuer")
    async def user_add_roles(self, interaction: Interaction, role: Role) -> None:
        """Open a multi-user selector to add a role in bulk.

        Args:
            interaction (Interaction): The interaction object.
            role (Role): The role to add to the users.
        """
        log_request(logger, "user.add_roles", interaction, role=role)
        if not await is_in_allowed_channel(logger, interaction):
            return

        assert isinstance(interaction.user, Member)
        if not self._can_assign_role(interaction.user, role):
            logger.warning(f"Unauthorized add_roles by {interaction.user}")
            await interaction.response.send_message("Vous n'avez pas la permission d'ajouter ce rôle.", ephemeral=True)
            return

        view = BulkRoleView(role, interaction.user, action="add")
        await interaction.response.send_message(
            f"Sélectionnez les membres à qui ajouter {role.name!r} puis cliquez sur **Confirmer**.", view=view
        )

    @user_group.command(name="remove_roles", description="Retire un rôle à plusieurs utilisateurs via un sélecteur.")
    @app_commands.describe(role="Le rôle à retirer")
    async def user_remove_roles(self, interaction: Interaction, role: Role) -> None:
        """Open a multi-user selector to remove a role in bulk.

        Args:
            interaction (Interaction): The interaction object.
            role (Role): The role to remove from the users.
        """
        log_request(logger, "user.remove_roles", interaction, role=role)
        if not await is_in_allowed_channel(logger, interaction):
            return

        assert isinstance(interaction.user, Member)
        if not self._can_assign_role(interaction.user, role):
            logger.warning(f"Unauthorized remove_roles by {interaction.user}")
            await interaction.response.send_message("Vous n'avez pas la permission de retirer ce rôle.", ephemeral=True)
            return

        view = BulkRoleView(role, interaction.user, action="remove")
        await interaction.response.send_message(
            f"Sélectionnez les membres à qui retirer {role.name!r} puis cliquez sur **Confirmer**.", view=view
        )

    @user_group.command(
        name="dm",
        description="Envoie un message privé à plusieurs utilisateurs via un sélecteur.",
    )
    @app_commands.describe(message="Le message à envoyer en MP.")
    async def user_dm(self, interaction: Interaction, message: str) -> None:
        """Send a direct message to multiple users.

        Args:
            interaction (Interaction): The interaction object.
            message (str): The message content to send.
        """
        log_request(logger, "user.dm", interaction, message=message)
        if not await is_in_allowed_channel(logger, interaction):
            return

        assert isinstance(interaction.user, Member)

        role_names = {role.name for role in interaction.user.roles}
        if "Bureau" not in role_names:
            logger.warning(f"Unauthorized dm by {interaction.user}")
            await interaction.response.send_message("Permissions insuffisantes.", ephemeral=True)
            return

        message += f"\n\n*Ce message vous a été envoyé par un membre du Bureau du Fablab. Merci de ne pas y répondre directement.*\nPour plus d'informations, contactez <@{interaction.user.id}>."

        view = BulkDMView(interaction.user, message)
        await interaction.response.send_message(
            (
                "Selectionnez les membres a qui envoyer le message puis cliquez sur **Confirmer**.\n\n"
                f"Message à envoyer :\n>>> {message}"
            ),
            view=view,
        )

    # endregion User Slash Group

    # region ====== Helpers ======
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
                        f"{codir_role.mention} Je ne peux pas retirer le rôle admin de {user.mention}({user.name!r})."
                    )
                    return
                except HTTPException as e:
                    logger.error(f"HTTP error while removing admin role from {user}: {e}")
                    await interaction.followup.send(
                        f"{codir_role.mention} Erreur HTTP lors de la suppression du rôle admin de {user.mention}({user.name!r})."
                    )
                    return

                logger.info(f"Revoked temporary admin from {user} after {time} minutes")
                await interaction.followup.send(f"Droits admin retirés de {user.mention}({user.name!r}) après {time} minutes.")
            self.deop_tasks.pop(user.id, None)
        except asyncio.CancelledError:
            logger.info(f"Deop timer cancelled for {user}")

    @staticmethod
    def _can_assign_role(member: Member, target_role: Role) -> bool:
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
            UserManagement._is_user_server_admin(member)
            or UserManagement._is_user_responsible_for_pole(member, target_role)
            or UserManagement._is_user_responsible_for_trainers(member, target_role)
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
        if target_role.name == "Sbire Bureau" and "Bureau" in role_names:
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
        return "Respo Formations" in role_names and target_role.name.startswith("F - ")

    # endregion Helpers


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

        self.confirm_button: ui.Button[Any] = ui.Button(label="Confirmer", style=ButtonStyle.primary)
        self.confirm_button.callback = self.confirm

        self.add_item(self.select)
        self.add_item(self.confirm_button)

    async def confirm(self, interaction: Interaction) -> None:
        """Confirm the bulk role assignment/removal.

        Args:
            interaction (Interaction): The interaction triggered by the confirm button.
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

        action_str = {"add": "Ajouté", "remove": "Retiré"}
        already_str = {"add": "présent", "remove": "absent"}

        lines: list[str] = [f"Rôle {self.role.name!r} :"]
        if modified:
            lines.append(f"{action_str[self.action]} avec succès : {', '.join(f'{m.mention}({m.name!r})' for m in modified)}")
        if already:
            lines.append(f"Déjà {already_str[self.action]} chez : {', '.join(f'{m.mention}({m.name!r})' for m in already)}")
        if failed:
            lines.append(f"Échec : {', '.join(f'{m.mention}({m.name!r})' for m in failed)}")

        for child in self.children:
            if isinstance(child, ui.Button | ui.UserSelect):
                child.disabled = True
        await interaction.response.edit_message(view=self)

        await interaction.edit_original_response(content="\n".join(lines), view=None)


class BulkDMView(ui.View):
    """View for bulk direct message sending."""

    def __init__(
        self,
        sender: Member,
        message: str,
        *,
        timeout: float = 180.0,
    ) -> None:
        """Initialize the view for bulk direct messages.

        Args:
            sender (Member): The member initiating the message sending.
            message (str): The message to send to the selected members.
            timeout (float, optional): The timeout duration in seconds. Defaults to 180.0.
        """
        super().__init__(timeout=timeout)
        self.sender = sender
        self.message = message

        async def _on_select(interaction: Interaction) -> None:
            if not interaction.response.is_done():
                await interaction.response.defer()

        self.select: ui.UserSelect[Any] = ui.UserSelect(
            placeholder="Sélectionne les membres…",
            min_values=1,
            max_values=25,
        )
        self.select.callback = _on_select

        self.confirm_button: ui.Button[Any] = ui.Button(label="Confirmer", style=ButtonStyle.primary)
        self.confirm_button.callback = self.confirm

        self.add_item(self.select)
        self.add_item(self.confirm_button)

    async def confirm(self, interaction: Interaction) -> None:
        """Confirm the direct message sending.

        Args:
            interaction (Interaction): The interaction triggered by the confirm button.
        """
        members: list[Member] = [m for m in self.select.values if isinstance(m, Member)]
        if not members:
            await interaction.response.send_message("Aucun membre sélectionné.", ephemeral=True)
            return

        delivered: list[Member] = []
        failed: list[tuple[Member, str]] = []

        for member in members:
            try:
                await member.send(self.message)
                delivered.append(member)
            except Forbidden:
                failed.append((member, "Forbidden"))
            except Exception as e:
                logger.error(f"Failed to DM {member}: {e}")
                failed.append((member, f"Exception: {e}"))

        logger.info(f"Bulk DM by {self.sender} delivered to {delivered} with failures {failed}")

        lines: list[str] = ["Envoi des messages terminé."]
        if delivered:
            lines.append("Succès : " + ", ".join(f"{member.mention}({member.name!r})" for member in delivered))
        if failed:
            lines.append("Échecs : " + ", ".join(f"{member.mention}({member.name!r})" for member, _ in failed))
        lines.append("Contenu envoyé :")
        lines.append(f">>> {self.message}")

        for child in self.children:
            if isinstance(child, ui.Button | ui.UserSelect):
                child.disabled = True
        await interaction.response.edit_message(view=self)

        await interaction.edit_original_response(content="\n".join(lines), view=None)
