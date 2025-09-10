"""Utility functions for cogs."""

from __future__ import annotations

from logging import Logger
from typing import Any

from discord import Guild, Interaction, TextChannel
from discord.utils import get

COMMANDS_CHANNEL_NAME = "commandes_bot"


def log_request(logger: Logger, command_name: str, interaction: Interaction, **kwargs: Any) -> None:
    """Logs a request made to a command.

    Args:
        logger (Logger): The logger of the cog.
        command_name (str): The name of the command.
        interaction (Interaction): The interaction object representing the command invocation.
        **kwargs (Any): Additional details to log.
    """
    details = " ".join(f"{k}={v}" for k, v in kwargs.items())
    logger.info(f"[{command_name}]: user={interaction.user!s} id={interaction.user.id} {details}")


async def is_in_allowed_channel(logger: Logger, interaction: Interaction) -> bool:
    """Check if the interaction was made in the allowed commands channel.

    Args:
        logger (Logger): The logger of the cog.
        interaction (Interaction): The interaction object representing the command invocation.

    Returns:
        bool: ``True`` if the interaction was made in the allowed commands channel,
            ``False`` otherwise.
    """
    assert isinstance(interaction.guild, Guild)
    assert isinstance(interaction.channel, TextChannel)
    commands_channel = get(interaction.guild.channels, name=COMMANDS_CHANNEL_NAME)
    assert isinstance(commands_channel, TextChannel)
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
