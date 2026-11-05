"""The trainer takes the attendance from the start message, against a simulated Discord and the real reminder tick."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import logging
import sqlite3
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

from conftest import LISTED_GUILD, OTHER_LISTED_GUILD, make_guild, make_interaction, make_member
import discord
from discord import Permissions
import pytest

from fablabot import db
from fablabot.guild_config import set_log_channel_id
from fablabot.helpers.constants import MAX_MSG_CHARS, PARIS_TZ, RoleNames
from fablabot.helpers.formation.attendance import deliver
from fablabot.helpers.formation.rendering import discord_timestamp
from fablabot.helpers.guild_access import GuildAccessPolicy
from fablabot.models.formation import FmMessageDraft, Formation, PublishedMessage
from fablabot.services import attendance, formations
from fablabot.ui import attendance as ui_attendance
from fablabot.ui.attendance import DYNAMIC_ITEMS

if TYPE_CHECKING:
    import simcord

GUILD_ID = int(LISTED_GUILD)
MESSAGE_ID = 5000
GONE = "Cet appel n'existe plus. Rien n'a été modifié."
NO_RIGHT = "Seuls le formateur, les Respo Formation et les admins peuvent faire cet appel. Rien n'a été modifié."


class Hall:
    """A server with a trainer, the staff and some registered members, and the training they all meet for."""

    def __init__(self, env: simcord.Env) -> None:
        """Create the server, its roles and the people around the training."""
        self.env = env
        self.guild = env.create_guild(id=GUILD_ID)
        self.general = self.guild.create_text_channel("general")
        self.logs = self.guild.create_text_channel("logs")
        set_log_channel_id(GUILD_ID, self.logs.id)
        self.roles = {
            name: self.guild.create_role(name, permissions=Permissions.none())
            for name in (RoleNames.TRAININGS_MANAGER, RoleNames.CODIR, RoleNames.ADMIN_TEMP)
        }
        self.trainer = self.member("camille")
        self.respo = self.member("respo", RoleNames.TRAININGS_MANAGER)
        self.plain = self.member("plain")
        self.codir = self.member("codir", RoleNames.CODIR)
        self.admin_temp = self.member("temp", RoleNames.ADMIN_TEMP)
        self.registered: list[Any] = []

    def member(self, name: str, *roles: str) -> Any:
        """Add a member holding these roles."""
        return self.guild.add_member(self.env.create_user(name), roles=[self.roles[r] for r in roles])

    def register(self, count: int) -> list[Any]:
        """Add ``count`` members who will be registered to the training."""
        self.registered = [self.member(f"lab{i:03}") for i in range(count)]
        return self.registered

    def training(
        self, *, start: datetime, name: str = "Découpe laser", trainer_mention: str | None = None, duration: str = "2h"
    ) -> Formation:
        """Build a training the registered members signed up for."""
        users = [{"user_id": m.id, "username": m.name, "ts_iso": start.isoformat()} for m in self.registered]
        return Formation(
            emoji="🔧",
            name=name,
            trainer_mention=trainer_mention or f"<@{self.trainer.id}>",
            start_iso=start.isoformat(),
            duration=duration,
            seats=200,
            excusable=False,
            registered_users=users,
        )

    def store(self, trainings: list[Formation], message_id: int = MESSAGE_ID) -> None:
        """Store an announcement of these trainings as published."""
        draft = FmMessageDraft(header="h", role_id=1, intro="i", fms=trainings, end="e")
        formations.save_published(GUILD_ID, PublishedMessage(message_id=message_id, channel_id=self.general.id, message=draft))

    def publish(
        self,
        *,
        start: datetime | None = None,
        trainer_mention: str | None = None,
        duration: str = "2h",
        message_id: int = MESSAGE_ID,
    ) -> None:
        """Store the published announcement of one training that starts now unless told otherwise."""
        training = self.training(start=start or datetime.now(PARIS_TZ), trainer_mention=trainer_mention, duration=duration)
        self.store([training], message_id)

    async def tick(self) -> None:
        """Run the formation cog's reminder tick, the way the five-minute loop does."""
        bot: Any = self.env.bot
        cog = bot.get_cog("FormationManagement")
        await cog._check_upcoming_formations.coro(cog)

    def dm(self, who: Any | None = None) -> list[discord.Message]:
        """List the messages the bot sent to a member in private, oldest first."""
        who = who or self.trainer
        return who.user.dm_channel.history()  # pyright: ignore[reportOptionalMemberAccess]  # Any-typed handle

    async def click(self, who: Any, message: discord.Message, custom_id: str) -> Any:
        """Click a button of a private message as its recipient."""
        return await who.user.click(message, custom_id=custom_id)

    @property
    def handled(self) -> bool:
        """Whether the stored start of the only training is recorded as done."""
        published = formations.current_published(GUILD_ID)
        assert published is not None
        return published.message.fms[0].notified_at_start

    @property
    def appel_id(self) -> int:
        """The ID of the only check stored."""
        return db.connection().execute("SELECT id FROM appel").fetchone()["id"]

    async def validate(self, *marked: Any) -> None:
        """Have the trainer mark these members present, then press the validation button."""
        for member in marked:
            (message,) = hall_messages(self)
            await self.click(self.trainer, message, self.toggle_id(member))
        (message,) = hall_messages(self)
        await self.click(self.trainer, message, f"appel:{self.appel_id}:ok")

    def toggle_id(self, member: Any) -> str:
        """Write the custom ID of a member's button."""
        return f"appel:{self.appel_id}:m:{member.id}"

    async def forged_click(self, who: Any, custom_id: str, *, shown: Any | None = None) -> tuple[bool, MagicMock]:
        """Send a click the way Discord does for a message the clicker cannot reach, and tell whether the bot let it run.

        Args:
            who: The clicker.
            custom_id: The control clicked.
            shown: The message the clicker saw, as Discord sends it with the click; a blank one when left out.

        Returns:
            tuple[bool, MagicMock]: Whether the item allowed the click, and the interaction the bot answered.
        """
        bot: Any = self.env.bot
        interaction = make_interaction(user_id=who.id)
        interaction.client = bot
        interaction.guild = None
        interaction.guild_id = None
        interaction.message = shown or MagicMock()
        item_class = next(cls for cls in DYNAMIC_ITEMS if cls.__discord_ui_compiled_template__.fullmatch(custom_id))
        match = item_class.__discord_ui_compiled_template__.fullmatch(custom_id)
        item = await item_class.from_custom_id(interaction, MagicMock(), match)  # pyright: ignore[reportArgumentType]
        allowed = await item.interaction_check(interaction)
        if allowed:
            await item.callback(interaction)
        return allowed, interaction


def hall_messages(hall: Hall) -> list[discord.Message]:
    """List the messages the trainer got in private."""
    return hall.dm()


@pytest.fixture
async def hall(simcord_env: simcord.Env) -> Hall:
    """Open a server with a trainer, a Respo Formation, a CoDir, a temporary admin and a plain member."""
    return Hall(simcord_env)


def _buttons(message: discord.Message) -> dict[str, dict[str, Any]]:
    """Index the buttons of a message by their custom ID."""
    return {c["custom_id"]: c for row in _rows(message) for c in row["components"] if "custom_id" in c}


def _rows(message: discord.Message) -> list[dict[str, Any]]:
    """Read the component rows of a message."""
    return [dict(component.to_dict()) for component in message.components]  # pyright: ignore[reportReturnType]


def _refused(interaction: MagicMock) -> str:
    """Return the text the bot answered, after checking it was private."""
    interaction.response.send_message.assert_awaited_once()
    assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True
    return interaction.response.send_message.await_args.args[0]


def _stored(appel_id: int) -> tuple[set[int], tuple[Any, ...]]:
    """Read the marks and the saved counts of a check."""
    appel = attendance.get(appel_id)
    assert appel is not None
    return attendance.present(appel_id), (appel.validated_by, appel.present_count, appel.absent_count)


async def test_the_start_tick_opens_one_appel_and_sends_it_to_the_trainer(hall: Hall) -> None:
    """Two ticks at the start open one check and send one private message, with a button per registered member."""
    registered = hall.register(3)
    hall.publish()

    await hall.tick()
    await hall.tick()

    assert db.connection().execute("SELECT COUNT(*) FROM appel").fetchone()[0] == 1
    (message,) = hall.dm()
    assert message.content.startswith("Salut ")
    assert "commence maintenant" in message.content
    assert message.content.endswith("_0 présent sur 3 inscrits._")
    buttons = _buttons(message)
    assert set(buttons) == {hall.toggle_id(m) for m in registered} | {f"appel:{hall.appel_id}:ok"}
    appel = attendance.get(hall.appel_id)
    assert appel is not None
    assert appel.trainer_id == hall.trainer.id


async def test_the_trainer_marks_present_then_absent_again(hall: Hall) -> None:
    """Touching a name ticks it and the counter follows; touching it again takes the tick back."""
    first, *_ = hall.register(2)
    hall.publish()
    await hall.tick()
    (message,) = hall.dm()

    await hall.click(hall.trainer, message, hall.toggle_id(first))

    (message,) = hall.dm()
    assert attendance.present(hall.appel_id) == {first.id}
    assert message.content.endswith("_1 présent sur 2 inscrits._")
    assert _buttons(message)[hall.toggle_id(first)]["label"].startswith("✓ ")

    await hall.click(hall.trainer, message, hall.toggle_id(first))

    (message,) = hall.dm()
    assert attendance.present(hall.appel_id) == set()
    assert message.content.endswith("_0 présent sur 2 inscrits._")
    assert not _buttons(message)[hall.toggle_id(first)]["label"].startswith("✓ ")


