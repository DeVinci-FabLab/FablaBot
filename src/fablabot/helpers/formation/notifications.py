"""Notification management for formations (DMs to users and trainers)."""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
from typing import TYPE_CHECKING, Literal

from fablabot.helpers.constants import RoleNames
from fablabot.helpers.formation.attendance import trainer_id
from fablabot.helpers.formation.rendering import discord_timestamp, format_formation_export
from fablabot.helpers.utils import counted, get_members_by_role, get_or_fetch_member, send_dm_to_member

if TYPE_CHECKING:
    from discord import Guild

    from fablabot.models.formation import Formation

logger = logging.getLogger(__name__)


@dataclass
class DmTally:
    """Who an update told by private message, counted by member: a member told twice is one.

    Attributes:
        told (set[int]): The members to whom Discord took at least one message.
        failed (set[int]): The members at least one of whose messages could not be sent.
    """

    told: set[int] = field(default_factory=set)
    failed: set[int] = field(default_factory=set)

    def record(self, user_id: int, sent: bool) -> None:
        """Note the outcome of one message to a member."""
        (self.told if sent else self.failed).add(user_id)

    @property
    def refused(self) -> int:
        """Count the members that no message reached."""
        return len(self.failed - self.told)


def _footer(contacts: str) -> str:
    """Write the end every bot message to a member carries: it is a bot's, and who to ask."""
    return f"\n\n*Ce message a été envoyé par un bot. Pour plus d'informations merci de contacter {contacts}.*"


def changes_members_plans(before: Formation, after: Formation) -> bool:
    """Tell whether a session's start or trainer differs: the two values that can keep a member from coming.

    Args:
        before (Formation): The session as published.
        after (Formation): The session as the update leaves it.

    Returns:
        bool: True when the members of the session are owed a message.
    """
    return before.start_dt.timestamp() != after.start_dt.timestamp() or before.trainer_mention != after.trainer_mention


async def _send_update_dm(guild: Guild, user_id: int, body: str, contacts: str, dm_type: str) -> bool:
    """Send a member the body of an update message with the greeting and the footer; say whether it went through.

    Args:
        guild (Guild): The server.
        user_id (int): The member to tell.
        body (str): The text between the greeting and the footer.
        contacts (str): The contact string for formation managers.
        dm_type (str): What the message is, for the log lines: no training name.

    Returns:
        bool: True when Discord took the message, False when the member is gone or closed their private messages.
    """
    member = await get_or_fetch_member(guild, user_id)
    if member is None:
        logger.error(f"Failed to fetch member {user_id} for {dm_type} DM in guild {guild.id}.")
        return False
    message = f"Salut {member.display_name} !\n{body}{_footer(contacts)}"
    return await send_dm_to_member(logger, guild, member, message, dm_type=dm_type) is not None


async def send_change_dm(  # noqa: PLR0913 - the two sessions, who and how; one message each
    guild: Guild, user_id: int, before: Formation, after: Formation, *, waitlisted: bool, contacts: str
) -> bool:
    """Tell a member of a session that its start or trainer changed, naming only what changed.

    Args:
        guild (Guild): The server.
        user_id (int): The registered or waitlisted member.
        before (Formation): The session as published.
        after (Formation): The session as the update leaves it.
        waitlisted (bool): Whether the member is on the waitlist, whose place is kept as a seat is.
        contacts (str): The contact string for formation managers.

    Returns:
        bool: True when a message was sent; False when nothing they care about changed or it could not be sent.
    """
    if not changes_members_plans(before, after):
        return False
    new_start, old_start = discord_timestamp(after.start_dt), discord_timestamp(before.start_dt)
    start_moved = new_start != old_start
    trainer_moved = before.trainer_mention != after.trainer_mention
    if start_moved and trainer_moved:
        what = (
            f"La formation **{after.name}** change : elle a lieu le {new_start} avec {after.trainer_mention} "
            f"(au lieu du {old_start} avec {before.trainer_mention})."
        )
    elif start_moved:
        what = (
            f"La formation **{after.name}** change de date : elle a lieu le {new_start} (au lieu du {old_start}), "
            f"toujours avec {after.trainer_mention}."
        )
    else:
        what = (
            f"La formation **{after.name}** change de formateur : {after.trainer_mention} l'anime "
            f"(au lieu de {before.trainer_mention}), toujours le {new_start}."
        )
    kept = (
        "Ta place en liste d'attente est gardée."
        if waitlisted
        else "Ton inscription est gardée. Si tu ne peux plus venir, retire ta réaction pour libérer la place."
    )
    return await _send_update_dm(guild, user_id, f"{what}\n{kept}", contacts, "change of a published session")


