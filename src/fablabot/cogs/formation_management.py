"""Cog for managing and publishing weekly training sessions (formations)."""

from __future__ import annotations

import asyncio
import contextlib
import csv
from datetime import datetime, timedelta
import io
import logging
import sqlite3
from typing import TYPE_CHECKING, Any, override
from warnings import deprecated

from discord import (
    AllowedMentions,
    Embed,
    File,
    HTTPException,
    Interaction,
    InteractionType,
    Member,
    Message,
    NotFound,
    Object,
    RawReactionActionEvent,
    Role,
    TextChannel,
    app_commands,
)
from discord.ext import commands, tasks

from fablabot.helpers.access import AnyRole, access
from fablabot.helpers.constants import (
    APPEL_COMMAND_ROLES,
    APPEL_EDIT_WINDOW,
    PARIS_TZ,
    TEMPLATE_ROLES,
    ErrorMessages,
    retention_cutoff,
    staff_default_permissions,
)
from fablabot.helpers.constants import FORMATION_ROLES as ALLOWED_ROLES
from fablabot.helpers.formation import (
    format_current_registrations,
    format_respo_contacts,
    is_past,
    notify_participants_before_formation,
    notify_responsible_before_formation,
    notify_trainer_before_formation,
    parse_date_time,
    render_message,
    send_due_alerts,
    send_promotion_dm,
    send_registration_dm,
    send_waitlist_dm,
    start_appel,
)
from fablabot.helpers.formation.attendance import appel_message, trainer_id
from fablabot.helpers.reaction_log import ReactionLogManager
from fablabot.helpers.safe_discord_operations import retract_unsaved_post
from fablabot.helpers.utils import RefusalKind, counted, ensure_command_context, is_valid_emoji, log_refusal
from fablabot.logging_handlers import guild_log_context
from fablabot.models import FmCommand, FmMessageDraft, Formation, PublishedMessage, ReactionAction, ReactionEvent
from fablabot.services import attendance, catalogue, formations
from fablabot.ui import fmui
from fablabot.ui.attendance import DYNAMIC_ITEMS, OpenAppelPicker
from fablabot.ui.catalogue import CATALOGUE_ITEMS, cut, plain, templates_message
from fablabot.ui.draft import DRAFT_ITEMS, draft_message
from fablabot.ui.session import SESSION_ITEMS, open_session_form

if TYPE_CHECKING:
    from discord import Guild

    from fablabot.main import Fablabot

logger = logging.getLogger(__name__)


_ACCESS = AnyRole(ALLOWED_ROLES)
_CHOICE_CHARS = 100
_UNKNOWN_CHARS = 100
_ID_DIGITS = 18
TRAINER_NOTIFICATION_ADVANCE = timedelta(hours=1)
REGISTRATION_WINDOW = timedelta(minutes=20)


def _parse_ts(ts_iso: Any) -> datetime:
    """Read a stored reaction time; a value that is not a date ranks first."""
    try:
        return datetime.fromisoformat(ts_iso).astimezone(PARIS_TZ)
    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=PARIS_TZ)