async def test_a_retried_click_on_the_same_message_keeps_the_mark(hall: Hall) -> None:
    """Touching a name twice on a message that still shows it unticked marks it once: the second touch changes nothing."""
    first, *_ = hall.register(2)
    hall.publish()
    await hall.tick()
    (message,) = hall.dm()
    seen = SimpleNamespace(components=list(message.components))

    await hall.forged_click(hall.trainer, hall.toggle_id(first), shown=seen)
    await hall.forged_click(hall.trainer, hall.toggle_id(first), shown=seen)

    assert attendance.present(hall.appel_id) == {first.id}


async def test_a_click_on_a_ticked_name_unticks_it_whatever_is_stored(hall: Hall) -> None:
    """A copy that shows a name ticked unticks it, whatever another copy did meanwhile."""
    first, *_ = hall.register(2)
    hall.publish()
    await hall.tick()
    await hall.click(hall.trainer, hall.dm()[0], hall.toggle_id(first))
    (message,) = hall.dm()
    seen = SimpleNamespace(components=list(message.components))
    attendance.mark(hall.appel_id, [first.id], [])

    await hall.forged_click(hall.trainer, hall.toggle_id(first), shown=seen)

    assert attendance.present(hall.appel_id) == set()


async def test_validation_saves_the_counts_and_renames_the_button(hall: Hall) -> None:
    """Validating stores who validated and the counts, says so, and offers the update from then on."""
    first, second, _third = hall.register(3)
    hall.publish()
    await hall.tick()
    for member in (first, second):
        (message,) = hall.dm()
        await hall.click(hall.trainer, message, hall.toggle_id(member))
    (message,) = hall.dm()

    await hall.click(hall.trainer, message, f"appel:{hall.appel_id}:ok")

    (message,) = hall.dm()
    assert _stored(hall.appel_id) == ({first.id, second.id}, (hall.trainer.id, 2, 1))
    assert "✅ **Appel enregistré** : 2 présents, 1 absent." in message.content
    assert "pas encore enregistré" not in message.content
    assert _buttons(message)[f"appel:{hall.appel_id}:ok"]["label"] == "Mettre à jour l'appel"


async def test_a_change_after_validation_shows_the_pending_counter(hall: Hall) -> None:
    """A correction after the validation shows what is not saved yet, and saves nothing until the update."""
    first, second, _third = hall.register(3)
    hall.publish()
    await hall.tick()
    for custom_id in (hall.toggle_id(first), f"appel:{hall.appel_id}:ok"):
        (message,) = hall.dm()
        await hall.click(hall.trainer, message, custom_id)

    (message,) = hall.dm()
    await hall.click(hall.trainer, message, hall.toggle_id(second))

    (message,) = hall.dm()
    assert "_2 présents sur 3 inscrits, pas encore enregistré._" in message.content
    assert "1 présent, 2 absents" in message.content
    assert _stored(hall.appel_id)[1] == (hall.trainer.id, 1, 2)

    await hall.click(hall.trainer, message, f"appel:{hall.appel_id}:ok")

    (message,) = hall.dm()
    assert _stored(hall.appel_id)[1] == (hall.trainer.id, 2, 1)
    assert "pas encore enregistré" not in message.content


async def test_a_menu_sets_only_its_own_members(hall: Hall) -> None:
    """With thirty registered members, a menu changes the members it lists and leaves the other menu's marks alone."""
    members = hall.register(30)
    hall.publish()
    await hall.tick()
    appel_id = hall.appel_id
    (message,) = hall.dm()
    assert [row["components"][0]["custom_id"] for row in _rows(message)[:2]] == [
        f"appel:{appel_id}:s:0",
        f"appel:{appel_id}:s:1",
    ]

    await hall.trainer.user.select(message, [str(members[27].id)], custom_id=f"appel:{appel_id}:s:1")
    (message,) = hall.dm()
    await hall.trainer.user.select(message, [str(members[0].id), str(members[3].id)], custom_id=f"appel:{appel_id}:s:0")

    assert attendance.present(appel_id) == {members[0].id, members[3].id, members[27].id}

    (message,) = hall.dm()
    await hall.trainer.user.select(message, [str(members[0].id)], custom_id=f"appel:{appel_id}:s:0")

    (message,) = hall.dm()
    assert attendance.present(appel_id) == {members[0].id, members[27].id}
    assert message.content.endswith("_2 présents sur 30 inscrits._")


async def test_the_appel_survives_a_restart(hall: Hall, started_bot: Any) -> None:
    """A second bot on the same database answers a click on the first bot's message, from the database alone."""
    first, *_ = hall.register(2)
    hall.publish()
    await hall.tick()
    (message,) = hall.dm()
    custom_id = hall.toggle_id(first)
    assert custom_id in _buttons(message)
    trainer = make_member(hall.trainer.id)
    started_bot.get_guild = MagicMock(return_value=make_guild(GUILD_ID, [], [trainer, make_member(first.id)]))

    interaction = make_interaction(user_id=hall.trainer.id)
    interaction.client = started_bot
    interaction.guild = None
    interaction.guild_id = None
    interaction.message = MagicMock()
    store = started_bot._connection._view_store
    pattern, item_class = next((p, c) for p, c in store._dynamic_items.items() if p.fullmatch(custom_id))
    item = await item_class.from_custom_id(interaction, MagicMock(), pattern.fullmatch(custom_id))
    assert await item.interaction_check(interaction)
    await item.callback(interaction)

    assert attendance.present(hall.appel_id) == {first.id}
    edited = interaction.response.edit_message.await_args.kwargs
    assert edited["content"].endswith("_1 présent sur 2 inscrits._")


@pytest.mark.parametrize("clicker", ["plain", "codir", "admin_temp"])
async def test_a_member_without_rights_is_refused_and_nothing_is_stored(hall: Hall, clicker: str) -> None:
    """A member who is neither the trainer, a Respo Formation nor an admin gets a private refusal, and no mark is stored."""
    first, *_ = hall.register(2)
    hall.publish()
    await hall.tick()
    before = _stored(hall.appel_id)

    allowed, interaction = await hall.forged_click(getattr(hall, clicker), hall.toggle_id(first))

    assert not allowed
    assert _refused(interaction) == NO_RIGHT
    assert _stored(hall.appel_id) == before
    interaction.response.edit_message.assert_not_awaited()


async def test_a_respo_formation_may_mark_the_appel_of_another_trainer(hall: Hall) -> None:
    """The Respo Formation holders may take the appel in the trainer's place."""
    first, *_ = hall.register(2)
    hall.publish()
    await hall.tick()

    allowed, interaction = await hall.forged_click(hall.respo, hall.toggle_id(first))

    assert allowed
    interaction.response.send_message.assert_not_awaited()
    assert attendance.present(hall.appel_id) == {first.id}


async def test_a_member_who_is_not_registered_cannot_be_marked(hall: Hall) -> None:
    """A button for someone who is not registered changes nothing, even from the trainer."""
    hall.register(2)
    hall.publish()
    await hall.tick()

    await hall.forged_click(hall.trainer, f"appel:{hall.appel_id}:m:{hall.plain.id}")

    assert attendance.present(hall.appel_id) == set()


async def test_a_click_after_seven_days_is_refused_and_nothing_is_stored(hall: Hall) -> None:
    """Eight days after the start the check is closed: the trainer is told so and nothing changes."""
    first, *_ = hall.register(2)
    start = datetime.now(PARIS_TZ) - timedelta(days=8)
    hall.publish(start=start)
    attendance.open_appel(
        GUILD_ID, MESSAGE_ID, 0, trainer_id=hall.trainer.id, starts_at=start, ends_at=start + timedelta(hours=2)
    )
    before = _stored(hall.appel_id)
    closes = int((start + timedelta(days=7)).timestamp())

    allowed, interaction = await hall.forged_click(hall.trainer, hall.toggle_id(first))

    assert not allowed
    assert _refused(interaction) == f"L'appel de **Découpe laser** est fermé depuis le <t:{closes}:D>. Rien n'a été modifié."
    assert _stored(hall.appel_id) == before


async def test_a_server_no_longer_served_refuses_the_click(hall: Hall) -> None:
    """When the server leaves the allow-list, a click on its old message is refused and nothing is stored."""
    first, *_ = hall.register(2)
    hall.publish()
    await hall.tick()
    before = _stored(hall.appel_id)
    bot: Any = hall.env.bot
    bot.guild_access = GuildAccessPolicy(allowed=frozenset({int(OTHER_LISTED_GUILD)}))

    allowed, interaction = await hall.forged_click(hall.trainer, hall.toggle_id(first))

    assert not allowed
    assert _refused(interaction) == "Ce bot n'est pas disponible ici. Rien n'a été modifié."
    assert _stored(hall.appel_id) == before


async def test_an_appel_that_does_not_exist_is_gone_and_a_stranger_cannot_tell_it_from_one(hall: Hall) -> None:
    """A forged ID, or a member of no part of the server, hears the same thing: the appel is gone."""
    first, *_ = hall.register(2)
    hall.publish()
    await hall.tick()
    stranger = hall.env.create_user("stranger")
    before = _stored(hall.appel_id)

    _, missing = await hall.forged_click(hall.trainer, f"appel:{hall.appel_id + 50}:m:{first.id}")
    _, outsider = await hall.forged_click(stranger, hall.toggle_id(first))

    assert _refused(missing) == GONE
    assert _refused(outsider) == GONE
    assert _stored(hall.appel_id) == before


