"""Manages welcome messages and related features."""

from __future__ import annotations

import logging
from typing import Literal
from warnings import deprecated

from discord import HTTPException, Interaction, Member, PermissionOverwrite, TextChannel, app_commands
from discord.ext import commands
from discord.utils import get

from fablabot.guild_config import is_welcome_verify_enabled, set_welcome_verify_enabled
from fablabot.helpers import build_help_message
from fablabot.helpers.constants import ADMIN_ROLES, ErrorMessages, RoleNames
from fablabot.helpers.safe_discord_operations import safe_add_roles, safe_create_text_channel, safe_delete_channel
from fablabot.helpers.utils import ensure_command_context, escape_md

logger = logging.getLogger(__name__)


class WelcomeManagement(commands.Cog):
    """Manages welcome messages and related features.

    Commands:
        - /welcome verify: Toggle automatic welcome channel creation + role assignment for new members.
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
        name="welcome",
        description="Gestion des messages de bienvenue et des fonctionnalités associées.",
    )

    @welcome_group.command(
        name="help",
        description="Affiche l'aide pour les commandes de bienvenue.",
    )
    @app_commands.describe(show="Afficher l'aide publiquement ou non")
    async def welcome_help(self, interaction: Interaction, *, show: bool = False) -> None:
        """Display help for welcome commands.

        Args:
            interaction (Interaction): The Discord interaction object.
            show (bool): Whether to show the help publicly or not.
        """
        help_message = build_help_message(
            "Commandes de gestion des channels de bienvenue",
            "welcome",
            [
                (
                    "verify <enable>",
                    "Activer ou désactiver la création automatique de salons de bienvenue",
                ),
                (
                    "approve <city>",
                    "Valider un nouveau membre et lui attribuer les rôles adaptés à sa ville",
                ),
            ],
        )
        await interaction.response.send_message(help_message, ephemeral=not show)

    @welcome_group.command(
        name="verify",
        description="Active ou désactive la création automatique de salons de bienvenue.",
    )
    @app_commands.describe(enable="Activer ou désactiver la fonctionnalité")
    async def welcome_verify(self, interaction: Interaction, *, enable: bool) -> None:
        """Toggle automatic welcome channel creation and role assignment for new members.

        Args:
            interaction (Interaction): The Discord interaction context.
            enable (bool): Whether to enable or disable the feature.
        """
        if not await ensure_command_context(
            logger,
            "welcome.auto",
            interaction,
            log_details={"enable": enable},
            required_roles=ADMIN_ROLES,
        ):
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

        set_welcome_verify_enabled(interaction.guild.id, enabled=enable)

        await interaction.response.send_message(f"Fonctionnalité de bienvenue automatique : {status}.")

    @welcome_group.command(
        name="approve",
        description="Valide un nouveau membre en lui attribuant les rôles appropriés.",
    )
    @app_commands.describe(city="La ville de l'utilisateur (Paris, Nantes, Montpellier)")
    async def welcome_approve(self, interaction: Interaction, *, city: Literal["Paris", "Nantes", "Montpellier"]) -> None:
        """Approve a new member by assigning appropriate roles.

        Args:
            interaction (Interaction): The Discord interaction context.
            city (Literal["Paris", "Nantes", "Montpellier"]): The user's city.
        """
        if not await ensure_command_context(
            logger,
            "welcome.approve",
            interaction,
            log_details={"city": city},
            required_roles=ADMIN_ROLES,
            enforce_commands_channel=False,
        ):
            return

        channel = interaction.channel
        assert isinstance(channel, TextChannel)
        if not channel.name.startswith("welcome-"):
            logger.warning("The approve command was not used in a welcome channel.")
            await interaction.response.send_message(
                "Cette commande doit être utilisée dans un salon de bienvenue.",
                ephemeral=True,
            )
            return

        assert interaction.guild is not None
        member_name = channel.name.removeprefix("welcome-")
        member = get(interaction.guild.members, name=member_name)
        if member is None:
            logger.warning(f"Member not found by name: {member_name}")
            member = next((u for u in channel.overwrites if isinstance(u, Member)), None)
        if member is None:
            logger.warning(f"Member not found: {member_name}")
            await interaction.response.send_message(
                ErrorMessages.MEMBER_NOT_FOUND.format(member_name=escape_md(member_name)),
                ephemeral=True,
            )
            return

        city_role_name = f"Membre {city}"
        city_role = get(interaction.guild.roles, name=city_role_name)
        if city_role is None:
            await interaction.response.send_message(
                ErrorMessages.ROLE_NOT_FOUND.format(role_name=city_role_name),
                ephemeral=True,
            )
            return

        member_role = get(interaction.guild.roles, name=RoleNames.MEMBER_VERIFIED)
        if member_role is None:
            await interaction.response.send_message(
                ErrorMessages.ROLE_NOT_FOUND.format(role_name=RoleNames.MEMBER_VERIFIED),
                ephemeral=True,
            )
            return

        success, error = await safe_add_roles(logger, member, city_role, member_role, reason="Validation du nouveau membre")
        if not success:
            await interaction.response.send_message(error, ephemeral=True)
            return

        await interaction.response.send_message(
            f"Le membre {member.mention} a été validé avec succès et les rôles ont été attribués.",
            ephemeral=True,
        )

        success, error = await safe_delete_channel(
            logger,
            channel,
            reason="Salon de bienvenue supprimé après validation du membre",
        )
        if not success:
            await interaction.response.send_message(error, ephemeral=True)

    # endregion Welcome Slash Commands Group

    # region ====== Event Listeners ======

    @commands.Cog.listener()
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

        codir_role = get(guild.roles, name=RoleNames.CODIR)
        if codir_role is None:
            logger.error(f"{RoleNames.CODIR} role not found, cannot create welcome channel.")
            return

        overwrites = {
            guild.default_role: PermissionOverwrite(view_channel=False),
            codir_role: PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            member: PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        }
        channel, error = await safe_create_text_channel(
            logger,
            validation_category,
            f"welcome-{member.name}",
            overwrites=overwrites,
            reason=f"Welcome channel for new member {member.name}",
        )
        if not channel or error:
            logger.exception(ErrorMessages.CHANNEL_CREATE_FAILED.format(channel_type=f"salon de bienvenue pour {member.name}"))
            return

        try:
            await channel.send(
                "Comment accéder au serveur discord :\n"
                " - Utilisez `/nick` pour ajouter votre prénom à votre username\n"
                " - Envoyez un message contenant votre adresse mail `~@edu.devinci.fr`"
                " et votre ville (Paris, Nantes, Montepellier) pour recevoir la validation\n"
                "   Assurez vous d 'avoir envoyé votre RI signé sur le formulaire !\n"
                "\n"
                "Vous êtes un ancien ? Envoyez simplement un message précisant que vous en êtes un 🙂",
            )
        except HTTPException:
            logger.exception(f"Failed to send welcome message for {member.name}")
            return

    # endregion Event Listeners


@deprecated("Load the cog using `bot.add_cog()` instead.")
async def setup(bot: commands.Bot) -> None:
    """Sets up the Welcome cog.

    Args:
        bot (commands.Bot): The bot instance.
    """
    await bot.add_cog(WelcomeManagement(bot))
