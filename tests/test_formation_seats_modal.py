"""The duration and seats modal of the formation editor: what it replies to each kind of input."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from fablabot.helpers.constants import ErrorMessages
from fablabot.ui import formation


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("2h - 12", None),
        ("2h - 1", None),
        ("2h - 500", None),
        ("2h - abc", ErrorMessages.INVALID_SEATS),
        ("2h - 0", ErrorMessages.INVALID_SEATS),
        ("2h - 501", ErrorMessages.INVALID_SEATS),
        ("2h", ErrorMessages.INVALID_DURATION_SEATS_FORMAT),
        ("2h - 1 - 2", ErrorMessages.INVALID_DURATION_SEATS_FORMAT),
        (" - 5", ErrorMessages.INVALID_DURATION),
    ],
)
async def test_duration_seats_modal_replies_as_before(typed: str, expected: str | None) -> None:
    """Valid input updates the formation; each kind of bad input gets its own ephemeral reply."""
    view = MagicMock()
    view.updated_formation.duration = "1h"
    view.updated_formation.seats = 5
    view.refresh_main_message = AsyncMock()
    modal = formation._EditDurationSeatsModal(view)
    modal.duration_seats_input = SimpleNamespace(value=typed)
    interaction = MagicMock()
    interaction.guild.id = 111111111111111111
    interaction.response.send_message = AsyncMock()

    await modal.on_submit(interaction)

    if expected is None:
        view.refresh_main_message.assert_awaited_once_with(interaction)
        assert view.updated_formation.seats == int(typed.split("-")[1])
        assert view.updated_formation.duration == "2h"
        interaction.response.send_message.assert_not_awaited()
    else:
        interaction.response.send_message.assert_awaited_once_with(expected, ephemeral=True)
        view.refresh_main_message.assert_not_awaited()
