"""Single-target /user commands: no grant through the temporary role, no self-grant, refusals logged by kind."""

from __future__ import annotations

from datetime import UTC, datetime
import json
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from conftest import make_guild, make_interaction, make_member, make_role
import pytest

from fablabot.cogs import user_management
from fablabot.cogs.user_management import UserManagement
from fablabot.helpers.constants import RoleNames

NOW = datetime(2026, 10, 28, 15, 0, tzinfo=UTC)
GUILD_ID = 1001
CALLER_ID, TARGET_ID = 418003123456789012, 501
CALLER_NAME = "member_418003123456789012"
STATE_FILE = Path("data/temp_admin_state.json")


@pytest.fixture(autouse=True)
def _fixed_clock_and_context():
    with (
        patch.object(user_management, "_utcnow", return_value=NOW),
        patch.object(user_management, "ensure_command_context", AsyncMock(return_value=True)),
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
    """One guild with every role the tests need, a caller holding ``caller_roles`` and a target."""

    def __init__(self, *caller_roles: str) -> None:
        """Build a server whose caller holds the given roles."""
        self.roles = {
            name: make_role(name, role_id=i)
            for i, name in enumerate(
                (
                    RoleNames.ADMIN_TEMP,
                    RoleNames.CODIR,
                    RoleNames.PRESIDENT,
                    RoleNames.TRAININGS_MANAGER,
                    RoleNames.DIGITAL_MANAGER,
                    "F - Laser",
                    "Pôle Numérique",
                ),
            )
        }
        self.caller = make_member(CALLER_ID, *(self.roles[n] for n in caller_roles))
        self.target = make_member(TARGET_ID)
        self.guild = make_guild(GUILD_ID, list(self.roles.values()), [self.caller, self.target])

    def interaction(self) -> MagicMock:
        """Return an interaction made by the caller in this guild."""
        interaction = make_interaction(guild_id=GUILD_ID)
        interaction.guild = self.guild
        interaction.user = self.caller
        interaction.channel_id = 777
        return interaction


def _refusal(interaction: MagicMock) -> str:
    interaction.response.send_message.assert_awaited_once()
    args = interaction.response.send_message.await_args
    assert args.kwargs["ephemeral"] is True
    return args.args[0]


def _refusal_lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if "refused" in r.getMessage()]


async def _op(cog: UserManagement, server: Server, member: MagicMock, minutes: int = 30) -> MagicMock:
    interaction = server.interaction()
    await UserManagement.user_op.callback(cog, interaction, member=member, reason="atelier", time=minutes)
    return interaction


# -- Temporary role alone gives nothing here --


@pytest.mark.parametrize(
    "command",
    ["add_role", "remove_role", "op", "deop"],
)
async def test_temp_admin_only_cannot_grant_op_or_deop(
    cog: UserManagement, caplog: pytest.LogCaptureFixture, command: str
) -> None:
    """A caller with only the temporary role cannot grant op or deop."""
    caplog.set_level(logging.DEBUG)
    server = Server(RoleNames.ADMIN_TEMP)
    interaction = server.interaction()
    target = server.target

    if command == "add_role":
        await UserManagement.user_add_role.callback(cog, interaction, member=target, role=server.roles[RoleNames.PRESIDENT])
    elif command == "remove_role":
        await UserManagement.user_remove_role.callback(cog, interaction, member=target, role=server.roles["F - Laser"])
    elif command == "op":
        await UserManagement.user_op.callback(cog, interaction, member=target, reason="x", time=90)
    else:
        await UserManagement.user_deop.callback(cog, interaction, member=target)

    _refusal(interaction)
    target.add_roles.assert_not_awaited()
    target.remove_roles.assert_not_awaited()
    assert not STATE_FILE.exists()
    assert cog.deop_tasks == {}
    assert any("kind=missing_right" in line for line in _refusal_lines(caplog))


async def test_temp_admin_only_deop_leaves_expiry_untouched(cog: UserManagement) -> None:
    """A refused deop from a temporary admin leaves the expiry untouched."""
    server = Server(RoleNames.PRESIDENT)
    await _op(cog, server, server.target)
    before = STATE_FILE.read_text(encoding="utf-8")
    other = Server(RoleNames.ADMIN_TEMP)
    other.guild = server.guild
    interaction = other.interaction()

    await UserManagement.user_deop.callback(cog, interaction, member=server.target)

    _refusal(interaction)
    assert STATE_FILE.read_text(encoding="utf-8") == before
    assert (GUILD_ID, TARGET_ID) in cog.deop_tasks


# -- The temporary role is given by op only --


