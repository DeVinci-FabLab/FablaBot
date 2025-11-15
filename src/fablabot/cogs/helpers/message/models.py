"""Message management commands for Discord Bot. Provides slash commands for sending and managing messages."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from fablabot.cogs.helpers.constants import RoleNames

ANONYMOUS_ICON_URL = "https://e7.pngegg.com/pngimages/84/165/png-clipart-united-states-avatar-organization-information-user-avatar-service-computer-wallpaper-thumbnail.png"


@dataclass
class ReactionAction:
    """Configuration for a reaction-based action.

    Attributes:
        emoji (str): The emoji that triggers this action.
        action_type (Literal["channel", "user_dm", "role_dm"]): The type of action to perform.
        message_content (str): The message to send when the reaction is triggered.
        target_id (int | None): The ID of the target (channel_id, user_id, or role_id).
        target_name (str | None): Human-readable name of the target for display purposes.
    """

    emoji: str
    action_type: Literal["channel", "user_dm", "role_dm"]
    message_content: str
    target_id: int | None = None
    target_name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "emoji": self.emoji,
            "action_type": self.action_type,
            "message_content": self.message_content,
            "target_id": self.target_id,
            "target_name": self.target_name,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReactionAction:
        """Create from dictionary."""
        return cls(
            emoji=data["emoji"],
            action_type=data["action_type"],
            message_content=data["message_content"],
            target_id=data.get("target_id"),
            target_name=data.get("target_name"),
        )


@dataclass
class MessageDraft:
    """A draft message with reaction-based actions.

    Attributes:
        content (str): The message content.
        reactions (list[ReactionAction]): List of reaction actions configured for this message.
    """

    content: str
    reactions: list[ReactionAction]

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "content": self.content,
            "reactions": [r.to_dict() for r in self.reactions],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MessageDraft:
        """Create from dictionary."""
        return cls(
            content=data["content"],
            reactions=[ReactionAction.from_dict(r) for r in data.get("reactions", [])],
        )


@dataclass
class SuggestionConfig:
    """Configuration for a suggestion type.

    Attributes:
        embed_title (str): The title of the suggestion embed.
        role_name (str | None): The role name to get responsible members.
        channel_name (str | None): The channel name to send the suggestion to.
        embed_color (int): The color of the embed. Defaults to 0x00AAFF.
        success_message (str): The success message to send to the user.
        error_message (str): The error message when no recipients are configured.
    """

    embed_title: str
    """The title of the suggestion embed."""
    role_name: str | None
    """The role name to get responsible members."""
    channel_name: str | None
    """The channel name to send the suggestion to."""
    success_message: str
    """The success message to send to the user."""
    error_message: str
    """The error message when no recipients are configured."""
    embed_color: int = 0x00AAFF


_CODIR_SUGGESTION_CONFIG = SuggestionConfig(
    embed_title="Nouvelle suggestion pour le CoDir",
    role_name=None,
    channel_name="codir-general",
    success_message="Suggestion envoyée au CoDir. Merci !",
    error_message="Aucun salon 'codir-general' n'est configuré pour recevoir les suggestions.",
)

_BUREAU_SUGGESTION_CONFIG = SuggestionConfig(
    embed_title="Nouvelle suggestion pour le Bureau",
    role_name=None,
    channel_name="bureau-general",
    success_message="Suggestion envoyée au Bureau. Merci !",
    error_message="Aucun salon 'bureau-general' n'est configuré pour recevoir les suggestions.",
)

_COMMUNICATION_SUGGESTION_CONFIG = SuggestionConfig(
    embed_title="Nouvelle suggestion pour le Pôle Communication",
    role_name=RoleNames.COMMUNICATION_MANAGER,
    channel_name="pole-communication",
    success_message="Suggestion envoyée au Pôle Communication. Merci !",
    error_message="Aucun·e Respo Communication et aucun salon 'pole-communication'"
    " ne sont configurés pour recevoir les suggestions.",
)

_EVENT_SUGGESTION_CONFIG = SuggestionConfig(
    embed_title="Nouvelle suggestion pour le Pôle Événements",
    role_name=RoleNames.EVENTS_MANAGER,
    channel_name="pole-event",
    success_message="Suggestion envoyée au Pôle Events. Merci !",
    error_message="Aucun·e Respo Events et aucun salon 'pole-events' ne sont configurés pour recevoir les suggestions.",
)

_TRAINING_SUGGESTION_CONFIG = SuggestionConfig(
    embed_title="Nouvelle suggestion de formation",
    role_name=RoleNames.TRAININGS_MANAGER,
    channel_name="pole-formation",
    success_message="Suggestion envoyée au Pôle Formation. Merci !",
    error_message="Aucun·e Respo Formation et aucun salon 'pole-formation' ne sont configurés pour recevoir les suggestions.",
)

_IT_SUGGESTION_CONFIG = SuggestionConfig(
    embed_title="Nouvelle suggestion pour le Pôle Numérique",
    role_name=RoleNames.DIGITAL_MANAGER,
    channel_name="pole-numerique",
    success_message="Suggestion envoyée au Pôle Numérique. Merci !",
    error_message="Aucun·e Respo Numérique et aucun salon 'pole-numerique' ne sont configurés pour recevoir les suggestions.",
)

_PARTNERSHIPS_SUGGESTION_CONFIG = SuggestionConfig(
    embed_title="Nouvelle suggestion pour le Pôle Partenariat",
    role_name=RoleNames.PARTNERSHIPS_MANAGER,
    channel_name="pole-partenariat",
    success_message="Suggestion envoyée au Pôle Partenariat. Merci !",
    error_message="Aucun·e Respo Partenariat et aucun salon 'pole-partenariat'"
    " ne sont configurés pour recevoir les suggestions.",
)

_PROJECTS_SUGGESTION_CONFIG = SuggestionConfig(
    embed_title="Nouvelle suggestion pour le Pôle Projets",
    role_name=RoleNames.PROJECTS_MANAGER,
    channel_name=None,
    success_message="Suggestion envoyée au Pôle Projets. Merci !",
    error_message="Aucun·e Respo Projets n'est configuré·e pour recevoir les suggestions.",
)

SUGGESTION_OPTIONS: dict[str, tuple[str, SuggestionConfig, str]] = {
    "codir": ("CoDir", _CODIR_SUGGESTION_CONFIG, "suggest.to_codir"),
    "bureau": ("Bureau", _BUREAU_SUGGESTION_CONFIG, "suggest.to_bureau"),
    "communication": ("Pôle Communication", _COMMUNICATION_SUGGESTION_CONFIG, "suggest.to_communication"),
    "events": ("Pôle Event", _EVENT_SUGGESTION_CONFIG, "suggest.to_event"),
    "formation": ("Pôle Formation", _TRAINING_SUGGESTION_CONFIG, "suggest.formation"),
    "numerique": ("Pôle Numérique", _IT_SUGGESTION_CONFIG, "suggest.it_feature"),
    "partenariat": ("Pôle Partenariat", _PARTNERSHIPS_SUGGESTION_CONFIG, "suggest.to_partenariat"),
    "projets": ("Pôle Projet", _PROJECTS_SUGGESTION_CONFIG, "suggest.to_projet"),
}
