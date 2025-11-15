"""Data models for formation management."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Literal

from fablabot.cogs.helpers.constants import PARIS_TZ


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
        description (str): Brief description of the formation. Defaults to "".
        excusable (bool): Whether absences are excusable for this formation. Defaults to True.
        registered_users (list[dict[str, Any]]): Ordered list of registered users metadata.
        waitlisted_users (list[dict[str, Any]]): Ordered list of waitlisted users metadata.
        notified_hour_before (bool): Whether the trainer has been notified one hour before the formation. Defaults to False.
        notified_at_start (bool): Whether the trainer has been notified at the start time of the formation. Defaults to False.
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
    excusable: bool = True
    """Whether absences are excusable for this formation."""
    registered_users: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    """Registered users metadata (order preserved)."""
    waitlisted_users: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    """Waitlisted users metadata (order preserved)."""
    notified_hour_before: bool = False
    """Whether the trainer has been notified one hour before the formation."""
    notified_at_start: bool = False
    """Whether the trainer has been notified at the formation start time."""

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
        fms (list[Formation]): List of Formation objects.
        end (str): Ending text.
    """

    header: str
    """The header text for the message."""
    role_id: int
    """The role ID to mention."""
    intro: str
    """Introduction text."""
    fms: list[Formation]
    """List of Formation objects."""
    end: str
    """Ending text."""

    def to_dict(self) -> dict[str, Any]:
        """Convert the Draft instance to a dictionary.

        Returns:
            dict[str, Any]: The dictionary representation of the Draft.
        """
        return {
            "header": self.header,
            "role_id": self.role_id,
            "intro": self.intro,
            "fms": [fm.to_dict() for fm in self.fms],
            "end": self.end,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Draft:
        """Create a Draft instance from a dictionary.

        Args:
            data (dict[str, Any]): Dictionary containing draft data.

        Returns:
            Draft: The Draft instance.
        """
        return cls(
            header=data.get("header", ""),
            role_id=data.get("role_id", 0),
            intro=data.get("intro", ""),
            fms=[Formation(**fm) for fm in data.get("fms", [])],
            end=data.get("end", ""),
        )


@dataclass
class PublishedMessage:
    """Published formations message metadata.

    Attributes:
        message_id (int): The Discord message ID.
        channel_id (int): The Discord channel ID.
        message (Draft): The message payload with formations.
    """

    message_id: int
    """The Discord message ID."""
    channel_id: int
    """The Discord channel ID."""
    message: Draft
    """The message payload with formations."""

    def to_dict(self) -> dict[str, Any]:
        """Convert the PublishedMessage instance to a dictionary.

        Returns:
            dict[str, Any]: The dictionary representation of the PublishedMessage.
        """
        return {
            "message_id": self.message_id,
            "channel_id": self.channel_id,
            "message": self.message.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PublishedMessage:
        """Create a PublishedMessage instance from a dictionary.

        Args:
            data (dict[str, Any]): Dictionary containing published message data.

        Returns:
            PublishedMessage: The PublishedMessage instance.
        """
        return cls(
            message_id=data.get("message_id", 0),
            channel_id=data.get("channel_id", 0),
            message=Draft.from_dict(data.get("message", {})),
        )


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
    """The Discord message ID."""
    user_id: int
    """The Discord user ID."""
    user_name: str | None
    """The Discord user name."""
    emoji: str
    """The emoji used in the reaction."""
    action: Literal["add", "remove"]
    """The action taken ('add' or 'remove')."""
    ts_iso: str
    """The timestamp in ISO format."""

    def to_dict(self) -> dict[str, Any]:
        """Convert the ReactionEvent instance to a dictionary.

        Returns:
            dict[str, Any]: The dictionary representation of the ReactionEvent.
        """
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReactionEvent:
        """Create a ReactionEvent instance from a dictionary.

        Args:
            data (dict[str, Any]): Dictionary containing reaction event data.

        Returns:
            ReactionEvent: The ReactionEvent instance.
        """
        return cls(
            message_id=data.get("message_id", 0),
            user_id=data.get("user_id", 0),
            user_name=data.get("user_name"),
            emoji=data.get("emoji", ""),
            action=data.get("action", "add"),
            ts_iso=data.get("ts_iso", ""),
        )
