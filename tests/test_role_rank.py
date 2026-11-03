"""/user gives or takes a role only below the caller's highest role; the owner is exempt; op is unaffected."""

from __future__ import annotations

from datetime import UTC, datetime
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from conftest import make_guild, make_interaction, make_member, make_role
from discord import NotFound
import pytest

from fablabot.cogs import user_management
from fablabot.cogs.user_management import UserManagement
from fablabot.helpers.constants import RoleNames
from fablabot.ui.user import RIGHT_LOST_MESSAGE

GUILD_ID = 1001
CALLER_ID, TARGET_ID = 418003123456789012, 501
RANKS = {
    RoleNames.ADMIN_TEMP: 50,
    RoleNames.CODIR: 45,
    RoleNames.PRESIDENT: 40,
    RoleNames.SECRETARY: 30,
    RoleNames.BUREAU: 20,
    "Respo Numérique": 20,
    "Pôle Numérique": 10,
    RoleNames.SBIRE_BUREAU: 10,
}


@pytest.fixture(autouse=True)
def _context_ok():
    with (
        patch.object(user_management, "_utcnow", return_value=datetime(2026, 10, 28, 15, 0, tzinfo=UTC)),
        patch.object(user_management, "ensure_command_context", AsyncMock(return_value=True)),
        patch("fablabot.helpers.utils._is_in_allowed_channel", AsyncMock(return_value=True)),
    ):
        yield


@pytest.fixture
async def cog():
    """Build the cog and cancel its timers afterwards."""
    instance = UserManagement(MagicMock())
    yield instance
    for task in instance.deop_tasks.values():
        task.cancel()


class Server:
    """A ranked guild, a caller holding ``caller_roles`` and a target."""

    def __init__(self, *caller_roles: str, owner: bool = False, ranks: dict[str, int] | None = None) -> None:
        """Build the server; ``ranks`` overrides the default positions."""
        ranks = {**RANKS, **(ranks or {})}
        self.roles = {name: make_role(name, role_id=i, position=pos) for i, (name, pos) in enumerate(ranks.items())}
        self.caller = make_member(CALLER_ID, *(self.roles[n] for n in caller_roles))
        self.target = make_member(TARGET_ID)
        self.guild = make_guild(GUILD_ID, list(self.roles.values()), [self.caller, self.target])
        self.guild.owner_id = CALLER_ID if owner else 1
        self.caller.guild = self.target.guild = self.guild

    def interaction(self) -> MagicMock:
        """Return an interaction made by the caller."""
        interaction = make_interaction(guild_id=GUILD_ID)
        interaction.guild = self.guild
        interaction.user = self.caller
        interaction.followup.send = AsyncMock(return_value=MagicMock(id=42))
        interaction.channel_id = 777
        return interaction


def _call(command: Any) -> Any:
    """Return the plain callback of a command, untyped like the Discord decorator leaves it."""
    return command.callback


async def _add(cog: UserManagement, server: Server, role: str) -> MagicMock:
    interaction = server.interaction()
    await _call(UserManagement.user_add_role)(cog, interaction, member=server.target, role=server.roles[role])
    return interaction


def test_refusal_names_the_way_out() -> None:
    """The refusal says who to ask, not only that the role is refused."""
    assert "demandez à un membre" in user_management._ROLE_NOT_BELOW_REFUSED


async def test_secretary_general_cannot_give_president(cog: UserManagement) -> None:
    """A role above the caller's highest is refused, with the way out."""
    server = Server(RoleNames.SECRETARY)
    interaction = await _add(cog, server, RoleNames.PRESIDENT)

    server.target.add_roles.assert_not_awaited()
    interaction.response.send_message.assert_awaited_once_with(user_management._ROLE_NOT_BELOW_REFUSED, ephemeral=True)


