"""Member journeys against a simulated Discord: real commands, components and permission checks, no network."""

from __future__ import annotations

from datetime import datetime, timedelta
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from discord import Permissions
import pytest

from fablabot import db, guild_config
from fablabot.cogs import user_management
from fablabot.helpers.constants import PARIS_TZ, RoleNames
from fablabot.helpers.utils import RIGHT_LOST_MESSAGE
from fablabot.import_state import main as run_import
from fablabot.models import MessageDraft, MsgReactionEvent, TrackedMessage
from fablabot.services import formations, messages, op_expiries

if TYPE_CHECKING:
    import sqlite3

    import simcord

GUILD_ID = 111111111111111111
BOT_ROLE_POSITION = 100  # above every staff role, so the bot may give and take them


class Server:
    """A server with the channels and roles the bot looks up by name."""

    def __init__(self, env: simcord.Env) -> None:
        """Create the channels, the roles and three members with different rights."""
        self.env = env
        self.guild = env.create_guild(id=GUILD_ID)
        self.commands = self.guild.create_text_channel("commandes_bot")
        self.general = self.guild.create_text_channel("general")
        self.category = self.guild.create_category("Vocaux")
        self.admin = self.member("admin", "Administrateur")
        self.plain = self.member("plain")
        self.bureau = self.member("bureau", "Bureau")
        self.discord_admin = self.guild.add_member(
            env.create_user("discord_admin"),
            roles=[self.guild.create_role("Owner", permissions=Permissions(administrator=True))],
        )
        # A bot cannot give a role at or above its own highest one: lift its managed role over the staff roles.
        bot_roles = [
            {"id": r.id, "position": BOT_ROLE_POSITION} for r in env.backend.get_guild(GUILD_ID).roles.values() if r.managed
        ]
        env.backend.reorder_roles(GUILD_ID, bot_roles)

    async def show(self, name: str, *roles: str) -> None:
        """Allow roles on a command, as a server administrator does in Integrations."""
        bot: Any = self.env.bot
        command = next(c for c in await bot.tree.fetch_commands() if c.name == name)
        self.guild.set_command_permissions(command, {self.role(r): True for r in roles})

    def role(self, name: str, position: int | None = None) -> Any:
        """Find a role, creating it the first time, at ``position`` when given (below the bot's role)."""
        options = {} if position is None else {"position": position}
        return self.guild.roles.get(name) or self.guild.create_role(name, permissions=Permissions.none(), **options)

    def member(self, name: str, *roles: str) -> Any:
        """Add a member holding these roles."""
        return self.guild.add_member(self.env.create_user(name), roles=[self.role(r) for r in roles])


@pytest.fixture
async def server(simcord_env) -> Server:
    """Open a server set up as the operator guide says, with `/fm` and `/vocal` shown to their roles."""
    built = Server(simcord_env)
    await built.show("fm", "Administrateur")
    await built.show("vocal", "Bureau")
    return built


def lines(result: Any) -> str:
    """Join the text of the first embed of a reply, so a test can search it."""
    embed = result.response.embeds[0]
    return " ".join([embed.title or "", embed.description or "", *[f"{f.name} {f.value}" for f in embed.fields]])


async def test_help_private_by_default(server: Server) -> None:
    """`/help` answers privately and lists what the caller may run."""
    result = await server.admin.slash(server.commands, "help")
    assert result.ephemeral
    assert "/fm" in lines(result)


async def test_help_show_posts_publicly(server: Server) -> None:
    """The `show` option posts the list for everyone."""
    result = await server.admin.slash(server.commands, "help", show=True)
    assert not result.ephemeral


async def test_private_help_works_outside_the_commands_channel(server: Server) -> None:
    """Outside the commands channel, `/help` still gives a member the list, privately."""
    result = await server.plain.slash(server.general, "help")
    assert result.ephemeral
    assert "/suggest" in lines(result)


async def test_private_help_works_in_a_voice_channel(server: Server) -> None:
    """The text chat of a voice channel counts as any channel."""
    voice = server.guild.create_voice_channel("salon", category=server.category)
    result = await server.plain.slash(voice, "help")
    assert result.ephemeral
    assert "/suggest" in lines(result)


async def test_public_help_refusal_gives_the_private_way_out(server: Server) -> None:
    """A member who cannot open the commands channel is told to drop ``show``."""
    result = await server.plain.slash(server.general, "help", show=True)
    assert result.ephemeral
    assert "`show`" in result.response.content


