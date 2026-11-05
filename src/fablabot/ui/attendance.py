"""Components of the attendance check. Each click reads the check from the database; nothing about it is kept in memory."""

from __future__ import annotations

from datetime import datetime
import logging
from typing import TYPE_CHECKING, Any, Final, override

from discord import ButtonStyle, SelectOption, Thread, ui
from discord.components import Button, SelectMenu

from fablabot.helpers.constants import PARIS_TZ, ErrorMessages
from fablabot.helpers.formation.attendance import (
    alert_answer,
    alert_message,
    appel_message,
    deliver,
    may_take,
    notify_validation,
    refresh_alerts,
    settle_alert,
)
from fablabot.helpers.formation.rendering import discord_timestamp
from fablabot.helpers.utils import RefusalKind, get_or_fetch_member, log_refusal
from fablabot.logging_handlers import guild_log_context
from fablabot.models import AlertAction
from fablabot.services import attendance

if TYPE_CHECKING:
    from collections.abc import Iterable
    from logging import Logger
    import re

    from discord import Interaction, Message
    from discord.components import Component

    from fablabot.main import Fablabot
    from fablabot.models import Appel

logger = logging.getLogger(__name__)

_UNCHANGED = "Rien n'a été modifié."
_GONE = "Cet appel n'existe plus."
_NO_RIGHT = "Seuls le formateur, les Respo Formation et les admins peuvent faire cet appel."
_COMMAND = "appel"


async def _refuse(interaction: Interaction[Fablabot], kind: RefusalKind, text: str, *, logger: Logger) -> None:
    """Log a refused click by kind and tell the clicker, privately, that nothing changed."""
    log_refusal(logger, _COMMAND, interaction, kind)
    await interaction.response.send_message(f"{text} {_UNCHANGED}", ephemeral=True)


async def _refusal(bot: Fablabot, appel: Appel, user_id: int, *, alert: bool) -> tuple[RefusalKind, str] | None:
    """Find why a click on an existing check must be refused: the kind for the log and the text for the clicker.

    An alert already answered is not refused here: its click is told the first answer, whether or not the check closed since.
    """
    if not bot.guild_access.is_served(appel.guild_id):
        return RefusalKind.SERVER_NOT_SERVED, ErrorMessages.GUILD_NOT_ALLOWED
    guild = bot.get_guild(appel.guild_id)
    member = await get_or_fetch_member(guild, user_id) if guild else None
    if member is None:
        return RefusalKind.MISSING_RIGHT, _GONE
    if not may_take(appel, member):
        return RefusalKind.MISSING_RIGHT, _NO_RIGHT
    if alert and alert_answer(appel) is not None:
        return None
    return _closed_refusal(appel)


def _closed_refusal(appel: Appel) -> tuple[RefusalKind, str] | None:
    """Find why a check no longer takes changes, if it does not."""
    if appel.cancelled:
        return RefusalKind.APPEL_CANCELLED, f"La formation **{appel.name}** a été annulée : l'appel est fermé."
    if not appel.is_open(datetime.now(PARIS_TZ)):
        return (
            RefusalKind.APPEL_CLOSED,
            f"L'appel de **{appel.name}** est fermé depuis le {discord_timestamp(appel.closes_at, 'D')}.",
        )
    return None


async def check_click(
    interaction: Interaction[Fablabot], appel_id: int, *, logger: Logger, alert: bool = False
) -> Appel | None:
    """Check a click on a control of a check, before anything is stored.

    The order is fixed: the check exists, its server is served, the clicker may take it there, and it still takes
    changes. A refusal is answered here, privately, and logged by kind with IDs only. A clicker who is not on the check's
    server hears the same as for a check that does not exist, so a forged ID tells nothing.

    Args:
        interaction (Interaction[Fablabot]): The click.
        appel_id (int): The check read from the control's custom ID.
        logger (Logger): The logger of the module that owns the control.
        alert (bool): True for a button of the alert, which stays answerable with the first answer while the check is closed.

    Returns:
        Appel | None: The check when the click may go on, None when it was refused.
    """
    appel = attendance.get(appel_id)
    if appel is None:
        await _refuse(interaction, RefusalKind.APPEL_UNKNOWN, _GONE, logger=logger)
        return None
    with guild_log_context(appel.guild_id):
        refusal = await _refusal(interaction.client, appel, interaction.user.id, alert=alert)
        if refusal is not None:
            await _refuse(interaction, *refusal, logger=logger)
            return None
    return appel


