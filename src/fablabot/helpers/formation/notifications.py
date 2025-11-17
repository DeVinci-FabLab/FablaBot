"""Notification management for formations (DMs to users and trainers)."""

from __future__ import annotations

from datetime import datetime
import logging
import re
from typing import TYPE_CHECKING, Literal

from fablabot.helpers.constants import RoleNames
from fablabot.helpers.formation.rendering import format_formation_export, humanize_dt
from fablabot.helpers.utils import get_members_by_role, get_or_fetch_member, send_dm_to_member

if TYPE_CHECKING:
    from discord import Guild

    from fablabot.models.formation import Formation

logger = logging.getLogger(__name__)
# TODO: trop d'appel APIs discord ici, envisager un cache


async def send_registration_dm(
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
    member = await get_or_fetch_member(guild, user_id)
    if member is None:
        logger.error(f"Failed to fetch member {user_id} for registration DM in guild {guild.id}.")
        return
    logger.debug(f"Resolved member {member.id} ({member.display_name}) for registration DM in guild {guild.id}.")

    datetime_text = humanize_dt(datetime.fromisoformat(formation.start_iso)).lower()[2:-2]

    message = (
        f"Salut {member.display_name} !\n"
        f"Ton inscription à la formation **{formation.name}** le {datetime_text} a bien été enregistrée.\n"
        "Si tu ne peux finalement pas y participer, pense à retirer ta réaction pour libérer la place.\n\n"
        f"*Ce message a été envoyé par un bot. Pour plus d'informations merci de contacter {contacts}.*"
    )

    await send_dm_to_member(logger, guild, member, message, dm_type=f"registration for formation {formation.name}")


async def send_waitlist_dm(
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
    member = await get_or_fetch_member(guild, user_id)
    if member is None:
        logger.error(f"Failed to fetch member {user_id} for waitlist DM in guild {guild.id}.")
        return
    logger.debug(f"Resolved member {member.id} ({member.display_name}) for waitlist DM in guild {guild.id}.")

    position_text = f"en **{waitlist_position}{'e' if waitlist_position > 1 else 're'} position**"
    message = (
        f"Salut {member.display_name} !\n"
        f"On sait que la formation **{formation_name}** t'intéresse, mais toutes les places sont déjà prises.\n"
        f"Tu es {position_text} sur la liste d'attente. Nous te préviendrons si une place se libère.\n\n"
        f"*Ce message a été envoyé par un bot. Pour plus d'informations merci de contacter {contacts}.*"
    )

    await send_dm_to_member(logger, guild, member, message, dm_type=f"waitlist for formation {formation_name}")


async def send_promotion_dm(
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
    member = await get_or_fetch_member(guild, user_id)
    if member is None:
        logger.error(f"Failed to fetch member {user_id} for promotion DM in guild {guild.id}.")
        return
    logger.debug(f"Resolved member {member.id} ({member.display_name}) for waitlist promotion DM in guild {guild.id}.")

    datetime_text = humanize_dt(datetime.fromisoformat(formation.start_iso)).lower()[2:-2]

    message = (
        f"Salut {member.display_name} !\n"
        "Bonne nouvelle : une place s'est libérée ! "
        f"Tu es désormais inscrit·e à la formation **{formation.name}** le {datetime_text}.\n"
        "Si tu ne peux finalement pas y participer, pense à retirer ta réaction pour libérer la place.\n\n"
        f"*Ce message a été envoyé par un bot. Pour plus d'informations merci de contacter {contacts}.*"
    )

    await send_dm_to_member(logger, guild, member, message, dm_type=f"promotion for formation {formation.name}")


async def notify_trainer_before_formation(
    guild: Guild,
    formation: Formation,
    contacts: str,
    moment: Literal["hour_before", "start"] = "hour_before",
) -> None:
    """Send a DM to the trainer with the list of registered attendees.

    Args:
        guild (Guild): The guild where the formation is taking place.
        formation (Formation): The formation starting soon.
        contacts (str): The contact string for formation managers.
        moment (Literal["hour_before", "start"]): When the notification is sent.
    """
    trainer_mention = formation.trainer_mention
    trainer_id_match = re.search(r"<@!?(\d+)>", trainer_mention)
    if not trainer_id_match:
        logger.warning(f"Could not extract trainer ID from mention {trainer_mention!r} for formation {formation.name!r}.")
        return
    trainer_id = int(trainer_id_match.group(1))

    trainer = await get_or_fetch_member(guild, trainer_id)
    if trainer is None:
        logger.error(f"Failed to fetch trainer {trainer_id} for formation {formation.name!r}.")
        return

    datetime_text = humanize_dt(formation.start_dt).lower()[2:-2]
    formation_export = format_formation_export(formation)

    if moment == "hour_before":
        timing_line = f"Ta formation **{formation.name}** commence bientôt (le {datetime_text})."
        subject = f"reminder for formation {formation.name}"
    else:
        timing_line = f"Ta formation **{formation.name}** commence maintenant (le {datetime_text})."
        subject = f"start alert for formation {formation.name}"

    excusable_line = "Merci de transmettre au **CoDir** la liste des participants à excuser si besoin.\n\n"

    message = (
        f"Salut {trainer.display_name} !\n"
        f"{timing_line}\n\n"
        f"{formation_export}\n\n"
        f"{excusable_line if formation.excusable and moment == 'start' else ''}"
        f"*Ce message a été envoyé par un bot. Pour plus d'informations merci de contacter {contacts}.*"
    )

    await send_dm_to_member(logger, guild, trainer, message, dm_type=subject)


async def notify_responsible_before_formation(
    guild: Guild,
    formation: Formation,
    *,
    moment: Literal["hour_before", "start"] = "hour_before",
) -> None:
    """Send a DM to the training responsible with the list of registered attendees.

    Args:
        guild (Guild): The guild where the formation is taking place.
        formation (Formation): The formation starting soon.
        moment (Literal["hour_before", "start"]): When the notification is sent.
    """
    datetime_text = humanize_dt(formation.start_dt).lower()[2:-2]
    formation_export = format_formation_export(formation)

    if moment == "hour_before":
        timing_line = f"La formation **{formation.name}** commence bientôt (le {datetime_text})."
        subject = f"export reminder for formation {formation.name}"
    else:
        timing_line = f"La formation **{formation.name}** commence maintenant (le {datetime_text})."
        subject = f"start export for formation {formation.name}"

    responsibles = get_members_by_role(logger, guild, role=RoleNames.TRAININGS_MANAGER)

    for responsible in responsibles:
        message = f"Salut {responsible.display_name} !\n{timing_line}\n\n{formation_export}"

        await send_dm_to_member(logger, guild, responsible, message, dm_type=subject)

    if not responsibles:
        logger.error(f"No responsible found to notify for formation {formation.name!r} in guild {guild.id}.")


async def notify_participants_before_formation(
    guild: Guild,
    formation: Formation,
    contacts: str,
) -> None:
    """Send a DM to all participants of the formation.

    Args:
        guild (Guild): The guild where the formation is taking place.
        formation (Formation): The formation starting soon.
        contacts (str): The contact string for formation managers.
    """
    datetime_text = humanize_dt(formation.start_dt).lower()[2:-2]

    message_content = (
        f"La formation **{formation.name}** commence bientôt (le {datetime_text}).\n\n"
        f"*Ce message a été envoyé par un bot. Pour plus d'informations merci de contacter {contacts}.*"
    )

    for entry in formation.registered_users:
        participant_id = int(entry["user_id"])
        participant = await get_or_fetch_member(guild, participant_id)
        if participant is None:
            logger.error(f"Failed to fetch participant {participant_id} for formation {formation.name!r}.")
            continue

        message = f"Salut {participant.display_name} !\n{message_content}"

        await send_dm_to_member(logger, guild, participant, message, dm_type=f"reminder for formation {formation.name}")
