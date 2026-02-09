"""Cog for managing and publishing weekly training sessions (formations)."""

from __future__ import annotations

import asyncio
import contextlib
import csv
import io
import logging
from datetime import datetime, timedelta
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
from fablabot.helpers.help_messages import build_help_message
from fablabot.helpers.reaction_log import ReactionLogManager
from fablabot.helpers.state_store import JsonStateStore
from fablabot.helpers.utils import ensure_command_context, is_valid_emoji
from fablabot.models import FmCommand, FmMessageDraft, Formation, PublishedMessage, ReactionAction, ReactionEvent
from fablabot.ui import fmui

logger = logging.getLogger(__name__)


ALLOWED_ROLES = {RoleNames.TRAININGS_MANAGER, RoleNames.ADMIN_TEMP, RoleNames.ADMIN, RoleNames.CODIR}
FM_STATE_FILE = Path("data/formations_state.json")
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
        self._state_store = JsonStateStore(logger, FM_STATE_FILE)
        self._reaction_logs = ReactionLogManager(
            self._state_store,
            retention=REACTION_LOG_RETENTION,
            logger=logger,
        )
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
        self._reaction_logs.purge_all()
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
        help_message = build_help_message(
            "Commandes de gestion des formations",
            "fm",
            [
                (
                    "start <role>",
                    "Démarrer un brouillon de formation et choisir le rôle à mentionner (modal intro/conclusion)",
                ),
                (
                    "edit_text [role] [text]",
                    "Mettre à jour l'introduction, la conclusion ou le rôle mentionné du brouillon",
                ),
                (
                    "add <emoji> <trainer> <date> <hour> <duration> <seats>",
                    "Ajouter une formation au brouillon via un modal (nom, description, caractère excusable)",
                ),
                ("edit", "Modifier une formation existante après sélection"),
                ("remove <index>", "Supprimer une formation du brouillon par son index"),
                ("clear", "Vider le brouillon actuel en conservant intro et conclusion"),
                ("preview", "Afficher l'aperçu du brouillon dans le salon courant"),
                ("publish <channel>", "Publier le brouillon dans un salon cible avec réactions automatiques"),
                ("export [message_id]", "Exporter les réactions/inscriptions en CSV ou texte"),
            ],
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
        if not await ensure_command_context(
            logger,
            "fm.start",
            interaction,
            log_details={"role": role},
            required_roles=ALLOWED_ROLES,
        ):
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
        if not await ensure_command_context(
            logger,
            "fm.edit_text",
            interaction,
            log_details={"role": role},
            required_roles=ALLOWED_ROLES,
        ):
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
                fmui.EditTextModal(
                    self,
                    role.id if role else draft.role_id,
                    draft.intro,
                    draft.end,
                    draft.request_forms_url,
                ),
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
        if not await ensure_command_context(
            logger,
            "fm.add",
            interaction,
            log_details={
                "emoji": emoji,
                "trainer": trainer,
                "date": date,
                "hour": hour,
                "duration": duration,
                "seats": seats,
            },
            required_roles=ALLOWED_ROLES,
        ):
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

        fm = Formation(
            emoji=emoji_clean,
            name="",
            trainer_mention=trainer.mention,
            start_iso=start_dt.isoformat(),
            duration=duration.strip(),
            seats=int(seats),
            excusable=excusable,
        )

        await interaction.response.send_modal(fmui.AddFmModal(self, fm))

    @fm_group.command(name="edit", description="Modifier une formation existante.")
    async def fm_edit(self, interaction: Interaction) -> None:
        """Edit a formation in the draft using an interactive view system.

        Args:
            interaction (Interaction): The Discord interaction context.
        """
        if not await ensure_command_context(logger, "fm.edit", interaction, required_roles=ALLOWED_ROLES):
            return

        assert interaction.guild is not None
        draft = self.get_guild_draft(interaction.guild.id)

        if not draft.fms:
            logger.warning(f"Guild {interaction.guild.id} tried to edit formation but draft is empty.")
            await interaction.response.send_message(ErrorMessages.DRAFT_EMPTY, ephemeral=True)
            return

        view = fmui.FormationSelectView(self, draft.fms, FmCommand.EDIT)
        await interaction.response.send_message(
            "Sélectionne la formation à modifier.",
            view=view,
            embed=Embed(title="Aperçu brouillon", description=render_message(draft)),
            ephemeral=True,
        )

    @fm_group.command(name="remove", description="Retirer une formation du brouillon par son index (1..n).")
    async def fm_remove(self, interaction: Interaction) -> None:
        """Remove a formation from the draft by its index (1..n).

        Args:
            interaction (Interaction): The Discord interaction context.
        """
        if not await ensure_command_context(logger, "fm.remove", interaction, required_roles=ALLOWED_ROLES):
            return

        assert interaction.guild is not None
        draft = self.get_guild_draft(interaction.guild.id)

        if not draft.fms:
            logger.warning(f"Guild {interaction.guild.id} tried to remove formation but draft is empty.")
            await interaction.response.send_message(ErrorMessages.DRAFT_EMPTY, ephemeral=True)
            return

        view = fmui.FormationSelectView(self, draft.fms, FmCommand.REMOVE)
        await interaction.response.send_message(
            "Sélectionne la formation à supprimer.",
            view=view,
            embed=Embed(title="Aperçu brouillon", description=render_message(draft)),
            ephemeral=True,
        )

    @fm_group.command(name="clear", description="Vider le brouillon courant (intro et fin conservées).")
    async def fm_clear(self, interaction: Interaction) -> None:
        """Clear the current draft (introduction and ending are preserved).

        Args:
            interaction (Interaction): The Discord interaction context.
        """
        if not await ensure_command_context(logger, "fm.clear", interaction, required_roles=ALLOWED_ROLES):
            return

        assert interaction.guild is not None
        draft = self.get_guild_draft(interaction.guild.id)

        draft = FmMessageDraft(
            header=draft.header,
            role_id=draft.role_id,
            intro=draft.intro,
            fms=[],
            end=draft.end,
            request_forms_url=draft.request_forms_url,
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
        if not await ensure_command_context(logger, "fm.preview", interaction, required_roles=ALLOWED_ROLES):
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
        if not await ensure_command_context(
            logger,
            "fm.publish",
            interaction,
            log_details={"channel": channel},
            required_roles=ALLOWED_ROLES,
        ):
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

        await asyncio.sleep(1.5 * 60 * 60)
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
        if not await ensure_command_context(
            logger,
            "fm.export",
            interaction,
            log_details={"message_id": message_id},
            required_roles=ALLOWED_ROLES,
        ):
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

        history = self._reaction_logs.history(interaction.guild.id, target_message_id)
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

            self._reaction_logs.append(payload.guild_id, reaction_event)

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

        for guild_id_str in self._state_store.state:
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
                request_forms_url=pub.message.request_forms_url,
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

    def _get_guild_state(self, guild_id: int) -> dict[str, Any]:
        """Get state for a specific guild."""
        return self._state_store.ensure_guild(guild_id)

    def _set_guild_state(self, guild_id: int, payload: dict[str, Any]) -> None:
        """Set the state for a specific guild."""
        self._state_store.set_guild(guild_id, payload)

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
        """Get the last published state for a specific guild."""
        guild_state = self._get_guild_state(guild_id)
        pub_dict = guild_state.get("published")
        if not pub_dict:
            logger.warning(f"No published formations data stored for guild {guild_id}.")
            return None
        return PublishedMessage.from_dict(pub_dict)

    def _set_last_published_in_guild(self, guild_id: int, published: PublishedMessage) -> None:
        """Set the last published state for a specific guild."""
        guild_state = self._get_guild_state(guild_id)
        guild_state["published"] = published.to_dict()
        self._set_guild_state(guild_id, guild_state)

    # -- Reaction Updates --

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
        """Update the published message to reflect current registrations."""
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

        history = self._reaction_logs.history(guild_id, message_id)

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
            request_forms_url=pub_msg.request_forms_url,
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
