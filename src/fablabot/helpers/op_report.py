"""What an opped member did by hand in Discord, read back from the audit log when the op ends."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Final

from discord import AllowedMentions, AuditLogAction, HTTPException, Permissions, TextChannel

from fablabot.guild_config import get_log_channel_id
from fablabot.helpers.utils import escape_md

if TYPE_CHECKING:
    from discord import AuditLogEntry, Guild, Member, Role

logger = logging.getLogger(__name__)

MAX_LINES: Final = 20
# How far back the bot looks for the grant of the role, and for the member's actions since.
_GRANT_SCAN: Final = 100
_ACTION_SCAN: Final = 100
# Well inside the 15 minutes a deferred reply stays open, and short enough not to hold the end notice.
_TIMEOUT: Final = 30

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


# Discord's limit is 2000 characters a message; the rest is headroom for the header.
_MESSAGE_CHARS: Final = 1900
_VALUE_CHARS: Final = 80
_FIELDS_PER_ENTRY: Final = 8

_FIELDS: Final = {
    "name": "nom",
    "topic": "sujet",
    "position": "position",
    "nsfw": "NSFW",
    "slowmode_delay": "mode lent (s)",
    "user_limit": "limite de membres",
    "bitrate": "débit",
    "category": "catégorie",
    "colour": "couleur",
    "color": "couleur",
    "hoist": "affiché à part",
    "mentionable": "mentionnable",
    "permissions": "permissions",
    "allow": "autorisé",
    "deny": "refusé",
    "nick": "pseudo",
    "roles": "rôles",
    "mute": "muet",
    "deaf": "sourd",
    "max_age": "durée (s)",
    "max_uses": "utilisations max",
    "temporary": "temporaire",
    "channel": "salon",
    "code": "code",
}


def _value(value: Any) -> str:
    """Show one side of a change in a few characters, pinging nobody and breaking no markdown."""
    if value is None or value == []:
        return "∅"
    if isinstance(value, bool):
        return "oui" if value else "non"
    if isinstance(value, (list, tuple)):
        return ", ".join(_value(item) for item in value)
    mention = getattr(value, "mention", None)
    if isinstance(mention, str):
        return mention
    text = str(value)
    return escape_md(text if len(text) <= _VALUE_CHARS else f"{text[: _VALUE_CHARS - 1]}…")


def _permissions(before: Any, after: Any) -> str:
    """Show a permission change as the flags it turned on and off."""
    was = {name for name, on in (before or Permissions.none()) if on}
    now = {name for name, on in (after or Permissions.none()) if on}
    return " ".join([f"+{name}" for name in sorted(now - was)] + [f"-{name}" for name in sorted(was - now)]) or "∅"


def _changes(entry: AuditLogEntry) -> list[str]:
    """Return one line per changed field of an entry: its name, the value before and after."""
    before, after = dict(entry.changes.before), dict(entry.changes.after)
    lines: list[str] = []
    for field in list(dict.fromkeys([*before, *after])):
        old, new = before.get(field), after.get(field)
        label = _FIELDS.get(field, field)
        if isinstance(old, Permissions) or isinstance(new, Permissions):
            lines.append(f"  {label} : {_permissions(old, new)}")
        elif entry.action is AuditLogAction.member_role_update and field == "roles":
            # Discord lists the roles added on one side and the roles removed on the other.
            lines.append(f"  {label} : +{_value(new)} -{_value(old)}")
        else:
            lines.append(f"  {label} : {_value(old)} → {_value(new)}")
    if len(lines) > _FIELDS_PER_ENTRY:
        lines = [*lines[:_FIELDS_PER_ENTRY], f"  … et {len(lines) - _FIELDS_PER_ENTRY} autres champs"]
    return lines


def describe(entry: AuditLogEntry) -> str:
    """Return the lines of an audit log entry: what was done, to what, then each field before and after."""
    label = _LABELS.get(entry.action, entry.action.name)
    target = entry.target
    shown = (
        getattr(target, "mention", None)
        or (escape_md(code) if isinstance(code := getattr(target, "code", None), str) else None)
        or (f"id {target.id}" if target is not None else None)
    )
    head = f"- {label} : {shown}" if shown else f"- {label}"
    return "\n".join([head, *_changes(entry)])


def _messages(header: str, blocks: list[str]) -> list[str]:
    """Pack the header and the blocks into as few messages as Discord's length limit allows."""
    messages = [header]
    for block in blocks:
        block = block[:_MESSAGE_CHARS]
        if len(messages[-1]) + 1 + len(block) > _MESSAGE_CHARS:
            messages.append(block)
        else:
            messages[-1] = f"{messages[-1]}\n{block}"
    return messages


async def _manual_actions(guild: Guild, member: Member, admin_role: Role) -> list[AuditLogEntry] | None:
    """List what the member did since the bot last gave them the role, oldest first; None when that grant is not found.

    A renewed op keeps the role, so its actions count from the first grant. Actions the member ran
    through the bot are the bot's in the audit log, and are not listed.
    """
    async for grant in guild.audit_logs(limit=_GRANT_SCAN, user=guild.me, action=AuditLogAction.member_role_update):
        target_id = getattr(grant.target, "id", None)
        if target_id == member.id and admin_role in getattr(grant.after, "roles", []):
            return [entry async for entry in guild.audit_logs(limit=_ACTION_SCAN, user=member, after=grant, oldest_first=True)]
    return None


async def report_op(guild: Guild, member: Member, admin_role: Role) -> str:
    """Post the detail of an ended op in the log channel and return the sentence for the op channel.

    The role is already gone when this runs: any failure, a slow audit log included, ends in a sentence, never
    in an error that would hide the end of the op.

    Args:
        guild (Guild): The server.
        member (Member): The member whose op ended.
        admin_role (Role): The temporary role.

    Returns:
        str: One sentence giving the count, never the detail: the op channel may be open to members.
    """
    try:
        async with asyncio.timeout(_TIMEOUT):
            return await _report(guild, member, admin_role)
    except Exception:
        logger.exception(f"Op report failed guild_id={guild.id} member_id={member.id}")
        return "Actions hors du bot : journal d'audit illisible."


async def _report(guild: Guild, member: Member, admin_role: Role) -> str:
    """Do the work of ``report_op``, letting any failure through."""
    actions = await _manual_actions(guild, member, admin_role)
    if actions is None:
        return "Actions hors du bot : début de l'op introuvable dans le journal d'audit."
    if not actions:
        return "Aucune action hors du bot dans le journal d'audit."
    count = f"au moins {len(actions)}" if len(actions) >= _ACTION_SCAN else str(len(actions))
    blocks = [describe(entry) for entry in actions[:MAX_LINES]]
    if len(actions) > MAX_LINES:
        blocks.append(f"- … et {len(actions) - MAX_LINES} autres")
    channel = guild.get_channel(get_log_channel_id(guild.id) or 0)
    if not isinstance(channel, TextChannel):
        logger.warning(f"No log channel for an op report guild_id={guild.id} member_id={member.id}")
        return f"{count} action(s) hors du bot ; aucun salon de logs pour le détail (`/log set`)."
    try:
        for message in _messages(f"Op de {member.mention} terminé : {count} action(s) hors du bot, dans l'ordre.", blocks):
            await channel.send(message, allowed_mentions=AllowedMentions.none())
    except HTTPException:
        logger.warning(f"Op report not posted guild_id={guild.id} member_id={member.id}")
        return f"{count} action(s) hors du bot ; le détail n'a pas pu être posté dans les logs."
    return f"{count} action(s) hors du bot, détail dans {channel.mention}."
