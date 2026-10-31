"""``/help`` lists what the member can run, privately by default, from the server's locale."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

from conftest import make_interaction
from discord import AllowedMentions, Embed, Locale, Permissions, TextChannel, app_commands
from mockups.screens import declared_commands
import pytest

from fablabot.cogs import help_management
from fablabot.cogs.help_management import HelpManagement
from fablabot.helpers import utils
from fablabot.helpers.access import Caller
from fablabot.helpers.constants import BRAND_COLOUR, ErrorMessages
from fablabot.helpers.guild_access import GuildAccessPolicy, GuildAccessTree
from fablabot.helpers.help_page import build_help
from fablabot.helpers.i18n import catalog

if TYPE_CHECKING:
    from collections.abc import Iterator

USER_ID = 418003123456789012
SUGGEST_LINE = "`/suggest`: send an idea or a problem to the staff, anonymously if you like"


def _cog() -> HelpManagement:
    """Build the cog on a bot whose tree holds every declared command."""
    bot = MagicMock()
    bot.tree.walk_commands.side_effect = lambda: iter(declared_commands())
    return HelpManagement(bot)


def _interaction(locale: Locale | None = Locale.american_english) -> MagicMock:
    """Build a member's interaction with no special permission and a server locale."""
    interaction = make_interaction(user_id=USER_ID)
    interaction.user.guild_permissions = Permissions.none()
    interaction.guild_locale = locale
    return interaction


async def _help(interaction: MagicMock, *, show: bool = False, cog: HelpManagement | None = None) -> None:
    """Run the callback with the role and channel check allowing the call."""
    with patch.object(help_management, "ensure_command_context", AsyncMock(return_value=True)):
        await HelpManagement.show_help.callback(cog or _cog(), interaction, show=show)  # pyright: ignore[reportCallIssue]  # cog bound away


def _embed(interaction: MagicMock) -> Embed:
    """Return the embed of the one reply."""
    interaction.response.send_message.assert_awaited_once()
    return interaction.response.send_message.await_args.kwargs["embed"]


def _field(embed: Embed, name: str) -> str:
    """Return the value of the field called ``name``."""
    return next(field.value or "" for field in embed.fields if field.name == name)


async def test_member_sees_suggest_and_help() -> None:
    """A member with no staff role gets one field, for everyone, with the suggest and help lines."""
    interaction = _interaction()
    await _help(interaction)
    embed = _embed(interaction)
    assert embed.title == "What you can run here"
    assert embed.description == "These are the commands your roles let you run on this server."
    assert embed.colour is not None
    assert embed.colour.value == BRAND_COLOUR
    assert [field.name for field in embed.fields] == ["For everyone", "Want to do more?"]
    assert _field(embed, "For everyone") == f"{SUGGEST_LINE}\n`/help`: show this list"


async def test_member_is_told_how_to_get_more() -> None:
    """The last field sends the member to the staff for a role."""
    interaction = _interaction()
    await _help(interaction)
    value = _field(_embed(interaction), "Want to do more?")
    assert "ask a staff member to give you the right role" in value


async def test_footer_points_to_suggest() -> None:
    """The footer tells where to report something missing."""
    interaction = _interaction()
    await _help(interaction)
    assert _embed(interaction).footer.text == "Something missing or not working? Tell us with /suggest."


async def test_help_is_private_by_default() -> None:
    """Without ``show`` the reply is ephemeral."""
    interaction = _interaction()
    await _help(interaction)
    assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True


async def test_show_posts_the_same_reply_in_the_channel() -> None:
    """With ``show`` the reply is public and carries the same embed."""
    private, public = _interaction(), _interaction()
    await _help(private)
    await _help(public, show=True)
    assert public.response.send_message.await_args.kwargs["ephemeral"] is False
    assert _embed(public).to_dict() == _embed(private).to_dict()


async def test_reply_follows_the_server_locale() -> None:
    """A French server gets French fields and the command named in French."""
    interaction = _interaction(Locale.french)
    await _help(interaction)
    embed = _embed(interaction)
    assert _field(embed, "Pour tout le monde").endswith("`/aide`\N{NO-BREAK SPACE}: affiche cette liste")
    assert embed.title == "Ce que tu peux lancer ici"


@pytest.mark.parametrize(
    ("locale", "promise"),
    [
        (Locale.french, "`/suggest`\N{NO-BREAK SPACE}: envoie une idée ou un problème au staff, anonymement si tu veux"),
        (Locale.german, "`/suggest`: schickt dem Staff eine Idee oder ein Problem, auf Wunsch anonym"),
    ],
)
async def test_suggest_line_keeps_the_anonymity_promise_in_french_and_german(locale: Locale, promise: str) -> None:
    """The French and German suggest lines say, in their own words, that the idea can be sent anonymously."""
    interaction = _interaction(locale)
    await _help(interaction)
    assert promise in (_embed(interaction).fields[0].value or "")


