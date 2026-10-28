"""User interface components for user-related features."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Literal

from discord import ButtonStyle, Interaction, Member, Role, ui

from fablabot.helpers.safe_discord_operations import safe_add_roles, safe_remove_roles
from fablabot.helpers.utils import (
    RefusalKind,
    escape_md,
    format_member_mention,
    format_role_mention,
    get_members_by_role,
    log_refusal,
)

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

RIGHT_LOST_MESSAGE = "Vous n'avez plus les droits pour cette action. Rien n'a été modifié."


class BulkRoleAssignmentView(ui.View):
    """View for bulk role assignment/removal."""

    def __init__(
        self,
        role: Role,
        user: Member,
        followup_id: int,
        *,
        action: Literal["add", "remove"],
        check: Callable[[Member, Member | None], RefusalKind | None],
    ) -> None:
        """Initialize the view for bulk role assignment/removal.

        Args:
            role (Role): The role to assign.
            user (Member): The member initiating the role assignment.
            followup_id (int): The ID of the follow-up message to edit with results.
            action (Literal["add", "remove"]): "add" to add the role, "remove" to remove it.
            check (Callable[[Member, Member | None], RefusalKind | None]): Why the clicking member may not act on a
                target (None for the role alone), or None when allowed; run again on Confirm.
        """
        super().__init__(timeout=None)
        self.role = role
        self.user = user
        self.followup_id = followup_id
        self.action = action
        self.check = check

    @ui.select(cls=ui.UserSelect, placeholder="Sélectionne les membres...", min_values=1, max_values=25)
    async def select_members(self, interaction: Interaction, _select: ui.UserSelect) -> None:
        """Called when members are selected from the user select.

        Args:
            interaction (Interaction): The interaction that triggered the selection.
            _select (ui.UserSelect): The select component.
        """
        logger.debug(f"Bulk role select_members user={interaction.user} selection={_select.values}")
        if not interaction.response.is_done():
            await interaction.response.defer()

    @ui.button(label="Confirmer", style=ButtonStyle.primary)
    async def confirm_button(self, interaction: Interaction, button: ui.Button) -> None:
        """Confirm the bulk role assignment/removal, after checking the clicker's rights again.

        Args:
            interaction (Interaction): The Discord interaction triggered by the confirm button.
            button (ui.Button): The button that was clicked.
        """
        members: list[Member] = [m for m in self.select_members.values if isinstance(m, Member)]
        if not members:
            await interaction.response.send_message("Aucun membre sélectionné.", ephemeral=True)
            return
        assert isinstance(interaction.user, Member)
        if self.check(interaction.user, None) is not None:
            log_refusal(logger, f"user.{self.action}_roles", interaction, RefusalKind.RIGHT_LOST)
            await interaction.response.send_message(RIGHT_LOST_MESSAGE, ephemeral=True)
            return

        logger.info(f"Bulk role confirm user={interaction.user} role={self.role} action={self.action} members={members}")
        self.select_members.disabled = True
        button.disabled = True
        await interaction.response.edit_message(view=self)

        refused = [m for m in members if self.check(interaction.user, m) is not None]
        if refused:
            log_refusal(logger, f"user.{self.action}_roles", interaction, RefusalKind.SELF_TARGET)
        modified, already, failed = await self._apply([m for m in members if m not in refused])

        action_str = {"add": "Ajouté", "remove": "Retiré"}
        already_str = {"add": "présent", "remove": "absent"}

        lines: list[str] = [f"Rôle {escape_md(self.role.name)} :"]
        if modified:
            lines.append(f"{action_str[self.action]} avec succès : {', '.join(format_member_mention(m) for m in modified)}")
        if already:
            lines.append(f"Déjà {already_str[self.action]} chez : {', '.join(format_member_mention(m) for m in already)}")
        if failed:
            lines.append(f"Échec : {', '.join(format_member_mention(m) for m in failed)}")
        if refused:
            lines.append(f"Refusé (soi-même) : {', '.join(format_member_mention(m) for m in refused)}")

        await interaction.followup.edit_message(self.followup_id, content="\n".join(lines))
        await interaction.delete_original_response()

    async def _apply(self, members: list[Member]) -> tuple[list[Member], list[Member], list[Member]]:
        """Give or take the role for each member.

        Args:
            members (list[Member]): The members to act on.

        Returns:
            tuple[list[Member], list[Member], list[Member]]: Members changed, already in the wanted state, and failed.
        """
        modified: list[Member] = []
        already: list[Member] = []
        failed: list[Member] = []
        for m in members:
            if (self.role in m.roles) == (self.action == "add"):
                already.append(m)
                continue
            if self.action == "add":
                success, _error = await safe_add_roles(logger, m, self.role, reason=f"Bulk add by {self.user}")
            else:
                success, _error = await safe_remove_roles(logger, m, self.role, reason=f"Bulk remove by {self.user}")
            (modified if success else failed).append(m)
        logger.info(
            f"Members to {self.action} role={self.role} success={modified} already={already} failed={failed}",
        )
        return modified, already, failed


class MultiRoleSelectorView(ui.View):
    """View for multi role selection; the result replaces the private selector message."""

    def __init__(self, *, check: Callable[[Member], bool]) -> None:
        """Initialize the view for multi role selection.

        Args:
            check (Callable[[Member], bool]): Whether the clicking member may still list members; run on Confirm.
        """
        super().__init__(timeout=None)
        self.check = check

    @ui.select(cls=ui.RoleSelect, placeholder="Sélectionne les rôles...", min_values=1, max_values=25)
    async def select_roles(self, interaction: Interaction, _select: ui.RoleSelect) -> None:
        """Called when roles are selected from the role select.

        Args:
            interaction (Interaction): The interaction that triggered the selection.
            _select (ui.RoleSelect): The select component.
        """
        logger.debug(f"Multi-role select_roles user={interaction.user} selection={_select.values}")
        if not interaction.response.is_done():
            await interaction.response.defer()

    @ui.button(label="Confirmer", style=ButtonStyle.primary)
    async def confirm_button(self, interaction: Interaction, button: ui.Button) -> None:  # noqa: ARG002
        """Confirm the multi role selection, after checking the clicker's rights again.

        Args:
            interaction (Interaction): The Discord interaction triggered by the confirm button.
            button (ui.Button): The button that was clicked.
        """
        roles: list[Role] = [r for r in self.select_roles.values if isinstance(r, Role)]
        if not roles:
            await interaction.response.send_message("Aucun rôle sélectionné.", ephemeral=True)
            return
        assert isinstance(interaction.user, Member)
        if not self.check(interaction.user):
            log_refusal(logger, "user.with_roles", interaction, RefusalKind.RIGHT_LOST)
            await interaction.response.send_message(RIGHT_LOST_MESSAGE, ephemeral=True)
            return

        logger.info(f"Roles selected user={interaction.user} roles={roles}")

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

        await interaction.response.edit_message(content="\n".join(lines), view=None)