async def _show(interaction: Interaction[Fablabot], appel_id: int) -> None:
    """Edit the clicked message to show the check as it is stored now."""
    appel = attendance.get(appel_id)
    guild = interaction.client.get_guild(appel.guild_id) if appel else None
    if appel is None or guild is None:
        return
    content, view = appel_message(guild, appel, datetime.now(PARIS_TZ), thread=isinstance(interaction.channel, Thread))
    await interaction.response.edit_message(content=content, view=view)


def _shown(message: Message | None, custom_id: str) -> Component | None:
    """Find a control of the clicked message by its custom ID: the copy Discord holds, which the clicker saw."""
    for row in message.components if message else ():
        for component in getattr(row, "children", ()):
            if getattr(component, "custom_id", None) == custom_id:
                return component
    return None


def _listed(message: Message | None, custom_id: str) -> list[int]:
    """Read the members a menu of the clicked message lists: its own options, never the live list."""
    menu = _shown(message, custom_id)
    return [int(option.value) for option in menu.options] if isinstance(menu, SelectMenu) else []


class AppelToggle(ui.DynamicItem[ui.Button[ui.View]], template=r"appel:(?P<appel>[0-9]+):m:(?P<member>[0-9]+)"):
    """The button of one registered member: touching it marks them present, touching it again takes the mark back."""

    def __init__(  # noqa: PLR0913 - the arguments a button is built from
        self,
        appel_id: int,
        member_id: int,
        label: str | None = None,
        *,
        present: bool = False,
        disabled: bool = False,
        row: int | None = None,
    ) -> None:
        """Build the button.

        Args:
            appel_id (int): The check.
            member_id (int): The member the button stands for.
            label (str | None): The text, the member's pseudo.
            present (bool): Whether the member is marked present, which colours the button.
            disabled (bool): Whether the button takes no click.
            row (int | None): The row of the message.
        """
        super().__init__(
            ui.Button(
                label=label,
                style=ButtonStyle.success if present else ButtonStyle.secondary,
                custom_id=f"appel:{appel_id}:m:{member_id}",
                disabled=disabled,
            ),
            row=row,
        )
        self.appel_id = appel_id
        self.member_id = member_id

    @override
    @classmethod
    async def from_custom_id(cls, interaction: Interaction[Any], item: ui.Item[Any], match: re.Match[str], /) -> AppelToggle:
        """Rebuild the button from its custom ID."""
        return cls(int(match["appel"]), int(match["member"]))

    @override
    async def interaction_check(self, interaction: Interaction[Any], /) -> bool:
        """Let the click through when the check, the server, the clicker and the time allow it."""
        return await check_click(interaction, self.appel_id, logger=logger) is not None

    @override
    async def callback(self, interaction: Interaction[Any]) -> None:
        """Set the opposite of the mark the clicked message shows, then show the check.

        Setting rather than flipping what is stored makes a retried or doubled click, or one on an older copy, harmless.
        """
        if self.member_id in {r.user_id for r in attendance.registrants(self.appel_id)}:
            button = _shown(interaction.message, f"appel:{self.appel_id}:m:{self.member_id}")
            # A click always carries its message; the stored mark only stands in when a caller has none.
            here = (
                button.style == ButtonStyle.success
                if isinstance(button, Button)
                else self.member_id in attendance.present(self.appel_id)
            )
            attendance.mark(self.appel_id, [self.member_id], [] if here else [self.member_id])
            who = f"appel_id={self.appel_id} user_id={interaction.user.id} member_id={self.member_id}"
            logger.info(f"[appel] mark {who} present={not here}")
        await _show(interaction, self.appel_id)