@pytest.mark.parametrize("locale", [Locale.french, Locale.german, Locale.american_english, None])
async def test_suggest_line_is_the_long_text_in_every_locale(locale: Locale | None) -> None:
    """The suggest line carries the long catalogue text, not the short command description."""
    texts = catalog().texts(locale.value if locale else None)
    interaction = _interaction(locale)
    await _help(interaction)
    fields = _embed(interaction).fields
    assert texts.get("help.cmd.suggest") in (fields[0].value or "")


async def test_help_never_pings() -> None:
    """The reply allows no mention."""
    interaction = _interaction()
    await _help(interaction)
    assert interaction.response.send_message.await_args.kwargs["allowed_mentions"].to_dict() == AllowedMentions.none().to_dict()


def test_command_without_a_rule_is_never_listed() -> None:
    """A command with no access rule is left out, even under a listed name."""

    async def callback(interaction: Any) -> None:
        """Do nothing."""

    orphan = app_commands.Command(name="suggest", description="x", callback=callback)
    caller = Caller(roles=(), permissions=Permissions.none(), server_roles=())
    embeds = build_help([orphan], caller, catalog().texts("en"))
    assert [field.name for field in embeds[0].fields] == ["Want to do more?"]


@pytest.fixture
def allowed_channel() -> Iterator[None]:
    """Let the commands-channel check pass, keeping the rest of the real check."""
    with patch.object(utils, "_is_in_allowed_channel", AsyncMock(return_value=True)):
        yield


@pytest.mark.usefixtures("allowed_channel")
async def test_help_logs_ids_and_show_only(caplog: pytest.LogCaptureFixture) -> None:
    """One request line carries the user ID and ``show``, and no record holds a text of the reply."""
    caplog.set_level(logging.DEBUG)
    interaction = _interaction()
    await HelpManagement.show_help.callback(_cog(), interaction, show=False)  # pyright: ignore[reportCallIssue]  # cog bound away
    lines = [r.getMessage() for r in caplog.records if "[help]" in r.getMessage()]
    assert lines == [f"[help]: user_id={USER_ID} show=False"]
    reply = _embed(interaction)
    texts = [
        reply.title or "",
        reply.description or "",
        reply.footer.text or "",
        *(field.value or "" for field in reply.fields),
    ]
    assert all(text not in record.getMessage() for record in caplog.records for text in texts)


async def test_public_help_is_refused_outside_the_commands_channel() -> None:
    """With ``show``, the channel check still applies: the member is sent to the commands channel and gets no list."""
    interaction = _interaction()
    commands = MagicMock(spec=TextChannel)
    commands.id = 55
    commands.name = "commandes_bot"
    commands.mention = "<#55>"
    other = MagicMock(spec=TextChannel)
    other.id = 66
    other.name = "general"
    interaction.channel = other
    interaction.guild.channels = [commands]
    interaction.guild.get_channel = MagicMock(return_value=commands)
    await HelpManagement.show_help.callback(_cog(), interaction, show=True)  # pyright: ignore[reportCallIssue]  # cog bound away
    interaction.response.send_message.assert_awaited_once()
    args, kwargs = interaction.response.send_message.await_args
    assert args[0] == catalog().texts("en-US").get("help.public_channel_only", channel="<#55>")
    assert "embed" not in kwargs


async def test_private_help_answers_outside_the_commands_channel() -> None:
    """Without ``show``, a member who cannot open the commands channel still gets the list."""
    interaction = _interaction()
    other = MagicMock(spec=TextChannel)
    other.id = 66
    other.name = "general"
    interaction.channel = other
    await HelpManagement.show_help.callback(_cog(), interaction, show=False)  # pyright: ignore[reportCallIssue]  # cog bound away
    interaction.response.send_message.assert_awaited_once()
    assert "embed" in interaction.response.send_message.await_args.kwargs


async def test_help_is_refused_outside_listed_servers() -> None:
    """The tree's server check covers the help command like any other."""
    client = MagicMock()
    client._connection._command_tree = None
    client.guild_access = GuildAccessPolicy(frozenset({111111111111111111}))
    tree = GuildAccessTree(client)
    interaction = _interaction()
    interaction.guild_id = 222222222222222222
    interaction.type = MagicMock()
    interaction.command = HelpManagement.show_help
    assert await tree.interaction_check(interaction) is False
    assert interaction.response.send_message.await_args.args[0] == ErrorMessages.GUILD_NOT_ALLOWED


@pytest.mark.parametrize("show", [False, True])
async def test_every_message_keeps_the_visibility(show: bool) -> None:
    """A help split in two sends both messages with the same visibility and no mention."""
    first, second = Embed(title="one"), Embed(title="two")
    interaction = _interaction()
    with patch.object(help_management, "build_help", return_value=[first, second]):
        await _help(interaction, show=show)
    sent = interaction.response.send_message.await_args.kwargs
    followed = interaction.followup.send.await_args.kwargs
    assert (sent["embed"], followed["embed"]) == (first, second)
    assert (sent["ephemeral"], followed["ephemeral"]) == (not show, not show)
    assert sent["allowed_mentions"].to_dict() == AllowedMentions.none().to_dict()
    assert followed["allowed_mentions"].to_dict() == AllowedMentions.none().to_dict()
