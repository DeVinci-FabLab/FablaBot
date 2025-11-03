"""Suggestion management commands for Discord Bot. Provides slash commands for sending suggestions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import logging
from warnings import deprecated

from discord import (
    Embed,
    Guild,
    Interaction,
    Member,
    app_commands,
)
from discord.ext import commands
from discord.utils import get

from fablabot.cogs.helpers import (
    PARIS_TZ,
    RoleNames,
    can_dm_user,
    get_members_by_role,
)

logger = logging.getLogger(__name__)

ANONYMOUS_ICON_URL = "https://e7.pngegg.com/pngimages/84/165/png-clipart-united-states-avatar-organization-information-user-avatar-service-computer-wallpaper-thumbnail.png"


@dataclass
class SuggestionConfig:
    """Configuration for a suggestion type.

    Attributes:
        embed_title (str): The title of the suggestion embed.
        role_name (str | None): The role name to get responsible members.
        channel_name (str | None): The channel name to send the suggestion to.
        embed_color (int): The color of the embed. Defaults to 0x00AAFF.
        success_message (str): The success message to send to the user.
        error_message (str): The error message when no recipients are configured.
    """

    embed_title: str
    """The title of the suggestion embed."""
    role_name: str | None
    """The role name to get responsible members."""
    channel_name: str | None
    """The channel name to send the suggestion to."""
    success_message: str
    """The success message to send to the user."""
    error_message: str
    """The error message when no recipients are configured."""
    embed_color: int = 0x00AAFF


class SuggestionManagement(commands.Cog):
    """Cog to register suggestion management commands.

    Commands:
        - /suggest_to help: Display help for feature suggestion commands.
        - /suggest_to codir: Suggest something to the CoDir.
        - /suggest_to bureau: Suggest something to the Bureau.
        - /suggest_to pole_communication: Suggest something to the communication pole.
        - /suggest_to pole_event: Suggest something to the events pole.
        - /suggest_to pole_formation: Suggest a formation to the formations pole.
        - /suggest_to pole_numerique: Suggest a new IT feature to the IT pole.
        - /suggest_to pole_partenariat: Suggest something to the partnerships pole.
        - /suggest_to pole_projet: Suggest something to the project pole.

    Attributes:
        suggest_group (app_commands.Group): Command group for feature suggestion commands.
    """

    def __init__(self, bot: commands.Bot) -> None:
        """Initialize the cog and register its command groups.

        Args:
            bot (commands.Bot): The bot instance.
        """
        self.bot = bot
        logger.info("SuggestionManagement initialized")

    # region ====== Suggest Slash Commands Group ======

    suggest_group = app_commands.Group(name="suggest_to", description="Suggestions de fonctionnalités")

    CODIR_SUGGESTION_CONFIG = SuggestionConfig(
        embed_title="Nouvelle suggestion pour le CoDir",
        role_name=None,
        channel_name="codir-general",
        success_message="Suggestion envoyée au CoDir. Merci !",
        error_message="Aucun salon 'codir-general' n'est configuré pour recevoir les suggestions.",
    )

    BUREAU_SUGGESTION_CONFIG = SuggestionConfig(
        embed_title="Nouvelle suggestion pour le Bureau",
        role_name=None,
        channel_name="bureau-general",
        success_message="Suggestion envoyée au Bureau. Merci !",
        error_message="Aucun salon 'bureau-general' n'est configuré pour recevoir les suggestions.",
    )

    COMMUNICATION_SUGGESTION_CONFIG = SuggestionConfig(
        embed_title="Nouvelle suggestion pour le Pôle Communication",
        role_name=RoleNames.RESPO_COMMUNICATION,
        channel_name="pole-communication",
        success_message="Suggestion envoyée au Pôle Communication. Merci !",
        error_message="Aucun·e Respo Communication et aucun salon 'pole-communication'"
        " ne sont configurés pour recevoir les suggestions.",
    )

    EVENT_SUGGESTION_CONFIG = SuggestionConfig(
        embed_title="Nouvelle suggestion pour le Pôle Événements",
        role_name=RoleNames.RESPO_EVENT,
        channel_name="pole-event",
        success_message="Suggestion envoyée au Pôle Events. Merci !",
        error_message="Aucun·e Respo Events et aucun salon 'pole-events' ne sont configurés pour recevoir les suggestions.",
    )

    TRAINING_SUGGESTION_CONFIG = SuggestionConfig(
        embed_title="Nouvelle suggestion de formation",
        role_name=RoleNames.RESPO_FORMATION,
        channel_name="pole-formation",
        success_message="Suggestion envoyée au Pôle Formation. Merci !",
        error_message="Aucun·e Respo Formation et aucun salon 'pole-formation'"
        " ne sont configurés pour recevoir les suggestions.",
    )

    IT_SUGGESTION_CONFIG = SuggestionConfig(
        embed_title="Nouvelle suggestion pour le Pôle Numérique",
        role_name=RoleNames.RESPO_NUMERIQUE,
        channel_name="pole-numerique",
        success_message="Suggestion envoyée au Pôle Numérique. Merci !",
        error_message="Aucun·e Respo Numérique et aucun salon 'pole-numerique'"
        " ne sont configurés pour recevoir les suggestions.",
    )

    PARTERSHIPS_SUGGESTION_CONFIG = SuggestionConfig(
        embed_title="Nouvelle suggestion pour le Pôle Partenariat",
        role_name=RoleNames.RESPO_PARTENARIAT,
        channel_name="pole-partenariat",
        success_message="Suggestion envoyée au Pôle Partenariat. Merci !",
        error_message="Aucun·e Respo Partenariat et aucun salon 'pole-partenariat'"
        " ne sont configurés pour recevoir les suggestions.",
    )

    PROJECTS_SUGGESTION_CONFIG = SuggestionConfig(
        embed_title="Nouvelle suggestion pour le Pôle Projets",
        role_name=RoleNames.RESPO_PROJETS,
        channel_name=None,
        success_message="Suggestion envoyée au Pôle Projets. Merci !",
        error_message="Aucun·e Respo Projets n'est configuré·e pour recevoir les suggestions.",
    )

    @suggest_group.command(name="help", description="Affiche l'aide pour les commandes de suggestions de fonctionnalités.")
    @app_commands.describe(show="Afficher l'aide publiquement ou non")
    async def suggest_help(self, interaction: Interaction, show: bool = False) -> None:
        """Display help for feature suggestion commands.

        Args:
            interaction (Interaction): The Discord interaction context.
            show (bool): Whether to show the help publicly or not.
        """
        help_text = (
            "**Commandes de suggestions de fonctionnalités :**\n"
            "- `/suggest_to codir <suggestion> [anonymous]` : Suggérer quelque chose au CoDir.\n"
            "- `/suggest_to bureau <suggestion> [anonymous]` : Suggérer quelque chose au Bureau.\n"
            "- `/suggest_to pole_communication <suggestion> [anonymous]` : Suggérer quelque chose au pôle communication.\n"
            "- `/suggest_to pole_event <suggestion> [anonymous]` : Suggérer quelque chose au pôle événements.\n"
            "- `/suggest_to pole_formation <formation> [anonymous]` : Demander une formation.\n"
            "- `/suggest_to pole_numerique <suggestion> [anonymous]` : "
            "Suggérer une nouvelle fonctionnalité IT (Pour le bot discord, un site, etc.).\n"
            "- `/suggest_to pole_partenariat <suggestion> [anonymous]` : Suggérer quelque chose au pôle partenariats.\n"
            "- `/suggest_to pole_projet <suggestion> [anonymous]` : Suggérer quelque chose au pôle projets.\n"
            "- `/suggest_to help [show]`: Affiche cette aide. Par défaut, elle est affichée secrètement.\n"
            "\n"
            "N'hésitez pas à suggérer des idées pour qu'on puisse s'améliorer !"
        )
        await interaction.response.send_message(help_text, ephemeral=not show)

    @suggest_group.command(name="codir", description="Suggérer quelque chose au CoDir.")
    @app_commands.describe(suggestion="Détails de la suggestion", anonymous="Souhaitez-vous rester anonyme ?")
    async def suggest_to_codir(self, interaction: Interaction, suggestion: str, anonymous: bool = False) -> None:
        """Suggest something to the CoDir.

        Args:
            interaction (Interaction): The Discord interaction context.
            suggestion (str): The details of the suggested improvement.
            anonymous (bool): Whether the suggestion is anonymous or not. Defaults to False.
        """
        await self._handle_suggestion(interaction, suggestion, anonymous, self.CODIR_SUGGESTION_CONFIG, "suggest.to_codir")

    @suggest_group.command(name="bureau", description="Suggérer quelque chose au bureau.")
    @app_commands.describe(suggestion="Détails de la suggestion", anonymous="Souhaitez-vous rester anonyme ?")
    async def suggest_to_bureau(self, interaction: Interaction, suggestion: str, anonymous: bool = False) -> None:
        """Suggest something to the bureau.

        Args:
            interaction (Interaction): The Discord interaction context.
            suggestion (str): The details of the suggested improvement.
            anonymous (bool): Whether the suggestion is anonymous or not. Defaults to False.
        """
        await self._handle_suggestion(interaction, suggestion, anonymous, self.BUREAU_SUGGESTION_CONFIG, "suggest.to_bureau")

    @suggest_group.command(name="pole_communication", description="Suggérer quelque chose au pôle communication.")
    @app_commands.describe(suggestion="Détails de la suggestion", anonymous="Souhaitez-vous rester anonyme ?")
    async def suggest_to_communication_pole(self, interaction: Interaction, suggestion: str, anonymous: bool = False) -> None:
        """Suggest something to the communication pole.

        Args:
            interaction (Interaction): The Discord interaction context.
            suggestion (str): The details of the suggested improvement.
            anonymous (bool): Whether the suggestion is anonymous or not. Defaults to False.
        """
        await self._handle_suggestion(
            interaction, suggestion, anonymous, self.COMMUNICATION_SUGGESTION_CONFIG, "suggest.to_communication"
        )

    @suggest_group.command(name="pole_event", description="Suggérer quelque chose au pôle events.")
    @app_commands.describe(suggestion="Détails de la suggestion", anonymous="Souhaitez-vous rester anonyme ?")
    async def suggest_to_event_pole(self, interaction: Interaction, suggestion: str, anonymous: bool = False) -> None:
        """Suggest something to the events pole.

        Args:
            interaction (Interaction): The Discord interaction context.
            suggestion (str): The details of the suggested improvement.
            anonymous (bool): Whether the suggestion is anonymous or not. Defaults to False.
        """
        await self._handle_suggestion(interaction, suggestion, anonymous, self.EVENT_SUGGESTION_CONFIG, "suggest.to_events")

    @suggest_group.command(name="pole_formation", description="Suggérer une formation au pôle formations.")
    @app_commands.describe(formation="Détails de la formation demandée", anonymous="Souhaitez-vous rester anonyme ?")
    async def suggest_to_training_pole(self, interaction: Interaction, formation: str, anonymous: bool = False) -> None:
        """Suggest a formation to the training pole.

        Args:
            interaction (Interaction): The Discord interaction context.
            formation (str): The details of the suggested formation.
            anonymous (bool): Whether the suggestion is anonymous or not. Defaults to False.
        """
        await self._handle_suggestion(interaction, formation, anonymous, self.TRAINING_SUGGESTION_CONFIG, "suggest.formation")

    @suggest_group.command(name="pole_numerique", description="Suggérer une fonctionnalité IT au pôle numérique.")
    @app_commands.describe(it_feature="Détails de la suggestion", anonymous="Souhaitez-vous rester anonyme ?")
    async def suggest_to_digital_pole(self, interaction: Interaction, it_feature: str, anonymous: bool = False) -> None:
        """Suggest a new IT feature to the digital pole.

        Args:
            interaction (Interaction): The Discord interaction context.
            it_feature (str): The details of the suggested IT feature.
            anonymous (bool): Whether the suggestion is anonymous or not. Defaults to False.
        """
        await self._handle_suggestion(interaction, it_feature, anonymous, self.IT_SUGGESTION_CONFIG, "suggest.it_feature")

    @suggest_group.command(name="pole_partenariat", description="Suggérer quelque chose au pôle partenariats.")
    @app_commands.describe(suggestion="Détails de la suggestion", anonymous="Souhaitez-vous rester anonyme ?")
    async def suggest_to_partnerships_pole(self, interaction: Interaction, suggestion: str, anonymous: bool = False) -> None:
        """Suggest something to the partnerships pole.

        Args:
            interaction (Interaction): The Discord interaction context.
            suggestion (str): The details of the suggested improvement.
            anonymous (bool): Whether the suggestion is anonymous or not. Defaults to False.
        """
        await self._handle_suggestion(
            interaction, suggestion, anonymous, self.PARTERSHIPS_SUGGESTION_CONFIG, "suggest.to_partenariats"
        )

    @suggest_group.command(name="pole_projet", description="Suggérer quelque chose au pôle projets.")
    @app_commands.describe(suggestion="Détails de la suggestion", anonymous="Souhaitez-vous rester anonyme ?")
    async def suggest_to_projects_pole(self, interaction: Interaction, suggestion: str, anonymous: bool = False) -> None:
        """Suggest something to the projects pole.

        Args:
            interaction (Interaction): The Discord interaction context.
            suggestion (str): The details of the suggested improvement.
            anonymous (bool): Whether the suggestion is anonymous or not. Defaults to False.
        """
        await self._handle_suggestion(interaction, suggestion, anonymous, self.PROJECTS_SUGGESTION_CONFIG, "suggest.to_projets")

    # endregion Suggest Slash Commands Group

    # region ====== Helpers ======

    async def _handle_suggestion(
        self,
        interaction: Interaction,
        suggestion_text: str,
        anonymous: bool,
        config: SuggestionConfig,
        command_name: str,
    ) -> None:
        """Handle a suggestion command.

        Args:
            interaction: The Discord interaction context.
            suggestion_text: The raw suggestion text.
            anonymous: Whether the suggestion is anonymous or not.
            config: The suggestion configuration.
            command_name: The command name for logging (e.g., "suggest.formation").
        """
        logger.info(
            f"[{command_name}]: user={interaction.user if not anonymous else 'Anonymous'!r} suggestion_text={suggestion_text!r}"
        )

        cleaned_text = suggestion_text.strip()
        if not cleaned_text:
            await interaction.followup.send("Le texte de la suggestion ne peut pas être vide.", ephemeral=True)
            return

        assert interaction.guild is not None
        author = interaction.user
        assert isinstance(author, Member)

        embed = Embed(
            title=config.embed_title,
            description=suggestion_text,
            color=config.embed_color,
            timestamp=datetime.now(PARIS_TZ),
        )
        if not anonymous:
            embed.set_author(name=author.display_name, icon_url=author.display_avatar.url)
            embed.add_field(name="Utilisateur·ice", value=author.mention, inline=False)
        else:
            embed.set_author(
                name="Anonymous",
                icon_url=ANONYMOUS_ICON_URL,
            )

        success = await self._send_suggestion(interaction.guild, embed, config, author.id if not anonymous else None)

        if not success:
            await interaction.response.send_message(config.error_message, ephemeral=True)
            return

        logger.info(
            f"Guild {interaction.guild.id} user "
            f"{interaction.user.id if not anonymous else 'Anonymous'} made a suggestion via {command_name}."
        )
        await interaction.response.send_message(config.success_message, ephemeral=True)

    async def _send_suggestion(
        self,
        guild: Guild,
        embed: Embed,
        config: SuggestionConfig,
        user_id: int | None,
    ) -> bool:
        """Send a suggestion to responsible members and channel.

        Args:
            guild (Guild): The guild where the suggestion is made.
            embed (Embed): The suggestion embed to send.
            config (SuggestionConfig): The suggestion configuration.
            user_id (int | None): The ID of the user making the suggestion.

        Returns:
            True if at least one recipient received the suggestion, False otherwise.
        """
        responsibles: set[Member] = get_members_by_role(logger, guild, role=config.role_name) if config.role_name else set()
        channel = get(guild.text_channels, name=config.channel_name) if config.channel_name else None

        if not responsibles and not channel:
            logger.error(
                f"Guild {guild.id} has no {config.role_name} responsibles and no {config.channel_name} channel configured."
            )
            return False

        for responsible in responsibles:
            if await can_dm_user(responsible):
                try:
                    await responsible.send(embed=embed)
                except Exception:
                    logger.exception(
                        f"Failed to send suggestion from user {user_id or 'Anonymous'} to responsible {responsible.id}."
                    )

        if channel:
            try:
                await channel.send(embed=embed)
            except Exception:
                logger.exception(f"Failed to send suggestion from user {user_id or 'Anonymous'} to channel {channel.id}.")

        return True

    # endregion Helpers


@deprecated("Load the cog using `bot.add_cog()` instead.")
async def setup(bot: commands.Bot) -> None:
    """Setup function to load the SuggestionManagement cog.

    Args:
        bot (commands.Bot): The bot instance.
    """
    await bot.add_cog(SuggestionManagement(bot))
