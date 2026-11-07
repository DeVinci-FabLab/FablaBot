"""Planning an update of the published announcement: what changes, what stays as published, and the message it makes."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from hashlib import sha256
import logging
from typing import TYPE_CHECKING, Final, Literal

from discord import HTTPException, NotFound, TextChannel
from discord.utils import escape_markdown, escape_mentions

from fablabot.helpers.constants import ANNOUNCEMENT_MAX_CHARS, MAX_SESSIONS, SESSION_EMOJIS
from fablabot.helpers.formation.catalogue import session_digest
from fablabot.helpers.formation.rendering import announcement_length, grouped
from fablabot.helpers.utils import RefusalKind

if TYPE_CHECKING:
    from collections.abc import Mapping

    from discord import Guild

    from fablabot.models.formation import FmMessageDraft, Formation, PublishedMessage

logger = logging.getLogger(__name__)

# The reminder tick opens a session's check up to 5 minutes before its start, so from then on the session stays as posted.
APPEL_OPENS_BEFORE: Final = timedelta(minutes=5)
_UNCHANGED: Final = "Rien n'a été modifié."
CHANGED_REFUSAL: Final = (
    f"L'annonce ou le brouillon a changé depuis ce résumé : presse de nouveau **Publier** pour le revoir. {_UNCHANGED}"
)
RETRY_REFUSAL: Final = f"L'annonce n'a pas pu être mise à jour pour l'instant : réessaie dans un instant. {_UNCHANGED}"

StepKind = Literal["changed", "added", "removed", "frozen"]


@dataclass(frozen=True)
class Step:
    """What an update does to one session.

    Attributes:
        kind (StepKind): ``changed``, ``added``, ``removed``, or ``frozen`` for a session left as it is.
        before (Formation | None): The session as published, None for an added one.
        after (Formation | None): The session as it would be, None for a removed one.
    """

    kind: StepKind
    before: Formation | None
    after: Formation | None


@dataclass(frozen=True)
class UpdatePlan:
    """An update of the published announcement, worked out and not yet done.

    Attributes:
        message (FmMessageDraft): The announcement as it would be, the kept sessions with their sign-ups and flags.
        steps (tuple[Step, ...]): The sessions that change, then the ones left as they are.
        text_changes (tuple[str, ...]): What changes in the text: ``intro``, ``conclusion``, ``lien``, ``rôle``.
        moves (Mapping[int, int]): The place in the list of each kept session whose place changes, old to new.
    """

    message: FmMessageDraft
    steps: tuple[Step, ...]
    text_changes: tuple[str, ...]
    moves: Mapping[int, int]

    @property
    def trainers_to_remind(self) -> tuple[str, ...]:
        """Name the sessions whose new trainer is owed a reminder that already went out for the old one.

        A new start gives the reminder back to everyone, and the tick sends it. A new trainer alone does not: the flag
        stays set so that the participants and the Respo are not reminded twice, and the new trainer is sent theirs.

        Returns:
            tuple[str, ...]: The emojis of the kept sessions whose reminder was sent, trainer changed and start did not.
        """
        return tuple(
            step.after.emoji
            for step in self.steps
            if step.kind == "changed"
            and step.before is not None
            and step.after is not None
            and step.before.notified_hour_before
            and _moment(step.before) == _moment(step.after)
            and step.before.trainer_mention != step.after.trainer_mention
        )

    def digest(self, message_id: int) -> str:
        """Summarise the plan in 12 hex digits, so that a confirmation can tell it from a plan that has changed since.

        Args:
            message_id (int): The published message the plan is for.

        Returns:
            str: The digest of the message ID, the texts and, per step, its kind and the values of its sessions; the
                sign-ups and the reminder flags are not in it, since members keep signing up while a summary is open.
        """
        message = self.message
        parts = [str(message_id), message.header, str(message.role_id), message.intro, message.end, message.request_forms_url]
        for step in self.steps:
            parts.append(step.kind)
            parts.extend(session_digest(session) if session else "" for session in (step.before, step.after))
        return sha256("\x1f".join(parts).encode()).hexdigest()[:12]


def gone_refusal(channel_id: int) -> str:
    """Say that the published message no longer exists in its channel, and what to do instead."""
    return f"L'annonce publiée n'existe plus dans <#{channel_id}> : publie une nouvelle annonce. {_UNCHANGED}"


async def posted_reacted_emojis(guild: Guild, published: PublishedMessage) -> frozenset[str] | tuple[str, RefusalKind]:
    """Read the posted message and name the emojis that carry a reaction other than the bot's, whatever session wears them.

    The reaction log misses what the bot did not hear: reactions made before it noted the emojis no session wears, while it
    was down or cut off from the gateway, or refused by a storage failure. Discord keeps them all, so the summary and the
    confirmation both read them here before they give an emoji to an added session. Another bot's reaction counts too, at
    the cost of one emoji of the pool left unused.

    Args:
        guild (Guild): The server.
        published (PublishedMessage): The announcement whose message is read.

    Returns:
        frozenset[str] | tuple[str, RefusalKind]: The emojis, or the refusal and its kind when the message is gone or
            Discord does not answer.
    """
    gone = gone_refusal(published.channel_id), RefusalKind.ANNOUNCEMENT_GONE
    channel = guild.get_channel(published.channel_id)
    if not isinstance(channel, TextChannel):
        return gone
    try:
        message = await channel.fetch_message(published.message_id)
    except NotFound:
        return gone
    except HTTPException:
        logger.exception(f"Guild {guild.id} could not read message {published.message_id} before updating it.")
        return RETRY_REFUSAL, RefusalKind.DISCORD_UNREADABLE
    return frozenset(str(reaction.emoji) for reaction in message.reactions if reaction.count > int(reaction.me))


def is_frozen(session: Formation, now: datetime) -> bool:
    """Tell whether a session is left as published: started, starting within 5 minutes, or already flagged at its start.

    Args:
        session (Formation): The session as published.
        now (datetime): The time to judge at, timezone-aware.

    Returns:
        bool: True when its check is or may be open.
    """
    return session.notified_at_start or session.start_dt <= now + APPEL_OPENS_BEFORE


def as_draft(message: FmMessageDraft) -> FmMessageDraft:
    """Write an announcement back as the draft that says the same: no sign-up, no waiting member, no reminder flag.

    Args:
        message (FmMessageDraft): The announcement.

    Returns:
        FmMessageDraft: A copy with every session emptied of its lists and flags.
    """
    emptied = [
        replace(fm, registered_users=[], waitlisted_users=[], notified_hour_before=False, notified_at_start=False)
        for fm in message.fms
    ]
    return replace(message, fms=emptied)


def _moment(session: Formation) -> int:
    """Read a session's start as an instant, so that two spellings of the same start are the same."""
    return int(session.start_dt.timestamp())


