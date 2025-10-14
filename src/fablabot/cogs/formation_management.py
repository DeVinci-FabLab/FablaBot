"""Cog for managing and publishing weekly training sessions (formations)."""

from __future__ import annotations

import asyncio
import csv
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
import io
import json
import logging
from pathlib import Path
import re
from typing import Any, override
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
from discord.ext import commands, tasks
from discord.utils import get
from emoji import EMOJI_DATA

from .utils import check_has_role, is_in_allowed_channel, log_request, send_dm_to_member

logger = logging.getLogger(__name__)


ALLOWED_ROLES = {"Respo Formations", "Admin -temp-", "Administrateur"}
DATA_FILE = "data/formations_state.json"
DISCORD_EMOJI_RE = re.compile(r"^<a?:\w+:\d+>$")
ROLE_MENTION_RE = re.compile(r"<@&(\d+)>")  # TODO remove
MAX_MSG_CHARS = 1900
REACTION_LOG_RETENTION = timedelta(days=30)
FM_REQUEST_FORMS = "https://forms.office.com/e/MqVdQujzjf"
TRAINER_NOTIFICATION_ADVANCE = timedelta(hours=1)


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
    description: str = ""
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

    Commands:
        - /fm help
        - /fm start intro:<str> end:<str> role:<@Role>
        - /fm edit_text [intro] [end]
        - /fm add emoji:<str> name:<str> trainer:<@Member> date:<DD/MM/YYYY> hour:<HH:MM> duration:<str> seats:<int> description:<str>
        - /fm edit index:<int> [emoji] [name] [trainer] [date] [hour] [duration] [seats] [description]
        - /fm remove index:<int>
        - /fm clear
        - /fm preview
        - /fm publish channel:<#salon>
        - /fm export [message_id] [publication_channel]

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
                  "action": "add"|"remove",
                  "ts_iso": str
                }
              ]
          }
        }
        ```
        """
        self._reaction_lock = asyncio.Lock()
        self._purge_all_reaction_logs()
        self._check_upcoming_formations.start()
        logger.info("FormationManagement initialized")

    @override
    async def cog_unload(self) -> None:
        """Clean up when the cog is unloaded."""
        self._check_upcoming_formations.cancel()
        logger.info("FormationManagement unloaded")

    # region ====== Fm Slash Commands Group ======
    fm_group = app_commands.Group(name="fm", description="Gère les annonces de Formations et les inscriptions.")

    @fm_group.command(name="help", description="Afficher l'aide pour les commandes de gestion des formations.")
    async def fm_help(self, interaction: Interaction) -> None:
        """Display help information for the formation management commands.

        Args:
            interaction (Interaction): The Discord interaction context.
        """
        help_message = (
            "**Commandes de gestion des formations :**\n"
            "- `/fm start <intro> <end> <role>` : Démarrer un nouveau brouillon de formation.\n"
            "- `/fm edit_text [intro] [end]` : Modifier le texte d'introduction et/ou de conclusion du brouillon.\n"
            "- `/fm add <emoji> <name> <trainer> <date> <hour> <duration> <seats> <description>` :"
            " Ajouter une nouvelle formation au brouillon.\n"
            "- `/fm edit <index> [emoji] [name] [trainer] [date] [hour] [duration] [seats] [description]` :"
            " Modifier une formation existante dans le brouillon.\n"
            "- `/fm remove <index>` : Supprimer une formation du brouillon.\n"
            "- `/fm clear` : Effacer le brouillon actuel.\n"
            "- `/fm preview` : Prévisualiser le brouillon actuel.\n"
            "- `/fm publish` <channel> : Publier le brouillon dans un salon spécifique.\n"
            "- `/fm export` [message_id] [publication_channel] : Exporter le brouillon sous forme de message.\n"
            "\n"
            "Assurez-vous d'avoir les permissions nécessaires pour utiliser ces commandes."
        )
        await interaction.response.send_message(help_message, ephemeral=True)

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
        assert isinstance(interaction.channel, TextChannel)

        emoji_set = {emoji for emoji in interaction.guild.emojis if emoji.name == "dvfl"}
        emoji = emoji_set.pop() if emoji_set else ":loudspeaker:"
        header = f"# [FORMATIONS] {emoji}"

        intro_body = intro.replace("\\n", "\n").strip()
        end_body = end.replace("\\n", "\n").strip()

        draft: dict[str, Any] = {
            "header": header,
            "role_id": role.id,
            "intro": intro_body,
            "fms": [],
            "end": end_body,
        }
        self._set_guild_draft(interaction.guild.id, draft)

        fms = [Formation(**x) for x in draft["fms"]]
        content = self._render_message(
            draft["header"],
            draft["role_id"],
            draft["intro"],
            fms,
            draft["end"],
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

        current_intro = draft.get("intro", "")
        current_end = draft.get("end", "")
        current_role_id = draft.get("role_id", 0)

        updated_intro = current_intro if intro is None else intro.strip()
        updated_end = current_end if end is None else end.strip()

        updated_intro = updated_intro.replace("\\n", "\n")
        updated_end = updated_end.replace("\\n", "\n")

        role_changed = False
        if role is not None:
            new_role_id = role.id
            role_changed = current_role_id != new_role_id
            draft["role_id"] = new_role_id

        if updated_intro == current_intro and updated_end == current_end and not role_changed:
            await interaction.response.send_message(
                "Aucune modification détectée.",
                ephemeral=True,
            )
            return

        draft["intro"] = updated_intro
        draft["end"] = updated_end
        self._set_guild_draft(interaction.guild.id, draft)

        fms = [Formation(**x) for x in draft["fms"]]
        content = self._render_message(
            draft["header"],
            draft["role_id"],
            draft["intro"],
            fms,
            draft["end"],
        )

        intro_changed = updated_intro != current_intro
        end_changed = updated_end != current_end
        logger.info(
            f"Guild {interaction.guild.id} updated draft intro/end "
            f"(intro_changed={intro_changed}, end_changed={end_changed}, role_changed={role_changed})."
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
        trainer: Member,
        date: str,
        hour: str,
        duration: str,
        seats: app_commands.Range[int, 1, 500],
        description: str = "",
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

        if not emoji_clean or (
            emoji_clean not in EMOJI_DATA
            and not 0x1F1E6 <= ord(emoji_clean) <= 0x1F1FF
            and not DISCORD_EMOJI_RE.match(emoji_clean)
        ):
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

        preview = self._render_message(
            draft["header"],
            draft["role_id"],
            draft["intro"],
            fms,
            draft["end"],
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
            if not candidate or (
                candidate not in EMOJI_DATA
                and not 0x1F1E6 <= ord(candidate) <= 0x1F1FF
                and not DISCORD_EMOJI_RE.match(candidate)
            ):
                logger.warning(f"Guild {interaction.guild.id} tried to edit formation with invalid emoji: {candidate!r}.")
                await interaction.response.send_message("Émoji invalide.", ephemeral=True)
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

        new_seats = original.seats if seats is None else seats

        new_start_iso = original.start_iso
        if date is not None or hour is not None:
            date_part = date.strip() if date is not None else original.start_dt.strftime("%d/%m/%Y")
            hour_part = hour.strip() if hour is not None else original.start_dt.strftime("%H:%M")
            try:
                new_start_iso = self._parse_date_time(date_part, hour_part).isoformat()
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

        preview = self._render_message(
            draft["header"],
            draft["role_id"],
            draft["intro"],
            fms,
            draft["end"],
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
        preview = self._render_message(
            draft["header"],
            draft["role_id"],
            draft["intro"],
            fms,
            draft["end"],
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
        draft: dict[str, Any] = self._get_guild_draft(interaction.guild.id)
        header = draft["header"]
        role_id = draft["role_id"]
        intro = draft["intro"]
        end = draft["end"]
        draft = {"header": header, "role_id": role_id, "intro": intro, "fms": [], "end": end}
        self._set_guild_draft(interaction.guild.id, draft)

        fms = [Formation(**x) for x in draft["fms"]]
        content = self._render_message(
            header,
            role_id,
            intro,
            fms,
            end,
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

        await interaction.response.send_message("Génération du message en cours...")
        assert interaction.guild is not None
        draft = self._get_guild_draft(interaction.guild.id)
        fms = [Formation(**x) for x in draft["fms"]]
        fms.sort(key=lambda x: x.start_dt)

        content = self._render_message(
            draft["header"],
            draft["role_id"],
            draft["intro"],
            fms,
            draft["end"],
        )
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
        fms = [Formation(**x) for x in draft["fms"]]
        if not fms:
            logger.warning(f"Guild {interaction.guild.id} tried to publish empty formations draft.")
            await interaction.response.send_message("Le brouillon ne contient aucune formation.", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)

        fms.sort(key=lambda x: x.start_dt)
        content = self._render_message(
            draft["header"],
            draft["role_id"],
            draft["intro"],
            fms,
            draft["end"],
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

        published_message = {
            "header": draft["header"],
            "role_id": draft["role_id"],
            "intro": draft["intro"],
            "end": draft["end"],
            "fms": [],
        }

        success_reactions = 0
        for fm in fms:
            fm_dict = fm.to_dict()
            published_message["fms"].append(fm_dict)
            try:
                await msg.add_reaction(fm.emoji)
                success_reactions += 1
            except Exception:
                logger.exception(
                    f"Guild {interaction.guild.id} failed to add reaction {fm.emoji!r} "
                    f"for formation {fm.name!r} in published message."
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
        await interaction.followup.send(
            f"Message publié dans {channel.mention} (ID: `{msg.id}`) avec "
            f"{success_reactions}/{len(fms)} réaction(s) ajoutée(s)."
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
            interaction (Interaction): The Discord interaction context.
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

        published = self._get_last_published_in_guild(interaction.guild.id)
        if not published:
            logger.error(f"Guild {interaction.guild.id} has no published message but no ID/channel was given.")
            await interaction.response.send_message("Aucun message publié enregistré.", ephemeral=True)
            return

        await interaction.response.send_message("Export en cours...")

        message_payload = published.get("message", {})
        raw_fms = message_payload.get("fms", [])
        fms: list[Formation] = [Formation(**fm_dict) for fm_dict in raw_fms]

        reg_text, reg_file = self._format_current_registrations(fms)
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

            member_name = member.name if member else None

            self._log_reaction(
                payload.guild_id,
                payload.message_id,
                payload.user_id,
                emoji_str,
                payload.event_type,
                member_name,
            )

            await self._update_published_message(payload.guild_id)

    # endregion Event Listeners

    # region ====== Helpers ======
    # -- Parsing & Rendering --

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

    def _render_message(
        self,
        header: str,
        role_id: int,
        intro: str,
        fms: list[Formation],
        end: str,
    ) -> str:
        """Render the message for the formations.

        Args:
            header (str): The header line (e.g. title).
            role_id (int): The role mention to prepend.
            intro (str): The introduction text.
            fms (list[Formation]): The list of formations to include in the message.
            end (str): The ending text.

        Returns:
            str: The rendered message.
        """
        logger.debug("Rendering formations message.")
        lines: list[str] = []
        header_text = header.strip()
        intro_block = intro.strip()
        lines.append(header_text)
        lines.append(f"Hey <@&{role_id}> !")
        if intro_block:
            lines.append(intro_block)
        lines.append("")

        for fm in fms:
            line_block = [
                f"{fm.emoji} **{fm.name}** avec {fm.trainer_mention}",
                f":date: {self._humanize_dt(fm.start_dt)}  — "
                f":hourglass_flowing_sand: {fm.duration}  — "
                f":busts_in_silhouette: {len(fm.registered_users)}/{fm.seats} place(s)",
            ]
            line_block += [fm.description] if fm.description else []
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

    # TODO: remove
    def _normalize_message_payload(self, payload: dict[str, Any]) -> bool:
        """Ensure message payloads follow the expected schema.

        Args:
            payload (dict[str, Any]): Draft or published message payload.

        Returns:
            bool: True if the payload was modified.
        """
        changed = False

        intro_text = payload.get("intro")
        if intro_text is None:
            payload["intro"] = ""
            intro_text = ""
            changed = True
        intro_lines = intro_text.splitlines()

        def pop_leading_blank() -> None:
            nonlocal intro_lines, changed
            while intro_lines and not intro_lines[0].strip():
                intro_lines.pop(0)
                changed = True

        pop_leading_blank()

        header_line: str | None = None
        if intro_lines:
            first_line = intro_lines[0].strip()
            if first_line.startswith("# [FORMATIONS]"):
                header_line = first_line
                intro_lines.pop(0)
                changed = True

        if header_line is not None:
            if payload.get("header") != header_line:
                payload["header"] = header_line
                changed = True
        elif "header" not in payload:
            payload["header"] = ""
            changed = True

        pop_leading_blank()

        role_id_value = payload.get("role_id")
        if intro_lines:
            role_match = ROLE_MENTION_RE.search(intro_lines[0])
            if role_match:
                extracted_role_id = int(role_match.group(1))
                if role_id_value != extracted_role_id:
                    payload["role_id"] = extracted_role_id
                    role_id_value = extracted_role_id
                    changed = True
                intro_lines.pop(0)
                changed = True

        if "role_id" not in payload:
            payload["role_id"] = role_id_value
            changed = True

        pop_leading_blank()

        normalized_intro = "\n".join(intro_lines).strip()
        if payload.get("intro") != normalized_intro:
            payload["intro"] = normalized_intro
            changed = True

        return changed

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

    def _format_formation_export(self, formation: Formation) -> str:
        """Format the export of a single formation with registered and waitlisted users.

        Args:
            formation (Formation): The formation to export.

        Returns:
            str: The formatted export string.
        """
        lines: list[str] = []

        lines.append(f"{formation.emoji} **{formation.name}** ({formation.seats} place{'s' if formation.seats != 1 else ''})")

        if formation.registered_users:
            for idx, user_entry in enumerate(formation.registered_users, start=1):
                user_id = user_entry.get("user_id")
                username = user_entry.get("username", "Utilisateur inconnu")
                ts = user_entry.get("ts_iso", datetime.min.isoformat(timespec="seconds"))
                dt = datetime.fromisoformat(ts)
                when = self._humanize_dt(dt).lower()[2:-2] if dt != datetime.min else "n/a"
                lines.append(f"{idx}. <@{user_id}> ({username}) · inscrit·e le {when}")
        else:
            lines.append("_(Aucune inscription)_")

        if formation.waitlisted_users:
            for idx, user_entry in enumerate(formation.waitlisted_users, start=len(formation.registered_users) + 1):
                user_id = user_entry.get("user_id")
                username = user_entry.get("username", "Utilisateur inconnu")
                ts = user_entry.get("ts_iso", datetime.min)
                dt = datetime.fromisoformat(ts)
                when = self._humanize_dt(dt).lower()[2:-2] if dt != datetime.min else "n/a"
                lines.append(f"{idx}. <@{user_id}> ({username}) · inscrit·e le {when} (en attente)")

        return "\n".join(lines)

    # -- Notifications --

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
        try:
            member = guild.get_member(user_id) or await guild.fetch_member(user_id)
        except Exception:
            logger.exception(f"Failed to fetch member {user_id} for registration DM in guild {guild.id}.")
            return
        logger.debug(f"Resolved member {member.id} ({member.display_name}) for registration DM in guild {guild.id}.")

        datetime_text = self._humanize_dt(datetime.fromisoformat(formation.start_iso)).lower()[2:-2]

        message = (
            f"Salut {member.display_name} !\n"
            f"Ton inscription à la formation **{formation.name}** le {datetime_text} a bien été enregistrée.\n"
            "Si tu ne peux finalement pas y participer, pense à retirer ta réaction pour libérer la place.\n\n"
            f"*Ce message a été envoyé par un bot. Pour plus d'informations merci de contacter {contacts}.*"
        )

        await send_dm_to_member(logger, guild, member, message, f"registration for formation {formation.name}")

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
        try:
            member = guild.get_member(user_id) or await guild.fetch_member(user_id)
        except Exception:
            logger.exception(f"Unexpected error while fetching member {user_id} in guild {guild.id}.")
            return
        logger.debug(f"Resolved member {member.id} ({member.display_name}) for waitlist DM in guild {guild.id}.")

        position_text = f"en **{waitlist_position}{'e' if waitlist_position > 1 else 're'} position**"
        message = (
            f"Salut {member.display_name} !\n"
            f"On sait que la formation **{formation_name}** t'intéresse, mais toutes les places sont déjà prises.\n"
            f"Tu es {position_text} sur la liste d'attente. Nous te préviendrons si une place se libère.\n\n"
            f"*Ce message a été envoyé par un bot. Pour plus d'informations merci de contacter {contacts}.*"
        )

        await send_dm_to_member(logger, guild, member, message, f"waitlist for formation {formation_name}")

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
        try:
            member = guild.get_member(user_id) or await guild.fetch_member(user_id)
        except Exception:
            logger.exception(f"Unexpected error while fetching member {user_id} in guild {guild.id}.")
            return
        logger.debug(f"Resolved member {member.id} ({member.display_name}) for waitlist promotion DM in guild {guild.id}.")

        datetime_text = self._humanize_dt(datetime.fromisoformat(formation.start_iso)).lower()[2:-2]

        message = (
            f"Salut {member.display_name} !\n"
            "Bonne nouvelle : une place s'est libérée ! "
            f"Tu es désormais inscrit·e à la formation **{formation.name}** le {datetime_text}.\n"
            "Si tu ne peux finalement pas y participer, pense à retirer ta réaction pour libérer la place.\n\n"
            f"*Ce message a été envoyé par un bot. Pour plus d'informations merci de contacter {contacts}.*"
        )

        await send_dm_to_member(logger, guild, member, message, f"promotion for formation {formation.name}")

    async def _notify_trainer_before_formation(
        self, guild: Guild, formation: Formation, published_data: dict[str, Any], contacts: str
    ) -> None:
        """Send a DM to the trainer with the list of registered attendees.

        Args:
            guild (Guild): The guild where the formation is taking place.
            formation (Formation): The formation starting soon.
            published_data (dict[str, Any]): The published message data.
            contacts (str): The contact string for formation managers.
        """
        trainer_mention = formation.trainer_mention
        trainer_id_match = re.search(r"<@!?(\d+)>", trainer_mention)
        if not trainer_id_match:
            logger.warning(f"Could not extract trainer ID from mention {trainer_mention!r} for formation {formation.name!r}.")
            return
        trainer_id = int(trainer_id_match.group(1))

        try:
            trainer = guild.get_member(trainer_id) or await guild.fetch_member(trainer_id)
        except Exception:
            logger.exception(f"Failed to fetch trainer {trainer_id} for formation {formation.name!r}.")
            return

        datetime_text = self._humanize_dt(formation.start_dt).lower()[2:-2]
        formation_export = self._format_formation_export(formation)

        message = (
            f"Salut {trainer.display_name} !\n"
            f"Ta formation **{formation.name}** commence bientôt (le {datetime_text}).\n\n"
            f"{formation_export}\n\n"
            f"*Ce message a été envoyé par un bot. Pour plus d'informations merci de contacter {contacts}.*"
        )

        await send_dm_to_member(logger, guild, trainer, message, f"reminder for formation {formation.name}")

    # -- State --

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
            guild_state["draft"] = {"header": None, "role_id": None, "intro": "", "fms": [], "end": ""}
        # return guild_state["draft"]   # TODO: uncomment and remove after

        draft = guild_state["draft"]
        if self._normalize_message_payload(draft):
            logger.debug(f"Upgraded draft schema for guild {guild_id}.")
            self._set_guild_draft(guild_id, draft)
        return draft

    def _set_guild_draft(self, guild_id: int, draft: dict[str, Any]) -> None:
        """Set the draft for a specific guild.

        Args:
            guild_id (int): The ID of the guild.
            draft (dict[str, Any]): The draft of the guild.
        """
        self._normalize_message_payload(draft)  # TODO: remove
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
        # TODO: remove
        message_payload = published.get("message")
        if isinstance(message_payload, dict) and self._normalize_message_payload(message_payload):
            logger.debug(f"Upgraded published message schema for guild {guild_id}.")
            self._set_last_published_in_guild(guild_id, published)
        # until here
        return published

    def _set_last_published_in_guild(self, guild_id: int, published: dict[str, Any]) -> None:
        """Set the last published state for a specific guild.

        Args:
            guild_id (int): The ID of the guild.
            published (dict[str, Any]): The published state of the guild.
        """
        # TODO: remove
        message_payload = published.get("message")
        if isinstance(message_payload, dict):
            self._normalize_message_payload(message_payload)
        # until here
        guild_state = self._get_guild_state(guild_id)
        guild_state["published"] = published
        self._set_guild_state(guild_id, guild_state)

    # -- Reaction Logs & Updates --

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
        history.sort(key=lambda ev: ev.get("ts_iso", ""))  # pyright: ignore
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
        header = message_payload.get("header")
        role_id = message_payload.get("role_id")
        intro = message_payload.get("intro")
        end = message_payload.get("end")

        raw_fms = message_payload.get("fms", [])
        fms: list[Formation] = [Formation(**fm_dict) for fm_dict in raw_fms]
        tracked_emojis = {fm.emoji for fm in fms if fm.emoji}

        if not header or not role_id or not intro or not end or not fms:
            logger.warning(
                f"Published formations payload incomplete for guild {guild_id}; "
                f"header={bool(header)} role_id={bool(role_id)} intro={bool(intro)} end={bool(end)} formations={len(fms)}."
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

        updated_fms = [fm.to_dict() for fm in fms]
        pub["message"] = {
            "header": header,
            "role_id": role_id,
            "intro": intro,
            "end": end,
            "fms": updated_fms,
        }
        self._set_last_published_in_guild(guild_id, pub)

        content = self._render_message(header, role_id, intro, fms, end)

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

    def _format_current_registrations(self, formations: list[Formation]) -> tuple[str, io.BytesIO | None]:
        """Format the current registrations for each formation using data from guild state.

        Args:
            formations (list[Formation]): The list of formations with up-to-date registration data.

        Returns:
            tuple[str, io.BytesIO | None]: The formatted message and an optional file object.
        """
        lines: list[str] = []
        lines.append("**Inscriptions actuelles par formation (ordre d'inscription)**")
        lines.append("")

        for formation in formations:
            formation_export = self._format_formation_export(formation)
            lines.append(formation_export)
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

    # -- Background Tasks --

    @tasks.loop(minutes=10)
    async def _check_upcoming_formations(self) -> None:
        """Check for formations starting in ~1 hour and notify trainers with registration export."""
        logger.debug("Checking for upcoming formations to notify trainers.")
        now = datetime.now()
        notification_window_start = now + TRAINER_NOTIFICATION_ADVANCE - timedelta(minutes=10)
        notification_window_end = now + TRAINER_NOTIFICATION_ADVANCE + timedelta(minutes=10)

        for guild_id_str, guild_state in self.state.items():
            guild_id = int(guild_id_str)
            guild = self.bot.get_guild(guild_id)
            if not guild:
                continue

            published = self._get_last_published_in_guild(guild_id)
            if not published:
                continue

            message_payload = published.get("message", {})
            raw_fms = message_payload.get("fms", [])
            if not raw_fms:
                continue

            notified_formations = guild_state.get("notified_formations", [])
            notified_keys = {(nf.get("formation_emoji"), nf.get("start_iso")) for nf in notified_formations}

            for fm_dict in raw_fms:
                fm = Formation(**fm_dict)
                formation_key = (fm.emoji, fm.start_iso)

                if formation_key in notified_keys:
                    continue

                if notification_window_start <= fm.start_dt <= notification_window_end:
                    await self._notify_trainer_before_formation(
                        guild,
                        fm,
                        published,
                        self._format_respo_contacts(guild),
                    )

                    notified_formations.append(
                        {
                            "formation_emoji": fm.emoji,
                            "formation_name": fm.name,
                            "start_iso": fm.start_iso,
                            "notified_at": now.isoformat(),
                        }
                    )
                    guild_state["notified_formations"] = notified_formations
                    self._set_guild_state(guild_id, guild_state)

    @_check_upcoming_formations.before_loop
    async def _before_check_upcoming_formations(self) -> None:
        """Wait for the bot to be ready before starting the background task."""
        await self.bot.wait_until_ready()
        logger.info("Formation notification task started")

    # endregion Helpers


@deprecated("Load the cog using `bot.add_cog()` instead.")
async def setup(bot: commands.Bot) -> None:
    """Setup function to load the Formations cog.

    Args:
        bot (commands.Bot): The bot instance.
    """
    await bot.add_cog(FormationManagement(bot))
