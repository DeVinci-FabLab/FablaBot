"""A view opened earlier acts on its own formation or is refused, never on a neighbour that took its place."""

from __future__ import annotations

from typing import Any

from conftest import fill_text_input, make_interaction
import pytest

from fablabot.helpers.constants import ErrorMessages
from fablabot.models import FmCommand, FmMessageDraft, Formation
from fablabot.ui import formation

GUILD_ID = 1234


def _fm(emoji: str, day: int, *, seats: int = 5) -> Formation:
    return Formation(
        emoji=emoji,
        name=f"Formation {emoji}",
        trainer_mention="<@1>",
        start_iso=f"2099-06-{day:02d}T18:00:00+02:00",
        duration="2h",
        seats=seats,
    )


@pytest.fixture
async def cog(started_bot: Any) -> Any:
    """The real formation cog."""
    return started_bot.get_cog("FormationManagement")


def _store(cog: Any, *fms: Formation) -> None:
    cog.set_guild_draft(
        GUILD_ID, FmMessageDraft(header="", role_id=0, intro="", fms=sorted(fms, key=lambda f: f.start_dt), end="")
    )


def _emojis(cog: Any) -> list[str]:
    return [fm.emoji for fm in cog.get_guild_draft(GUILD_ID).fms]


def _button(view: formation.FormationSelectView, emoji: str) -> Any:
    return next(item for item in view.children if emoji in str(getattr(item, "label", "")))


async def test_remove_acts_on_the_formation_shown_when_an_earlier_one_lands(cog: Any) -> None:
    """Z is added before A while the selector is open: clicking A removes A, not Z."""
    _store(cog, _fm("🅰", 10), _fm("🅱", 11))
    view = formation.FormationSelectView(cog, cog.get_guild_draft(GUILD_ID).fms, FmCommand.REMOVE)
    _store(cog, _fm("🅰", 10), _fm("🅱", 11), _fm("🇿", 9))
    interaction = make_interaction()
    interaction.guild.id = GUILD_ID

    await view.remove_formation(interaction, _button(view, "🅰").formation)

    assert _emojis(cog) == ["🇿", "🅱"]
    assert interaction.response.edit_message.await_args.kwargs["content"] == "Supprimé: 🅰 Formation 🅰"


@pytest.mark.parametrize("change", ["gone", "edited"])
async def test_remove_refuses_a_formation_that_is_gone_or_changed(cog: Any, change: str) -> None:
    """A formation removed or edited since the selector opened is not removed: the view is expired."""
    _store(cog, _fm("🅰", 10), _fm("🅱", 11))
    view = formation.FormationSelectView(cog, cog.get_guild_draft(GUILD_ID).fms, FmCommand.REMOVE)
    if change == "gone":
        _store(cog, _fm("🅱", 11))
    else:
        _store(cog, _fm("🅰", 10, seats=99), _fm("🅱", 11))
    before = _emojis(cog)
    interaction = make_interaction()
    interaction.guild.id = GUILD_ID

    await view.remove_formation(interaction, _button(view, "🅰").formation)

    interaction.response.send_message.assert_awaited_once_with(ErrorMessages.EXPIRED_VIEW_MESSAGE, ephemeral=True)
    interaction.response.edit_message.assert_not_awaited()
    assert _emojis(cog) == before


async def test_edit_validate_replaces_the_formation_it_opened_when_an_earlier_one_lands(cog: Any) -> None:
    """Z lands before B while B is edited: the draft is Z, A, B edited, with nothing lost or duplicated."""
    _store(cog, _fm("🅰", 10), _fm("🅱", 11))
    opened = cog.get_guild_draft(GUILD_ID).fms[1]
    view = formation._EditFormationView(cog, 2, opened)
    view.updated_formation.seats = 42
    _store(cog, _fm("🅰", 10), _fm("🅱", 11), _fm("🇿", 9))
    interaction = make_interaction()
    interaction.guild.id = GUILD_ID

    await view.validate_button.callback(interaction)

    fms = cog.get_guild_draft(GUILD_ID).fms
    assert [fm.emoji for fm in fms] == ["🇿", "🅰", "🅱"]
    assert [fm.seats for fm in fms] == [5, 5, 42]


async def test_second_editor_of_one_formation_does_not_overwrite_the_first(cog: Any) -> None:
    """Two views opened on one formation: the second validate is refused and the first edit stays."""
    _store(cog, _fm("🅰", 10))
    opened = cog.get_guild_draft(GUILD_ID).fms[0]
    first = formation._EditFormationView(cog, 1, opened)
    second = formation._EditFormationView(cog, 1, opened)
    first.updated_formation.seats = 99
    second.updated_formation.name = "Renamed"
    interaction = make_interaction()
    interaction.guild.id = GUILD_ID

    await first.validate_button.callback(interaction)
    other = make_interaction()
    other.guild.id = GUILD_ID
    await second.validate_button.callback(other)

    other.response.send_message.assert_awaited_once_with(ErrorMessages.EXPIRED_VIEW_MESSAGE, ephemeral=True)
    other.response.edit_message.assert_not_awaited()
    [stored] = cog.get_guild_draft(GUILD_ID).fms
    assert (stored.seats, stored.name) == (99, "Formation 🅰")


async def test_edit_validate_refuses_an_emoji_taken_since_the_modal_check(cog: Any) -> None:
    """The new emoji of the edit was free at the modal and taken by an add before validate: nothing is written."""
    _store(cog, _fm("🅰", 10))
    view = formation._EditFormationView(cog, 1, cog.get_guild_draft(GUILD_ID).fms[0])
    view.updated_formation.emoji = "🇿"
    _store(cog, _fm("🅰", 10), _fm("🇿", 9))
    interaction = make_interaction()
    interaction.guild.id = GUILD_ID

    await view.validate_button.callback(interaction)

    interaction.response.send_message.assert_awaited_once_with(ErrorMessages.EXPIRED_VIEW_MESSAGE, ephemeral=True)
    assert _emojis(cog) == ["🇿", "🅰"]


async def test_emoji_modal_keeps_its_own_emoji_when_an_earlier_formation_lands(cog: Any) -> None:
    """Retyping its own emoji is not a clash, even after the draft positions moved."""
    _store(cog, _fm("🅰", 10), _fm("🅱", 11))
    view = formation._EditFormationView(cog, 2, cog.get_guild_draft(GUILD_ID).fms[1])
    _store(cog, _fm("🅰", 10), _fm("🅱", 11), _fm("🇿", 9))
    modal = formation._EditEmojiModal(view)
    fill_text_input(modal.emoji_input, "🅱")
    interaction = make_interaction()
    interaction.guild.id = GUILD_ID

    await modal.on_submit(interaction)

    interaction.response.send_message.assert_not_awaited()
    interaction.response.edit_message.assert_awaited_once()
