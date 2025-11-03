"""Constants used across multiple cogs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
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

    RESPO_COMMUNICATION = "Respo Communication"
    """Communications manager role."""

    RESPO_EVENT = "Respo Event"
    """Events manager role."""

    RESPO_FORMATION = "Respo Formation"
    """Training manager role."""

    RESPO_NUMERIQUE = "Respo Numérique"
    """IT manager role."""

    RESPO_PARTENARIAT = "Respo Partenariat"
    """Partnerships manager role."""

    RESPO_PROJET = "Respo Projet"
    """Project manager role."""

    BUREAU = "Bureau"
    """Office/Bureau role."""

    POLE_NUMERIQUE = "Pôle Numérique"
    """Digital Pole role."""

    SBIRE_BUREAU = "Sbire Bureau"
    """Office assistant role."""

    MEMBER_VERIFIED = "Membre ✓"
    """Verified member role."""


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


class Emojis:
    """Discord emojis used throughout the bot."""

    PEOPLE = ":busts_in_silhouette:"
    ARROW_RIGHT = ":arrow_right:"
    WARNING = ":warning:"
    LOUDSPEAKER = ":loudspeaker:"

    @staticmethod
    def get_clock_emoji(dt: datetime) -> str:
        """Get the clock emoji corresponding to the given hour and minute.

        Args:
            dt (datetime): The datetime to get the clock emoji for.

        Returns:
            str: The corresponding clock emoji.
        """
        dt = Emojis.round_hour(dt)
        clock_emojis = {
            (0, 0): ":clock12:",
            (0, 30): ":clock1230:",
            (1, 0): ":clock1:",
            (1, 30): ":clock130:",
            (2, 0): ":clock2:",
            (2, 30): ":clock230:",
            (3, 0): ":clock3:",
            (3, 30): ":clock330:",
            (4, 0): ":clock4:",
            (4, 30): ":clock430:",
            (5, 0): ":clock5:",
            (5, 30): ":clock530:",
            (6, 0): ":clock6:",
            (6, 30): ":clock630:",
            (7, 0): ":clock7:",
            (7, 30): ":clock730:",
            (8, 0): ":clock8:",
            (8, 30): ":clock830:",
            (9, 0): ":clock9:",
            (9, 30): ":clock930:",
            (10, 0): ":clock10:",
            (10, 30): ":clock1030:",
            (11, 0): ":clock11:",
            (11, 30): ":clock1130:",
        }
        return clock_emojis.get((dt.hour % 12, dt.minute), ":clock12:")

    @staticmethod
    def round_hour(dt: datetime) -> datetime:
        """Round a datetime to the nearest hour emoji.

        Args:
            dt (datetime): The datetime to round.

        Returns:
            datetime: The rounded datetime.
        """
        if dt.minute >= 45:
            dt += timedelta(hours=1)
            dt = dt.replace(minute=0, second=0, microsecond=0)
        elif dt.minute < 15:
            dt = dt.replace(minute=0, second=0, microsecond=0)
        else:
            dt = dt.replace(minute=30, second=0, microsecond=0)
        return dt


@dataclass
class EasterEggTrigger:
    """Structure for defining an Easter egg trigger."""

    keywords: list[str]
    """List of keywords that trigger the Easter egg."""
    response: str
    """Response to send when the Easter egg is triggered."""
    probability: float
    """Probability of triggering the Easter egg when keywords are found."""


EASTER_EGGS: list[EasterEggTrigger] = [
    EasterEggTrigger(
        ["c'est pas sorcier"],
        "https://tenor.com/view/c-est-pas-sorcier-c%27est-pas-sorcier-jamy-fred-sabine-gif-499752155684427888",
        1.0,
    ),
    EasterEggTrigger(
        ["autiste", "autisme"],
        "https://tenor.com/view/autism-autistic-spongebob-i%27m-autistic-spongebob-meme-gif-990745265488627503",
        1 / 3,
    ),
    EasterEggTrigger(
        ["contre nature", "c'est bizarre"],
        "https://tenor.com/view/lpj-gif-7210529",
        1.0,
    ),
    EasterEggTrigger(
        ["t'es pas net", "baptiste"],
        "https://tenor.com/view/baptiste-feu-fire-gif-13214452",
        1.0,
    ),
    EasterEggTrigger(
        ["feu ", "brûl", "brul"],
        "https://tenor.com/view/elmo-fire-burn-flame-gif-5042503",
        1 / 4,
    ),
]
