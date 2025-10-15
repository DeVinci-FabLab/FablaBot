"""Data models for formation management."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

PARIS_TZ = ZoneInfo("Europe/Paris")


@dataclass
class Formation:
    """Single Formation entry in a draft.

    Attributes:
        emoji (str): Single emoji representing the formation.
        name (str): Name of the formation.
        trainer_mention (str): Mention of the trainer.
        start_iso (str): Start date/time in ISO format (timezone-aware if possible).
        duration (str): Duration of the formation in text format.
        seats (int): Number of seats available for the formation.
        description (str): Brief description of the formation.
        registered_users (list[dict[str, Any]]): Ordered list of registered users metadata.
        waitlisted_users (list[dict[str, Any]]): Ordered list of waitlisted users metadata.
        notified (bool): Whether the trainer has been notified for this formation.
    """

    emoji: str
    """Single emoji representing the formation."""
    name: str
    """Name of the formation."""
    trainer_mention: str
    """Mention of the trainer."""
    start_iso: str
    """Start date/time in ISO format."""
    duration: str
    """Duration of the formation in text format."""
    seats: int
    """Number of seats available for the formation."""
    description: str = ""
    """Brief description of the formation."""
    registered_users: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    """Registered users metadata (order preserved)."""
    waitlisted_users: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    """Waitlisted users metadata (order preserved)."""
    notified: bool = False
    """Whether the trainer has been notified for this formation."""

    @property
    def start_dt(self) -> datetime:
        """Get the start date/time as a datetime object.

        Returns:
            datetime: The start date/time as a datetime object.
        """
        return datetime.fromisoformat(self.start_iso).astimezone(PARIS_TZ)

    def to_dict(self) -> dict[str, Any]:
        """Convert the Formation instance to a dictionary.

        Returns:
            dict[str, Any]: The dictionary representation of the Formation.
        """
        return asdict(self)


@dataclass
class Draft:
    """Draft message containing formations.

    Attributes:
        header (str): The header text for the message.
        role_id (int): The role ID to mention.
        intro (str): Introduction text.
        fms (list[dict[str, Any]]): List of formations as dictionaries.
        end (str): Ending text.
    """

    header: str
    role_id: int
    intro: str
    fms: list[dict[str, Any]]
    end: str

    def to_dict(self) -> dict[str, Any]:
        """Convert the Draft instance to a dictionary.

        Returns:
            dict[str, Any]: The dictionary representation of the Draft.
        """
        return asdict(self)


@dataclass
class PublishedMessage:
    """Published formations message metadata.

    Attributes:
        message_id (int): The Discord message ID.
        channel_id (int): The Discord channel ID.
        message (dict[str, Any]): The message payload with formations.
    """

    message_id: int
    channel_id: int
    message: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Convert the PublishedMessage instance to a dictionary.

        Returns:
            dict[str, Any]: The dictionary representation of the PublishedMessage.
        """
        return asdict(self)


@dataclass
class ReactionEvent:
    """A single reaction event log entry.

    Attributes:
        message_id (int): The Discord message ID.
        user_id (int): The Discord user ID.
        user_name (str): The Discord user name.
        emoji (str): The emoji used in the reaction.
        action (str): The action taken ('add' or 'remove').
        ts_iso (str): The timestamp in ISO format.
    """

    message_id: int
    user_id: int
    user_name: str | None
    emoji: str
    action: str
    ts_iso: str

    def to_dict(self) -> dict[str, Any]:
        """Convert the ReactionEvent instance to a dictionary.

        Returns:
            dict[str, Any]: The dictionary representation of the ReactionEvent.
        """
        return asdict(self)
