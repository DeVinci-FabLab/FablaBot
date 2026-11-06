"""/welcome approve gives the two member roles only below the caller's highest role; the owner is exempt."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

from conftest import make_guild, make_interaction, make_member, make_role
from discord import TextChannel

from fablabot.cogs import welcome_management
from fablabot.cogs.welcome_management import WelcomeManagement
from fablabot.helpers.constants import RoleNames

if TYPE_CHECKING:
    import pytest

GUILD_ID = 1001
CALLER_ID, NEWCOMER_ID = 418003123456789012, 501
CITY = "Membre Paris"


class Server:
    """A ranked guild with a welcome channel for one newcomer and a caller holding ``caller_roles``."""

    def __init__(self, *caller_roles: str, owner: bool = False, city_rank: int = 10, verified_rank: int = 10) -> None:
        """Build the server: the caller's roles sit at 30 (Secrétaire Général) and 40 (Président), the temporary one at 50."""
        ranks = {
            RoleNames.ADMIN_TEMP: 50,
            RoleNames.PRESIDENT: 40,
            RoleNames.SECRETARY: 30,
            CITY: city_rank,
            RoleNames.MEMBER_VERIFIED: verified_rank,
        }
        self.roles = {name: make_role(name, role_id=i, position=pos) for i, (name, pos) in enumerate(ranks.items())}
        self.caller = make_member(CALLER_ID, *(self.roles[name] for name in caller_roles))
        self.newcomer = make_member(NEWCOMER_ID)
        self.channel = MagicMock(spec=TextChannel)
        self.channel.id = 9
        self.channel.name = f"welcome-{self.newcomer.name}"
        self.channel.overwrites = {}
        self.channel.delete = AsyncMock()
        self.guild = make_guild(GUILD_ID, list(self.roles.values()), [self.caller, self.newcomer], channel=self.channel)
        self.guild.owner_id = CALLER_ID if owner else 1
        self.caller.guild = self.newcomer.guild = self.guild

    async def approve(self) -> MagicMock:
        """Run the command as the caller, in the welcome channel, and return its interaction."""
        interaction = make_interaction(guild_id=GUILD_ID)
        interaction.guild = self.guild
        interaction.user = self.caller
        interaction.channel = self.channel
        with patch.object(welcome_management, "ensure_command_context", AsyncMock(return_value=True)):
            command: Any = WelcomeManagement.welcome_approve.callback
            await command(WelcomeManagement(MagicMock()), interaction, city="Paris")
        return interaction


async def test_a_caller_below_the_roles_given_gives_nothing(caplog: pytest.LogCaptureFixture) -> None:
    """A caller whose highest role sits below one of the two roles is refused with the way out, and the channel stays."""
    caplog.set_level(logging.WARNING)
    server = Server(RoleNames.SECRETARY, city_rank=35)

    interaction = await server.approve()

    server.newcomer.add_roles.assert_not_awaited()
    server.channel.delete.assert_not_awaited()
    interaction.response.send_message.assert_awaited_once_with(welcome_management._ROLE_NOT_BELOW_REFUSED, ephemeral=True)
    assert "demandez à un membre" in welcome_management._ROLE_NOT_BELOW_REFUSED
    assert "role_not_below" in caplog.text


async def test_the_verified_role_is_held_to_the_rule_too() -> None:
    """Either role above the caller's rank refuses both."""
    server = Server(RoleNames.SECRETARY, verified_rank=35)

    await server.approve()

    server.newcomer.add_roles.assert_not_awaited()


async def test_a_role_at_the_callers_rank_is_refused() -> None:
    """Strictly above means a role at the same rank is refused."""
    server = Server(RoleNames.SECRETARY, city_rank=30)

    await server.approve()

    server.newcomer.add_roles.assert_not_awaited()
    server.channel.delete.assert_not_awaited()


async def test_a_caller_strictly_above_both_roles_approves() -> None:
    """The member gets the two roles and the welcome channel goes."""
    server = Server(RoleNames.SECRETARY, city_rank=29, verified_rank=10)

    interaction = await server.approve()

    server.newcomer.add_roles.assert_awaited_once()
    assert {role.name for role in server.newcomer.add_roles.await_args.args} == {CITY, RoleNames.MEMBER_VERIFIED}
    server.channel.delete.assert_awaited_once()
    assert "validé avec succès" in interaction.response.send_message.await_args.args[0]


async def test_the_temporary_role_does_not_raise_the_ceiling() -> None:
    """Holding Admin -temp- on top of a lower role gives no right above that role."""
    server = Server(RoleNames.SECRETARY, RoleNames.ADMIN_TEMP, city_rank=35)

    await server.approve()

    server.newcomer.add_roles.assert_not_awaited()


async def test_the_server_owner_is_exempt() -> None:
    """The owner approves whatever the ranks, as for /user add_role."""
    server = Server(RoleNames.SECRETARY, owner=True, city_rank=35, verified_rank=45)

    await server.approve()

    server.newcomer.add_roles.assert_awaited_once()
    server.channel.delete.assert_awaited_once()
