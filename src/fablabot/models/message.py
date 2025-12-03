"""Data models for messages and related configurations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
import re
from typing import Any, Literal

from fablabot.helpers.constants import RoleNames

ANONYMOUS_ICON_URL = "https://e7.pngegg.com/pngimages/84/165/png-clipart-united-states-avatar-organization-information-user-avatar-service-computer-wallpaper-thumbnail.png"


@dataclass
class EasterEggTrigger:
    """Structure for defining an Easter egg trigger."""

    keywords: list[re.Pattern]
    """List of keywords that trigger the Easter egg."""
    response: str
    """Response to send when the Easter egg is triggered."""
    probability: float
    """Probability of triggering the Easter egg when keywords are found."""
    reaction: str | None = None
    """Optional reaction to add when the Easter egg is triggered."""


EASTER_EGGS: list[EasterEggTrigger] = [
    EasterEggTrigger(
        [re.compile(r"est pas sorcier", flags=re.IGNORECASE)],
        "https://tenor.com/view/c-est-pas-sorcier-c%27est-pas-sorcier-jamy-fred-sabine-gif-499752155684427888",
        1.0,
    ),
    EasterEggTrigger(
        [re.compile(r"autis[tm]e", flags=re.IGNORECASE)],
        "https://tenor.com/view/autism-autistic-spongebob-i%27m-autistic-spongebob-meme-gif-990745265488627503",
        1 / 3,
    ),
    EasterEggTrigger(
        [re.compile(r"contre nature", flags=re.IGNORECASE)],
        "https://tenor.com/view/lpj-gif-7210529",
        1.0,
    ),
    EasterEggTrigger(
        [re.compile(r"pas net", flags=re.IGNORECASE)],
        "https://tenor.com/view/baptiste-feu-fire-gif-13214452",
        1.0,
    ),
    EasterEggTrigger(
        [re.compile(r"baptiste", flags=re.IGNORECASE)],
        "https://tenor.com/view/baptiste-feu-fire-gif-13214452",
        0.25,
    ),
    EasterEggTrigger(
        [
            re.compile(r"feu\b", flags=re.IGNORECASE),
            re.compile(r"br[uû]l", flags=re.IGNORECASE),
        ],
        "https://tenor.com/view/elmo-hello-elmo-rise-elmo-hell-gif-3989603362345473118",
        0.5,
    ),
    EasterEggTrigger(
        [re.compile(r"trivial($|^-|\s)", flags=re.IGNORECASE)],
        "https://tenor.com/view/didier-gossard-didier-gossard-cest-trivial-gif-18897720",
        1.0,
    ),
    EasterEggTrigger(
        [re.compile(r"monster", flags=re.IGNORECASE)],
        "Nous vous recommandons Royale Taurine ou Red Bull, c'est meilleur pour votre santé mentale",
        0.01,
        "<:MonsterKiwi:1438645773947506829>",
    ),
    EasterEggTrigger(
        [re.compile(r"monster", flags=re.IGNORECASE)],
        "",
        0.2,
        "<:MonsterKiwi:1438645773947506829>",
    ),
    EasterEggTrigger(
        [re.compile(r"(\s|^)quoi\s*\?*$", flags=re.IGNORECASE)],
        "FEUR",
        0.5,
    ),
]


MsgActionType = Literal["channel", "user_dm", "role_dm"]
"""Supported reaction action types for message management."""


class MsgCommand(Enum):
    """Enumeration of tracked message commands.

    Attributes:
        LINK: Link a new reaction-based action to a tracked message.
        UNLINK: Unlink a configured reaction from a tracked message.
        STOP: Stop tracking a tracked message.
        EXPORT: Export tracked messages to a JSON file.
    """

    LINK = auto()
    """Link a new reaction-based action to a tracked message."""
    UNLINK = auto()
    """Unlink a configured reaction from a tracked message."""
    STOP = auto()
    """Stop tracking a tracked message."""
    EXPORT = auto()
    """Export tracked messages to a JSON file."""


@dataclass
class MsgReactionEvent:
    """Configuration for a reaction-based action.

    Attributes:
        emoji (str): The emoji that triggers this action.
        action_type (Literal["channel", "user_dm", "role_dm"]): The type of action to perform.
        message_content (str): The message to send when the reaction is triggered.
        target_id (int | None): The ID of the target (channel_id, user_id, or role_id).
        target_name (str | None): Human-readable name of the target for display purposes.
        message_id (int | None): The ID of the message this reaction is associated with.
    """

    emoji: str
    action_type: MsgActionType
    message_content: str
    target_id: int | None = None
    target_name: str | None = None
    message_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "emoji": self.emoji,
            "action_type": self.action_type,
            "message_content": self.message_content,
            "target_id": self.target_id,
            "target_name": self.target_name,
            "message_id": self.message_id,
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
            message_id=data.get("message_id"),
        )

    def __copy__(self) -> MsgReactionEvent:
        """Create a shallow copy of the MsgReactionEvent."""
        return MsgReactionEvent(
            emoji=self.emoji,
            action_type=self.action_type,
            message_content=self.message_content,
            target_id=self.target_id,
            target_name=self.target_name,
            message_id=self.message_id,
        )


@dataclass
class MessageDraft:
    """A draft message with reaction-based actions (pre-publication).

    Attributes:
        content (str): The content of the draft message.
        reactions (list[MsgReactionEvent]): List of reaction-based actions.
    """

    content: str
    reactions: list[MsgReactionEvent]

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization.

        Returns:
            dict[str, Any]: Dictionary representation of the MessageDraft.
        """
        return {
            "content": self.content,
            "reactions": [r.to_dict() for r in self.reactions],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MessageDraft:
        """Create from dictionary.

        Args:
            data (dict[str, Any]): Dictionary representation of a MessageDraft.

        Returns:
            MessageDraft: The created MessageDraft instance.
        """
        return cls(
            content=data.get("content", ""),
            reactions=[MsgReactionEvent.from_dict(r) for r in data.get("reactions", [])],
        )


@dataclass
class TrackedMessage:
    """Tracked message with optional reaction actions and metadata.

    Attributes:
        message_id (int): The ID of the tracked message.
        channel_id (int): The ID of the channel containing the message.
        content (str): The content of the tracked message.
        reactions (list[MsgReactionEvent]): List of reaction-based actions.
        created_by (int | None): ID of the user who created the tracked message.
        created_at_iso (str | None): ISO timestamp of when the tracked message was created.
    """

    message_id: int
    channel_id: int
    content: str
    reactions: list[MsgReactionEvent]
    created_by: int | None = None
    created_at_iso: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "message_id": self.message_id,
            "channel_id": self.channel_id,
            "content": self.content,
            "reactions": [r.to_dict() for r in self.reactions],
            "created_by": self.created_by,
            "created_at_iso": self.created_at_iso,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TrackedMessage:
        """Create from dictionary."""
        return cls(
            message_id=int(data["message_id"]),
            channel_id=int(data["channel_id"]),
            content=data.get("content", ""),
            reactions=[MsgReactionEvent.from_dict(r) for r in data.get("reactions", [])],
            created_by=data.get("created_by"),
            created_at_iso=data.get("created_at_iso"),
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
