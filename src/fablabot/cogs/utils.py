"""Utility functions for cogs."""

from __future__ import annotations

from logging import Logger
from typing import Any

from discord import Interaction, Member, TextChannel
from discord.utils import get

COMMANDS_CHANNEL_NAME = "commandes_bot"


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

    commands_channel = get(interaction.guild.channels, name=COMMANDS_CHANNEL_NAME)
    if not isinstance(commands_channel, TextChannel):
        logger.warning(f"Commands channel {COMMANDS_CHANNEL_NAME} not found in guild {interaction.guild.id}")
        await interaction.response.send_message(
            f"Le salon {COMMANDS_CHANNEL_NAME} n'est pas configuré sur ce serveur.",
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
        logger.warning(f"Missing roles {missing_roles} for guild {interaction.guild}")
        await interaction.response.send_message(
            f"Les rôles suivants ne sont pas configurés sur ce serveur : {', '.join(missing_roles)}.",
            ephemeral=True,
        )

    assert isinstance(interaction.user, Member)
    member_role_names = {role.name for role in interaction.user.roles}
    if not bool(member_role_names & roles):
        logger.warning(f"User {interaction.user} doesn't have any of the roles {roles} in the guild {interaction.guild.id}")
        msg = f"Il est requis d'avoir au moins l'un des rôles suivants pour utiliser cette commande : {', '.join(roles)}."
        if interaction.response.is_done():
            await interaction.followup.send(
                msg,
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                msg,
                ephemeral=True,
            )
        return False
    return True