async def test_a_cancelled_training_refuses_the_click(hall: Hall) -> None:
    """A cancelled training closes its appel."""
    first, *_ = hall.register(2)
    hall.publish()
    await hall.tick()
    attendance.handle(hall.appel_id, hall.respo.id, attendance.AlertAction.CANCEL, datetime.now(PARIS_TZ))
    before = _stored(hall.appel_id)

    allowed, interaction = await hall.forged_click(hall.trainer, hall.toggle_id(first))

    assert not allowed
    assert _refused(interaction) == "La formation **Découpe laser** a été annulée : l'appel est fermé. Rien n'a été modifié."
    assert _stored(hall.appel_id) == before


async def test_a_refusal_logs_only_ids(hall: Hall, caplog: pytest.LogCaptureFixture) -> None:
    """The refusal line names the kind and Discord IDs, never a name."""
    first, *_ = hall.register(2)
    hall.publish()
    await hall.tick()
    caplog.set_level(logging.INFO)

    await hall.forged_click(hall.plain, hall.toggle_id(first))

    line = next(r.getMessage() for r in caplog.records if "refused" in r.getMessage())
    assert line == f"[appel] refused kind=missing_right guild_id=None user_id={hall.plain.id}"


async def test_an_unreadable_trainer_opens_the_appel_without_a_dm(hall: Hall) -> None:
    """When the announcement names no member as trainer, the check still opens and no private message goes out."""
    hall.register(2)
    hall.publish(trainer_mention="Camille")

    await hall.tick()

    appel = attendance.get(hall.appel_id)
    assert appel is not None
    assert appel.trainer_id is None
    assert hall.dm() == []


async def test_a_trainer_who_left_gets_no_dm_but_the_appel_opens(hall: Hall) -> None:
    """A trainer no longer on the server opens the check and receives nothing."""
    hall.register(2)
    hall.publish(trainer_mention="<@424242>")

    await hall.tick()

    appel = attendance.get(hall.appel_id)
    assert appel is not None
    assert appel.trainer_id == 424242
    assert all("Fais l'appel" not in m.content for m in hall.dm(hall.respo))


@pytest.mark.parametrize("closed_by", ["cancelled", "old"])
async def test_a_member_without_rights_never_learns_the_name_of_a_closed_appel(hall: Hall, closed_by: str) -> None:
    """A plain member hears the no-right line and a stranger hears the gone line, whether the check is closed or cancelled."""
    first, *_ = hall.register(2)
    start = datetime.now(PARIS_TZ) - (timedelta(days=8) if closed_by == "old" else timedelta())
    hall.publish(start=start)
    attendance.open_appel(
        GUILD_ID, MESSAGE_ID, 0, trainer_id=hall.trainer.id, starts_at=start, ends_at=start + timedelta(hours=2)
    )
    if closed_by == "cancelled":
        attendance.handle(hall.appel_id, hall.respo.id, attendance.AlertAction.CANCEL, datetime.now(PARIS_TZ))
    stranger = hall.env.create_user("stranger")
    before = _stored(hall.appel_id)

    for clicker, expected in ((hall.plain, NO_RIGHT), (stranger, GONE)):
        allowed, interaction = await hall.forged_click(clicker, hall.toggle_id(first))

        assert not allowed
        assert _refused(interaction) == expected
        assert "Découpe laser" not in _refused(interaction)
    assert _stored(hall.appel_id) == before


async def test_a_training_whose_start_window_was_missed_still_gets_its_appel(hall: Hall) -> None:
    """After a restart past the five-minute window, the next tick opens the check once and sends it to the trainer."""
    first, *_ = hall.register(2)
    hall.publish(start=datetime.now(PARIS_TZ) - timedelta(minutes=12))

    await hall.tick()
    await hall.tick()

    assert db.connection().execute("SELECT COUNT(*) FROM appel").fetchone()[0] == 1
    (message,) = hall.dm()
    assert "Découpe laser" in message.content
    assert hall.toggle_id(first) in _buttons(message)
    assert hall.dm(hall.respo) == []  # the Respo's "starts now" export is not sent late


async def test_an_older_announcement_still_gets_its_appel(hall: Hall) -> None:
    """A newer announcement does not hide the training of an older one that has started."""
    hall.register(2)
    hall.publish(start=datetime.now(PARIS_TZ) - timedelta(minutes=30), message_id=MESSAGE_ID)
    hall.publish(start=datetime.now(PARIS_TZ) + timedelta(days=3), message_id=MESSAGE_ID + 1)

    await hall.tick()

    appel = db.connection().execute("SELECT fm_message_id FROM appel").fetchall()
    assert len(appel) == 1
    assert attendance.get(hall.appel_id).message_id == MESSAGE_ID  # pyright: ignore[reportOptionalMemberAccess]
    assert len(hall.dm()) == 1


async def test_a_training_past_the_correction_window_gets_no_appel(hall: Hall) -> None:
    """Eight days after the start nobody can take the attendance any more, so no check is opened."""
    hall.register(2)
    hall.publish(start=datetime.now(PARIS_TZ) - timedelta(days=8))

    await hall.tick()

    assert db.connection().execute("SELECT COUNT(*) FROM appel").fetchone()[0] == 0


async def test_a_training_published_again_gets_one_appel_and_one_message(hall: Hall) -> None:
    """The same training in two announcements gets one check and one private message, on the newer announcement."""
    hall.register(2)
    start = datetime.now(PARIS_TZ) - timedelta(minutes=1)
    hall.publish(start=start, message_id=MESSAGE_ID)
    hall.publish(start=start, message_id=MESSAGE_ID + 1)

    await hall.tick()
    await hall.tick()

    assert db.connection().execute("SELECT COUNT(*) FROM appel").fetchone()[0] == 1
    assert attendance.get(hall.appel_id).message_id == MESSAGE_ID + 1  # pyright: ignore[reportOptionalMemberAccess]
    assert len(hall.dm()) == 1


async def test_an_older_copy_of_a_training_the_newer_announcement_handled_gets_no_second_appel(hall: Hall) -> None:
    """A copy left unhandled in an older announcement does not open a check once the newer one has opened its own."""
    hall.register(2)
    start = datetime.now(PARIS_TZ) - timedelta(minutes=1)
    hall.publish(start=start, message_id=MESSAGE_ID + 1)
    await hall.tick()
    hall.publish(start=start, message_id=MESSAGE_ID)

    await hall.tick()

    assert db.connection().execute("SELECT COUNT(*) FROM appel").fetchone()[0] == 1
    assert len(hall.dm()) == 1


@pytest.mark.parametrize("started_ago", [timedelta(minutes=1), timedelta(days=1)])
async def test_a_training_published_again_after_its_appel_opened_gets_no_second_appel(
    hall: Hall, started_ago: timedelta
) -> None:
    """A newer announcement of a training whose check is already open keeps that check and sends nothing more."""
    hall.register(2)
    start = datetime.now(PARIS_TZ) - started_ago
    hall.publish(start=start, message_id=MESSAGE_ID)
    await hall.tick()
    hall.publish(start=start, message_id=MESSAGE_ID + 1)

    await hall.tick()
    await hall.tick()

    assert db.connection().execute("SELECT COUNT(*) FROM appel").fetchone()[0] == 1
    assert attendance.get(hall.appel_id).message_id == MESSAGE_ID  # pyright: ignore[reportOptionalMemberAccess]
    assert len(hall.dm()) == 1
    assert hall.handled  # the newer copy is done too, so no later tick looks at it again


async def test_only_the_trainings_an_announcement_alone_holds_open_a_check_from_it(hall: Hall) -> None:
    """An older announcement keeps the check of its own training and leaves the one a newer announcement holds again."""
    hall.register(2)
    now = datetime.now(PARIS_TZ)
    own = hall.training(start=now - timedelta(minutes=2), name="Soudure")
    again = hall.training(start=now - timedelta(minutes=1))
    hall.store([own, again], MESSAGE_ID)
    hall.store([again], MESSAGE_ID + 1)

    await hall.tick()

    checks = db.connection().execute("SELECT m.message_id, a.position FROM appel a JOIN fm_message m ON m.id = a.fm_message_id")
    assert {(row["message_id"], row["position"]) for row in checks} == {(MESSAGE_ID, 0), (MESSAGE_ID + 1, 0)}
    assert len(hall.dm()) == 2


async def test_an_older_announcement_sends_no_hour_before_reminder(hall: Hall) -> None:
    """An older announcement visited for a training that started sends no reminder for its other trainings."""
    registered = hall.register(2)
    now = datetime.now(PARIS_TZ)
    hall.store(
        [hall.training(start=now - timedelta(minutes=30)), hall.training(start=now + timedelta(hours=1), name="Soudure")],
        MESSAGE_ID,
    )
    hall.publish(start=now + timedelta(days=3), message_id=MESSAGE_ID + 1)

    await hall.tick()

    (message,) = hall.dm()
    assert "Découpe laser" in message.content
    assert all(hall.dm(member) == [] for member in registered)


