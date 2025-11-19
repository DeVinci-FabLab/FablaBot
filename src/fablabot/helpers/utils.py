"""Utility functions for cogs."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, overload

from discord import (
    CategoryChannel,
    Embed,
    Forbidden,
    Guild,
    HTTPException,
    Interaction,
    Member,
    PermissionOverwrite,
    Role,
    TextChannel,
    VoiceChannel,
)
from discord.utils import get
from emoji import EMOJI_DATA

from fablabot.guild_config import get_commands_channel_id, set_commands_channel_id
from fablabot.helpers.constants import ErrorMessages

if TYPE_CHECKING:
    from logging import Logger

_COMMANDS_CHANNEL_NAME = "commandes_bot"
_DISCORD_EMOJI_RE = re.compile(r"^<a?:\w+:\d+>$")


def log_request(logger: Logger, command_name: str, interaction: Interaction, **kwargs: Any) -> None:
    """Logs a request made to a command.

    Args:
        logger (Logger): The logger of the cog.
        command_name (str): The name of the command.
        interaction (Interaction): The Discord interaction context of the command invocation.
        **kwargs (Any): Additional details to log.
    """
    details = " ".join(f"{k}={v}" for k, v in kwargs.items())
    logger.info(f"[{command_name}]: user={interaction.user!s} id={interaction.user.id} {details}")


async def is_in_allowed_channel(logger: Logger, interaction: Interaction) -> bool:
    """Check if the interaction was made in the allowed commands channel.

    Args:
        logger (Logger): The logger of the cog.
        interaction (Interaction): The Discord interaction context of the command invocation.

    Returns:
        bool: ``True`` if the interaction was made in the allowed commands channel,
            ``False`` otherwise.
    """
    assert interaction.guild is not None
    if not isinstance(interaction.channel, TextChannel):
        logger.warning(f"Attempt to use command in a non-text channel: {interaction.channel} ({type(interaction.channel)})")
        await interaction.response.send_message(
            "Vous ne pouvez pas utiliser de commandes en dehors d'un salon textuel.",
            ephemeral=True,
        )
        return False

    stored_channel_id = get_commands_channel_id(interaction.guild.id)
    commands_channel: TextChannel | None = None

    if stored_channel_id is not None:
        try:
            maybe_channel: Any = interaction.guild.get_channel(stored_channel_id) or await interaction.guild.fetch_channel(
                stored_channel_id,
            )
        except Exception:
            logger.warning(
                f"Stored commands channel id {stored_channel_id} could not be fetched for guild {interaction.guild.id}",
            )
            set_commands_channel_id(interaction.guild.id, None)
        else:
            if isinstance(maybe_channel, TextChannel):
                commands_channel = maybe_channel
            elif maybe_channel is not None:
                logger.warning(
                    f"Stored commands channel id {stored_channel_id} is not a TextChannel for guild {interaction.guild.id}",
                )
                set_commands_channel_id(interaction.guild.id, None)

    if commands_channel is None:
        maybe_channel = get(interaction.guild.channels, name=_COMMANDS_CHANNEL_NAME)
        if isinstance(maybe_channel, TextChannel):
            commands_channel = maybe_channel
            set_commands_channel_id(interaction.guild.id, maybe_channel.id)

    if commands_channel is None:
        logger.warning(f"Commands channel not configured for guild {interaction.guild.id}")
        await interaction.response.send_message(
            "Le salon de commandes du bot n'est pas configuré sur ce serveur.",
            ephemeral=True,
        )
        return False

    if interaction.channel != commands_channel:
        logger.warning(
            f"Attempt to use command in a different channel than {_COMMANDS_CHANNEL_NAME}: {interaction.channel.name}",
        )
        await interaction.response.send_message(
            f"Vous ne pouvez pas utiliser de commandes en dehors du salon {commands_channel.mention}.",
            ephemeral=True,
        )
        return False
    return True


async def check_has_role(logger: Logger, interaction: Interaction, roles: set[str]) -> bool:
    """Check if the interaction user has any of the specified roles.

    Args:
        logger (Logger): The logger of the cog.
        interaction (Interaction): The Discord interaction context of the command invocation.
        roles (set[str]): The set of role names to check.

    Returns:
        bool: True if the user has any of the roles, False otherwise.
    """
    assert interaction.guild is not None
    guild_role_names = {role.name for role in interaction.guild.roles}
    missing_roles = roles - guild_role_names
    if missing_roles:
        assert isinstance(interaction.channel, TextChannel)
        logger.warning(f"Missing roles {', '.join(missing_roles)} for guild {interaction.guild}")
        await interaction.channel.send(ErrorMessages.ROLE_NOT_FOUND.format(role_name=", ".join(missing_roles)))

    assert isinstance(interaction.user, Member)
    member_role_names = {role.name for role in interaction.user.roles}
    if not member_role_names & roles:
        logger.warning(
            f"User {interaction.user} doesn't have any of the roles {', '.join(roles)} in the guild {interaction.guild.id}",
        )
        msg = f"Il est requis d'avoir au moins l'un des rôles suivants pour utiliser cette commande : {', '.join(roles)}."
        await interaction.response.send_message(
            msg,
            ephemeral=True,
        )
        return False
    return True


def is_valid_emoji(emoji: str) -> bool:
    """Check if an emoji is valid.

    Args:
        emoji (str): The emoji to check.

    Returns:
        bool: True if valid, False otherwise.
    """
    emoji_clean = emoji.strip()
    try:
        return emoji_clean is not None and (
            emoji_clean in EMOJI_DATA
            or _DISCORD_EMOJI_RE.match(emoji_clean) is not None
            or 0x1F1E6 <= ord(emoji_clean) <= 0x1F1FF
        )
    except Exception:
        return False


async def _can_dm_user(user: Member) -> bool:
    """Check if the bot can send a DM to the user.

    Args:
        user (Member): The user to check.

    Returns:
        bool: True if the bot can send a DM to the user, False otherwise.
    """
    try:
        await user.send()
    except Forbidden:
        return False
    except HTTPException:
        return True
    return True


async def send_dm_to_member(
    logger: Logger,
    guild: Guild,
    member: Member,
    message_content: str | None,
    *,
    embed: Embed | None = None,
    dm_type: str,
) -> bool:
    """Send a direct message to a guild member.

    Args:
        logger (Logger): The logger of the cog.
        guild (Guild): The guild where the user is located.
        member (Member): The member to notify.
        message_content (str | None): The message content to send.
        embed (Embed | None): The embed to send. Defaults to None.
        dm_type (str): The type of DM being sent (for logging purposes).

    Returns:
        bool: True if the message was sent successfully, False otherwise.
    """
    logger.debug(f"Preparing {dm_type} DM for guild {guild.id} user {member}.")

    if member.bot:
        return False

    if not await _can_dm_user(member):
        logger.error(f"Cannot DM user {member.id} ({member.name!r}) in guild {guild.id}; skipping {dm_type} DM.")
        return False

    logger.debug(f"Resolved member {member.id} ({member.display_name}) for {dm_type} DM in guild {guild.id}.")

    try:
        if embed is not None:
            await member.send(content=message_content, embed=embed)
        else:
            await member.send(content=message_content)
    except Exception:
        logger.exception(f"Failed to send {dm_type} DM to user {member} in guild {guild.id}.")
        return False

    logger.info(f"Sent {dm_type} DM to user {member} in guild {guild.id}.")
    return True


async def get_or_fetch_member(guild: Guild, member_id: int) -> Member | None:
    """Get a member from cache or fetch from API if not cached.

    Args:
        guild (Guild): The guild to search in.
        member_id (int): The ID of the member to retrieve.

    Returns:
        Member | None: The member if found, None otherwise.
    """
    member = guild.get_member(member_id)
    if member is not None:
        return member

    try:
        return await guild.fetch_member(member_id)
    except Exception:
        return None


@overload
def get_members_by_role(logger: Logger, guild: Guild, *, role: str) -> set[Member]: ...
@overload
def get_members_by_role(*, role: Role) -> set[Member]: ...


def get_members_by_role(
    logger: Logger | None = None,
    guild: Guild | None = None,
    *,
    role: Role | str,
) -> set[Member]:
    """Get all members in a guild that have a specific role.

    Args:
        logger (Logger | None): The logger of the cog. Defaults to None.
        guild (Guild | None): The guild to search in. Defaults to None.
        role (Role | str): The role to filter members by.

    Returns:
        set[Member]: Set of members that have the specified role.
    """
    if isinstance(role, Role):
        return set(role.members)

    assert logger is not None
    assert guild is not None

    role_obj = get(guild.roles, name=role)
    if role_obj is None:
        logger.error(f"Role '{role}' not found in guild {guild.id}; returning empty member set.")
        return set()
    return set(role_obj.members)


# region ====== Formatting Helpers ======


def escape_md(text: str) -> str:
    """Escape markdown characters in a string.

    Args:
        text (str): The text to escape.

    Returns:
        str: The escaped text.
    """
    return f"`{text}`"


def format_member_mention(member: Member) -> str:
    """Format a member mention with escaped name.

    Args:
        member (Member): The member to format.

    Returns:
        str: Formatted string like "{mention} (`name`)".
    """
    return f"{member.mention} ({escape_md(member.name)})"


def format_role_mention(role: Role) -> str:
    """Format a role mention with escaped name.

    Args:
        role (Role): The role to format.

    Returns:
        str: Formatted string like "{mention} (`name`)".
    """
    return f"{role.mention} ({escape_md(role.name)})"


def format_channel_mention(channel: TextChannel | VoiceChannel) -> str:
    """Format a channel mention with escaped name.

    Args:
        channel (TextChannel | VoiceChannel): The channel to format.

    Returns:
        str: Formatted string like "{mention} (`name`)".
    """
    return f"{channel.mention} ({escape_md(channel.name)})"


# endregion Formatting Helpers


# region ====== Safe Discord Operations (with error handling) ======


async def safe_add_roles(
    logger: Logger,
    member: Member,
    *roles: Role,
    reason: str | None = None,
) -> tuple[bool, str | None]:
    """Safely add roles to a member with error handling.

    Args:
        logger (Logger): The logger to use for error messages.
        member (Member): The member to add roles to.
        *roles (Role): The roles to add.
        reason (str | None, optional): The reason for adding roles. Defaults to None.

    Returns:
        tuple[bool, str | None]: (Success status, Error message if failed).
    """
    try:
        await member.add_roles(*roles, reason=reason)
    except Forbidden:
        logger.exception(f"Forbidden to add roles {roles} to {member}")
        return False, ErrorMessages.ROLE_ADD_FAILED
    except HTTPException:
        logger.exception(f"HTTP error while adding roles {roles} to {member}")
        return False, ErrorMessages.HTTP_ERROR.format(
            operation=f"l'ajout des rôles {', '.join(role.name for role in roles)} à {format_member_mention(member)}",
        )
    return True, None


async def safe_remove_roles(
    logger: Logger,
    member: Member,
    *roles: Role,
    reason: str | None = None,
) -> tuple[bool, str | None]:
    """Safely remove roles from a member with error handling.

    Args:
        logger (Logger): The logger to use for error messages.
        member (Member): The member to remove roles from.
        *roles (Role): The roles to remove.
        reason (str | None, optional): The reason for removing roles. Defaults to None.

    Returns:
        tuple[bool, str | None]: (Success status, Error message if failed).
    """
    try:
        await member.remove_roles(*roles, reason=reason)
    except Forbidden:
        logger.exception(f"Forbidden to remove roles {roles} from {member}")
        return False, ErrorMessages.ROLE_REMOVE_FAILED
    except HTTPException:
        logger.exception(f"HTTP error while removing roles {roles} from {member}")
        return False, ErrorMessages.HTTP_ERROR.format(
            operation=f"le retrait des rôles {', '.join(role.name for role in roles)} à {format_member_mention(member)}",
        )
    return True, None


async def safe_create_text_channel(
    logger: Logger,
    category: CategoryChannel,
    name: str,
    overwrites: dict[Any, PermissionOverwrite] | None = None,
    reason: str | None = None,
    **options: Any,
) -> tuple[TextChannel | None, str | None]:
    """Safely create a text channel with error handling.

    Args:
        logger (Logger): The logger to use for error messages.
        category (CategoryChannel): The category to create the channel in.
        name (str): The name of the channel.
        overwrites (dict[Any, PermissionOverwrite] | None, optional): Permission overwrites. Defaults to None.
        reason (str | None, optional): The reason for creation. Defaults to None.
        **options (Any): Additional channel options.

    Returns:
        tuple[TextChannel | None, str | None]: (Created channel or None, Error message if failed).
    """
    try:
        channel = await category.create_text_channel(
            name=name,
            overwrites=overwrites or {},
            reason=reason,
            **options,
        )
    except HTTPException:
        logger.exception(f"HTTP error while creating text channel {name!r} in category {category}")
        return None, ErrorMessages.CHANNEL_CREATE_FAILED.format(channel_type=f"salon textuel {name!r}")
    return channel, None


async def safe_create_voice_channel(
    logger: Logger,
    category: CategoryChannel,
    name: str,
    overwrites: dict[Any, PermissionOverwrite] | None = None,
    reason: str | None = None,
    **options: Any,
) -> tuple[VoiceChannel | None, str | None]:
    """Safely create a voice channel with error handling.

    Args:
        logger (Logger): The logger to use for error messages.
        category (CategoryChannel): The category to create the channel in.
        name (str): The name of the channel.
        overwrites (dict[Any, PermissionOverwrite] | None, optional): Permission overwrites. Defaults to None.
        reason (str | None, optional): The reason for creation. Defaults to None.
        **options (Any): Additional channel options.

    Returns:
        tuple[VoiceChannel | None, str | None]: (Created channel or None, Error message if failed).
    """
    try:
        channel = await category.create_voice_channel(
            name=name,
            overwrites=overwrites or {},
            reason=reason,
            **options,
        )
    except HTTPException:
        logger.exception(f"HTTP error while creating voice channel {name!r} in category {category}")
        return None, ErrorMessages.CHANNEL_CREATE_FAILED.format(channel_type=f"salon vocal {name!r}")
    return channel, None


async def safe_delete_channel(
    logger: Logger,
    channel: TextChannel | VoiceChannel,
    reason: str | None = None,
) -> tuple[bool, str | None]:
    """Safely delete a channel with error handling.

    Args:
        logger (Logger): The logger to use for error messages.
        channel (TextChannel | VoiceChannel): The channel to delete.
        reason (str | None, optional): The reason for deletion. Defaults to None.

    Returns:
        tuple[bool, str | None]: (Success status, Error message if failed).
    """
    channel_type = "salon textuel" if isinstance(channel, TextChannel) else "salon vocal"
    try:
        await channel.delete(reason=reason)
    except HTTPException:
        logger.exception(f"HTTP error while deleting channel {channel}")
        return False, ErrorMessages.CHANNEL_DELETE_FAILED.format(channel_type=channel_type)
    return True, None


async def safe_edit_channel(
    logger: Logger,
    channel: TextChannel | VoiceChannel,
    reason: str | None = None,
    **options: Any,
) -> tuple[bool, str | None]:
    """Safely edit a channel with error handling.

    Args:
        logger (Logger): The logger to use for error messages.
        channel (TextChannel | VoiceChannel): The channel to edit.
        reason (str | None, optional): The reason for editing. Defaults to None.
        **options (Any): The channel attributes to edit.

    Returns:
        tuple[bool, str | None]: (Success status, Error message if failed).
    """
    channel_type = "salon textuel" if isinstance(channel, TextChannel) else "salon vocal"
    try:
        await channel.edit(reason=reason, **options)
    except HTTPException:
        logger.exception(f"HTTP error while editing channel {channel}")
        return False, ErrorMessages.CHANNEL_EDIT_FAILED.format(channel_type=channel_type)
    return True, None


# endregion Safe Discord Operations
