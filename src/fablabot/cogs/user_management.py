"""User management commands and permission utilities for Discord Bot.

Provides slash commands for temporary admin and role assignments.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from warnings import deprecated

from discord import Guild, HTTPException, Interaction, Member, NotFound, Role, User, app_commands
from discord.abc import Messageable
from discord.ext import commands
from discord.utils import get

from fablabot.helpers.access import AnyRole, Check, access
from fablabot.helpers.constants import ADMIN_ROLES, ErrorMessages, RoleNames, staff_default_permissions
from fablabot.helpers.safe_discord_operations import safe_add_roles, safe_remove_roles
from fablabot.helpers.state_store import JsonStateStore
from fablabot.helpers.utils import (
    RefusalKind,
    ensure_command_context,
    escape_md,
    format_member_mention,
    format_role_mention,
    log_refusal,
)
from fablabot.models.user import OpExpiry
from fablabot.ui import uui

if TYPE_CHECKING:
    from collections.abc import Collection

logger = logging.getLogger(__name__)

OP_STATE_FILE = Path("data/temp_admin_state.json")
EXPIRY_RETRY_DELAY = timedelta(seconds=60)

_SELF_OP_REFUSED = "Vous ne pouvez pas vous op vous-même : un autre membre du staff doit le faire."
_TEMP_ROLE_REFUSED = f"Le rôle {RoleNames.ADMIN_TEMP} se donne uniquement avec `/user op`, qui fixe sa durée."
_SELF_ADD_REFUSED = "Vous ne pouvez pas vous donner un rôle vous-même : un autre membre du staff doit le faire."
_ADD_REFUSALS = {
    RefusalKind.MISSING_RIGHT: ErrorMessages.NO_PERMISSION_ADD_ROLE,
    RefusalKind.TEMP_ROLE_OUTSIDE_OP: _TEMP_ROLE_REFUSED,
    RefusalKind.SELF_TARGET: _SELF_ADD_REFUSED,
}
_REMOVE_REFUSALS = {RefusalKind.MISSING_RIGHT: ErrorMessages.NO_PERMISSION_REMOVE_ROLE}
# Same audience as the other staff tools (/msg): the member lists it shows are personal data.
_WITH_ROLES_ALLOWED = ADMIN_ROLES | {RoleNames.BUREAU}


def _role_names(member: Member) -> frozenset[str]:
    """Return the names of the roles a member holds."""
    return frozenset(role.name for role in member.roles)


def can_assign(role_names: Collection[str], role: str) -> bool:
    """Tell whether a holder of ``role_names`` may give or take the role named ``role``.

    Args:
        role_names (Collection[str]): The role names the member holds.
        role (str): The name of the role to give or take.

    Returns:
        bool: True for an administrative role (never for the admin role itself), for the bureau's own roles,
            for the pole whose manager the member is, and for the ``F - `` roles of a training manager.
    """
    if role == RoleNames.ADMIN:
        return False
    held = set(role_names)
    if held & ADMIN_ROLES:
        return True
    if role in {RoleNames.SBIRE_BUREAU, RoleNames.AGENTS_MYFAB} and RoleNames.BUREAU in held:
        return True
    if role.startswith("Pôle ") and f"Respo {role.split('Pôle ', 1)[1]}" in held:
        return True
    return RoleNames.TRAININGS_MANAGER in held and role.startswith("F - ")


def role_refusal(role_names: Collection[str], role: str, *, action: Literal["add", "remove"]) -> RefusalKind | None:
    """Tell why a holder of ``role_names`` may not give or take ``role``, or None when allowed.

    Args:
        role_names (Collection[str]): The role names the member holds.
        role (str): The name of the role to give or take.
        action (Literal["add", "remove"]): Whether the role is given or taken.

    Returns:
        RefusalKind | None: The reason to refuse, or None. The self-target refusal depends on the target and is not here.
    """
    if not can_assign(role_names, role):
        return RefusalKind.MISSING_RIGHT
    if action == "add" and role == RoleNames.ADMIN_TEMP:
        return RefusalKind.TEMP_ROLE_OUTSIDE_OP
    return None


def can_op(role_names: Collection[str]) -> bool:
    """Tell whether a holder of ``role_names`` may op or deop: a right over the temporary role, or the digital manager."""
    return can_assign(role_names, RoleNames.ADMIN_TEMP) or RoleNames.DIGITAL_MANAGER in role_names


_OP_ACCESS = Check(lambda c: RoleNames.ADMIN_TEMP in c.server_roles and RoleNames.CODIR in c.server_roles and can_op(c.roles))
_DEOP_ACCESS = Check(lambda c: RoleNames.ADMIN_TEMP in c.server_roles and can_op(c.roles))
_GIVE_ACCESS = Check(lambda c: any(role_refusal(c.roles, r, action="add") is None for r in c.server_roles))
_TAKE_ACCESS = Check(lambda c: any(role_refusal(c.roles, r, action="remove") is None for r in c.server_roles))
_WITH_ROLES_ACCESS = AnyRole(_WITH_ROLES_ALLOWED)


def _utcnow() -> datetime:
    """Return the current time, timezone-aware (patched in tests)."""
    return datetime.now(UTC)


def _delay_until(expires_at: datetime) -> float:
    """Return the seconds left before ``expires_at``, never negative.

    Args:
        expires_at (datetime): Timezone-aware due time.

    Returns:
        float: Seconds to wait.
    """
    return max(0.0, (expires_at - _utcnow()).total_seconds())


class UserManagement(commands.Cog):
    """Cog to register user management commands.

    Commands:
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
        self.deop_tasks: dict[tuple[int, int], asyncio.Task[None]] = {}
        self._op_store = JsonStateStore(logger, OP_STATE_FILE)
        if not isinstance(self._op_store.state, dict):
            # Fail closed: with no readable expiry, every holder is removed at start.
            logger.error(f"Op expiry state in {OP_STATE_FILE} is not a mapping; ignored")
            self._op_store.state = {}
        """state structure: ``{"<guild_id>": {"<member_id>": {"expires_at": iso8601 with offset, "channel_id": int}}}``"""
        logger.info("UserManagement initialized")

    # region ====== User Slash Commands Group ======
    # -- Admin Access --

    user_group = app_commands.Group(
        name="user", description="Gestion des utilisateurs", default_permissions=staff_default_permissions()
    )

    @user_group.command(
        name="op", description="Donne des droits admin temporaires à un utilisateur.", extras=access(_OP_ACCESS)
    )
    @app_commands.describe(
        member="L'utilisateur cible",
        reason="Raison de l'attribution",
        time="Durée en minutes (par défaut 5)",
    )
    async def user_op(
        self,
        interaction: Interaction,
        *,
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
        if not await ensure_command_context(
            logger,
            "user.op",
            interaction,
            log_details={"target": member, "reason": reason, "duration": time},
        ):
            return

        assert interaction.guild is not None
        admin_role = get(interaction.guild.roles, name=RoleNames.ADMIN_TEMP)
        codir_role = get(interaction.guild.roles, name=RoleNames.CODIR)
        if admin_role is None or codir_role is None:
            logger.error(f"Required role not found: {RoleNames.ADMIN_TEMP} or {RoleNames.CODIR}")
            await interaction.response.send_message(
                "Rôles administratifs manquants sur le serveur.",
                ephemeral=True,
            )
            return
        assert isinstance(interaction.user, Member)
        if not can_op(_role_names(interaction.user)):
            await self._refuse(interaction, "user.op", RefusalKind.MISSING_RIGHT, ErrorMessages.INSUFFICIENT_PERMISSIONS)
            return
        if member.id == interaction.user.id:
            await self._refuse(interaction, "user.op", RefusalKind.SELF_TARGET, _SELF_OP_REFUSED)
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

        # Role first, then record: a crash in between leaves a holder with no record, removed at start.
        expiry = OpExpiry(expires_at=_utcnow() + timedelta(minutes=time), channel_id=interaction.channel_id or 0)
        self._record_op(interaction.guild.id, member.id, expiry)
        self._arm_deop(interaction.guild.id, member.id, expiry.expires_at)
        logger.info(f"Granted {member} temporary admin for {time} minutes for reason: {reason}")
        await interaction.response.send_message(
            f"{codir_role.mention} Droits admin donnés à {format_member_mention(member)} pour {time} minutes. "
            f"Raison: {escape_md(reason)}",
        )

    @user_group.command(
        name="deop",
        description="Retire les droits admin temporaires d'un utilisateur.",
        extras=access(_DEOP_ACCESS),
    )
    @app_commands.describe(member="L'utilisateur cible")
    async def user_deop(self, interaction: Interaction, *, member: Member) -> None:
        """Revoke temporary admin privileges from a user.

        Args:
            interaction (Interaction): The Discord interaction context.
            member (Member): The user to remove privileges from.

        """
        if not await ensure_command_context(logger, "user.deop", interaction, log_details={"target": member}):
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
        if not can_op(_role_names(interaction.user)):
            await self._refuse(interaction, "user.deop", RefusalKind.MISSING_RIGHT, ErrorMessages.INSUFFICIENT_PERMISSIONS)
            return

        key = (interaction.guild.id, member.id)
        timer_before = self.deop_tasks.get(key)
        success, error = await safe_remove_roles(
            logger,
            member,
            admin_role,
            reason=f"Remove with op command by {interaction.user}",
        )
        if not success:
            await interaction.response.send_message(error, ephemeral=True)
            return

        # An op run while the removal was in flight owns a new timer and record: leave both alone.
        timer_now = self.deop_tasks.get(key)
        if timer_now is None or timer_now is timer_before:
            self._cancel_deop(*key)
            self._clear_op(*key)

        logger.info(f"Revoked temporary admin from {member}")
        await interaction.response.defer(thinking=True)
        await interaction.followup.send(content=f"Droits admin retirés de {format_member_mention(member)}")

    # -- Role Management --

    @user_group.command(
        name="add_role",
        description="Donne un rôle à un utilisateur.",
        extras=access(_GIVE_ACCESS),
    )
    @app_commands.describe(
        member="L'utilisateur cible",
        role="Le rôle à attribuer",
    )
    async def user_add_role(self, interaction: Interaction, *, member: Member, role: Role) -> None:
        """Add a role to a single user.

        Args:
            interaction (Interaction): The Discord interaction context.
            member (Member): The user to add the role to.
            role (Role): The role to add to the user.
        """
        if not await ensure_command_context(logger, "user.add_role", interaction, log_details={"target": member, "role": role}):
            return

        assert isinstance(interaction.user, Member)
        kind = self.assignment_refusal(interaction.user, member, role, action="add")
        if kind is not None:
            await self._refuse(interaction, "user.add_role", kind, _ADD_REFUSALS[kind])
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

    @user_group.command(
        name="remove_role",
        description="Retire un rôle à un utilisateur.",
        extras=access(_TAKE_ACCESS),
    )
    @app_commands.describe(
        member="L'utilisateur cible",
        role="Le rôle à retirer",
    )
    async def user_remove_role(self, interaction: Interaction, *, member: Member, role: Role) -> None:
        """Remove a role from a single user.

        Args:
            interaction (Interaction): The Discord interaction context.
            member (Member): The user to remove the role from.
            role (Role): The role to remove from the user.
        """
        if not await ensure_command_context(
            logger,
            "user.remove_role",
            interaction,
            log_details={"target": member, "role": role},
        ):
            return

        assert isinstance(interaction.user, Member)
        kind = self.assignment_refusal(interaction.user, member, role, action="remove")
        if kind is not None:
            await self._refuse(interaction, "user.remove_role", kind, _REMOVE_REFUSALS[kind])
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
        extras=access(_GIVE_ACCESS),
    )
    @app_commands.describe(role="Le rôle à attribuer")
    async def user_add_roles(self, interaction: Interaction, *, role: Role) -> None:
        """Open a multi-user selector to add a role in bulk.

        Args:
            interaction (Interaction): The Discord interaction context.
            role (Role): The role to add to the users.
        """
        if not await ensure_command_context(logger, "user.add_roles", interaction, log_details={"role": role}):
            return

        assert isinstance(interaction.user, Member)
        kind = self.assignment_refusal(interaction.user, None, role, action="add")
        if kind is not None:
            await self._refuse(interaction, "user.add_roles", kind, _ADD_REFUSALS[kind])
            return

        await interaction.response.defer(thinking=True)
        followup_mes = await interaction.followup.send("Sélection des membres en cours...", wait=True)

        view = uui.BulkRoleAssignmentView(
            role,
            interaction.user,
            followup_mes.id,
            action="add",
            check=lambda actor, target: self.assignment_refusal(actor, target, role, action="add"),
        )
        await interaction.followup.send(
            f"Sélectionnez les membres à qui ajouter {escape_md(role.name)} puis cliquez sur **Confirmer**.",
            view=view,
            ephemeral=True,
        )

    @user_group.command(
        name="remove_roles",
        description="Retire un rôle à plusieurs utilisateurs via un sélecteur.",
        extras=access(_TAKE_ACCESS),
    )
    @app_commands.describe(role="Le rôle à retirer")
    async def user_remove_roles(self, interaction: Interaction, *, role: Role) -> None:
        """Open a multi-user selector to remove a role in bulk.

        Args:
            interaction (Interaction): The Discord interaction context.
            role (Role): The role to remove from the users.
        """
        if not await ensure_command_context(logger, "user.remove_roles", interaction, log_details={"role": role}):
            return

        assert isinstance(interaction.user, Member)
        kind = self.assignment_refusal(interaction.user, None, role, action="remove")
        if kind is not None:
            await self._refuse(interaction, "user.remove_roles", kind, _REMOVE_REFUSALS[kind])
            return

        await interaction.response.defer(thinking=True)
        followup_mes = await interaction.followup.send("Sélection des membres en cours...", wait=True)

        view = uui.BulkRoleAssignmentView(
            role,
            interaction.user,
            followup_mes.id,
            action="remove",
            check=lambda actor, target: self.assignment_refusal(actor, target, role, action="remove"),
        )
        await interaction.followup.send(
            f"Sélectionnez les membres à qui retirer {escape_md(role.name)} puis cliquez sur **Confirmer**.",
            view=view,
            ephemeral=True,
        )

    @user_group.command(
        name="with_roles",
        description="Obtenir les membres avec des rôles spécifiques.",
        extras=access(_WITH_ROLES_ACCESS),
    )
    async def user_with_roles(self, interaction: Interaction) -> None:
        """Get members with specific roles.

        Args:
            interaction (Interaction): The Discord interaction context.
        """
        if not await ensure_command_context(logger, "user.with_roles", interaction, required_roles=_WITH_ROLES_ALLOWED):
            return

        view = uui.MultiRoleSelectorView(check=lambda actor: bool({r.name for r in actor.roles} & _WITH_ROLES_ALLOWED))
        await interaction.response.send_message(
            "Sélectionnez les rôles que vous recherchez puis cliquez sur **Confirmer**.",
            view=view,
            ephemeral=True,
        )

    # endregion User Slash Commands Group

    # region ====== Helpers ======
    # -- Scheduling --

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        """Replay recorded op expiries on every guild the bot sees.

        Discord sends ``on_ready`` again after some reconnects, so this is idempotent: a running
        timer is kept, a record is never duplicated. Unavailable guilds keep their records.
        """
        for guild in self.bot.guilds:
            if not guild.unavailable:
                await self._reconcile_guild(guild)

    @commands.Cog.listener()
    async def on_guild_available(self, guild: Guild) -> None:
        """Reconcile a guild that comes back from an outage, or reaches the bot after ``on_ready``."""
        await self._reconcile_guild(guild)

    async def _reconcile_guild(self, guild: Guild) -> None:
        """Remove the temporary role from holders with no future expiry, arm the others, drop stale records."""
        admin_role = get(guild.roles, name=RoleNames.ADMIN_TEMP)
        if admin_role is None:
            return
        holders = {member.id: member for member in admin_role.members}
        for member in holders.values():
            expiry = self._load_op(guild.id, member.id)
            if expiry is not None and expiry.expires_at > _utcnow():
                task = self.deop_tasks.get((guild.id, member.id))
                if task is None or task.done():
                    self._arm_deop(guild.id, member.id, expiry.expires_at)
                continue
            cause = "no recorded expiry" if expiry is None else "expiry passed"
            success, _error = await safe_remove_roles(
                logger,
                member,
                admin_role,
                reason=f"Temporary admin role removed at bot start: {cause}",
            )
            if not success:
                logger.error(f"Failed to remove temporary admin role at start guild_id={guild.id} member_id={member.id}")
                continue
            logger.warning(f"Removed temporary admin role at start ({cause}) guild_id={guild.id} member_id={member.id}")
            self._clear_op(guild.id, member.id)

        self._sweep_stale_records(guild, admin_role)

    def _sweep_stale_records(self, guild: Guild, admin_role: Role) -> None:
        """Drop this guild's records of ops that are over, and arm the ones the cache cannot vouch for.

        A record is kept when its member holds the role or a live timer waits for them (an op that
        started while the removals were awaited). Otherwise it is dropped only when the guild's
        member cache is complete; with a partial cache the member may hold the role unseen, so the
        record gets its timer and ``_expire`` asks Discord when it fires (now, if the time has passed).
        """
        state = self._op_store.state
        entries = state.get(str(guild.id))
        if str(guild.id) in state and not isinstance(entries, dict):
            logger.error(f"Op state entry is not a mapping; reset guild_id={guild.id}")
            state.pop(str(guild.id))
            self._op_store.save()
            return
        if entries is None:
            return
        holder_ids = {member.id for member in admin_role.members}
        for key in list(entries):
            if not key.isdigit():
                entries.pop(key)
                continue
            member_id = int(key)
            task = self.deop_tasks.get((guild.id, member_id))
            if member_id in holder_ids or (task is not None and not task.done()):
                continue
            if guild.chunked:
                entries.pop(key)
                continue
            expiry = self._load_op(guild.id, member_id)
            if expiry is None:
                entries.pop(key)
            else:
                self._arm_deop(guild.id, member_id, max(expiry.expires_at, _utcnow()))
        if not entries:
            self._op_store.state.pop(str(guild.id))
        self._op_store.save()

    def _record_op(self, guild_id: int, member_id: int, expiry: OpExpiry) -> None:
        """Persist the expiry of a member's op on one guild, replacing any previous one."""
        entries = self._op_store.ensure_guild(guild_id)
        if not isinstance(entries, dict):
            entries = self._op_store.state[str(guild_id)] = {}
        entries[str(member_id)] = expiry.to_dict()
        self._op_store.save()

    def _clear_op(self, guild_id: int, member_id: int) -> None:
        """Forget the expiry of a member's op on one guild, if any."""
        entries = self._op_store.state.get(str(guild_id))
        if not isinstance(entries, dict) or str(member_id) not in entries:
            return
        entries.pop(str(member_id))
        if not entries:
            self._op_store.state.pop(str(guild_id))
        self._op_store.save()

    def _load_op(self, guild_id: int, member_id: int) -> OpExpiry | None:
        """Read the recorded expiry of a member's op on one guild.

        Returns:
            OpExpiry | None: The expiry, or None when absent or unreadable (logged as an error).
        """
        entries = self._op_store.state.get(str(guild_id))
        if entries is not None and not isinstance(entries, dict):
            logger.error(f"Unreadable op expiries for guild_id={guild_id}; treated as absent")
            return None
        raw = entries.get(str(member_id)) if entries else None
        if raw is None:
            return None
        try:
            return OpExpiry.from_dict(raw)
        except (TypeError, ValueError):
            logger.exception(f"Unreadable op expiry guild_id={guild_id} member_id={member_id}; treated as absent")
            return None

    def _arm_deop(self, guild_id: int, member_id: int, expires_at: datetime) -> None:
        """Start the one timer of a member's op on one guild, replacing any running one."""
        self._cancel_deop(guild_id, member_id)
        self.deop_tasks[guild_id, member_id] = asyncio.create_task(self._deop_later(guild_id, member_id, expires_at))

    def _cancel_deop(self, guild_id: int, member_id: int) -> None:
        """Cancel the timer of a member's op on one guild, if any."""
        task = self.deop_tasks.pop((guild_id, member_id), None)
        if task:
            task.cancel()

    async def _deop_later(self, guild_id: int, member_id: int, expires_at: datetime) -> None:
        """Wait until the recorded time, then end the op.

        The wait is measured on the loop's monotonic clock while the recorded time is a wall-clock
        time, so a wake-up before the wall clock reaches it sleeps again instead of giving up.
        While the guild cannot be acted on (an outage, a member Discord has not answered for), the
        timer keeps the record and tries again every ``EXPIRY_RETRY_DELAY``, so the role does not
        outlive the outage until the next start.
        """
        try:
            while True:
                while (delay := _delay_until(expires_at)) > 0:
                    await asyncio.sleep(delay)
                if await self._expire(guild_id, member_id):
                    return
                expires_at = _utcnow() + EXPIRY_RETRY_DELAY
        except asyncio.CancelledError:
            logger.debug(f"Deop timer cancelled guild_id={guild_id} member_id={member_id}")
            raise
        except Exception:
            logger.exception(f"Error in deop timer guild_id={guild_id} member_id={member_id}")
        finally:
            if self.deop_tasks.get((guild_id, member_id)) is asyncio.current_task():
                self.deop_tasks.pop((guild_id, member_id))

    async def _expire(self, guild_id: int, member_id: int) -> bool:
        """End a member's op on one guild if its recorded time has come.

        Removes the role, then forgets the record (a crash in between leaves an orphan record,
        cleared later), then posts the notice in the op channel. A failed removal keeps the record
        for the next start and posts a failure notice instead. An unavailable guild, or a member
        Discord could not be asked about, keeps the record and asks the caller to try again.

        Returns:
            bool: False when the caller should wait and call again, True when there is nothing left to retry.
        """
        expiry = self._load_op(guild_id, member_id)
        if expiry is None or expiry.expires_at > _utcnow():
            return True
        guild = self.bot.get_guild(guild_id)
        if guild is None or guild.unavailable:
            logger.warning(f"Guild unreachable at op expiry guild_id={guild_id} member_id={member_id}; record kept")
            # An unavailable guild comes back; one the bot is not in does not.
            return guild is None
        admin_role = get(guild.roles, name=RoleNames.ADMIN_TEMP)
        if admin_role is None:
            self._clear_op(guild_id, member_id)
            return True
        try:
            member = await self._find_member(guild, member_id)
        except HTTPException:
            logger.warning(f"Member lookup failed at op expiry guild_id={guild_id} member_id={member_id}; record kept")
            return False
        if member is None or admin_role not in member.roles:
            self._clear_op(guild_id, member_id)
            return True

        await self._revoke_at_expiry(guild, member, admin_role, expiry)
        return True

    async def _revoke_at_expiry(self, guild: Guild, member: Member, admin_role: Role, expiry: OpExpiry) -> None:
        """Take the temporary admin role off a member at expiry and tell the op channel how it went."""
        success, error = await safe_remove_roles(logger, member, admin_role, reason="Scheduled removal of temporary admin role")
        if not success:
            logger.error(f"Failed to remove temporary admin role guild_id={guild.id} member_id={member.id}; record kept")
            await self._post_op_notice(
                guild,
                member.id,
                expiry,
                f"Impossible de retirer le rôle {RoleNames.ADMIN_TEMP} à {format_member_mention(member)} : {error}",
            )
            return

        self._clear_op(guild.id, member.id)
        logger.info(f"Revoked temporary admin at expiry guild_id={guild.id} member_id={member.id}")
        notice = f"Droits admin temporaires retirés de {format_member_mention(member)} : durée écoulée."
        await self._post_op_notice(guild, member.id, expiry, notice)

    @staticmethod
    async def _find_member(guild: Guild, member_id: int) -> Member | None:
        """Find a guild member, asking Discord when the cache cannot say the member is gone.

        A cached member is used as is. A missing one is read as gone only when the guild's member
        cache is complete: `Guild.chunked` compares a member count that can drift, so otherwise
        Discord is asked.

        Args:
            guild (Guild): The guild to look in.
            member_id (int): The member's Discord ID.

        Returns:
            Member | None: The member, or None when they are no longer in the guild.

        Raises:
            HTTPException: Discord could not answer (anything but "unknown member").
        """
        member = guild.get_member(member_id)
        if member is not None or guild.chunked:
            return member
        try:
            return await guild.fetch_member(member_id)
        except NotFound:
            return None

    @staticmethod
    async def _post_op_notice(guild: Guild, member_id: int, expiry: OpExpiry, text: str) -> None:
        """Post an op notice in the channel where the op was run; log only when it is gone."""
        channel = guild.get_channel(expiry.channel_id)
        if channel is None or not isinstance(channel, Messageable):
            logger.warning(
                f"Op channel {expiry.channel_id} gone in guild_id={guild.id} member_id={member_id}; notice not posted",
            )
            return
        await channel.send(text)

    # -- Permission Checks --

    @staticmethod
    async def _refuse(interaction: Interaction, command_name: str, kind: RefusalKind, text: str) -> None:
        """Log a refusal by kind and tell the caller privately."""
        log_refusal(logger, command_name, interaction, kind)
        await interaction.response.send_message(text, ephemeral=True)

    @staticmethod
    def assignment_refusal(
        actor: Member,
        target: Member | None,
        role: Role,
        *,
        action: Literal["add", "remove"],
    ) -> RefusalKind | None:
        """Tell why ``actor`` may not give ``role`` to (or take it from) ``target``, or None when allowed.

        Args:
            actor (Member): The member acting, with their current roles.
            target (Member | None): The member targeted; None to check the role alone.
            role (Role): The role to give or take.
            action (Literal["add", "remove"]): Whether the role is given or taken.

        Returns:
            RefusalKind | None: The first reason to refuse, or None.
        """
        kind = role_refusal(_role_names(actor), role.name, action=action)
        if kind is None and action == "add" and target is not None and target.id == actor.id:
            return RefusalKind.SELF_TARGET
        return kind

    @staticmethod
    def _can_assign_role(user: Member | User, target_role: Role) -> bool:
        """Checks if the member can assign a specific role.

        Args:
            user (Member | User): The user attempting to assign the role.
            target_role (Role): The role to be assigned.

        Returns:
            bool: True if the user can assign the role, False otherwise.
        """
        assert isinstance(user, Member)
        return can_assign(_role_names(user), target_role.name)

    # endregion Helpers


@deprecated("Load the cog using `bot.add_cog()` instead.")
async def setup(bot: commands.Bot) -> None:
    """Setup function to load the UserManagement cog.

    Args:
        bot (commands.Bot): The bot instance.
    """
    await bot.add_cog(UserManagement(bot))
