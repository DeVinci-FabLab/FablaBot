"""Bulk DM, draft and reaction message bodies never reach the logs."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

from conftest import fill_text_input, make_interaction
import pytest

from fablabot.ui.message import BulkDMModal, StartMessageModal, _ReactionMessageInputModal

SECRET_BODY = "SECRET-DM-BODY"


@pytest.mark.asyncio
async def test_bulk_dm_modal_logs_no_body(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    interaction = make_interaction()
    interaction.followup.send.return_value = MagicMock(id=42)
    modal = BulkDMModal(MagicMock())
    fill_text_input(modal.message_input, SECRET_BODY)

    await modal.on_submit(interaction)

    assert SECRET_BODY not in caplog.text
    assert "BulkDMModal submitted" in caplog.text


@pytest.mark.asyncio
async def test_start_message_modal_logs_no_body(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    interaction = make_interaction()
    cog = MagicMock()
    cog.set_draft = MagicMock()
    modal = StartMessageModal(cog)
    fill_text_input(modal.content_input, SECRET_BODY)

    await modal.on_submit(interaction)

    assert SECRET_BODY not in caplog.text
    assert "StartMessageModal submitted" in caplog.text


@pytest.mark.asyncio
async def test_reaction_message_modal_logs_no_body(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    interaction = make_interaction()
    cog = MagicMock()
    cog.register_reaction_action = AsyncMock(return_value="ok")
    reaction = MagicMock(emoji="👍", action_type="dm", target_id=5, target_name="role")
    modal = _ReactionMessageInputModal(cog, 1234, reaction)
    fill_text_input(modal.message_input, SECRET_BODY)

    await modal.on_submit(interaction)

    assert SECRET_BODY not in caplog.text
    assert "Reaction modal submitted" in caplog.text
