"""Common models used across the FablaBot application."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any


class ReactionAction(str, Enum):
    """Enumeration of possible reaction actions.

    Attributes:
        ADD (str): Represents adding a reaction.
        REMOVE (str): Represents removing a reaction.
    """

    ADD = "add"
    """Represents adding a reaction."""
    REMOVE = "remove"
    """Represents removing a reaction."""


@dataclass
class ReactionEvent:
    """A single reaction event log entry.

    Attributes:
        message_id (int): The Discord message ID.
        user_id (int): The Discord user ID.
        user_name (str | None): The Discord user name.
        emoji (str): The emoji used in the reaction.
        action (ReactionAction): The action taken ('add' or 'remove').
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
    action: ReactionAction
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
            action=ReactionAction(data.get("action", ReactionAction.ADD)),
            ts_iso=data.get("ts_iso", ""),
        )
