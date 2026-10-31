"""Member journeys against a simulated Discord: real commands, components and permission checks, no network."""

from __future__ import annotations

from datetime import datetime, timedelta
import importlib
import json
from typing import TYPE_CHECKING, Any

from discord import Permissions
import pytest

from fablabot.helpers.constants import PARIS_TZ
from fablabot.helpers.guild_access import ALLOWED_GUILD_IDS_ENV

if TYPE_CHECKING:
    import simcord

GUILD_ID = 111111111111111111


@pytest.fixture
def simcord_bot(tmp_path, monkeypatch) -> Any:
    """Build the real bot, with a fake token and one listed guild."""
    token = tmp_path / "token"
    token.write_text("fake-token")
    monkeypatch.setenv("DISCORD_TOKEN_FILE", str(token))
    monkeypatch.setenv(ALLOWED_GUILD_IDS_ENV, str(GUILD_ID))
    import fablabot.main

    main = importlib.reload(fablabot.main)
    monkeypatch.setattr(main, "configure_logging", lambda _bot: (None, None))
    return main.Fablabot()


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

    async def show(self, name: str, *roles: str) -> None:
        """Allow roles on a command, as a server administrator does in Integrations."""
        bot: Any = self.env.bot
        command = next(c for c in await bot.tree.fetch_commands() if c.name == name)
        self.guild.set_command_permissions(command, {self.role(r): True for r in roles})

    def role(self, name: str) -> Any:
        """Find a role, creating it the first time."""
        return self.guild.roles.get(name) or self.guild.create_role(name, permissions=Permissions.none())

    def member(self, name: str, *roles: str) -> Any:
        """Add a member holding these roles."""
        return self.guild.add_member(self.env.create_user(name), roles=[self.role(r) for r in roles])


@pytest.fixture
async def server(simcord_env) -> Server:
    """Open a server set up as the README says, with `/fm` and `/vocal` shown to their roles."""
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
