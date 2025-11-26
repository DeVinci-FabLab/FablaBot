"""Channel management commands for Discord Bot. Provides slash commands for text and dynamic voice channel management."""

from __future__ import annotations

import asyncio
import logging
import re
from warnings import deprecated

from discord import (
    AuditLogAction,
    CategoryChannel,
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

from fablabot.helpers import build_help_message
from fablabot.helpers.constants import ADMIN_ROLES, RoleNames
from fablabot.helpers.safe_discord_operations import (
    safe_create_text_channel,
    safe_create_voice_channel,
    safe_delete_channel,
    safe_edit_channel,
)
from fablabot.helpers.utils import ensure_command_context, escape_md, format_channel_mention

logger = logging.getLogger(__name__)


DYNAMIC_SUFFIX = "-vocal"
INDEX_SEPARATOR = "/"
EPHEMERAL_SUFFIX = "-temp"


class ChannelManagement(commands.Cog):
    """Cog to register text and voice channel management commands and listeners.

    Commands:
        - /text help: Display help for text channel management commands.
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

    Attributes:
        text_group (app_commands.Group): Command group for text channel management commands.
        vocal_group (app_commands.Group): Command group for voice channel management commands.
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

    @text_group.command(
        name="help",
        description="Affiche l'aide pour les commandes de gestion des salons textuels.",
    )
    @app_commands.describe(show="Afficher l'aide publiquement ou non")
    async def text_help(self, interaction: Interaction, *, show: bool = False) -> None:
        """Display help for text channel management commands.

        Args:
            interaction (Interaction): The Discord interaction context.
            show (bool): Whether to show the help publicly or not.
        """
        help_message = build_help_message(
            "Commandes de gestion des salons textuels",
            "text",
            [
                ("create <channel> <category>", "Créer un salon textuel dans la catégorie spécifiée"),
                ("rename <channel> <new_name>", "Renommer un salon textuel existant"),
                ("delete <channel>", "Supprimer un salon textuel existant"),
            ],
        )
        await interaction.response.send_message(help_message, ephemeral=not show)

    @text_group.command(
        name="create",
        description="Crée un nouveau salon dans la catégorie spécifiée.",
    )
    @app_commands.describe(
        channel="Le nom du salon à créer",
        category="La catégorie dans laquelle créer le salon",
    )
    async def text_create(self, interaction: Interaction, *, channel: str, category: CategoryChannel) -> None:
        """Create a text channel in the passed category.

        Args:
            interaction (Interaction): The Discord interaction context.
            channel (str): The name of the channel to create.
            category (CategoryChannel): The category to create the channel in.
        """
        if not await ensure_command_context(
            logger,
            "text.create",
            interaction,
            log_details={"channel": channel, "category": category.name},
            required_roles=ADMIN_ROLES,
        ):
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
            f"Le salon textuel {format_channel_mention(channel=new_channel)} a été créé dans {escape_md(category.name)}.",
        )

    @text_group.command(
        name="rename",
        description="Renomme un salon textuel.",
    )
    @app_commands.describe(
        channel="Salon à renommer",
        new_name="Nouveau nom du salon",
    )
    async def text_rename(self, interaction: Interaction, *, channel: TextChannel, new_name: str) -> None:
        """Rename a text channel.

        Args:
            interaction (Interaction): The Discord interaction context.
            channel (TextChannel): The channel to rename.
            new_name (str): The new name of the channel.
        """
        if not await ensure_command_context(
            logger,
            "text.rename",
            interaction,
            log_details={"channel": channel.name, "new_name": new_name},
            required_roles=ADMIN_ROLES,
        ):
            return

        old_name = channel.name
        success, error = await safe_edit_channel(
            logger,
            channel,
            reason=f"With rename command by {interaction.user}",
            name=new_name,
        )
        if not success:
            await interaction.response.send_message(error, ephemeral=True)
            return
        logger.info(f"Renamed channel {channel} from {old_name!r} to {new_name!r}")
        await interaction.response.send_message(
            f"Le salon textuel {channel.mention}, anciennement {escape_md(old_name)}, a été renommé en {escape_md(new_name)}.",
        )

    @text_group.command(
        name="delete",
        description="Supprime un salon textuel.",
    )
    @app_commands.describe(channel="Le salon à supprimer")
    async def text_delete(self, interaction: Interaction, *, channel: TextChannel) -> None:
        """Delete a text channel.

        Args:
            interaction (Interaction): The Discord interaction context.
            channel (TextChannel): The channel to delete.
        """
        if not await ensure_command_context(
            logger,
            "text.delete",
            interaction,
            log_details={"channel": channel.name},
            required_roles=ADMIN_ROLES,
        ):
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

    @vocal_group.command(
        name="help",
        description="Affiche l'aide pour les commandes de gestion des salons vocaux.",
    )
    @app_commands.describe(show="Afficher l'aide publiquement ou non")
    async def vocal_help(self, interaction: Interaction, *, show: bool = False) -> None:
        """Display help for vocal channel management commands.

        Args:
            interaction (Interaction): The Discord interaction context.
            show (bool): Whether to show the help publicly or not.
        """
        help_message = build_help_message(
            "Commandes de gestion des salons vocaux",
            "vocal",
            [
                (
                    "create <name> <category> [is_temporary] [max_user]",
                    "Créer un salon vocal (temporaire par défaut) avec nombre d'utilisateurs optionnel",
                ),
                ("rename <channel> <new_name>", "Renommer un salon vocal existant"),
                ("delete <channel>", "Supprimer un salon vocal existant"),
            ],
        )
        await interaction.response.send_message(help_message, ephemeral=not show)

    @vocal_group.command(
        name="create",
        description="Crée un salon vocal personnalisé.",
    )
    @app_commands.describe(
        name="Nom du salon vocal à créer",
        category="Catégorie dans laquelle créer le salon vocal",
        is_temporary="Salon temporaire (supprimé après inactivité)",
        max_user="Nombre max d'utilisateurs (None pour illimité)",
    )
    async def vocal_create(
        self,
        interaction: Interaction,
        *,
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
        if not await ensure_command_context(
            logger,
            "vocal.create",
            interaction,
            log_details={
                "name": name,
                "category": category.name,
                "is_temporary": is_temporary,
                "max_user": max_user,
            },
            required_roles=ADMIN_ROLES,
        ):
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

    @vocal_group.command(
        name="rename",
        description="Renomme un salon vocal.",
    )
    @app_commands.describe(
        channel="Le salon vocal à renommer",
        new_name="Nouveau nom du salon",
    )
    async def vocal_rename(self, interaction: Interaction, *, channel: VoiceChannel, new_name: str) -> None:
        """Rename a voice channel.

        Args:
            interaction (Interaction): The Discord interaction context.
            channel (VoiceChannel): The voice channel to rename.
            new_name (str): The new name of the voice channel.
        """
        if not await ensure_command_context(
            logger,
            "vocal.rename",
            interaction,
            log_details={"channel": channel.name, "new_name": new_name},
            required_roles=ADMIN_ROLES,
        ):
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
            logger,
            channel,
            reason=f"With rename command by {interaction.user}",
            name=new_name,
        )
        if not success:
            await interaction.response.send_message(error, ephemeral=True)
            return
        logger.info(f"Renamed voice channel {channel} from {old_name!r} to {new_name!r}")
        assert channel.category is not None
        for vc in channel.category.voice_channels:
            if vc.name.startswith(f"{old_name}{INDEX_SEPARATOR}"):
                suffix = vc.name[len(old_name) :]
                new_vc_name = f"{new_name}{suffix}"
                success, error = await safe_edit_channel(
                    logger,
                    vc,
                    reason="Renaming associated dynamic channel",
                    name=new_vc_name,
                )
                if not success:
                    await interaction.response.send_message(error, ephemeral=True)
                    return
                logger.info(f"Renamed associated dynamic channel {vc} from {old_name + suffix!r} to {new_vc_name!r}")
        await interaction.response.send_message(
            f"Le salon vocal {channel.mention}, anciennement {escape_md(old_name)}, a été renommé en {escape_md(new_name)}.",
        )

    @vocal_group.command(
        name="delete",
        description="Supprime un salon vocal.",
    )
    @app_commands.describe(channel="Le salon vocal à supprimer")
    async def vocal_delete(self, interaction: Interaction, *, channel: VoiceChannel) -> None:
        """Delete a voice channel.

        Args:
            interaction (Interaction): The Discord interaction context.
            channel (VoiceChannel): The voice channel to delete.
        """
        if not await ensure_command_context(
            logger,
            "vocal.delete",
            interaction,
            log_details={"channel": channel.name},
            required_roles=ADMIN_ROLES,
        ):
            return

        if len(channel.members) > 0:
            logger.warning(f"Attempt to delete non-empty voice channel: {channel}")
            await interaction.response.send_message(
                f"Le salon vocal {channel.mention} n'est pas vide.",
                ephemeral=True,
            )
            return
        channel_name = channel.name
        success, error = await safe_delete_channel(logger, channel, reason=f"With delete command by {interaction.user}")
        if not success:
            await interaction.response.send_message(error, ephemeral=True)
            return
        logger.info(f"Deleted voice channel {channel_name!r}")
        await interaction.response.send_message(f"Le salon vocal {escape_md(channel_name)} a été supprimé.")

    # endregion Vocal Slash Commands Group

    # region ====== Event Listeners ======

    @commands.Cog.listener()
    async def on_message_delete(self, msg: Message) -> None:
        """Handle message deletion events.

        Args:
            msg (Message): The deleted message.
        """
        logger.debug(f"Message {msg} deleted")
        if not isinstance(msg.channel, TextChannel):
            logger.debug(f"Message {msg.id} deleted in non-text channel {msg.channel}")
            return
        if not msg.author.bot:
            logger.debug(f"Message {msg.id} deleted wasn't sent by a bot, ignoring")
            return

        from fablabot.guild_config import get_commands_channel_id, get_log_channel_id

        assert msg.guild is not None
        if (
            not msg.channel.name.endswith("_bot")
            and get_log_channel_id() != msg.channel.id
            and get_commands_channel_id(msg.guild.id) != msg.channel.id
        ):
            return

        codir_mention = await self._get_codir_mention(msg.guild, msg.channel)
        try:
            async for entry in msg.guild.audit_logs(limit=1, action=AuditLogAction.message_delete):
                deleter = entry.user
                assert isinstance(deleter, Member)
                logger.warning(f"Message deleted in channel {msg.channel.name!r} by {deleter.name!r} : {msg.content}")
                try:
                    await msg.channel.send(
                        f"{codir_mention}Un message a été supprimé par {deleter.mention} :\n>>> {msg.content}",
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

        from fablabot.guild_config import get_commands_channel_id, get_log_channel_id

        if (
            not channel.name.endswith("_bot")
            and get_log_channel_id() != channel.id
            and get_commands_channel_id(guild.id) != channel.id
        ):
            return

        codir_mention = await self._get_codir_mention(guild, channel)
        try:
            async for entry in guild.audit_logs(limit=1, action=AuditLogAction.message_delete):
                deleter = entry.user
                assert isinstance(deleter, Member)
                logger.warning(
                    f"Message with ID {payload.message_id} not found in channel {channel.name}. Deleted by {deleter.name!r}.",
                )
                try:
                    await channel.send(
                        f"{codir_mention}Un message irrécupérable a été supprimé par {deleter.mention}."
                        f"\nID du message : {payload.message_id}.",
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
            for vc in category.voice_channels:
                if vc.name.endswith(DYNAMIC_SUFFIX):
                    await self._manage_voice_channels(category, vc)
                elif vc.name.endswith(EPHEMERAL_SUFFIX) and not vc.members and vc.id not in self.remove_tasks:
                    self.remove_tasks[vc.id] = asyncio.create_task(self._delayed_delete(vc, 60))

    # endregion Event Listeners

    # region ====== Helpers ======
    # -- Voice Channel Helpers --

    async def _manage_voice_channels(self, category: CategoryChannel, base_vc: VoiceChannel) -> None:
        """Manage voice channels in a category.

        Args:
            category (CategoryChannel): The category to manage channels in.
            base_vc (VoiceChannel): The base voice channel to manage.
        """
        logger.debug(f"_manage_voice_channels: category={category.name} base={base_vc.name}")
        vcs = [vc for vc in category.voice_channels if vc.name.startswith(base_vc.name)]
        empty = [vc for vc in vcs if not vc.members]

        if len(empty) > 1:
            empty.sort(key=lambda c: c.name)
            for vc in empty[1:]:
                if vc.id not in self.remove_tasks:
                    self.remove_tasks[vc.id] = asyncio.create_task(self._delayed_delete(vc, 10))

        if not empty:
            vc_names = {vc.name for vc in vcs}
            for idx in range(1, len(vcs) + 2):
                new_name = f"{base_vc.name}{INDEX_SEPARATOR}{idx}"
                if new_name not in vc_names:
                    new_vc, error = await safe_create_voice_channel(
                        logger,
                        category,
                        new_name,
                        bitrate=base_vc.bitrate,
                        user_limit=base_vc.user_limit,
                        rtc_region=base_vc.rtc_region,
                        video_quality_mode=base_vc.video_quality_mode,
                        overwrites=base_vc.overwrites,
                        reason="Creating additional dynamic voice channel",
                    )
                    if not new_vc or error:
                        logger.exception(error)
                    else:
                        logger.info(f"Created additional dynamic voice channel {new_vc} in category {category.name!r}")
                    break

    async def _delayed_delete(self, vc: VoiceChannel, timeout: int) -> None:
        """Wait a specified amount of time then delete the voice channel if still empty.

        Args:
            vc (VoiceChannel): The voice channel to delete.
            timeout (int): The time to wait before deleting the channel in seconds.
        """
        logger.debug(f"_delayed_delete: channel={vc} timeout={timeout}")
        await asyncio.sleep(timeout)
        if not vc.members:
            success, error = await safe_delete_channel(logger, vc, reason="Auto-deleting empty temporary voice channel")
            if not success:
                logger.error(f"Failed to delete empty voice channel {vc.name!r}: {error}")

    @staticmethod
    async def _get_codir_mention(guild: Guild, channel: TextChannel) -> str:
        """Get CoDir role mention or return empty string if not found."""
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