async def test_public_help_outside_commands_channel_points_to_it(server: Server) -> None:
    """Outside the commands channel, `/help show:True` refuses and names the channel."""
    result = await server.admin.slash(server.general, "help", show=True)
    assert result.ephemeral
    assert "commandes_bot" in result.response.content or str(server.commands.id) in result.response.content


async def test_plain_member_sees_only_public_commands(server: Server) -> None:
    """A member without a staff role is offered `/help` and `/suggest` alone."""
    assert set(server.plain.available_commands(server.commands)) == {"help", "suggest"}


async def test_help_for_plain_member_lists_only_public_commands(server: Server) -> None:
    """`/help` lists no staff command to a member without a staff role."""
    text = lines(await server.plain.slash(server.commands, "help"))
    assert "/suggest" in text
    assert "/fm" not in text


async def test_suggest_keeps_recipient_after_toggling_anonymity(server: Server) -> None:
    """The chosen recipient stays selected after the anonymity button."""
    shown = await server.plain.slash(server.general, "suggest")
    message = shown.response.message
    await server.plain.select(message, [_first_recipient(message)])
    toggled = await server.plain.click(message, label="Masquer mon nom")
    options = _select_options(toggled.response.message if toggled.response else message)
    assert [o["value"] for o in options if o.get("default")] == [_first_recipient(message)]


def _select_options(message: Any) -> list[dict[str, Any]]:
    """Read the options of the first select menu of a message."""
    for row in message.components:
        for item in row.children:
            if hasattr(item, "options"):
                return [{"value": o.value, "default": o.default} for o in item.options]
    pytest.fail("no select menu")


def _first_recipient(message: Any) -> str:
    """Return the first recipient offered by the suggestion menu."""
    return _select_options(message)[0]["value"]


def fill(shown: Any, by_label: dict[str, str]) -> dict[str, str]:
    """Map a modal's visible labels to the custom IDs discord.py generated."""
    inputs = [item for row in shown.modal["components"] for item in row["components"]]
    return {item["custom_id"]: by_label[item["label"]] for item in inputs if item["label"] in by_label}


def everything(channel: Any) -> str:
    """Join what a channel holds, every embed field included, so a test can search it."""
    parts = []
    for message in channel.history():
        parts.append(message.content)
        parts += [json.dumps(embed.to_dict(), default=str, ensure_ascii=False) for embed in message.embeds]
    return " ".join(parts)


async def test_discord_admin_sees_staff_groups(server: Server) -> None:
    """The Administrator permission opens the staff groups in the menu."""
    assert "fm add" in server.discord_admin.available_commands(server.commands)


async def test_suggest_anonymous_hides_the_author(server: Server) -> None:
    """An anonymous suggestion reaches the channel and the manager without its author."""
    role = server.role("Respo Communication")
    respo_user = server.env.create_user("respo")
    server.guild.add_member(respo_user, roles=[role])
    inbox = server.guild.create_text_channel("pole-communication")
    shown = await server.plain.slash(server.general, "suggest")
    message = shown.response.message
    await server.plain.select(message, ["communication"])
    await server.plain.click(message, label="Masquer mon nom")
    modal = await server.plain.click(message, label="Rédiger la suggestion")
    await server.plain.submit_modal(modal, fill(modal, {"Votre suggestion": "Plus de PLA"}))

    posted = everything(inbox)
    assert "Plus de PLA" in posted
    assert "Anonymous" in posted
    assert server.plain.name not in posted
    assert str(server.plain.id) not in posted
    dm = everything(respo_user.dm_channel)
    assert "Plus de PLA" in dm
    assert str(server.plain.id) not in dm


async def _draft(server: Server, when: datetime) -> None:
    """Create a draft holding one training, as a staff member does."""
    shown = await server.admin.slash(server.commands, "fm start", role=server.role("Membre ✓"))
    await server.admin.submit_modal(shown, fill(shown, {"Intro": "Bonjour", "Conclusion": "A bientot"}))
    added = await server.admin.slash(
        server.commands,
        "fm add",
        emoji="🔧",
        trainer=server.plain,
        date=when.strftime("%d/%m/%Y"),
        hour=when.strftime("%H:%M"),
        duration="2h",
        seats=4,
    )
    await server.admin.submit_modal(added, fill(added, {"Nom de la formation": "Arduino"}))


