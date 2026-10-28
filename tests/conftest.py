"""Shared fixtures and Discord stubs for the test suite."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from discord import Guild, Member, Role, TextChannel, ui
import pytest


@pytest.fixture(autouse=True)
def _isolated_cwd(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    """Run every test from an empty temporary directory so nothing lands in ``data/`` or ``logs/``."""
    monkeypatch.chdir(tmp_path)


def make_interaction(
    *,
    user_id: int = 418003123456789012,
    user_name: str = "kevin_secret_name",
    display_name: str = "Kevin Secret",
    guild_id: int = 1234,
) -> MagicMock:
    """Build a stub interaction whose user and guild look like Discord's.

    Args:
        user_id (int): The ID of the invoking user.
        user_name (str): The account name of the invoking user, also its ``str()``.
        display_name (str): The display name of the invoking user.
        guild_id (int): The ID of the guild.

    Returns:
        MagicMock: The stub interaction.
    """
    interaction = MagicMock()
    user = MagicMock(spec=Member)
    user.id = user_id
    user.name = user_name
    user.display_name = display_name
    user.roles = []
    user.__str__.return_value = user_name
    interaction.user = user

    guild = MagicMock(spec=Guild)
    guild.id = guild_id
    guild.roles = []
    guild.text_channels = []
    interaction.guild = guild
    interaction.guild_id = guild_id

    interaction.response.send_message = AsyncMock()
    interaction.response.send_modal = AsyncMock()
    interaction.response.edit_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


def fill_text_input(item: ui.TextInput, value: str) -> None:
    """Set the submitted value of a text input, as Discord would on submit.

    Args:
        item (ui.TextInput): The text input to fill.
        value (str): The value the user typed.
    """
    item._value = value  # noqa: SLF001


def cog_bot() -> MagicMock:
    """Build a stub standing in for the bot or cog handed to views.

    Returns:
        MagicMock: The stub.
    """
    return MagicMock()


def make_role(name: str, *, role_id: int = 0) -> MagicMock:
    """Build a stub role with no holder yet.

    Args:
        name (str): The role name.
        role_id (int): The role ID.

    Returns:
        MagicMock: The stub role; ``members`` grows as ``make_member`` hands it out.
    """
    role = MagicMock(spec=Role)
    role.name = name
    role.id = role_id
    role.members = []
    return role


def make_member(member_id: int, *roles: MagicMock) -> MagicMock:
    """Build a stub member holding ``roles``, with awaitable role edits.

    Args:
        member_id (int): The member ID.
        *roles (MagicMock): Roles from ``make_role``; the member is added to each one's ``members``.

    Returns:
        MagicMock: The stub member.
    """
    member = MagicMock(spec=Member)
    member.id = member_id
    member.name = f"member_{member_id}"
    member.display_name = f"Member {member_id}"
    member.mention = f"<@{member_id}>"
    member.bot = False
    member.roles = list(roles)
    member.add_roles = AsyncMock()
    member.remove_roles = AsyncMock()
    for role in roles:
        role.members.append(member)
    return member


def make_guild(guild_id: int, roles: list[MagicMock], members: list[MagicMock], *, channel: MagicMock | None = None) -> MagicMock:
    """Build a stub guild that finds its members by ID and returns ``channel`` for any channel ID.

    Args:
        guild_id (int): The guild ID.
        roles (list[MagicMock]): The guild's roles.
        members (list[MagicMock]): The guild's members.
        channel (MagicMock | None): The channel ``get_channel`` returns; a fresh ``TextChannel`` stub when None.

    Returns:
        MagicMock: The stub guild.
    """
    guild = MagicMock(spec=Guild)
    guild.id = guild_id
    guild.unavailable = False
    guild.roles = roles
    guild.members = members
    guild.get_member = lambda member_id: next((m for m in members if m.id == member_id), None)
    if channel is None:
        channel = MagicMock(spec=TextChannel)
        channel.send = AsyncMock()
    guild.get_channel = MagicMock(return_value=channel)
    return guild