async def test_a_failed_opening_is_tried_again_at_the_next_tick(hall: Hall, monkeypatch: pytest.MonkeyPatch) -> None:
    """A storage error at the start leaves the training unhandled, so the next tick opens its check once."""
    hall.register(2)
    hall.publish(start=datetime.now(PARIS_TZ) - timedelta(minutes=1))
    real_open = attendance.open_appel
    attempts: list[int] = []

    def locked_once(*args: Any, **kwargs: Any) -> int:
        attempts.append(1)
        if len(attempts) == 1:
            raise sqlite3.OperationalError
        return real_open(*args, **kwargs)

    monkeypatch.setattr(attendance, "open_appel", locked_once)

    await hall.tick()

    assert db.connection().execute("SELECT COUNT(*) FROM appel").fetchone()[0] == 0
    assert not hall.handled
    assert hall.dm() == []
    assert hall.dm(hall.respo) == []

    await hall.tick()
    await hall.tick()

    assert db.connection().execute("SELECT COUNT(*) FROM appel").fetchone()[0] == 1
    assert hall.handled
    assert len(hall.dm()) == 1
    assert len(hall.dm(hall.respo)) == 1


@pytest.mark.parametrize("trainer_mention", ["Camille", "<@424242>"])
async def test_a_check_nobody_can_be_sent_is_still_handled(hall: Hall, trainer_mention: str) -> None:
    """Once the check exists the start is done, whether or not its trainer can be reached."""
    hall.register(2)
    hall.publish(trainer_mention=trainer_mention)

    await hall.tick()

    assert db.connection().execute("SELECT COUNT(*) FROM appel").fetchone()[0] == 1
    assert hall.handled


def _lists(hall: Hall, who: Any) -> list[discord.Message]:
    """List what the bot sent a member in private, leaving out the start reminder."""
    return [m for m in hall.dm(who) if "commence maintenant" not in m.content]


async def test_each_respo_formation_gets_the_lists_after_a_validation(hall: Hall) -> None:
    """Every Respo Formation gets one private message naming who validated, the present and the absent members."""
    first, second, third = hall.register(3)
    other_respo = hall.member("respo2", RoleNames.TRAININGS_MANAGER)
    hall.publish()
    await hall.tick()

    await hall.validate(first, third)

    for respo in (hall.respo, other_respo):
        (message,) = _lists(hall, respo)
        assert message.content.startswith("Salut !\nL'appel de **Découpe laser** (")
        assert f") a été fait par <@{hall.trainer.id}>.\n\n" in message.content
        assert f"**Présents (2)** : <@{first.id}>, <@{third.id}>\n" in message.content
        assert message.content.endswith(f"**Absents (1)** : <@{second.id}>")
    assert _lists(hall, hall.plain) == []


async def test_an_update_says_updated_and_goes_out_again(hall: Hall) -> None:
    """A correction sends the lists again, worded as an update; the first validation says the check was made."""
    first, second = hall.register(2)
    hall.publish()
    await hall.tick()
    await hall.validate(first)
    (message,) = hall.dm()
    await hall.click(hall.trainer, message, hall.toggle_id(second))
    (message,) = hall.dm()
    await hall.click(hall.trainer, message, f"appel:{hall.appel_id}:ok")

    first_dm, update = _lists(hall, hall.respo)
    assert "a été fait par" in first_dm.content
    assert f"a été mis à jour par <@{hall.trainer.id}>." in update.content
    assert "**Présents (2)**" in update.content
    assert "**Absents (0)** : aucun" in update.content
    lines = [m.content for m in hall.logs.history()]  # pyright: ignore[reportAttributeAccessIssue]
    assert "validé par" in lines[0]
    assert "mis à jour par" in lines[1]


async def test_a_long_list_is_split_under_the_limit(hall: Hall) -> None:
    """A hundred registered members all present make several messages, each under the limit, none losing a mention."""
    registered = hall.register(100)
    hall.publish()
    await hall.tick()

    # Mark them through the store: a hundred clicks add nothing to what is tested here.
    ids = [m.id for m in registered]
    attendance.mark(hall.appel_id, ids, ids)
    await hall.validate()

    texts = [m.content for m in _lists(hall, hall.respo)]
    assert len(texts) > 1
    assert all(len(text) <= MAX_MSG_CHARS for text in texts)
    joined = "\n".join(texts)
    assert all(f"<@{m.id}>" in joined for m in registered)
    assert "**Présents (100)**" in joined


async def test_an_empty_list_reads_none(hall: Hall) -> None:
    """With nobody present the present list reads aucun, and with nobody registered the absent list does too."""
    hall.register(2)
    hall.publish()
    await hall.tick()

    await hall.validate()

    (message,) = _lists(hall, hall.respo)
    assert "**Présents (0)** : aucun\n**Absents (2)** : <@" in message.content


