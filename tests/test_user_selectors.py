"""Selector flows of /user: rights checked again on Confirm, no self-grant, member lists kept private."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

from conftest import make_guild, make_interaction, make_member, make_role
import pytest

from fablabot.cogs import user_management
from fablabot.cogs.user_management import UserManagement
from fablabot.helpers.constants import RoleNames

GUILD_ID = 1001
CALLER_ID, TARGET_ID, OTHER_ID = 418003123456789012, 501, 502


@pytest.fixture(autouse=True)
def _commands_channel_ok():
    """Skip the commands-channel check so the real role check runs."""
    with patch("fablabot.helpers.utils._is_in_allowed_channel", AsyncMock(return_value=True)):
        yield


class Server:
    """A guild with staff roles, a caller holding ``caller_roles`` and two other members."""

    def __init__(self, *caller_roles: str) -> None:
        """Build the server with the caller holding ``caller_roles``."""
        names = (RoleNames.ADMIN_TEMP, RoleNames.PRESIDENT, RoleNames.BUREAU, "F - Laser", *user_management.ADMIN_ROLES)
        self.roles = {name: make_role(name, role_id=i) for i, name in enumerate(dict.fromkeys(names))}
        self.caller = make_member(CALLER_ID, *(self.roles[n] for n in caller_roles))
        self.target = make_member(TARGET_ID)
        self.other = make_member(OTHER_ID)
        self.guild = make_guild(GUILD_ID, list(self.roles.values()), [self.caller, self.target, self.other])

    def interaction(self) -> MagicMock:
        """Return an interaction made by the caller in this server."""
        interaction = make_interaction(guild_id=GUILD_ID)
        interaction.guild = self.guild
        interaction.user = self.caller
        interaction.followup.send = AsyncMock(return_value=MagicMock(id=42))
        interaction.followup.edit_message = AsyncMock()
        interaction.delete_original_response = AsyncMock()
        return interaction


async def _open_bulk(server: Server, role_name: str, action: str) -> tuple[MagicMock, object]:
    interaction = server.interaction()
    command = UserManagement.user_add_roles if action == "add" else UserManagement.user_remove_roles
    await command.callback(UserManagement(MagicMock()), interaction, role=server.roles[role_name])
    sent = interaction.followup.send.await_args
    return interaction, sent.kwargs.get("view") if sent else None


async def _confirm_bulk(server: Server, view: object, *members: MagicMock) -> MagicMock:
    # discord.py exposes no public setter for a selection
    view.select_members._values = list(members)
    click = server.interaction()
    await view.confirm_button.callback(click)
    return click


# -- Bulk assignment --


async def test_bulk_add_refuses_temp_role_at_command(caplog: pytest.LogCaptureFixture) -> None:
    """Adding the temporary role in bulk is refused at the command and points to the op command."""
    caplog.set_level(logging.DEBUG)
    server = Server(RoleNames.PRESIDENT)

    interaction, view = await _open_bulk(server, RoleNames.ADMIN_TEMP, "add")

    assert view is None
    assert "/user op" in interaction.response.send_message.await_args.args[0]
    assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True
    assert any("kind=temp_role_outside_op" in r.getMessage() for r in caplog.records)


async def test_bulk_add_skips_self_and_lists_it_as_refused(caplog: pytest.LogCaptureFixture) -> None:
    """A bulk add skips the caller and lists them as refused."""
    caplog.set_level(logging.DEBUG)
    server = Server(RoleNames.PRESIDENT)
    _interaction, view = await _open_bulk(server, "F - Laser", "add")

    click = await _confirm_bulk(server, view, server.caller, server.target)

    server.caller.add_roles.assert_not_awaited()
    server.target.add_roles.assert_awaited_once()
    summary = click.followup.edit_message.await_args.kwargs["content"]
    assert "Refusé" in summary
    assert "<@418003123456789012>" in summary.split("Refusé", 1)[1]
    assert any("kind=self_target" in r.getMessage() for r in caplog.records)


async def test_bulk_remove_from_self_is_allowed() -> None:
    """A bulk remove may target the caller."""
    server = Server(RoleNames.PRESIDENT, "F - Laser")
    _interaction, view = await _open_bulk(server, "F - Laser", "remove")

    await _confirm_bulk(server, view, server.caller)

    server.caller.remove_roles.assert_awaited_once()


async def test_bulk_confirm_after_losing_rights_changes_nothing(caplog: pytest.LogCaptureFixture) -> None:
    """Confirming a bulk change after losing rights changes no role."""
    caplog.set_level(logging.DEBUG)
    server = Server(RoleNames.PRESIDENT)
    _interaction, view = await _open_bulk(server, "F - Laser", "add")
    server.caller.roles.clear()

    click = await _confirm_bulk(server, view, server.target, server.other)

    server.target.add_roles.assert_not_awaited()
    server.other.add_roles.assert_not_awaited()
    assert click.response.send_message.await_args.kwargs["ephemeral"] is True
    click.followup.edit_message.assert_not_awaited()
    lines = [r.getMessage() for r in caplog.records if "refused" in r.getMessage()]
    assert lines == [f"[user.add_roles] refused kind=right_lost_before_confirm guild_id={GUILD_ID} user_id={CALLER_ID}"]


# -- Role search --


@pytest.mark.parametrize("roles", [(), (RoleNames.ADMIN_TEMP,), ("F - Laser",)], ids=["none", "temp-only", "trainer"])
async def test_with_roles_refuses_non_staff(roles: tuple[str, ...]) -> None:
    """The role search is refused to callers outside the staff."""
    server = Server(*roles)
    interaction = server.interaction()

    await UserManagement.user_with_roles.callback(UserManagement(MagicMock()), interaction)

    assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True
    interaction.followup.send.assert_not_awaited()


@pytest.mark.parametrize("role", [RoleNames.PRESIDENT, RoleNames.BUREAU], ids=["president", "bureau"])
async def test_with_roles_list_is_private(role: str) -> None:
    """The role search answers privately and lists the members holding the role."""
    server = Server(role)
    server.roles["F - Laser"].members.extend([server.target])
    interaction = server.interaction()

    await UserManagement.user_with_roles.callback(UserManagement(MagicMock()), interaction)

    sent = interaction.response.send_message.await_args
    assert sent.kwargs["ephemeral"] is True
    view = sent.kwargs["view"]
    interaction.response.defer.assert_not_awaited()
    interaction.followup.send.assert_not_awaited()

    # discord.py exposes no public setter for a selection
    view.select_roles._values = [server.roles["F - Laser"]]
    click = server.interaction()
    await view.confirm_button.callback(click)

    content = click.response.edit_message.await_args.kwargs["content"]
    assert "<@501>" in content
    click.followup.edit_message.assert_not_awaited()
    click.followup.send.assert_not_awaited()


async def test_with_roles_confirm_after_losing_rights_lists_nothing(caplog: pytest.LogCaptureFixture) -> None:
    """Confirming a role search after losing rights lists nothing."""
    caplog.set_level(logging.DEBUG)
    server = Server(RoleNames.BUREAU)
    interaction = server.interaction()
    await UserManagement.user_with_roles.callback(UserManagement(MagicMock()), interaction)
    view = interaction.response.send_message.await_args.kwargs["view"]
    server.caller.roles.clear()

    # discord.py exposes no public setter for a selection
    view.select_roles._values = [server.roles["F - Laser"]]
    click = server.interaction()
    await view.confirm_button.callback(click)

    assert click.response.send_message.await_args.kwargs["ephemeral"] is True
    click.response.edit_message.assert_not_awaited()
    assert any("kind=right_lost_before_confirm" in r.getMessage() for r in caplog.records)