async def test_add_role_refuses_temp_role_and_points_to_op(cog: UserManagement, caplog: pytest.LogCaptureFixture) -> None:
    """Adding the temporary role is refused and the reply points to op."""
    caplog.set_level(logging.DEBUG)
    server = Server(RoleNames.PRESIDENT)
    interaction = server.interaction()

    await UserManagement.user_add_role.callback(cog, interaction, member=server.target, role=server.roles[RoleNames.ADMIN_TEMP])

    assert "/user op" in _refusal(interaction)
    server.target.add_roles.assert_not_awaited()
    assert any("kind=temp_role_outside_op" in line for line in _refusal_lines(caplog))


# -- No self-grant --


async def test_add_role_to_self_is_refused(cog: UserManagement, caplog: pytest.LogCaptureFixture) -> None:
    """Adding a role to oneself is refused."""
    caplog.set_level(logging.DEBUG)
    server = Server(RoleNames.PRESIDENT)
    interaction = server.interaction()

    await UserManagement.user_add_role.callback(cog, interaction, member=server.caller, role=server.roles["F - Laser"])

    assert "vous-même" in _refusal(interaction)
    server.caller.add_roles.assert_not_awaited()
    assert any("kind=self_target" in line for line in _refusal_lines(caplog))


async def test_op_self_is_refused_even_for_digital_manager(cog: UserManagement) -> None:
    """Opping oneself is refused even for a digital manager."""
    server = Server(RoleNames.DIGITAL_MANAGER)
    interaction = await _op(cog, server, server.caller)

    assert "vous-même" in _refusal(interaction)
    server.caller.add_roles.assert_not_awaited()
    assert not STATE_FILE.exists()


async def test_remove_role_from_self_is_allowed(cog: UserManagement) -> None:
    """Removing a role from oneself is allowed."""
    server = Server(RoleNames.PRESIDENT, "F - Laser")
    interaction = server.interaction()

    await UserManagement.user_remove_role.callback(cog, interaction, member=server.caller, role=server.roles["F - Laser"])

    server.caller.remove_roles.assert_awaited_once()


async def test_deop_self_is_allowed(cog: UserManagement) -> None:
    """Deopping oneself is allowed."""
    server = Server(RoleNames.PRESIDENT, RoleNames.ADMIN_TEMP)
    interaction = server.interaction()

    await UserManagement.user_deop.callback(cog, interaction, member=server.caller)

    server.caller.remove_roles.assert_awaited_once()


# -- Other roles keep their rights while opped --


async def test_opped_trainings_manager_still_gives_trainer_roles(cog: UserManagement) -> None:
    """An opped trainings manager can still give trainer roles."""
    server = Server(RoleNames.ADMIN_TEMP, RoleNames.TRAININGS_MANAGER)
    interaction = server.interaction()

    await UserManagement.user_add_role.callback(cog, interaction, member=server.target, role=server.roles["F - Laser"])

    server.target.add_roles.assert_awaited_once()


# -- Refusal lines --


async def test_refusal_lines_carry_ids_but_no_name(cog: UserManagement, caplog: pytest.LogCaptureFixture) -> None:
    """Refusal log lines carry ids but no names."""
    caplog.set_level(logging.DEBUG)
    server = Server(RoleNames.ADMIN_TEMP)

    await UserManagement.user_add_role.callback(cog, server.interaction(), member=server.target, role=server.roles["F - Laser"])

    lines = _refusal_lines(caplog)
    assert lines == [f"[user.add_role] refused kind=missing_right guild_id={GUILD_ID} user_id={CALLER_ID}"]
    assert all(CALLER_NAME not in r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING)


# -- Help --


async def test_help_explains_op_scope_and_self_rule(cog: UserManagement) -> None:
    """The help text explains the op scope and the self rule."""
    interaction = make_interaction()

    await UserManagement.user_help.callback(cog, interaction)

    text = interaction.response.send_message.await_args.args[0]
    assert "durée limitée" in text
    assert "/fm" in text
    assert "soi-même" in text


# -- A malformed guild entry in the op state does not block op --


@pytest.mark.parametrize("entry", [[], None, "x"], ids=["list", "null", "string"])
async def test_op_works_after_start_on_a_malformed_guild_entry(entry: object) -> None:
    """Op works after a start on a malformed guild entry."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps({str(GUILD_ID): entry}), encoding="utf-8")
    server = Server(RoleNames.PRESIDENT)
    bot = MagicMock()
    bot.guilds = [server.guild]
    cog = UserManagement(bot)
    await cog.on_ready()

    interaction = await _op(cog, server, server.target)

    server.target.add_roles.assert_awaited_once()
    assert str(TARGET_ID) in json.loads(STATE_FILE.read_text(encoding="utf-8"))[str(GUILD_ID)]
    assert (GUILD_ID, TARGET_ID) in cog.deop_tasks
    interaction.response.send_message.assert_awaited_once()
    for task in cog.deop_tasks.values():
        task.cancel()