def later() -> datetime:
    """Return a start date that stays in the future, on the half hour."""
    return (datetime.now(PARIS_TZ) + timedelta(days=30)).replace(hour=18, minute=30, second=0, microsecond=0)


async def test_fm_add_refuses_a_past_date(server: Server) -> None:
    """A training that already started is refused before the form opens."""
    shown = await server.admin.slash(server.commands, "fm start", role=server.role("Membre ✓"))
    await server.admin.submit_modal(shown, fill(shown, {"Intro": "Bonjour", "Conclusion": "A bientot"}))
    result = await server.admin.slash(
        server.commands, "fm add", emoji="🔧", trainer=server.plain, date="01/01/2020", hour="10:00", duration="2h", seats=4
    )
    assert result.modal is None
    assert result.ephemeral


async def test_fm_preview_uses_a_discord_timestamp(server: Server) -> None:
    """The preview writes the start as a Discord timestamp."""
    when = later()
    await _draft(server, when)
    await server.admin.slash(server.commands, "fm preview")
    assert f"<t:{int(when.timestamp())}:F>" in everything(server.commands)


async def test_fm_remove_picks_by_name(server: Server) -> None:
    """`/fm remove` offers the training by name and empties the draft."""
    await _draft(server, later())
    shown = await server.admin.slash(server.commands, "fm remove")
    await server.admin.click(shown.response.message, label="🔧 Arduino")
    again = await server.admin.slash(server.commands, "fm remove")
    assert again.ephemeral
    assert not again.response.components


async def test_fm_publish_then_a_reaction_counts_as_a_sign_up(server: Server) -> None:
    """A reaction on the published message lands in `/fm export`."""
    await _draft(server, later())
    await server.admin.slash(server.commands, "fm publish", channel=server.general)
    published = server.general.last_message
    assert published is not None
    assert "Arduino" in published.content
    assert [str(reaction.emoji) for reaction in published.reactions] == ["🔧"]

    await server.plain.react(published, "🔧")
    await server.env.advance_time(120)
    export = await server.admin.slash(server.commands, "fm export")
    log = await next(a for a in export.response.attachments if a.filename.endswith(".csv")).read()
    assert str(server.plain.id).encode() in log


async def test_fm_publish_the_database_refuses_takes_the_post_down_and_says_so(
    server: Server, monkeypatch: pytest.MonkeyPatch, storage_error: sqlite3.Error
) -> None:
    """A storage failure after the post leaves no post, no bot reaction, and a private reply; the draft publishes later."""

    def refused(*_args: Any) -> None:
        raise storage_error

    await _draft(server, later())
    with monkeypatch.context() as patched:
        patched.setattr(formations, "save_published", refused)
        await server.admin.slash(server.commands, "fm publish", channel=server.general)
    assert "Publication annulée" in everything(server.commands)
    assert server.general.last_message is None

    await server.admin.slash(server.commands, "fm publish", channel=server.general)
    assert "Message publié dans" in everything(server.commands)
    published = server.general.last_message
    assert published is not None
    await server.plain.react(published, "🔧")
    await server.env.advance_time(120)
    export = await server.admin.slash(server.commands, "fm export")
    log = await next(a for a in export.response.attachments if a.filename.endswith(".csv")).read()
    assert str(server.plain.id).encode() in log


async def test_bureau_creates_a_temporary_voice_channel(server: Server) -> None:
    """Bureau creates a temporary voice channel."""
    result = await server.bureau.slash(
        server.commands, "vocal create", name="reunion", category=server.category, is_temporary=True
    )
    assert not result.ephemeral
    assert any(name.startswith("reunion") for name in server.guild.channels)


async def test_bureau_cannot_create_a_permanent_voice_channel(server: Server) -> None:
    """Bureau is refused a permanent voice channel."""
    result = await server.bureau.slash(
        server.commands, "vocal create", name="reunion", category=server.category, is_temporary=False
    )
    assert result.ephemeral
    assert not any(name.startswith("reunion") for name in server.guild.channels)


async def test_bureau_cannot_rename_a_voice_channel(server: Server) -> None:
    """Bureau is refused `/vocal rename`."""
    voice = server.guild.create_voice_channel("salon", category=server.category)
    result = await server.bureau.slash(server.commands, "vocal rename", channel=voice, new_name="autre")
    assert result.ephemeral
    assert "salon" in server.guild.channels


