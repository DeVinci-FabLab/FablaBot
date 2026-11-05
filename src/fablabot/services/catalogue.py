"""Training templates of each server, in rows. Every read and write is scoped by the server's ID."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

from fablabot import db
from fablabot.models.catalogue import TemplateFields, TrainingTemplate

if TYPE_CHECKING:
    from collections.abc import Iterable


class TemplateNameTakenError(Exception):
    """Another template of the server already has this name, whatever the case."""


def _template(row: sqlite3.Row) -> TrainingTemplate:
    """Build a template from its row."""
    fields = TemplateFields(
        name=row["name"],
        duration=row["duration"],
        seats=row["seats"],
        description=row["description"],
        excusable=bool(row["excusable"]),
    )
    return TrainingTemplate(id=row["id"], guild_id=row["guild_id"], fields=fields)


def _values(fields: TemplateFields) -> tuple[str, str, int, str, int]:
    """Give the column values of what a template holds, in table order."""
    return (fields.name, fields.duration, fields.seats, fields.description, int(fields.excusable))


def _rows(rows: Iterable[sqlite3.Row]) -> list[TrainingTemplate]:
    """Build the templates of rows, in their order."""
    return [_template(row) for row in rows]


def list_templates(guild_id: int) -> list[TrainingTemplate]:
    """List the templates of a server by name, whatever the case.

    Args:
        guild_id (int): The ID of the server.

    Returns:
        list[TrainingTemplate]: Its templates.
    """
    rows = db.connection().execute(
        "SELECT * FROM training_template WHERE guild_id = ? ORDER BY name COLLATE NOCASE, id", (guild_id,)
    )
    return _rows(rows)


def get_template(guild_id: int, template_id: int) -> TrainingTemplate | None:
    """Read one template of a server; another server's template reads as missing.

    Args:
        guild_id (int): The ID of the server.
        template_id (int): The template's ID.

    Returns:
        TrainingTemplate | None: The template, or None.
    """
    row = (
        db.connection()
        .execute("SELECT * FROM training_template WHERE guild_id = ? AND id = ?", (guild_id, template_id))
        .fetchone()
    )
    return _template(row) if row else None


def find_by_name(guild_id: int, name: str) -> TrainingTemplate | None:
    """Read the template of a server with this name, whatever the case.

    Args:
        guild_id (int): The ID of the server.
        name (str): The name.

    Returns:
        TrainingTemplate | None: The template, or None.
    """
    row = (
        db.connection()
        .execute("SELECT * FROM training_template WHERE guild_id = ? AND name = ? COLLATE NOCASE", (guild_id, name.strip()))
        .fetchone()
    )
    return _template(row) if row else None


def search_templates(guild_id: int, text: str, *, limit: int = 25) -> list[TrainingTemplate]:
    """List the templates of a server whose name holds the text, by name.

    Args:
        guild_id (int): The ID of the server.
        text (str): What was typed so far; empty lists the first templates.
        limit (int): At most this many, Discord's 25 choices by default.

    Returns:
        list[TrainingTemplate]: The templates found.
    """
    folded = text.strip().casefold()
    # Python folds accents' case, which SQLite's NOCASE does not; a server has tens of templates, not thousands.
    return [template for template in list_templates(guild_id) if folded in template.fields.name.casefold()][:limit]


def create_template(guild_id: int, fields: TemplateFields) -> TrainingTemplate:
    """Store a new template of a server.

    Args:
        guild_id (int): The ID of the server.
        fields (TemplateFields): What the template holds.

    Returns:
        TrainingTemplate: The stored template.

    Raises:
        TemplateNameTakenError: If another template of the server has this name, whatever the case.
    """
    try:
        with db.transaction() as conn:
            template_id = conn.execute(
                "INSERT INTO training_template (guild_id, name, duration, seats, description, excusable) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (guild_id, *_values(fields)),
            ).lastrowid
    except sqlite3.IntegrityError as error:
        if "UNIQUE" not in str(error):
            raise
        raise TemplateNameTakenError(fields.name) from error
    assert template_id is not None
    return TrainingTemplate(id=template_id, guild_id=guild_id, fields=fields)


def update_template(guild_id: int, template_id: int, fields: TemplateFields) -> bool:
    """Replace what a stored template of a server holds.

    Args:
        guild_id (int): The ID of the server.
        template_id (int): The template's ID.
        fields (TemplateFields): What it holds now.

    Returns:
        bool: False when the server holds no such template any more.

    Raises:
        TemplateNameTakenError: If another template of the server has the new name, whatever the case.
    """
    try:
        with db.transaction() as conn:
            changed = conn.execute(
                "UPDATE training_template SET name = ?, duration = ?, seats = ?, description = ?, excusable = ? "
                "WHERE guild_id = ? AND id = ?",
                (*_values(fields), guild_id, template_id),
            ).rowcount
    except sqlite3.IntegrityError as error:
        if "UNIQUE" not in str(error):
            raise
        raise TemplateNameTakenError(fields.name) from error
    return changed == 1


def delete_template(guild_id: int, template_id: int) -> bool:
    """Delete a template of a server; sessions copied from it stay as they are.

    Args:
        guild_id (int): The ID of the server.
        template_id (int): The template's ID.

    Returns:
        bool: False when the server held no such template.
    """
    with db.transaction() as conn:
        return (
            conn.execute("DELETE FROM training_template WHERE guild_id = ? AND id = ?", (guild_id, template_id)).rowcount == 1
        )


def count_templates() -> int:
    """Count the templates of every server, for the export that cannot hold them.

    Returns:
        int: The count.
    """
    return db.connection().execute("SELECT count(*) FROM training_template").fetchone()[0]