async def test_a_respo_with_closed_dms_does_not_stop_the_others(
    hall: Hall, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A Respo whose DM fails gets nothing and is logged by ID; the next Respo still gets the lists and the line goes out."""
    hall.register(2)
    other_respo = hall.member("respo2", RoleNames.TRAININGS_MANAGER)
    hall.publish()
    await hall.tick()
    blocked = min(hall.respo.id, other_respo.id)
    real_send = discord.Member.send

    async def send(member: Any, *args: Any, **kwargs: Any) -> Any:
        if member.id == blocked:
            raise discord.Forbidden(MagicMock(status=403), "closed")
        return await real_send(member, *args, **kwargs)

    monkeypatch.setattr(discord.Member, "send", send)
    caplog.set_level(logging.INFO)

    await hall.validate()

    survivor = other_respo if blocked == hall.respo.id else hall.respo
    stuck = hall.respo if blocked == hall.respo.id else other_respo
    assert len(_lists(hall, survivor)) == 1
    assert _lists(hall, stuck) == []
    assert f"Cannot DM user {blocked} in guild {GUILD_ID}" in caplog.text
    assert f"Sent attendance lists {hall.appel_id} DM to user {blocked}" not in caplog.text
    assert f"Sent attendance lists {hall.appel_id} DM to user {survivor.id}" in caplog.text
    assert len(hall.logs.history()) == 1  # pyright: ignore[reportAttributeAccessIssue]


async def test_the_log_channel_gets_counts_without_any_registrant(hall: Hall) -> None:
    """The log line has the training, who validated and the counts, and no registrant ID, mention or name."""
    registered = hall.register(3)
    hall.publish()
    await hall.tick()

    await hall.validate(registered[0])

    (line,) = [m.content for m in hall.logs.history()]  # pyright: ignore[reportAttributeAccessIssue]
    assert line.startswith("Appel de **Découpe laser** (<t:")
    assert f"validé par <@{hall.trainer.id}> : 1 présent, 2 absents." in line
    for member in registered:
        assert str(member.id) not in line
        assert member.name not in line


async def test_the_log_line_notifies_nobody(hall: Hall, monkeypatch: pytest.MonkeyPatch) -> None:
    """The log line is sent with no mention allowed, so the validator is not pinged."""
    hall.register(1)
    hall.publish()
    await hall.tick()
    sends: list[dict[str, Any]] = []
    real_send = discord.TextChannel.send

    async def send(channel: Any, *args: Any, **kwargs: Any) -> Any:
        sends.append(kwargs)
        return await real_send(channel, *args, **kwargs)

    monkeypatch.setattr(discord.TextChannel, "send", send)

    await hall.validate()

    (kwargs,) = [k for k in sends if "allowed_mentions" in k]
    assert kwargs["allowed_mentions"].to_dict() == discord.AllowedMentions.none().to_dict()


async def test_the_lists_report_what_was_saved_not_what_a_toggle_changed_meanwhile(
    hall: Hall, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A mark landing while the validated message is edited is not reported as saved: lists and counts match the save."""
    first, second = hall.register(2)
    hall.publish()
    await hall.tick()
    real_show = ui_attendance._show

    async def show(interaction: Any, appel_id: int) -> None:
        stored = attendance.get(appel_id)
        if stored is not None and stored.validated_at is not None:
            attendance.mark(appel_id, [second.id], [second.id])
        await real_show(interaction, appel_id)

    monkeypatch.setattr(ui_attendance, "_show", show)

    await hall.validate(first)

    stored = attendance.get(hall.appel_id)
    assert stored is not None
    assert (stored.present_count, stored.absent_count) == (1, 1)
    (message,) = _lists(hall, hall.respo)
    assert f"**Présents (1)** : <@{first.id}>\n" in message.content
    assert message.content.endswith(f"**Absents (1)** : <@{second.id}>")
    (line,) = [m.content for m in hall.logs.history()]  # pyright: ignore[reportAttributeAccessIssue]
    assert "1 présent, 1 absent." in line


async def test_a_saved_validation_goes_out_even_when_editing_the_message_fails(
    hall: Hall, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the edit of the clicked message fails after the save, the Respo lists and the log line still go out."""
    first, _second = hall.register(2)
    hall.publish()
    await hall.tick()
    (message,) = hall.dm()
    await hall.click(hall.trainer, message, hall.toggle_id(first))

    async def show(*_: Any) -> None:
        raise discord.NotFound(MagicMock(status=404), "expired")

    monkeypatch.setattr(ui_attendance, "_show", show)

    with pytest.raises(discord.NotFound):
        await hall.forged_click(hall.trainer, f"appel:{hall.appel_id}:ok")

    stored = attendance.get(hall.appel_id)
    assert stored is not None
    assert stored.validated_at is not None
    (lists,) = _lists(hall, hall.respo)
    assert f"**Présents (1)** : <@{first.id}>" in lists.content
    assert len(hall.logs.history()) == 1  # pyright: ignore[reportAttributeAccessIssue]


async def _until(condition: Any) -> None:
    """Let the other tasks run until the condition holds, failing the test rather than waiting forever."""

    async def spin() -> None:
        while not condition():
            await asyncio.sleep(0)

    await asyncio.wait_for(spin(), timeout=5)


async def test_a_report_a_newer_save_replaced_is_not_sent(hall: Hall, monkeypatch: pytest.MonkeyPatch) -> None:
    """A validation whose message edit is slow sends nothing once a newer one is saved: the last report is the newest."""
    first, second = hall.register(2)
    hall.publish()
    await hall.tick()
    await hall.validate(first)
    real_show = ui_attendance._show
    held = asyncio.Event()
    release = asyncio.Event()
    slow: list[int] = []

    async def show(interaction: Any, appel_id: int) -> None:
        if not slow:
            slow.append(appel_id)
            held.set()
            await release.wait()
        await real_show(interaction, appel_id)

    monkeypatch.setattr(ui_attendance, "_show", show)
    # SimCord runs one click at a time, so the slow validation is a callback of its own, as discord.py would run it.
    slow_save = asyncio.create_task(hall.forged_click(hall.trainer, f"appel:{hall.appel_id}:ok"))
    await asyncio.wait_for(held.wait(), timeout=5)

    await hall.validate(second)
    release.set()
    await slow_save

    stored = attendance.get(hall.appel_id)
    assert stored is not None
    assert (stored.present_count, stored.absent_count) == (2, 0)
    texts = [m.content for m in _lists(hall, hall.respo)]
    assert len(texts) == 2
    assert "a été fait par" in texts[0]
    assert "**Présents (2)**" in texts[1]
    lines = [m.content for m in hall.logs.history()]  # pyright: ignore[reportAttributeAccessIssue]
    assert len(lines) == 2
    assert lines[-1].endswith(": 2 présents, 0 absent.")


async def test_reports_of_one_check_go_out_in_the_order_of_the_saves(hall: Hall, monkeypatch: pytest.MonkeyPatch) -> None:
    """A report still being sent finishes before the next one of the check starts: each names who saved it, the newest last."""
    first, second = hall.register(2)
    other_respo = hall.member("respo2", RoleNames.TRAININGS_MANAGER)
    hall.publish()
    await hall.tick()
    real_send = discord.Member.send
    stuck = asyncio.Event()
    release = asyncio.Event()
    sending: list[str] = []

    async def send(member: Any, *args: Any, **kwargs: Any) -> Any:
        if member.id == hall.respo.id and args and not sending:
            sending.append(args[0])
            stuck.set()
            await release.wait()
        return await real_send(member, *args, **kwargs)

    monkeypatch.setattr(discord.Member, "send", send)
    real_notify = ui_attendance.notify_validation
    reporting: list[int] = []

    async def notify(guild: Any, saved: Any) -> None:
        reporting.append(saved.appel.present_count)
        await real_notify(guild, saved)

    monkeypatch.setattr(ui_attendance, "notify_validation", notify)
    attendance.mark(hall.appel_id, [first.id], [first.id])
    older = asyncio.create_task(hall.forged_click(hall.trainer, f"appel:{hall.appel_id}:ok"))
    await asyncio.wait_for(stuck.wait(), timeout=5)
    attendance.mark(hall.appel_id, [second.id], [second.id])
    newer = asyncio.create_task(hall.forged_click(other_respo, f"appel:{hall.appel_id}:ok"))
    await _until(lambda: len(reporting) == 2)

    release.set()
    await asyncio.gather(older, newer)

    texts = [m.content for m in _lists(hall, hall.respo)]
    assert len(texts) == 2
    assert "**Présents (1)**" in texts[0]
    assert f"fait par <@{hall.trainer.id}>." in texts[0]
    assert "**Présents (2)**" in texts[1]
    assert f"mis à jour par <@{other_respo.id}>." in texts[1]


async def test_a_report_in_flight_does_not_hold_back_the_report_of_another_check(
    hall: Hall, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A report stuck in sending holds back the later reports of its own check only, not those of another check."""
    hall.register(1)
    now = datetime.now(PARIS_TZ)
    hall.store([hall.training(start=now), hall.training(start=now, name="Soudure")])
    await hall.tick()
    slow, other = [row["id"] for row in db.connection().execute("SELECT id FROM appel ORDER BY position")]
    real_send = discord.Member.send
    stuck = asyncio.Event()
    release = asyncio.Event()

    async def send(member: Any, *args: Any, **kwargs: Any) -> Any:
        if member.id == hall.respo.id and args and "Découpe laser" in args[0] and not stuck.is_set():
            stuck.set()
            await release.wait()
        return await real_send(member, *args, **kwargs)

    monkeypatch.setattr(discord.Member, "send", send)
    slow_report = asyncio.create_task(hall.forged_click(hall.trainer, f"appel:{slow}:ok"))
    await asyncio.wait_for(stuck.wait(), timeout=5)

    try:
        await asyncio.wait_for(hall.forged_click(hall.trainer, f"appel:{other}:ok"), timeout=5)
    finally:
        release.set()
    await slow_report

    texts = [m.content for m in _lists(hall, hall.respo)]
    assert len(texts) == 2
    assert "Soudure" in texts[0]
    assert "Découpe laser" in texts[1]


async def test_swapping_a_present_member_for_another_is_reported_though_the_counts_match(hall: Hall) -> None:
    """A correction that keeps the counts but changes who is present still sends the lists and the log line."""
    first, second = hall.register(2)
    hall.publish()
    await hall.tick()
    await hall.validate(first)
    attendance.mark(hall.appel_id, [first.id, second.id], [second.id])

    await hall.forged_click(hall.trainer, f"appel:{hall.appel_id}:ok")

    _first_dm, update = _lists(hall, hall.respo)
    assert f"**Présents (1)** : <@{second.id}>\n" in update.content
    assert len(hall.logs.history()) == 2  # pyright: ignore[reportAttributeAccessIssue]


async def test_a_failed_read_before_the_report_still_sends_what_can_be_sent(
    hall: Hall, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A storage error while checking for a newer save is logged by ID and the lists and the log line still go out."""
    first, _second = hall.register(2)
    hall.publish()
    await hall.tick()
    real_notify = ui_attendance.notify_validation
    real_get = attendance.get
    armed = False

    async def notify(guild: Any, saved: Any) -> None:
        nonlocal armed
        armed = True
        await real_notify(guild, saved)

    def get(appel_id: int) -> Any:
        if armed:
            msg = "disk I/O error"
            raise sqlite3.OperationalError(msg)
        return real_get(appel_id)

    monkeypatch.setattr(ui_attendance, "notify_validation", notify)
    monkeypatch.setattr(attendance, "get", get)
    caplog.set_level(logging.INFO)

    await hall.validate(first)

    (lists,) = _lists(hall, hall.respo)
    assert f"**Présents (1)** : <@{first.id}>" in lists.content
    assert len(hall.logs.history()) == 1  # pyright: ignore[reportAttributeAccessIssue]
    assert f"Could not check appel {hall.appel_id} in guild {GUILD_ID} for a newer validation" in caplog.text
    assert "Découpe laser" not in caplog.text


async def test_a_server_without_a_log_channel_still_sends_the_lists(hall: Hall, caplog: pytest.LogCaptureFixture) -> None:
    """With no log channel set, nothing raises, the Respo still get the lists, and the warning holds IDs only."""
    first, _second = hall.register(2)
    with db.transaction() as conn:
        conn.execute("DELETE FROM guild_settings WHERE guild_id = ?", (GUILD_ID,))
    hall.publish()
    await hall.tick()
    caplog.set_level(logging.INFO)

    await hall.validate(first)

    (lists,) = _lists(hall, hall.respo)
    assert f"**Présents (1)** : <@{first.id}>" in lists.content
    assert hall.logs.history() == []  # pyright: ignore[reportAttributeAccessIssue]
    assert f"No log channel for the counts of appel {hall.appel_id} in guild {GUILD_ID}." in caplog.text
    assert "Découpe laser" not in caplog.text


async def test_a_trainer_who_only_holds_codir_takes_the_appel(hall: Hall) -> None:
    """The trainer named in the announcement takes the check whatever their role, even with CoDir alone."""
    first, _second = hall.register(2)
    lead = hall.member("lead", RoleNames.CODIR)
    hall.publish(trainer_mention=f"<@{lead.id}>")
    await hall.tick()
    (message,) = hall.dm(lead)

    await hall.click(lead, message, hall.toggle_id(first))
    (message,) = hall.dm(lead)
    await hall.click(lead, message, f"appel:{hall.appel_id}:ok")

    stored = attendance.get(hall.appel_id)
    assert stored is not None
    assert (stored.validated_by, stored.present_count, stored.absent_count) == (lead.id, 1, 1)
    (lists,) = _lists(hall, hall.respo)
    assert f"a été fait par <@{lead.id}>." in lists.content


def _close_dms(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every private message fail the way a closed DM does."""

    async def closed(_user: Any) -> bool:
        return False

    monkeypatch.setattr("fablabot.helpers.utils._can_dm_user", closed)


def _threads(channel: Any) -> list[Any]:
    """List the threads made under a channel."""
    return list(channel.threads)


def _thread_members(hall: Hall, thread: Any) -> set[int]:
    """List the members a thread holds, the bot included."""
    return set(hall.env.backend.channels[thread.id].thread_members)


async def test_closed_dms_open_a_private_thread_in_a_text_announcement_channel(
    hall: Hall, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the trainer's DMs closed the appel goes to a private thread of the announcement's channel."""
    hall.register(2)
    hall.publish()
    _close_dms(monkeypatch)
    created: list[dict[str, Any]] = []
    real_create = discord.TextChannel.create_thread

    async def create_thread(channel: Any, **kwargs: Any) -> Any:
        created.append(kwargs)
        return await real_create(channel, **kwargs)

    monkeypatch.setattr(discord.TextChannel, "create_thread", create_thread)

    await hall.tick()

    (thread,) = _threads(hall.general)
    assert created == [
        {
            "name": "Appel : Découpe laser",
            "type": discord.ChannelType.private_thread,
            "invitable": False,
            "auto_archive_duration": 10080,
        }
    ]
    (message,) = thread.history()
    assert message.content.startswith(
        f"<@{hall.trainer.id}> tes MP sont fermés, alors l'appel de **Découpe laser** se fait ici. "
        "Ce fil n'est visible que de toi et du staff.\n\n**Fais l'appel**"
    )
    assert message.content.endswith("_0 présent sur 2 inscrits._")
    assert f"appel:{hall.appel_id}:ok" in _buttons(message)
    appel = attendance.get(hall.appel_id)
    assert appel is not None
    assert appel.thread_id == thread.id
    assert hall.dm() == []


async def test_an_announcement_channel_sends_the_thread_to_the_commands_channel(
    hall: Hall, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An announcement posted in a news channel cannot hold a private thread: the commands channel does."""
    commands = hall.guild.create_text_channel("commandes_bot")
    hall.general = hall.guild.create_news_channel("annonces")
    hall.register(1)
    hall.publish()
    _close_dms(monkeypatch)

    await hall.tick()

    assert _threads(hall.general) == []
    (thread,) = _threads(commands)
    appel = attendance.get(hall.appel_id)
    assert appel is not None
    assert appel.thread_id == thread.id
    assert len(thread.history()) == 1


async def test_the_thread_holds_the_trainer_and_each_respo_formation(hall: Hall, monkeypatch: pytest.MonkeyPatch) -> None:
    """The trainer and every Respo Formation are added to the thread; nobody else is."""
    other_respo = hall.member("respo2", RoleNames.TRAININGS_MANAGER)
    hall.register(1)
    hall.publish()
    _close_dms(monkeypatch)

    await hall.tick()

    (thread,) = _threads(hall.general)
    members = _thread_members(hall, thread)
    assert {hall.trainer.id, hall.respo.id, other_respo.id} <= members
    assert not members & {hall.plain.id, hall.codir.id, hall.admin_temp.id}


async def test_a_relaunch_reuses_the_thread(hall: Hall, monkeypatch: pytest.MonkeyPatch) -> None:
    """Delivering the appel again sends to the thread already stored and makes no second one."""
    hall.register(1)
    hall.publish()
    _close_dms(monkeypatch)
    await hall.tick()
    appel = attendance.get(hall.appel_id)
    assert appel is not None
    guild = hall.env.bot.get_guild(GUILD_ID)  # pyright: ignore[reportAttributeAccessIssue]
    assert guild is not None

    assert await deliver(guild, appel)

    (thread,) = _threads(hall.general)
    assert len(thread.history()) == 2
    assert attendance.get(hall.appel_id).thread_id == thread.id  # pyright: ignore[reportOptionalMemberAccess]


async def test_a_thread_refused_by_discord_leaves_the_appel_open_and_logged(
    hall: Hall, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """When no channel accepts the thread the appel stays open, nothing is stored for it, and the failure is logged by ID."""
    hall.guild.create_text_channel("commandes_bot")
    hall.register(1)
    hall.publish()
    _close_dms(monkeypatch)

    async def refuse(_channel: Any, **_kwargs: Any) -> Any:
        raise discord.Forbidden(MagicMock(status=403), "Missing Permissions")

    monkeypatch.setattr(discord.TextChannel, "create_thread", refuse)
    caplog.set_level(logging.INFO)

    await hall.tick()

    appel = attendance.get(hall.appel_id)
    assert appel is not None
    assert appel.thread_id is None
    assert appel.is_open(datetime.now(PARIS_TZ))
    assert f"Could not open a thread for appel {hall.appel_id} in guild {GUILD_ID}" in caplog.text
    assert hall.handled


async def test_a_respo_formation_takes_the_appel_in_the_thread(hall: Hall, monkeypatch: pytest.MonkeyPatch) -> None:
    """A Respo Formation clicks the thread's buttons and the mark is stored."""
    first, *_ = hall.register(2)
    hall.publish()
    _close_dms(monkeypatch)
    await hall.tick()
    (thread,) = _threads(hall.general)
    (message,) = thread.history()

    await hall.respo.click(message, custom_id=hall.toggle_id(first))

    assert attendance.present(hall.appel_id) == {first.id}
    (message,) = thread.history()
    assert message.content.endswith("_1 présent sur 2 inscrits._")
    assert "se fait ici" in message.content


async def test_a_member_who_only_manages_threads_is_refused(hall: Hall, monkeypatch: pytest.MonkeyPatch) -> None:
    """Seeing private threads through Manage Threads gives no right to take the appel."""
    first, *_ = hall.register(2)
    hall.publish()
    _close_dms(monkeypatch)
    await hall.tick()
    role = hall.guild.create_role("modos", permissions=Permissions(manage_threads=True))
    moderator = hall.guild.add_member(hall.env.create_user("modo"), roles=[role])
    before = _stored(hall.appel_id)

    allowed, interaction = await hall.forged_click(moderator, hall.toggle_id(first))

    assert not allowed
    assert _refused(interaction) == NO_RIGHT
    assert _stored(hall.appel_id) == before


async def test_a_thread_made_while_another_won_the_race_is_dropped(hall: Hall, monkeypatch: pytest.MonkeyPatch) -> None:
    """When another delivery stored its thread first, the second thread is deleted and the first one is used."""
    hall.register(1)
    hall.publish()
    _close_dms(monkeypatch)
    backend = hall.env.backend
    winner = backend.create_thread(
        hall.general.id, "Appel : Découpe laser", hall.trainer.id, type=discord.ChannelType.private_thread.value
    )
    backend.add_thread_member(winner.id, hall.trainer.id)
    await hall.env.settle()
    real_set = attendance.set_thread

    def lose(appel_id: int, thread_id: int) -> bool:
        assert real_set(appel_id, winner.id)
        return real_set(appel_id, thread_id)

    monkeypatch.setattr(attendance, "set_thread", lose)

    await hall.tick()

    threads = [c.id for c in backend.channels.values() if c.is_thread]
    assert threads == [winner.id]
    assert len(backend.messages[winner.id]) == 1
    assert attendance.get(hall.appel_id).thread_id == winner.id  # pyright: ignore[reportOptionalMemberAccess]


async def test_a_refusal_in_the_announcement_channel_falls_back_to_the_commands_channel(
    hall: Hall, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When Discord refuses the thread in the announcement's channel only, the commands channel holds it."""
    commands = hall.guild.create_text_channel("commandes_bot")
    hall.register(1)
    hall.publish()
    _close_dms(monkeypatch)
    real_create = discord.TextChannel.create_thread

    async def create_thread(channel: Any, **kwargs: Any) -> Any:
        if channel.id == hall.general.id:
            raise discord.Forbidden(MagicMock(status=403), "Missing Permissions")
        return await real_create(channel, **kwargs)

    monkeypatch.setattr(discord.TextChannel, "create_thread", create_thread)

    await hall.tick()

    assert _threads(hall.general) == []
    (thread,) = _threads(commands)
    assert len(thread.history()) == 1
    assert attendance.get(hall.appel_id).thread_id == thread.id  # pyright: ignore[reportOptionalMemberAccess]


def _two_trainers(hall: Hall, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Announce two trainings starting now, the first trainer with open DMs and the second with closed ones.

    Returns:
        Any: The second trainer, whose appel goes to a thread.
    """
    closed_trainer = hall.member("closed")
    hall.register(1)
    start = datetime.now(PARIS_TZ)
    hall.store(
        [
            hall.training(start=start),
            hall.training(start=start, name="Fraiseuse", trainer_mention=f"<@{closed_trainer.id}>"),
        ]
    )

    async def can_dm(user: Any) -> bool:
        return user.id != closed_trainer.id

    monkeypatch.setattr("fablabot.helpers.utils._can_dm_user", can_dm)
    return closed_trainer


async def test_a_network_error_on_the_thread_does_not_send_the_dms_again(
    hall: Hall, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A timeout while making the thread is logged and the tick still records the start: the first trainer gets one DM."""
    _two_trainers(hall, monkeypatch)
    calls: list[int] = []

    async def timeout(_channel: Any, **_kwargs: Any) -> Any:
        calls.append(1)
        raise TimeoutError

    monkeypatch.setattr(discord.TextChannel, "create_thread", timeout)

    await hall.tick()
    await hall.tick()

    assert len(hall.dm()) == 1
    assert calls == [1]
    assert "Could not make a thread" in caplog.text


async def test_a_storage_error_on_the_thread_deletes_it_and_sends_no_dm_again(
    hall: Hall, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A failed write of the thread ID is logged, the thread just made is deleted, and the first trainer gets one DM."""
    _two_trainers(hall, monkeypatch)

    def fail(_appel_id: int, _thread_id: int) -> bool:
        raise sqlite3.OperationalError

    monkeypatch.setattr(attendance, "set_thread", fail)

    await hall.tick()
    await hall.tick()

    assert len(hall.dm()) == 1
    assert _threads(hall.general) == []
    assert "Could not store thread" in caplog.text


async def test_an_error_sending_to_the_thread_is_logged_and_leaves_the_tick_whole(
    hall: Hall, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A network error on the thread's message does not abort the tick that already sent another trainer's DM."""
    _two_trainers(hall, monkeypatch)

    async def timeout(_thread: Any, *_args: Any, **_kwargs: Any) -> Any:
        raise TimeoutError

    monkeypatch.setattr(discord.Thread, "send", timeout)

    await hall.tick()
    await hall.tick()

    assert len(hall.dm()) == 1
    assert "to thread" in caplog.text


OVERDUE = timedelta(minutes=90)
ALERT_ACTIONS = ("r", "i", "c")
IGNORED = "\N{HEAVY MINUS SIGN}"


async def _overdue(hall: Hall, *, registered: int = 2, **publish: Any) -> None:
    """Register members, store a one-hour training that began ninety minutes ago, and run the tick that alerts."""
    hall.register(registered)
    hall.publish(start=datetime.now(PARIS_TZ) - OVERDUE, duration="1h", **publish)
    await hall.tick()


def _dms(member: Any) -> list[discord.Message]:
    """List what the bot sent a member in private, none when no private channel was ever opened."""
    channel = member.user.dm_channel
    return channel.history() if channel else []


def _alert(hall: Hall, who: Any) -> discord.Message:
    """Find the alert a Respo Formation got, as it stands now."""
    (found,) = [m for m in _dms(who) if f"appel:{hall.appel_id}:a:r" in _buttons(m)]
    return found


def _alert_id(hall: Hall, action: str) -> str:
    """Write the custom ID of an alert button."""
    return f"appel:{hall.appel_id}:a:{action}"


def _answer(hall: Hall, line: str) -> str:
    """Write an answer line with the time the check stored for it, in the form the alert shows."""
    appel = attendance.get(hall.appel_id)
    assert appel is not None
    assert appel.handled_at is not None
    return line.replace("{at}", discord_timestamp(appel.handled_at, "t"))


def _stored_answer(hall: Hall) -> tuple[Any, ...]:
    """Read who answered the alert, how and when."""
    appel = attendance.get(hall.appel_id)
    assert appel is not None
    return appel.handled_by, appel.handled_action, appel.handled_at


async def test_the_end_of_the_slot_alerts_each_respo_once(hall: Hall) -> None:
    """When the slot is over and nobody made the appel, each Respo Formation gets one alert, however many ticks pass."""
    other_respo = hall.member("respo2", RoleNames.TRAININGS_MANAGER)
    await _overdue(hall)
    await hall.tick()

    appel = attendance.get(hall.appel_id)
    assert appel is not None
    slot = f"{discord_timestamp(appel.starts_at)} à {discord_timestamp(appel.ends_at, 't')}"
    for respo in (hall.respo, other_respo):
        (message,) = _dms(respo)
        assert message.content == (
            f"Salut !\n<@{hall.trainer.id}> n'a pas fait l'appel de **Découpe laser** ({slot}), et le créneau est terminé.\n\n"
            "Relance le formateur, ignore, ou annule la formation si elle n'a pas eu lieu."
        )
        buttons = _buttons(message)
        assert [(b["label"], b["style"], b.get("disabled", False)) for b in buttons.values()] == [
            ("Relancer le formateur", 1, False),
            ("Ignorer", 2, False),
            ("Annuler la formation", 4, False),
        ]
        assert list(buttons) == [_alert_id(hall, a) for a in ALERT_ACTIONS]
    assert not [m for m in _dms(hall.trainer) if "n'a pas fait l'appel" in m.content]
    assert appel.alert_sent
    assert len(attendance.alert_messages(hall.appel_id)) == 2


async def test_no_alert_before_the_end_or_after_a_validation(hall: Hall) -> None:
    """A slot still running is not alerted, nor one whose appel was saved before the end."""
    hall.register(1)
    hall.publish(start=datetime.now(PARIS_TZ) - timedelta(minutes=30), duration="2h")
    await hall.tick()
    assert _dms(hall.respo) == []

    await hall.validate()
    attendance_end = (datetime.now(PARIS_TZ) - timedelta(minutes=1)).isoformat()
    db.connection().execute("UPDATE appel SET ends_at = ?", (attendance_end,))
    await hall.tick()

    assert [m for m in _dms(hall.respo) if "n'a pas fait l'appel" in m.content] == []
    appel = attendance.get(hall.appel_id)
    assert appel is not None
    assert not appel.alert_sent


async def test_no_alert_once_the_appel_closed(hall: Hall) -> None:
    """A check past its correction window, or one answered already, is not alerted."""
    hall.register(1)
    long_ago = datetime.now(PARIS_TZ) - timedelta(days=8)
    hall.publish(start=long_ago, duration="1h")
    attendance.open_appel(
        GUILD_ID, MESSAGE_ID, 0, trainer_id=hall.trainer.id, starts_at=long_ago, ends_at=long_ago + timedelta(hours=1)
    )

    await hall.tick()

    assert _dms(hall.respo) == []
    appel = attendance.get(hall.appel_id)
    assert appel is not None
    assert not appel.alert_sent


async def test_relaunch_sends_the_trainer_the_appel_again(hall: Hall) -> None:
    """Relaunching sends the trainer the appel again in private and shows who did it, with the buttons off."""
    await _overdue(hall)
    assert len(_dms(hall.trainer)) == 1

    await hall.click(hall.respo, _alert(hall, hall.respo), _alert_id(hall, "r"))

    again = _dms(hall.trainer)
    assert len(again) == 2
    assert "a eu lieu le" in again[1].content
    assert f"appel:{hall.appel_id}:ok" in _buttons(again[1])
    alert = _alert(hall, hall.respo)
    assert alert.content.endswith(_answer(hall, f"🔁 **Formateur relancé par <@{hall.respo.id}>** à {{at}}."))
    assert all(b.get("disabled") for b in _buttons(alert).values())
    assert _stored_answer(hall)[:2] == (hall.respo.id, attendance.AlertAction.RELAUNCH)


def _close_dms_of(monkeypatch: pytest.MonkeyPatch, user_id: int) -> None:
    """Make private messages to one member fail the way a closed DM does."""

    async def can_dm(member: Any) -> bool:
        return member.id != user_id

    monkeypatch.setattr("fablabot.helpers.utils._can_dm_user", can_dm)


async def test_relaunch_falls_back_to_the_thread(hall: Hall, monkeypatch: pytest.MonkeyPatch) -> None:
    """A trainer whose DMs closed since the start gets the relaunched appel in a private thread."""
    await _overdue(hall)
    _close_dms_of(monkeypatch, hall.trainer.id)

    await hall.click(hall.respo, _alert(hall, hall.respo), _alert_id(hall, "r"))

    (thread,) = _threads(hall.general)
    (message,) = thread.history()
    assert message.content.startswith(f"<@{hall.trainer.id}> tes MP sont fermés")
    assert len(_dms(hall.trainer)) == 1
    assert _stored_answer(hall)[1] is attendance.AlertAction.RELAUNCH


async def test_a_relaunch_that_reaches_nobody_says_so_and_leaves_the_alert_open(
    hall: Hall, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With DMs closed and no thread possible, the clicker is told, the alert stays answerable and nothing is stored."""
    await _overdue(hall)
    _close_dms_of(monkeypatch, hall.trainer.id)

    async def refuse(_channel: Any, **_kwargs: Any) -> Any:
        raise discord.Forbidden(MagicMock(status=403), "Missing Permissions")

    monkeypatch.setattr(discord.TextChannel, "create_thread", refuse)

    allowed, interaction = await hall.forged_click(hall.respo, _alert_id(hall, "r"))

    assert allowed
    assert _refused(interaction) == ("Le formateur n'a pas pu être relancé : ses MP sont fermés et aucun fil n'a pu être créé.")
    assert _stored_answer(hall) == (None, None, None)
    assert not any(b.get("disabled") for b in _buttons(_alert(hall, hall.respo)).values())


@pytest.mark.parametrize("case", ["closed_dms", "no_respo"])
async def test_an_alert_that_reaches_nobody_is_recorded_and_not_sent_again(
    hall: Hall, monkeypatch: pytest.MonkeyPatch, case: str, caplog: pytest.LogCaptureFixture
) -> None:
    """With no Respo Formation reached, the alert is logged and recorded anyway, so a later tick sends nothing."""
    role = hall.roles[RoleNames.TRAININGS_MANAGER]
    closed = {hall.respo.id} if case == "closed_dms" else set()

    async def can_dm(member: Any) -> bool:
        return member.id not in closed

    monkeypatch.setattr("fablabot.helpers.utils._can_dm_user", can_dm)
    if case == "no_respo":
        hall.env.backend.edit_member(GUILD_ID, hall.respo.id, {"role_ids": []})
        await asyncio.sleep(0)
    with caplog.at_level(logging.WARNING):
        await _overdue(hall)

    appel = attendance.get(hall.appel_id)
    assert appel is not None
    assert appel.alert_sent
    assert attendance.alert_messages(hall.appel_id) == []
    assert f"Alert of appel {hall.appel_id} in guild {GUILD_ID} reached no Respo Formation." in caplog.text

    # The Respo can be reached from now on: the check is not read again, so nothing goes out late.
    closed.clear()
    hall.env.backend.edit_member(GUILD_ID, hall.respo.id, {"role_ids": [role.id]})
    await asyncio.sleep(0)
    await hall.tick()

    assert _dms(hall.respo) == []


async def test_cancel_closes_the_appel_and_tells_no_registrant(hall: Hall) -> None:
    """Cancelling closes the appel, shows it counts as not held, and sends nothing to the registered members."""
    await _overdue(hall)

    await hall.click(hall.respo, _alert(hall, hall.respo), _alert_id(hall, "c"))

    appel = attendance.get(hall.appel_id)
    assert appel is not None
    assert appel.cancelled
    assert not appel.is_open(datetime.now(PARIS_TZ))
    assert _alert(hall, hall.respo).content.endswith(
        _answer(hall, f"❌ **Formation annulée par <@{hall.respo.id}>** à {{at}} : elle compte comme non tenue.")
    )
    assert all(_dms(member) == [] for member in hall.registered)
    allowed, interaction = await hall.forged_click(hall.trainer, hall.toggle_id(hall.registered[0]))
    assert not allowed
    assert _refused(interaction) == "La formation **Découpe laser** a été annulée : l'appel est fermé. Rien n'a été modifié."


async def test_ignore_does_nothing_else(hall: Hall) -> None:
    """Ignoring is recorded and shown, and leaves the appel open and the trainer untouched."""
    await _overdue(hall)

    await hall.click(hall.respo, _alert(hall, hall.respo), _alert_id(hall, "i"))

    assert _alert(hall, hall.respo).content.endswith(_answer(hall, f"{IGNORED} **Ignoré par <@{hall.respo.id}>** à {{at}}."))
    assert _stored_answer(hall)[:2] == (hall.respo.id, attendance.AlertAction.IGNORE)
    assert len(_dms(hall.trainer)) == 1
    assert all(_dms(member) == [] for member in hall.registered)
    allowed, _ = await hall.forged_click(hall.trainer, hall.toggle_id(hall.registered[0]))
    assert allowed


async def test_the_first_click_wins_and_every_inbox_shows_it(hall: Hall) -> None:
    """Every Respo Formation's copy shows the first answer, with its buttons off."""
    other_respo = hall.member("respo2", RoleNames.TRAININGS_MANAGER)
    await _overdue(hall)

    await hall.click(other_respo, _alert(hall, other_respo), _alert_id(hall, "i"))

    for respo in (hall.respo, other_respo):
        alert = _alert(hall, respo)
        assert alert.content.endswith(_answer(hall, f"{IGNORED} **Ignoré par <@{other_respo.id}>** à {{at}}."))
        assert all(b.get("disabled") for b in _buttons(alert).values())


async def test_a_second_click_is_refused_with_the_first_answer(hall: Hall) -> None:
    """A later click is told who answered first and how, and changes nothing."""
    other_respo = hall.member("respo2", RoleNames.TRAININGS_MANAGER)
    await _overdue(hall)
    await hall.click(other_respo, _alert(hall, other_respo), _alert_id(hall, "c"))
    before = _stored_answer(hall)

    allowed, interaction = await hall.forged_click(hall.respo, _alert_id(hall, "i"))

    assert allowed
    assert _refused(interaction) == "Déjà traité : " + _answer(
        hall, f"❌ **Formation annulée par <@{other_respo.id}>** à {{at}} : elle compte comme non tenue."
    )
    assert _stored_answer(hall) == before


async def test_a_validation_after_the_alert_closes_it_in_every_inbox(hall: Hall) -> None:
    """When the trainer saves the appel after the alert, every copy shows it done and the buttons go off."""
    other_respo = hall.member("respo2", RoleNames.TRAININGS_MANAGER)
    await _overdue(hall)

    await hall.validate()

    assert _stored_answer(hall)[:2] == (hall.trainer.id, attendance.AlertAction.DONE)
    for respo in (hall.respo, other_respo):
        alert = _alert(hall, respo)
        assert alert.content.endswith(_answer(hall, f"✅ **Appel fait par <@{hall.trainer.id}>** à {{at}}."))
        assert all(b.get("disabled") for b in _buttons(alert).values())


async def test_a_validation_that_beats_the_alert_record_still_closes_it(hall: Hall, monkeypatch: pytest.MonkeyPatch) -> None:
    """A validation landing while the alerts are being sent closes them as soon as they are recorded."""
    hall.register(1)
    hall.publish(start=datetime.now(PARIS_TZ) - OVERDUE, duration="1h")
    real_record = attendance.record_alert
    validated: list[bool] = []

    def record(appel_id: int, messages: Any) -> None:
        # The trainer saves between the sends and the record.
        attendance.validate(appel_id, hall.trainer.id, datetime.now(PARIS_TZ))
        validated.append(True)
        real_record(appel_id, messages)

    monkeypatch.setattr(attendance, "record_alert", record)

    await hall.tick()

    assert validated
    assert _stored_answer(hall)[:2] == (hall.trainer.id, attendance.AlertAction.DONE)
    assert _alert(hall, hall.respo).content.endswith(_answer(hall, f"✅ **Appel fait par <@{hall.trainer.id}>** à {{at}}."))


async def test_an_alert_click_from_a_member_who_lost_respo_formation_is_refused(hall: Hall) -> None:
    """A member who held Respo Formation when the alert was sent but no longer does is refused, and nothing is stored."""
    await _overdue(hall)
    alert = _alert(hall, hall.respo)
    hall.env.backend.edit_member(GUILD_ID, hall.respo.id, {"role_ids": []})
    await asyncio.sleep(0)

    allowed, interaction = await hall.forged_click(hall.respo, _alert_id(hall, "c"), shown=alert)

    assert not allowed
    assert _refused(interaction) == NO_RIGHT
    assert _stored_answer(hall) == (None, None, None)


async def test_the_alert_survives_a_restart(hall: Hall, started_bot: Any) -> None:
    """A second bot on the same database answers a click on the first bot's alert, from the database alone."""
    await _overdue(hall)
    custom_id = _alert_id(hall, "i")
    role = MagicMock(spec=discord.Role)
    role.name = RoleNames.TRAININGS_MANAGER
    respo = make_member(hall.respo.id)
    respo.roles = [role]
    started_bot.get_guild = MagicMock(return_value=make_guild(GUILD_ID, [role], [respo]))
    interaction = make_interaction(user_id=hall.respo.id)
    interaction.client = started_bot
    interaction.guild = None
    interaction.guild_id = None
    interaction.message = MagicMock()
    store = started_bot._connection._view_store
    pattern, item_class = next((p, c) for p, c in store._dynamic_items.items() if p.fullmatch(custom_id))
    item = await item_class.from_custom_id(interaction, MagicMock(), pattern.fullmatch(custom_id))
    assert await item.interaction_check(interaction)

    await item.callback(interaction)

    assert _stored_answer(hall)[:2] == (hall.respo.id, attendance.AlertAction.IGNORE)
    assert f"{IGNORED} **Ignoré par" in interaction.response.edit_message.await_args.kwargs["content"]


async def test_an_appel_without_trainer_alerts_about_le_formateur(hall: Hall) -> None:
    """With no trainer named, the alert says "Le formateur" and mentions nobody."""
    await _overdue(hall, trainer_mention="à définir")

    (message,) = _dms(hall.respo)
    assert message.content.startswith("Salut !\nLe formateur n'a pas fait l'appel de **Découpe laser** (")
    assert "<@" not in message.content


async def test_an_unreadable_duration_alerts_three_hours_after_the_start(hall: Hall) -> None:
    """A duration that cannot be read ends the slot three hours after the start, and the alert shows the start alone."""
    hall.register(1)
    now = datetime.now(PARIS_TZ)
    early = hall.training(start=now - timedelta(hours=2, minutes=50), name="Trop tôt", duration="bientôt")
    late = hall.training(start=now - timedelta(hours=3, minutes=10), name="Assez tard", duration="bientôt")
    hall.store([early, late])

    await hall.tick()

    (message,) = _dms(hall.respo)
    assert "**Assez tard**" in message.content
    assert " à " not in message.content.split("**Assez tard** (")[1].split(")")[0]


async def test_a_thread_message_pings_only_the_trainer(hall: Hall, monkeypatch: pytest.MonkeyPatch) -> None:
    """The thread's message allows the mention of the trainer and no one else, no role and no everyone."""
    hall.register(1)
    hall.publish()
    _close_dms(monkeypatch)
    sent: list[Any] = []
    real_send = discord.Thread.send

    async def send(thread: Any, *args: Any, **kwargs: Any) -> Any:
        sent.append(kwargs["allowed_mentions"])
        return await real_send(thread, *args, **kwargs)

    monkeypatch.setattr(discord.Thread, "send", send)

    await hall.tick()

    (mentions,) = sent
    assert mentions.to_dict() == {"parse": [], "users": [hall.trainer.id], "replied_user": True}


async def test_a_deleted_thread_is_forgotten_and_a_new_one_is_made(hall: Hall, monkeypatch: pytest.MonkeyPatch) -> None:
    """A stored thread that no longer exists is cleared, and the next delivery makes a new one."""
    _close_dms_of(monkeypatch, hall.trainer.id)
    await _overdue(hall)
    (old,) = _threads(hall.general)
    hall.env.backend.delete_channel(old.id)
    await asyncio.sleep(0)

    await hall.click(hall.respo, _alert(hall, hall.respo), _alert_id(hall, "r"))

    (new,) = _threads(hall.general)
    assert new.id != old.id
    assert attendance.get(hall.appel_id).thread_id == new.id  # pyright: ignore[reportOptionalMemberAccess]
    assert len(new.history()) == 1
