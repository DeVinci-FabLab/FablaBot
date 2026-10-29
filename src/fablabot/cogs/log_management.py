"""Message management commands for Discord Bot. Provides slash commands for sending and managing messages."""

from __future__ import annotations

from datetime import datetime
import logging
from warnings import deprecated

from discord import Interaction, TextChannel, abc, app_commands
from discord.ext import commands

from fablabot.guild_config import get_log_channel_id, migrate_legacy_log_channel, set_log_channel_id
from fablabot.helpers.constants import ADMIN_ROLES, PARIS_TZ, RoleNames, staff_default_permissions
from fablabot.helpers.help_messages import build_help_message
from fablabot.helpers.utils import ensure_command_context, format_channel_mention
from fablabot.logging_handlers import DailyFileHandler

logger = logging.getLogger(__name__)


def _parse_export_date(raw: str) -> datetime | None:
    """Parse a date typed as DD/MM/YYYY.

    Args:
        raw (str): The date as typed by the user.

    Returns:
        datetime | None: The date at midnight in Paris time, or None if it is malformed.
    """
    try:
        return datetime.strptime(raw, "%d/%m/%Y").replace(tzinfo=PARIS_TZ)
    except ValueError:
        return None


class LogManagement(commands.Cog):
    """Cog to register log management commands.

    Commands:
        - /log help: Display help for log management commands.
        - /log set: Configure the channel receiving bot logs.
        - /log export: Export the logs.

    Attributes:
        log_group (app_commands.Group): Command group for log management commands.
    """

    def __init__(self, bot: commands.Bot) -> None:
        """Initialize the cog and register its command groups.

        Args:
            bot (commands.Bot): The bot instance.
        """
        self.bot = bot
        logger.info("LogManagement initialized")

    def _guild_of_channel(self, channel_id: int) -> int | None:
        """Resolve a channel ID to the ID of its guild through the bot cache.

        Args:
            channel_id (int): The channel to look up.

        Returns:
            int | None: The guild ID, or None when the channel is not a cached guild channel.
        """
        channel = self.bot.get_channel(channel_id)
        return channel.guild.id if isinstance(channel, abc.GuildChannel) else None

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        """Give the legacy instance-wide log channel to the server that owns it."""
        migrate_legacy_log_channel(self._guild_of_channel)

    # region ====== Log Slash Commands Group ======
    log_group = app_commands.Group(
        name="log", description="Configuration des logs du bot", default_permissions=staff_default_permissions()
    )

    @log_group.command(
        name="help",
        description="Affiche l'aide pour les commandes de gestion des logs.",
    )
    @app_commands.describe(show="Afficher l'aide publiquement ou non")
    async def log_help(self, interaction: Interaction, *, show: bool = False) -> None:
        """Display help for log management commands.

        Args:
            interaction (Interaction): The Discord interaction context.
            show (bool): Whether to show the help publicly or not.
        """
        help_message = build_help_message(
            "Commandes de gestion des logs",
            "log",
            [
                ("set <channel>", "Configurer le salon recevant les erreurs du bot sur ce serveur"),
                ("export [date]", "Exporter les logs de ce serveur, du jour ou d'une date donnée (format JJ/MM/AAAA)"),
            ],
        )
        await interaction.response.send_message(help_message, ephemeral=not show)

    @log_group.command(
        name="set",
        description="Configure le salon recevant les erreurs du bot sur ce serveur.",
    )
    @app_commands.describe(channel="Salon textuel qui recevra les logs du bot.")
    async def log_set(self, interaction: Interaction, *, channel: TextChannel) -> None:
        """Configure the log channel destination for Discord logging.

        Args:
            interaction (Interaction): The Discord interaction context.
            channel (TextChannel): The text channel receiving bot logs.
        """
        if not await ensure_command_context(
            logger,
            "log.set",
            interaction,
            log_details={"channel_id": channel.id},
            required_roles=ADMIN_ROLES,
        ):
            return

        assert interaction.guild is not None
        guild = interaction.guild
        if channel.guild.id != guild.id:
            logger.warning(f"[log.set] refused kind=other_guild_channel guild_id={guild.id} user_id={interaction.user.id}")
            await interaction.response.send_message("Ce salon n'appartient pas à ce serveur.", ephemeral=True)
            return

        previous_channel: TextChannel | None = None
        previous_id = get_log_channel_id(guild.id)
        if previous_id is not None:
            maybe_previous = guild.get_channel(previous_id)
            if isinstance(maybe_previous, TextChannel):
                previous_channel = maybe_previous

        set_log_channel_id(guild.id, channel.id)
        logger.info(f"Log channel set (guild_id={guild.id}, channel_id={channel.id}, user_id={interaction.user.id})")

        confirmation = f"Les logs seront désormais envoyés dans {format_channel_mention(channel)}."
        if previous_channel is not None:
            confirmation += f" Ancien salon : {format_channel_mention(previous_channel)}."
        await interaction.response.send_message(confirmation)

    @log_group.command(
        name="export",
        description="Exporte les logs récents de ce serveur.",
    )
    @app_commands.describe(
        date="Date des logs à exporter (format : JJ/MM/AAAA). Si non spécifié, les logs d'aujourd'hui seront exportés.",
    )
    async def log_export(self, interaction: Interaction, *, date: str | None = None) -> None:
        """Export this server's recent logs.

        Args:
            interaction (Interaction): The Discord interaction context.
            date (str | None): Date of logs to export in DD/MM/YYYY format. If not specified, exports logs from today.
        """
        if not await ensure_command_context(
            logger,
            "log.export",
            interaction,
            log_details=None,
            required_roles=ADMIN_ROLES | {RoleNames.DIGITAL_MANAGER, RoleNames.DIGITAL_POLE},
        ):
            return

        assert interaction.guild is not None
        guild = interaction.guild
        file_log_handler = getattr(self.bot, "file_log_handler", None)
        if not isinstance(file_log_handler, DailyFileHandler):
            logger.error("DailyFileHandler is not initialized on the bot.")
            await interaction.response.send_message(
                "Le gestionnaire de logs n'est pas initialisé sur ce bot.",
                ephemeral=True,
            )
            return

        if date:
            dt = _parse_export_date(date)
            if dt is None:
                logger.info(f"[log.export] refused kind=malformed_date guild_id={guild.id} user_id={interaction.user.id}")
                await interaction.response.send_message(
                    "Date invalide. Format attendu : JJ/MM/AAAA (exemple : 28/10/2026).",
                    ephemeral=True,
                )
                return
        else:
            dt = datetime.now(tz=PARIS_TZ)

        file = await file_log_handler.export_logs(guild.id, dt.date())
        day_text = dt.strftime("%d/%m/%Y")
        logger.info(f"Log export (guild_id={guild.id}, date={dt.date().isoformat()}, found={'yes' if file else 'no'})")

        if file is None:
            await interaction.response.send_message(f"Aucun log de ce serveur pour le {day_text}.", ephemeral=True)
            return

        await interaction.response.send_message(f"Voici les logs de ce serveur du {day_text} :", file=file, ephemeral=True)

    # endregion Log Slash Commands Group


@deprecated("Load the cog using `bot.add_cog()` instead.")
async def setup(bot: commands.Bot) -> None:
    """Setup function to load the MessageManagement cog.

    Args:
        bot (commands.Bot): The bot instance.
    """
    await bot.add_cog(LogManagement(bot))
