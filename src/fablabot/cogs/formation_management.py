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
from typing import Any, override
from warnings import deprecated

from discord import Embed, File, Interaction, Member, RawReactionActionEvent, Role, TextChannel, app_commands
from discord.ext import commands, tasks

from fablabot.helpers.constants import PARIS_TZ, ErrorMessages, RoleNames
from fablabot.helpers.formation import (
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
from fablabot.models import FmCommand, FmMessageDraft, Formation, PublishedMessage, ReactionAction, ReactionEvent
from fablabot.ui import fmui

logger = logging.getLogger(__name__)


ALLOWED_ROLES = {RoleNames.TRAININGS_MANAGER, RoleNames.ADMIN_TEMP, RoleNames.ADMIN}
FM_STATE_FILE = "data/formations_state.json"
TRAINER_NOTIFICATION_ADVANCE = timedelta(hours=1)
REACTION_LOG_RETENTION = timedelta(days=30)


class FormationManagement(commands.Cog):
    """Hebdo formations management cog (Draft -> Publish -> Export).

    Commands:
        - /fm help: Display help for formation management commands.
        - /fm start: Start/overwrite a draft with an introduction, ending and role to mention (uses modal).
        - /fm edit_text: Edit the draft introduction and/or ending (uses modal if text=True).
        - /fm add: Add a new formation to the draft (uses modal for text inputs).
        - /fm edit: Edit an existing formation in the draft (uses modal for text inputs).
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
    async def fm_help(self, interaction: Interaction, *, show: bool = False) -> None:
        """Display help information for the formation management commands.

        Args:
            interaction (Interaction): The Discord interaction context.
            show (bool): Whether to show the help publicly or not.
        """
        help_message = (
            "**Commandes de gestion des formations :**\n"
            "- `/fm start <role>` : Démarrer un nouveau brouillon de formation (modal pour intro/conclusion).\n"
            "- `/fm edit_text [role] [text]` : "
            "Modifier le texte d'introduction et/ou de conclusion du brouillon et le rôle à mentionner.\n"
            "- `/fm add <emoji> <trainer> <date> <hour> <duration> <seats>` : "
            "Ajouter une nouvelle formation au brouillon (modal pour nom/description/excusable).\n"
            "- `/fm edit` : Modifier une formation existante dans le brouillon (view et modal).\n"
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
    @app_commands.describe(role="Rôle à mentionner")
    async def fm_start(self, interaction: Interaction, *, role: Role) -> None:
        """Start a new draft with an introduction.

        Args:
            interaction (Interaction): The Discord interaction context.
            role (Role): The role to mention.
        """
        log_request(logger, "fm.start", interaction, role=role)
        if not await is_in_allowed_channel(logger, interaction):
            return
        if not await check_has_role(logger, interaction, ALLOWED_ROLES):
            return

        await interaction.response.send_modal(fmui.StartFmModal(self, role.id))

    @fm_group.command(name="edit_text", description="Modifier l'introduction et/ou la conclusion du brouillon.")
    @app_commands.describe(
        role="Nouveau rôle à mentionner (laisser vide pour conserver)",
        text="Modifier l'introduction ou la conclusion",
    )
    async def fm_edit_text(self, interaction: Interaction, *, role: Role | None = None, text: bool = False) -> None:
        """Edit the draft introduction and/or ending.

        Args:
            interaction (Interaction): The Discord interaction context.
            role (Role | None, optional): The new role to mention. Defaults to None.
            text (bool, optional): Whether to modify the introduction or conclusion text. Defaults to False.
        """
        log_request(logger, "fm.edit_text", interaction, role=role)
        if not await is_in_allowed_channel(logger, interaction):
            return
        if not await check_has_role(logger, interaction, ALLOWED_ROLES):
            return

        if role is None and text is False:
            await interaction.response.send_message(
                "Aucun champ à modifier. Fournis au moins `text` ou `role`.",
                ephemeral=True,
            )
            return

        assert interaction.guild is not None
        draft = self.get_guild_draft(interaction.guild.id)

        if text:
            await interaction.response.send_modal(
                fmui.EditTextModal(self, role.id if role else draft.role_id, draft.intro, draft.end),
            )
            return

        assert role is not None
        if draft.role_id == role.id:
            await interaction.response.send_message("Aucune modification détectée.", ephemeral=True)
            return

        draft.role_id = role.id
        self.set_guild_draft(interaction.guild.id, draft)

        content = render_message(draft)

        logger.info(f"Guild {interaction.guild.id} updated draft role_id to {role.id}.")

        await interaction.response.send_message(
            "Brouillon mis à jour.",
            embed=Embed(title=f"Aperçu brouillon — {len(draft.fms)} formation(s)", description=f"{content or '_(vide)_'}"),
            ephemeral=True,
        )

    @fm_group.command(name="add", description="Ajouter une formation au brouillon (triée automatiquement par date/heure).")
    @app_commands.describe(
        emoji="Émoji unique pour cette FM (ex: 🔧)",
        trainer="Formateur·ice",
        date="Date au format DD/MM/YYYY",
        hour="Heure au format HH:MM (24h)",
        duration="Durée en texte, ce sera affiché tel quel",
        seats="Nombre de places",
        excusable="Absences excusables ?",
    )
    async def fm_add(
        self,
        interaction: Interaction,
        *,
        emoji: str,
        trainer: Member,
        date: str,
        hour: str,
        duration: str,
        seats: app_commands.Range[int, 1, 500],
        excusable: bool = True,
    ) -> None:
        """Add a formation to the draft (automatically sorted by date/time).

        Args:
            interaction (Interaction): The Discord interaction context.
            emoji (str): Unique emoji for this formation (e.g., 🔧).
            trainer (Member): The trainer.
            date (str): Date in DD/MM/YYYY format.
            hour (str): Time in HH:MM (24h) format.
            duration (str): Duration as text, displayed as is.
            seats (app_commands.Range[int, 1, 500]): Number of seats.
            excusable (bool, optional): Whether absences are excusable for this formation. Defaults to True.
        """
        log_request(
            logger,
            "fm.add",
            interaction,
            emoji=emoji,
            trainer=trainer,
            date=date,
            hour=hour,
            duration=duration,
            seats=seats,
        )
        if not await is_in_allowed_channel(logger, interaction):
            return
        if not await check_has_role(logger, interaction, ALLOWED_ROLES):
            return

        assert interaction.guild is not None
        draft = self.get_guild_draft(interaction.guild.id)

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

        await interaction.response.send_modal(
            fmui.AddFmModal(self, emoji_clean, trainer.mention, start_dt.isoformat(), duration.strip(), int(seats), excusable),
        )

    @fm_group.command(name="edit", description="Modifier une formation existante.")
    async def fm_edit(self, interaction: Interaction) -> None:
        """Edit a formation in the draft using an interactive view system.

        Args:
            interaction (Interaction): The Discord interaction context.
        """
        log_request(logger, "fm.edit", interaction)
        if not await is_in_allowed_channel(logger, interaction):
            return
        if not await check_has_role(logger, interaction, ALLOWED_ROLES):
            return

        assert interaction.guild is not None
        draft = self.get_guild_draft(interaction.guild.id)

        if not draft.fms:
            logger.warning(f"Guild {interaction.guild.id} tried to edit formation but draft is empty.")
            await interaction.response.send_message(ErrorMessages.DRAFT_EMPTY, ephemeral=True)
            return

        await interaction.response.send_message(
            "Sélectionne la formation à modifier :",
            view=fmui.SelectFormationView(self, draft.fms, FmCommand.EDIT),
            ephemeral=True,
        )

    @fm_group.command(name="remove", description="Retirer une formation du brouillon par son index (1..n).")
    async def fm_remove(self, interaction: Interaction) -> None:
        """Remove a formation from the draft by its index (1..n).

        Args:
            interaction (Interaction): The Discord interaction context.
        """
        log_request(logger, "fm.remove", interaction)
        if not await is_in_allowed_channel(logger, interaction):
            return
        if not await check_has_role(logger, interaction, ALLOWED_ROLES):
            return

        assert interaction.guild is not None
        draft = self.get_guild_draft(interaction.guild.id)

        if not draft.fms:
            logger.warning(f"Guild {interaction.guild.id} tried to remove formation but draft is empty.")
            await interaction.response.send_message(ErrorMessages.DRAFT_EMPTY, ephemeral=True)
            return

        removed = fms.pop(index - 1)

        draft = FmMessageDraft(
            header=draft.header,
            role_id=draft.role_id,
            intro=draft.intro,
            fms=fms,
            end=draft.end,
        )
        self.set_guild_draft(interaction.guild.id, draft)

        preview = render_message(draft)
        logger.info(f"Guild {interaction.guild.id} removed formation {removed.name!r} ({removed.start_iso}) from draft.")
        await interaction.response.send_message(
            "Sélectionne la formation à supprimer :",
            view=fmui.SelectFormationView(self, draft.fms, FmCommand.REMOVE),
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
        draft = self.get_guild_draft(interaction.guild.id)

        draft = FmMessageDraft(
            header=draft.header,
            role_id=draft.role_id,
            intro=draft.intro,
            fms=[],
            end=draft.end,
        )
        self.set_guild_draft(interaction.guild.id, draft)

        content = render_message(draft)
        logger.info(f"Guild {interaction.guild.id} cleared the formations draft.")
        await interaction.response.send_message(
            "Brouillon vidé (intro et fin conservées). "
            "Utilise **/fm add** pour ajouter des formations. **/fm preview** pour voir le rendu.",
            embed=Embed(title="Aperçu brouillon — 0 formation", description=f"{content or '_(vide)_'}"),
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
        draft = self.get_guild_draft(interaction.guild.id)

        content = render_message(draft)
        logger.info(f"Guild {interaction.guild.id} previewed the formations draft.")
        await interaction.followup.send(
            content=f"{content or '_(vide)_'}",
            embed=Embed(description="Utilise **/fm publish** pour le publier."),
        )

    @fm_group.command(name="publish", description="Publier le message dans un salon d'annonces (réactions auto-ajoutées).")
    @app_commands.describe(channel="Salon d'annonces cible")
    async def fm_publish(self, interaction: Interaction, *, channel: TextChannel) -> None:
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
        draft = self.get_guild_draft(interaction.guild.id)
        if not draft.fms:
            logger.warning(f"Guild {interaction.guild.id} tried to publish empty formations draft.")
            await interaction.response.send_message(ErrorMessages.DRAFT_EMPTY, ephemeral=True)
            return

        await interaction.response.defer(thinking=True)

        content = render_message(draft)

        try:
            msg = await channel.send(content, suppress_embeds=True)
        except Exception:
            logger.exception(f"Guild {interaction.guild.id} failed to publish the formations draft in {channel!r}.")
            await interaction.followup.send("Erreur pendant la publication du message.", ephemeral=True)
            return

        success_reactions = 0
        for fm in draft.fms:
            try:
                await msg.add_reaction(fm.emoji)
                success_reactions += 1
            except Exception:
                logger.exception(
                    f"Guild {interaction.guild.id} failed to add reaction {fm.emoji!r} "
                    f"for formation {fm.name!r} in published message.",
                )

        self._set_last_published_in_guild(
            interaction.guild.id,
            PublishedMessage(
                message_id=msg.id,
                channel_id=channel.id,
                message=draft,
            ),
        )

        logger.info(f"Guild {interaction.guild.id} published the formations draft in {channel}.")
        await interaction.followup.send(
            f"Message publié dans {channel.mention} (ID: `{msg.id}`) avec "
            f"{success_reactions}/{len(draft.fms)} réaction(s) ajoutée(s).",
        )

        await asyncio.sleep(24 * 60 * 60)
        with contextlib.suppress(Exception):
            await msg.publish()

        logger.info(f"Guild {interaction.guild.id} published the formations message as announcement.")
        await interaction.followup.send("Message publié en mode annonce.")

    @fm_group.command(name="export", description="Exporter la liste des membres ayant (dé)réagi aux émojis des FMs.")
    @app_commands.describe(message_id="ID du message publié (optionnel si dernière publication)")
    async def fm_export(self, interaction: Interaction, *, message_id: str | None = None) -> None:
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
                action=ReactionAction[payload.event_type.removeprefix("REACTION_")],
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

            send_contacts: str = format_respo_contacts(guild)

            for fm in fms:
                if fm.notified_at_start:
                    continue

                if not fm.notified_hour_before and notification_window_start <= fm.start_dt <= notification_window_end:
                    await notify_trainer_before_formation(guild, fm, send_contacts, moment="hour_before")
                    await notify_responsible_before_formation(guild, fm, moment="hour_before")
                    await notify_participants_before_formation(guild, fm, send_contacts)

                    fm.notified_hour_before = True
                    continue

                if start_window_start <= fm.start_dt <= start_window_end:
                    await notify_trainer_before_formation(guild, fm, send_contacts, moment="start")
                    await notify_responsible_before_formation(guild, fm, moment="start")

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

    def get_guild_draft(self, guild_id: int) -> FmMessageDraft:
        """Get the draft state for a specific guild.

        Args:
            guild_id (int): The ID of the guild.

        Returns:
            FmMessageDraft: The draft of the guild.
        """
        guild_state = self._get_guild_state(guild_id)
        if "draft" not in guild_state:
            logger.debug(f"Initializing draft state for guild {guild_id}.")
            self._set_guild_state(guild_id, guild_state)
            guild_state["draft"] = {"header": "", "role_id": 0, "intro": "", "fms": [], "end": ""}
        draft_dict = guild_state["draft"]
        return FmMessageDraft.from_dict(draft_dict)

    def set_guild_draft(self, guild_id: int, draft: FmMessageDraft) -> None:
        """Set the draft for a specific guild.

        Args:
            guild_id (int): The ID of the guild.
            draft (FmMessageDraft): The draft of the guild.
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
            if event.action != ReactionAction.ADD:
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

        content = render_message(updated_message_payload)

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
