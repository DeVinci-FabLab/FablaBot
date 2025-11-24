"""Data models for messages and related configurations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from fablabot.helpers.constants import RoleNames

ANONYMOUS_ICON_URL = "https://e7.pngegg.com/pngimages/84/165/png-clipart-united-states-avatar-organization-information-user-avatar-service-computer-wallpaper-thumbnail.png"


@dataclass
class EasterEggTrigger:
    """Structure for defining an Easter egg trigger."""

    keywords: list[str]
    """List of keywords that trigger the Easter egg."""
    response: str
    """Response to send when the Easter egg is triggered."""
    probability: float
    """Probability of triggering the Easter egg when keywords are found."""


# TODO regex pour trivial sauf si c'est le gif, pour feu avec espace ou rien derrière mais pas lettre
EASTER_EGGS: list[EasterEggTrigger] = [
    EasterEggTrigger(
        ["est pas sorcier"],
        "https://tenor.com/view/c-est-pas-sorcier-c%27est-pas-sorcier-jamy-fred-sabine-gif-499752155684427888",
        1.0,
    ),
    EasterEggTrigger(
        ["autiste", "autisme"],
        "https://tenor.com/view/autism-autistic-spongebob-i%27m-autistic-spongebob-meme-gif-990745265488627503",
        1 / 3,
    ),
    EasterEggTrigger(
        ["contre nature"],
        "https://tenor.com/view/lpj-gif-7210529",
        1.0,
    ),
    EasterEggTrigger(
        ["t'es pas net", "baptiste"],
        "https://tenor.com/view/baptiste-feu-fire-gif-13214452",
        1.0,
    ),
    EasterEggTrigger(
        ["feu", "brûl", "brul"],
        "https://tenor.com/view/elmo-hello-elmo-rise-elmo-hell-gif-3989603362345473118",
        1 / 3,
    ),
    EasterEggTrigger(
        ["trivial"],
        "https://tenor.com/view/didier-gossard-didier-gossard-cest-trivial-gif-18897720",
        1.0,
    ),
]


@dataclass
class MsgReactionEvent:
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
    def from_dict(cls, data: dict[str, Any]) -> MsgReactionEvent:
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
    reactions: list[MsgReactionEvent]

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
            reactions=[MsgReactionEvent.from_dict(r) for r in data.get("reactions", [])],
        )


# region ====== Suggestion Configurations ======


@dataclass
class SuggestionConfig:
    """Configuration for a suggestion type.

    Attributes:
        label (str): The label of the suggestion type.
        command_name (str): The command name for logging purposes.
        embed_title (str): The title of the suggestion embed.
        role_name (str | None): The role name to get responsible members.
        channel_name (str | None): The channel name to send the suggestion to.
        embed_color (int): The color of the embed. Defaults to 0x00AAFF.
        success_message (str): The success message to send to the user.
        error_message (str): The error message when no recipients are configured.
    """

    label: str
    """The label of the suggestion type."""
    command_name: str
    """The command name for logging purposes."""
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
    label="CoDir",
    command_name="suggest.to_codir",
    embed_title="Nouvelle suggestion pour le CoDir",
    role_name=None,
    channel_name="codir-general",
    success_message="Suggestion envoyée au CoDir. Merci !",
    error_message="Aucun salon 'codir-general' n'est configuré pour recevoir les suggestions.",
)

_BUREAU_SUGGESTION_CONFIG = SuggestionConfig(
    label="Bureau",
    command_name="suggest.to_bureau",
    embed_title="Nouvelle suggestion pour le Bureau",
    role_name=None,
    channel_name="bureau-general",
    success_message="Suggestion envoyée au Bureau. Merci !",
    error_message="Aucun salon 'bureau-general' n'est configuré pour recevoir les suggestions.",
)

_COMMUNICATION_SUGGESTION_CONFIG = SuggestionConfig(
    label="Pôle Communication",
    command_name="suggest.to_communication",
    embed_title="Nouvelle suggestion pour le Pôle Communication",
    role_name=RoleNames.COMMUNICATION_MANAGER,
    channel_name="pole-communication",
    success_message="Suggestion envoyée au Pôle Communication. Merci !",
    error_message="Aucun·e Respo Communication et aucun salon 'pole-communication'"
    " ne sont configurés pour recevoir les suggestions.",
)

_EVENTS_SUGGESTION_CONFIG = SuggestionConfig(
    label="Pôle Event",
    command_name="suggest.to_events",
    embed_title="Nouvelle suggestion pour le Pôle Event",
    role_name=RoleNames.EVENTS_MANAGER,
    channel_name="pole-event",
    success_message="Suggestion envoyée au Pôle Event. Merci !",
    error_message="Aucun·e Respo Event et aucun salon 'pole-event' ne sont configurés pour recevoir les suggestions.",
)

_TRAINING_SUGGESTION_CONFIG = SuggestionConfig(
    label="Pôle Formation",
    command_name="suggest.to_trainings",
    embed_title="Nouvelle suggestion pour le Pôle Formation",
    role_name=RoleNames.TRAININGS_MANAGER,
    channel_name="pole-formation",
    success_message="Suggestion envoyée au Pôle Formation. Merci !",
    error_message="Aucun·e Respo Formation et aucun salon 'pole-formation' ne sont configurés pour recevoir les suggestions.",
)

_IT_SUGGESTION_CONFIG = SuggestionConfig(
    label="Pôle Numérique",
    command_name="suggest.to_it",
    embed_title="Nouvelle suggestion pour le Pôle Numérique",
    role_name=RoleNames.DIGITAL_MANAGER,
    channel_name="pole-numerique",
    success_message="Suggestion envoyée au Pôle Numérique. Merci !",
    error_message="Aucun·e Respo Numérique et aucun salon 'pole-numerique' ne sont configurés pour recevoir les suggestions.",
)

_PARTNERSHIPS_SUGGESTION_CONFIG = SuggestionConfig(
    label="Pôle Partenariat",
    command_name="suggest.to_partnerships",
    embed_title="Nouvelle suggestion pour le Pôle Partenariat",
    role_name=RoleNames.PARTNERSHIPS_MANAGER,
    channel_name="pole-partenariat",
    success_message="Suggestion envoyée au Pôle Partenariat. Merci !",
    error_message="Aucun·e Respo Partenariat et aucun salon 'pole-partenariat'"
    " ne sont configurés pour recevoir les suggestions.",
)

_PROJECTS_SUGGESTION_CONFIG = SuggestionConfig(
    label="Pôle Projet",
    command_name="suggest.to_projects",
    embed_title="Nouvelle suggestion pour le Pôle Projet",
    role_name=RoleNames.PROJECTS_MANAGER,
    channel_name=None,
    success_message="Suggestion envoyée au Pôle Projet. Merci !",
    error_message="Aucun·e Respo Projet n'est configuré·e pour recevoir les suggestions.",
)

SUGGESTION_OPTIONS: dict[str, SuggestionConfig] = {
    "codir": _CODIR_SUGGESTION_CONFIG,
    "bureau": _BUREAU_SUGGESTION_CONFIG,
    "communication": _COMMUNICATION_SUGGESTION_CONFIG,
    "event": _EVENTS_SUGGESTION_CONFIG,
    "formation": _TRAINING_SUGGESTION_CONFIG,
    "numerique": _IT_SUGGESTION_CONFIG,
    "partenariat": _PARTNERSHIPS_SUGGESTION_CONFIG,
    "projet": _PROJECTS_SUGGESTION_CONFIG,
}

# endregion ====== Suggestion Configurations ======
