"""Models for the attendance check of a training."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime


class AlertAction(StrEnum):
    """What the Respo Formation answers to an alert about an attendance check nobody made."""

    RELAUNCH = "relaunch"
    IGNORE = "ignore"
    CANCEL = "cancel"
    DONE = "done"


@dataclass(frozen=True)
class Appel:
    """The attendance check of one training, with the announcement it belongs to.

    Attributes:
        id (int): Its row ID.
        guild_id (int): The server.
        message_id (int): The published announcement.
        channel_id (int | None): The announcement's channel.
        position (int): The training's place in the announcement.
        name (str): The training's name, read from the announcement.
        duration (str): The training's duration as typed, read from the announcement.
        excusable (bool): Whether absences are excusable for the training, read from the announcement.
        trainer_id (int | None): The trainer, when the announcement names one.
        starts_at (datetime): The start.
        ends_at (datetime): The end of the slot.
        closes_at (datetime): When the check stops taking changes.
        thread_id (int | None): The thread made when the trainer's DMs were closed.
        validated_by (int | None): Who last validated it.
        validated_at (datetime | None): When.
        present_count (int | None): Present members at the last validation.
        absent_count (int | None): Absent members at the last validation.
        alert_sent (bool): Whether the Respo Formation was told nobody made it.
        handled_by (int | None): Who answered the alert.
        handled_at (datetime | None): When.
        handled_action (AlertAction | None): The answer.
    """

    id: int
    guild_id: int
    message_id: int
    channel_id: int | None
    position: int
    name: str
    duration: str
    excusable: bool
    trainer_id: int | None
    starts_at: datetime
    ends_at: datetime
    closes_at: datetime
    thread_id: int | None
    validated_by: int | None
    validated_at: datetime | None
    present_count: int | None
    absent_count: int | None
    alert_sent: bool
    handled_by: int | None
    handled_at: datetime | None
    handled_action: AlertAction | None

    @property
    def cancelled(self) -> bool:
        """Whether the training was cancelled."""
        return self.handled_action is AlertAction.CANCEL

    def is_open(self, now: datetime) -> bool:
        """Tell whether the check still takes changes.

        Args:
            now (datetime): The current time.

        Returns:
            bool: True until it is cancelled or its closing time is reached.
        """
        return not self.cancelled and now < self.closes_at


@dataclass(frozen=True)
class Registrant:
    """A member registered to a training.

    Attributes:
        user_id (int): The member's ID.
        username (str): The handle stored at sign-up.
    """

    user_id: int
    username: str


@dataclass(frozen=True)
class Validation:
    """What one validation saved, read in the same transaction as the save.

    Attributes:
        appel (Appel): The check as it stands after the save.
        here (list[int]): The registered members saved present, in their order.
        away (list[int]): The registered members saved absent, in their order.
        first (bool): Whether the check was never validated before.
        alert_closed (bool): Whether this save answered an open alert done, in the same transaction.
    """

    appel: Appel
    here: list[int]
    away: list[int]
    first: bool
    alert_closed: bool = False
