"""Components of the attendance check. Each click reads the check from the database; nothing about it is kept in memory."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import logging
from typing import TYPE_CHECKING, Any, Final, override

from discord import ButtonStyle, HTTPException, SelectOption, Thread, ui
from discord.components import Button, SelectMenu

from fablabot.helpers.constants import APPEL_COMMAND_ROLES, PARIS_TZ, ErrorMessages
from fablabot.helpers.formation.attendance import (
    alert_answer,
    alert_message,
    appel_message,
    deliver,
    is_staff,
    may_take,
    notify_validation,
    refresh_alerts,
    settle_alert,
)
from fablabot.helpers.formation.rendering import discord_timestamp, humanize_dt
from fablabot.helpers.utils import RefusalKind, get_or_fetch_member, log_refusal, right_check
from fablabot.logging_handlers import guild_log_context
from fablabot.models import AlertAction
from fablabot.services import attendance, bot_state

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator
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
_NO_ALERT_RIGHT = "Seuls les Respo Formation et les admins peuvent répondre à cette alerte."
_COMMAND = "appel"
_RELAUNCH_UNREACHED = "Le formateur n'a pas pu être relancé : ses MP sont fermés et aucun fil n'a pu être créé."
_RELAUNCH_NO_TRAINER = "Le formateur n'a pas pu être relancé : aucun formateur n'est nommé pour cette formation."
_RELAUNCH_ERROR = "Le formateur n'a pas pu être relancé : une erreur est survenue. L'alerte reste ouverte."

# The checks whose relaunch is in flight in this process: the claim is stored before the trainer is reached, so until it
# settles a stored "relaunch" does not yet mean the trainer was. The same fact is stored under ``_RELAUNCH_KEY`` so that
# a restart mid-relaunch is found at the next tick and its claim given back (see ``recover_relaunches``).
_relaunching: Final[set[int]] = set()
_RELAUNCH_KEY: Final = "relaunch:"


@contextmanager
def _appel_log_context(appel_id: int) -> Iterator[None]:
    """Hold the check's server as the current one for the log lines of a click; none when the check is gone."""
    appel = attendance.get(appel_id)
    with guild_log_context(appel.guild_id if appel else None):
        yield


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
    if alert and not is_staff(member):
        return RefusalKind.MISSING_RIGHT, _NO_ALERT_RIGHT
    if not alert and not may_take(appel, member):
        return RefusalKind.MISSING_RIGHT, _NO_RIGHT
    if alert and alert_answer(appel) is not None:
        return None
    return _closed_refusal(appel)


def _cancelled_refusal(appel: Appel) -> tuple[RefusalKind, str]:
    """Write the refusal of a check whose training was cancelled."""
    return RefusalKind.APPEL_CANCELLED, f"La formation **{appel.name}** a été annulée : l'appel est fermé."


def _closed_refusal(appel: Appel) -> tuple[RefusalKind, str] | None:
    """Find why a check no longer takes changes, if it does not."""
    if appel.cancelled:
        return _cancelled_refusal(appel)
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
        alert (bool): True for a button of the alert, which only the staff may answer and which stays answerable with the
            first answer while the check is closed.

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
        with _appel_log_context(self.appel_id):
            await self._toggle(interaction)

    async def _toggle(self, interaction: Interaction[Any]) -> None:
        """Set the mark, then show the check."""
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
        with _appel_log_context(self.appel_id):
            await self._choose(interaction)

    async def _choose(self, interaction: Interaction[Any]) -> None:
        """Mark the chosen members, then show the check."""
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
        with _appel_log_context(self.appel_id):
            await self._save(interaction)

    async def _save(self, interaction: Interaction[Any]) -> None:
        """Save the counts, refuse a check cancelled meanwhile, then show the check and report the save."""
        try:
            saved = attendance.validate(self.appel_id, interaction.user.id, datetime.now(PARIS_TZ))
        except LookupError:
            await interaction.response.send_message(f"{_GONE} {_UNCHANGED}", ephemeral=True)
            return
        except attendance.AppelCancelledError as cancelled:
            # The cancel landed after the click was checked: the same refusal as a click made after it.
            await _refuse(interaction, *_cancelled_refusal(cancelled.appel), logger=logger)
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
        with _appel_log_context(self.appel_id):
            await self._answer(interaction)

    async def _answer(self, interaction: Interaction[Any]) -> None:
        """Record the answer, relaunch the trainer when asked, then show the answer in every copy."""
        answer = self._ANSWERS[self.action]
        user_id = interaction.user.id
        if not attendance.handle(self.appel_id, user_id, answer, datetime.now(PARIS_TZ)):
            await self._already(interaction)
            return
        relaunch = answer is AlertAction.RELAUNCH
        if relaunch and (failure := await self._relaunch(interaction)) is not None:
            await interaction.followup.send(failure, ephemeral=True)
            return
        logger.info(f"[appel] alert appel_id={self.appel_id} user_id={user_id} action={answer}")
        appel = attendance.get(self.appel_id)
        if appel is None:
            return
        content, view = alert_message(appel)
        skip = interaction.message.id if interaction.message else None
        try:
            if relaunch:
                await interaction.edit_original_response(content=content, view=view)
            else:
                await interaction.response.edit_message(content=content, view=view)
        except HTTPException:
            # Discord refused the response: the clicker's own copy is edited with the others, not left with live buttons.
            logger.exception(f"Could not answer the alert click on appel {self.appel_id}; editing the copy directly.")
            skip = None
        await refresh_alerts(interaction.client, appel, skip=skip)

    async def _already(self, interaction: Interaction[Any]) -> None:
        """Tell the clicker, privately, how the alert was answered first, or that a relaunch is still in progress.

        A relaunch in flight can still fail and give the answer back, so it is not told as done: the clicker is asked to
        try again, and nothing was stored for them.
        """
        appel = attendance.get(self.appel_id)
        if appel is not None and appel.id in _relaunching:
            log_refusal(logger, _COMMAND, interaction, RefusalKind.APPEL_RELAUNCHING)
            await interaction.response.send_message(
                f"Relance en cours par <@{appel.handled_by}>. "
                f"Réessaie dans un instant : si elle échoue, l'alerte redevient disponible. {_UNCHANGED}",
                ephemeral=True,
            )
            return
        answer = alert_answer(appel) if appel else None
        log_refusal(logger, _COMMAND, interaction, RefusalKind.APPEL_ANSWERED)
        await interaction.response.send_message(f"Déjà traité : {answer or _GONE}", ephemeral=True)

    async def _relaunch(self, interaction: Interaction[Any]) -> str | None:
        """Defer the click, then send the trainer the check again; None when it went, else what to tell the clicker.

        The click is deferred first: reaching the trainer may fetch the member and open a thread, which can outlast the
        three seconds Discord gives a response.

        When nobody was reached, or sending failed (the failure is logged, not raised), the claim is taken back. The
        trainer may have saved meanwhile, and the save found the alert answered, so it is closed done here rather than
        left open on a saved check. Until then the check is listed as relaunching, so that another Respo's click is told
        to try again rather than that it is done.
        """
        reached = False
        failed = _RELAUNCH_UNREACHED
        _relaunching.add(self.appel_id)
        try:
            bot_state.put(f"{_RELAUNCH_KEY}{self.appel_id}", "claimed")
            await interaction.response.defer()
            appel = attendance.get(self.appel_id)
            guild = interaction.client.get_guild(appel.guild_id) if appel else None
            if appel is not None and appel.trainer_id is None:
                failed = _RELAUNCH_NO_TRAINER
            else:
                reached = appel is not None and guild is not None and await deliver(guild, appel)
        except Exception:  # the click is answered below; raised, it would leave the clicker with a deferred "thinking"
            logger.exception(f"Relaunch of appel {self.appel_id} by user {interaction.user.id} failed.")
            failed = _RELAUNCH_ERROR
        finally:
            try:
                if not reached:
                    attendance.release(self.appel_id, interaction.user.id, AlertAction.RELAUNCH)
                bot_state.delete(f"{_RELAUNCH_KEY}{self.appel_id}")
            finally:
                # Dropped with the release, before any await: a click that comes after finds the alert open again.
                _relaunching.discard(self.appel_id)
            if not reached:
                released = attendance.get(self.appel_id)
                if released is not None:
                    await settle_alert(interaction.client, released)
        return None if reached else failed