STAFF_RANKS = {
    RoleNames.ADMIN_TEMP: 50,
    RoleNames.PRESIDENT: 40,
    RoleNames.SECRETARY: 30,
    RoleNames.DIGITAL_MANAGER: 20,
    RoleNames.DIGITAL_POLE: 10,
    RoleNames.CODIR: 5,
}


@pytest.fixture
async def ranked(server: Server) -> Server:
    """The server with the staff roles in their order and the `/user` group shown to the staff."""
    for name, position in STAFF_RANKS.items():
        server.role(name, position)
    await server.show("user", RoleNames.PRESIDENT, RoleNames.SECRETARY, RoleNames.DIGITAL_MANAGER)
    return server


def role_names(member: Any) -> set[str]:
    """Read the names of the roles a member holds, as the bot sees them."""
    assert member.member is not None
    return {role.name for role in member.member.roles}


async def test_secretary_general_is_refused_president_and_nothing_changes(ranked: Server) -> None:
    """A role above the caller's highest is refused with the way out, and the target keeps their roles."""
    secretary = ranked.member("secretary", RoleNames.SECRETARY)
    target = ranked.member("target")

    result = await secretary.slash(ranked.commands, "user add_role", member=target, role=ranked.role(RoleNames.PRESIDENT))

    assert result.ephemeral
    assert result.response.content == user_management._ROLE_NOT_BELOW_REFUSED
    assert RoleNames.PRESIDENT not in role_names(target)


async def test_respo_gives_the_pole_below_them(ranked: Server) -> None:
    """A Respo gives the matching Pôle, which sits below the Respo role."""
    respo = ranked.member("respo", RoleNames.DIGITAL_MANAGER)
    target = ranked.member("target")

    await respo.slash(ranked.commands, "user add_role", member=target, role=ranked.role(RoleNames.DIGITAL_POLE))

    assert RoleNames.DIGITAL_POLE in role_names(target)


async def test_president_ops_with_the_temporary_role_above_and_deop_revokes(ranked: Server) -> None:
    """Op works though Admin -temp- outranks the president; taking it as a role is refused, deop revokes."""
    president = ranked.member("president", RoleNames.PRESIDENT)
    target = ranked.member("target")

    await president.slash(ranked.commands, "user op", member=target, reason="atelier", time=5)
    assert RoleNames.ADMIN_TEMP in role_names(target)

    refused = await president.slash(ranked.commands, "user remove_role", member=target, role=ranked.role(RoleNames.ADMIN_TEMP))
    assert refused.ephemeral
    assert refused.response.content == user_management._TEMP_ROLE_TAKE_REFUSED
    assert RoleNames.ADMIN_TEMP in role_names(target)

    await president.slash(ranked.commands, "user deop", member=target)
    assert RoleNames.ADMIN_TEMP not in role_names(target)


def _restart() -> None:
    """Close the database and open it again, as a new process would."""
    path = db.connection().execute("PRAGMA database_list").fetchone()["file"]
    db.close_database()
    db.open_database(Path(path))


async def test_a_sign_up_survives_a_restart(server: Server) -> None:
    """A registration and the reaction history are read back after a restart."""
    await _draft(server, later())
    await server.admin.slash(server.commands, "fm publish", channel=server.general)
    published = server.general.last_message
    assert published is not None
    await server.plain.react(published, "🔧")
    await server.env.advance_time(120)

    _restart()

    export = await server.admin.slash(server.commands, "fm export")
    log = await next(a for a in export.response.attachments if a.filename.endswith(".csv")).read()
    assert str(server.plain.id).encode() in log
    assert server.plain.name in export.response.content


async def test_a_draft_survives_a_restart(server: Server) -> None:
    """A draft in progress is still there after a restart."""
    when = later()
    await _draft(server, when)

    _restart()

    await server.admin.slash(server.commands, "fm preview")
    assert f"<t:{int(when.timestamp())}:F>" in everything(server.commands)


async def test_a_tracked_message_survives_a_restart(server: Server) -> None:
    """A message followed with `/msg follow` is still listed after a restart."""
    await server.show("msg", "Administrateur")
    target = await server.admin.send(server.general, "Annonce")

    await server.admin.slash(server.commands, "msg follow", channel=server.general, message=str(target.id))
    _restart()

    listed = await server.admin.slash(server.commands, "msg list")
    assert str(target.id) in listed.response.content


