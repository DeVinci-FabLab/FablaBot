"""What /help lists matches what every command's own check lets a member run, for each kind of member."""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

from conftest import make_interaction
from discord import Member, Permissions, Role
import pytest

from fablabot.cogs import user_management
from fablabot.helpers import utils
from fablabot.helpers.access import AnyRole, Caller, rule_of
from fablabot.helpers.constants import ErrorMessages, RoleNames
from fablabot.helpers.help_page import EVERYONE_DOMAIN, HELP_COMMAND, build_help, leaf_commands
from fablabot.helpers.i18n import catalog

if TYPE_CHECKING:
    from collections.abc import Iterator

    from discord import app_commands

SERVER_ROLES = tuple(value for key, value in vars(RoleNames).items() if key.isupper())
F_ROLE = "F - Découpe laser"
NO_RIGHT = (
    ErrorMessages.ROLE_REQUIRED.split("{")[0],
    ErrorMessages.INSUFFICIENT_PERMISSIONS,
    ErrorMessages.NO_PERMISSION_ADD_ROLE,
    ErrorMessages.NO_PERMISSION_REMOVE_ROLE,
    ErrorMessages.NO_PERMISSION_MANAGE_MESSAGES,
    user_management._TEMP_ROLE_REFUSED,
    "Seuls les administrateurs peuvent utiliser cette commande",
)


@dataclass(frozen=True)
class Profile:
    """A kind of member: the roles held (lowest first), the server permissions, and whether the server has F roles."""

    roles: tuple[str, ...]
    permissions: Permissions
    expected: int
    f_roles: bool = False


PROFILES = {
    "member": Profile((RoleNames.MEMBER_VERIFIED,), Permissions.none(), 0),
    "trainer": Profile((RoleNames.TRAININGS_MANAGER,), Permissions.none(), 9),
    "trainer-with-F-roles": Profile((RoleNames.TRAININGS_MANAGER,), Permissions.none(), 9, f_roles=True),
    "temp-admin": Profile((RoleNames.ADMIN_TEMP,), Permissions.none(), 9),
    "codir": Profile((RoleNames.CODIR,), Permissions.none(), 9),
    "bureau": Profile((RoleNames.BUREAU,), Permissions.none(), 10),
    "digital-manager": Profile((RoleNames.DIGITAL_MANAGER,), Permissions.none(), 1),
    "admin-no-permission": Profile((RoleNames.ADMIN,), Permissions.none(), 28),
    "admin-bureau-administrator": Profile((RoleNames.BUREAU, RoleNames.ADMIN), Permissions.all(), 29),
    "manage-messages-only": Profile((), Permissions(manage_messages=True), 0),
}


class _Answered(BaseException):
    """Raised by the armed reply mocks to stop a callback once it has answered."""


def _role(name: str) -> MagicMock:
    """Build a role stub with a name."""
    role = MagicMock(spec=Role)
    role.name = name
    return role


@dataclass
class World:
    """One member on one server: the interaction they use and the roles the server holds."""

    interaction: MagicMock
    server_roles: list[MagicMock]
    caller: Caller


def _world(profile: Profile) -> World:
    """Build the interaction, the server's roles and the help caller of a profile."""
    interaction = make_interaction()
    names = (*SERVER_ROLES, F_ROLE) if profile.f_roles else SERVER_ROLES
    server_roles = [_role(name) for name in names]
    interaction.guild.roles = server_roles
    interaction.user.roles = [_role(name) for name in profile.roles]
    interaction.user.guild_permissions = profile.permissions
    interaction.permissions = profile.permissions
    for mock in (
        interaction.response.send_message,
        interaction.response.send_modal,
        interaction.response.defer,
        interaction.response.edit_message,
    ):
        mock.side_effect = _Answered
    return World(interaction, server_roles, Caller.of(interaction.user, interaction.guild))


@pytest.fixture
def patched_checks() -> Iterator[None]:
    """Let the commands-channel check pass and keep role edits from reaching Discord."""
    with (
        patch.object(utils, "_is_in_allowed_channel", AsyncMock(return_value=True)),
        patch.object(user_management, "safe_add_roles", AsyncMock(return_value=(False, "stop"))),
        patch.object(user_management, "safe_remove_roles", AsyncMock(return_value=(False, "stop"))),
    ):
        yield


