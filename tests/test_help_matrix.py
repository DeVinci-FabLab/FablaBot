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
from fablabot.helpers.access import AccessRule, AnyRole, Caller, rule_of
from fablabot.helpers.constants import ADMIN_ROLES, ErrorMessages, RoleNames
from fablabot.helpers.help_page import DOMAINS, EVERYONE_DOMAIN, HELP_COMMAND, build_help, leaf_commands, line_key
from fablabot.helpers.i18n import catalog

if TYPE_CHECKING:
    from collections.abc import Iterator

    from discord import app_commands

SERVER_ROLES: list[str] = [value for key, value in vars(RoleNames).items() if key.isupper()]
F_ROLE = "F - Découpe laser"
NO_RIGHT = (
    ErrorMessages.ROLE_REQUIRED.split("{")[0],
    ErrorMessages.INSUFFICIENT_PERMISSIONS,
    ErrorMessages.NO_PERMISSION_ADD_ROLE,
    ErrorMessages.NO_PERMISSION_REMOVE_ROLE,
    ErrorMessages.NO_PERMISSION_MANAGE_MESSAGES,
    user_management._TEMP_ROLE_REFUSED,
    "Seuls les administrateurs peuvent utiliser cette commande",
    "Rôles administratifs manquants",
    ErrorMessages.ROLE_NOT_FOUND.split("{")[0],
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
    "trainer-with-F-roles": Profile((RoleNames.TRAININGS_MANAGER,), Permissions.none(), 13, f_roles=True),
    "temp-admin": Profile((RoleNames.ADMIN_TEMP,), Permissions.none(), 9),
    "codir": Profile((RoleNames.CODIR,), Permissions.none(), 9),
    "bureau": Profile((RoleNames.BUREAU,), Permissions.none(), 15),
    "digital-manager": Profile((RoleNames.DIGITAL_MANAGER,), Permissions.none(), 7),
    "admin-no-permission": Profile((RoleNames.ADMIN,), Permissions.none(), 35),
    "admin-bureau-administrator": Profile((RoleNames.BUREAU, RoleNames.ADMIN), Permissions.all(), 38),
    "manage-messages-only": Profile((), Permissions(manage_messages=True), 1),
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


def _world(profile: Profile, *, absent: tuple[str, ...] = ()) -> World:
    """Build the interaction, the server's roles (less those in ``absent``) and the help caller of a profile."""
    interaction = make_interaction()
    names = [name for name in (*SERVER_ROLES, *([F_ROLE] if profile.f_roles else [])) if name not in absent]
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


def _rule(command: app_commands.Command[Any, ..., Any]) -> AccessRule:
    """The rule of a command; a command without one fails the test that reads it."""
    rule = rule_of(command)
    assert rule is not None, f"/{command.qualified_name} has no access rule"
    return rule


def _ruled(bot: Any) -> list[app_commands.Command[Any, ..., Any]]:
    """Every command of the real tree, each once, each asserted to carry an access rule."""
    commands = {c.qualified_name: c for c in leaf_commands(bot.tree.walk_commands())}
    for command in commands.values():
        _rule(command)
    return list(commands.values())


def _command(bot: Any, name: str) -> app_commands.Command[Any, ..., Any]:
    """The command of the real tree with this qualified name."""
    return next(c for c in _ruled(bot) if c.qualified_name == name)


@pytest.mark.usefixtures("patched_checks")
@pytest.mark.parametrize("profile", PROFILES)
async def test_help_matches_every_callback(profile: str, started_bot: Any) -> None:
    """For every command with a rule, the rule admits the member exactly when the callback does not turn them away."""
    world = _world(PROFILES[profile])
    mismatches: list[str] = []
    for command in _ruled(started_bot):
        admitted = _rule(command).admits(world.caller)
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


async def test_every_command_has_a_rule_a_line_and_a_place(started_bot: Any) -> None:
    """Every command of the real tree has a rule, an English line and a place in DOMAINS, and DOMAINS lists no other."""
    reference = catalog().texts("en").reference
    tree = {c.qualified_name: c for c in leaf_commands(started_bot.tree.walk_commands())}
    placed = {name if domain == EVERYONE_DOMAIN else f"{domain} {name}" for domain, names in DOMAINS for name in names}
    lacking = {
        name: [
            what
            for what, missing in (
                ("a rule", rule_of(command) is None),
                ("an en line", line_key(name) not in reference),
                ("a place in DOMAINS", name not in placed),
            )
            if missing
        ]
        for name, command in tree.items()
    }
    problems = [f"/{name} lacks {', '.join(what)}" for name, what in lacking.items() if what]
    problems += [f"DOMAINS names /{name}, which the tree lacks" for name in sorted(placed - tree.keys())]
    assert not problems, "\n".join(problems)


@pytest.mark.usefixtures("patched_checks")
async def test_op_needs_both_roles_on_the_server(started_bot: Any) -> None:
    """An admin on a server without CoDir, or without the temporary role, is neither listed nor served for /user op."""
    command = _command(started_bot, "user op")
    full = _world(PROFILES["admin-no-permission"])
    assert _rule(command).admits(full.caller)
    assert not await _refused(command, full)
    for absent in (RoleNames.CODIR, RoleNames.ADMIN_TEMP):
        world = _world(PROFILES["admin-no-permission"], absent=(absent,))
        assert not _rule(command).admits(world.caller), absent
        assert await _refused(command, world), absent


@pytest.mark.usefixtures("patched_checks")
async def test_deop_needs_the_temporary_role_on_the_server(started_bot: Any) -> None:
    """/user deop is listed only on a server that has the temporary role, whether or not it has CoDir."""
    command = _command(started_bot, "user deop")
    for absent, listed in (((), True), ((RoleNames.CODIR,), True), ((RoleNames.ADMIN_TEMP,), False)):
        world = _world(PROFILES["admin-no-permission"], absent=absent)
        assert _rule(command).admits(world.caller) is listed, absent
        assert await _refused(command, world) is not listed, absent


@pytest.mark.usefixtures("patched_checks")
@pytest.mark.parametrize("name", ["user add_role", "user add_roles"])
async def test_role_commands_need_a_role_the_caller_may_give(name: str, started_bot: Any) -> None:
    """Bureau on a server with neither Sbire Bureau nor agent MyFab has no role to give: unlisted, and refused for all."""
    command = _command(started_bot, name)
    world = _world(PROFILES["bureau"], absent=(RoleNames.SBIRE_BUREAU, RoleNames.AGENTS_MYFAB))
    assert not _rule(command).admits(world.caller)
    assert await _refused(command, world)


def test_give_rule_judges_a_role_as_given_and_take_rule_as_taken() -> None:
    """With only the temporary role on offer, the give rule refuses it (/user op gives it) and the take rule admits."""
    # A real server also has @everyone, which an administrative role may give, so the two rules agree there.
    caller = Caller(roles=(RoleNames.PRESIDENT,), permissions=Permissions.none(), server_roles=(RoleNames.ADMIN_TEMP,))
    assert user_management._GIVE_ACCESS.admits(caller) is False
    assert user_management._TAKE_ACCESS.admits(caller) is True


@pytest.mark.usefixtures("patched_checks")
@pytest.mark.parametrize("name", ["user add_role", "user add_roles", "user remove_role", "user remove_roles"])
@pytest.mark.parametrize("absent", [RoleNames.SBIRE_BUREAU, RoleNames.AGENTS_MYFAB])
async def test_bureau_may_give_and_take_each_of_its_two_roles(name: str, absent: str, started_bot: Any) -> None:
    """Bureau on a server holding only one of Sbire Bureau and agent MyFab is still listed and served for that one."""
    command = _command(started_bot, name)
    world = _world(PROFILES["bureau"], absent=(absent,))
    assert _rule(command).admits(world.caller), absent
    assert not await _refused(command, world), absent


@pytest.mark.parametrize("holder", sorted(ADMIN_ROLES))
@pytest.mark.parametrize(
    "beside",
    [(), (RoleNames.MEMBER_VERIFIED,), (RoleNames.ADMIN_TEMP,), (*sorted(ADMIN_ROLES), RoleNames.BUREAU)],
    ids=["alone", "with-member-role", "with-temporary-role", "with-every-administrative-role"],
)
def test_no_administrative_role_may_give_or_take_the_admin_role(holder: str, beside: tuple[str, ...]) -> None:
    """The admin role is out of reach of every administrative role, alone or among other roles, whatever the action."""
    held = {holder, *beside}
    assert user_management.can_assign(held, RoleNames.ADMIN) is False
    for action in ("add", "remove"):
        assert user_management.role_refusal(held, RoleNames.ADMIN, action=action) is not None


def test_role_refusal_agrees_with_assignment_refusal() -> None:
    """For every role and every kind of holder, the rule's reasoning and the callback's give the same refusal."""
    roles = [*SERVER_ROLES, "F - x", RoleNames.DIGITAL_POLE]
    for profile in PROFILES.values():
        actor = MagicMock(spec=Member)
        actor.id = 1
        actor.roles = [_role(name) for name in profile.roles]
        names = user_management._role_names(actor)
        for name in roles:
            for action in ("add", "remove"):
                stub = _role(name)
                expected = user_management.UserManagement.assignment_refusal(actor, None, stub, action=action)
                assert user_management.role_refusal(names, name, action=action) == expected, (profile.roles, name, action)


def _member(member_id: int, *names: str) -> MagicMock:
    """Build a member stub with an id and the roles named."""
    member = MagicMock(spec=Member)
    member.id = member_id
    member.roles = [_role(name) for name in names]
    return member


@pytest.mark.parametrize(
    ("held", "role", "expected"),
    [
        (RoleNames.MEMBER_VERIFIED, RoleNames.BUREAU, utils.RefusalKind.MISSING_RIGHT),
        (RoleNames.MEMBER_VERIFIED, RoleNames.ADMIN_TEMP, utils.RefusalKind.MISSING_RIGHT),
        (RoleNames.PRESIDENT, RoleNames.ADMIN_TEMP, utils.RefusalKind.TEMP_ROLE_OUTSIDE_OP),
        (RoleNames.PRESIDENT, F_ROLE, utils.RefusalKind.SELF_TARGET),
    ],
    ids=["no-right", "no-right-temporary-role", "temporary-role", "role-the-caller-may-give"],
)
def test_giving_a_role_to_oneself_reports_the_first_refusal(held: str, role: str, expected: utils.RefusalKind) -> None:
    """The missing right is reported before the temporary role, and both before the self-target."""
    actor = _member(1, held)
    refusal = user_management.UserManagement.assignment_refusal(actor, actor, _role(role), action="add")
    assert refusal is expected


def test_the_temporary_role_is_given_by_op_only_but_taken_like_any_other() -> None:
    """Adding the temporary role is left to /user op; whoever may take a role may take it too."""
    actor, target, role = _member(1, RoleNames.PRESIDENT), _member(2), _role(RoleNames.ADMIN_TEMP)
    assignment_refusal = user_management.UserManagement.assignment_refusal
    assert assignment_refusal(actor, target, role, action="add") is utils.RefusalKind.TEMP_ROLE_OUTSIDE_OP
    assert assignment_refusal(actor, target, role, action="remove") is None
    assert user_management.role_refusal({RoleNames.PRESIDENT}, RoleNames.ADMIN_TEMP, action="remove") is None