class FormationManagement(commands.Cog):
    """Hebdo formations management cog (Draft -> Publish -> Export).

    Commands:
        - /fm start: Start/overwrite a draft with an introduction, ending and role to mention (uses modal).
        - /fm edit_text: Edit the draft introduction and/or ending (uses modal if text=True).
        - /fm add: Add a new formation to the draft (uses modal for text inputs).
        - /fm edit: Edit an existing formation in the draft (uses modal for text inputs).
        - /fm remove: Remove a formation from the draft.
        - /fm clear: Clear the draft.
        - /fm preview: Preview the draft.
        - /fm publish: Publish the draft to a channel.
        - /fm export: Export the draft as a message.
        - /fm modeles: Manage the training templates of the server.
        - /fm brouillon: See the draft, set its text and role, preview it and publish it.

    Listeners:
        - on_raw_reaction_event: Log reactions (add/remove) on messages published by this cog.

    Attributes:
        fm_group (app_commands.Group): Command group for formation management commands.
    """

    def __init__(self, bot: Fablabot) -> None:
        """Initialize the FormationManagement cog.

        Args:
            bot (Fablabot): The bot instance.
        """
        self.bot = bot
        self._reaction_logs = ReactionLogManager(
            "formation",
            retention=retention_cutoff,
            logger=logger,
        )
        self._reaction_lock = asyncio.Lock()
        self._published_locks: dict[int, asyncio.Lock] = {}
        # Servers whose draft is being posted: no second post starts before the first is saved. Ceiling: in memory, which
        # is enough as a post never outlives the process.
        self._posting: set[int] = set()
        self._pending_update_tasks: dict[int, asyncio.Task] = {}
        # Servers asked for an update after the running one began: it runs once more when it ends.
        self._update_wanted: set[int] = set()
        # Servers where a storage failure left a reaction unlogged or an update unsaved. Each reminder tick runs their
        # update, which takes the missed reactions back from Discord. Ceiling: in memory, so a restart during an outage
        # forgets them and a member who reacted then reacts again; the upgrade path is a column.
        self._stale_guilds: set[int] = set()
        # Servers where an update failed for another reason, such as Discord refusing a read: the reactions it was to seat
        # are logged and wait for the next reminder tick to run it again. Same ceiling: a restart forgets them.
        self._retry_guilds: set[int] = set()
        self._reaction_logs.purge_all()
        self.bot.add_dynamic_items(*DYNAMIC_ITEMS, *CATALOGUE_ITEMS, *DRAFT_ITEMS, *SESSION_ITEMS)
        self._check_upcoming_formations.start()
        logger.info("FormationManagement initialized")

    @override
    async def cog_unload(self) -> None:
        """Clean up when the cog is unloaded."""
        self._check_upcoming_formations.cancel()
        self.bot.remove_dynamic_items(*DYNAMIC_ITEMS, *CATALOGUE_ITEMS, *DRAFT_ITEMS, *SESSION_ITEMS)
        for task in list(self._pending_update_tasks.values()):
            try:
                task.cancel()
            except Exception:
                logger.exception("Failed to cancel pending scheduled update task during cog unload.")
        self._pending_update_tasks.clear()
        logger.info("FormationManagement unloaded")

    # region ====== Fm Slash Commands Group ======
    fm_group = app_commands.Group(
        name="fm",
        description="Gère les annonces de Formations et les inscriptions.",
        default_permissions=staff_default_permissions(),
    )

    @fm_group.command(name="start", description="Démarrer/écraser un brouillon avec une introduction.", extras=access(_ACCESS))
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

    @fm_group.command(
        name="edit_text", description="Modifier l'introduction et/ou la conclusion du brouillon.", extras=access(_ACCESS)
    )
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
            embed=Embed(
                title=f"Aperçu brouillon — {counted(len(draft.fms), 'formation', 'formations')}",
                description=f"{content or '_(vide)_'}",
            ),
            ephemeral=True,
        )

    @fm_group.command(
        name="add",
        description="Ajouter une formation au brouillon (triée automatiquement par date/heure).",
        extras=access(_ACCESS),
    )
    @app_commands.describe(
        emoji="Émoji unique pour cette FM (ex: 🔧)",
        trainer="Formateur",
        date="Date au format DD/MM/YYYY",
        hour="Heure au format HH:MM (24h)",
        duration="Durée en texte, ce sera affiché tel quel",
        seats="Nombre de places",
        excusable="Absences excusables ?",
    )
    async def fm_add(  # noqa: PLR0913 - one parameter per slash command option
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

        if is_past(start_dt):
            logger.warning(f"Guild {interaction.guild.id} tried to add formation starting in the past: {date} {hour}.")
            await interaction.response.send_message(ErrorMessages.PAST_DATETIME, ephemeral=True)
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

    @fm_group.command(name="edit", description="Modifier une formation existante.", extras=access(_ACCESS))
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

    @fm_group.command(
        name="appel",
        description="Faire ou corriger l'appel d'une formation des 7 derniers jours.",
        extras=access(AnyRole(APPEL_COMMAND_ROLES)),
    )
    async def fm_appel(self, interaction: Interaction) -> None:
        """Open the attendance check of a training of the last seven days, picked from a menu when several are open.

        Args:
            interaction (Interaction): The Discord interaction context.
        """
        if not await ensure_command_context(logger, "fm.appel", interaction, required_roles=APPEL_COMMAND_ROLES):
            return

        assert interaction.guild is not None
        now = datetime.now(PARIS_TZ)
        # A menu holds 25 options: the newest 25 checks, which are the ones a trainer is likely to fix.
        appels = attendance.open_in(interaction.guild.id, now)[:25]
        if not appels:
            await interaction.response.send_message("Aucun appel ouvert sur ce serveur.", ephemeral=True)
            return
        if len(appels) == 1:
            content, view = appel_message(interaction.guild, appels[0], now)
            await interaction.response.send_message(content, view=view, ephemeral=True)
            return
        await interaction.response.send_message("Choisis une formation", view=OpenAppelPicker(appels), ephemeral=True)

    @fm_group.command(
        name="modeles",
        description="Gérer les modèles de formation.",
        extras=access(AnyRole(TEMPLATE_ROLES)),
    )
    async def fm_modeles(self, interaction: Interaction) -> None:
        """Show the training templates of the server, with the buttons that add, change and delete them.

        Args:
            interaction (Interaction): The Discord interaction context.
        """
        if not await ensure_command_context(logger, "fm.modeles", interaction, required_roles=TEMPLATE_ROLES):
            return

        assert interaction.guild is not None
        content, embed, view = templates_message(interaction.guild.id, 0)
        await interaction.response.send_message(
            content or None, embed=embed, view=view, ephemeral=True, allowed_mentions=AllowedMentions.none()
        )

    @fm_group.command(name="brouillon", description="Voir et préparer le brouillon de la semaine.", extras=access(_ACCESS))
    async def fm_brouillon(self, interaction: Interaction) -> None:
        """Show the draft with the buttons that set its text, preview it and publish it.

        Args:
            interaction (Interaction): The Discord interaction context.
        """
        if not await ensure_command_context(logger, "fm.brouillon", interaction, required_roles=ALLOWED_ROLES):
            return

        assert interaction.guild is not None
        content, embed, view = draft_message(interaction.guild, self.get_guild_draft(interaction.guild.id))
        await interaction.response.send_message(
            content or None, embed=embed, view=view, ephemeral=True, allowed_mentions=AllowedMentions.none()
        )

    @fm_group.command(name="ajouter", description="Ajouter au brouillon une session d'un modèle.", extras=access(_ACCESS))
    @app_commands.describe(modele="Le modèle : tape les premières lettres")
    async def fm_ajouter(self, interaction: Interaction, *, modele: str) -> None:
        """Open the form of a session of a template, picked by the suggestions or typed in full.

        Args:
            interaction (Interaction): The Discord interaction context.
            modele (str): The ID of a suggested template of this server, else the template's name.
        """
        if not await ensure_command_context(logger, "fm.ajouter", interaction, required_roles=ALLOWED_ROLES):
            return

        assert interaction.guild is not None
        guild_id = interaction.guild.id
        typed = modele.strip()
        # A suggestion's value is an ID; a number too long to be one is a name.
        by_id = typed.isascii() and typed.isdecimal() and len(typed) <= _ID_DIGITS
        template = (catalogue.get_template(guild_id, int(typed)) if by_id else None) or catalogue.find_by_name(guild_id, typed)
        if template is None:
            log_refusal(logger, "fm.ajouter", interaction, RefusalKind.TEMPLATE_UNKNOWN)
            await interaction.response.send_message(
                f"Aucun modèle **{cut(plain(typed), _UNKNOWN_CHARS)}** sur ce serveur : choisis-en un dans les "
                "suggestions, ou passe par /fm brouillon. Rien n'a été ajouté.",
                ephemeral=True,
                allowed_mentions=AllowedMentions.none(),
            )
            return
        await open_session_form(interaction, template)

    @fm_ajouter.autocomplete("modele")
    async def _template_choices(self, interaction: Interaction, current: str) -> list[app_commands.Choice[str]]:
        """Suggest the templates of this server whose name holds the letters typed, to a member who may add sessions.

        Args:
            interaction (Interaction): The Discord interaction context.
            current (str): What was typed so far.

        Returns:
            list[app_commands.Choice[str]]: At most 25 choices named by template and valued by ID; none for a server that
                is not served, or for a member without a formation role.
        """
        user = interaction.user
        if (
            interaction.guild is None
            or not self.bot.guild_access.is_served(interaction.guild.id)
            or not isinstance(user, Member)
            or not {role.name for role in user.roles} & ALLOWED_ROLES
        ):
            return []
        templates = catalogue.search_templates(interaction.guild.id, current)
        return [app_commands.Choice(name=cut(t.fields.name, _CHOICE_CHARS), value=str(t.id)) for t in templates]

    @fm_group.command(name="remove", description="Retirer une formation du brouillon.", extras=access(_ACCESS))
    async def fm_remove(self, interaction: Interaction) -> None:
        """Remove a formation from the draft, picked from a selector.

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

    @fm_group.command(name="clear", description="Vider le brouillon courant (intro et fin conservées).", extras=access(_ACCESS))
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

    @fm_group.command(name="preview", description="Afficher l'aperçu du brouillon dans ce salon.", extras=access(_ACCESS))
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

    @fm_group.command(
        name="publish",
        description="Publier le message dans un salon d'annonces (réactions auto-ajoutées).",
        extras=access(_ACCESS),
    )
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

        await self.publish_draft(interaction, channel)

    async def publish_draft(self, interaction: Interaction, channel: TextChannel) -> None:
        """Post the draft in a channel with its reactions, store it as the published message and announce it later.

        The caller has checked the roles and that the draft can be posted. One post runs at a time on a server: the
        claim is taken before the first await and kept until the message is saved, so two clicks cannot both post.

        Args:
            interaction (Interaction): The Discord interaction context.
            channel (TextChannel): The target announcement channel.
        """
        assert interaction.guild is not None
        guild_id = interaction.guild.id
        if guild_id in self._posting:
            log_refusal(logger, "fm.publish", interaction, RefusalKind.DRAFT_NOT_READY)
            await interaction.response.send_message(
                "Une publication est déjà en cours sur ce serveur. Rien n'a été publié.", ephemeral=True
            )
            return
        self._posting.add(guild_id)
        try:
            posted = await self._post_draft(interaction, channel)
        finally:
            self._posting.discard(guild_id)
        if posted is None:
            await self._close_question(interaction, f"Rien n'a été publié dans {channel.mention}.")
            return
        msg, draft = posted

        success_reactions = 0
        for index, fm in enumerate(draft.fms, start=1):
            try:
                await msg.add_reaction(fm.emoji)
                success_reactions += 1
            except Exception:
                logger.exception(
                    f"Guild {guild_id} failed to add reaction {fm.emoji!r} to the published message {msg.id} "
                    f"(session {index} of {len(draft.fms)})."
                )

        logger.info(f"Guild {guild_id} published the formations draft in channel_id={channel.id}.")
        await interaction.followup.send(
            f"Message publié dans {channel.mention} (ID: `{msg.id}`) avec "
            f"{success_reactions}/{len(draft.fms)} "
            + ("réactions ajoutées." if success_reactions > 1 else "réaction ajoutée."),
        )
        await self._close_question(interaction, f"📣 Annonce publiée dans {channel.mention}.")

        await asyncio.sleep(1.5 * 60 * 60)
        with contextlib.suppress(Exception):
            await msg.publish()

        logger.info(f"Guild {guild_id} published the formations message as announcement.")
        await interaction.followup.send("Message publié en mode annonce.")

    async def _post_draft(self, interaction: Interaction, channel: TextChannel) -> tuple[Message, FmMessageDraft] | None:
        """Send the draft to the channel and store it as the published message.

        Args:
            interaction (Interaction): The Discord interaction context.
            channel (TextChannel): The target announcement channel.

        Returns:
            tuple[Message, FmMessageDraft] | None: The posted message and the draft it holds, or None when it failed
                and the member has been told.
        """
        assert interaction.guild is not None
        draft = self.get_guild_draft(interaction.guild.id)
        if interaction.type is InteractionType.component:
            # The click answers in place, which also takes the Publier button off the question for good.
            await interaction.response.edit_message(
                content=f"📣 Publication dans {channel.mention}…", view=None, allowed_mentions=AllowedMentions.none()
            )
        else:
            await interaction.response.defer(thinking=True)

        content = render_message(draft)

        try:
            # Staff type the intro, so it may not reach beyond the chosen role and the trainers: the bot's own rights
            # must not ping everyone, nor anyone named in the text.
            trainers = {found for fm in draft.fms if (found := trainer_id(fm.trainer_mention)) is not None}
            mentions = AllowedMentions(
                everyone=False,
                roles=[Object(draft.role_id)] if draft.role_id else False,
                users=[Object(user_id) for user_id in sorted(trainers)],
            )
            msg = await channel.send(content, suppress_embeds=True, allowed_mentions=mentions)
        except Exception:
            logger.exception(f"Guild {interaction.guild.id} failed to publish the formations draft in channel_id={channel.id}.")
            await interaction.followup.send("Erreur pendant la publication du message.", ephemeral=True)
            return None

        # Saved before the reactions: a reaction counts from the first one, and a refused save leaves nothing to undo.
        try:
            self._set_last_published_in_guild(
                interaction.guild.id,
                PublishedMessage(
                    message_id=msg.id,
                    channel_id=channel.id,
                    message=draft,
                ),
            )
        except sqlite3.Error:
            logger.exception(f"Guild {interaction.guild.id} could not save the published message {msg.id}; retracting it.")
            await interaction.followup.send(await retract_unsaved_post(logger, msg))
            return None
        return msg, draft

    async def _close_question(self, interaction: Interaction, text: str) -> None:
        """Say how the publication ended on the "where to publish" question a Publier click came from."""
        if interaction.type is not InteractionType.component:
            return
        try:
            await interaction.edit_original_response(content=text, view=None, allowed_mentions=AllowedMentions.none())
        except HTTPException:
            logger.warning(f"Guild {interaction.guild_id} could not close the question of the publication.")

    @fm_group.command(
        name="export", description="Exporter la liste des membres ayant (dé)réagi aux émojis des FMs.", extras=access(_ACCESS)
    )
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

        A server the allow-list refuses is dropped, and nothing catches that up. The servers of a start with an empty
        list are served from the first connection, so no reaction is refused while the bot settles.

        Args:
            payload (RawReactionActionEvent): The raw reaction event payload.
        """
        if payload.guild_id is None or not self.bot.guild_access.is_served(payload.guild_id):
            return

        member = payload.member
        if member and member.bot:
            return

        async with self._reaction_lock:
            received_at = datetime.now(PARIS_TZ)
            reaction_event = ReactionEvent(
                message_id=payload.message_id,
                user_id=payload.user_id,
                user_name=member.name if member else None,
                emoji=str(payload.emoji),
                action=ReactionAction[payload.event_type.removeprefix("REACTION_")],
                ts_iso=received_at.isoformat(timespec="seconds"),
            )
            try:
                if not self._reaction_counts(payload.guild_id, reaction_event):
                    return
                self._reaction_logs.append(payload.guild_id, reaction_event)
            except sqlite3.Error:
                logger.exception(
                    f"Reaction not recorded guild_id={payload.guild_id} message_id={payload.message_id}; "
                    "taken back from Discord once the storage answers"
                )
                self._stale_guilds.add(payload.guild_id)
                return
            self._schedule_update(payload.guild_id)

    # endregion Event Listeners

    # region ====== Background Tasks ======

    @tasks.loop(minutes=5)
    async def _check_upcoming_formations(self) -> None:
        """Check for formations starting soon and notify trainers/responsibles (Paris timezone)."""
        logger.debug("Checking for upcoming formations to notify trainers and responsibles (Paris time).")
        now = datetime.now(PARIS_TZ)
        served = self.bot.guild_access.is_served
        for guild_id in filter(served, self._stale_guilds | self._retry_guilds):
            with guild_log_context(guild_id):  # the update's task inherits it, so its lines stay with their server
                self._schedule_update(guild_id, delay=0)
        # An exception other than a network one ends a tasks.loop for good: a storage failure waits for the next tick.
        try:
            # A snapshot: waiting on a guild's lock yields to code that may add a guild to the state.
            guild_ids = formations.guilds_with_published()
        except sqlite3.Error:
            logger.exception("Reminder tick skipped: storage failure.")
            return
        for guild_id in filter(served, guild_ids):
            with guild_log_context(guild_id):
                try:
                    await self._send_due_reminders(guild_id, now)
                except sqlite3.Error:
                    logger.exception(f"Reminders of guild {guild_id} stopped by a storage failure; next tick tries again.")
        try:
            await send_due_alerts(self.bot, now)
        except sqlite3.Error:
            logger.exception("Alerts stopped by a storage failure; next tick tries again.")

    async def _send_due_reminders(self, guild_id: int, now: datetime) -> None:
        """Notify the people of one guild's formations that are due, and record that they were.

        Args:
            guild_id (int): The ID of the guild.
            now (datetime): The time of this tick.
        """
        start_window_end = now + timedelta(minutes=5)

        guild = self.bot.get_guild(guild_id)
        if not guild:
            return

        # One writer at a time: load, await and write back must not interleave with the registration update.
        async with self._published_lock(guild_id):
            newest = self._get_last_published_in_guild(guild_id)
            if not newest:
                return
            # A start missed while the bot was down, or hidden behind a newer announcement, is caught up until its check closes.
            behind = formations.published_awaiting_start(guild_id, now - APPEL_EDIT_WINDOW, start_window_end)
            send_contacts: str = format_respo_contacts(guild)
            for pub in sorted({p.message_id: p for p in [*behind, newest]}.values(), key=lambda p: p.message_id):
                await self._remind_message(guild, pub, now, send_contacts, catch_up=pub.message_id != newest.message_id)

    async def _remind_message(
        self, guild: Guild, pub: PublishedMessage, now: datetime, send_contacts: str, *, catch_up: bool
    ) -> None:
        """Notify for the trainings of one published message that are due, and store that they were.

        An older message is only caught up on starts: it gets no hour-before reminder. A training published in two
        messages gets one check: a message leaves the training to the other when that one already has its check, and an
        older message also leaves it to a newer one that holds it again.

        Args:
            guild (Guild): The guild.
            pub (PublishedMessage): The message, as stored.
            now (datetime): The time of this tick.
            send_contacts (str): The Respo contacts to write in the participants' reminder.
            catch_up (bool): True for a message that is not the newest.
        """
        window_tolerance = timedelta(minutes=5)
        notification_window_start = now + TRAINER_NOTIFICATION_ADVANCE - window_tolerance
        notification_window_end = now + TRAINER_NOTIFICATION_ADVANCE + window_tolerance
        start_window_end = now + window_tolerance

        fms = list(pub.message.fms)
        held = attendance.checked_elsewhere(guild.id, pub.message_id)
        if catch_up:
            held |= formations.reissued_positions(guild.id, pub.message_id)
        for position, fm in enumerate(fms):
            if fm.notified_at_start:
                continue

            if position in held:
                fm.notified_at_start = True
                continue

            if (
                not catch_up
                and not fm.notified_hour_before
                and notification_window_start <= fm.start_dt <= notification_window_end
            ):
                await notify_trainer_before_formation(guild, fm, send_contacts)
                await notify_responsible_before_formation(guild, fm, moment="hour_before")
                await notify_participants_before_formation(guild, fm, send_contacts)

                fm.notified_hour_before = True
                continue

            if now - APPEL_EDIT_WINDOW < fm.start_dt <= start_window_end and await start_appel(guild, pub, position):
                if fm.start_dt >= now - window_tolerance:  # "starts now" is only true in the window
                    await notify_responsible_before_formation(guild, fm, moment="start")

                fm.notified_at_start = True  # only once the check exists: a failed opening is tried again at the next tick

        updated_pub_msg = FmMessageDraft(
            header=pub.message.header,
            role_id=pub.message.role_id,
            intro=pub.message.intro,
            fms=fms,
            end=pub.message.end,
            request_forms_url=pub.message.request_forms_url,
        )
        self._set_last_published_in_guild(
            guild.id,
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

    def get_guild_draft(self, guild_id: int) -> FmMessageDraft:
        """Get the draft state for a specific guild.

        Args:
            guild_id (int): The ID of the guild.

        Returns:
            FmMessageDraft: The draft of the guild.
        """
        return formations.get_draft(guild_id) or FmMessageDraft(header="", role_id=0, intro="", fms=[], end="")

    def set_guild_draft(self, guild_id: int, draft: FmMessageDraft) -> None:
        """Set the draft for a specific guild.

        Args:
            guild_id (int): The ID of the guild.
            draft (FmMessageDraft): The draft of the guild.
        """
        formations.save_draft(guild_id, draft)

    def _get_last_published_in_guild(self, guild_id: int) -> PublishedMessage | None:
        """Get the last published state for a specific guild."""
        published = formations.current_published(guild_id)
        if published is None:
            logger.debug(f"No published formations data stored for guild {guild_id}.")
        return published

    def _set_last_published_in_guild(self, guild_id: int, published: PublishedMessage) -> None:
        """Set the last published state for a specific guild."""
        formations.save_published(guild_id, published)

    def _reaction_counts(self, guild_id: int, event: ReactionEvent) -> bool:
        """Tell whether a reaction is on a server's published message, on one of its emojis, and not an add past the deadline.

        The deadline is judged at the reaction's own time, so a check that runs late does not change the answer. It
        closes sign-ups only: a removal is always logged on a served server, or the member who left would take their
        place back at the next update.

        Args:
            guild_id (int): The ID of the guild.
            event (ReactionEvent): The reaction.

        Returns:
            bool: True when the reaction belongs in the log.

        Raises:
            sqlite3.Error: The published message cannot be read.
        """
        pub = self._get_last_published_in_guild(guild_id)
        if not pub or event.message_id != pub.message_id:
            return False
        fm_emojis = {fm.emoji for fm in pub.message.fms}
        if fm_emojis and event.emoji not in fm_emojis:
            return False

        formation = next((fm for fm in pub.message.fms if fm.emoji == event.emoji), None)
        if formation and event.action == ReactionAction.ADD:
            registration_deadline = formation.start_dt + REGISTRATION_WINDOW
            if datetime.fromisoformat(event.ts_iso) > registration_deadline:
                logger.info(
                    f"Ignoring reaction {event.emoji} for formation {formation.name!r} "
                    f"from user {event.user_id} - registration closed.",
                )
                return False
        return True

    def _log_missed_reactions(
        self,
        guild_id: int,
        message_id: int,
        reactions: dict[str, dict[int, str]],
        last_add: dict[tuple[str, int], datetime],
    ) -> None:
        """Log the reactions Discord shows and the log misses after a storage failure, as of now.

        A failed write is the only reason here, so a member keeps their sign-up and takes their place in the queue at
        this time, also when they had left before: a refused re-add does not give back the place they gave up. A
        reaction past the registration deadline stays out, as the listener would have left it.

        Args:
            guild_id (int): The ID of the guild.
            message_id (int): The published message.
            reactions (dict[str, dict[int, str]]): The members who reacted, by emoji.
            last_add (dict[tuple[str, int], datetime]): The logged time of each reaction still in place, completed here.
        """
        now = datetime.now(PARIS_TZ)
        for emoji, users in reactions.items():
            for user_id, user_name in users.items():
                if (emoji, user_id) in last_add:
                    continue
                event = ReactionEvent(
                    message_id=message_id,
                    user_id=user_id,
                    user_name=user_name,
                    emoji=emoji,
                    action=ReactionAction.ADD,
                    ts_iso=now.isoformat(timespec="seconds"),
                )
                if self._reaction_counts(guild_id, event):
                    self._reaction_logs.append(guild_id, event)
                    last_add[emoji, user_id] = now

    # -- Reaction Updates --

    def _schedule_update(self, guild_id: int, delay: float = 2.0) -> None:
        """Schedule a debounced update for a guild's published message.

        A request while an update is waiting out its delay is covered by it: it reads the history after the wait.
        One that comes once the update has begun may have missed that reaction, so the update runs once more
        when it ends.

        Args:
            guild_id (int): The ID of the guild.
            delay (float, optional): The delay in seconds before performing the update. Defaults to 2.0.
        """
        existing = self._pending_update_tasks.get(guild_id)
        if existing and not existing.done():
            self._update_wanted.add(guild_id)
            return

        async def _delayed() -> None:
            try:
                while True:
                    await asyncio.sleep(delay)
                    self._update_wanted.discard(guild_id)
                    self._retry_guilds.discard(guild_id)
                    await self._update_published_message(guild_id)
                    if guild_id not in self._update_wanted:
                        break
            except asyncio.CancelledError:
                logger.debug(f"Scheduled update for guild {guild_id} was cancelled.")
            except sqlite3.Error:
                logger.exception(
                    f"Update of guild {guild_id} stopped by a storage failure; the next reminder tick tries again."
                )
                self._stale_guilds.add(guild_id)
            except Exception:
                logger.exception(f"Error during scheduled update for guild {guild_id}; the next reminder tick tries again.")
                self._retry_guilds.add(guild_id)
            finally:
                self._update_wanted.discard(guild_id)
                self._pending_update_tasks.pop(guild_id, None)

        task = asyncio.create_task(_delayed())
        self._pending_update_tasks[guild_id] = task

    def _published_lock(self, guild_id: int) -> asyncio.Lock:
        """Get the lock serialising the writers of a guild's published message (reminder tick and registration update)."""
        return self._published_locks.setdefault(guild_id, asyncio.Lock())

    async def _update_published_message(self, guild_id: int) -> None:
        """Update the published message to reflect current registrations, one writer at a time.

        Args:
            guild_id (int): The ID of the guild.
        """
        async with self._published_lock(guild_id):
            await self._update_published_message_locked(guild_id)

    async def _update_published_message_locked(self, guild_id: int) -> None:  # noqa: PLR0912, PLR0915 - split once formations have tests
        """Reload the published message, rebuild registrations from reactions, save and notify."""
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
        if not isinstance(channel, TextChannel):
            logger.warning(
                f"Channel {channel_id} of the published formations message is gone for guild {guild_id}; skipping update."
            )
            return

        try:
            msg = await channel.fetch_message(message_id)
        except Exception as exc:
            logger.exception(f"Failed to fetch message {message_id} in channel {channel_id}.")
            if not isinstance(exc, NotFound):  # a deleted post stays so at the next tick; a refused read can be repaired
                self._retry_guilds.add(guild_id)
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

        # The time of each reaction still in place: a logged removal takes the member out, so a reaction the log does
        # not hold again never gets the old place back.
        last_add: dict[tuple[str, int], datetime] = {}
        journaled: set[tuple[str, int]] = set()
        for event in history:
            emoji = str(event.emoji)
            user_id = event.user_id
            if not emoji or user_id is None or emoji not in tracked_emojis:
                continue
            key = (emoji, int(user_id))
            journaled.add(key)
            if event.action != ReactionAction.ADD:
                last_add.pop(key, None)
                continue
            ts = _parse_ts(event.ts_iso)
            if key not in last_add or ts.timestamp() > last_add[key].timestamp():
                last_add[key] = ts
        # The journal keeps 30 days: a seat taken earlier has no entry left, and the stored time ranks it. The reaction
        # still has to stand on Discord, which the rebuild below checks.
        for fm in fms:
            for stored in (*fm.registered_users, *fm.waitlisted_users):
                stored_id = stored.get("user_id")
                if isinstance(stored_id, int) and (fm.emoji, stored_id) not in journaled:
                    last_add[fm.emoji, stored_id] = _parse_ts(stored.get("ts_iso"))

        reactions_snapshot: dict[str, dict[int, str]] = {}
        for reaction in msg.reactions:
            emoji_str = str(reaction.emoji)
            if emoji_str not in tracked_emojis:
                continue
            async for user in reaction.users():
                if user.bot:
                    continue
                reactions_snapshot.setdefault(emoji_str, {})[user.id] = f"{user.display_name} ({user.name})"

        if guild_id in self._stale_guilds:
            # Cleared first: a reaction refused from here on marks the server again, and a failure is marked by the caller.
            self._stale_guilds.discard(guild_id)
            self._log_missed_reactions(guild_id, message_id, reactions_snapshot, last_add)

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
                key=lambda item: (item[1].timestamp(), item[0]),
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
                        if datetime.now(PARIS_TZ) <= fm.start_dt + REGISTRATION_WINDOW:
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
async def setup(bot: Fablabot) -> None:
    """Setup function to load the Formations cog.

    Args:
        bot (Fablabot): The bot instance.
    """
    await bot.add_cog(FormationManagement(bot))
