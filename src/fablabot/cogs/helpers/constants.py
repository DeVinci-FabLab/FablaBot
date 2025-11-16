"""Constants used across multiple cogs."""

from __future__ import annotations

from zoneinfo import ZoneInfo

PARIS_TZ = ZoneInfo("Europe/Paris")
MAX_MSG_CHARS = 1900


class RoleNames:
    """Standard role names used in the guild."""

    CODIR = "CoDir"
    """Comité Directeur role."""

    ADMIN = "Administrateur"
    """Permanent administrator role."""

    ADMIN_TEMP = "Admin -temp-"
    """Temporary administrator role."""

    PRESIDENT = "Président.e"
    """President role."""

    VICE_PRESIDENT = "Vice-Président.e"
    """Vice-president role."""

    SECRETARY = "Secrétaire Général"
    """General secretary role."""

    COMMUNICATION_MANAGER = "Respo Communication"
    """Communications manager role."""

    EVENTS_MANAGER = "Respo Event"
    """Events manager role."""

    TRAININGS_MANAGER = "Respo Formation"
    """Training manager role."""

    DIGITAL_MANAGER = "Respo Numérique"
    """IT manager role."""

    PARTNERSHIPS_MANAGER = "Respo Partenariat"
    """Partnerships manager role."""

    PROJECTS_MANAGER = "Respo Projet"
    """Project manager role."""

    BUREAU = "Bureau"
    """Office/Bureau role."""

    DIGITAL_POLE = "Pôle Numérique"
    """Digital Pole role."""

    SBIRE_BUREAU = "Sbire Bureau"
    """Office assistant role."""

    MEMBER_VERIFIED = "Membre ✓"
    """Verified member role."""


ADMIN_ROLES = {
    RoleNames.ADMIN_TEMP,
    RoleNames.ADMIN,
    RoleNames.PRESIDENT,
    RoleNames.VICE_PRESIDENT,
    RoleNames.SECRETARY,
}


class ErrorMessages:
    """Standard error messages."""

    # Permission errors
    INSUFFICIENT_PERMISSIONS = "Permissions insuffisantes."
    """User lacks required permissions."""

    NO_PERMISSION_MANAGE_MESSAGES = "Vous n'avez pas la permission de gérer les messages dans ce salon."
    """User lacks permission to manage messages in channel."""

    NO_PERMISSION_ADD_ROLE = "Vous n'avez pas la permission d'ajouter ce rôle."
    """User lacks permission to add role."""

    NO_PERMISSION_REMOVE_ROLE = "Vous n'avez pas la permission de retirer ce rôle."
    """User lacks permission to remove role."""

    # Role/Member operation errors
    ROLE_ADD_FAILED = "Impossible d'ajouter le rôle."
    """Failed to add role to user."""

    ROLE_REMOVE_FAILED = "Impossible de retirer le rôle."
    """Failed to remove role from user."""

    ROLE_NOT_FOUND = "Impossible de trouver le(s) rôle(s) {role_name} sur ce serveur."
    """Role not found on server."""

    MEMBER_NOT_FOUND = "Impossible de trouver le membre avec le nom {member_name}."
    """Member not found by name."""

    # Channel operation errors
    CHANNEL_CREATE_FAILED = "Erreur lors de la création du {channel_type}."
    """Failed to create channel."""

    CHANNEL_DELETE_FAILED = "Erreur lors de la suppression du {channel_type}."
    """Failed to delete channel."""

    CHANNEL_EDIT_FAILED = "Erreur lors du renommage du {channel_type}."
    """Failed to edit/rename channel."""

    CHANNEL_CLEAR_FAILED = "Erreur lors du nettoyage de ce salon."
    """Failed to clear channel messages."""

    # Formation management errors
    INVALID_EMOJI = "Émoji invalide."
    """Invalid emoji provided."""

    EMOJI_ALREADY_USED = "Cet émoji est déjà utilisé par une autre formation."
    """Emoji already in use by another formation."""

    INVALID_NAME = "Nom invalide."
    """Invalid name provided."""

    INVALID_DURATION = "Durée invalide."
    """Invalid duration provided."""

    INDEX_OUT_OF_BOUNDS = "Index hors limites (il y a {count} FM)."
    """Formation index out of bounds."""

    DRAFT_EMPTY = "Le brouillon ne contient aucune formation."
    """Draft contains no formations."""

    NO_PUBLISHED_MESSAGE = "Aucun message publié enregistré et aucun ID fourni."
    """No published message recorded and no ID provided."""

    INCONSISTENT_PUBLISHED_DATA = "Données de message publié incohérentes."
    """Published message data is inconsistent."""

    # Generic errors
    GENERIC_ERROR = "Une erreur est survenue lors de {operation}."
    """Generic error message."""

    HTTP_ERROR = "Erreur HTTP lors de {operation}."
    """HTTP error during operation."""
