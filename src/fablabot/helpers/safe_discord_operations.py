"""Utility functions for cogs."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from discord import (
    CategoryChannel,
    Forbidden,
    HTTPException,
    Member,
    PermissionOverwrite,
    Role,
    TextChannel,
    VoiceChannel,
)

from fablabot.helpers.constants import ErrorMessages
from fablabot.helpers.utils import format_member_mention

if TYPE_CHECKING:
    from logging import Logger


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
