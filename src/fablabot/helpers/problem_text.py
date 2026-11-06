"""Tell what a server lacks in French for its staff: a sentence and the path to fix it, never a code or a raw detail."""

# ruff: noqa: RUF001 - the settings paths use the separator of Discord's own menus
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Final

from fablabot.helpers.constants import RoleNames
from fablabot.helpers.server_check import FIXES
from fablabot.helpers.utils import COMMANDS_CHANNEL_NAME, counted
from fablabot.models.message import SUGGESTION_OPTIONS

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from fablabot.helpers.server_check import Problem

LANGUAGE_NAMES: Final = {"en": "anglais", "fr": "français", "de": "allemand"}
"""The French name of each language the bot ships; a test fails when a locale file has none."""

PERMISSIONS: Final = {
    "create_instant_invite": "créer des invitations",
    "manage_channels": "gérer les salons",
    "add_reactions": "ajouter des réactions",
    "view_audit_log": "voir les logs du serveur",
    "read_messages": "voir les salons",
    "send_messages": "envoyer des messages",
    "manage_messages": "gérer les messages",
    "embed_links": "intégrer des liens",
    "attach_files": "joindre des fichiers",
    "read_message_history": "voir les anciens messages",
    "manage_roles": "gérer les rôles",
    "create_private_threads": "créer des fils privés",
    "send_messages_in_threads": "écrire dans les fils",
}
"""The French name of each permission the invitation asks for."""

LISTED: Final = 8
"""How many names a sentence lists before it counts the others: a server may hold dozens of roles."""

_SERVER_ROLES: Final = "Paramètres du serveur › Rôles"
_INTEGRATIONS: Final = "Paramètres du serveur › Intégrations › FablaBot"
_CHANNEL_PERMISSIONS: Final = "Paramètres du salon › Permissions"

type Wording = tuple[str, str]
"""A sentence and its fix, in Markdown, without the arrow."""


def _listing(items: Sequence[str]) -> str:
    """Join names the way a sentence does: ``A``, ``A et B``, ``A, B et C``, and past ``LISTED`` the count of the others."""
    if len(items) > LISTED:
        return f"{', '.join(items[:LISTED])} … et {counted(len(items) - LISTED, 'autre', 'autres')}"
    if len(items) > 1:
        return f"{', '.join(items[:-1])} et {items[-1]}"
    return ", ".join(items)


def _items(detail: str) -> list[str]:
    """Split a detail that lists names with commas."""
    return [item for item in detail.split(", ") if item]


def _channel(detail: str) -> str:
    """Name the channel a detail holds by its mention, which Discord shows as its name."""
    match = re.search(r"channel_id=(\d+)", detail)
    return f"<#{match[1]}>" if match else "ce salon"


def _roles(labels: str) -> list[str]:
    """Name roles: a role the checks know by name keeps it, one they only know by ID becomes a mention."""
    return [f"<@&{match[1]}>" if (match := re.fullmatch(r"role_id=(\d+)", label)) else label for label in _items(labels)]


def _command(detail: str) -> str:
    """Read the command a detail starts with, as ``/name``."""
    match = re.match(r"/\w+", detail)
    return match[0] if match else "/?"


def _commands_channel_no_post(detail: str) -> Wording:
    return (
        f"Le bot ne peut pas écrire dans le salon de commandes {_channel(detail)}",
        f"{_CHANNEL_PERMISSIONS} : autorise le bot à voir le salon et à y envoyer des messages",
    )


def _log_channel_no_post(detail: str) -> Wording:
    return (
        f"Le bot ne peut pas écrire dans le salon de logs {_channel(detail)}",
        f"{_CHANNEL_PERMISSIONS} : autorise le bot à voir le salon, à y écrire et à y joindre des fichiers, "
        "ou choisis un autre salon avec `/log set`",
    )


def _permissions_missing(detail: str) -> Wording:
    names = [PERMISSIONS.get(name, name.replace("_", " ")) for name in _items(detail)]
    return (
        f"Il manque {counted(len(names), 'permission', 'permissions')} au bot : {_listing(names)}",
        f"{_SERVER_ROLES} › rôle du bot : ajoute-les, ou invite à nouveau le bot avec le lien d'invitation",
    )


def _roles_missing(detail: str) -> Wording:
    items = [
        f"au moins un rôle parmi {', '.join(item.removeprefix('one of ').split(' | '))}" if item.startswith("one of ") else item
        for item in _items(detail)
    ]
    return (
        f"{counted(len(items), 'rôle manquant', 'rôles manquants')} sur ce serveur : {_listing(items)}",
        f"{_SERVER_ROLES} : crée chaque rôle avec exactement ce nom",
    )


def _role_order(detail: str) -> Wording:
    return (
        f"Le rôle du bot est sous {_listing(_roles(detail))}",
        f"{_SERVER_ROLES} : glisse le rôle du bot au-dessus de ces rôles",
    )


def _welcome_setup_missing(detail: str) -> Wording:
    names = [
        item.replace("category ", "la catégorie ", 1) if item.startswith("category ") else item.replace("role ", "le rôle ", 1)
        for item in _items(detail)
    ]
    return (
        f"L'accueil des nouveaux est activé, mais il manque {_listing(names)}",
        "Crée ce qui manque, ou désactive l'accueil avec `/welcome verify`",
    )


