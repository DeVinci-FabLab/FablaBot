"""A formation cannot start in the past: /fm add and the edit modal refuse it privately and change nothing."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

from conftest import fill_text_input, make_interaction
import pytest

from fablabot.helpers import formation as formation_helpers
from fablabot.helpers.constants import PARIS_TZ, ErrorMessages
from fablabot.helpers.formation import rendering
from fablabot.ui import formation

if TYPE_CHECKING:
    from collections.abc import Iterator

NOW = datetime(2030, 6, 15, 18, 30, tzinfo=PARIS_TZ)
EMOJI = "🔧"


class _FrozenDatetime(datetime):
    """A datetime whose ``now`` is fixed at ``NOW``."""

    @classmethod
    def now(cls, tz: Any = None) -> datetime:
        return NOW.astimezone(tz) if tz else NOW


@pytest.fixture(autouse=True)
def _frozen_clock(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Fix the clock the past check reads."""
    monkeypatch.setattr(rendering, "datetime", _FrozenDatetime)
    yield


def test_past_check_compares_aware_datetimes() -> None:
    """One minute before now is past; now and later are not."""
    assert formation_helpers.is_past(datetime(2030, 6, 15, 18, 29, tzinfo=PARIS_TZ))
    assert not formation_helpers.is_past(NOW)
    assert not formation_helpers.is_past(datetime(2030, 6, 15, 18, 31, tzinfo=PARIS_TZ))


def test_past_check_holds_in_the_repeated_hour(monkeypatch: pytest.MonkeyPatch) -> None:
    """In the hour the clocks go back, a start earlier in absolute time is past though its wall clock is later."""

    class _Night(datetime):
        """The clock at 02:10 CET, the second 02:10 of the night."""

        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return datetime(2026, 10, 25, 1, 10, tzinfo=UTC).astimezone(tz)

    monkeypatch.setattr(rendering, "datetime", _Night)
    assert formation_helpers.is_past(datetime(2026, 10, 25, 2, 40, tzinfo=PARIS_TZ))


@pytest.fixture
async def cog(started_bot: Any) -> Any:
    """The real formation cog."""
    return started_bot.get_cog("FormationManagement")


async def _add(cog: Any, monkeypatch: pytest.MonkeyPatch, date: str, hour: str) -> MagicMock:
    monkeypatch.setattr("fablabot.cogs.formation_management.ensure_command_context", AsyncMock(return_value=True))
    interaction = make_interaction()
    trainer = MagicMock()
    trainer.mention = "<@1>"
    await cog.fm_add.callback(
        cog, interaction, emoji=EMOJI, trainer=trainer, date=date, hour=hour, duration="2h", seats=5, excusable=True
    )
    return interaction


async def test_add_in_the_past_is_refused_privately(
    cog: Any, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A start before now gets the private refusal, a warning with the guild id, and no modal."""
    interaction = await _add(cog, monkeypatch, "15/06/2030", "18:29")
    interaction.response.send_message.assert_awaited_once_with(ErrorMessages.PAST_DATETIME, ephemeral=True)
    interaction.response.send_modal.assert_not_awaited()
    assert cog.get_guild_draft(interaction.guild.id).fms == []
    assert any("1234" in r.getMessage() and r.levelname == "WARNING" for r in caplog.records)


@pytest.mark.parametrize(("date", "hour"), [("15/06/2030", "18:30"), ("15/06/2030", "18:31"), ("16/06/2031", "09:00")])
async def test_add_now_or_later_opens_the_modal(cog: Any, monkeypatch: pytest.MonkeyPatch, date: str, hour: str) -> None:
    """A start exactly now or later passes to the modal."""
    interaction = await _add(cog, monkeypatch, date, hour)
    interaction.response.send_modal.assert_awaited_once()
    interaction.response.send_message.assert_not_awaited()


def _edit(typed: str) -> tuple[Any, MagicMock, MagicMock]:
    view = MagicMock()
    view.updated_formation.start_iso = "unchanged"
    view.updated_formation.start_dt = NOW
    view.refresh_main_message = AsyncMock()
    modal = formation._EditDatetimeModal(view)
    fill_text_input(modal.datetime_input, typed)
    interaction = make_interaction()
    return modal, view, interaction


async def test_edit_modal_past_start_is_refused_privately(caplog: pytest.LogCaptureFixture) -> None:
    """A past start in the edit modal is refused privately, logged as a warning with the guild id, and changes nothing."""
    modal, view, interaction = _edit("15/06/2030 18:29")
    await modal.on_submit(interaction)
    interaction.response.send_message.assert_awaited_once_with(ErrorMessages.PAST_DATETIME, ephemeral=True)
    assert any("1234" in r.getMessage() and r.levelname == "WARNING" for r in caplog.records)
    assert view.updated_formation.start_iso == "unchanged"
    view.refresh_main_message.assert_not_awaited()


@pytest.mark.parametrize("typed", ["15/06/2030 18:30", "15/06/2030 18:31"])
async def test_edit_modal_now_or_later_passes(typed: str) -> None:
    """A start now or later updates the formation."""
    modal, view, interaction = _edit(typed)
    await modal.on_submit(interaction)
    view.refresh_main_message.assert_awaited_once_with(interaction)
    assert view.updated_formation.start_iso != "unchanged"
    interaction.response.send_message.assert_not_awaited()


async def test_remove_description_mentions_no_index(cog: Any) -> None:
    """/fm remove opens a selector, so its slash description names no index."""
    assert cog.fm_remove.description == "Retirer une formation du brouillon."
