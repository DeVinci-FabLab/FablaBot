"""Channel management commands."""

from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime
import logging
from warnings import deprecated

import discord
from discord import CategoryChannel, Guild, app_commands
from discord.ext import commands
from discord.utils import get

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


class TextChannelManagementGroup(app_commands.Group, name="text", description="Gestion des salons textuels"):
    """Manages text channel-related commands.

    Args:
        app_commands (app_commands.Group): The app_commands group.
        name (str, optional): The name of the group. Defaults to "text".
        description (str, optional): The description of the group. Defaults to "Gestion des salons textuels".
    """

    @app_commands.command(name="clear", description="Nettoie le salon actuel de ses derniers messages.")
    @app_commands.describe(messages="Le nombre de messages à supprimer (par défaut 5)")
    async def clear(self, interaction: discord.Interaction, messages: int = 5) -> None:
        """Clears the current channel of its last messages.

        Args:
            interaction (discord.Interaction): The interaction that triggered the command.
            messages (int, optional): The number of messages to purge. Defaults to 5.
        """
        logger.info("[text.clear] %s clear %s:%s", CURRENT_TIME, interaction.user.name, interaction.user.id)
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
                    await interaction.channel.purge(limit=messages)
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
        logger.info(
            "[text.create] %s create %s:%s %s %s", CURRENT_TIME, interaction.user.name, interaction.user.id, channel, category
        )
        assert interaction.guild is not None  # Satisfies type checker
        assert isinstance(interaction.user, discord.Member)  # Satisfies type checker
        if channel not in [channel.name for channel in category.channels]:
            new_channel = await interaction.guild.create_text_channel(channel, category=category)
            await new_channel.set_permissions(interaction.user, overwrite=overwrite[0])

            await interaction.response.send_message(f"Le salon {channel!r} a été créé dans {category.name} !")
        else:
            await interaction.response.send_message(f"Un salon {channel!r} existe déjà dans {category.name} !")


class VocalChannelManagementGroup(app_commands.Group, name="vocal", description="Gestion des salons vocaux dynamiques"):
    """Manages voice channel-related commands.

    Args:
        app_commands (app_commands.Group): The app_commands group.
        name (str, optional): The name of the group. Defaults to "vocal".
        description (str, optional): The description of the group. Defaults to "Gestion des salons vocaux dynamiques".
    """

    @app_commands.command(name="create", description="Crée un salon vocal personnalisé.")
    @app_commands.describe(
        name="Nom du salon vocal à créer",
        max_user="Nombre max d'utilisateurs (défaut 25)",
    )
    async def create(self, interaction: discord.Interaction, name: str, max_user: int = 25) -> None:
        """Create a custom voice channel in the specified category.

        Args:
            interaction (discord.Interaction): The Discord interaction.
            name (str): The name of the voice channel to create.
            max_user (int, optional): Maximum number of users in the channel. Defaults to 25.
        """
        logger.info(
            "[vocal.create] %s create %s:%s %s max_user=%d",
            CURRENT_TIME,
            interaction.user.name,
            interaction.user.id,
            name,
            max_user,
        )
        guild = interaction.guild
        assert isinstance(guild, Guild)
        category = get(guild.categories, name="Salons vocaux")
        assert isinstance(category, CategoryChannel)
        if name not in [channel.name for channel in category.voice_channels]:
            await guild.create_voice_channel(f"{name}-temp", category=category, user_limit=max_user)
            await interaction.response.send_message(
                f"Le salon vocal {name!r} a été créé dans {category.name} ! (max {max_user} utilisateurs)."
            )
        else:
            await interaction.response.send_message(f"Un salon {name!r} existe déjà dans {category.name} !")

    @app_commands.command(name="rename", description="Renomme un salon vocal.")
    @app_commands.describe(old_name="Nom actuel du salon", new_name="Nouveau nom du salon")
    async def rename(self, interaction: discord.Interaction, old_name: str, new_name: str) -> None:
        """Rename a voice channel.

        Args:
            interaction (discord.Interaction): The Discord interaction.
            old_name (str): The current name of the voice channel.
            new_name (str): The new name of the voice channel.
        """
        logger.info(
            "[vocal.rename] %s rename %s:%s %s -> %s",
            CURRENT_TIME,
            interaction.user.name,
            interaction.user.id,
            old_name,
            new_name,
        )
        guild = interaction.guild
        assert isinstance(guild, Guild)
        channel = get(guild.voice_channels, name=old_name)
        if not channel:
            channel = get(guild.voice_channels, name=f"{old_name}-temp")
        if not channel:
            await interaction.response.send_message(
                f"Aucun salon vocal nommé '{old_name}' ou '{old_name}-temp' trouvé.", ephemeral=True
            )
            return
        if not new_name.endswith("-temp"):
            new_name += "-temp"
        await channel.edit(name=new_name)
        await interaction.response.send_message(f"Salon vocal renommé en '{new_name}'.")