class AppelMenu(ui.DynamicItem[ui.Select[ui.View]], template=r"appel:(?P<appel>[0-9]+):s:(?P<menu>[0-3])"):
    """A menu listing a slice of the registered members, for a training too big for one button each."""

    def __init__(  # noqa: PLR0913 - the arguments a menu is built from
        self,
        appel_id: int,
        menu: int,
        *,
        placeholder: str | None = None,
        options: Iterable[SelectOption] = (),
        disabled: bool = False,
        row: int | None = None,
    ) -> None:
        """Build the menu.

        Args:
            appel_id (int): The check.
            menu (int): Its place among the menus, from 0.
            placeholder (str | None): The text shown while nothing is open.
            options (Iterable[SelectOption]): The members it lists, those present ticked.
            disabled (bool): Whether the menu takes no choice.
            row (int | None): The row of the message.
        """
        listed = list(options)
        super().__init__(
            ui.Select(
                custom_id=f"appel:{appel_id}:s:{menu}",
                placeholder=placeholder,
                min_values=0,
                max_values=max(1, len(listed)),
                options=listed,
                disabled=disabled,
            ),
            row=row,
        )
        self.appel_id = appel_id
        self.menu = menu

    @override
    @classmethod
    async def from_custom_id(cls, interaction: Interaction[Any], item: ui.Item[Any], match: re.Match[str], /) -> AppelMenu:
        """Rebuild the menu from its custom ID."""
        return cls(int(match["appel"]), int(match["menu"]))

    @override
    async def interaction_check(self, interaction: Interaction[Any], /) -> bool:
        """Let the choice through when the check, the server, the clicker and the time allow it."""
        return await check_click(interaction, self.appel_id, logger=logger) is not None

    @override
    async def callback(self, interaction: Interaction[Any]) -> None:
        """Set the members this menu lists to what was chosen, leave the others, then show the check."""
        listed = _listed(interaction.message, self.item.custom_id)
        attendance.mark(self.appel_id, listed, [int(value) for value in self.item.values])
        logger.info(
            f"[appel] menu appel_id={self.appel_id} user_id={interaction.user.id} menu={self.menu} listed={len(listed)}"
        )
        await _show(interaction, self.appel_id)


class AppelValidate(ui.DynamicItem[ui.Button[ui.View]], template=r"appel:(?P<appel>[0-9]+):ok"):
    """The button that saves the counts, and saves them again after a correction."""

    def __init__(self, appel_id: int, *, updated: bool = False, disabled: bool = False, row: int | None = None) -> None:
        """Build the button.

        Args:
            appel_id (int): The check.
            updated (bool): Whether the check was saved already, which changes the label.
            disabled (bool): Whether the button takes no click.
            row (int | None): The row of the message.
        """
        super().__init__(
            ui.Button(
                label="Mettre à jour l'appel" if updated else "Valider l'appel",
                style=ButtonStyle.primary,
                custom_id=f"appel:{appel_id}:ok",
                disabled=disabled,
            ),
            row=row,
        )
        self.appel_id = appel_id

    @override
    @classmethod
    async def from_custom_id(cls, interaction: Interaction[Any], item: ui.Item[Any], match: re.Match[str], /) -> AppelValidate:
        """Rebuild the button from its custom ID."""
        return cls(int(match["appel"]))

    @override
    async def interaction_check(self, interaction: Interaction[Any], /) -> bool:
        """Let the click through when the check, the server, the clicker and the time allow it."""
        return await check_click(interaction, self.appel_id, logger=logger) is not None

    @override
    async def callback(self, interaction: Interaction[Any]) -> None:
        """Save the counts for the registered members marked present and the others, then show the check."""
        try:
            saved = attendance.validate(self.appel_id, interaction.user.id, datetime.now(PARIS_TZ))
        except LookupError:
            await interaction.response.send_message(f"{_GONE} {_UNCHANGED}", ephemeral=True)
            return
        logger.info(
            f"[appel] validated appel_id={self.appel_id} user_id={interaction.user.id} "
            f"present={len(saved.here)} absent={len(saved.away)}"
        )
        try:
            await _show(interaction, self.appel_id)
        finally:
            # The save stands whatever became of the edit: the Respo hear of it either way.
            if saved.alert_closed:
                await refresh_alerts(interaction.client, saved.appel)
            guild = interaction.client.get_guild(saved.appel.guild_id)
            if guild is not None:
                await notify_validation(guild, saved)


