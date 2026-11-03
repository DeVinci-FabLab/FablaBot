"""What an opped member did by hand in Discord, read back from the audit log when the op ends."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Final

from discord import AllowedMentions, AuditLogAction, HTTPException, TextChannel

from fablabot.guild_config import get_log_channel_id

if TYPE_CHECKING:
    from discord import AuditLogEntry, Guild, Member, Role

logger = logging.getLogger(__name__)

MAX_LINES: Final = 20
# How far back the bot looks for the grant of the role, and for the member's actions since.
_GRANT_SCAN: Final = 100
_ACTION_SCAN: Final = 100

_LABELS: Final = {
    AuditLogAction.guild_update: "serveur modifié",
    AuditLogAction.channel_create: "salon créé",
    AuditLogAction.channel_update: "salon modifié",
    AuditLogAction.channel_delete: "salon supprimé",
    AuditLogAction.overwrite_create: "permissions d'un salon ajoutées",
    AuditLogAction.overwrite_update: "permissions d'un salon modifiées",
    AuditLogAction.overwrite_delete: "permissions d'un salon retirées",
    AuditLogAction.kick: "membre exclu",
    AuditLogAction.ban: "membre banni",
    AuditLogAction.unban: "membre débanni",
    AuditLogAction.member_update: "membre modifié",
    AuditLogAction.member_role_update: "rôles d'un membre modifiés",
    AuditLogAction.role_create: "rôle créé",
    AuditLogAction.role_update: "rôle modifié",
    AuditLogAction.role_delete: "rôle supprimé",
    AuditLogAction.invite_create: "invitation créée",
    AuditLogAction.message_delete: "message supprimé",
    AuditLogAction.message_bulk_delete: "messages supprimés en masse",
}


def describe(entry: AuditLogEntry) -> str:
    """Return one line for an audit log entry: what was done, and to what."""
    label = _LABELS.get(entry.action, entry.action.name)
    target = entry.target
    shown = getattr(target, "mention", None) or (f"id {target.id}" if target is not None else None)
    return f"- {label} : {shown}" if shown else f"- {label}"


async def _manual_actions(guild: Guild, member: Member, admin_role: Role) -> list[AuditLogEntry] | None:
    """List what the member did since the bot last gave them the role, oldest first; None when that grant is not found.

    A renewed op keeps the role, so its actions count from the first grant. Actions the member ran
    through the bot are the bot's in the audit log, and are not listed.
    """
    async for grant in guild.audit_logs(limit=_GRANT_SCAN, user=guild.me, action=AuditLogAction.member_role_update):
        target_id = getattr(grant.target, "id", None)
        if target_id == member.id and admin_role in getattr(grant.after, "roles", []):
            return [entry async for entry in guild.audit_logs(limit=_ACTION_SCAN, user=member, after=grant)]
    return None


async def report_op(guild: Guild, member: Member, admin_role: Role) -> str:
    """Post the detail of an ended op in the log channel and return the sentence for the op channel.

    Args:
        guild (Guild): The server.
        member (Member): The member whose op ended.
        admin_role (Role): The temporary role.

    Returns:
        str: One sentence giving the count, never the detail: the op channel may be open to members.
    """
    try:
        actions = await _manual_actions(guild, member, admin_role)
    except Exception:  # the role is already gone: a report that fails must not hide that
        logger.exception(f"Audit log unreadable at op end guild_id={guild.id} member_id={member.id}")
        return "Actions faites à la main : journal d'audit illisible."
    if actions is None:
        return "Actions faites à la main : début de l'op introuvable dans le journal d'audit."
    if not actions:
        return "Aucune action faite à la main."
    lines = [describe(entry) for entry in actions[:MAX_LINES]]
    if len(actions) > MAX_LINES:
        lines.append(f"- … et {len(actions) - MAX_LINES} autres")
    channel = guild.get_channel(get_log_channel_id(guild.id) or 0)
    if not isinstance(channel, TextChannel):
        logger.warning(f"No log channel for an op report guild_id={guild.id} member_id={member.id}")
        return f"{len(actions)} action(s) faite(s) à la main ; aucun salon de logs pour le détail (`/log set`)."
    try:
        await channel.send(
            f"Op de {member.mention} terminé : {len(actions)} action(s) faite(s) à la main.\n" + "\n".join(lines),
            allowed_mentions=AllowedMentions.none(),
        )
    except HTTPException:
        logger.warning(f"Op report not posted guild_id={guild.id} member_id={member.id}")
        return f"{len(actions)} action(s) faite(s) à la main ; le détail n'a pas pu être posté dans les logs."
    return f"{len(actions)} action(s) faite(s) à la main, détail dans {channel.mention}."