async def test_admin_temp_does_not_raise_the_ceiling(cog: UserManagement) -> None:
    """Holding the temporary role gives no right to give or take a role above one's own rank."""
    server = Server(RoleNames.SECRETARY, RoleNames.ADMIN_TEMP)
    interaction = await _add(cog, server, RoleNames.PRESIDENT)
    server.target.add_roles.assert_not_awaited()
    interaction.response.send_message.assert_awaited_once_with(user_management._ROLE_NOT_BELOW_REFUSED, ephemeral=True)

    removal = server.interaction()
    await _call(UserManagement.user_remove_role)(cog, removal, member=server.target, role=server.roles[RoleNames.PRESIDENT])
    server.target.remove_roles.assert_not_awaited()
    removal.response.send_message.assert_awaited_once_with(user_management._ROLE_NOT_BELOW_REFUSED, ephemeral=True)


async def test_secretary_general_cannot_take_president(cog: UserManagement) -> None:
    """Taking a role is held to the same rank rule."""
    server = Server(RoleNames.SECRETARY)
    interaction = server.interaction()

    await _call(UserManagement.user_remove_role)(cog, interaction, member=server.target, role=server.roles[RoleNames.PRESIDENT])

    server.target.remove_roles.assert_not_awaited()
    interaction.response.send_message.assert_awaited_once_with(user_management._ROLE_NOT_BELOW_REFUSED, ephemeral=True)


async def test_equal_rank_is_refused(cog: UserManagement) -> None:
    """A role at the rank of the caller's highest is refused."""
    server = Server(RoleNames.SECRETARY, ranks={RoleNames.PRESIDENT: 30})
    await _add(cog, server, RoleNames.PRESIDENT)

    server.target.add_roles.assert_not_awaited()


async def test_one_rank_below_is_given(cog: UserManagement) -> None:
    """A role strictly below the caller's highest is given."""
    server = Server(RoleNames.SECRETARY, ranks={RoleNames.PRESIDENT: 29})
    await _add(cog, server, RoleNames.PRESIDENT)

    server.target.add_roles.assert_awaited_once()


async def test_respo_still_gives_their_pole(cog: UserManagement) -> None:
    """A Respo gives the matching Pôle."""
    server = Server("Respo Numérique")
    await _add(cog, server, "Pôle Numérique")

    server.target.add_roles.assert_awaited_once()


async def test_bureau_still_gives_sbire_bureau(cog: UserManagement) -> None:
    """The bureau gives Sbire Bureau."""
    server = Server(RoleNames.BUREAU)
    await _add(cog, server, RoleNames.SBIRE_BUREAU)

    server.target.add_roles.assert_awaited_once()


async def test_owner_is_exempt(cog: UserManagement) -> None:
    """The server owner gives a role above their own."""
    server = Server(RoleNames.SECRETARY, owner=True)
    await _add(cog, server, RoleNames.PRESIDENT)

    server.target.add_roles.assert_awaited_once()


async def test_president_still_ops_with_admin_temp_above(cog: UserManagement) -> None:
    """Op goes through although the temporary role outranks the president."""
    server = Server(RoleNames.PRESIDENT, RoleNames.CODIR, ranks={RoleNames.CODIR: 5})
    interaction = server.interaction()

    await _call(UserManagement.user_op)(cog, interaction, member=server.target, reason="atelier", time=5)

    server.target.add_roles.assert_awaited_once()
    for task in cog.deop_tasks.values():
        task.cancel()


async def test_bulk_add_refuses_a_role_above_the_caller(cog: UserManagement) -> None:
    """The selector of add_roles refuses at the command, before any selection."""
    server = Server(RoleNames.SECRETARY)
    interaction = server.interaction()

    await _call(UserManagement.user_add_roles)(cog, interaction, role=server.roles[RoleNames.PRESIDENT])

    interaction.followup.send.assert_not_awaited()
    interaction.response.send_message.assert_awaited_once_with(user_management._ROLE_NOT_BELOW_REFUSED, ephemeral=True)


async def test_bulk_remove_refuses_a_role_above_the_caller(cog: UserManagement) -> None:
    """The selector of remove_roles refuses as well."""
    server = Server(RoleNames.SECRETARY)
    interaction = server.interaction()

    await _call(UserManagement.user_remove_roles)(cog, interaction, role=server.roles[RoleNames.PRESIDENT])

    interaction.followup.send.assert_not_awaited()
    interaction.response.send_message.assert_awaited_once_with(user_management._ROLE_NOT_BELOW_REFUSED, ephemeral=True)


