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
    RoleNames.ADMIN,
    RoleNames.ADMIN_TEMP,
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
    INVALID_EMOJI = "Émoji invalide. Utilise un émoji standard ou custom."
    """Invalid emoji provided."""

    EMOJI_ALREADY_USED = "Cet émoji est déjà utilisé par une autre formation."
    """Emoji already in use by another formation."""

    INVALID_DATETIME = "Date/heure invalides. Format attendu: `DD/MM/YYYY HH:MM` (ex: `15/12/2024 18:30`)."
    """Invalid date and time provided."""

    INVALID_DURATION_SEATS_FORMAT = "Durée/Places invalides. Format attendu: `durée - places` (ex: `2h - 10`)."
    """Invalid duration and seats provided."""

    INVALID_MESSAGE_ID = "ID ou lien de message invalide."
    """Invalid message id or link provided."""

    INVALID_DURATION = "Durée invalide. Format attendu: durée en string (ex: `2h30`, `15 minutes`, ...)."
    """Invalid duration format provided."""

    INVALID_SEATS = "Nombre de places invalide. Veuillez fournir un entier positif compris entre 1 et 500."
    """Invalid seats number provided."""

    DRAFT_EMPTY = "Le brouillon ne contient aucune formation. Utilise `/fm add` pour en ajouter une."
    """Draft contains no formations."""

    MSG_NO_DRAFT = "Aucun brouillon n'est enregistré."
    """No message draft present."""

    MSG_DRAFT_EMPTY = "Aucun brouillon ou contenu vide."
    """Draft is empty or missing content."""

    MSG_NO_TRACKED = "Aucun suivi trouvé pour ce message."
    """Tracked message not found."""

    MSG_NO_REACTIONS_SOURCE = "Aucune réaction préliée trouvée sur le message source fourni."
    """No reactions found to copy from source message."""

    MSG_NO_TRACKED_AVAILABLE = "Aucun message suivi trouvé."
    """No tracked message available."""

    MSG_NO_REACTION_HISTORY = "Aucune réaction enregistrée pour ce message."
    """No reaction history for message."""

    MSG_NO_ACTION_FOR_EMOJI = "Aucune action trouvée pour cet émoji et type."
    """No action matched for emoji and action type."""

    NO_PUBLISHED_MESSAGE = "Aucun message publié enregistré et aucun ID fourni."
    """No published message recorded and no ID provided."""

    INCONSISTENT_PUBLISHED_DATA = "Données de message publié incohérentes."
    """Published message data is inconsistent."""

    # Generic errors
    GENERIC_ERROR = "Une erreur est survenue lors de {operation}."
    """Generic error message."""

    HTTP_ERROR = "Erreur HTTP lors de {operation}."
    """HTTP error during operation."""
