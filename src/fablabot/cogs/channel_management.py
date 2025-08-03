"""Channel management commands for Discord Bot. Provides slash commands for text and dynamic voice channel management."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from typing import Any
from warnings import deprecated

from discord import (
    CategoryChannel,
    DMChannel,
    ForumChannel,
    GroupChannel,
    Interaction,
    Member,
    TextChannel,
    VoiceChannel,
    VoiceState,
    app_commands,
)
from discord.ext import commands

logger = logging.getLogger(__name__)

DYNAMIC_SUFFIX = "-vocal"
INDEX_SEPARATOR = "/"
EPHEMERAL_SUFFIX = "-temp"


def log_request(command_name: str, interaction: Interaction, **kwargs: Any) -> None:
    """Logs a request made to a command.

    Args:
        command_name (str): The name of the command.
        interaction (Interaction): The interaction object representing the command invocation.
        **kwargs (Any): Additional details to log.
    """
    details = " ".join(f"{k}={v}" for k, v in kwargs.items())
    logger.info(f"[{command_name}]: user={interaction.user!s} id={interaction.user.id} {details}")


class TextChannelManagementGroup(app_commands.Group, name="text", description="Gestion des salons textuels"):
    """Manages text channel-related commands.

    Args:
        app_commands (app_commands.Group): The app_commands group.
        name (str, optional): The name of the group. Defaults to "text".
        description (str, optional): The description of the group. Defaults to "Gestion des salons textuels".
    """

    @app_commands.command(name="clear", description="Nettoie le salon actuel de ses derniers messages.")
    @app_commands.describe(messages="Le nombre de messages à supprimer (par défaut 5)")
    async def clear(self, interaction: Interaction, messages: int = 5) -> None:
        """Clears the current channel of its last messages.

        Args:
            interaction (Interaction): The interaction that triggered the command.
            messages (int, optional): The number of messages to purge. Defaults to 5.
        """
        log_request("text.clear", interaction, messages=messages)
        assert not isinstance(interaction.channel, ForumChannel | CategoryChannel | DMChannel | GroupChannel | None)
        if not interaction.permissions.manage_messages:
            logger.warning(f"Insufficient permissions for manage_messages: {interaction.user}")
            await interaction.response.send_message(
                "Vous n'avez pas la permission de gérer les messages dans ce salon.", ephemeral=True
            )
            return
        if interaction.channel.name == "commandes_bot":
            logger.warning("Attempt to clear commandes_bot channel")
            await interaction.response.send_message(
                "Vous ne pouvez pas nettoyer le salon commandes_bot. Veuillez contacter le pôle numérique si nécessaire.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message("Nettoyage en cours...", ephemeral=True)
        deleted = await interaction.channel.purge(limit=messages)
        logger.info(f"Deleted {len(deleted)} messages in channel {interaction.channel.name}")
        await interaction.followup.send(f"{len(deleted)} messages supprimés avec succès !", ephemeral=True)

    @app_commands.command(name="create", description="Crée un nouveau salon dans la catégorie spécifiée.")
    @app_commands.describe(
        channel="Le nom du salon à créer",
        category="La catégorie dans laquelle créer le salon",
    )
    async def create(self, interaction: Interaction, channel: str, category: CategoryChannel) -> None:
        """Create a text channel in the passed category.

        Args:
            interaction (Interaction): The interaction object.
            channel (str): The name of the channel to create.
            category (CategoryChannel): The category to create the channel in.
        """
        log_request("text.create", interaction, channel=channel, category=category.name)
        assert isinstance(interaction.user, Member)
        if not category.permissions_for(interaction.user).manage_channels:
            logger.warning(f"Insufficient permissions for manage_channels: {interaction.user}")
            await interaction.response.send_message(
                "Vous n'avez pas la permission de créer des salons dans cette catégorie.", ephemeral=True
            )
            return
        if channel in (c.name for c in category.channels):
            logger.info(f"Text channel {channel!r} already exists in {category!r}")
            await interaction.response.send_message(f"Un salon {channel!r} existe déjà dans {category.name!r}.", ephemeral=True)
            return
        new_channel = await category.create_text_channel(channel)
        logger.info(f"Created text channel {new_channel!r} in category {category!r}")
        await interaction.response.send_message(f"Le salon {new_channel.mention} a été créé dans {category.name!r}.")

    @app_commands.command(name="rename", description="Renomme un salon textuel.")
    @app_commands.describe(channel="Salon à renommer", new_name="Nouveau nom du salon")
    async def rename(self, interaction: Interaction, channel: TextChannel, new_name: str) -> None:
        """Rename a text channel.

        Args:
            interaction (Interaction): The interaction object.
            channel (TextChannel): The channel to rename.
            new_name (str): The new name of the channel.
        """
        log_request("text.rename", interaction, channel=channel.name, new_name=new_name)
        assert isinstance(interaction.user, Member)
        if not channel.permissions_for(interaction.user).manage_channels:
            logger.warning(f"Insufficient permissions for rename: {interaction.user}")
            await interaction.response.send_message(
                f"Vous n'avez pas la permission de renommer le salon {channel.mention}.", ephemeral=True
            )
            return
        old_name = channel.name
        await channel.edit(name=new_name)
        logger.info(f"Renamed channel {channel} from {old_name!r} to {new_name!r}")
        await interaction.response.send_message(
            f"Le salon {channel.mention}, anciennement {old_name!r}, a été renommé en {new_name!r}."
        )

    @app_commands.command(name="delete", description="Supprime un salon textuel.")
    @app_commands.describe(channel="Le salon à supprimer")
    async def delete(self, interaction: Interaction, channel: TextChannel) -> None:
        """Delete a text channel.

        Args:
            interaction (Interaction): The interaction object.
            channel (TextChannel): The channel to delete.
        """
        log_request("text.delete", interaction, channel=channel.name)
        assert isinstance(interaction.user, Member)
        if not channel.permissions_for(interaction.user).manage_channels:
            logger.warning(f"Insufficient permissions for delete: {interaction.user}")
            await interaction.response.send_message(
                f"Vous n'avez pas la permission de supprimer le salon {channel.mention}.", ephemeral=True
            )
            return
        await channel.delete()
        logger.info(f"Deleted text channel {channel.name!r}")
        await interaction.response.send_message(f"Le salon {channel.name!r} a été supprimé.")


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
        category="Catégorie dans laquelle créer le salon vocal",
        is_temporary="Salon temporaire (supprimé après inactivité)",
        max_user="Nombre max d'utilisateurs (None pour illimité)",
    )
    async def create(
        self,
        interaction: Interaction,
        name: str,
        category: CategoryChannel,
        is_temporary: bool = True,
        max_user: int | None = None,
    ) -> None:
        """Create a custom voice channel in the passed category.

        Args:
            interaction (Interaction): The Discord interaction.
            name (str): The name of the voice channel to create.
            category (CategoryChannel): The category in which to create the voice channel.
            is_temporary (bool, optional): Whether the channel is temporary. Defaults to True.
            max_user (int | None, optional): The maximum number of users allowed in the channel. Defaults to None.
        """
        log_request(
            "vocal.create", interaction, name=name, category=category.name, is_temporary=is_temporary, max_user=max_user
        )
        assert isinstance(interaction.user, Member)
        if not category.permissions_for(interaction.user).manage_channels:
            logger.warning(f"Insufficient permissions for create voice channel: {interaction.user}")
            await interaction.response.send_message("Vous n'avez pas la permission de créer des salons vocaux.", ephemeral=True)
            return
        existing = {vc.name for vc in category.voice_channels}
        channel_name = f"{name}{EPHEMERAL_SUFFIX}" if is_temporary else name
        if channel_name in existing:
            logger.info(f"Voice channel {channel_name!r} already exists in {category!r}")
            await interaction.response.send_message(
                f"Un salon vocal {name!r} existe déjà dans {category.name!r}.", ephemeral=True
            )
            return
        new_channel = await category.create_voice_channel(channel_name, user_limit=max_user)
        logger.info(f"Created voice channel {new_channel!r} in category {category.name!r}")
        channel_creation_message = f"Salon vocal {'temporaire' if is_temporary else 'permanent'} créé: {new_channel.mention}"
        if max_user:
            channel_creation_message += f" (max {max_user} utilisateurs)"
        await interaction.response.send_message(channel_creation_message)

    @app_commands.command(name="rename", description="Renomme un salon vocal.")
    @app_commands.describe(channel="Le salon vocal à renommer", new_name="Nouveau nom du salon")
    async def rename(self, interaction: Interaction, channel: VoiceChannel, new_name: str) -> None:
        """Rename a voice channel.

        Args:
            interaction (Interaction): The Discord interaction.
            channel (VoiceChannel): The voice channel to rename.
            new_name (str): The new name of the voice channel.
        """
        log_request("vocal.rename", interaction, channel=channel.name, new_name=new_name)
        assert isinstance(interaction.user, Member)
        assert isinstance(channel.category, CategoryChannel)
        if not channel.permissions_for(interaction.user).manage_channels:
            logger.warning(f"Insufficient permissions for rename voice channel: {interaction.user}")
            await interaction.response.send_message(
                f"Vous n'avez pas la permission de renommer le salon vocal {channel.mention}.", ephemeral=True
            )
            return
        if re.search(rf"{DYNAMIC_SUFFIX}{INDEX_SEPARATOR}\d+$", channel.name):
            logger.warning(f"Attempt to rename a dynamic voice channel: {channel.name}")
            await interaction.response.send_message(
                f"Le salon vocal {channel.mention} est un salon dynamique et ne peut pas être renommé.", ephemeral=True
            )
            return
        old_name = channel.name
        if old_name.endswith(EPHEMERAL_SUFFIX) and not new_name.endswith(EPHEMERAL_SUFFIX):
            new_name += EPHEMERAL_SUFFIX
        if old_name.endswith(DYNAMIC_SUFFIX) and not new_name.endswith(DYNAMIC_SUFFIX):
            new_name += DYNAMIC_SUFFIX
        await channel.edit(name=new_name)
        logger.info(f"Renamed voice channel {channel} from {old_name!r} to {new_name!r}")
        for vc in channel.category.voice_channels:
            if vc.name.startswith(f"{old_name}{INDEX_SEPARATOR}"):
                suffix = vc.name[len(old_name) :]
                new_vc_name = f"{new_name}{suffix}"
                await vc.edit(name=new_vc_name)
                logger.info(f"Renamed associated dynamic channel {vc} from {old_name + suffix!r} to {new_vc_name!r}")
        await interaction.response.send_message(
            f"Le salon vocal {channel.mention}, anciennement {old_name!r}, a été renommé en {new_name!r}."
        )

    @app_commands.command(name="delete", description="Supprime un salon vocal.")
    @app_commands.describe(channel="Le salon vocal à supprimer")
    async def delete(self, interaction: Interaction, channel: VoiceChannel) -> None:
        """Delete a voice channel.

        Args:
            interaction (Interaction): The Discord interaction.
            channel (VoiceChannel): The voice channel to delete.
        """
        log_request("vocal.delete", interaction, channel=channel.name)
        assert isinstance(interaction.user, Member)
        if not channel.permissions_for(interaction.user).manage_channels:
            logger.warning(f"Insufficient permissions for delete voice channel: {interaction.user}")
            await interaction.response.send_message(
                f"Vous n'avez pas la permission de supprimer le salon vocal {channel.mention}.", ephemeral=True
            )
            return
        if len(channel.members) > 0:
            logger.warning(f"Attempt to delete non-empty voice channel: {channel}")
            await interaction.response.send_message(f"Le salon vocal {channel.mention} n'est pas vide.", ephemeral=True)
            return
        await channel.delete()
        logger.info(f"Deleted voice channel {channel.name!r}")
        await interaction.response.send_message(f"Le salon vocal {channel.name!r} a été supprimé.")


class ChannelManagement(commands.Cog):
    """Cog to register text and voice channel management commands and listeners."""

    def __init__(self, bot: commands.Bot) -> None:
        """Initialize the cog and register its command groups.

        Args:
            bot (commands.Bot): The bot instance.
        """
        self.bot = bot
        self.remove_tasks: dict[int, asyncio.Task[None]] = {}
        self.bot.tree.add_command(TextChannelManagementGroup())
        self.bot.tree.add_command(VocalChannelManagementGroup())

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: Member, before: VoiceState, after: VoiceState) -> None:
        """Dynamically create and remove voice channels in the 'Association' category.

        Args:
            member (Member): The member whose voice state changed.
            before (VoiceState): The voice state before the change.
            after (VoiceState): The voice state after the change.
        """
        logger.debug(f"voice_state_update: user={member} id={member.id} before={before.channel} after={after.channel}")
        if isinstance(after.channel, VoiceChannel):
            task = self.remove_tasks.pop(after.channel.id, None)
            if task:
                task.cancel()
                logger.info(f"Cancelled remove task for channel {after.channel.name}")

        for category in member.guild.categories:
            for voice_channel in category.voice_channels:
                if voice_channel.name.endswith(DYNAMIC_SUFFIX):
                    await self._manage_voice_channels(category, voice_channel)
                elif (
                    voice_channel.name.endswith(EPHEMERAL_SUFFIX)
                    and not voice_channel.members
                    and voice_channel.id not in self.remove_tasks
                ):
                    self.remove_tasks[voice_channel.id] = asyncio.create_task(self._delayed_delete(voice_channel, 60))

    async def _manage_voice_channels(self, category: CategoryChannel, base_channel: VoiceChannel) -> None:
        """Manage voice channels in a category.

        Args:
            category (CategoryChannel): The category to manage channels in.
            base_channel (VoiceChannel): The base channel to manage.
        """
        logger.debug(f"_manage_voice_channels: category={category.name} base={base_channel.name}")
        channels = [vc for vc in category.voice_channels if vc.name.startswith(base_channel.name)]
        empty = [vc for vc in channels if not vc.members]
        empty.sort(key=lambda c: c.name)
        while len(empty) > 1:
            vc = empty.pop()
            if vc.id not in self.remove_tasks:
                self.remove_tasks[vc.id] = asyncio.create_task(self._delayed_delete(vc, 10))
        if not empty:
            for idx in range(1, len(channels) + 1):
                if f"{base_channel.name}{INDEX_SEPARATOR}{idx}" not in (c.name for c in channels):
                    new_name = f"{base_channel.name}{INDEX_SEPARATOR}{idx}"
                    await category.create_voice_channel(
                        new_name,
                        bitrate=base_channel.bitrate,
                        user_limit=base_channel.user_limit,
                        rtc_region=base_channel.rtc_region,
                        video_quality_mode=base_channel.video_quality_mode,
                        overwrites=base_channel.overwrites,
                    )
                    logger.info(f"Created additional voice channel {new_name!r}")
                    break

    async def _delayed_delete(self, channel: VoiceChannel, timeout: int) -> None:
        """Wait a specified amount of time then delete the channel if still empty.

        Args:
            channel (VoiceChannel): The channel to delete.
            timeout (int): The time to wait before deleting the channel in seconds.
        """
        logger.info(f"_delayed_delete: channel={channel} timeout={timeout}")
        await asyncio.sleep(timeout)
        if not channel.members:
            with contextlib.suppress(Exception):
                await channel.delete()
                logger.info(f"Deleted empty voice channel {channel.name!r} after timeout")


@deprecated("Load the cog using `bot.add_cog()` instead.")
async def setup(bot: commands.Bot) -> None:
    """Setup function to load the ChannelManagement cog.

    Args:
        bot (commands.Bot): The bot instance.
    """
    await bot.add_cog(ChannelManagement(bot))