async def _try(command: app_commands.Command[Any, ..., Any], world: World, role: MagicMock) -> bool:
    """Run the callback once with ``role`` as its role option and tell whether a no-right reply came first."""
    interaction = world.interaction
    interaction.response.send_message.reset_mock()
    target = MagicMock(spec=Member)
    target.id = 987654321
    values: dict[str, Any] = {"member": target, "role": role, "time": 5, "reason": "test"}
    options = {param.name: values.get(param.name, MagicMock()) for param in command.parameters}
    with contextlib.suppress(_Answered, Exception):
        await cast("Any", command.callback)(command.binding, interaction, **options)
    calls = interaction.response.send_message.call_args_list
    if not calls:
        return False
    text = calls[0].args[0] if calls[0].args else calls[0].kwargs.get("content", "")
    return isinstance(text, str) and text.startswith(NO_RIGHT)


async def _refused(command: app_commands.Command[Any, ..., Any], world: World) -> bool:
    """Tell whether the command's own check turns the member away (with any server role for a role option)."""
    options = [param.name for param in command.parameters]
    candidates = world.server_roles if "role" in options else world.server_roles[:1]
    for role in candidates:
        if not await _try(command, world, role):
            return False
    return True


def _ruled(bot: Any) -> list[app_commands.Command[Any, ..., Any]]:
    """The commands of the real tree that carry an access rule, each once."""
    ruled = {c.qualified_name: c for c in leaf_commands(bot.tree.walk_commands()) if rule_of(c) is not None}
    return list(ruled.values())


@pytest.mark.usefixtures("patched_checks")
@pytest.mark.parametrize("profile", PROFILES)
async def test_help_matches_every_callback(profile: str, started_bot: Any) -> None:
    """For every command with a rule, the rule admits the member exactly when the callback does not turn them away."""
    world = _world(PROFILES[profile])
    mismatches: list[str] = []
    for command in _ruled(started_bot):
        rule = rule_of(command)
        assert rule is not None
        admitted = rule.admits(world.caller)
        refused = await _refused(command, world)
        if admitted == refused:
            mismatches.append(f"/{command.qualified_name}: help says {admitted}, callback refuses {refused}")
    assert not mismatches, "\n".join(mismatches)


@pytest.mark.usefixtures("patched_checks")
@pytest.mark.parametrize("profile", PROFILES)
async def test_profile_runs_its_commands(profile: str, started_bot: Any) -> None:
    """The callbacks admit the expected number of staff commands, and /help lists as many."""
    world = _world(PROFILES[profile])
    staff = [c for c in _ruled(started_bot) if c.qualified_name not in {"suggest", HELP_COMMAND, "aide"}]
    names = {c.qualified_name for c in staff}
    runnable = [c for c in staff if not await _refused(c, world)]
    texts = catalog().texts("en")
    embeds = build_help(_ruled(started_bot), world.caller, texts)
    listed = [
        line
        for embed in embeds
        for field in embed.fields
        if field.name != texts.get(f"help.domain.{EVERYONE_DOMAIN}")
        for line in (field.value or "").split("\n")
        if line.startswith("`/") and line.split("`")[1][1:] in names
    ]
    assert (len(runnable), len(listed)) == (PROFILES[profile].expected, PROFILES[profile].expected)


async def test_every_role_rule_is_the_set_the_callback_checks(started_bot: Any) -> None:
    """The roles a command's callback requires are the very set its help rule holds, not an equal copy."""
    world = _world(PROFILES["member"])
    commands = [c for c in _ruled(started_bot) if isinstance(rule_of(c), AnyRole)]
    assert commands
    mismatches: list[str] = []
    for command in commands:
        rule = rule_of(command)
        assert isinstance(rule, AnyRole)
        seen: list[Any] = []

        async def spy(_logger: Any, _name: str, _interaction: Any, roles: Any, **_kwargs: Any) -> bool:
            seen.append(roles)  # noqa: B023 - called before the next loop turn
            return False

        with patch.object(utils, "_check_has_role", spy):
            await _try(command, world, world.server_roles[0])
        if not seen or seen[0] is not rule.roles:
            mismatches.append(f"/{command.qualified_name}: " + ("no role check ran" if not seen else "another set"))
    assert not mismatches, "\n".join(mismatches)
