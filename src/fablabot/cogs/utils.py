"""Utility functions for cogs."""

from __future__ import annotations

from logging import Logger
from typing import Any

from discord import Forbidden, Guild, HTTPException, Interaction, Member, TextChannel
from discord.utils import get

from fablabot.guild_config import get_commands_channel_id, set_commands_channel_id

COMMANDS_CHANNEL_NAME = "commandes_bot"
ADMIN_ROLES = {
    "Admin -temp-",
    "Administrateur",
    "Président.e",
    "Vice-Président.e",
    "Secrétaire Général",
}


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
        maybe_channel: Any = interaction.guild.get_channel(stored_channel_id) or await interaction.guild.fetch_channel(
            stored_channel_id
        )
        if isinstance(maybe_channel, TextChannel):
            commands_channel = maybe_channel
        else:
            logger.warning(
                f"Stored commands channel id {stored_channel_id} is invalid for guild {interaction.guild.id}",
            )
            set_commands_channel_id(interaction.guild.id, None)

    if commands_channel is None:
        maybe_channel = get(interaction.guild.channels, name=COMMANDS_CHANNEL_NAME)
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
            f"Attempt to use command in a different channel than {COMMANDS_CHANNEL_NAME}: {interaction.channel.name}"
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
    guild_roles = {role.name: role for role in interaction.guild.roles}
    missing_roles = [role for role in roles if role not in guild_roles]
    if missing_roles:
        assert isinstance(interaction.channel, TextChannel)
        logger.warning(f"Missing roles {', '.join(missing_roles)} for guild {interaction.guild}")
        await interaction.channel.send(
            f"Les rôles suivants ne sont pas configurés sur ce serveur : {', '.join(missing_roles)}.",
        )

    assert isinstance(interaction.user, Member)
    member_role_names = {role.name for role in interaction.user.roles}
    if not bool(member_role_names & roles):
        logger.warning(
            f"User {interaction.user} doesn't have any of the roles {','.join(roles)} in the guild {interaction.guild.id}"
        )
        msg = f"Il est requis d'avoir au moins l'un des rôles suivants pour utiliser cette commande : {', '.join(roles)}."
        await interaction.response.send_message(
            msg,
            ephemeral=True,
        )
        return False
    return True


def escape_md(text: str) -> str:
    """Escape markdown characters in a string.

    Args:
        text (str): The text to escape.

    Returns:
        str: The escaped text.
    """
    return f"`{text}`"


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
    message_content: str,
    dm_type: str,
) -> bool:
    """Send a direct message to a guild member.

    Args:
        logger (Logger): The logger of the cog.
        guild (Guild): The guild where the user is located.
        member (Member): The member to notify.
        message_content (str): The message content to send.
        dm_type (str): The type of DM being sent (for logging purposes).

    Returns:
        bool: True if the message was sent successfully, False otherwise.
    """
    logger.debug(f"Preparing {dm_type} DM for guild {guild.id} user {member}.")

    if member.bot:
        return False

    if not await _can_dm_user(member):
        logger.error(f"Cannot DM user {member.name} ({member.display_name}) in guild {guild.id}; skipping {dm_type} DM.")
        return False

    logger.debug(f"Resolved member {member.id} ({member.display_name}) for {dm_type} DM in guild {guild.id}.")

    try:
        await member.send(message_content)
    except Exception:
        logger.exception(f"Failed to send {dm_type} DM to user {member} in guild {guild.id}.")
        return False
    else:
        logger.info(f"Sent {dm_type} DM to user {member} in guild {guild.id}.")
        return True
