"""Cog for managing and publishing weekly training sessions (formations)."""

from __future__ import annotations

import asyncio
import contextlib
import csv
from datetime import datetime, timedelta
import io
import json
import logging
from pathlib import Path
from typing import Any, Literal, cast, override
from warnings import deprecated

from discord import Embed, File, Interaction, Member, RawReactionActionEvent, Role, TextChannel, app_commands
from discord.ext import commands, tasks

from fablabot.helpers.constants import PARIS_TZ, ErrorMessages, RoleNames
from fablabot.helpers.formation import (
    Emojis,
    format_current_registrations,
    format_respo_contacts,
    notify_participants_before_formation,
    notify_responsible_before_formation,
    notify_trainer_before_formation,
    parse_date_time,
    render_message,
    send_promotion_dm,
    send_registration_dm,
    send_waitlist_dm,
)
from fablabot.helpers.utils import check_has_role, is_in_allowed_channel, is_valid_emoji, log_request
from fablabot.models.formation import FmMessageDraft, Formation, PublishedMessage, ReactionEvent

logger = logging.getLogger(__name__)


ALLOWED_ROLES = {RoleNames.TRAININGS_MANAGER, RoleNames.ADMIN_TEMP, RoleNames.ADMIN}
FM_STATE_FILE = "data/formations_state.json"
TRAINER_NOTIFICATION_ADVANCE = timedelta(hours=1)
REACTION_LOG_RETENTION = timedelta(days=30)