def _by_start(step: Step) -> tuple[int, str]:
    """Order steps by the start of their session as published, then by emoji."""
    session = step.before or step.after
    assert session is not None
    return _moment(session), session.emoji


def _retouched(kept: Formation, wanted: Formation) -> Formation:
    """Give a session the draft's values, keeping its sign-ups and flags; a new start re-arms the reminder.

    A new trainer alone does not: the reminder went out to everyone, and only the new trainer is owed it (see
    ``UpdatePlan.trainers_to_remind``).
    """
    moved = _moment(kept) != _moment(wanted)
    return replace(
        kept,
        name=wanted.name,
        trainer_mention=wanted.trainer_mention,
        start_iso=wanted.start_iso if moved else kept.start_iso,
        duration=wanted.duration,
        seats=wanted.seats,
        description=wanted.description,
        excusable=wanted.excusable,
        notified_hour_before=kept.notified_hour_before and not moved,
    )


def _text_changes(published: FmMessageDraft, draft: FmMessageDraft) -> tuple[str, ...]:
    """Name what differs in the announcement's text, in the order of the copy."""
    differing = (
        ("intro", published.intro.strip() != draft.intro.strip()),
        ("conclusion", published.end.strip() != draft.end.strip()),
        ("lien", published.request_forms_url != draft.request_forms_url),
        ("rôle", published.role_id != draft.role_id),
    )
    return tuple(name for name, differs in differing if differs)


