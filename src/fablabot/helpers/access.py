"""Who may run a command, stated once on the command and read back by the help listing."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Final, Protocol, Self

from discord import Permissions

if TYPE_CHECKING:
    from discord import Guild, Member, app_commands


@dataclass(frozen=True)
class Caller:
    """What the help knows about the member asking.

    Attributes:
        roles (tuple[str, ...]): The member's role names, highest first.
        permissions (Permissions): The member's permissions on the server.
        server_roles (tuple[str, ...]): The role names that exist on the server.
    """

    roles: tuple[str, ...]
    permissions: Permissions
    server_roles: tuple[str, ...]

    @classmethod
    def of(cls, member: Member, guild: Guild) -> Self:
        """Describe a member on a server.

        Args:
            member (Member): The member asking.
            guild (Guild): The server.

        Returns:
            Caller: The member's roles, permissions and the server's roles.
        """
        return cls(
            roles=tuple(role.name for role in reversed(member.roles)),
            permissions=member.guild_permissions,
            server_roles=tuple(role.name for role in guild.roles),
        )

    def only(self, role: str) -> Caller:
        """Describe the same member holding one role and no permission.

        Args:
            role (str): The role name.

        Returns:
            Caller: The copy.
        """
        return replace(self, roles=(role,), permissions=Permissions.none())


class AccessRule(Protocol):
    """A rule that tells whether a caller may run a command."""

    def admits(self, caller: Caller) -> bool:
        """Tell whether ``caller`` may run the command."""
        ...


@dataclass(frozen=True)
class Everyone:
    """The rule of a command open to every member."""

    def admits(self, caller: Caller) -> bool:  # noqa: ARG002 - the rule ignores the caller by design
        """Admit everyone.

        Args:
            caller (Caller): The member asking.

        Returns:
            bool: Always True.
        """
        return True


EVERYONE: Final = Everyone()


def access(rule: AccessRule) -> dict[str, Any]:
    """Wrap a rule for a command decorator's ``extras=``.

    Args:
        rule (AccessRule): The command's one access rule.

    Returns:
        dict[str, Any]: The extras to attach.
    """
    return {"access": rule}


def rule_of(command: app_commands.Command[Any, ..., Any]) -> AccessRule | None:
    """Read back the rule attached to a command.

    Args:
        command (app_commands.Command[Any, ..., Any]): The command.

    Returns:
        AccessRule | None: The rule, None when the command has none (it is then never listed).
    """
    rule = command.extras.get("access")
    return rule if callable(getattr(rule, "admits", None)) else None