async def test_president_cannot_take_admin_temp_by_role_and_is_sent_to_deop(
    cog: UserManagement, caplog: pytest.LogCaptureFixture
) -> None:
    """With the temporary role above them, a president is refused it as a role and told about /user deop."""
    caplog.set_level(logging.DEBUG)
    server = Server(RoleNames.PRESIDENT)
    interaction = server.interaction()

    await _call(UserManagement.user_remove_role)(
        cog, interaction, member=server.target, role=server.roles[RoleNames.ADMIN_TEMP]
    )

    server.target.remove_roles.assert_not_awaited()
    interaction.response.send_message.assert_awaited_once_with(user_management._TEMP_ROLE_TAKE_REFUSED, ephemeral=True)
    assert "/user deop" in user_management._TEMP_ROLE_TAKE_REFUSED
    assert any("kind=temp_role_outside_deop" in r.getMessage() for r in caplog.records)


async def test_president_still_deops_with_admin_temp_above(cog: UserManagement) -> None:
    """Deop revokes the temporary role although it outranks the president."""
    server = Server(RoleNames.PRESIDENT, RoleNames.CODIR, ranks={RoleNames.CODIR: 5})
    interaction = server.interaction()

    await _call(UserManagement.user_deop)(cog, interaction, member=server.target)

    server.target.remove_roles.assert_awaited_once()


@pytest.mark.parametrize("action", ["add", "remove"])
@pytest.mark.parametrize("moved", ["caller lowered", "role raised"])
async def test_confirm_checks_the_rank_again(cog: UserManagement, action: str, moved: str) -> None:
    """Whether the caller dropped or the role rose to the caller's rank before Confirm, nothing is applied."""
    server = Server(RoleNames.SECRETARY, ranks={RoleNames.PRESIDENT: 29})
    live = server.roles[RoleNames.PRESIDENT]
    option = make_role(live.name, role_id=live.id, position=live.position)  # what the command received, frozen
    command = UserManagement.user_add_roles if action == "add" else UserManagement.user_remove_roles
    interaction = server.interaction()
    await _call(command)(cog, interaction, role=option)
    view = interaction.followup.send.await_args.kwargs["view"]
    view.select_members._values = [server.target]  # discord.py exposes no public setter for a selection

    if moved == "caller lowered":
        server.roles[RoleNames.SECRETARY].position = 20
    else:
        live.position = 35
    click = server.interaction()
    click.followup.edit_message = AsyncMock()
    click.delete_original_response = AsyncMock()
    await view.confirm_button.callback(click)

    server.target.add_roles.assert_not_awaited()
    server.target.remove_roles.assert_not_awaited()
    click.response.send_message.assert_awaited_once_with(RIGHT_LOST_MESSAGE, ephemeral=True)
    click.response.edit_message.assert_not_awaited()


async def test_confirm_with_the_role_deleted_leaves_the_failure_to_discord(cog: UserManagement) -> None:
    """A role deleted before Confirm is judged as the command saw it; Discord refuses it and the member is listed as failed."""
    server = Server(RoleNames.SECRETARY, ranks={RoleNames.PRESIDENT: 29})
    option = server.roles[RoleNames.PRESIDENT]
    interaction = server.interaction()
    await _call(UserManagement.user_add_roles)(cog, interaction, role=option)
    view = interaction.followup.send.await_args.kwargs["view"]
    view.select_members._values = [server.target]  # discord.py exposes no public setter for a selection

    server.guild.roles.remove(option)
    server.target.add_roles.side_effect = NotFound(MagicMock(status=404, reason="Not Found"), "Unknown Role")
    click = server.interaction()
    edit_message = AsyncMock()
    click.followup.edit_message = edit_message
    click.delete_original_response = AsyncMock()
    await view.confirm_button.callback(click)

    click.response.send_message.assert_not_awaited()
    assert "Échec : <@501>" in edit_message.await_args_list[0].kwargs["content"]