def _refusal(draft: FmMessageDraft, sessions: list[Formation], now: datetime) -> str | None:
    """Tell why the draft's role, intro, conclusion or a session it changes or adds forbid an update, or None."""
    if not draft.role_id:
        return "Choisis d'abord le rôle à notifier : **Intro / conclusion**."
    if not draft.intro.strip() or not draft.end.strip():
        return "Écris d'abord l'intro et la conclusion : **Intro / conclusion**."
    if soon := next((fm for fm in sorted(sessions, key=_moment) if fm.start_dt <= now + APPEL_OPENS_BEFORE), None):
        when = "avant maintenant" if soon.start_dt < now else "dans moins de 5 minutes"
        return f"La session **{soon.emoji} {_plain(soon)}** commence {when} : change sa date ou retire-la."
    return None


def _given_emojis(sessions: list[Formation], taken: set[str]) -> list[str] | None:
    """Give each added session an emoji: its own when nothing holds it, else the first of the pool nothing holds.

    Args:
        sessions (list[Formation]): The added sessions, in the order they are appended.
        taken (set[str]): The emojis the announcement holds or that had reactions on it.

    Returns:
        list[str] | None: One emoji per session, or None when the pool has none left for one of them.
    """
    claimed = set(taken)
    keeps: list[str | None] = []
    for session in sessions:
        keeps.append(None if session.emoji in claimed else session.emoji)
        claimed.add(session.emoji)
    given: list[str] = []
    for keep in keeps:
        emoji = keep or next((e for e in SESSION_EMOJIS if e not in claimed), None)
        if emoji is None:
            return None
        claimed.add(emoji)
        given.append(emoji)
    return given


def _appended(fresh: list[Formation], taken: set[str]) -> list[Formation] | None:
    """Make the sessions to append from the draft's new ones, by start, each with its emoji and no sign-up.

    Returns:
        list[Formation] | None: The sessions, or None when the pool has no emoji left for one of them.
    """
    ordered = sorted(fresh, key=_moment)
    emojis = _given_emojis(ordered, taken)
    if emojis is None:
        return None
    return [
        replace(
            session,
            emoji=emoji,
            registered_users=[],
            waitlisted_users=[],
            notified_hour_before=False,
            notified_at_start=False,
        )
        for session, emoji in zip(ordered, emojis, strict=True)
    ]


def _unfit(count: int, *, removed: int, has_emoji: bool) -> str | None:
    """Tell why the sessions the update leaves cannot make an announcement: a removal, too many, or no emoji left."""
    if removed:
        return "Retirer une session d'une annonce publiée arrive bientôt : pour l'instant, publie une nouvelle annonce."
    if count > MAX_SESSIONS:
        return f"L'annonce compterait {count} sessions : Discord n'accepte pas plus de 20 réactions sur un message."
    if not has_emoji:
        return "Plus aucun émoji libre pour une session de plus sur cette annonce : publie une nouvelle annonce."
    return None


def _plain(session: Formation) -> str:
    """Write a session's name so that it shows as typed: no formatting, no mention."""
    return escape_mentions(escape_markdown(session.name))


def _held_back(published: FmMessageDraft, draft: FmMessageDraft, now: datetime) -> str | None:
    """Tell that the draft differs from the announcement only on a session left as published, naming the first, or None."""
    wanted = {fm.emoji: fm for fm in draft.fms}
    held = (
        fm
        for fm in sorted(published.fms, key=_moment)
        if is_frozen(fm, now) and (fm.emoji not in wanted or session_digest(wanted[fm.emoji]) != session_digest(fm))
    )
    if (first := next(held, None)) is None:
        return None
    return (
        f"La session **{first.emoji} {_plain(first)}** a déjà commencé ou commence dans moins de 5 minutes : "
        "elle reste telle quelle."
    )


