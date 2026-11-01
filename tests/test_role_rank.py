"""/user gives or takes a role only below the caller's highest role; the owner is exempt; op is unaffected."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from conftest import make_guild, make_interaction, make_member, make_role
import pytest

from fablabot.cogs import user_management
from fablabot.cogs.user_management import UserManagement
from fablabot.helpers.constants import RoleNames

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
        self.caller.guild.owner_id = CALLER_ID if owner else 1
        self.target = make_member(TARGET_ID)
        self.guild = make_guild(GUILD_ID, list(self.roles.values()), [self.caller, self.target])

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


async def test_secretary_general_cannot_give_president(cog: UserManagement) -> None:
    """A role above the caller's highest is refused, with the way out."""
    server = Server(RoleNames.SECRETARY)
    interaction = await _add(cog, server, RoleNames.PRESIDENT)

    server.target.add_roles.assert_not_awaited()
    assert "au-dessus" in interaction.response.send_message.await_args.args[0]


async def test_secretary_general_cannot_take_president(cog: UserManagement) -> None:
    """Taking a role is held to the same rank rule."""
    server = Server(RoleNames.SECRETARY)
    interaction = server.interaction()

    await _call(UserManagement.user_remove_role)(cog, interaction, member=server.target, role=server.roles[RoleNames.PRESIDENT])

    server.target.remove_roles.assert_not_awaited()
    interaction.response.send_message.assert_awaited_once()


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

    await _call(UserManagement.user_op)(cog, interaction, member=server.target, reason="atelier", time=30)

    server.target.add_roles.assert_awaited_once()
    for task in cog.deop_tasks.values():
        task.cancel()


async def test_bulk_add_refuses_a_role_above_the_caller(cog: UserManagement) -> None:
    """The selector of add_roles refuses at the command, before any selection."""
    server = Server(RoleNames.SECRETARY)
    interaction = server.interaction()

    await _call(UserManagement.user_add_roles)(cog, interaction, role=server.roles[RoleNames.PRESIDENT])

    interaction.followup.send.assert_not_awaited()
    assert "au-dessus" in interaction.response.send_message.await_args.args[0]


async def test_bulk_remove_refuses_a_role_above_the_caller(cog: UserManagement) -> None:
    """The selector of remove_roles refuses as well."""
    server = Server(RoleNames.SECRETARY)
    interaction = server.interaction()

    await _call(UserManagement.user_remove_roles)(cog, interaction, role=server.roles[RoleNames.PRESIDENT])

    interaction.followup.send.assert_not_awaited()
    interaction.response.send_message.assert_awaited_once()
