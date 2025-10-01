"""Manages welcome messages and related features."""

from __future__ import annotations

import logging
from warnings import deprecated

from discord import (
    HTTPException,
    Interaction,
    Member,
    PermissionOverwrite,
    app_commands,
)
from discord.ext import commands
from discord.utils import get

from fablabot.guild_config import is_welcome_verify_enabled, set_welcome_verify_enabled

from .utils import check_has_role, is_in_allowed_channel, log_request

logger = logging.getLogger(__name__)

ADMIN_ROLES = {
    "Admin -temp-",
    "Administrateur",
    "Président.e",
    "Vice-Président.e",
    "Secrétaire Général",
}


class Welcome(commands.Cog):
    """Manages welcome messages and related features.

    Commands:
        - /welcome auto: Toggle automatic welcome channel creation + role assignment for new members.
        - /welcome approve: Approve the new member.

    Listeners:
        - on_member_join: Create a welcome channel.

    Attributes:
        welcome_group (app_commands.Group): Command group for welcome-related commands.
    """

    def __init__(self, bot: commands.Bot) -> None:
        """Initialize the Welcome cog.

        Args:
            bot (commands.Bot): The bot instance.
        """
        self.bot = bot
        logger.info("Welcome cog initialized")

    # region ====== Welcome Slash Commands Group ======

    welcome_group = app_commands.Group(
        name="welcome", description="Gestion des messages de bienvenue et des fonctionnalités associées."
    )

    @welcome_group.command(name="help", description="Affiche l'aide pour les commandes de bienvenue.")
    async def welcome_help(self, interaction: Interaction) -> None:
        """Display help for welcome commands.

        Args:
            interaction (Interaction): The Discord interaction object.
        """
        help_message = (
            "**Commandes de gestion des channels de bienvenue :**\n"
            "- `/welcome auto <enable>`: Activer/désactiver la création automatique de salons de bienvenue et l'attribution de rôles pour les nouveaux membres.\n"
            "- `/welcome approve <city>`: Valider le nouveau membre. Ajoute les rôles appropriés en fonction de la ville.\n"
            "- `/welcome help`: Affiche cette aide pour les commandes de bienvenue.\n"
            "\n"
            "Assurez-vous d'avoir les permissions nécessaires pour utiliser ces commandes."
        )
        await interaction.response.send_message(help_message, ephemeral=True)

    @welcome_group.command(
        name="auto",
        description="Active ou désactive la création automatique de salons de bienvenue et l'attribution de rôles pour les nouveaux membres.",
    )
    @app_commands.describe(enable="Activer ou désactiver la fonctionnalité")
    async def welcome_auto(self, interaction: Interaction, enable: bool) -> None:
        """Toggle automatic welcome channel creation and role assignment for new members.

        Args:
            interaction (Interaction): The Discord interaction context.
            enable (bool): Whether to enable or disable the feature.
        """
        log_request(logger, "welcome.auto", interaction, enable=enable)
        if not await is_in_allowed_channel(logger, interaction):
            return

        assert isinstance(interaction.user, Member)
        if not await check_has_role(logger, interaction, ADMIN_ROLES):
            return

        assert interaction.guild is not None
        previous_state = is_welcome_verify_enabled(interaction.guild.id)
        status = "activée" if enable else "désactivée"
        if previous_state == enable:
            await interaction.response.send_message(
                f"La fonctionnalité de bienvenue automatique est déjà {status}.",
                ephemeral=True,
            )
            return

        set_welcome_verify_enabled(interaction.guild.id, enable)

        await interaction.response.send_message(f"Fonctionnalité de bienvenue automatique : {status}.")

    # endregion Welcome Slash Commands Group

    # region ====== Event Listeners ======

    async def on_member_join(self, member: Member) -> None:
        """Event listener for when a member joins the guild.

        Args:
            member (Member): The member that joined.
        """
        logger.debug(f"Member joined: {member} ({member.id})")
        is_enabled = is_welcome_verify_enabled(member.guild.id)
        if not is_enabled:
            logger.debug(f"Auto welcome is disabled for guild {member.guild.id}, skipping welcome channel creation.")
            return

        guild = member.guild
        validation_category = get(guild.categories, name="Validation")
        if validation_category is None:
            logger.error("Validation category not found, cannot create welcome channel.")
            return

        codir_role = get(guild.roles, name="CoDir")
        if codir_role is None:
            logger.error("CoDir role not found, cannot create welcome channel.")
            return

        overwrites = {
            guild.default_role: PermissionOverwrite(view_channel=False),
            codir_role: PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            member: PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        }
        try:
            channel = await guild.create_text_channel(
                name=f"welcome-{member.name}",
                category=validation_category,
                overwrites=overwrites,
                reason=f"Welcome channel for new member {member.name}",
            )
        except HTTPException:
            logger.exception("Failed to create welcome channel")
            return

        message = await channel.send("TODO")
        await message.add_reaction("🇵")  # Paris
        await message.add_reaction("🇳")  # Nantes
        # - auto select de paris ou nantes par la personne via émoji + envoi de son email et/ou infos nécessaires pour vous dans le chat
        # - ajout du role membre à la mano par vous
        # - dès que la personne à le role membre et un role entre paris et nantes le channel éphémère est supprimé

    # endregion Event Listeners


@deprecated("Load the cog using `bot.add_cog()` instead.")
async def setup(bot: commands.Bot) -> None:
    """Sets up the Welcome cog.

    Args:
        bot (commands.Bot): The bot instance.
    """
    await bot.add_cog(Welcome(bot))