def _suggest_recipient_unreachable(detail: str) -> Wording:
    labels = [SUGGESTION_OPTIONS[key].label if key in SUGGESTION_OPTIONS else key for key in _items(detail)]
    return (
        f"Personne ne peut recevoir les suggestions pour {_listing(labels)}",
        "Donne le rôle correspondant à un membre, ou crée le salon du pôle",
    )


def _language_not_shipped(detail: str) -> Wording:
    locale = detail.removeprefix("locale=")
    shipped = _listing([f"l'{name}" if name[0] in "aeiouéh" else f"le {name}" for name in sorted(LANGUAGE_NAMES.values())])
    return (
        f"La langue du serveur ({locale}) n'a pas de textes : l'aide s'affiche en anglais",
        f"Paramètres du serveur › Communauté › Vue d'ensemble : choisis {shipped}",
    )


def _integrations_no_role(detail: str) -> Wording:
    command = _command(detail)
    return (
        f"`{command}` n'est ouvert à aucun des rôles qui l'utilisent",
        f"{_INTEGRATIONS} › `{command}` : ajoute les rôles du staff concernés",
    )


def _integrations_role_missing(detail: str) -> Wording:
    command = _command(detail)
    roles = _listing(_roles(detail.partition(": ")[2]))
    many = len(_items(detail.partition(": ")[2])) > 1
    return (
        f"`{command}` n'est pas ouvert {'aux rôles' if many else 'au rôle'} {roles}",
        f"{_INTEGRATIONS} › `{command}` : ajoute {roles}",
    )


def _integrations_everyone_allowed(detail: str) -> Wording:
    command = _command(detail)
    return (f"`{command}` est ouvert à tout le monde", f"{_INTEGRATIONS} › `{command}` : retire `@everyone`")


def _member_command_denied(detail: str) -> Wording:
    denials: dict[str, list[str]] = {}
    for command, target in re.findall(r"(/\w+) denied for (@everyone|All Channels)", detail):
        denials.setdefault(command, []).append("à tout le monde" if target == "@everyone" else "dans tous les salons")
    sentence = " ; ".join(f"`{command}` est refusé {_listing(targets)}" for command, targets in denials.items())
    commands = _listing([f"`{command}`" for command in denials])
    return sentence or "Une commande ouverte à tous est refusée", f"{_INTEGRATIONS} › {commands} : retire le refus"


def _leftover_commands(detail: str) -> Wording:
    names = [f"`{name}`" for name in re.findall(r"(/\S+) command_id=", detail)]
    return (
        f"{counted(len(names), 'commande en trop', 'commandes en trop')} sur ce serveur : {_listing(names)}",
        "Le bot les retire à son démarrage : redémarre-le, et si elles restent, lis l'avertissement dans les logs",
    )


def _fixed(sentence: str, fix: str) -> Callable[[str], Wording]:
    """Build the wording of a code that needs nothing from its detail."""
    return lambda _detail: (sentence, fix)


SENTENCES: Final[Mapping[str, Callable[[str], Wording]]] = {
    "commands_channel_missing": _fixed("Aucun salon de commandes", f"Crée un salon textuel nommé `{COMMANDS_CHANNEL_NAME}`"),
    "commands_channel_no_post": _commands_channel_no_post,
    "log_channel_missing": _fixed(
        "Aucun salon de logs n'est choisi, ou il n'existe plus",
        "Lance `/log set` dans le salon de commandes pour choisir le salon des logs",
    ),
    "log_channel_no_post": _log_channel_no_post,
    "permissions_missing": _permissions_missing,
    "role_order_temp": _fixed(
        f"Le rôle du bot est sous {RoleNames.ADMIN_TEMP}",
        f"{_SERVER_ROLES} : glisse le rôle du bot au-dessus de {RoleNames.ADMIN_TEMP}",
    ),
    "role_order": _role_order,
    "roles_missing": _roles_missing,
    "welcome_setup_missing": _welcome_setup_missing,
    "suggest_recipient_unreachable": _suggest_recipient_unreachable,
    "language_not_shipped": _language_not_shipped,
    "integrations_no_role": _integrations_no_role,
    "integrations_role_missing": _integrations_role_missing,
    "integrations_everyone_allowed": _integrations_everyone_allowed,
    "member_command_denied": _member_command_denied,
    "leftover_commands": _leftover_commands,
    "check_failed": _fixed(
        "Une lecture auprès de Discord a échoué",
        "Relance `/info`. Si l'erreur revient, lis les logs : une erreur 403 sur les commandes veut dire que "
        "l'invitation n'avait pas `applications.commands`, invite alors le bot à nouveau",
    ),
}
"""The French sentence and fix of each code of ``FIXES``, built from the problem's detail."""


def describe(problem: Problem) -> str:
    """Write a problem for the staff of the server: its mark, a bold sentence and, on the next line, the way to fix it.

    Args:
        problem (Problem): A problem the checks found, whose code is a key of ``FIXES``.

    Returns:
        str: ``⚠️ **sentence**`` or ``❌ **sentence** · bloquant``, a line break and ``→ fix``.
    """
    sentence, fix = SENTENCES[problem.code](problem.detail)
    blocking = FIXES[problem.code][0] == "blocking"
    return f"{'❌' if blocking else '⚠️'} **{sentence}**{' · bloquant' if blocking else ''}\n→ {fix}"
