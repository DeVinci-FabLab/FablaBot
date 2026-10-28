"""Models for user management: the recorded end of a temporary admin grant."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping


@dataclass(frozen=True, slots=True)
class OpExpiry:
    """When a member's temporary admin role ends, and where to say so.

    Attributes:
        expires_at (datetime): Timezone-aware time at which the role is removed.
        channel_id (int): ID of the channel where the op was run; the end notice is posted there.
    """

    expires_at: datetime
    """Timezone-aware time at which the role is removed."""
    channel_id: int
    """ID of the channel where the op was run."""

    def to_dict(self) -> dict[str, Any]:
        """Convert the expiry to its JSON form.

        Returns:
            dict[str, Any]: ``expires_at`` as an ISO 8601 string with its offset, and ``channel_id``.
        """
        return {"expires_at": self.expires_at.isoformat(), "channel_id": self.channel_id}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> OpExpiry:
        """Read an expiry from its JSON form, refusing anything incomplete.

        Args:
            data (Mapping[str, Any]): The stored entry.

        Returns:
            OpExpiry: The expiry.

        Raises:
            ValueError: When the time is missing, malformed or has no timezone.
            TypeError: When the channel ID is not an int.
        """
        expires_at = datetime.fromisoformat(str(data["expires_at"])) if "expires_at" in data else None
        if expires_at is None or expires_at.tzinfo is None:
            msg = f"expiry needs a timezone-aware expires_at: {data!r}"
            raise ValueError(msg)
        channel_id = data.get("channel_id")
        if not isinstance(channel_id, int) or isinstance(channel_id, bool):
            msg = f"expiry needs an int channel_id: {data!r}"
            raise TypeError(msg)
        return cls(expires_at=expires_at, channel_id=channel_id)