async def test_op_with_no_member_ops_the_caller(ranked: Server) -> None:
    """A president who names nobody gets the temporary role themselves."""
    president = ranked.member("president", RoleNames.PRESIDENT)

    await president.slash(ranked.commands, "user op", reason="atelier")

    assert RoleNames.ADMIN_TEMP in role_names(president)


async def test_a_temporary_role_keeps_its_expiry_after_a_restart(ranked: Server) -> None:
    """An op recorded before a restart is still recorded after it."""
    president = ranked.member("president", RoleNames.PRESIDENT)
    target = ranked.member("target")
    await president.slash(ranked.commands, "user op", member=target, reason="atelier", time=5)

    _restart()

    assert op_expiries.get(GUILD_ID, target.id) is not None


async def test_a_commands_channel_setting_survives_a_restart(server: Server) -> None:
    """A server's setting stays across a restart."""
    guild_config.set_commands_channel_id(GUILD_ID, server.commands.id)

    _restart()

    assert guild_config.get_commands_channel_id(GUILD_ID) == server.commands.id


async def test_a_full_journey_writes_no_json_state(server: Server) -> None:
    """Drafting, publishing and a sign-up leave only the command sync record as a JSON file."""
    await _draft(server, later())
    await server.admin.slash(server.commands, "fm publish", channel=server.general)
    published = server.general.last_message
    assert published is not None
    await server.plain.react(published, "🔧")
    await server.env.advance_time(120)

    assert [path.name for path in Path("data").glob("*.json")] == ["command_sync_state.json"]


async def _sign_up(server: Server, *members: Any) -> Any:
    """Publish a training with one seat and let members react, as they did before the release that moves the state."""
    await _draft(server, later())
    await server.admin.slash(server.commands, "fm publish", channel=server.general)
    published = server.general.last_message
    assert published is not None
    for member in members:
        await member.react(published, "🔧")
    return published


def _state_of_the_old_release(published: Any, journal: list[dict[str, Any]], *seated: Any) -> None:
    """Leave only JSON files behind, in the shape the old release wrote them for this post: one seat, a waitlist.

    The members signed up in the order given, all of them before the journal's retention.
    """
    live = formations.current_published(GUILD_ID)
    assert live is not None
    training = live.message.fms[0].to_dict()
    first_stamp = datetime.now(PARIS_TZ) - timedelta(days=3 * 365 + 10)
    entries = [
        {"user_id": m.id, "username": m.name, "ts_iso": (first_stamp + timedelta(minutes=i)).isoformat(timespec="seconds")}
        for i, m in enumerate(seated)
    ]
    training |= {"seats": 1, "registered_users": entries[:1], "waitlisted_users": entries[1:]}
    message = {**live.message.to_dict(), "fms": [training]}
    state = {"published": {"message_id": published.id, "channel_id": published.channel.id, "message": message}}
    db.close_database()
    Path("data").mkdir(exist_ok=True)
    Path("data/formations_state.json").write_text(
        json.dumps({str(GUILD_ID): {**state, "reactions_log": journal}}, ensure_ascii=False), encoding="utf-8"
    )


def _add(published: Any, member: Any, hours_ago: int) -> dict[str, Any]:
    """Build one journal line of the old release."""
    moment = datetime.now(PARIS_TZ) - timedelta(hours=hours_ago)
    return {
        "message_id": published.id,
        "user_id": member.id,
        "user_name": member.name,
        "emoji": "🔧",
        "action": "add",
        "ts_iso": moment.isoformat(timespec="seconds"),
    }


async def test_a_published_training_keeps_its_seats_after_the_import(server: Server) -> None:
    """Moved by the import, the bot rebuilds the same list: same seat, same waitlist, a new member behind, no DM repeated."""
    users = [server.env.create_user(name) for name in ("first", "second", "third")]
    first, second, third = (server.guild.add_member(user) for user in users)
    published = await _sign_up(server, first, second)
    await server.env.advance_time(120)
    _state_of_the_old_release(published, [_add(published, first, 2), _add(published, second, 1)], first, second)

    run_import([])
    db.open_database()
    received = [everything(user.dm_channel) for user in users[:2]]
    await third.react(published, "🔧")
    await server.env.advance_time(120)

    live = formations.current_published(GUILD_ID)
    assert live is not None
    training = live.message.fms[0]
    assert [u["user_id"] for u in training.registered_users] == [first.id]
    assert [u["user_id"] for u in training.waitlisted_users] == [second.id, third.id]
    assert [everything(user.dm_channel) for user in users[:2]] == received
    assert "Arduino" in everything(users[2].dm_channel)


