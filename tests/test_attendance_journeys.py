"""The trainer takes the attendance from the start message, against a simulated Discord and the real reminder tick."""

from __future__ import annotations

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


async def test_the_lists_name_who_validated_not_who_validated_meanwhile(hall: Hall, monkeypatch: pytest.MonkeyPatch) -> None:
    """Someone else validating while the message is being edited does not take the credit for the first save."""
    hall.register(1)
    hall.publish()
    await hall.tick()
    real_show = ui_attendance._show

    async def show(interaction: Any, appel_id: int) -> None:
        stored = attendance.get(appel_id)
        if stored is not None and stored.validated_at is not None:
            attendance.validate(appel_id, hall.plain.id, datetime.now(PARIS_TZ))
        await real_show(interaction, appel_id)

    monkeypatch.setattr(ui_attendance, "_show", show)

    await hall.validate()

    (message,) = _lists(hall, hall.respo)
    assert f"a été fait par <@{hall.trainer.id}>." in message.content
    (line,) = [m.content for m in hall.logs.history()]  # pyright: ignore[reportAttributeAccessIssue]
    assert f"validé par <@{hall.trainer.id}>" in line


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
