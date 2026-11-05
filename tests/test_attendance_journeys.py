"""The trainer takes the attendance from the start message, against a simulated Discord and the real reminder tick."""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

from conftest import LISTED_GUILD, OTHER_LISTED_GUILD, make_guild, make_interaction, make_member
from discord import Permissions
import pytest

from fablabot import db
from fablabot.helpers.constants import PARIS_TZ, RoleNames
from fablabot.helpers.guild_access import GuildAccessPolicy
from fablabot.models.formation import FmMessageDraft, Formation, PublishedMessage
from fablabot.services import attendance, formations
from fablabot.ui.attendance import DYNAMIC_ITEMS

if TYPE_CHECKING:
    import discord
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

    def publish(self, *, start: datetime | None = None, trainer_mention: str | None = None, duration: str = "2h") -> None:
        """Store the published announcement of one training that starts now unless told otherwise."""
        start = start or datetime.now(PARIS_TZ)
        users = [{"user_id": m.id, "username": m.name, "ts_iso": start.isoformat()} for m in self.registered]
        training = Formation(
            emoji="🔧",
            name="Découpe laser",
            trainer_mention=trainer_mention or f"<@{self.trainer.id}>",
            start_iso=start.isoformat(),
            duration=duration,
            seats=200,
            excusable=False,
            registered_users=users,
        )
        draft = FmMessageDraft(header="h", role_id=1, intro="i", fms=[training], end="e")
        published = PublishedMessage(message_id=MESSAGE_ID, channel_id=self.general.id, message=draft)
        formations.save_published(GUILD_ID, published)

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
    def appel_id(self) -> int:
        """The ID of the only check stored."""
        return db.connection().execute("SELECT id FROM appel").fetchone()["id"]

    def toggle_id(self, member: Any) -> str:
        """Write the custom ID of a member's button."""
        return f"appel:{self.appel_id}:m:{member.id}"

    async def forged_click(self, who: Any, custom_id: str) -> tuple[bool, MagicMock]:
        """Send a click the way Discord does for a message the clicker cannot reach, and tell whether the bot let it run.

        Returns:
            tuple[bool, MagicMock]: Whether the item allowed the click, and the interaction the bot answered.
        """
        bot: Any = self.env.bot
        interaction = make_interaction(user_id=who.id)
        interaction.client = bot
        interaction.guild = None
        interaction.guild_id = None
        interaction.message = MagicMock()
        item_class = next(cls for cls in DYNAMIC_ITEMS if cls.__discord_ui_compiled_template__.fullmatch(custom_id))
        match = item_class.__discord_ui_compiled_template__.fullmatch(custom_id)
        item = await item_class.from_custom_id(interaction, MagicMock(), match)  # pyright: ignore[reportArgumentType]
        allowed = await item.interaction_check(interaction)
        if allowed:
            await item.callback(interaction)
        return allowed, interaction


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
