"""Channel management commands for Discord Bot. Provides slash commands for text and dynamic voice channel management."""

from __future__ import annotations

import asyncio
import logging
import re
from warnings import deprecated

from discord import (
    AuditLogAction,
    CategoryChannel,
    DMChannel,
    ForumChannel,
    GroupChannel,
    Guild,
    HTTPException,
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

from fablabot.cogs.constants import ErrorMessages, RoleNames
from fablabot.cogs.helpers.utils import (
    ADMIN_ROLES,
    check_has_role,
    escape_md,
    format_channel_mention,
    is_in_allowed_channel,
    log_request,
    safe_create_text_channel,
    safe_create_voice_channel,
    safe_delete_channel,
    safe_edit_channel,
)
from fablabot.discord_log_handler import DiscordLogHandler
from fablabot.guild_config import set_log_channel_id

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
        - /log set: Configure the text channel receiving bot logs on errors.

    Listeners:
        - on_message_delete: Notify when a message is deleted in a bot channel, log the deleter and resend the content.
        - on_raw_message_delete: Notify when a message is deleted in a bot channel (for uncached messages).
        - on_voice_state_update: Create and remove dynamic voice channels in categories with a base channel named *-vocal.

    Attributes:
        text_group (app_commands.Group): Command group for text channel management commands.
        vocal_group (app_commands.Group): Command group for voice channel management commands.
        log_group (app_commands.Group): Command group for bot log configuration commands.
    """

    def __init__(self, bot: commands.Bot) -> None:
        """Initialize the cog and register its command groups.

        Args:
            bot (commands.Bot): The bot instance.
        """
        self.bot = bot
        self.remove_tasks: dict[int, asyncio.Task[None]] = {}

    # region ====== Text Slash Commands Group ======
    text_group = app_commands.Group(name="text", description="Gestion des salons textuels")

    @text_group.command(name="help", description="Affiche l'aide pour les commandes de gestion des salons textuels.")
    async def text_help(self, interaction: Interaction) -> None:
        """Display help for text channel management commands.

        Args:
            interaction (Interaction): The Discord interaction context.
        """
        help_message = (
            "**Commandes de gestion des salons textuels :**\n"
            "- `/text clear [messages]`: Nettoie le salon actuel de ses derniers messages. "
            "Par défaut, 5 messages sont supprimés.\n"
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
    async def text_clear(self, interaction: Interaction, messages: app_commands.Range[int, 1, 50] = 5) -> None:
        """Clears the current channel of its last messages.

        Args:
            interaction (Interaction): The Discord interaction context.
            messages (app_commands.Range[int, 1, 50], optional): The number of messages to purge. Defaults to 5.
        """
        log_request(logger, "text.clear", interaction, messages=messages)
        assert not isinstance(
            interaction.channel,
            ForumChannel | CategoryChannel | DMChannel | GroupChannel | None,
        )
        if not interaction.permissions.manage_messages:
            logger.warning(f"Insufficient permissions for manage_messages: {interaction.user}")
            await interaction.response.send_message(ErrorMessages.NO_PERMISSION_MANAGE_MESSAGES, ephemeral=True)
            return
        if interaction.channel.name.endswith("_bot"):
            logger.warning(f"Attempt to clear {interaction.channel.name} channel")
            await interaction.response.send_message(
                f"Vous ne pouvez pas nettoyer le salon {interaction.channel.mention}."
                f" Veuillez contacter le pôle numérique si nécessaire.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message("Nettoyage en cours...", ephemeral=True)
        try:
            deleted = await interaction.channel.purge(
                limit=messages,
                reason=f"With clear command by {interaction.user}",
            )
        except HTTPException:
            logger.exception(f"HTTP error while purging {messages} messages in {interaction.channel}")
            await interaction.edit_original_response(content=ErrorMessages.CHANNEL_CLEAR_FAILED)
            return
        logger.info(f"Deleted {len(deleted)} messages in channel {interaction.channel.name}")
        await interaction.edit_original_response(content=f"{len(deleted)} messages supprimés avec succès !")

    @text_group.command(name="create", description="Crée un nouveau salon dans la catégorie spécifiée.")
    @app_commands.describe(
        channel="Le nom du salon à créer",
        category="La catégorie dans laquelle créer le salon",
    )
    async def text_create(self, interaction: Interaction, channel: str, category: CategoryChannel) -> None:
        """Create a text channel in the passed category.

        Args:
            interaction (Interaction): The Discord interaction context.
            channel (str): The name of the channel to create.
            category (CategoryChannel): The category to create the channel in.
        """
        log_request(logger, "text.create", interaction, channel=channel, category=category.name)
        if not await is_in_allowed_channel(logger, interaction):
            return

        assert isinstance(interaction.user, Member)
        if not await check_has_role(logger, interaction, ADMIN_ROLES):
            return

        if channel in (c.name for c in category.channels):
            logger.info(f"Text channel {channel!r} already exists in {category!r}")
            await interaction.response.send_message(
                f"Un salon {escape_md(channel)} existe déjà dans {escape_md(category.name)}.",
                ephemeral=True,
            )
            return
        new_channel, error = await safe_create_text_channel(
            logger,
            category,
            channel,
            reason=f"With create command by {interaction.user}",
        )
        if not new_channel or error:
            await interaction.response.send_message(error, ephemeral=True)
            return
        logger.info(f"Created text channel {new_channel!r} in category {category!r}")
        await interaction.response.send_message(
            f"Le salon textuel {format_channel_mention(channel=new_channel)} a été créé dans {escape_md(category.name)}."
        )

    @text_group.command(name="rename", description="Renomme un salon textuel.")
    @app_commands.describe(channel="Salon à renommer", new_name="Nouveau nom du salon")
    async def text_rename(self, interaction: Interaction, channel: TextChannel, new_name: str) -> None:
        """Rename a text channel.

        Args:
            interaction (Interaction): The Discord interaction context.
            channel (TextChannel): The channel to rename.
            new_name (str): The new name of the channel.
        """
        log_request(logger, "text.rename", interaction, channel=channel.name, new_name=new_name)
        if not await is_in_allowed_channel(logger, interaction):
            return

        assert isinstance(interaction.user, Member)
        if not await check_has_role(logger, interaction, ADMIN_ROLES):
            return

        old_name = channel.name
        success, error = await safe_edit_channel(
            logger, channel, reason=f"With rename command by {interaction.user}", name=new_name
        )
        if not success:
            await interaction.response.send_message(error, ephemeral=True)
            return
        logger.info(f"Renamed channel {channel} from {old_name!r} to {new_name!r}")
        await interaction.response.send_message(
            f"Le salon textuel {channel.mention}, anciennement {escape_md(old_name)}, a été renommé en {escape_md(new_name)}."
        )

    @text_group.command(name="delete", description="Supprime un salon textuel.")
    @app_commands.describe(channel="Le salon à supprimer")
    async def text_delete(self, interaction: Interaction, channel: TextChannel) -> None:
        """Delete a text channel.

        Args:
            interaction (Interaction): The Discord interaction context.
            channel (TextChannel): The channel to delete.
        """
        log_request(logger, "text.delete", interaction, channel=channel.name)
        if not await is_in_allowed_channel(logger, interaction):
            return

        assert isinstance(interaction.user, Member)
        if not await check_has_role(logger, interaction, ADMIN_ROLES):
            return

        channel_name = channel.name
        success, error = await safe_delete_channel(logger, channel, reason=f"With delete command by {interaction.user}")
        if not success:
            await interaction.response.send_message(error, ephemeral=True)
            return
        logger.info(f"Deleted text channel {channel_name!r}")
        await interaction.response.send_message(f"Le salon textuel {escape_md(channel.name)} a été supprimé.")

    # endregion Text Slash Commands Group

    # region ====== Vocal Slash Commands Group ======
    vocal_group = app_commands.Group(name="vocal", description="Gestion des salons vocaux dynamiques")

    @vocal_group.command(name="help", description="Affiche l'aide pour les commandes de gestion des salons vocaux.")
    async def vocal_help(self, interaction: Interaction) -> None:
        """Display help for vocal channel management commands.

        Args:
            interaction (Interaction): The Discord interaction context.
        """
        help_message = (
            "**Commandes de gestion des salons vocaux :**\n"
            "- `/vocal create <name> <category> [is_temporary] [max_user]`: "
            "Crée un nouveau salon vocal dans la catégorie spécifiée. Par défaut, le salon est temporaire et illimité.\n"
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
        max_user: app_commands.Range[int, 1, 99] | None = None,
    ) -> None:
        """Create a custom voice channel in the passed category.

        Args:
            interaction (Interaction): The Discord interaction context.
            name (str): The name of the voice channel to create.
            category (CategoryChannel): The category in which to create the voice channel.
            is_temporary (bool, optional): Whether the channel is temporary. Defaults to True.
            max_user (app_commands.Range[int, 1, 99] | None, optional):
                The maximum number of users allowed in the channel. Defaults to None.
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
        if not await check_has_role(logger, interaction, ADMIN_ROLES):
            return

        existing = {vc.name for vc in category.voice_channels}
        channel_name = f"{name}{EPHEMERAL_SUFFIX}" if is_temporary else name
        if channel_name in existing:
            logger.info(f"Voice channel {channel_name!r} already exists in {category!r}")
            await interaction.response.send_message(
                f"Un salon vocal {escape_md(channel_name)} existe déjà dans {escape_md(category.name)}.",
                ephemeral=True,
            )
            return
        new_channel, error = await safe_create_voice_channel(
            logger,
            category,
            channel_name,
            user_limit=max_user,
            reason=f"With create command by {interaction.user}",
        )
        if not new_channel or error:
            await interaction.response.send_message(error, ephemeral=True)
            return
        logger.info(f"Created voice channel {new_channel!r} in category {category.name!r}")
        channel_creation_message = (
            f"Le salon vocal {'temporaire' if is_temporary else 'permanent'} {format_channel_mention(new_channel)} "
            f"a été créé dans la catégorie {escape_md(category.name)}."
        )
        if max_user:
            channel_creation_message += f" (max {max_user} utilisateurs)"
        await interaction.response.send_message(channel_creation_message)

    @vocal_group.command(name="rename", description="Renomme un salon vocal.")
    @app_commands.describe(channel="Le salon vocal à renommer", new_name="Nouveau nom du salon")
    async def vocal_rename(self, interaction: Interaction, channel: VoiceChannel, new_name: str) -> None:
        """Rename a voice channel.

        Args:
            interaction (Interaction): The Discord interaction context.
            channel (VoiceChannel): The voice channel to rename.
            new_name (str): The new name of the voice channel.
        """
        log_request(logger, "vocal.rename", interaction, channel=channel.name, new_name=new_name)
        if not await is_in_allowed_channel(logger, interaction):
            return

        assert isinstance(interaction.user, Member)
        assert channel.category is not None
        if not await check_has_role(logger, interaction, ADMIN_ROLES):
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
        success, error = await safe_edit_channel(
            logger, channel, reason=f"With rename command by {interaction.user}", name=new_name
        )
        if not success:
            await interaction.response.send_message(error, ephemeral=True)
            return
        logger.info(f"Renamed voice channel {channel} from {old_name!r} to {new_name!r}")
        for vc in channel.category.voice_channels:
            if vc.name.startswith(f"{old_name}{INDEX_SEPARATOR}"):
                suffix = vc.name[len(old_name) :]
                new_vc_name = f"{new_name}{suffix}"
                success, error = await safe_edit_channel(
                    logger, vc, reason="Renaming associated dynamic channel", name=new_vc_name
                )
                if not success:
                    await interaction.response.send_message(error, ephemeral=True)
                    return
                logger.info(f"Renamed associated dynamic channel {vc} from {old_name + suffix!r} to {new_vc_name!r}")
        await interaction.response.send_message(
            f"Le salon vocal {channel.mention}, anciennement {escape_md(old_name)}, a été renommé en {escape_md(new_name)}."
        )

    @vocal_group.command(name="delete", description="Supprime un salon vocal.")
    @app_commands.describe(channel="Le salon vocal à supprimer")
    async def vocal_delete(self, interaction: Interaction, channel: VoiceChannel) -> None:
        """Delete a voice channel.

        Args:
            interaction (Interaction): The Discord interaction context.
            channel (VoiceChannel): The voice channel to delete.
        """
        log_request(logger, "vocal.delete", interaction, channel=channel.name)
        if not await is_in_allowed_channel(logger, interaction):
            return

        assert isinstance(interaction.user, Member)
        if not await check_has_role(logger, interaction, ADMIN_ROLES):
            return

        if len(channel.members) > 0:
            logger.warning(f"Attempt to delete non-empty voice channel: {channel}")
            await interaction.response.send_message(f"Le salon vocal {channel.mention} n'est pas vide.", ephemeral=True)
            return
        channel_name = channel.name
        success, error = await safe_delete_channel(logger, channel, reason=f"With delete command by {interaction.user}")
        if not success:
            await interaction.response.send_message(error, ephemeral=True)
            return
        logger.info(f"Deleted voice channel {channel_name!r}")
        await interaction.response.send_message(f"Le salon vocal {escape_md(channel_name)} a été supprimé.")

    # endregion Vocal Slash Commands Group

    # region ====== Log Slash Commands Group ======
    log_group = app_commands.Group(name="log", description="Configuration des logs du bot")

    @log_group.command(name="set", description="Configure le salon recevant les logs du bot en cas d'erreur.")
    @app_commands.describe(channel="Salon textuel qui recevra les logs du bot.")
    async def log_set(self, interaction: Interaction, channel: TextChannel) -> None:
        """Configure the log channel destination for Discord logging.

        Args:
            interaction (Interaction): The Discord interaction context.
            channel (TextChannel): The text channel receiving bot logs.
        """
        log_request(logger, "log.set", interaction, channel=channel.name, channel_id=channel.id)
        if not await is_in_allowed_channel(logger, interaction):
            return

        assert interaction.guild is not None
        assert isinstance(interaction.user, Member)
        if not await check_has_role(logger, interaction, ADMIN_ROLES):
            return

        handler = getattr(self.bot, "log_handler", None)
        if not isinstance(handler, DiscordLogHandler):
            logger.error("DiscordLogHandler is not initialized on the bot.")
            await interaction.response.send_message(
                "Le gestionnaire de logs n'est pas initialisé sur ce bot.",
                ephemeral=True,
            )
            return

        previous_id = handler.log_channel_id
        if previous_id == channel.id:
            await interaction.response.send_message(
                f"{channel.mention} est déjà configuré comme salon de logs.",
                ephemeral=True,
            )
            return

        previous_channel: TextChannel | None = None
        maybe_previous = self.bot.get_channel(previous_id)
        if isinstance(maybe_previous, TextChannel):
            previous_channel = maybe_previous

        handler.set_log_channel(channel)
        set_log_channel_id(channel.id)
        logger.info(msg=f"Log channel set to {channel} (id={channel.id}) by {interaction.user} (id={interaction.user.id})")

        confirmation = f"Les logs seront désormais envoyés dans {format_channel_mention(channel)}."
        if previous_channel is not None:
            confirmation += f" Ancien salon : {format_channel_mention(previous_channel)}."
        await interaction.response.send_message(confirmation)

    # endregion Log Slash Commands Group

    # region ====== Event Listeners ======

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
        codir_mention = await self._get_codir_mention(message.guild, message.channel)
        try:
            async for entry in message.guild.audit_logs(limit=1, action=AuditLogAction.message_delete):
                deleter = entry.user
                assert isinstance(deleter, Member)
                logger.warning(f"Message deleted in channel {message.channel.name!r} by {deleter.name!r} : {message.content}")
                try:
                    await message.channel.send(
                        f"{codir_mention}Un message a été supprimé par {deleter.mention} :\n> {message.content}"
                    )
                except HTTPException:
                    logger.exception("HTTP error while notifying message deletion")
        except HTTPException:
            logger.exception("Unable to read audit logs for message deletion")

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

        codir_mention = await self._get_codir_mention(guild, channel)
        try:
            async for entry in guild.audit_logs(limit=1, action=AuditLogAction.message_delete):
                deleter = entry.user
                assert isinstance(deleter, Member)
                logger.warning(
                    f"Message with ID {payload.message_id} not found in channel {channel.name}. Deleted by {deleter.name!r}."
                )
                try:
                    await channel.send(
                        f"{codir_mention}Un message irrécupérable a été supprimé par {deleter.mention}."
                        f"\nID du message : {payload.message_id}."
                    )
                except HTTPException:
                    logger.exception("HTTP error while notifying message deletion")
        except HTTPException:
            logger.exception("Unable to inspect audit logs for raw deletion")

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

    # endregion Event Listeners

    # region ====== Helpers ======
    # -- Voice Channel Helpers --

    async def _manage_voice_channels(self, category: CategoryChannel, base_channel: VoiceChannel) -> None:
        """Manage voice channels in a category.

        Args:
            category (CategoryChannel): The category to manage channels in.
            base_channel (VoiceChannel): The base channel to manage.
        """
        logger.debug(f"_manage_voice_channels: category={category.name} base={base_channel.name}")
        channels = [vc for vc in category.voice_channels if vc.name.startswith(base_channel.name)]
        empty = [vc for vc in channels if not vc.members]

        if len(empty) > 1:
            empty.sort(key=lambda c: c.name)
            for vc in empty[1:]:
                if vc.id not in self.remove_tasks:
                    self.remove_tasks[vc.id] = asyncio.create_task(self._delayed_delete(vc, 10))

        if not empty:
            channel_names = {c.name for c in channels}
            for idx in range(1, len(channels) + 2):
                new_name = f"{base_channel.name}{INDEX_SEPARATOR}{idx}"
                if new_name not in channel_names:
                    new_channel, error = await safe_create_voice_channel(
                        logger,
                        category,
                        new_name,
                        bitrate=base_channel.bitrate,
                        user_limit=base_channel.user_limit,
                        rtc_region=base_channel.rtc_region,
                        video_quality_mode=base_channel.video_quality_mode,
                        overwrites=base_channel.overwrites,
                        reason="Creating additional dynamic voice channel",
                    )
                    if not new_channel or error:
                        logger.exception(error)
                    else:
                        logger.info(f"Created additional dynamic voice channel {new_channel} in category {category.name!r}")
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
            success, error = await safe_delete_channel(logger, channel, reason="Auto-deleting empty temporary voice channel")
            if not success:
                logger.error(f"Failed to delete empty voice channel {channel.name!r}: {error}")

    @staticmethod
    async def _get_codir_mention(guild: Guild, channel: TextChannel) -> str:
        """Get CoDir role mention or return empty string if not found.

        Args:
            guild (Guild): The guild to search for the role.
            channel (TextChannel): The channel to send error message to if role not found.

        Returns:
            str: The role mention with trailing space, or empty string if not found.
        """
        codir_role = get(guild.roles, name=RoleNames.CODIR)
        if codir_role is None:
            logger.error(f"Required role {RoleNames.CODIR} not found.")
            try:
                await channel.send(f"Rôle {RoleNames.CODIR} manquant sur le serveur.")
            except HTTPException:
                logger.exception(f"HTTP error while notifying missing {RoleNames.CODIR} role")
            return ""
        return f"{codir_role.mention} "

    # endregion Helpers


@deprecated("Load the cog using `bot.add_cog()` instead.")
async def setup(bot: commands.Bot) -> None:
    """Setup function to load the ChannelManagement cog.

    Args:
        bot (commands.Bot): The bot instance.
    """
    await bot.add_cog(ChannelManagement(bot))