class FormationManagement(commands.Cog):
    """Hebdo formations management cog (Draft -> Publish -> Export).

    Commands:
        - /fm help: Display help for formation management commands.
        - /fm start: Start/overwrite a draft with an introduction, ending and role to mention.
        - /fm edit_text: Edit the draft introduction and/or ending.
        - /fm add: Add a new formation to the draft.
        - /fm edit: Edit an existing formation in the draft.
        - /fm remove: Remove a formation from the draft.
        - /fm clear: Clear the draft.
        - /fm preview: Preview the draft.
        - /fm publish: Publish the draft to a channel.
        - /fm export: Export the draft as a message.

    Listeners:
        - on_raw_reaction_event: Log reactions (add/remove) on messages published by this cog.

    Attributes:
        fm_group (app_commands.Group): Command group for formation management commands.
    """

    def __init__(self, bot: commands.Bot) -> None:
        """Initialize the FormationManagement cog.

        Args:
            bot (commands.Bot): The bot instance.
        """
        self.bot = bot
        self.state: dict[str, dict[str, Any]] = self._load_state()
        """state structure (par guild):
        ```
        {
          "<guild_id>": {
              "draft": { "intro": str, "fms": [Formation as dict] },
              "published": {
                  "message_id": int,
                  "channel_id": int,
                  "message": { "intro": str, "end": str, "fms": [Formation as dict] }
              },
              "reactions_log": [
                {
                  "message_id": int,
                  "user_id": int,
                  "user_name": str,
                  "emoji": str,
                  "action": Literal["add", "remove"],
                  "ts_iso": str
                }
              ]
          }
        }
        ```
        """
        self._reaction_lock = asyncio.Lock()
        self._pending_update_tasks: dict[int, asyncio.Task] = {}
        self._purge_all_reaction_logs()
        self._check_upcoming_formations.start()
        logger.info("FormationManagement initialized")

    @override
    async def cog_unload(self) -> None:
        """Clean up when the cog is unloaded."""
        self._check_upcoming_formations.cancel()
        for task in list(self._pending_update_tasks.values()):
            try:
                task.cancel()
            except Exception:
                logger.exception("Failed to cancel pending scheduled update task during cog unload.")
        self._pending_update_tasks.clear()
        logger.info("FormationManagement unloaded")

    # region ====== Fm Slash Commands Group ======
    fm_group = app_commands.Group(name="fm", description="Gère les annonces de Formations et les inscriptions.")

    @fm_group.command(name="help", description="Afficher l'aide pour les commandes de gestion des formations.")
    @app_commands.describe(show="Afficher l'aide publiquement ou non")
    async def fm_help(self, interaction: Interaction, show: bool = False) -> None:
        """Display help information for the formation management commands.

        Args:
            interaction (Interaction): The Discord interaction context.
            show (bool): Whether to show the help publicly or not.
        """
        help_message = (
            "**Commandes de gestion des formations :**\n"
            "- `/fm start <intro> <end> <role>` : Démarrer un nouveau brouillon de formation.\n"
            "- `/fm edit_text [intro] [end] [role]` : "
            "Modifier le texte d'introduction et/ou de conclusion du brouillon et le rôle à mentionner.\n"
            "- `/fm add <emoji> <name> <trainer> <date> <hour> <duration> <seats> [description] [excusable]` :"
            " Ajouter une nouvelle formation au brouillon.\n"
            "- `/fm edit <index> [emoji] [name] [trainer] [date] [hour] [duration] [seats] [description] [excusable]` :"
            " Modifier une formation existante dans le brouillon.\n"
            "- `/fm remove <index>` : Supprimer une formation du brouillon.\n"
            "- `/fm clear` : Effacer le brouillon actuel.\n"
            "- `/fm preview` : Prévisualiser le brouillon actuel.\n"
            "- `/fm publish <channel>` : Publier le brouillon dans un salon spécifique.\n"
            "- `/fm export [message_id]` : Exporter le brouillon sous forme de message.\n"
            "- `/fm help [show]` : Affiche cette aide. Par défaut, elle est affichée secrètement.\n"
            "\n"
            "Assurez-vous d'avoir les permissions nécessaires pour utiliser ces commandes."
        )
        await interaction.response.send_message(help_message, ephemeral=not show)

    @fm_group.command(name="start", description="Démarrer/écraser un brouillon avec une introduction.")
    @app_commands.describe(
        intro="Texte d'introduction affiché en tête du message (utilisez \\n pour un saut de ligne)",
        end="Texte de fin affiché en bas du message (utilisez \\n pour un saut de ligne)",
        role="Rôle à mentionner",
    )
    async def fm_start(self, interaction: Interaction, intro: str, end: str, role: Role) -> None:
        """Start a new draft with an introduction.

        Args:
            interaction (Interaction): The Discord interaction context.
            intro (str): The introduction text.
            end (str): The ending text.
            role (Role): The role to mention.
        """
        log_request(logger, "fm.start", interaction, intro=intro)
        if not await is_in_allowed_channel(logger, interaction):
            return
        if not await check_has_role(logger, interaction, ALLOWED_ROLES):
            return
        assert interaction.guild is not None

        emoji_set = {emoji for emoji in interaction.guild.emojis if emoji.name == "dvfl"}
        emoji = emoji_set.pop() if emoji_set else Emojis.LOUDSPEAKER
        header = f"# [FORMATIONS] {emoji}"

        intro_body = intro.replace("\\n", "\n").strip()
        end_body = end.replace("\\n", "\n").strip()

        draft = FmMessageDraft(
            header=header,
            role_id=role.id,
            intro=intro_body,
            fms=[],
            end=end_body,
        )
        self._set_guild_draft(interaction.guild.id, draft)

        content = render_message(
            draft.header,
            draft.role_id,
            draft.intro,
            draft.fms,
            draft.end,
        )
        logger.info(f"Guild {interaction.guild.id} started a new formations draft.")
        await interaction.response.send_message(
            "Brouillon initialisé.\nUtilise **/fm add** pour ajouter des formations. **/fm preview** pour voir le rendu.",
            embed=Embed(
                title="Aperçu brouillon — 0 formation",
                description=f"{content or '_(vide)_'}",
            ),
            ephemeral=True,
        )

    @fm_group.command(name="edit_text", description="Modifier l'introduction et/ou la conclusion du brouillon.")
    @app_commands.describe(
        intro="Nouveau texte d'introduction (laisser vide pour conserver)",
        end="Nouveau texte de conclusion (laisser vide pour conserver)",
        role="Nouveau rôle à mentionner (laisser vide pour conserver)",
    )
    async def fm_edit_text(
        self,
        interaction: Interaction,
        intro: str | None = None,
        end: str | None = None,
        role: Role | None = None,
    ) -> None:
        """Edit the draft introduction and/or ending.

        Args:
            interaction (Interaction): The Discord interaction context.
            intro (str | None, optional): The new introduction text.
            end (str | None, optional): The new ending text.
            role (Role | None, optional): The new role to mention.
        """
        log_request(logger, "fm.edit_text", interaction, intro=intro, end=end, role=role)
        if not await is_in_allowed_channel(logger, interaction):
            return
        if not await check_has_role(logger, interaction, ALLOWED_ROLES):
            return

        if intro is None and end is None and role is None:
            await interaction.response.send_message(
                "Aucun champ à modifier. Fournis au moins `intro`, `end` ou `role`.",
                ephemeral=True,
            )
            return

        assert interaction.guild is not None
        draft = self._get_guild_draft(interaction.guild.id)

        current_intro = draft.intro
        current_end = draft.end
        current_role_id = draft.role_id

        updated_intro = current_intro if intro is None else intro.strip()
        updated_end = current_end if end is None else end.strip()

        updated_intro = updated_intro.replace("\\n", "\n")
        updated_end = updated_end.replace("\\n", "\n")

        updated_role_id = current_role_id
        role_changed = False
        if role is not None:
            updated_role_id = role.id
            role_changed = current_role_id != updated_role_id

        if updated_intro == current_intro and updated_end == current_end and not role_changed:
            await interaction.response.send_message(
                "Aucune modification détectée.",
                ephemeral=True,
            )
            return

        draft = FmMessageDraft(
            header=draft.header,
            role_id=updated_role_id,
            intro=updated_intro,
            fms=draft.fms,
            end=updated_end,
        )
        self._set_guild_draft(interaction.guild.id, draft)

        content = render_message(
            draft.header,
            draft.role_id,
            draft.intro,
            draft.fms,
            draft.end,
        )

        intro_changed = updated_intro != current_intro
        end_changed = updated_end != current_end
        logger.info(
            f"Guild {interaction.guild.id} updated draft intro/end "
            f"(intro_changed={intro_changed}, end_changed={end_changed}, role_changed={role_changed}).",
        )

        await interaction.response.send_message(
            "Brouillon mis à jour.",
            embed=Embed(
                title=f"Aperçu brouillon — {len(draft.fms)} formation(s)",
                description=f"{content or '_(vide)_'}",
            ),
            ephemeral=True,
        )

    @fm_group.command(name="add", description="Ajouter une formation au brouillon (triée automatiquement par date/heure).")
    @app_commands.describe(
        emoji="Émoji unique pour cette FM (ex: 🔧)",
        name="Nom de la formation",
        trainer="Formateur·ice",
        date="Date au format DD/MM/YYYY",
        hour="Heure au format HH:MM (24h)",
        duration="Durée en texte, ce sera affiché comme tel",
        seats="Nombre de places",
        description="Brève description",
        excusable="Absences excusables ?",
    )
    async def fm_add(
        self,
        interaction: Interaction,
        emoji: str,
        name: str,
        trainer: Member,
        date: str,
        hour: str,
        duration: str,
        seats: app_commands.Range[int, 1, 500],
        description: str = "",
        excusable: bool = True,
    ) -> None:
        """Add a formation to the draft (automatically sorted by date/time).

        Args:
            interaction (Interaction): The Discord interaction context.
            emoji (str): The emoji for the formation.
            name (str): The name of the formation.
            trainer (Member): The trainer.
            date (str): The date of the formation.
            hour (str): The hour of the formation.
            duration (str): The duration of the formation in text format.
            seats (app_commands.Range[int, 1, 500]): The number of seats for the formation.
            description (str, optional): The description of the formation. Defaults to "".
            excusable (bool, optional): Whether absences are excusable for this formation. Defaults to True.
        """
        log_request(
            logger,
            "fm.add",
            interaction,
            emoji=emoji,
            name=name,
            description=description,
            trainer=trainer,
            date=date,
            hour=hour,
            duration=duration,
            seats=seats,
            excusable=excusable,
        )
        if not await is_in_allowed_channel(logger, interaction):
            return
        if not await check_has_role(logger, interaction, ALLOWED_ROLES):
            return

        assert interaction.guild is not None
        draft = self._get_guild_draft(interaction.guild.id)

        emoji_clean = emoji.strip()

        if not is_valid_emoji(emoji_clean):
            logger.warning(f"Guild {interaction.guild.id} tried to add formation with invalid emoji: {emoji_clean!r}.")
            await interaction.response.send_message(ErrorMessages.INVALID_EMOJI, ephemeral=True)
            return

        if any(existing.emoji == emoji_clean for existing in draft.fms):
            logger.warning(f"Guild {interaction.guild.id} tried to add formation with duplicate emoji {emoji_clean!r}.")
            await interaction.response.send_message(ErrorMessages.EMOJI_ALREADY_USED, ephemeral=True)
            return

        try:
            start_dt = parse_date_time(date, hour, PARIS_TZ)
        except Exception:
            logger.warning(f"Guild {interaction.guild.id} tried to add formation with invalid date/hour: {date} {hour}.")
            await interaction.response.send_message(
                "Date/heure invalides. Exemples: date `15/09/2025`, heure `18:08`.",
                ephemeral=True,
            )
            return

        fm = Formation(
            emoji=emoji_clean,
            name=name.strip(),
            trainer_mention=trainer.mention,
            start_iso=start_dt.isoformat(),
            duration=duration.strip(),
            seats=int(seats),
            description=description.strip(),
            excusable=excusable,
        )

        fms = list(draft.fms)
        fms.append(fm)
        fms.sort(key=lambda x: x.start_dt)

        draft = FmMessageDraft(
            header=draft.header,
            role_id=draft.role_id,
            intro=draft.intro,
            fms=fms,
            end=draft.end,
        )
        self._set_guild_draft(interaction.guild.id, draft)

        preview = render_message(
            draft.header,
            draft.role_id,
            draft.intro,
            draft.fms,
            draft.end,
        )
        logger.info(f"Guild {interaction.guild.id} added formation {fm.name!r} ({fm.start_iso}) to draft.")
        await interaction.response.send_message(
            "Formation ajoutée & brouillon mis à jour (trié). "
            "Utilise **/fm add** pour ajouter d'autres formations. **/fm preview** pour voir le rendu.",
            embed=Embed(
                title=f"Aperçu brouillon — {len(fms)} formation(s)",
                description=f"{preview or '_(vide)_'}",
            ),
            ephemeral=True,
        )

    @fm_group.command(name="edit", description="Modifier une formation existante (champs optionnels).")
    @app_commands.describe(
        index="Position de la FM dans l'aperçu trié (1..n)",
        emoji="Nouvel émoji pour cette formation",
        name="Nouveau nom de la formation",
        trainer="Nouveau formateur ou nouvelle formatrice",
        date="Nouvelle date au format DD/MM/YYYY",
        hour="Nouvelle heure au format HH:MM (24h)",
        duration="Nouvelle durée affichée",
        seats="Nouveau nombre de places",
        description="Nouvelle description",
        excusable="Absences excusables ?",
    )
    async def fm_edit(
        self,
        interaction: Interaction,
        index: app_commands.Range[int, 1, 1000],
        emoji: str | None = None,
        name: str | None = None,
        trainer: Member | None = None,
        date: str | None = None,
        hour: str | None = None,
        duration: str | None = None,
        seats: app_commands.Range[int, 1, 500] | None = None,
        description: str | None = None,
        excusable: bool | None = None,
    ) -> None:
        """Edit a formation in the draft while keeping other entries untouched.

        Args:
            interaction (Interaction): The Discord interaction context.
            index (app_commands.Range[int, 1, 1000]): The index of the formation to edit (1-based).
            emoji (str | None, optional): New emoji for the formation. Defaults to None.
            name (str | None, optional): New name for the formation. Defaults to None.
            trainer (Member | None, optional): New trainer for the formation. Defaults to None.
            date (str | None, optional): New date for the formation. Defaults to None.
            hour (str | None, optional): New hour for the formation. Defaults to None.
            duration (str | None, optional): New duration for the formation. Defaults to None.
            seats (app_commands.Range[int, 1, 500] | None, optional): New number of seats for the formation. Defaults to None.
            description (str | None, optional): New description for the formation. Defaults to None.
            excusable (bool | None, optional): Whether absences are excusable for this formation. Defaults to None.
        """
        log_request(
            logger,
            "fm.edit",
            interaction,
            index=index,
            emoji=emoji,
            name=name,
            trainer=trainer,
            date=date,
            hour=hour,
            duration=duration,
            seats=seats,
            description=description,
            excusable=excusable,
        )
        if not await is_in_allowed_channel(logger, interaction):
            return
        if not await check_has_role(logger, interaction, ALLOWED_ROLES):
            return

        assert interaction.guild is not None
        draft = self._get_guild_draft(interaction.guild.id)
        fms = sorted(draft.fms, key=lambda x: x.start_dt)

        if index > len(fms):
            logger.warning(
                f"Guild {interaction.guild.id} tried to edit out-of-bounds formation index {index}.",
            )
            await interaction.response.send_message(
                ErrorMessages.INDEX_OUT_OF_BOUNDS.format(count=len(fms)),
                ephemeral=True,
            )
            return

        original = fms[index - 1]

        new_emoji = original.emoji
        if emoji is not None:
            candidate = emoji.strip()
            if not is_valid_emoji(candidate):
                logger.warning(f"Guild {interaction.guild.id} tried to edit formation with invalid emoji: {candidate!r}.")
                await interaction.response.send_message(ErrorMessages.INVALID_EMOJI, ephemeral=True)
                return
            if any(i != index - 1 and fm.emoji == candidate for i, fm in enumerate(fms)):
                logger.warning(f"Guild {interaction.guild.id} tried to reuse emoji {candidate} while editing formation.")
                await interaction.response.send_message(ErrorMessages.EMOJI_ALREADY_USED, ephemeral=True)
                return
            new_emoji = candidate

        new_name = original.name if name is None else name.strip()
        if not new_name:
            logger.warning(f"Guild {interaction.guild.id} provided an empty name while editing a formation.")
            await interaction.response.send_message(ErrorMessages.INVALID_NAME, ephemeral=True)
            return

        new_trainer = original.trainer_mention if trainer is None else trainer.mention
        new_duration = original.duration if duration is None else duration.strip()
        if not new_duration:
            logger.warning(f"Guild {interaction.guild.id} provided an empty duration while editing a formation.")
            await interaction.response.send_message(ErrorMessages.INVALID_DURATION, ephemeral=True)
            return

        new_seats = original.seats if seats is None else seats

        new_start_iso = original.start_iso
        if date is not None or hour is not None:
            date_part = date.strip() if date is not None else original.start_dt.strftime("%d/%m/%Y")
            hour_part = hour.strip() if hour is not None else original.start_dt.strftime("%H:%M")
            try:
                new_start_iso = parse_date_time(date_part, hour_part, PARIS_TZ).isoformat()
            except Exception:
                logger.warning(
                    f"Guild {interaction.guild.id} provided invalid date/hour while"
                    f" editing formation: {date_part} {hour_part}.",
                )
                await interaction.response.send_message(
                    "Date/heure invalides. Exemples: date `15/09/2025`, heure `18:08`.",
                    ephemeral=True,
                )
                return

        new_description = original.description if description is None else description.strip()

        new_excusable = original.excusable if excusable is None else excusable

        updated = Formation(
            emoji=new_emoji,
            name=new_name,
            trainer_mention=new_trainer,
            start_iso=new_start_iso,
            duration=new_duration,
            seats=new_seats,
            description=new_description,
            excusable=new_excusable,
        )

        fms[index - 1] = updated
        fms.sort(key=lambda x: x.start_dt)

        draft = FmMessageDraft(
            header=draft.header,
            role_id=draft.role_id,
            intro=draft.intro,
            fms=fms,
            end=draft.end,
        )
        self._set_guild_draft(interaction.guild.id, draft)

        preview = render_message(
            draft.header,
            draft.role_id,
            draft.intro,
            draft.fms,
            draft.end,
        )
        new_position = fms.index(updated) + 1

        logger.info(
            f"Guild {interaction.guild.id} edited formation {original.name!r} -> "
            f"{updated.name!r} (index {index} → {new_position}).",
        )

        await interaction.response.send_message(
            f"Mise à jour: {updated.emoji} {updated.name} (position {new_position}).",
            embed=Embed(
                title=f"Aperçu brouillon — {len(fms)} formation(s)",
                description=f"{preview or '_(vide)_'}",
            ),
            ephemeral=True,
        )

    @fm_group.command(name="remove", description="Retirer une formation du brouillon par son index (1..n).")
    @app_commands.describe(index="Position de la FM dans l'aperçu trié (1..n)")
    async def fm_remove(self, interaction: Interaction, index: app_commands.Range[int, 1, 1000]) -> None:
        """Remove a formation from the draft by its index (1..n).

        Args:
            interaction (Interaction): The Discord interaction context.
            index (app_commands.Range[int, 1, 1000]): The index of the formation to remove (1-based).
        """
        log_request(logger, "fm.remove", interaction, index=index)
        if not await is_in_allowed_channel(logger, interaction):
            return
        if not await check_has_role(logger, interaction, ALLOWED_ROLES):
            return

        assert interaction.guild is not None
        draft = self._get_guild_draft(interaction.guild.id)
        fms = sorted(draft.fms, key=lambda x: x.start_dt)

        if index > len(fms):
            logger.warning(f"Guild {interaction.guild.id} tried to remove out-of-bounds formation index {index}.")
            await interaction.response.send_message(ErrorMessages.INDEX_OUT_OF_BOUNDS.format(count=len(fms)), ephemeral=True)
            return

        removed = fms.pop(index - 1)

        draft = FmMessageDraft(
            header=draft.header,
            role_id=draft.role_id,
            intro=draft.intro,
            fms=fms,
            end=draft.end,
        )
        self._set_guild_draft(interaction.guild.id, draft)

        preview = render_message(
            draft.header,
            draft.role_id,
            draft.intro,
            draft.fms,
            draft.end,
        )
        logger.info(f"Guild {interaction.guild.id} removed formation {removed.name!r} ({removed.start_iso}) from draft.")
        await interaction.response.send_message(
            f"Supprimé: {removed.emoji} {removed.name}",
            embed=Embed(
                title=f"Aperçu brouillon — {len(fms)} formation(s)",
                description=f"{preview or '_(vide)_'}",
            ),
            ephemeral=True,
        )

    @fm_group.command(name="clear", description="Vider le brouillon courant (intro et fin conservées).")
    async def fm_clear(self, interaction: Interaction) -> None:
        """Clear the current draft (introduction and ending are preserved).

        Args:
            interaction (Interaction): The Discord interaction context.
        """
        log_request(logger, "fm.clear", interaction)
        if not await is_in_allowed_channel(logger, interaction):
            return
        if not await check_has_role(logger, interaction, ALLOWED_ROLES):
            return

        assert interaction.guild is not None
        draft = self._get_guild_draft(interaction.guild.id)

        draft = FmMessageDraft(
            header=draft.header,
            role_id=draft.role_id,
            intro=draft.intro,
            fms=[],
            end=draft.end,
        )
        self._set_guild_draft(interaction.guild.id, draft)

        content = render_message(
            draft.header,
            draft.role_id,
            draft.intro,
            draft.fms,
            draft.end,
        )
        logger.info(f"Guild {interaction.guild.id} cleared the formations draft.")
        await interaction.response.send_message(
            "Brouillon vidé (intro et fin conservées). "
            "Utilise **/fm add** pour ajouter des formations. **/fm preview** pour voir le rendu.",
            embed=Embed(
                title="Aperçu brouillon — 0 formation",
                description=f"{content or '_(vide)_'}",
            ),
            ephemeral=True,
        )

    @fm_group.command(name="preview", description="Afficher l'aperçu du brouillon dans ce salon.")
    async def fm_preview(self, interaction: Interaction) -> None:
        """Show the draft preview in the current channel.

        Args:
            interaction (Interaction): The Discord interaction context.
        """
        log_request(logger, "fm.preview", interaction)
        if not await is_in_allowed_channel(logger, interaction):
            return
        if not await check_has_role(logger, interaction, ALLOWED_ROLES):
            return

        await interaction.response.defer(thinking=True)
        assert interaction.guild is not None
        draft = self._get_guild_draft(interaction.guild.id)
        fms = sorted(draft.fms, key=lambda x: x.start_dt)

        content = render_message(
            draft.header,
            draft.role_id,
            draft.intro,
            fms,
            draft.end,
        )
        logger.info(f"Guild {interaction.guild.id} previewed the formations draft.")
        await interaction.followup.send(
            content=f"{content or '_(vide)_'}",
            embed=Embed(description="Utilise **/fm publish** pour le publier."),
        )

    @fm_group.command(name="publish", description="Publier le message dans un salon d'annonces (réactions auto-ajoutées).")
    @app_commands.describe(channel="Salon d'annonces cible")
    async def fm_publish(self, interaction: Interaction, channel: TextChannel) -> None:
        """Publish the message in an announcement channel (auto-added reactions).

        Args:
            interaction (Interaction): The Discord interaction context.
            channel (TextChannel): The target announcement channel.
        """
        log_request(logger, "fm.publish", interaction, channel=channel)
        if not await is_in_allowed_channel(logger, interaction):
            return
        if not await check_has_role(logger, interaction, ALLOWED_ROLES):
            return

        assert interaction.guild is not None
        draft = self._get_guild_draft(interaction.guild.id)
        if not draft.fms:
            logger.warning(f"Guild {interaction.guild.id} tried to publish empty formations draft.")
            await interaction.response.send_message(ErrorMessages.DRAFT_EMPTY, ephemeral=True)
            return

        await interaction.response.defer(thinking=True)

        fms = sorted(draft.fms, key=lambda x: x.start_dt)
        content = render_message(
            draft.header,
            draft.role_id,
            draft.intro,
            fms,
            draft.end,
        )

        try:
            msg = await channel.send(content, suppress_embeds=True)
        except Exception:
            logger.exception(f"Guild {interaction.guild.id} failed to publish the formations draft in {channel!r}.")
            await interaction.followup.send(
                "Erreur pendant la publication du message.",
                ephemeral=True,
            )
            return

        with contextlib.suppress(Exception):
            await msg.publish()

        success_reactions = 0
        for fm in fms:
            try:
                await msg.add_reaction(fm.emoji)
                success_reactions += 1
            except Exception:
                logger.exception(
                    f"Guild {interaction.guild.id} failed to add reaction {fm.emoji!r} "
                    f"for formation {fm.name!r} in published message.",
                )

        pub_msg = FmMessageDraft(
            header=draft.header,
            role_id=draft.role_id,
            intro=draft.intro,
            fms=fms,
            end=draft.end,
        )

        self._set_last_published_in_guild(
            interaction.guild.id,
            PublishedMessage(
                message_id=msg.id,
                channel_id=channel.id,
                message=pub_msg,
            ),
        )

        logger.info(f"Guild {interaction.guild.id} published the formations draft in {channel}.")
        await interaction.followup.send(
            f"Message publié dans {channel.mention} (ID: `{msg.id}`) avec "
            f"{success_reactions}/{len(fms)} réaction(s) ajoutée(s).",
        )

    @fm_group.command(name="export", description="Exporter la liste des membres ayant (dé)réagi aux émojis des FMs.")
    @app_commands.describe(
        message_id="ID du message publié (optionnel si dernière publication)",
    )
    async def fm_export(self, interaction: Interaction, message_id: str | None = None) -> None:
        """Export the list of members who reacted (added/removed) to the formation emojis.

        Args:
            interaction (Interaction): The Discord interaction context.
            message_id (str | None, optional): The ID of the published message. Defaults to None.
        """
        log_request(logger, "fm.export", interaction, message_id=message_id)
        if not await is_in_allowed_channel(logger, interaction):
            return
        if not await check_has_role(logger, interaction, ALLOWED_ROLES):
            return

        assert interaction.guild is not None
        pub = self._get_last_published_in_guild(interaction.guild.id)
        if not pub and not message_id:
            logger.warning(f"Guild {interaction.guild.id} tried to export reactions without published message or ID.")
            await interaction.response.send_message(ErrorMessages.NO_PUBLISHED_MESSAGE, ephemeral=True)
            return

        target_message_id = int(message_id) if message_id else pub.message_id if pub else None
        if not target_message_id:
            logger.error(f"Guild {interaction.guild.id} has inconsistent published message data: {pub}")
            await interaction.response.send_message(ErrorMessages.INCONSISTENT_PUBLISHED_DATA, ephemeral=True)
            return

        fm_by_emoji: dict[str, dict[str, Any]] = {}
        if pub:
            for fm in pub.message.fms:
                fm_by_emoji[fm.emoji] = {"name": fm.name, "seats": fm.seats}

        history = self._get_reaction_history(interaction.guild.id, target_message_id)
        history_csv = io.StringIO()
        hist_writer = csv.writer(history_csv, lineterminator="\n")
        hist_writer.writerow(["timestamp_iso", "action", "emoji", "formation_name", "user_name", "user_id", "formation_seats"])
        for ev in history:
            hist_writer.writerow(
                [
                    ev.ts_iso,
                    ev.action,
                    ev.emoji,
                    fm_by_emoji.get(ev.emoji, {}).get("name", ""),
                    ev.user_name or "",
                    ev.user_id,
                ],
            )

        if message_id:
            await interaction.response.send_message(
                content="Export du message spécifié.",
                file=File(
                    fp=io.BytesIO(history_csv.getvalue().encode(encoding="utf-8")),
                    filename="formations_reactions_log.csv",
                ),
            )
            return

        await interaction.response.defer(thinking=True)

        fms = pub.message.fms if pub else []

        reg_text, reg_file = format_current_registrations(fms)
        if reg_file:
            await interaction.followup.send(
                reg_text,
                files=[
                    File(reg_file, filename="inscriptions_ordre_inscription.txt"),
                    File(
                        fp=io.BytesIO(history_csv.getvalue().encode(encoding="utf-8")),
                        filename="formations_reactions_log.csv",
                    ),
                ],
            )
            return

        await interaction.followup.send(
            reg_text,
            files=[
                File(
                    fp=io.BytesIO(history_csv.getvalue().encode(encoding="utf-8")),
                    filename="formations_reactions_log.csv",
                ),
            ],
        )

    # endregion Fm Slash Commands Group

    # region ====== Event Listeners ======

    @commands.Cog.listener(name="on_raw_reaction_add")
    @commands.Cog.listener(name="on_raw_reaction_remove")
    async def on_raw_reaction_event(self, payload: RawReactionActionEvent) -> None:
        """Log reaction updates on the last published formations message.

        Args:
            payload (RawReactionActionEvent): The raw reaction event payload.
        """
        if payload.guild_id is None:
            return

        member = payload.member
        if member and member.bot:
            return

        async with self._reaction_lock:
            pub = self._get_last_published_in_guild(payload.guild_id)
            if not pub or payload.message_id != pub.message_id:
                return
            emoji_str = str(payload.emoji)
            fm_emojis = {fm.emoji for fm in pub.message.fms}
            if fm_emojis and emoji_str not in fm_emojis:
                return

            formation = next((fm for fm in pub.message.fms if fm.emoji == emoji_str), None)
            if formation:
                now = datetime.now(PARIS_TZ)
                registration_deadline = formation.start_dt + timedelta(minutes=20)
                if now > registration_deadline:
                    logger.info(
                        f"Ignoring reaction {emoji_str} for formation {formation.name!r} "
                        f"from user {payload.user_id} - registration closed.",
                    )
                    return

            member_name = member.name if member else None
            reaction_event = ReactionEvent(
                message_id=payload.message_id,
                user_id=payload.user_id,
                user_name=member_name,
                emoji=emoji_str,
                action=cast("Literal['add', 'remove']", payload.event_type.removeprefix("REACTION_").lower()),
                ts_iso=datetime.now(PARIS_TZ).isoformat(timespec="seconds"),
            )

            self._log_reaction(payload.guild_id, reaction_event)

            self._schedule_update(payload.guild_id)

    # endregion Event Listeners

    # region ====== Background Tasks ======

    @tasks.loop(minutes=5)
    async def _check_upcoming_formations(self) -> None:
        """Check for formations starting soon and notify trainers/responsibles (Paris timezone)."""
        logger.debug("Checking for upcoming formations to notify trainers and responsibles (Paris time).")
        now = datetime.now(PARIS_TZ)
        window_tolerance = timedelta(minutes=5)
        notification_window_start = now + TRAINER_NOTIFICATION_ADVANCE - window_tolerance
        notification_window_end = now + TRAINER_NOTIFICATION_ADVANCE + window_tolerance
        start_window_start = now - window_tolerance
        start_window_end = now + window_tolerance

        for guild_id_str in self.state:
            guild_id = int(guild_id_str)
            guild = self.bot.get_guild(guild_id)
            if not guild:
                continue

            pub = self._get_last_published_in_guild(guild_id)
            if not pub:
                continue

            fms = list(pub.message.fms)
            if not fms:
                continue

            for fm in fms:
                if fm.notified_at_start:
                    continue

                send_contacts = format_respo_contacts(guild)
                if not fm.notified_hour_before and notification_window_start <= fm.start_dt <= notification_window_end:
                    await notify_trainer_before_formation(
                        guild,
                        fm,
                        send_contacts,
                        moment="hour_before",
                    )
                    await notify_responsible_before_formation(
                        guild,
                        fm,
                        moment="hour_before",
                    )
                    await notify_participants_before_formation(
                        guild,
                        fm,
                        send_contacts,
                    )

                    fm.notified_hour_before = True
                    continue

                if start_window_start <= fm.start_dt <= start_window_end:
                    await notify_trainer_before_formation(
                        guild,
                        fm,
                        send_contacts,
                        moment="start",
                    )
                    await notify_responsible_before_formation(
                        guild,
                        fm,
                        moment="start",
                    )

                    fm.notified_at_start = True

            updated_pub_msg = FmMessageDraft(
                header=pub.message.header,
                role_id=pub.message.role_id,
                intro=pub.message.intro,
                fms=fms,
                end=pub.message.end,
            )
            self._set_last_published_in_guild(
                guild_id,
                PublishedMessage(
                    message_id=pub.message_id,
                    channel_id=pub.channel_id,
                    message=updated_pub_msg,
                ),
            )

    @_check_upcoming_formations.before_loop
    async def _before_check_upcoming_formations(self) -> None:
        """Wait for the bot to be ready before starting the background task."""
        await self.bot.wait_until_ready()
        logger.info("Formation notification task started")

    # endregion Background Tasks

    # region ====== Helpers ======
    # -- State --

    @staticmethod
    def _load_state() -> dict[str, dict[str, Any]]:
        """Load the state from the JSON file.

        Returns:
            dict[str, dict[str, Any]]: The loaded state, or an empty dictionary if the file does not exist or an error occurs.
        """
        try:
            with open(FM_STATE_FILE, encoding="utf-8") as f:
                state = json.load(f)
        except FileNotFoundError:
            logger.debug(f"Formations state file {FM_STATE_FILE} not found; starting with empty state.")
            return {}
        except json.JSONDecodeError:
            logger.exception(f"Failed to decode formations state from {FM_STATE_FILE}.")
            return {}
        except Exception:
            logger.exception(f"Unexpected error while loading formations state from {FM_STATE_FILE}.")
            return {}
        logger.debug("Loaded formations state.")
        return state

    @staticmethod
    def _save_state(state: dict[str, dict[str, Any]]) -> None:
        """Save the state to the JSON file.

        Args:
            state (dict[str, dict[str, Any]]): The state to save.
        """
        try:
            Path(Path(FM_STATE_FILE).parent).mkdir(parents=True, exist_ok=True)
            with open(FM_STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
        except OSError:
            logger.exception(f"Failed to persist formations state to {FM_STATE_FILE}")
        except TypeError:
            logger.exception("Invalid data encountered while serializing formations state.")
        except Exception:
            logger.exception(f"Unexpected error while saving formations state to {FM_STATE_FILE}.")
        else:
            logger.debug(f"Saved formations state to {FM_STATE_FILE}.")

    def _get_guild_state(self, guild_id: int) -> dict[str, Any]:
        """Get state for a specific guild.

        Args:
            guild_id (int): The ID of the guild.

        Returns:
            dict[str, Any]: The state of the guild.
        """
        key = str(guild_id)
        if key not in self.state:
            logger.debug(f"Initializing state container for guild {guild_id}.")
            self.state[key] = {}
        return self.state[key]

    def _set_guild_state(self, guild_id: int, payload: dict[str, Any]) -> None:
        """Set the state for a specific guild.

        Args:
            guild_id (int): The ID of the guild.
            payload (dict[str, Any]): The state of the guild.
        """
        self.state[str(guild_id)] = payload
        self._save_state(self.state)

    def _get_guild_draft(self, guild_id: int) -> FmMessageDraft:
        """Get the draft state for a specific guild.

        Args:
            guild_id (int): The ID of the guild.

        Returns:
            Draft: The draft of the guild.
        """
        guild_state = self._get_guild_state(guild_id)
        if "draft" not in guild_state:
            logger.debug(f"Initializing draft state for guild {guild_id}.")
            self._set_guild_state(guild_id, guild_state)
            guild_state["draft"] = {"header": "", "role_id": 0, "intro": "", "fms": [], "end": ""}
        draft_dict = guild_state["draft"]
        return FmMessageDraft.from_dict(draft_dict)

    def _set_guild_draft(self, guild_id: int, draft: FmMessageDraft) -> None:
        """Set the draft for a specific guild.

        Args:
            guild_id (int): The ID of the guild.
            draft (Draft): The draft of the guild.
        """
        guild_state = self._get_guild_state(guild_id)
        guild_state["draft"] = draft.to_dict()
        self._set_guild_state(guild_id, guild_state)

    def _get_last_published_in_guild(self, guild_id: int) -> PublishedMessage | None:
        """Get the last published state for a specific guild.

        Args:
            guild_id (int): The ID of the guild.

        Returns:
            PublishedMessage | None: The last published state of the guild, or None if not found.
        """
        guild_state = self._get_guild_state(guild_id)
        pub_dict = guild_state.get("published")
        if not pub_dict:
            logger.warning(f"No published formations data stored for guild {guild_id}.")
            return None
        return PublishedMessage.from_dict(pub_dict)

    def _set_last_published_in_guild(self, guild_id: int, published: PublishedMessage) -> None:
        """Set the last published state for a specific guild.

        Args:
            guild_id (int): The ID of the guild.
            published (PublishedMessage): The published state of the guild.
        """
        guild_state = self._get_guild_state(guild_id)
        guild_state["published"] = published.to_dict()
        self._set_guild_state(guild_id, guild_state)

    # -- Reaction Logs & Updates --

    def _log_reaction(self, guild_id: int, reaction_event: ReactionEvent) -> None:
        """Log a reaction event with Paris timezone.

        Args:
            guild_id (int): The ID of the guild.
            reaction_event (ReactionEvent): The event to log.
        """
        self._purge_all_reaction_logs()
        guild_state = self._get_guild_state(guild_id)
        log = [ReactionEvent.from_dict(entry) for entry in guild_state.get("reactions_log") or []]
        if reaction_event.user_name is None:
            reaction_event.user_name = self._get_last_known_user_name(log, reaction_event.user_id)
        log.append(reaction_event)
        guild_state["reactions_log"] = [event.to_dict() for event in log]
        logger.debug(
            f"Logged {reaction_event.action} reaction for guild {guild_id} message {reaction_event.message_id} "
            f"user {reaction_event.user_id} with emoji {reaction_event.emoji} (Paris time: {reaction_event.ts_iso}).",
        )
        self._set_guild_state(guild_id, guild_state)

    def _purge_all_reaction_logs(self) -> None:
        """Apply retention to all guild reaction logs and persist changes."""
        changed = False
        removed_total = 0
        for guild_state in self.state.values():
            raw_log = guild_state.get("reactions_log")
            if not raw_log:
                continue
            log = [ReactionEvent.from_dict(entry) for entry in raw_log]
            filtered = self._purge_reaction_log(list(log))
            if len(filtered) != len(log):
                removed_total += len(log) - len(filtered)
                guild_state["reactions_log"] = [event.to_dict() for event in filtered]
                changed = True
        if changed:
            logger.debug(f"Purged {removed_total} reaction log entries across guilds.")
            self._save_state(self.state)

    @staticmethod
    def _purge_reaction_log(log: list[ReactionEvent]) -> list[ReactionEvent]:
        """Remove reaction log entries older than the retention window (Paris timezone).

        Args:
            log (list[ReactionEvent]): The reaction log to purge.

        Returns:
            list[ReactionEvent]: The filtered reaction log.
        """
        cutoff = datetime.now(PARIS_TZ) - REACTION_LOG_RETENTION
        filtered: list[ReactionEvent] = []
        for entry in log:
            ts_iso = entry.ts_iso
            if not ts_iso:
                filtered.append(entry)
                continue
            try:
                ts = datetime.fromisoformat(ts_iso).astimezone(PARIS_TZ)
            except Exception:
                filtered.append(entry)
                continue
            if ts >= cutoff:
                filtered.append(entry)
        return filtered

    @staticmethod
    def _get_last_known_user_name(log: list[ReactionEvent], user_id: int) -> str | None:
        """Get the last known user name from the log.

        Args:
            log (list[ReactionEvent]): The reaction log.
            user_id (int): The ID of the user.

        Returns:
            str | None: The last known user name, or None if not found.
        """
        for entry in reversed(log):
            if entry.user_id == user_id and entry.user_name:
                return entry.user_name
        return None

    def _get_reaction_history(self, guild_id: int, message_id: int) -> list[ReactionEvent]:
        """Get the reaction history for a specific message in a guild.

        Args:
            guild_id (int): The ID of the guild.
            message_id (int): The ID of the message.

        Returns:
            list[ReactionEvent]: The reaction history for the message.
        """
        guild_state = self._get_guild_state(guild_id)
        raw_log = guild_state.get("reactions_log", []) or []
        history = [ev for entry in raw_log if (ev := ReactionEvent.from_dict(entry)).message_id == message_id]
        history.sort(key=lambda ev: ev.ts_iso or "")
        logger.debug(f"Loaded {len(history)} reaction events for guild {guild_id} message {message_id}.")
        return history

    def _schedule_update(self, guild_id: int, delay: float = 2.0) -> None:
        """Schedule a debounced update for a guild's published message.

        If an update is already scheduled and not finished, this is a no-op. The
        scheduled coroutine waits `delay` seconds before invoking
        `_update_published_message`, coalescing rapid events.

        Args:
            guild_id (int): The ID of the guild.
            delay (float, optional): The delay in seconds before performing the update. Defaults to 2.0.
        """
        existing = self._pending_update_tasks.get(guild_id)
        if existing and not existing.done():
            return

        async def _delayed() -> None:
            try:
                await asyncio.sleep(delay)
                await self._update_published_message(guild_id)
            except asyncio.CancelledError:
                logger.debug(f"Scheduled update for guild {guild_id} was cancelled.")
            except Exception:
                logger.exception(f"Error during scheduled update for guild {guild_id}.")
            finally:
                self._pending_update_tasks.pop(guild_id, None)

        task = asyncio.create_task(_delayed())
        self._pending_update_tasks[guild_id] = task

    async def _update_published_message(self, guild_id: int) -> None:
        """Update the published message to reflect current registrations.

        Args:
            guild_id (int): The ID of the guild.
        """
        pub = self._get_last_published_in_guild(guild_id)
        if not pub:
            logger.debug(f"No published formations message recorded for guild {guild_id}; skipping update.")
            return
        guild = self.bot.get_guild(guild_id)
        channel_id = pub.channel_id
        message_id = pub.message_id
        if not guild or not channel_id or not message_id:
            logger.warning(
                f"Incomplete published formations state for guild {guild_id} (channel={channel_id}, message={message_id}).",
            )
            return
        channel = guild.get_channel(channel_id)
        assert isinstance(channel, TextChannel)

        try:
            msg = await channel.fetch_message(message_id)
        except Exception:
            logger.exception(f"Failed to fetch message {message_id} in channel {channel_id}.")
            return
        logger.debug(f"Fetched published message {message_id} in channel {channel_id} for guild {guild_id}.")

        pub_msg = pub.message
        header = pub_msg.header
        role_id = pub_msg.role_id
        intro = pub_msg.intro
        end = pub_msg.end
        fms = list(pub_msg.fms)
        tracked_emojis = {fm.emoji for fm in fms if fm.emoji}

        if not header or not role_id or not intro or not end or not fms:
            logger.warning(
                f"Published formations payload incomplete for guild {guild_id}; "
                f"header={bool(header)} role_id={bool(role_id)} intro={bool(intro)} end={bool(end)} formations={len(fms)}.",
            )
            return

        history = self._get_reaction_history(guild_id, message_id)

        last_add: dict[tuple[str, int], datetime] = {}
        for event in history:
            if event.action != "add":
                continue
            emoji = str(event.emoji)
            user_id = event.user_id
            if not emoji or user_id is None or emoji not in tracked_emojis:
                continue
            try:
                ts = datetime.fromisoformat(event.ts_iso).astimezone(PARIS_TZ)
            except (TypeError, ValueError):
                ts = datetime.min.replace(tzinfo=PARIS_TZ)
            key = (emoji, int(user_id))
            if key not in last_add or ts > last_add[key]:
                last_add[key] = ts

        reactions_snapshot: dict[str, dict[int, str]] = {}
        for reaction in msg.reactions:
            emoji_str = str(reaction.emoji)
            if emoji_str not in tracked_emojis:
                continue
            async for user in reaction.users():
                if user.bot:
                    continue
                reactions_snapshot.setdefault(emoji_str, {})[user.id] = f"{user.display_name} ({user.name})"

        waitlist_notifications: list[tuple[int, str, int]] = []
        promotion_notifications: list[tuple[int, Formation]] = []
        registration_notifications: list[tuple[int, Formation]] = []

        for fm in fms:
            prev_registered_ids = {entry.get("user_id") for entry in fm.registered_users if entry.get("user_id") is not None}
            prev_waitlisted_ids = {entry.get("user_id") for entry in fm.waitlisted_users if entry.get("user_id") is not None}

            current_users = reactions_snapshot.get(fm.emoji, {})
            current_logged_user = {uid: username for uid, username in current_users.items() if (fm.emoji, uid) in last_add}
            ordered_users = sorted(
                (
                    (uid, last_add.get((fm.emoji, uid), datetime.min.replace(tzinfo=PARIS_TZ)), username)
                    for uid, username in current_logged_user.items()
                ),
                key=lambda item: (item[1], item[0]),
            )

            registered: list[dict[str, Any]] = []
            waitlisted: list[dict[str, Any]] = []
            for position, (uid, ts, username) in enumerate(ordered_users):
                entry = {
                    "user_id": uid,
                    "username": username,
                    "ts_iso": ts.isoformat(timespec="seconds"),
                }
                if position < max(fm.seats, 0):
                    registered.append(entry)
                    if uid in prev_waitlisted_ids:
                        promotion_notifications.append((uid, fm))
                    elif uid not in prev_registered_ids:
                        registration_notifications.append((uid, fm))
                else:
                    waitlisted.append(entry)
                    if uid not in prev_waitlisted_ids:
                        waitlist_notifications.append((uid, fm.name, len(waitlisted)))
            fm.registered_users = registered
            fm.waitlisted_users = waitlisted

        updated_message_payload = FmMessageDraft(
            header=header,
            role_id=role_id,
            intro=intro,
            fms=fms,
            end=end,
        )
        self._set_last_published_in_guild(
            guild_id,
            PublishedMessage(
                message_id=pub.message_id,
                channel_id=pub.channel_id,
                message=updated_message_payload,
            ),
        )

        content = render_message(header, role_id, intro, fms, end)

        try:
            await msg.edit(content=content, suppress=True)
        except Exception:
            logger.exception(f"Failed to edit message {msg.id} in channel {msg.channel.id}.")
        else:
            logger.info(f"Updated published formations message {msg.id} in channel {channel_id} for guild {guild_id}.")

        contacts: str = format_respo_contacts(guild)

        if promotion_notifications:
            logger.info(f"Dispatching {len(promotion_notifications)} waitlist promotion notification(s) for guild {guild_id}.")
            for user_id, fm in promotion_notifications:
                await send_promotion_dm(guild, user_id, fm, contacts)
        else:
            logger.debug(f"No waitlist promotion notifications for guild {guild_id}.")

        if registration_notifications:
            logger.info(f"Dispatching {len(registration_notifications)} new registration notification(s) for guild {guild_id}.")
            for user_id, fm in registration_notifications:
                await send_registration_dm(guild, user_id, fm, contacts)
        else:
            logger.debug(f"No new registration notifications for guild {guild_id}.")

        if waitlist_notifications:
            logger.info(f"Dispatching {len(waitlist_notifications)} waitlist notification(s) for guild {guild_id}.")
            for user_id, fm_name, waitlist_index in waitlist_notifications:
                await send_waitlist_dm(guild, user_id, fm_name, waitlist_index, contacts)
        else:
            logger.debug(f"No new waitlist notifications for guild {guild_id}.")

    # endregion Helpers


@deprecated("Load the cog using `bot.add_cog()` instead.")
async def setup(bot: commands.Bot) -> None:
    """Setup function to load the Formations cog.

    Args:
        bot (commands.Bot): The bot instance.
    """
    await bot.add_cog(FormationManagement(bot))
