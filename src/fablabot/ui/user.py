"""User interface components for user-related features."""

from __future__ import annotations

import logging
from typing import Any, Literal

from discord import (
    ButtonStyle,
    Interaction,
    Member,
    Role,
    ui,
)

from fablabot.helpers import (
    escape_md,
    format_member_mention,
    format_role_mention,
    safe_add_roles,
    safe_remove_roles,
)
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

        async def _on_select(interaction: Interaction) -> None:
            if not interaction.response.is_done():
                await interaction.response.defer()

        self.select: ui.UserSelect[Any] = ui.UserSelect(
            placeholder="Sélectionne les membres…",
            min_values=1,
            max_values=25,
        )
        self.select.callback = _on_select  # type: ignore[method-assign]

        self.confirm_button: ui.Button[Any] = ui.Button(label="Confirmer", style=ButtonStyle.primary)
        self.confirm_button.callback = self.confirm  # type: ignore[method-assign]

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
        self.select.callback = _on_select  # type: ignore[method-assign]

        self.confirm_button: ui.Button[Any] = ui.Button(label="Confirmer", style=ButtonStyle.primary)
        self.confirm_button.callback = self.confirm  # type: ignore[method-assign]

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
        lines.extend(f"- {format_member_mention(m)}" for m in members)

        await interaction.followup.edit_message(self.followup_id, content="\n".join(lines))
        await interaction.delete_original_response()
