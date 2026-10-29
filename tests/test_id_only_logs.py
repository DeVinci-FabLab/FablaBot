"""Log lines name members by Discord ID, never by name or object repr."""

from __future__ import annotations

import logging
import re
from unittest.mock import AsyncMock, MagicMock, patch

from conftest import make_interaction
from discord import Forbidden, Member, TextChannel, ui
import pytest

from fablabot.cogs import message_management
from fablabot.cogs.channel_management import ChannelManagement
from fablabot.cogs.message_management import MessageManagement
from fablabot.helpers.safe_discord_operations import safe_add_roles, safe_remove_roles
from fablabot.helpers.utils import log_request, send_dm_to_member
from fablabot.models.message import EasterEggTrigger

NAME = "kevin_secret_name"


def _member(member_id: int = 4242) -> MagicMock:
    """Build a stub member whose name and repr are recognisable.

    Args:
        member_id (int): The member ID.

    Returns:
        MagicMock: The stub member.
    """
    member = MagicMock(spec=Member)
    member.id = member_id
    member.name = NAME
    member.display_name = "Kevin Secret"
    member.bot = False
    member.__str__.return_value = NAME
    return member


def test_log_request_logs_caller_and_member_options_by_id(caplog: pytest.LogCaptureFixture) -> None:
    """A Member keyword is logged as ``<key>_id`` and no name appears."""
    caplog.set_level(logging.DEBUG)
    log_request(logging.getLogger("test"), "x", make_interaction(), member=_member(), count=3)
    assert "member_id=4242" in caplog.text
    assert "count=3" in caplog.text
    assert NAME not in caplog.text
    assert "Kevin Secret" not in caplog.text


@pytest.mark.parametrize("op", [safe_add_roles, safe_remove_roles], ids=["add", "remove"])
async def test_role_change_failure_logs_member_id_not_name(caplog: pytest.LogCaptureFixture, op) -> None:
    """A refused role change names the member by ID."""
    member = _member()
    err = Forbidden(MagicMock(status=403), "x")
    member.add_roles = AsyncMock(side_effect=err)
    member.remove_roles = AsyncMock(side_effect=err)
    role = MagicMock()
    role.name = "Role"
    await op(logging.getLogger("test"), member, role)
    assert "member_id=4242" in caplog.text
    assert NAME not in caplog.text


async def test_dm_failure_logs_member_id_not_name(caplog: pytest.LogCaptureFixture) -> None:
    """A failed DM names the member by ID."""
    caplog.set_level(logging.DEBUG)
    member = _member()
    member.send = AsyncMock(side_effect=Forbidden(MagicMock(status=403), "x"))
    guild = MagicMock()
    guild.id = 1234
    await send_dm_to_member(logging.getLogger("test"), guild, member, "hi", dm_type="t")
    assert "4242" in caplog.text
    assert NAME not in caplog.text
    assert "Kevin Secret" not in caplog.text


async def test_msg_clear_refusal_logs_user_id_not_name(caplog: pytest.LogCaptureFixture) -> None:
    """The /msg clear refusal names the caller by ID."""
    caplog.set_level(logging.DEBUG)
    interaction = make_interaction()
    interaction.channel = MagicMock(spec=TextChannel)
    interaction.permissions.manage_messages = False
    await MessageManagement.msg_clear.callback(MagicMock(), interaction, messages=5)
    assert "user_id=418003123456789012" in caplog.text
    assert NAME not in caplog.text


async def test_deleted_message_debug_line_has_no_message_repr(caplog: pytest.LogCaptureFixture) -> None:
    """The debug line for a deleted message carries its ID, not its repr."""
    caplog.set_level(logging.DEBUG)
    msg = MagicMock()
    msg.id = 99
    msg.author.bot = False
    msg.author.__str__.return_value = NAME
    msg.__str__.return_value = f"message by {NAME}"
    msg.channel = MagicMock(spec=TextChannel)
    await ChannelManagement(MagicMock()).on_message_delete(msg)
    assert "99" in caplog.text
    assert NAME not in caplog.text


async def test_easter_egg_line_logs_author_id_not_name(caplog: pytest.LogCaptureFixture) -> None:
    """The easter egg line names the author by ID."""
    caplog.set_level(logging.DEBUG)
    egg = EasterEggTrigger(keywords=[re.compile("hello")], response="", probability=1.0)
    msg = MagicMock()
    msg.author = _member()
    msg.author.bot = False
    msg.content = "hello"
    msg.guild.id = 987654321
    msg.channel = MagicMock(spec=TextChannel)
    msg.channel.category.name = "General"
    msg.add_reaction = AsyncMock()
    with (
        patch.object(message_management, "EASTER_EGGS_ENABLED", new=True),
        patch.object(message_management, "EASTER_EGGS", [egg]),
    ):
        await MessageManagement(MagicMock()).on_message(msg)
    assert "author_id=4242" in caplog.text
    assert NAME not in caplog.text


async def test_msg_purge_logs_the_purged_member_as_target_and_the_caller_as_user(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The purge lines name the purged member as ``target_id`` and only the caller as ``user_id``."""
    caplog.set_level(logging.DEBUG)

    async def confirm(self, _timeout=None) -> bool:
        """Answer the confirmation view as if the caller pressed the confirm button."""
        self.confirmed = True
        return False

    async def no_history(**_kwargs):
        """Yield no message."""
        return
        yield

    monkeypatch.setattr(ui.View, "wait", confirm)
    interaction = make_interaction()
    interaction.user.guild_permissions.administrator = True
    channel = MagicMock(spec=TextChannel)
    channel.name = "salon"
    channel.history = no_history
    channel.purge = AsyncMock(return_value=[MagicMock(), MagicMock()])

    await MessageManagement.msg_purge.callback(
        MessageManagement(MagicMock()),
        interaction,
        user=_member(),
        count=5,
        channel=channel,
    )

    purged = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Purged")]
    assert len(purged) == 2
    assert all("target_id=4242" in line for line in purged)
    assert [line.count("user_id=418003123456789012") for line in purged] == [0, 1]
    assert not any("user_id=4242" in line for line in purged)
