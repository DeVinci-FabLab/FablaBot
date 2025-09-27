"""Cog for managing and publishing weekly training sessions (formations)."""

from __future__ import annotations

import csv
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
import io
import json
import logging
from pathlib import Path
from typing import Any
from warnings import deprecated

from discord import (
    Embed,
    File,
    Guild,
    Interaction,
    Member,
    RawReactionActionEvent,
    Role,
    TextChannel,
    app_commands,
)
from discord.ext import commands
from discord.utils import get
from emoji import emojize, is_emoji

from .utils import check_has_role, is_in_allowed_channel, log_request

logger = logging.getLogger(__name__)


ALLOWED_ROLES = {"Respo Formations", "Admin -temp-", "Administrateur"}
DATA_FILE = "data/formations_state.json"
MAX_MSG_CHARS = 1900
REACTION_LOG_RETENTION = timedelta(days=30)
FM_REQUEST_FORMS = "https://forms.office.com/e/MqVdQujzjf"


@dataclass
class Formation:
    """Single Formation entry in a draft.

    Attributes:
        emoji (str): Single emoji representing the formation.
        name (str): Name of the formation.
        trainer_mention (str): Mention of the trainer.
        start_iso (str): Start date/time in ISO format (timezone-aware if possible).
        duration (str): Duration of the formation in text format.
        seats (int): Number of seats available for the formation.
        description (str): Brief description of the formation.
        registered_users (list[dict[str, Any]]): Ordered list of registered users metadata.
        waitlisted_users (list[dict[str, Any]]): Ordered list of waitlisted users metadata.
    """

    emoji: str
    """Single emoji representing the formation."""
    name: str
    """Name of the formation."""
    trainer_mention: str
    """Mention of the trainer."""
    start_iso: str
    """Start date/time in ISO format."""
    duration: str
    """Duration of the formation in text format."""
    seats: int
    """Number of seats available for the formation."""
    description: str
    """Brief description of the formation."""
    registered_users: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    """Registered users metadata (order preserved)."""
    waitlisted_users: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    """Waitlisted users metadata (order preserved)."""

    @property
    def start_dt(self) -> datetime:
        """Get the start date/time as a datetime object.

        Returns:
            datetime: The start date/time as a datetime object.
        """
        return datetime.fromisoformat(self.start_iso)

    def to_dict(self) -> dict[str, Any]:
        """Convert the Formation instance to a dictionary.

        Returns:
            dict[str, Any]: The dictionary representation of the Formation.
        """
        return asdict(self)