def _sort_out(published: FmMessageDraft, draft: FmMessageDraft, now: datetime) -> tuple[dict[str, Formation], list[Formation]]:
    """Sort the draft's sessions into the ones that stand for a published session, by emoji, and the new ones.

    A frozen session is not changed by the draft: the draft's session on its emoji is the frozen session itself while it
    keeps its start, and a new session once it starts later.
    """
    announced = {fm.emoji: fm for fm in published.fms}
    wanted: dict[str, Formation] = {}
    fresh: list[Formation] = []
    for fm in draft.fms:
        known = announced.get(fm.emoji)
        if known is None or fm.emoji in wanted:
            fresh.append(fm)
        elif not is_frozen(known, now):
            wanted[fm.emoji] = fm
        elif fm.start_dt > now and _moment(fm) != _moment(known):
            fresh.append(fm)
    return wanted, fresh


def plan_update(
    published: FmMessageDraft, draft: FmMessageDraft, *, now: datetime, retired: frozenset[str]
) -> UpdatePlan | str:
    """Work out what an update of the published announcement by the draft would do, without doing it.

    A session is known by its emoji, the key of its sign-ups. A session started or about to start is left as published
    whatever the draft says.

    Args:
        published (FmMessageDraft): The announcement as stored, with its sign-ups.
        draft (FmMessageDraft): The draft, with the role to notify as the server still has it.
        now (datetime): The time to judge at, timezone-aware.
        retired (frozenset[str]): The emojis members reacted with on the published message, from the reaction log and from
            the message itself: none is given to a session added by the update, since the reactions still on the message
            would count for it.

    Returns:
        UpdatePlan | str: The plan, or the reason it cannot be made, ending with the word that nothing was changed.
    """
    wanted, fresh = _sort_out(published, draft, now)
    kept: list[Formation] = []
    changed: list[Step] = []
    frozen: list[Step] = []
    removed = 0
    for session in published.fms:
        if is_frozen(session, now):
            kept.append(session)
            frozen.append(Step("frozen", session, session))
        elif session.emoji in wanted:
            retouched = _retouched(session, wanted[session.emoji])
            kept.append(retouched)
            if session_digest(retouched) != session_digest(session):
                changed.append(Step("changed", session, retouched))
        else:
            removed += 1
    text_changes = _text_changes(published, draft)

    if not (changed or text_changes or fresh or removed):
        reason = (
            _held_back(published, draft, now) or "Le brouillon dit déjà ce que dit l'annonce publiée : rien à mettre à jour."
        )
        return f"{reason} {_UNCHANGED}"
    if reason := _refusal(draft, [*wanted.values(), *fresh], now):
        return f"{reason} {_UNCHANGED}"
    added = _appended(fresh, {fm.emoji for fm in published.fms} | retired)
    if reason := _unfit(len(kept) + len(fresh), removed=removed, has_emoji=added is not None):
        return f"{reason} {_UNCHANGED}"
    assert added is not None  # _unfit refused the lack of an emoji
    kept.extend(added)

    message = replace(
        published,
        role_id=draft.role_id,
        intro=draft.intro,
        end=draft.end,
        request_forms_url=draft.request_forms_url,
        fms=kept,
    )
    if (length := announcement_length(message)) > ANNOUNCEMENT_MAX_CHARS:
        return (
            f"L'annonce mise à jour ferait {grouped(length)} caractères toutes places prises, Discord en accepte 2 000 : "
            f"retire une session ou raccourcis une description. {_UNCHANGED}"
        )
    appended = [Step("added", None, session) for session in added]
    steps = (*sorted(changed, key=_by_start), *appended, *sorted(frozen, key=_by_start))
    return UpdatePlan(message, steps, text_changes, moves={})
