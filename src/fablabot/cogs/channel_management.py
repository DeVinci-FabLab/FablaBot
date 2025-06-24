"""Channel management commands."""

from __future__ import annotations

from datetime import datetime
import logging
from warnings import deprecated

import discord
from discord import app_commands
from discord.ext import commands

logger = logging.getLogger(__name__)

CURRENT_TIME = datetime.now().strftime("%Y/%m/%d, %H:%M:%S")

# define the different permissions [admin, invited, read only , blacklist]
# TODO: replace with named tuple
permissions = {
    "send_messages": [True, True, False, False],
    "read_messages": [True, True, True, False],
    "manage_messages": [True, False, False, False],
    "manage_channels": [True, False, False, False],
    "manage_roles": [True, False, False, False],
    "manage_permissions": [True, False, False, False],
    "manage_emojis": [True, True, False, False],
    "mention_everyone": [True, False, False, False],
    "create_private_threads": [True, True, False, False],
    "create_public_threads": [True, True, False, False],
    "read_message_history": [True, True, True, False],
    "add_reactions": [True, True, True, False],
    "attach_files": [True, True, False, False],
}
overwrite = [discord.PermissionOverwrite() for _ in range(4)]

for perm_name in permissions:
    for level in range(len(overwrite)):
        overwrite[level].update(**{perm_name: permissions[perm_name][level]})
overwrite += [None]


class ChannelManagementGroup(app_commands.Group, name="channel", description="Gestion des salons"):
    """Manages channel-related commands.

    Args:
        app_commands (app_commands.Group): The app_commands group.
        name (str, optional): The name of the group. Defaults to "channel".
        description (str, optional): The description of the group. Defaults to "Gestion des salons".
    """

    @app_commands.command(name="clear", description="Nettoie le salon actuel de ses derniers 100 messages.")
    async def clear(self, interaction: discord.Interaction) -> None:
        """Clears the current channel of its last 100 messages.

        Args:
            interaction: The interaction that triggered the command.
        """
        logger.info("%s clear %s:%s", CURRENT_TIME, interaction.user.name, interaction.user.id)
        if isinstance(
            interaction.channel,
            discord.TextChannel | discord.channel.VocalGuildChannel | discord.Thread,
        ):
            assert isinstance(interaction.user, discord.Member)
            for role in interaction.user.roles:
                if interaction.channel.permissions_for(interaction.user).manage_messages or role.permissions.manage_messages:
                    await interaction.response.send_message(
                        "Nettoyage du salon en cours, veuillez patienter...",
                        ephemeral=True,
                    )
                    await interaction.channel.purge(limit=100)
                    await interaction.followup.send("Le salon a été nettoyé avec succès !", ephemeral=True)
                    return
            await interaction.response.send_message("Vous n'avez pas la permission de gérer les messages dans ce salon.")

    @app_commands.command(name="create", description="Crée un nouveau salon dans la catégorie spécifiée.")
    @app_commands.describe(
        channel="Le nom du salon à créer",
        category="La catégorie dans laquelle créer le salon",
    )
    async def create(
        self,
        interaction: discord.Interaction,
        channel: str,
        category: discord.CategoryChannel,
    ) -> None:
        """Create a new channel in the passed category.

        Args:
            interaction (discord.Interaction): The interaction object.
            channel (str): The name of the channel to create.
            category (discord.CategoryChannel): The category to create the channel in.
        """
        assert interaction.guild is not None  # Satisfies type checker
        assert isinstance(interaction.user, discord.Member)  # Satisfies type checker
        logger.info(
            "%s create %s:%s %s %s",
            CURRENT_TIME,
            interaction.user.name,
            interaction.user.id,
            channel,
            category,
        )
        if channel not in [channel.name for channel in category.channels]:
            new_channel = await interaction.guild.create_text_channel(channel, category=category)
            await new_channel.set_permissions(interaction.user, overwrite=overwrite[0])

            await interaction.response.send_message(f"Le salon {channel!r} a été créé dans {category.name} !")
        else:
            await interaction.response.send_message(f"Un salon {channel!r} existe déjà dans {category.name} !")


class ChannelManagement(commands.Cog):
    """Manages channel."""

    def __init__(self, bot: commands.Bot) -> None:
        """Initialize the cog and register its command groups.

        Args:
            bot: The bot instance.
        """
        self.bot = bot
        for group in (ChannelManagementGroup(),):
            self.bot.tree.add_command(group)


@deprecated("Load the cog using `bot.add_cog()` instead.")
async def setup(bot: commands.Bot) -> None:
    """Sets up the channel management cog.

    Args:
        bot (commands.Bot): The bot instance.
    """
    await bot.add_cog(ChannelManagement(bot))