class FormationManagement(commands.Cog):
    """Hebdo formations management cog (Draft -> Publish -> Export).

    - /fm start intro:<str> end:<str> [role]
    - /fm edit_text [intro] [end]
    - /fm add emoji:<str> name:<str> trainer:<@Member> date:<DD/MM/YYYY> hour:<HH:MM> duration:<str> seats:<int> description:<str>
    - /fm remove index:<int>
    - /fm edit index:<int> [emoji] [name] [trainer] [date] [hour] [duration] [seats] [description]
    - /fm clear
    - /fm preview
    - /fm publish channel:<#salon>
    - /fm export [message_id]

    Log reactions (add/remove) on messages published by this cog.
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
                  "action": "add"|"remove",
                  "ts_iso": str
                }
              ]
          }
        }
        ```
        """
        self._purge_all_reaction_logs()
        logger.info("FormationManagement initialized")

    # region ====== Fm Slash Group ======
    fm_group = app_commands.Group(name="fm", description="Gère les annonces de Formations et les inscriptions.")

    @fm_group.command(name="start", description="Démarrer/écraser un brouillon avec une introduction.")
    @app_commands.describe(
        intro="Texte d'introduction affiché en tête du message",
        end="Texte de fin affiché en bas du message",
        role="Rôle à mentionner",
    )
    async def fm_start(self, interaction: Interaction, intro: str, end: str, role: Role) -> None:
        """Start a new draft with an introduction.

        Args:
            interaction (Interaction): The interaction context.
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
        assert isinstance(interaction.channel, TextChannel)

        emoji = {emoji for emoji in interaction.guild.emojis if emoji.name == "dvfl"}.pop()

        start = f"# [FORMATIONS] {emoji}\nHey {role.mention} !\n"
        draft: dict[str, Any] = {"intro": start + intro, "fms": [], "end": end}
        self._set_guild_draft(interaction.guild.id, draft)

        fms = [Formation(**x) for x in draft["fms"]]
        content = self._render_message(draft["intro"], fms, draft["end"])
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
    )
    async def fm_edit_text(
        self,
        interaction: Interaction,
        intro: str | None = None,
        end: str | None = None,
    ) -> None:
        """Edit the draft introduction and/or ending.

        Args:
            interaction (Interaction): The interaction context.
            intro (str | None): The new introduction text.
            end (str | None): The new ending text.
        """
        log_request(logger, "fm.edit_text", interaction, intro=intro, end=end)
        if not await is_in_allowed_channel(logger, interaction):
            return
        if not await check_has_role(logger, interaction, ALLOWED_ROLES):
            return

        if intro is None and end is None:
            await interaction.response.send_message(
                "Aucun champ à modifier. Fournis au moins `intro` ou `end`.",
                ephemeral=True,
            )
            return

        assert interaction.guild is not None
        draft = self._get_guild_draft(interaction.guild.id)

        current_intro = draft["intro"]
        current_end = draft["end"]

        updated_intro = current_intro if intro is None else intro.strip()
        updated_end = current_end if end is None else end.strip()

        if updated_intro == current_intro and updated_end == current_end:
            await interaction.response.send_message(
                "Aucune modification détectée.",
                ephemeral=True,
            )
            return

        draft["intro"] = updated_intro
        draft["end"] = updated_end
        self._set_guild_draft(interaction.guild.id, draft)

        fms = [Formation(**x) for x in draft["fms"]]
        content = self._render_message(draft["intro"], fms, draft["end"])

        logger.info(
            f"Guild {interaction.guild} updated draft intro/end "
            f"(intro_changed={intro is not None}, end_changed={end is not None})."
        )

        await interaction.response.send_message(
            "Brouillon mis à jour.",
            embed=Embed(
                title=f"Aperçu brouillon — {len(fms)} formation(s)",
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
    )
    async def fm_add(
        self,
        interaction: Interaction,
        emoji: str,
        name: str,
        description: str,
        trainer: Member,
        date: str,
        hour: str,
        duration: str,
        seats: app_commands.Range[int, 1, 500],
    ) -> None:
        """Add a formation to the draft (automatically sorted by date/time).

        Args:
            interaction (Interaction): The interaction context.
            emoji (str): The emoji for the formation.
            name (str): The name of the formation.
            description (str): The description of the formation.
            trainer (Member): The trainer.
            date (str): The date of the formation.
            hour (str): The hour of the formation.
            duration (str): The duration of the formation in text format.
            seats (app_commands.Range[int, 1, 500]): The number of seats for the formation.
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
        )
        if not await is_in_allowed_channel(logger, interaction):
            return
        if not await check_has_role(logger, interaction, ALLOWED_ROLES):
            return

        assert interaction.guild is not None
        draft = self._get_guild_draft(interaction.guild.id)

        emoji_clean = emoji.strip()

        if not emoji_clean or not is_emoji(emojize(emoji_clean)):
            logger.warning(f"Guild {interaction.guild.id} tried to add formation with invalid emoji: {emoji_clean!r}.")
            await interaction.response.send_message("Émoji invalide.", ephemeral=True)
            return

        fms: list[Formation] = [Formation(**x) for x in draft["fms"]]
        if any(existing.emoji == emoji_clean for existing in fms):
            logger.warning(f"Guild {interaction.guild.id} tried to add formation with duplicate emoji {emoji_clean!r}.")
            await interaction.response.send_message(
                "Cet émoji est deja utilisé pour une autre formation dans ce brouillon.", ephemeral=True
            )
            return

        try:
            start_dt = self._parse_date_time(date, hour)
        except Exception:
            logger.warning(f"Guild {interaction.guild.id} tried to add formation with invalid date/hour: {date} {hour}.")
            await interaction.response.send_message(
                "Date/heure invalides. Exemples: date `15/09/2025`, heure `18:08`.", ephemeral=True
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
        )

        fms.append(fm)
        fms.sort(key=lambda x: x.start_dt)

        draft["fms"] = [x.to_dict() for x in fms]
        self._set_guild_draft(interaction.guild.id, draft)

        preview = self._render_message(draft["intro"], fms, draft["end"])
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

    @fm_group.command(name="remove", description="Retirer une formation du brouillon par son index (1..n).")
    @app_commands.describe(index="Position de la FM dans l'aperçu trié (1..n)")
    async def fm_remove(self, interaction: Interaction, index: app_commands.Range[int, 1, 1000]) -> None:
        """Remove a formation from the draft by its index (1..n).

        Args:
            interaction (Interaction): The interaction context.
            index (app_commands.Range[int, 1, 1000]): The index of the formation to remove (1-based).
        """
        log_request(logger, "fm.remove", interaction, index=index)
        if not await is_in_allowed_channel(logger, interaction):
            return
        if not await check_has_role(logger, interaction, ALLOWED_ROLES):
            return

        assert interaction.guild is not None
        draft: dict[str, Any] = self._get_guild_draft(interaction.guild.id)
        fms: list[Formation] = [Formation(**x) for x in draft["fms"]]
        fms.sort(key=lambda x: x.start_dt)

        if index > len(fms):
            logger.warning(f"Guild {interaction.guild.id} tried to remove out-of-bounds formation index {index}.")
            await interaction.response.send_message(f"Index hors limites (il y a {len(fms)} FM).", ephemeral=True)
            return

        removed = fms.pop(index - 1)
        draft["fms"] = [x.to_dict() for x in fms]
        self._set_guild_draft(interaction.guild.id, draft)

        fms = [Formation(**x) for x in draft["fms"]]
        preview = self._render_message(draft["intro"], fms, draft["end"])
        logger.info(f"Guild {interaction.guild.id} removed formation {removed.name!r} ({removed.start_iso}) from draft.")
        await interaction.response.send_message(
            f"Supprimé: {removed.emoji} {removed.name}",
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
    ) -> None:
        """Edit a formation in the draft while keeping other entries untouched.

        Args:
            interaction (Interaction): The interaction context.
            index (app_commands.Range[int, 1, 1000]): The index of the formation to edit (1-based).
            emoji (str | None, optional): New emoji for the formation. Defaults to None.
            name (str | None, optional): New name for the formation. Defaults to None.
            trainer (Member | None, optional): New trainer for the formation. Defaults to None.
            date (str | None, optional): New date for the formation. Defaults to None.
            hour (str | None, optional): New hour for the formation. Defaults to None.
            duration (str | None, optional): New duration for the formation. Defaults to None.
            seats (app_commands.Range[int, 1, 500] | None, optional): New number of seats for the formation. Defaults to None.
            description (str | None, optional): New description for the formation. Defaults to None.
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
        )
        if not await is_in_allowed_channel(logger, interaction):
            return
        if not await check_has_role(logger, interaction, ALLOWED_ROLES):
            return

        assert interaction.guild is not None
        draft: dict[str, Any] = self._get_guild_draft(interaction.guild.id)
        fms: list[Formation] = [Formation(**x) for x in draft["fms"]]
        fms.sort(key=lambda x: x.start_dt)

        if index > len(fms):
            logger.warning(
                f"Guild {interaction.guild.id} tried to edit out-of-bounds formation index {index}.",
            )
            await interaction.response.send_message(
                f"Index hors limites (il y a {len(fms)} FM).",
                ephemeral=True,
            )
            return

        original = fms[index - 1]

        new_emoji = original.emoji
        if emoji is not None:
            candidate = emoji.strip()
            if not candidate:
                logger.warning(f"Guild {interaction.guild.id} provided an empty emoji while editing a formation.")
                await interaction.response.send_message("Émoji invalide.", ephemeral=True)
                return
            if any(i != index - 1 and fm.emoji == candidate for i, fm in enumerate(fms)):
                logger.warning(f"Guild {interaction.guild.id} tried to reuse emoji {candidate} while editing formation.")
                await interaction.response.send_message("Cet émoji est déjà utilisé par une autre formation.", ephemeral=True)
                return
            new_emoji = candidate

        new_name = original.name if name is None else name.strip()
        if not new_name:
            logger.warning(f"Guild {interaction.guild.id} provided an empty name while editing a formation.")
            await interaction.response.send_message("Nom invalide.", ephemeral=True)
            return

        new_trainer = original.trainer_mention if trainer is None else trainer.mention
        new_duration = original.duration if duration is None else duration.strip()
        if not new_duration:
            logger.warning(f"Guild {interaction.guild.id} provided an empty duration while editing a formation.")
            await interaction.response.send_message("Durée invalide.", ephemeral=True)
            return

        new_description = original.description if description is None else description.strip()
        if not new_description:
            logger.warning(f"Guild {interaction.guild.id} provided an empty description while editing a formation.")
            await interaction.response.send_message("Description invalide.", ephemeral=True)
            return

        new_seats = original.seats if seats is None else seats

        new_start_iso = original.start_iso
        if date is not None or hour is not None:
            date_part = date.strip() if date is not None else original.start_dt.strftime("%Y-%m-%d")
            hour_part = hour.strip() if hour is not None else original.start_dt.strftime("%H:%M")
            try:
                new_start_iso = self._parse_date_time(date_part, hour_part).isoformat()
            except Exception:
                logger.warning(
                    f"Guild {interaction.guild.id} provided invalid date/hour while editing formation: {date_part} {hour_part}.",
                )
                await interaction.response.send_message(
                    "Date/heure invalides. Exemples: date `15/09/2025`, heure `18:08`.",
                    ephemeral=True,
                )
                return

        updated = Formation(
            emoji=new_emoji,
            name=new_name,
            trainer_mention=new_trainer,
            start_iso=new_start_iso,
            duration=new_duration,
            seats=new_seats,
            description=new_description,
        )

        fms[index - 1] = updated
        fms.sort(key=lambda x: x.start_dt)

        draft["fms"] = [fm.to_dict() for fm in fms]
        self._set_guild_draft(interaction.guild.id, draft)

        preview = self._render_message(draft["intro"], fms, draft["end"])
        new_position = fms.index(updated) + 1

        logger.info(
            f"Guild {interaction.guild.id} edited formation {original.name!r} -> {updated.name!r} (index {index} → {new_position}).",
        )

        await interaction.response.send_message(
            f"Mise à jour: {updated.emoji} {updated.name} (position {new_position}).",
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
            interaction (Interaction): The interaction context.
        """
        log_request(logger, "fm.clear", interaction)
        if not await is_in_allowed_channel(logger, interaction):
            return
        if not await check_has_role(logger, interaction, ALLOWED_ROLES):
            return

        assert interaction.guild is not None
        draft: dict[str, Any] = self._get_guild_draft(interaction.guild.id)
        intro = draft["intro"]
        end = draft["end"]
        draft = {"intro": intro, "fms": [], "end": end}
        self._set_guild_draft(interaction.guild.id, draft)

        fms = [Formation(**x) for x in draft["fms"]]
        content = self._render_message(draft["intro"], fms, draft["end"])
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
            interaction (Interaction): _description_
        """
        log_request(logger, "fm.preview", interaction)
        if not await is_in_allowed_channel(logger, interaction):
            return
        if not await check_has_role(logger, interaction, ALLOWED_ROLES):
            return

        await interaction.response.send_message("Génération du message en cours...")
        assert interaction.guild is not None
        draft = self._get_guild_draft(interaction.guild.id)
        fms = [Formation(**x) for x in draft["fms"]]
        fms.sort(key=lambda x: x.start_dt)

        content = self._render_message(draft["intro"], fms, draft["end"])
        logger.info(f"Guild {interaction.guild.id} previewed the formations draft.")
        await interaction.edit_original_response(
            content=f"{content or '_(vide)_'}",
            embed=Embed(description="Utilise **/fm publish** pour le publier."),
        )

    @fm_group.command(name="publish", description="Publier le message dans un salon d'annonces (réactions auto-ajoutées).")
    @app_commands.describe(channel="Salon d'annonces cible")
    async def fm_publish(self, interaction: Interaction, channel: TextChannel) -> None:
        """Publish the message in an announcement channel (auto-added reactions).

        Args:
            interaction (Interaction): The interaction context.
            channel (TextChannel): The target announcement channel.
        """
        log_request(logger, "fm.publish", interaction, channel=channel)
        if not await is_in_allowed_channel(logger, interaction):
            return
        if not await check_has_role(logger, interaction, ALLOWED_ROLES):
            return

        assert interaction.guild is not None
        draft = self._get_guild_draft(interaction.guild.id)
        fms = [Formation(**x) for x in draft["fms"]]
        if not fms:
            logger.warning(f"Guild {interaction.guild.id} tried to publish empty formations draft.")
            await interaction.response.send_message("Le brouillon ne contient aucune formation.", ephemeral=True)
            return
        fms.sort(key=lambda x: x.start_dt)
        content = self._render_message(draft["intro"], fms, draft["end"])

        msg = await channel.send(content, suppress_embeds=True)

        published_message = {
            "intro": draft["intro"],
            "end": draft["end"],
            "fms": [],
        }

        for fm in fms:
            fm_dict = fm.to_dict()
            published_message["fms"].append(fm_dict)
            try:
                await msg.add_reaction(fm.emoji)
            except Exception:
                logger.exception(
                    f"Guild {interaction.guild.id} failed to add reaction {fm.emoji!r} for formation {fm.name!r} in published message."
                )

        self._set_last_published_in_guild(
            interaction.guild.id,
            {
                "message_id": msg.id,
                "channel_id": channel.id,
                "message": published_message,
            },
        )

        logger.info(f"Guild {interaction.guild.id} published the formations draft in {channel}.")
        await interaction.response.send_message(
            f"Message publié dans {channel.mention} (ID: `{msg.id}`) avec {len(msg.reactions)} réaction(s) ajoutée(s)."
        )

    @fm_group.command(name="export", description="Exporter la liste des membres ayant (dé)réagi aux émojis des FMs.")
    @app_commands.describe(
        message_id="ID du message publié (optionnel si dernière publication)",
        publication_channel="Salon du message publié (optionnel si dernière publication)",
    )
    async def fm_export(
        self, interaction: Interaction, message_id: str | None = None, publication_channel: TextChannel | None = None
    ) -> None:
        """Export the list of members who reacted (added/removed) to the formation emojis.

        Args:
            interaction (Interaction): The interaction context.
            message_id (str | None, optional): The ID of the published message. Defaults to None.
            publication_channel (TextChannel | None, optional): The channel of the published message. Defaults to None.
        """
        log_request(logger, "fm.export", interaction, message_id=message_id)
        if not await is_in_allowed_channel(logger, interaction):
            return
        if not await check_has_role(logger, interaction, ALLOWED_ROLES):
            return

        assert interaction.guild is not None
        published = self._get_last_published_in_guild(interaction.guild.id)
        if not published and (not message_id or not publication_channel):
            logger.warning(f"Guild {interaction.guild.id} tried to export reactions without published message or ID.")
            await interaction.response.send_message("Aucun message publié enregistré et aucun ID fourni.", ephemeral=True)
            return

        target_message_id = int(message_id) if message_id else int(published["message_id"]) if published else None
        target_channel_id = (
            int(publication_channel.id) if publication_channel else int(published["channel_id"]) if published else None
        )
        if not target_message_id or not target_channel_id:
            logger.error(f"Guild {interaction.guild.id} has inconsistent published message data: {published}")
            await interaction.response.send_message("Données de message publié incohérentes.")
            return

        message_payload = published["message"] if published else {}
        fm_by_emoji: dict[str, dict[str, Any]] = {}
        for fm in message_payload.get("fms", []):
            emoji = fm.get("emoji")
            if not emoji:
                continue
            fm_by_emoji[emoji] = {"name": fm.get("name"), "seats": fm.get("seats")}

        history = self._get_reaction_history(interaction.guild.id, target_message_id)
        history_csv = io.StringIO()
        hist_writer = csv.writer(history_csv, lineterminator="\n")
        hist_writer.writerow(["timestamp_iso", "action", "emoji", "formation_name", "user_name", "user_id", "formation_seats"])
        for ev in history:
            hist_writer.writerow(
                [
                    ev.get("ts_iso", ""),
                    ev.get("action", ""),
                    ev.get("emoji", ""),
                    fm_by_emoji.get(ev.get("emoji", ""), {}).get("name", ""),
                    ev.get("user_name", ""),
                    ev.get("user_id", ""),
                ]
            )

        if message_id or publication_channel:
            await interaction.response.send_message(
                content="Export du message spécifié.",
                file=File(
                    fp=io.BytesIO(history_csv.getvalue().encode(encoding="utf-8")), filename="formations_reactions_log.csv"
                ),
            )
            return

        try:
            channel = interaction.guild.get_channel(target_channel_id) or await interaction.guild.fetch_channel(
                target_channel_id
            )
            assert isinstance(channel, TextChannel)
            msg = await channel.fetch_message(target_message_id)
        except Exception:
            logger.exception(
                f"Guild {interaction.guild.id} failed to fetch message {target_message_id} in channel {target_channel_id}."
            )
            await interaction.response.send_message(content="Impossible de récupérer le message cible.", ephemeral=True)
            return

        await interaction.response.send_message("Export en cours...")

        reactions_snapshot: dict[str, dict[int, dict[str, str]]] = {}
        for reaction in msg.reactions:
            emoji_str = str(reaction.emoji)
            if fm_by_emoji and emoji_str not in fm_by_emoji:
                continue

            reactions_snapshot.setdefault(emoji_str, {})
            async for user in reaction.users():
                if user.bot:
                    continue
                reactions_snapshot[emoji_str][user.id] = {
                    "username": user.name,
                }

        reg_text, reg_file = self._format_current_registrations(history, reactions_snapshot, fm_by_emoji)
        if reg_file:
            await interaction.edit_original_response(
                content=reg_text,
                attachments=[
                    File(reg_file, filename="inscriptions_ordre_inscription.txt"),
                    File(
                        fp=io.BytesIO(history_csv.getvalue().encode(encoding="utf-8")), filename="formations_reactions_log.csv"
                    ),
                ],
            )
            return

        await interaction.edit_original_response(
            content=reg_text,
            attachments=[
                File(
                    fp=io.BytesIO(history_csv.getvalue().encode(encoding="utf-8")),
                    filename="formations_reactions_log.csv",
                ),
            ],
        )

    # endregion Fm Slash Group

    # region ====== Listeners ======
    @commands.Cog.listener(name="on_raw_reaction_add")
    @commands.Cog.listener(name="on_raw_reaction_remove")
    async def on_raw_reaction_event(self, payload: RawReactionActionEvent) -> None:
        """Log reaction updates on the last published formations message."""
        if payload.guild_id is None:
            return
        pub = self._get_last_published_in_guild(payload.guild_id)
        if not pub or payload.message_id != pub.get("message_id"):
            return

        emoji_str = str(payload.emoji)
        message_payload = pub.get("message", {})
        fm_emojis: set[str] = set()
        for fm in message_payload.get("fms", []):
            emoji = fm.get("emoji")
            if emoji:
                fm_emojis.add(str(emoji))
        if fm_emojis and emoji_str not in fm_emojis:
            return

        member_name: str | None = None
        if payload.event_type == "REACTION_ADD" and payload.member is not None:
            member = payload.member
            member_name = member.name

        self._log_reaction(
            payload.guild_id,
            payload.message_id,
            payload.user_id,
            emoji_str,
            payload.event_type,
            member_name,
        )

        await self._update_published_message(payload.guild_id)

    # endregion Listeners

    # region ====== Helpers ======
    @staticmethod
    def _parse_date_time(date_str: str, hour_str: str) -> datetime:
        """Parse date and time strings into a timezone-aware datetime object.

        Args:
            date_str (str): Date string in 'DD/MM/YYYY' format.
            hour_str (str): Hour string in 'HH:MM' format.

        Returns:
            datetime: A timezone-aware datetime object.
        """
        d, m, y = map(int, date_str.split("/"))
        hh, mm = map(int, hour_str.split(":"))
        dt = datetime(y, m, d, hh, mm)
        logger.debug(f"Parsed formation schedule {date_str} {hour_str} -> {dt.isoformat()}")
        return dt

    @staticmethod
    def _humanize_dt(dt: datetime) -> str:
        """Humanize a datetime object.

        Args:
            dt (datetime): The datetime object to humanize.

        Returns:
            str: The humanized date/time string.
        """
        days = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
        day_name = days[dt.weekday()]
        date_part = dt.strftime("%d/%m")
        time_part = dt.strftime("%Hh%M")
        return f"**{day_name} {date_part} à {time_part}**"

    def _render_message(self, intro: str, fms: list[Formation], end: str) -> str:
        """Render the message for the formations.

        Args:
            intro (str): The introduction text.
            fms (list[Formation]): The list of formations to include in the message.
            end (str): The ending text.

        Returns:
            str: The rendered message.
        """
        logger.debug("Rendering formations message.")
        lines: list[str] = []
        if intro.strip():
            lines.append(intro.strip())
            lines.append("")

        for fm in fms:
            line_block = [
                f"{fm.emoji} **{fm.name}** avec {fm.trainer_mention}",
                f":date: {self._humanize_dt(fm.start_dt)}  — "
                f":hourglass_flowing_sand: {fm.duration}  — :busts_in_silhouette: {len(fm.registered_users)}/{fm.seats} place(s)",
                f"{fm.description}",
            ]
            lines.append("\n".join(line_block))
            lines.append("")

        end_lines = [
            ":arrow_right: Pour s'inscrire, réagis avec les émojis des formations correspondantes.",
            ":warning: Si tu ne peux plus venir, n'oublie pas de retirer ta réaction pour libérer la place.",
            "",
            f"Tu veux apprendre autre chose ? [**Propose une formation ici**]({FM_REQUEST_FORMS})",
        ]
        lines.append("\n".join(end_lines))
        lines.append("")

        if end.strip():
            lines.append(end.strip())
            lines.append("")

        if lines and not lines[-1]:
            lines.pop()
        return "\n".join(lines)

    def _format_respo_contacts(self, guild: Guild) -> str:
        """Build the contact string for formation managers.

        Args:
            guild (Guild): The guild to get the role from.

        Returns:
            str: The contact string.
        """
        logger.debug(f"Resolving formation contacts for guild {guild.id}.")
        role = get(guild.roles, name="Respo Formations")
        if role is None:
            logger.debug(f"Role 'Respo Formations' missing in guild {guild.id}; using fallback contacts.")
            return "un·e membre du Pôle Formations"
        members = [member for member in role.members if not member.bot]
        if not members:
            logger.debug(f"Role 'Respo Formations' has no human members in guild {guild.id}; using fallback contacts.")
            return "un·e membre du Pôle Formations"
        mentions = [member.mention for member in members]
        logger.debug(f"Resolved {len(mentions)} formation manager contacts for guild {guild.id}.")
        return " ou ".join(mentions)

    async def _send_registration_dm(
        self,
        guild: Guild,
        user_id: int,
        formation: Formation,
        contacts: str,
    ) -> None:
        """Notify a user that they got a seat in a formation.

        Args:
            guild (Guild): The guild where the user is located.
            user_id (int): The ID of the user to notify.
            formation (Formation): The formation.
            contacts (str): The contact string for formation managers.
        """
        logger.debug(f"Preparing registration DM for guild {guild.id} user {user_id} formation {formation.name}.")
        try:
            member = guild.get_member(user_id) or await guild.fetch_member(user_id)
        except Exception:
            logger.exception(f"Unexpected error while fetching member {user_id} in guild {guild.id}.")
            return
        if member.bot:
            return

        logger.debug(f"Resolved member {member.id} ({member.display_name}) for registration DM in guild {guild.id}.")

        datetime_text = self._humanize_dt(datetime.fromisoformat(formation.start_iso)).lower()[2:-2]

        message = (
            f"Salut {member.display_name} !\n"
            f"Bonne nouvelle ! Ta réaction a bien été prise en compte. Tu es inscrit·e pour la formation **{formation.name}** le {datetime_text}.\n"
            "Si tu ne peux finalement pas participer, retire ta réaction pour libérer la place.\n\n"
            f"*Ce message a été envoyé par un bot. Pour plus d'informations merci de contacter {contacts}.*"
        )
        try:
            await member.send(message)
        except Exception:
            logger.exception(f"Failed to send registration DM to user {user_id} in guild {guild.id}.")
        else:
            logger.info(f"Sent registration DM to user {user_id} in guild {guild.id} for formation {formation.name}.")

    async def _send_promotion_dm(
        self,
        guild: Guild,
        user_id: int,
        formation: Formation,
        contacts: str,
    ) -> None:
        """Notify a user that they were promoted from the waitlist.

        Args:
            guild (Guild): The guild where the user is located.
            user_id (int): The ID of the user to notify.
            formation (Formation): The formation.
            contacts (str): The contact string for formation managers.
        """
        logger.debug(f"Preparing waitlist promotion DM for guild {guild.id} user {user_id} formation {formation.name}.")
        try:
            member = guild.get_member(user_id) or await guild.fetch_member(user_id)
        except Exception:
            logger.exception(f"Unexpected error while fetching member {user_id} in guild {guild.id}.")
            return
        if member.bot:
            return

        logger.debug(f"Resolved member {member.id} ({member.display_name}) for waitlist promotion DM in guild {guild.id}.")

        datetime_text = self._humanize_dt(datetime.fromisoformat(formation.start_iso)).lower()[2:-2]

        message = (
            f"Salut {member.display_name} !\n"
            f"Bonne nouvelle ! Ta patience a payé, tu quittes la liste d'attente et tu as une place pour la formation **{formation.name}** le {datetime_text}.\n"
            "Si tu ne peux finalement pas venir, retire ta réaction pour permettre à quelqu'un d'autre de s'inscrire.\n\n"
            f"*Ce message a été envoyé par un bot. Pour plus d'informations merci de contacter {contacts}.*"
        )
        try:
            await member.send(message)
        except Exception:
            logger.exception(f"Failed to send waitlist promotion DM to user {user_id} in guild {guild.id}.")
        else:
            logger.info(f"Sent waitlist promotion DM to user {user_id} in guild {guild.id} for formation {formation.name}.")

    async def _send_waitlist_dm(
        self,
        guild: Guild,
        user_id: int,
        formation_name: str,
        waitlist_position: int,
        contacts: str,
    ) -> None:
        """Notify a user that they joined the waitlist for a formation.

        Args:
            guild (Guild): The guild where the user is located.
            user_id (int): The ID of the user to notify.
            formation_name (str): The name of the formation.
            waitlist_position (int): The user's position on the waitlist.
            contacts (str): The contact string for formation managers.
        """
        logger.debug(
            f"Preparing waitlist DM for guild {guild.id} user {user_id} formation {formation_name} position {waitlist_position}."
        )
        try:
            member = guild.get_member(user_id) or await guild.fetch_member(user_id)
        except Exception:
            logger.exception(f"Unexpected error while fetching member {user_id} in guild {guild.id}.")
            return
        if member.bot:
            return

        logger.debug(f"Resolved member {member.id} ({member.display_name}) for waitlist DM in guild {guild.id}.")

        position_text = f"en {waitlist_position}{'ème' if waitlist_position > 1 else 'ère'} position"
        message = (
            f"Salut {member.display_name} !\n"
            f"On sait que tu es intéressé·e par **{formation_name}**, mais il n'y a plus de places disponibles.\n"
            f"Tu es {position_text} dans la liste d'attente. On te tiendra informé·e si suffisamment de places se libèrent.\n\n"
            f"*Ce message a été envoyé par un bot. Pour plus d'informations merci de contacter {contacts}.*"
        )
        try:
            await member.send(message)
        except Exception:
            logger.exception(f"Failed to send waitlist DM to user {user_id} in guild {guild.id}.")
        else:
            logger.info(
                f"Sent waitlist DM to user {user_id} in guild {guild.id} for formation {formation_name} (position {waitlist_position})."
            )

    @staticmethod
    def _load_state() -> dict[str, dict[str, Any]]:
        """Load the state from the JSON file.

        Returns:
            dict[str, dict[str, Any]]: The loaded state, or an empty dictionary if the file does not exist or an error occurs.
        """
        try:
            with open(DATA_FILE, encoding="utf-8") as f:
                state = json.load(f)
        except FileNotFoundError:
            logger.debug(f"Formations state file {DATA_FILE} not found; starting with empty state.")
            return {}
        except json.JSONDecodeError:
            logger.exception(f"Failed to decode formations state from {DATA_FILE}.")
            return {}
        except Exception:
            logger.exception(f"Unexpected error while loading formations state from {DATA_FILE}.")
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
            Path(Path(DATA_FILE).parent).mkdir(parents=True, exist_ok=True)
            with open(DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
        except OSError:
            logger.exception(f"Failed to persist formations state to {DATA_FILE}")
        except TypeError:
            logger.exception("Invalid data encountered while serializing formations state.")
        except Exception:
            logger.exception(f"Unexpected error while saving formations state to {DATA_FILE}.")
        else:
            logger.debug(f"Saved formations state to {DATA_FILE}.")

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

    def _get_guild_draft(self, guild_id: int) -> dict[str, Any]:
        """Get the draft state for a specific guild.

        Args:
            guild_id (int): The ID of the guild.

        Returns:
            dict[str, Any]: The draft of the guild.
        """
        guild_state = self._get_guild_state(guild_id)
        if "draft" not in guild_state:
            logger.debug(f"Initializing draft state for guild {guild_id}.")
            self._set_guild_state(guild_id, guild_state)
            guild_state["draft"] = {"intro": "", "fms": [], "end": ""}
        return guild_state["draft"]

    def _set_guild_draft(self, guild_id: int, draft: dict[str, Any]) -> None:
        """Set the draft for a specific guild.

        Args:
            guild_id (int): The ID of the guild.
            draft (dict[str, Any]): The draft of the guild.
        """
        guild_state = self._get_guild_state(guild_id)
        guild_state["draft"] = draft
        self._set_guild_state(guild_id, guild_state)

    def _get_last_published_in_guild(self, guild_id: int) -> dict[str, Any] | None:
        """Get the last published state for a specific guild.

        Args:
            guild_id (int): The ID of the guild.

        Returns:
            dict[str, Any] | None: The last published state of the guild, or None if not found.
        """
        guild_state = self._get_guild_state(guild_id)
        published = guild_state.get("published")
        if not published:
            logger.warning(f"No published formations data stored for guild {guild_id}.")
            return None
        return published

    def _set_last_published_in_guild(self, guild_id: int, published: dict[str, Any]) -> None:
        """Set the last published state for a specific guild.

        Args:
            guild_id (int): The ID of the guild.
            published (dict[str, Any]): The published state of the guild.
        """
        guild_state = self._get_guild_state(guild_id)
        guild_state["published"] = published
        self._set_guild_state(guild_id, guild_state)

    def _log_reaction(
        self, guild_id: int, message_id: int, user_id: int, emoji: str, action: str, user_name: str | None = None
    ) -> None:
        """Log a reaction event.

        Args:
            guild_id (int): The ID of the guild.
            message_id (int): The ID of the message.
            user_id (int): The ID of the user.
            emoji (str): The emoji used in the reaction.
            action (str): The action taken (e.g., "add" or "remove").
            user_name (str | None, optional): The name of the user. Defaults to None.
        """
        self._purge_all_reaction_logs()
        guild_state = self._get_guild_state(guild_id)
        log = guild_state.get("reactions_log") or []
        normalized_action = action[9:].lower()
        ts_iso = datetime.now().isoformat(timespec="seconds")
        if user_name is None:
            user_name = self._get_last_known_user_name(log, message_id, user_id)
        log.append(
            {
                "message_id": message_id,
                "user_id": user_id,
                "user_name": user_name,
                "emoji": emoji,
                "action": normalized_action,
                "ts_iso": ts_iso,
            }
        )
        guild_state["reactions_log"] = log
        logger.debug(
            f"Logged {normalized_action} reaction for guild {guild_id} message {message_id} user {user_id} with emoji {emoji}."
        )
        self._set_guild_state(guild_id, guild_state)

    def _purge_all_reaction_logs(self) -> None:
        """Apply retention to all guild reaction logs and persist changes."""
        changed = False
        removed_total = 0
        for guild_state in self.state.values():
            log = guild_state.get("reactions_log")
            if not log:
                continue
            filtered = self._purge_reaction_log(list(log))
            if len(filtered) != len(log):
                removed_total += len(log) - len(filtered)
                guild_state["reactions_log"] = filtered
                changed = True
        if changed:
            logger.debug(f"Purged {removed_total} reaction log entries across guilds.")
            self._save_state(self.state)

    @staticmethod
    def _purge_reaction_log(log: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Remove reaction log entries older than the retention window.

        Args:
            log (list[dict[str, Any]]): The reaction log to purge.

        Returns:
            list[dict[str, Any]]: The filtered reaction log.
        """
        cutoff = datetime.now() - REACTION_LOG_RETENTION
        filtered: list[dict[str, Any]] = []
        for entry in log:
            ts_iso = entry.get("ts_iso")
            if not ts_iso:
                filtered.append(entry)
                continue
            try:
                ts = datetime.fromisoformat(ts_iso)
            except Exception:
                filtered.append(entry)
                continue
            if ts >= cutoff:
                filtered.append(entry)
        return filtered

    @staticmethod
    def _get_last_known_user_name(log: list[dict[str, Any]], message_id: int, user_id: int) -> str | None:
        """Get the last known user name from the log.

        Args:
            log (list[dict[str, Any]]): The reaction log.
            message_id (int): The ID of the message.
            user_id (int): The ID of the user.

        Returns:
            str | None: The last known user name, or None if not found.
        """
        for entry in reversed(log):
            if entry.get("message_id") == message_id and entry.get("user_id") == user_id and entry.get("user_name"):
                return entry.get("user_name")
        return None

    def _get_reaction_history(self, guild_id: int, message_id: int) -> list[dict[str, Any]]:
        """Get the reaction history for a specific message in a guild.

        Args:
            guild_id (int): The ID of the guild.
            message_id (int): The ID of the message.

        Returns:
            list[dict[str, Any]]: The reaction history for the message.
        """
        guild_state = self._get_guild_state(guild_id)
        history = [event for event in guild_state.get("reactions_log", []) or [] if event.get("message_id") == message_id]
        history.sort(key=lambda ev: ev.get("ts_iso", ""))
        logger.debug(f"Loaded {len(history)} reaction events for guild {guild_id} message {message_id}.")
        return history

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
        channel_id = pub.get("channel_id")
        message_id = pub.get("message_id")
        if not guild or not channel_id or not message_id:
            logger.warning(
                f"Incomplete published formations state for guild {guild_id} (channel={channel_id}, message={message_id})."
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

        message_payload: dict[str, Any] = pub.get("message", {})
        intro = message_payload.get("intro")
        end = message_payload.get("end")

        raw_fms = message_payload.get("fms", [])
        fms: list[Formation] = []
        for entry in raw_fms:
            fms.append(Formation(**entry))
        tracked_emojis = {fm.emoji for fm in fms if fm.emoji}

        if not intro or not end or not fms:
            logger.warning(
                f"Published formations payload incomplete for guild {guild_id}; intro={bool(intro)} end={bool(end)} formations={len(fms)}."
            )
            return

        history = self._get_reaction_history(guild_id, message_id)

        last_add: dict[tuple[str, int], datetime] = {}
        for event in history:
            if event.get("action") != "add":
                continue
            emoji = str(event.get("emoji", ""))
            user_id = event.get("user_id")
            if not emoji or user_id is None or emoji not in tracked_emojis:
                continue
            try:
                ts = datetime.fromisoformat(event.get("ts_iso", ""))
            except (TypeError, ValueError):
                ts = datetime.min
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
                reactions_snapshot.setdefault(emoji_str, {})[user.id] = user.name

        waitlist_notifications: list[tuple[int, str, int]] = []
        promotion_notifications: list[tuple[int, Formation]] = []
        registration_notifications: list[tuple[int, Formation]] = []

        for fm in fms:
            prev_registered_ids = {entry.get("user_id") for entry in fm.registered_users if entry.get("user_id") is not None}
            prev_waitlisted_ids = {entry.get("user_id") for entry in fm.waitlisted_users if entry.get("user_id") is not None}
            current_users = reactions_snapshot.get(fm.emoji, {})
            ordered_users = sorted(
                ((uid, last_add.get((fm.emoji, uid), datetime.min), username) for uid, username in current_users.items()),
                key=lambda item: (item[1], item[0]),
            )
            registered: list[dict[str, Any]] = []
            waitlisted: list[dict[str, Any]] = []
            for position, (uid, _, username) in enumerate(ordered_users):
                entry = {"user_id": uid, "username": username}
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

        updated_fms = [fm.to_dict() for fm in fms]
        pub["message"] = {
            "intro": intro,
            "end": end,
            "fms": updated_fms,
        }
        self._set_last_published_in_guild(guild_id, pub)

        content = self._render_message(intro, fms, end)

        try:
            await msg.edit(content=content, suppress=True)
        except Exception:
            logger.exception(f"Failed to edit message {msg.id} in channel {msg.channel.id}.")
        else:
            logger.info(f"Updated published formations message {msg.id} in channel {channel_id} for guild {guild_id}.")

        contacts: str = self._format_respo_contacts(guild)

        if promotion_notifications:
            logger.info(f"Dispatching {len(promotion_notifications)} waitlist promotion notification(s) for guild {guild_id}.")
            for user_id, fm in promotion_notifications:
                await self._send_promotion_dm(guild, user_id, fm, contacts)
        else:
            logger.debug(f"No waitlist promotion notifications for guild {guild_id}.")

        if registration_notifications:
            logger.info(f"Dispatching {len(registration_notifications)} new registration notification(s) for guild {guild_id}.")
            for user_id, fm in registration_notifications:
                await self._send_registration_dm(guild, user_id, fm, contacts)
        else:
            logger.debug(f"No new registration notifications for guild {guild_id}.")

        if waitlist_notifications:
            logger.info(f"Dispatching {len(waitlist_notifications)} waitlist notification(s) for guild {guild_id}.")
            for user_id, fm_name, waitlist_index in waitlist_notifications:
                await self._send_waitlist_dm(guild, user_id, fm_name, waitlist_index, contacts)
        else:
            logger.debug(f"No new waitlist notifications for guild {guild_id}.")

    def _format_current_registrations(
        self,
        history: list[dict[str, Any]],
        reactions_snapshot: dict[str, dict[int, dict[str, str]]],
        fm_meta: dict[str, dict[str, Any]],
    ) -> tuple[str, io.BytesIO | None]:
        """Format the current registrations for each formation.

        Args:
            history (list[dict[str, Any]]): The history of reactions.
            reactions_snapshot (dict[str, dict[int, dict[str, str]]]): The current reactions snapshot.
            fm_meta (dict[str, dict[str, Any]]): The mapping of emojis to formation metadata.

        Returns:
            tuple[str, io.BytesIO | None]: The formatted message and an optional file object.
        """
        last_add: dict[tuple[str, int], datetime] = {}
        for ev in history:
            if ev.get("action") != "add":
                continue
            emoji = str(ev.get("emoji", ""))
            user_id = ev.get("user_id")
            if user_id is None:
                continue
            key = (emoji, int(user_id))
            try:
                ts = datetime.fromisoformat(ev.get("ts_iso", ""))
            except (TypeError, ValueError):
                ts = datetime.min
            if key not in last_add or ts > last_add[key]:
                last_add[key] = ts

        lines: list[str] = []
        lines.append("**Inscriptions actuelles par formation (ordre d'inscription)**")
        lines.append("")

        emojis = set(reactions_snapshot.keys()) | set(fm_meta.keys())
        for emoji in emojis:
            name = fm_meta.get(emoji, {}).get("name", "")
            seats = fm_meta.get(emoji, {}).get("seats", 0)
            header = f"{emoji} **{name}**" if name else f"{emoji}"
            header += f" ({seats} place{'s' if seats != 1 else ''})"
            lines.append(header)

            current_users = reactions_snapshot.get(emoji, {})
            ordering: list[tuple[int, datetime]] = []
            for uid, _meta in current_users.items():
                ts = last_add.get((emoji, uid), datetime.min)
                ordering.append((uid, ts))
            ordering.sort(key=lambda item: (item[1], item[0]))

            split_index = len(ordering) if seats is None else min(seats, len(ordering))
            registered_entries = ordering[:split_index]
            waitlisted_entries = ordering[split_index:]

            if registered_entries:
                for pos, (uid, ts) in enumerate(registered_entries, start=1):
                    when = self._humanize_dt(ts).lower()[2:-2] if ts != datetime.min else "n/a"
                    lines.append(f"{pos}. <@{uid}> · inscrit·e le {when}")
            else:
                lines.append("_(aucune inscription)_")

            if waitlisted_entries:
                for pos, (uid, ts) in enumerate(waitlisted_entries, start=len(registered_entries) + 1):
                    when = self._humanize_dt(ts).lower()[2:-2] if ts != datetime.min else "n/a"
                    lines.append(f"{pos}. <@{uid}> · inscrit·e le {when} (en attente)")

            lines.append("")

        text = "\n".join(lines).strip()

        text_length = len(text)
        file_obj: io.BytesIO | None = None
        if text_length > MAX_MSG_CHARS:
            file_obj = io.BytesIO(text.encode("utf-8"))
            logger.debug(f"Registrations export exceeded {MAX_MSG_CHARS} chars ({text_length}); switching to attachment.")
            text = "**Inscriptions actuelles par formation (extrait)**\nLe contenu complet est joint en fichier texte."
        else:
            logger.debug(f"Registrations export length: {text_length} characters.")

        return text, file_obj

    # endregion


@deprecated("Load the cog using `bot.add_cog()` instead.")
async def setup(bot: commands.Bot) -> None:
    """Setup function to load the Formations cog.

    Args:
        bot (commands.Bot): The bot instance.
    """
    await bot.add_cog(FormationManagement(bot))


# TODO: dm les gens la veille de leurs formations à x heures / cmd