async def test_a_member_who_signed_up_before_the_journal_retention_keeps_the_seat_after_the_import(server: Server) -> None:
    """The journal keeps 3 years: a seat taken earlier, with its reaction still on the post, is not given away."""
    users = [server.env.create_user(name) for name in ("first", "second", "third")]
    first, second, third = (server.guild.add_member(user) for user in users)
    published = await _sign_up(server, first, second)
    await server.env.advance_time(120)
    _state_of_the_old_release(published, [_add(published, second, 1)], first, second)

    run_import([])
    db.open_database()
    received = [everything(user.dm_channel) for user in users[:2]]
    await third.react(published, "🔧")
    await server.env.advance_time(120)

    live = formations.current_published(GUILD_ID)
    assert live is not None
    training = live.message.fms[0]
    assert [u["user_id"] for u in training.registered_users] == [first.id]
    assert [u["user_id"] for u in training.waitlisted_users] == [second.id, third.id]
    assert [everything(user.dm_channel) for user in users[:2]] == received


async def test_a_training_imported_without_its_journal_keeps_the_seats_the_post_still_shows(server: Server) -> None:
    """With no journal at all, a stored seat holds while its reaction stands; a reaction nobody stored does not count."""
    first = server.member("first")
    published = await _sign_up(server, first)
    await server.env.advance_time(120)
    _state_of_the_old_release(published, [], first)

    run_import([])
    db.open_database()
    await server.plain.react(published, "🔧")
    await server.env.advance_time(120)

    live = formations.current_published(GUILD_ID)
    assert live is not None
    assert [u["user_id"] for u in live.message.fms[0].registered_users] == [first.id]
    assert [u["user_id"] for u in live.message.fms[0].waitlisted_users] == [server.plain.id]


def lose(server: Server, member: Any, role: str) -> None:
    """Take a role from a member while the views they opened stay open."""
    server.env.backend.remove_member_role(GUILD_ID, member.id, server.role(role).id)


def button_labels(message: Any) -> list[str]:
    """List the labels of the buttons of a message."""
    return [item["label"] for row in message.components for item in row["components"] if "label" in item]


async def test_fm_start_modal_refuses_a_member_who_lost_the_role(server: Server) -> None:
    """The role goes while the form is open: the submit is refused in private and no draft is stored."""
    shown = await server.admin.slash(server.commands, "fm start", role=server.role("Membre ✓"))
    lose(server, server.admin, "Administrateur")

    refused = await server.admin.submit_modal(shown, fill(shown, {"Intro": "Bonjour", "Conclusion": "A bientot"}))

    assert refused.ephemeral
    assert refused.response.content == RIGHT_LOST_MESSAGE
    assert formations.get_draft(GUILD_ID) is None


async def test_fm_start_modal_still_stores_for_a_member_who_kept_the_role(server: Server) -> None:
    """The same submit, with the role kept, stores the draft."""
    shown = await server.admin.slash(server.commands, "fm start", role=server.role("Membre ✓"))

    await server.admin.submit_modal(shown, fill(shown, {"Intro": "Bonjour", "Conclusion": "A bientot"}))

    stored = formations.get_draft(GUILD_ID)
    assert stored is not None
    assert stored.intro == "Bonjour"


async def test_fm_remove_view_refuses_a_member_who_lost_the_role(server: Server) -> None:
    """The role goes while the selector is open: the click is refused and the training stays in the draft."""
    await _draft(server, later())
    shown = await server.admin.slash(server.commands, "fm remove")
    lose(server, server.admin, "Administrateur")

    refused = await server.admin.click(shown.response.message, label="🔧 Arduino")

    assert refused.response.content == RIGHT_LOST_MESSAGE
    draft = formations.get_draft(GUILD_ID)
    assert draft is not None
    assert [fm.name for fm in draft.fms] == ["Arduino"]


