"""Message management commands for Discord Bot. Provides slash commands for sending and managing messages."""

from __future__ import annotations

from datetime import datetime
import logging
from warnings import deprecated

from discord import Interaction, TextChannel, app_commands
from discord.ext import commands

from fablabot.guild_config import set_log_channel_id
from fablabot.helpers.constants import ADMIN_ROLES, PARIS_TZ, RoleNames
from fablabot.helpers.utils import check_has_role, format_channel_mention, is_in_allowed_channel, log_request
from fablabot.logging_handlers import DailyFileHandler, DiscordLogHandler

logger = logging.getLogger(__name__)


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

    # region ====== Log Slash Commands Group ======
    log_group = app_commands.Group(name="log", description="Configuration des logs du bot")

    @log_group.command(name="help", description="Affiche l'aide pour les commandes de gestion des logs.")
    @app_commands.describe(show="Afficher l'aide publiquement ou non")
    async def log_help(self, interaction: Interaction, show: bool = False) -> None:
        """Display help for log management commands.

        Args:
            interaction (Interaction): The Discord interaction context.
            show (bool): Whether to show the help publicly or not.
        """
        help_text = (
            "**Commandes de gestion des logs :**\n"
            "- `/log set <channel>`: Configure le salon recevant les logs du bot.\n"
            "- `/log export [date]`: Exporte les logs récents.\n"
            "- `/log help [show]`: Affiche cette aide. Par défaut, elle est affichée secrètement.\n"
            "\n"
            "Assurez-vous d'avoir les permissions nécessaires pour utiliser ces commandes."
        )
        await interaction.response.send_message(help_text, ephemeral=not show)

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

        if not await check_has_role(logger, interaction, ADMIN_ROLES):
            return

        discord_log_handler = getattr(self.bot, "discord_log_handler", None)
        if not isinstance(discord_log_handler, DiscordLogHandler):
            logger.error("DiscordLogHandler is not initialized on the bot.")
            await interaction.response.send_message(
                "Le gestionnaire de logs n'est pas initialisé sur ce bot.",
                ephemeral=True,
            )
            return

        previous_id = discord_log_handler.log_channel_id
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

        discord_log_handler.set_log_channel(channel)
        set_log_channel_id(channel.id)
        logger.info(msg=f"Log channel set to {channel} (id={channel.id}) by {interaction.user} (id={interaction.user.id})")

        confirmation = f"Les logs seront désormais envoyés dans {format_channel_mention(channel)}."
        if previous_channel is not None:
            confirmation += f" Ancien salon : {format_channel_mention(previous_channel)}."
        await interaction.response.send_message(confirmation)

    @log_group.command(name="export", description="Exporte les logs récents.")
    @app_commands.describe(
        date="Date des logs à exporter (format : DD/MM/YYYY). Si non spécifié, les logs d'aujourd'hui seront exportés.",
    )
    async def log_export(self, interaction: Interaction, date: str | None = None) -> None:
        """Export recent logs.

        Args:
            interaction (Interaction): The Discord interaction context.
            date (str | None): Date of logs to export in DD/MM/YYYY format. If not specified, exports logs from today.
        """
        log_request(logger, "log.export", interaction, date=date)
        if not await is_in_allowed_channel(logger, interaction):
            return

        if not await check_has_role(logger, interaction, ADMIN_ROLES | {RoleNames.DIGITAL_MANAGER, RoleNames.DIGITAL_POLE}):
            return

        file_log_handler = getattr(self.bot, "file_log_handler", None)
        if not isinstance(file_log_handler, DailyFileHandler):
            logger.error("DailyFileHandler is not initialized on the bot.")
            await interaction.response.send_message(
                "Le gestionnaire de logs n'est pas initialisé sur ce bot.",
                ephemeral=True,
            )
            return

        if date:
            d, m, y = map(int, date.split("/"))
            dt = datetime(y, m, d, tzinfo=PARIS_TZ)
        else:
            dt = datetime.now(tz=PARIS_TZ)

        date_str = dt.strftime("%Y-%m-%d")
        file = await file_log_handler.export_logs(date_str)

        if file is None:
            await interaction.response.send_message(
                f"Aucun fichier de logs trouvé pour la date {dt.strftime('%d/%m/%Y')}.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"Voici les logs du {dt.strftime('%d/%m/%Y')} :",
            file=file,
        )

    # endregion Log Slash Commands Group


@deprecated("Load the cog using `bot.add_cog()` instead.")
async def setup(bot: commands.Bot) -> None:
    """Setup function to load the MessageManagement cog.

    Args:
        bot (commands.Bot): The bot instance.
    """
    await bot.add_cog(LogManagement(bot))
