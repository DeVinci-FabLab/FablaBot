"""Data models for formation management."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Any

from fablabot.helpers.constants import PARIS_TZ


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

    def __copy__(self) -> Formation:
        """Create a shallow copy of the Formation instance.

        Returns:
            Formation: A shallow copy of the Formation instance.
        """
        return Formation(**self.to_dict())

    def __deepcopy__(self, memo: dict[int, Any] | None = None) -> Formation:
        """Create a deep copy of the Formation instance.

        Args:
            memo (dict[int, Any] | None): Memoization dictionary for deep copy.

        Returns:
            Formation: A deep copy of the Formation instance.
        """
        if memo is None:
            memo = {}
        return Formation(
            emoji=self.emoji,
            name=self.name,
            trainer_mention=self.trainer_mention,
            start_iso=self.start_iso,
            duration=self.duration,
            seats=self.seats,
            description=self.description,
            excusable=self.excusable,
            registered_users=deepcopy(self.registered_users, memo),
            waitlisted_users=deepcopy(self.waitlisted_users, memo),
            notified_hour_before=self.notified_hour_before,
            notified_at_start=self.notified_at_start,
        )


@dataclass
class FmMessageDraft:
    """Draft message containing formations.

    Attributes:
        header (str): The header text for the message.
        role_id (int): The role ID to mention.
        intro (str): Introduction text.
        fms (list[Formation]): List of Formation objects.
        end (str): Ending text.
        request_forms_url (str): URL for the formation request form. Defaults to the Office Forms link.
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
    request_forms_url: str = "https://forms.office.com/e/MqVdQujzjf"
    """URL for the formation request form."""

    def to_dict(self) -> dict[str, Any]:
        """Convert the FmMessageDraft instance to a dictionary.

        Returns:
            dict[str, Any]: The dictionary representation of the FmMessageDraft.
        """
        return {
            "header": self.header,
            "role_id": self.role_id,
            "intro": self.intro,
            "fms": [fm.to_dict() for fm in self.fms],
            "end": self.end,
            "request_forms_url": self.request_forms_url,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FmMessageDraft:
        """Create a FmMessageDraft instance from a dictionary.

        Args:
            data (dict[str, Any]): Dictionary containing draft data.

        Returns:
            FmMessageDraft: The FmMessageDraft instance.
        """
        return cls(
            header=data.get("header", ""),
            role_id=data.get("role_id", 0),
            intro=data.get("intro", ""),
            fms=[Formation(**fm) for fm in data.get("fms", [])],
            end=data.get("end", ""),
            request_forms_url=data.get("request_forms_url", "https://forms.office.com/e/MqVdQujzjf"),
        )


@dataclass
class PublishedMessage:
    """Published formations message metadata.

    Attributes:
        message_id (int): The Discord message ID.
        channel_id (int): The Discord channel ID.
        message (FmMessageDraft): The message payload with formations.
    """

    message_id: int
    """The Discord message ID."""
    channel_id: int
    """The Discord channel ID."""
    message: FmMessageDraft
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
            message=FmMessageDraft.from_dict(data.get("message", {})),
        )


class FmCommand(Enum):
    """Enumeration of formation management commands.

    Attributes:
        EDIT (FmCommand): Edit an existing formation in the draft.
        REMOVE (FmCommand): Remove a formation from the draft.
    """

    EDIT = auto()
    """Edit an existing formation in the draft."""
    REMOVE = auto()
    """Remove a formation from the draft."""
