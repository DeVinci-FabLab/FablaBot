"""The screens to preview, each produced by the bot's own command or view code with sample data."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

from fablabot.cogs import channel_management, formation_management, message_management
from fablabot.models.formation import FmMessageDraft, Formation
from fablabot.ui.formation import StartFmModal
from fablabot.ui.message import SuggestionView

from .preview import Capture, Sent

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine
    from typing import Any

ROLE_FORMATIONS = 1111
TRAINER = 2222
NAMES = {ROLE_FORMATIONS: "Formations", TRAINER: "Camille"}


@dataclass
class Screen:
    """What one screen shows: the messages and modals sent, and the names behind mentions."""

    sent: list[Sent]
    names: dict[int, str] = field(default_factory=lambda: dict(NAMES))


async def _invoke(command: object, cog: object, interaction: object, **options: object) -> None:
    """Run a slash command's own callback on ``cog``, as discord.py does when a member runs it."""
    # The callback's declared type binds the cog away, so the checker cannot see the cog argument.
    await command.callback(cog, interaction, **options)  # pyright: ignore[reportAttributeAccessIssue]  # cog is bound away by the declared type


def _allowed() -> AsyncMock:
    """Stand in for the role and channel check: a preview shows the allowed path."""
    return AsyncMock(return_value=True)


def _draft() -> FmMessageDraft:
    """A draft with two formations, as the staff would fill it."""
    return FmMessageDraft(
        header="# [FORMATIONS] :loudspeaker:",
        role_id=ROLE_FORMATIONS,
        intro="Cette semaine, deux formations pour bien démarrer au FabLab.",
        fms=[
            Formation(
                emoji="🔧",
                name="Découpe laser",
                trainer_mention=f"<@{TRAINER}>",
                start_iso="2026-11-04T18:30:00+01:00",
                duration="2h",
                seats=8,
                description="Prise en main de la découpeuse et règles de sécurité.",
                registered_users=[{"user_id": 1}, {"user_id": 2}, {"user_id": 3}],
            ),
            Formation(
                emoji="🖨️",
                name="Impression 3D",
                trainer_mention=f"<@{TRAINER}>",
                start_iso="2026-11-06T12:15:00+01:00",
                duration="1h30",
                seats=6,
                excusable=False,
            ),
        ],
        end="À très vite !",
    )


async def text_help() -> Screen:
    """``/text help``: the staff help for text channels, private by default."""
    capture = Capture()
    cog = channel_management.ChannelManagement(MagicMock())
    await _invoke(channel_management.ChannelManagement.text_help, cog, capture.interaction(), show=False)
    return Screen(capture.sent)


async def fm_edit() -> Screen:
    """``/fm edit``: the draft preview and one button per formation."""
    capture = Capture()
    cog = MagicMock()
    cog.get_guild_draft.return_value = _draft()
    with patch.object(formation_management, "ensure_command_context", _allowed()):
        await _invoke(formation_management.FormationManagement.fm_edit, cog, capture.interaction())
    return Screen(capture.sent)


async def suggest() -> Screen:
    """``/suggest``: the recipient menu, the anonymity button and the button that opens the modal."""
    capture = Capture()
    with patch.object(message_management, "ensure_command_context", _allowed()):
        await _invoke(message_management.MessageManagement.suggest, MagicMock(), capture.interaction())
    return Screen(capture.sent)


async def suggest_modal() -> Screen:
    """The modal that opens when a recipient is chosen and "Rédiger la suggestion" is clicked."""
    capture = Capture()
    view = SuggestionView(MagicMock())
    view.selected_recipient = "formation"
    await view.open_modal_button.callback(capture.interaction())
    return Screen(capture.sent)


async def fm_start_modal() -> Screen:
    """``/fm start``: the modal asking for the intro, the conclusion and an optional forms URL."""
    capture = Capture()
    await capture.interaction().response.send_modal(StartFmModal(MagicMock(), ROLE_FORMATIONS))
    return Screen(capture.sent)


SCREENS: dict[str, Callable[[], Coroutine[Any, Any, Screen]]] = {
    "text-help": text_help,
    "fm-edit": fm_edit,
    "suggest": suggest,
    "suggest-modal": suggest_modal,
    "fm-start-modal": fm_start_modal,
}