async def test_fm_edit_validate_refuses_a_member_who_lost_the_role(server: Server) -> None:
    """The role goes between the edit and the Validate click: the draft keeps the old duration."""
    await _draft(server, later())
    picker = await server.admin.slash(server.commands, "fm edit")
    editor = await server.admin.click(picker.response.message, label="🔧 Arduino")
    modal = await server.admin.click(editor.response.message, label="Durée & Places")
    await server.admin.submit_modal(modal, fill(modal, {"Durée - Places": "5h - 9"}))
    lose(server, server.admin, "Administrateur")

    refused = await server.admin.click(editor.response.message, label="Valider les modifications")

    assert refused.response.content == RIGHT_LOST_MESSAGE
    draft = formations.get_draft(GUILD_ID)
    assert draft is not None
    assert [(fm.duration, fm.seats) for fm in draft.fms] == [("2h", 4)]


async def test_fm_edit_modal_refuses_a_member_who_lost_the_role(server: Server) -> None:
    """The role goes while an edit modal is open: the submit is refused and the editor is not refreshed."""
    await _draft(server, later())
    picker = await server.admin.slash(server.commands, "fm edit")
    editor = await server.admin.click(picker.response.message, label="🔧 Arduino")
    modal = await server.admin.click(editor.response.message, label="Durée & Places")
    lose(server, server.admin, "Administrateur")

    refused = await server.admin.submit_modal(modal, fill(modal, {"Durée - Places": "5h - 9"}))

    assert refused.response.content == RIGHT_LOST_MESSAGE


async def test_fm_edit_validate_still_stores_for_a_member_who_kept_the_role(server: Server) -> None:
    """The same edit, with the role kept, reaches the draft."""
    await _draft(server, later())
    picker = await server.admin.slash(server.commands, "fm edit")
    editor = await server.admin.click(picker.response.message, label="🔧 Arduino")
    modal = await server.admin.click(editor.response.message, label="Durée & Places")
    await server.admin.submit_modal(modal, fill(modal, {"Durée - Places": "5h - 9"}))

    await server.admin.click(editor.response.message, label="Valider les modifications")

    draft = formations.get_draft(GUILD_ID)
    assert draft is not None
    assert [(fm.duration, fm.seats) for fm in draft.fms] == [("5h", 9)]


async def test_msg_start_modal_refuses_a_member_who_lost_the_role(server: Server) -> None:
    """The role goes while the form is open: the submit is refused in private and no draft is stored."""
    await server.show("msg", "Administrateur")
    shown = await server.admin.slash(server.commands, "msg start")
    lose(server, server.admin, "Administrateur")

    refused = await server.admin.submit_modal(shown, fill(shown, {"Contenu du message": "Annonce"}))

    assert refused.response.content == RIGHT_LOST_MESSAGE
    assert messages.get_draft(GUILD_ID) is None


async def test_msg_start_modal_still_stores_for_a_member_who_kept_the_role(server: Server) -> None:
    """The same submit, with the role kept, stores the draft."""
    await server.show("msg", "Administrateur")
    shown = await server.admin.slash(server.commands, "msg start")

    await server.admin.submit_modal(shown, fill(shown, {"Contenu du message": "Annonce"}))

    draft = messages.get_draft(GUILD_ID)
    assert draft is not None
    assert draft.content == "Annonce"


def _track_with_two_actions(target_id: int, channel_id: int) -> None:
    """Follow a message that holds two actions on one emoji, so removing one leaves the bot's reaction in place."""
    actions = [
        MsgReactionEvent(
            emoji="🅰", action_type=kind, message_content="Bonjour", target_id=0, target_name="", message_id=target_id
        )
        for kind in ("user_dm", "channel")
    ]
    messages.save_tracked(
        GUILD_ID,
        TrackedMessage(
            message_id=target_id,
            channel_id=channel_id,
            content="Annonce",
            reactions=actions,
            created_by=1,
            created_at_iso="2026-11-01T10:00:00+01:00",
        ),
    )


async def _open_unlink_view(server: Server) -> Any:
    """Open `/msg unlink_reaction` down to the list of actions of one followed message."""
    await server.show("msg", "Administrateur")
    target = await server.admin.send(server.general, "Annonce")
    _track_with_two_actions(target.id, server.general.id)
    picker = await server.admin.slash(server.commands, "msg unlink_reaction")
    return await server.admin.click(picker.response.message, label=f"Message {target.id}")


