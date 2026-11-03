"""Bulk DM, draft and reaction message bodies never reach the logs."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

from conftest import fill_text_input, make_interaction
import pytest

from fablabot.models import MessageDraft
from fablabot.ui.message import BulkDMModal, EditMessageModal, StartMessageModal, _ReactionMessageInputModal

SECRET_BODY = "SECRET-DM-BODY"


@pytest.mark.asyncio
async def test_bulk_dm_modal_logs_no_body(caplog: pytest.LogCaptureFixture) -> None:
    """Submitting the bulk DM modal logs the event but never the message body."""
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
    """Submitting the start message modal logs the event but never the message body."""
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
async def test_edit_message_modal_logs_no_body(caplog: pytest.LogCaptureFixture) -> None:
    """Submitting the edit form logs the event but neither the old nor the new text."""
    caplog.set_level(logging.DEBUG)
    interaction = make_interaction()
    cog = MagicMock()
    cog.get_draft = MagicMock(return_value=MessageDraft(content="OLD-" + SECRET_BODY, reactions=[]))
    modal = EditMessageModal(cog, "OLD-" + SECRET_BODY)
    fill_text_input(modal.content_input, SECRET_BODY)

    await modal.on_submit(interaction)

    cog.set_draft.assert_called_once()
    assert SECRET_BODY not in caplog.text
    assert "EditMessageModal submitted" in caplog.text


@pytest.mark.asyncio
async def test_reaction_message_modal_logs_no_body(caplog: pytest.LogCaptureFixture) -> None:
    """Submitting the reaction message modal logs the event but never the message body."""
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
