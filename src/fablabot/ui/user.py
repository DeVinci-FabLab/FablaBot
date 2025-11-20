"""User interface components for user-related features."""

from __future__ import annotations

import logging
from typing import Literal

from discord import ButtonStyle, Interaction, Member, Role, ui

from fablabot.helpers import escape_md, format_member_mention, format_role_mention, safe_add_roles, safe_remove_roles
from fablabot.helpers.utils import get_members_by_role

logger = logging.getLogger(__name__)


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

    @ui.select(cls=ui.UserSelect, placeholder="Sélectionne les membres...", min_values=1, max_values=25)
    async def select_members(self, interaction: Interaction, _select: ui.UserSelect) -> None:
        """Called when members are selected from the user select.

        Args:
            interaction (Interaction): The interaction that triggered the selection.
            _select (ui.UserSelect): The select component.
        """
        if not interaction.response.is_done():
            await interaction.response.defer()

    @ui.button(label="Confirmer", style=ButtonStyle.primary)
    async def confirm_button(self, interaction: Interaction, button: ui.Button) -> None:
        """Confirm the bulk role assignment/removal.

        Args:
            interaction (Interaction): The Discord interaction triggered by the confirm button.
            button (ui.Button): The button that was clicked.
        """
        members: list[Member] = [m for m in self.select_members.values if isinstance(m, Member)]
        if not members:
            await interaction.response.send_message("Aucun membre sélectionné.", ephemeral=True)
            return

        self.select_members.disabled = True
        button.disabled = True
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

    @ui.select(cls=ui.RoleSelect, placeholder="Sélectionne les rôles...", min_values=1, max_values=25)
    async def select_roles(self, interaction: Interaction, _select: ui.RoleSelect) -> None:
        """Called when roles are selected from the role select.

        Args:
            interaction (Interaction): The interaction that triggered the selection.
            _select (ui.RoleSelect): The select component.
        """
        if not interaction.response.is_done():
            await interaction.response.defer()

    @ui.button(label="Confirmer", style=ButtonStyle.primary)
    async def confirm_button(self, interaction: Interaction, button: ui.Button) -> None:
        """Confirm the multi role selection.

        Args:
            interaction (Interaction): The Discord interaction triggered by the confirm button.
            button (ui.Button): The button that was clicked.
        """
        roles: list[Role] = [r for r in self.select_roles.values if isinstance(r, Role)]
        if not roles:
            await interaction.response.send_message("Aucun rôle sélectionné.", ephemeral=True)
            return

        logger.info(f"Roles selected: {roles}")

        self.select_roles.disabled = True
        button.disabled = True
        await interaction.response.edit_message(view=self)

        assert interaction.guild is not None
        members: set[Member] = set(interaction.guild.members)

        multi_string = "s" if len(roles) > 1 else ""

        lines: list[str] = [f"Rôle{multi_string} sélectionné{multi_string} :"]
        for role in roles:
            role_members = get_members_by_role(role=role)
            lines.append(f"- {format_role_mention(role)} : {len(role_members)} membre{'s' if len(role_members) != 1 else ''}")
            members &= role_members

        lines.append(
            f"\nMembre(s) avec{' tous' if multi_string else ''} le{multi_string}"
            f" rôle{multi_string} sélectionné{multi_string} ({len(members)}) :",
        )
        if not members:
            lines.append("_(Aucun membre)_")
        lines.extend(f"- {format_member_mention(m)}" for m in members)

        await interaction.followup.edit_message(self.followup_id, content="\n".join(lines))
        await interaction.delete_original_response()
