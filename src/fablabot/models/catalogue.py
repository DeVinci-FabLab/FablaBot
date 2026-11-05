"""Data model of the training catalogue: the templates a server reuses for its sessions."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TemplateFields:
    """What a template holds, as its form gives it.

    Attributes:
        name (str): The training's name, unique on its server whatever the case.
        duration (str): The duration, shown as typed.
        seats (int): The seats of a session, 1 to 500.
        description (str): The description, possibly empty.
        excusable (bool): Whether an absence may be excused.
    """

    name: str
    duration: str
    seats: int
    description: str
    excusable: bool


@dataclass(frozen=True)
class TrainingTemplate:
    """A training a server gives again and again, typed once.

    Attributes:
        id (int): The template's row ID.
        guild_id (int): The server it belongs to.
        fields (TemplateFields): What it holds.
    """

    id: int
    guild_id: int
    fields: TemplateFields