async def send_cancel_dm(guild: Guild, user_id: int, session: Formation, *, waitlisted: bool, contacts: str) -> bool:
    """Tell a member that their session was removed from the announcement and no longer counts.

    Args:
        guild (Guild): The server.
        user_id (int): The registered or waitlisted member.
        session (Formation): The session as it was published.
        waitlisted (bool): Whether the member was on the waitlist.
        contacts (str): The contact string for formation managers.

    Returns:
        bool: True when a message was sent.
    """
    ends = "ta place en liste d'attente ne compte plus" if waitlisted else "ton inscription ne compte plus"
    body = (
        f"La formation **{session.name}** du {discord_timestamp(session.start_dt)} "
        f"est annulée et retirée de l'annonce : {ends}."
    )
    return await _send_update_dm(guild, user_id, body, contacts, "removal of a published session")


async def send_demotion_dm(guild: Guild, user_id: int, session: Formation, position: int, contacts: str) -> bool:
    """Tell a member who held a seat that fewer places moved them to the waitlist.

    Args:
        guild (Guild): The server.
        user_id (int): The member moved to the waitlist.
        session (Formation): The session with its new number of seats.
        position (int): The member's position on the waitlist.
        contacts (str): The contact string for formation managers.

    Returns:
        bool: True when a message was sent.
    """
    places = counted(session.seats, "place", "places")
    rank = f"{position}{'re' if position == 1 else 'e'}"
    body = (
        f"La formation **{session.name}** du {discord_timestamp(session.start_dt)} passe à {places} : "
        f"tu passes en liste d'attente, en **{rank} position**. Nous te préviendrons si une place se libère."
    )
    return await _send_update_dm(guild, user_id, body, contacts, "move to the waitlist of a published session")


async def send_trainer_dm(guild: Guild, session: Formation, channel_id: int, contacts: str) -> bool:
    """Tell the trainer of an added session, or the new trainer of a kept one, that they lead it.

    Args:
        guild (Guild): The server.
        session (Formation): The session as the update leaves it.
        channel_id (int): The channel of the announcement.
        contacts (str): The contact string for formation managers.

    Returns:
        bool: True when a message was sent; False when the trainer field holds no member or the message could not be sent.
    """
    user_id = trainer_id(session.trainer_mention)
    if user_id is None:
        return False
    body = (
        f"Tu animes la formation **{session.name}** le {discord_timestamp(session.start_dt)} : "
        f"l'annonce de <#{channel_id}> vient d'être mise à jour."
    )
    return await _send_update_dm(guild, user_id, body, contacts, "trainer of an updated announcement")


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
    logger.debug(f"Resolved member {member.id} for registration DM in guild {guild.id}.")

    datetime_text = discord_timestamp(formation.start_dt)

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
    logger.debug(f"Resolved member {member.id} for waitlist DM in guild {guild.id}.")

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
    logger.debug(f"Resolved member {member.id} for waitlist promotion DM in guild {guild.id}.")

    datetime_text = discord_timestamp(formation.start_dt)

    message = (
        f"Salut {member.display_name} !\n"
        "Bonne nouvelle : une place s'est libérée ! "
        f"Tu es désormais inscrit à la formation **{formation.name}** le {datetime_text}.\n"
        "Si tu ne peux finalement pas y participer, pense à retirer ta réaction pour libérer la place.\n\n"
        f"*Ce message a été envoyé par un bot. Pour plus d'informations merci de contacter {contacts}.*"
    )

    await send_dm_to_member(logger, guild, member, message, dm_type=f"promotion for formation {formation.name}")


async def notify_trainer_before_formation(guild: Guild, formation: Formation, contacts: str) -> None:
    """Send a DM to the trainer an hour before, with the list of registered attendees.

    Args:
        guild (Guild): The guild where the formation is taking place.
        formation (Formation): The formation starting soon.
        contacts (str): The contact string for formation managers.
    """
    trainer_mention = formation.trainer_mention
    trainer_user_id = trainer_id(trainer_mention)
    if trainer_user_id is None:
        logger.warning(f"Could not extract trainer ID from mention {trainer_mention!r} for formation {formation.name!r}.")
        return

    trainer = await get_or_fetch_member(guild, trainer_user_id)
    if trainer is None:
        logger.error(f"Failed to fetch trainer {trainer_user_id} for formation {formation.name!r}.")
        return

    datetime_text = discord_timestamp(formation.start_dt)
    formation_export = format_formation_export(formation)

    message = (
        f"Salut {trainer.display_name} !\n"
        f"Ta formation **{formation.name}** commence bientôt (le {datetime_text}).\n\n"
        f"{formation_export}\n\n"
        f"*Ce message a été envoyé par un bot. Pour plus d'informations merci de contacter {contacts}.*"
    )

    await send_dm_to_member(logger, guild, trainer, message, dm_type=f"reminder for formation {formation.name}")


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
    datetime_text = discord_timestamp(formation.start_dt)
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
    datetime_text = discord_timestamp(formation.start_dt)

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
