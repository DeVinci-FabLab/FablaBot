"""Shared fixtures and Discord stubs for the test suite."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from discord import Guild, Member, ui
import pytest


@pytest.fixture(autouse=True)
def _isolated_cwd(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    """Run every test from an empty temporary directory so nothing lands in ``data/`` or ``logs/``."""
    monkeypatch.chdir(tmp_path)


def make_interaction(
    *,
    user_id: int = 418003123456789012,
    user_name: str = "kevin_secret_name",
    display_name: str = "Kevin Secret",
    guild_id: int = 1234,
) -> MagicMock:
    """Build a stub interaction whose user and guild look like Discord's.

    Args:
        user_id (int): The ID of the invoking user.
        user_name (str): The account name of the invoking user, also its ``str()``.
        display_name (str): The display name of the invoking user.
        guild_id (int): The ID of the guild.

    Returns:
        MagicMock: The stub interaction.
    """
    interaction = MagicMock()
    user = MagicMock(spec=Member)
    user.id = user_id
    user.name = user_name
    user.display_name = display_name
    user.roles = []
    user.__str__.return_value = user_name
    interaction.user = user

    guild = MagicMock(spec=Guild)
    guild.id = guild_id
    guild.roles = []
    guild.text_channels = []
    interaction.guild = guild
    interaction.guild_id = guild_id

    interaction.response.send_message = AsyncMock()
    interaction.response.send_modal = AsyncMock()
    interaction.response.edit_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


def fill_text_input(item: ui.TextInput, value: str) -> None:
    """Set the submitted value of a text input, as Discord would on submit.

    Args:
        item (ui.TextInput): The text input to fill.
        value (str): The value the user typed.
    """
    item._value = value  # noqa: SLF001


def cog_bot() -> MagicMock:
    """Build a stub standing in for the bot or cog handed to views.

    Returns:
        MagicMock: The stub.
    """
    return MagicMock()