class ChannelManagement(commands.Cog):
    """Manages text and vocal channels."""

    def __init__(self, bot: commands.Bot) -> None:
        """Initialize the cog and register its command groups.

        Args:
            bot: The bot instance.
        """
        self.bot = bot
        self.remove_tasks: dict[int, asyncio.Task[None]] = {}
        for group in (TextChannelManagementGroup(), VocalChannelManagementGroup()):
            self.bot.tree.add_command(group)

    @commands.Cog.listener()
    async def on_voice_state_update(
        self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState
    ) -> None:
        """Dynamically create and remove voice channels in the 'Association' category.

        Args:
            member (discord.Member): The member whose voice state changed.
            before (discord.VoiceState): The voice state before the change.
            after (discord.VoiceState): The voice state after the change.
        """
        logger.debug(
            "[vocal.voice_state_update] %s %s:%s before=%s after=%s",
            CURRENT_TIME,
            member.name,
            member.id,
            before.channel,
            after.channel,
        )
        guild = member.guild

        new_channel = after.channel
        if isinstance(new_channel, discord.VoiceChannel):
            task = self.remove_tasks.pop(new_channel.id, None)
            if task:
                task.cancel()

        categories = [category for category in guild.categories if len(category.voice_channels) > 0]

        for category in categories:
            for voice_channel in category.voice_channels:
                if voice_channel.name.endswith("-vocal"):
                    await self._manage_voice_channels(guild, category, voice_channel.name)
                elif (
                    voice_channel.name.endswith("-temp")
                    and len(voice_channel.members) == 0
                    and voice_channel.id not in self.remove_tasks
                ):
                    self.remove_tasks[voice_channel.id] = asyncio.create_task(self._delayed_delete(voice_channel, timeout=60))

    async def _manage_voice_channels(self, guild: Guild, category: CategoryChannel, base_name: str) -> None:
        """Manage voice channels in a category.

        Args:
            guild (Guild): The guild where the channels are managed.
            category (CategoryChannel): The category to manage channels in.
            base_name (str): The base name for the channels.
        """
        logger.debug(
            "[vocal._manage_voice_channels] %s guild=%s category=%s base_name=%s",
            CURRENT_TIME,
            guild.name,
            category.name,
            base_name,
        )
        voice_channels = [
            voice_channel for voice_channel in category.voice_channels if voice_channel.name.startswith(base_name)
        ]
        empty_channels = [voice_channel for voice_channel in voice_channels if len(voice_channel.members) == 0]
        empty_channels.sort(key=lambda c: c.name)
        while len(empty_channels) > 1:
            channel_to_delete = empty_channels[-1]
            if channel_to_delete.id not in self.remove_tasks:
                self.remove_tasks[channel_to_delete.id] = asyncio.create_task(
                    self._delayed_delete(channel_to_delete, timeout=10)
                )
            del empty_channels[-1]

        if len(empty_channels) == 0:
            for n in range(1, len(category.voice_channels) + 1):
                if get(category.voice_channels, name=f"{base_name}/{n}") is None:
                    await guild.create_voice_channel(f"{base_name}/{n}", category=category)

    async def _delayed_delete(self, channel: discord.VoiceChannel, timeout: int) -> None:
        """Wait a specified amount of time then delete the channel if still empty.

        Args:
            channel (discord.VoiceChannel): The channel to delete.
            timeout (int): The time to wait before deleting the channel in seconds.
        """
        logger.info("[vocal._delayed_delete] %s channel=%s timeout=%d", CURRENT_TIME, channel.name, timeout)
        await asyncio.sleep(timeout)
        if len(channel.members) == 0:
            with contextlib.suppress(Exception):
                await channel.delete()


@deprecated("Load the cog using `bot.add_cog()` instead.")
async def setup(bot: commands.Bot) -> None:
    """Sets up the channel management cog.

    Args:
        bot (commands.Bot): The bot instance.
    """
    await bot.add_cog(ChannelManagement(bot))