class AppelAlertAction(ui.DynamicItem[ui.Button[ui.View]], template=r"appel:(?P<appel>[0-9]+):a:(?P<action>[ric])"):
    """A button of the alert the Respo Formation get when nobody made the check: the first answer decides."""

    ORDER: Final = ("r", "i", "c")
    _ANSWERS: Final = {"r": AlertAction.RELAUNCH, "i": AlertAction.IGNORE, "c": AlertAction.CANCEL}
    _LABELS: Final = {"r": "Relancer le formateur", "i": "Ignorer", "c": "Annuler la formation"}
    _STYLES: Final = {"r": ButtonStyle.primary, "i": ButtonStyle.secondary, "c": ButtonStyle.danger}

    def __init__(self, appel_id: int, action: str, *, disabled: bool = False) -> None:
        """Build the button.

        Args:
            appel_id (int): The check.
            action (str): ``r`` to relaunch the trainer, ``i`` to ignore, ``c`` to cancel the training.
            disabled (bool): Whether the button takes no click, once the alert was answered.
        """
        super().__init__(
            ui.Button(
                label=self._LABELS[action],
                style=self._STYLES[action],
                custom_id=f"appel:{appel_id}:a:{action}",
                disabled=disabled,
            )
        )
        self.appel_id = appel_id
        self.action = action

    @override
    @classmethod
    async def from_custom_id(
        cls, interaction: Interaction[Any], item: ui.Item[Any], match: re.Match[str], /
    ) -> AppelAlertAction:
        """Rebuild the button from its custom ID."""
        return cls(int(match["appel"]), match["action"])

    @override
    async def interaction_check(self, interaction: Interaction[Any], /) -> bool:
        """Let the click through when the check, the server and the clicker allow it."""
        return await check_click(interaction, self.appel_id, logger=logger, alert=True) is not None

    @override
    async def callback(self, interaction: Interaction[Any]) -> None:
        """Record the answer if it is the first, and show it in every copy; else tell the first answer, privately.

        The answer is recorded before the trainer is relaunched, so that two clicks send one relaunch. A relaunch that
        reaches nobody, or fails, takes its answer back: it settled nothing.
        """
        answer = self._ANSWERS[self.action]
        user_id = interaction.user.id
        if not attendance.handle(self.appel_id, user_id, answer, datetime.now(PARIS_TZ)):
            await self._already(interaction)
            return
        if answer is AlertAction.RELAUNCH and not await self._relaunch(interaction):
            await interaction.response.send_message(
                "Le formateur n'a pas pu être relancé : ses MP sont fermés et aucun fil n'a pu être créé.", ephemeral=True
            )
            return
        logger.info(f"[appel] alert appel_id={self.appel_id} user_id={user_id} action={answer}")
        appel = attendance.get(self.appel_id)
        if appel is None:
            return
        content, view = alert_message(appel)
        try:
            await interaction.response.edit_message(content=content, view=view)
        finally:
            await refresh_alerts(interaction.client, appel, skip=interaction.message.id if interaction.message else None)

    async def _already(self, interaction: Interaction[Any]) -> None:
        """Tell the clicker, privately, how the alert was answered first."""
        appel = attendance.get(self.appel_id)
        answer = alert_answer(appel) if appel else None
        logger.info(f"[appel] alert appel_id={self.appel_id} user_id={interaction.user.id} already answered")
        await interaction.response.send_message(f"Déjà traité : {answer or _GONE}", ephemeral=True)

    async def _relaunch(self, interaction: Interaction[Any]) -> bool:
        """Send the trainer the check again; False when nobody could be reached.

        When nobody was reached, or sending failed, the claim is taken back. The trainer may have saved meanwhile, and the
        save found the alert answered, so it is closed done here rather than left open on a saved check.
        """
        reached = False
        try:
            appel = attendance.get(self.appel_id)
            guild = interaction.client.get_guild(appel.guild_id) if appel else None
            reached = appel is not None and guild is not None and await deliver(guild, appel)
        finally:
            if not reached:
                attendance.release(self.appel_id, interaction.user.id, AlertAction.RELAUNCH)
                released = attendance.get(self.appel_id)
                if released is not None:
                    await settle_alert(interaction.client, released)
        return reached


DYNAMIC_ITEMS: Final = (AppelToggle, AppelMenu, AppelValidate, AppelAlertAction)
