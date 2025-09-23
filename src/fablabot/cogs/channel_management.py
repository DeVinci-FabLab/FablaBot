"""Channel management commands for Discord Bot. Provides slash commands for text and dynamic voice channel management."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from warnings import deprecated

from discord import (
    AuditLogAction,
    CategoryChannel,
    DMChannel,
    ForumChannel,
    GroupChannel,
    Interaction,
    Member,
    Message,
    RawMessageDeleteEvent,
    TextChannel,
    VoiceChannel,
    VoiceState,
    app_commands,
)
from discord.ext import commands
from discord.utils import get

from .utils import is_in_allowed_channel, log_request

logger = logging.getLogger(__name__)


DYNAMIC_SUFFIX = "-vocal"
INDEX_SEPARATOR = "/"
EPHEMERAL_SUFFIX = "-temp"


class ChannelManagement(commands.Cog):
    """Cog to register text and voice channel management commands and listeners.

    Commands:
    - /text help: Display help for text channel management commands.
    - /text clear: Clear the current text channel of its last messages.
    - /text create: Create a new text channel in the specified category.
    - /text rename: Rename an existing text channel.
    - /text delete: Delete a text channel.
    - /vocal help: Display help for voice channel management commands.
    - /vocal create: Create a new voice channel in the specified category.
    - /vocal rename: Rename an existing voice channel.
    - /vocal delete: Delete a voice channel.

    Listeners:
    - on_message_delete: Notify when a message is deleted in a bot channel, log the deleter and resend the content.
    - on_raw_message_delete: Notify when a message is deleted in a bot channel (for uncached messages).
    - on_voice_state_update: Create and remove dynamic voice channels in categories with a base channel named *-vocal.
    """

    def __init__(self, bot: commands.Bot) -> None:
        """Initialize the cog and register its command groups.

        Args:
            bot (commands.Bot): The bot instance.
        """
        self.bot = bot
        self.remove_tasks: dict[int, asyncio.Task[None]] = {}

    # region ====== Text Slash Group ======
    text_group = app_commands.Group(name="text", description="Gestion des salons textuels")

    @text_group.command(name="help", description="Affiche l'aide pour les commandes de gestion des salons textuels.")
    async def text_help(self, interaction: Interaction) -> None:
        """Display help for text channel management commands.

        Args:
            interaction (Interaction): The interaction that triggered the command.
        """
        help_message = (
            "**Commandes de gestion des salons textuels :**\n"
            "- `/text clear [messages]`: Nettoie le salon actuel de ses derniers messages. Par défaut, 5 messages sont supprimés.\n"
            "- `/text create <channel> <category>`: Crée un nouveau salon textuel dans la catégorie spécifiée.\n"
            "- `/text rename <channel> <new_name>`: Renomme un salon textuel existant.\n"
            "- `/text delete <channel>`: Supprime un salon textuel existant.\n"
            "- `/text help`: Affiche cette aide pour les commandes de gestion des salons textuels.\n"
            "\n"
            "Assurez-vous d'avoir les permissions nécessaires pour utiliser ces commandes."
        )
        await interaction.response.send_message(help_message, ephemeral=True)

    @text_group.command(name="clear", description="Nettoie le salon actuel de ses derniers messages.")
    @app_commands.describe(messages="Le nombre de messages à supprimer (par défaut 5)")
    async def text_clear(self, interaction: Interaction, messages: int = 5) -> None:
        """Clears the current channel of its last messages.

        Args:
            interaction (Interaction): The interaction that triggered the command.
            messages (int, optional): The number of messages to purge. Defaults to 5.
        """
        log_request(logger, "text.clear", interaction, messages=messages)
        assert not isinstance(
            interaction.channel,
            ForumChannel | CategoryChannel | DMChannel | GroupChannel | None,
        )
        if not interaction.permissions.manage_messages:
            logger.warning(f"Insufficient permissions for manage_messages: {interaction.user}")
            await interaction.response.send_message(
                "Vous n'avez pas la permission de gérer les messages dans ce salon.",
                ephemeral=True,
            )
            return
        if interaction.channel.name.endswith("_bot"):
            logger.warning(f"Attempt to clear {interaction.channel.name} channel")
            await interaction.response.send_message(
                f"Vous ne pouvez pas nettoyer le salon {interaction.channel.mention}."
                f" Veuillez contacter le pôle numérique si nécessaire.",
                ephemeral=True,
            )
            return
        if messages < 1 or messages > 50:
            logger.warning(f"Attempt to clear an invalid number of messages: {messages}")
            await interaction.response.send_message(
                "Vous ne pouvez pas supprimer moins de 1 message ou plus de 50 messages.", ephemeral=True
            )
            return
        await interaction.response.send_message("Nettoyage en cours...", ephemeral=True)
        deleted = await interaction.channel.purge(limit=messages, reason=f"With clear command by {interaction.user}")
        logger.info(f"Deleted {len(deleted)} messages in channel {interaction.channel.name}")
        await interaction.followup.send(f"{len(deleted)} messages supprimés avec succès !", ephemeral=True)

    @text_group.command(name="create", description="Crée un nouveau salon dans la catégorie spécifiée.")
    @app_commands.describe(
        channel="Le nom du salon à créer",
        category="La catégorie dans laquelle créer le salon",
    )
    async def text_create(self, interaction: Interaction, channel: str, category: CategoryChannel) -> None:
        """Create a text channel in the passed category.

        Args:
            interaction (Interaction): The interaction object.
            channel (str): The name of the channel to create.
            category (CategoryChannel): The category to create the channel in.
        """
        log_request(logger, "text.create", interaction, channel=channel, category=category.name)
        if not await is_in_allowed_channel(logger, interaction):
            return

        assert isinstance(interaction.user, Member)
        if not category.permissions_for(interaction.user).manage_channels:
            logger.warning(f"Insufficient permissions for manage_channels: {interaction.user}")
            await interaction.response.send_message(
                "Vous n'avez pas la permission de créer des salons dans cette catégorie.",
                ephemeral=True,
            )
            return
        if channel in (c.name for c in category.channels):
            logger.info(f"Text channel {channel!r} already exists in {category!r}")
            await interaction.response.send_message(
                f"Un salon {channel!r} existe déjà dans {category.mention}.",
                ephemeral=True,
            )
            return
        new_channel = await category.create_text_channel(channel, reason=f"With create command by {interaction.user}")
        logger.info(f"Created text channel {new_channel!r} in category {category!r}")
        await interaction.response.send_message(
            f"Le salon textuel {new_channel.mention}({new_channel.name!r}) a été créé dans {category.mention}({category.name!r})."
        )

    @text_group.command(name="rename", description="Renomme un salon textuel.")
    @app_commands.describe(channel="Salon à renommer", new_name="Nouveau nom du salon")
    async def text_rename(self, interaction: Interaction, channel: TextChannel, new_name: str) -> None:
        """Rename a text channel.

        Args:
            interaction (Interaction): The interaction object.
            channel (TextChannel): The channel to rename.
            new_name (str): The new name of the channel.
        """
        log_request(logger, "text.rename", interaction, channel=channel.name, new_name=new_name)
        if not await is_in_allowed_channel(logger, interaction):
            return

        assert isinstance(interaction.user, Member)
        if not channel.permissions_for(interaction.user).manage_channels:
            logger.warning(f"Insufficient permissions for rename: {interaction.user}")
            await interaction.response.send_message(
                f"Vous n'avez pas la permission de renommer le salon {channel.mention}.",
                ephemeral=True,
            )
            return
        old_name = channel.name
        await channel.edit(name=new_name, reason=f"With rename command by {interaction.user}")
        logger.info(f"Renamed channel {channel} from {old_name!r} to {new_name!r}")
        await interaction.response.send_message(
            f"Le salon textuel {channel.mention}, anciennement {old_name!r}, a été renommé en {new_name!r}."
        )

    @text_group.command(name="delete", description="Supprime un salon textuel.")
    @app_commands.describe(channel="Le salon à supprimer")
    async def text_delete(self, interaction: Interaction, channel: TextChannel) -> None:
        """Delete a text channel.

        Args:
            interaction (Interaction): The interaction object.
            channel (TextChannel): The channel to delete.
        """
        log_request(logger, "text.delete", interaction, channel=channel.name)
        if not await is_in_allowed_channel(logger, interaction):
            return

        assert isinstance(interaction.user, Member)
        if not channel.permissions_for(interaction.user).manage_channels:
            logger.warning(f"Insufficient permissions for delete: {interaction.user}")
            await interaction.response.send_message(
                f"Vous n'avez pas la permission de supprimer le salon {channel.mention}.",
                ephemeral=True,
            )
            return
        await channel.delete(reason=f"With delete command by {interaction.user}")
        logger.info(f"Deleted text channel {channel.name!r}")
        await interaction.response.send_message(f"Le salon textuel {channel.name!r} a été supprimé.")

    # endregion Text Slash Group

    # region ====== Vocal Slash Group ======
    vocal_group = app_commands.Group(name="vocal", description="Gestion des salons vocaux dynamiques")

    @vocal_group.command(name="help", description="Affiche l'aide pour les commandes de gestion des salons vocaux.")
    async def vocal_help(self, interaction: Interaction) -> None:
        """Display help for vocal channel management commands.

        Args:
            interaction (Interaction): The interaction that triggered the command.
        """
        help_message = (
            "**Commandes de gestion des salons vocaux :**\n"
            "- `/vocal create <name> <category> [is_temporary] [max_user]`: Crée un nouveau salon vocal dans la catégorie spécifiée. Par défaut, le salon est temporaire et illimité.\n"
            "- `/vocal rename <channel> <new_name>`: Renomme un salon vocal existant.\n"
            "- `/vocal delete <channel>`: Supprime un salon vocal existant.\n"
            "- `/vocal help`: Affiche cette aide pour les commandes de gestion des salons vocaux.\n"
            "\n"
            "Assurez-vous d'avoir les permissions nécessaires pour utiliser ces commandes."
        )
        await interaction.response.send_message(help_message, ephemeral=True)

    @vocal_group.command(name="create", description="Crée un salon vocal personnalisé.")
    @app_commands.describe(
        name="Nom du salon vocal à créer",
        category="Catégorie dans laquelle créer le salon vocal",
        is_temporary="Salon temporaire (supprimé après inactivité)",
        max_user="Nombre max d'utilisateurs (None pour illimité)",
    )
    async def vocal_create(
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
            logger,
            "vocal.create",
            interaction,
            name=name,
            category=category.name,
            is_temporary=is_temporary,
            max_user=max_user,
        )
        if not await is_in_allowed_channel(logger, interaction):
            return

        assert isinstance(interaction.user, Member)
        if not category.permissions_for(interaction.user).manage_channels:
            logger.warning(f"Insufficient permissions for create voice channel: {interaction.user}")
            await interaction.response.send_message(
                "Vous n'avez pas la permission de créer des salons vocaux.",
                ephemeral=True,
            )
            return
        existing = {vc.name for vc in category.voice_channels}
        channel_name = f"{name}{EPHEMERAL_SUFFIX}" if is_temporary else name
        if channel_name in existing:
            logger.info(f"Voice channel {channel_name!r} already exists in {category!r}")
            await interaction.response.send_message(
                f"Un salon vocal {name!r} existe déjà dans {category.mention}.",
                ephemeral=True,
            )
            return
        new_channel = await category.create_voice_channel(
            channel_name,
            user_limit=max_user,
            reason=f"With create command by {interaction.user}",
        )
        logger.info(f"Created voice channel {new_channel!r} in category {category.name!r}")
        channel_creation_message = (
            f"Le salon vocal {'temporaire' if is_temporary else 'permanent'} {new_channel.mention}({new_channel.name!r}) "
            f"a été créé dans {category.mention}({category.name!r})."
        )
        if max_user:
            channel_creation_message += f" (max {max_user} utilisateurs)"
        await interaction.response.send_message(channel_creation_message)

    @vocal_group.command(name="rename", description="Renomme un salon vocal.")
    @app_commands.describe(channel="Le salon vocal à renommer", new_name="Nouveau nom du salon")
    async def vocal_rename(self, interaction: Interaction, channel: VoiceChannel, new_name: str) -> None:
        """Rename a voice channel.

        Args:
            interaction (Interaction): The Discord interaction.
            channel (VoiceChannel): The voice channel to rename.
            new_name (str): The new name of the voice channel.
        """
        log_request(logger, "vocal.rename", interaction, channel=channel.name, new_name=new_name)
        if not await is_in_allowed_channel(logger, interaction):
            return

        assert isinstance(interaction.user, Member)
        assert channel.category is not None
        if not channel.permissions_for(interaction.user).manage_channels:
            logger.warning(f"Insufficient permissions for rename voice channel: {interaction.user}")
            await interaction.response.send_message(
                f"Vous n'avez pas la permission de renommer le salon vocal {channel.mention}.",
                ephemeral=True,
            )
            return
        if re.search(rf"{DYNAMIC_SUFFIX}{INDEX_SEPARATOR}\d+$", channel.name):
            logger.warning(f"Attempt to rename a dynamic voice channel: {channel.name}")
            await interaction.response.send_message(
                f"Le salon vocal {channel.mention} est un salon dynamique et ne peut pas être renommé.",
                ephemeral=True,
            )
            return
        old_name = channel.name
        if old_name.endswith(EPHEMERAL_SUFFIX) and not new_name.endswith(EPHEMERAL_SUFFIX):
            new_name += EPHEMERAL_SUFFIX
        if old_name.endswith(DYNAMIC_SUFFIX) and not new_name.endswith(DYNAMIC_SUFFIX):
            new_name += DYNAMIC_SUFFIX
        await channel.edit(name=new_name, reason=f"With rename command by {interaction.user}")
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

    @vocal_group.command(name="delete", description="Supprime un salon vocal.")
    @app_commands.describe(channel="Le salon vocal à supprimer")
    async def vocal_delete(self, interaction: Interaction, channel: VoiceChannel) -> None:
        """Delete a voice channel.

        Args:
            interaction (Interaction): The Discord interaction.
            channel (VoiceChannel): The voice channel to delete.
        """
        log_request(logger, "vocal.delete", interaction, channel=channel.name)
        if not await is_in_allowed_channel(logger, interaction):
            return

        assert isinstance(interaction.user, Member)
        if not channel.permissions_for(interaction.user).manage_channels:
            logger.warning(f"Insufficient permissions for delete voice channel: {interaction.user}")
            await interaction.response.send_message(
                f"Vous n'avez pas la permission de supprimer le salon vocal {channel.mention}.",
                ephemeral=True,
            )
            return
        if len(channel.members) > 0:
            logger.warning(f"Attempt to delete non-empty voice channel: {channel}")
            await interaction.response.send_message(f"Le salon vocal {channel.mention} n'est pas vide.", ephemeral=True)
            return
        await channel.delete(reason=f"With delete command by {interaction.user}")
        logger.info(f"Deleted voice channel {channel.name!r}")
        await interaction.response.send_message(f"Le salon vocal {channel.name!r} a été supprimé.")

    # endregion Vocal Slash Group

    # region ====== Listeners ======
    @commands.Cog.listener()
    async def on_message_delete(self, message: Message) -> None:
        """Handle message deletion events.

        Args:
            message (Message): The deleted message.
        """
        logger.debug(f"Message {message} deleted")
        if not isinstance(message.channel, TextChannel):
            logger.debug(f"Message {message.id} deleted in non-text channel {message.channel}")
            return
        if not message.channel.name.endswith("_bot"):
            return

        assert message.guild is not None
        codir_role = get(message.guild.roles, name="CoDir")
        if codir_role is None:
            logger.error("Required role CoDir not found.")
            await message.channel.send("Rôle CoDir manquant sur le serveur.")
            codir_mention = ""
        else:
            codir_mention = codir_role.mention + " "

        async for entry in message.guild.audit_logs(limit=1, action=AuditLogAction.message_delete):
            deleter = entry.user
            assert isinstance(deleter, Member)
            logger.warning(f"Message deleted in channel {message.channel.name!r} by {deleter.name!r} : {message.content}")
            await message.channel.send(f"{codir_mention}Un message a été supprimé par {deleter.mention} :\n> {message.content}")

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: RawMessageDeleteEvent) -> None:
        """Handle raw message deletion events.

        Args:
            payload (RawMessageDeleteEvent): The raw event payload data.
        """
        logger.debug(f"raw_message_delete: {payload.message_id} in channel {payload.channel_id} in guild {payload.guild_id}")
        if payload.cached_message:
            return
        if payload.guild_id is None:
            return
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return
        channel = self.bot.get_channel(payload.channel_id)
        if not isinstance(channel, TextChannel):
            return
        if not channel.name.endswith("_bot"):
            return

        codir_role = get(guild.roles, name="CoDir")
        if codir_role is None:
            logger.error("Required role CoDir not found.")
            await channel.send("Rôle CoDir manquant sur le serveur.")
            codir_mention = ""
        else:
            codir_mention = codir_role.mention + " "

        async for entry in guild.audit_logs(limit=1, action=AuditLogAction.message_delete):
            deleter = entry.user
            assert isinstance(deleter, Member)
            logger.warning(
                f"Message with ID {payload.message_id} not found in channel {channel.name}. Deleted by {deleter.name!r}."
            )
            await channel.send(
                f"{codir_mention}Un message irrécupérable a été supprimé par {deleter.mention}."
                f"\nID du message : {payload.message_id}."
            )

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

    # endregion Listeners

    # region ====== Helpers ======
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
        logger.debug(f"_delayed_delete: channel={channel} timeout={timeout}")
        await asyncio.sleep(timeout)
        if not channel.members:
            with contextlib.suppress(Exception):
                await channel.delete()
                logger.info(f"Deleted empty voice channel {channel.name!r} after timeout")

    # endregion Helpers


@deprecated("Load the cog using `bot.add_cog()` instead.")
async def setup(bot: commands.Bot) -> None:
    """Setup function to load the ChannelManagement cog.

    Args:
        bot (commands.Bot): The bot instance.
    """
    await bot.add_cog(ChannelManagement(bot))