async def recover_relaunches(bot: Fablabot) -> None:
    """Give back the claim of a relaunch that a restart interrupted, so the alert can be answered again.

    A relaunch stores its claim, then reaches the trainer; the mark kept beside it says it was not over. One found here
    that this process is not running belongs to an earlier process: whether the trainer was reached is unknown, so the
    claim is released rather than the trainer sent the check a second time, and the Respo may relaunch again.

    Args:
        bot (Fablabot): The bot.
    """
    for key in bot_state.keys(_RELAUNCH_KEY):
        appel_id = int(key.removeprefix(_RELAUNCH_KEY))
        if appel_id in _relaunching:
            continue
        appel = attendance.get(appel_id)
        # A server no longer served gets no write and no edit; its mark goes all the same.
        if appel is not None and not bot.guild_access.is_served(appel.guild_id):
            bot_state.delete(key)
            continue
        if appel is not None and appel.handled_action is AlertAction.RELAUNCH and appel.handled_by is not None:
            attendance.release(appel_id, appel.handled_by, AlertAction.RELAUNCH)
            with guild_log_context(appel.guild_id):
                logger.warning(f"[appel] relaunch interrupted by a restart released appel_id={appel_id}")
            released = attendance.get(appel_id)
            if released is not None:
                await settle_alert(bot, released)
        bot_state.delete(key)


class OpenAppelPicker(ui.View):
    """The menu `/fm appel` offers when several checks are open: choosing one shows it in place of the menu."""

    interaction_check = right_check(logger, "fm.appel", APPEL_COMMAND_ROLES)

    def __init__(self, appels: Iterable[Appel]) -> None:
        """Build the menu.

        Args:
            appels (Iterable[Appel]): The open checks of the server, at most 25 (Discord's limit for one menu).
        """
        super().__init__(timeout=None)
        # A check whose training row is gone has no name, and Discord rejects an empty label.
        self.choose.options = [
            SelectOption(
                label=appel.name or f"Formation du {appel.starts_at:%d/%m %Hh%M}",
                value=str(appel.id),
                description=humanize_dt(appel.starts_at),
            )
            for appel in appels
        ]

    @ui.select(placeholder="Choisis une formation")
    async def choose(self, interaction: Interaction[Fablabot], select: ui.Select[OpenAppelPicker]) -> None:
        """Check the chosen check again, then edit the reply into its message and controls."""
        appel = await check_click(interaction, int(select.values[0]), logger=logger)
        if appel is None:
            return
        guild = interaction.client.get_guild(appel.guild_id)
        if guild is None or interaction.guild_id != appel.guild_id:
            await _refuse(interaction, RefusalKind.APPEL_UNKNOWN, _GONE, logger=logger)
            return
        content, view = appel_message(guild, appel, datetime.now(PARIS_TZ))
        await interaction.response.edit_message(content=content, view=view)


DYNAMIC_ITEMS: Final = (AppelToggle, AppelMenu, AppelValidate, AppelAlertAction)