async def test_msg_unlink_refuses_a_member_who_lost_the_role(server: Server) -> None:
    """The role goes while the action list is open: the click is refused and the action stays linked."""
    actions = await _open_unlink_view(server)
    lose(server, server.admin, "Administrateur")

    refused = await server.admin.click(actions.response.message, label=button_labels(actions.response)[0])

    assert refused.response.content == RIGHT_LOST_MESSAGE
    assert [len(t.reactions) for t in messages.tracked_messages(GUILD_ID).values()] == [2]


async def test_msg_unlink_still_removes_for_a_member_who_kept_the_role(server: Server) -> None:
    """The same click, with the role kept, removes the action."""
    actions = await _open_unlink_view(server)

    await server.admin.click(actions.response.message, label=button_labels(actions.response)[0])

    assert [len(t.reactions) for t in messages.tracked_messages(GUILD_ID).values()] == [1]


async def test_msg_dm_confirm_refuses_a_member_who_lost_the_role(server: Server) -> None:
    """Bureau loses the role between the form and Confirm: nobody gets the message."""
    await server.show("msg", "Bureau")
    recipient = server.env.create_user("recipient")
    member = server.guild.add_member(recipient)
    shown = await server.bureau.slash(server.commands, "msg dm")
    sent = await server.bureau.submit_modal(shown, fill(shown, {"Message à envoyer": "Bonjour"}))
    picker = sent.followups[-1]
    await server.bureau.select(picker.message, [member])
    lose(server, server.bureau, "Bureau")

    refused = await server.bureau.click(picker.message, label="Confirmer")

    assert refused.response.content == RIGHT_LOST_MESSAGE
    assert recipient.dm_channel.history() == []


async def test_msg_dm_confirm_still_sends_for_a_member_who_kept_the_role(server: Server) -> None:
    """The same click, with the role kept, delivers the message."""
    await server.show("msg", "Bureau")
    recipient = server.env.create_user("recipient")
    member = server.guild.add_member(recipient)
    shown = await server.bureau.slash(server.commands, "msg dm")
    sent = await server.bureau.submit_modal(shown, fill(shown, {"Message à envoyer": "Bonjour"}))
    picker = sent.followups[-1]
    await server.bureau.select(picker.message, [member])

    await server.bureau.click(picker.message, label="Confirmer")

    # SimCord cannot edit the follow-up the bot reports to once the DM went out: reading the errors accepts it.
    assert len(server.env.errors) == 1
    assert len(recipient.dm_channel.history()) == 1


async def test_msg_edit_changes_the_text_and_keeps_the_reactions(server: Server) -> None:
    """The form opens on the draft's text; saving it changes the text only."""
    await server.show("msg", "Administrateur")
    shown = await server.admin.slash(server.commands, "msg start")
    await server.admin.submit_modal(shown, fill(shown, {"Contenu du message": "Bonjour"}))
    reaction = MsgReactionEvent(emoji="🅰", action_type="user_dm", message_content="Merci", target_id=0, target_name="")
    messages.save_draft(GUILD_ID, MessageDraft(content="Bonjour", reactions=[reaction]))

    edit = await server.admin.slash(server.commands, "msg edit")
    [field] = [item for row in edit.modal["components"] for item in row["components"]]
    assert field["value"] == "Bonjour"
    await server.admin.submit_modal(edit, fill(edit, {"Contenu du message": "Bonjour à tous"}))

    draft = messages.get_draft(GUILD_ID)
    assert draft is not None
    assert (draft.content, draft.reactions) == ("Bonjour à tous", [reaction])


async def test_msg_edit_without_a_draft_points_to_start(server: Server) -> None:
    """Nothing to edit: the reply says how to begin."""
    await server.show("msg", "Administrateur")
    refused = await server.admin.slash(server.commands, "msg edit")

    assert refused.modal is None
    assert "/msg start" in refused.response.content


async def test_msg_edit_does_not_erase_a_draft_changed_meanwhile(server: Server) -> None:
    """A second staff member's new draft survives a form opened on the old one."""
    await server.show("msg", "Administrateur")
    messages.save_draft(GUILD_ID, MessageDraft(content="Ancien", reactions=[]))
    edit = await server.admin.slash(server.commands, "msg edit")
    messages.save_draft(GUILD_ID, MessageDraft(content="Nouveau", reactions=[]))

    refused = await server.admin.submit_modal(edit, fill(edit, {"Contenu du message": "Écrasé"}))

    assert "relance `/msg edit`" in refused.response.content
    assert messages.get_draft(GUILD_ID) == MessageDraft(content="Nouveau", reactions=[])
