"""The staff updates the published announcement from `/fm brouillon`: the offer and the summary, against a simulated Discord."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
import logging
import sqlite3
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import aiohttp
from conftest import make_interaction, make_role
import discord
import pytest
from test_catalogue_journeys import (
    GUILD_ID,
    NOT_SERVED,
    OTHER_GUILD_ID,
    Lab,
    _buttons,
    _embed_lines,
    _forged,
    _published_via_publier,
    _session,
    _stage,
    _three,
)

from fablabot.cogs import formation_management
from fablabot.helpers.constants import SESSION_EMOJIS, RoleNames
from fablabot.helpers.formation import render_message
from fablabot.helpers.formation.update import as_draft
from fablabot.helpers.guild_access import GuildAccessPolicy
from fablabot.helpers.reaction_log import ReactionLogManager
from fablabot.helpers.utils import RIGHT_LOST_MESSAGE
from fablabot.models import ReactionAction, ReactionEvent
from fablabot.models.formation import FmMessageDraft, PublishedMessage
from fablabot.services import attendance, formations
from fablabot.ui import draft as draft_ui
from fablabot.ui.draft import DRAFT_ITEMS

if TYPE_CHECKING:
    import simcord

NBSP = "\N{NO-BREAK SPACE}"
ADDED = "➕"  # noqa: RUF001 - the sign the copy asks for
REMOVED = "🗑️"
UNCHANGED = "Rien n'a été modifié."
CONFIRM = "✅ Confirmer la mise à jour"
CHANGED = f"L'annonce ou le brouillon a changé depuis ce résumé : presse de nouveau **Publier** pour le revoir. {UNCHANGED}"
NOTHING_TO_UPDATE = f"Le brouillon dit déjà ce que dit l'annonce publiée : rien à mettre à jour. {UNCHANGED}"


@pytest.fixture
async def lab(simcord_env: simcord.Env) -> Lab:
    """Open a server with the staff roles and a member of each."""
    return Lab(simcord_env)


LONG_OLD = ("Ancien " * 14).strip()
LONG_NEW = ("Nouveau " * 11).strip()
"""Names of 97 and 87 characters: the announcement stays under 2 000 characters, the summary outgrows one embed."""


def _warning_text(lab: Lab) -> str:
    """The warning Publier gives when the announcement has sessions to come."""
    return (
        f"⚠️ Une annonce de formations est déjà publiée dans <#{lab.announce.id}>, avec des sessions à venir.\n"
        "**Mettre à jour l'annonce** y reporte le brouillon : les inscriptions restent, "
        "et les membres concernés sont prévenus.\n"
        "**Nouvelle annonce** en publie une autre : les inscriptions de l'ancienne ne compteront plus.\n"
        "Rien n'a encore été publié."
    )


def _change(emoji: str, **values: Any) -> None:
    """Change one session of the stored draft: ``start`` shifts its start by that many hours, the rest are set as given."""
    draft = formations.get_draft(GUILD_ID)
    assert draft is not None
    hours = values.pop("start", None)
    fms = []
    for fm in draft.fms:
        if fm.emoji == emoji:
            moved = {} if hours is None else {"start_iso": (fm.start_dt + timedelta(hours=hours)).isoformat()}
            fm = replace(fm, **moved, **values)
        fms.append(fm)
    formations.save_draft(GUILD_ID, replace(draft, fms=fms))


def _add(lab: Lab, emoji: str, name: str, *, days: float = 5, **values: Any) -> None:
    """Add a session to the stored draft, starting ``days`` from now, given by the member without a role."""
    draft = formations.get_draft(GUILD_ID)
    assert draft is not None
    session = replace(_session(emoji, name, days=days, trainer=lab.plain), **values)
    formations.save_draft(GUILD_ID, replace(draft, fms=[*draft.fms, session]))


def _member(user_id: int) -> dict[str, Any]:
    """A stored sign-up, as the journal of reactions writes it."""
    return {"user_id": user_id, "username": f"member{user_id}", "ts_iso": "2026-11-01T10:00:00+00:00"}


async def _announced(lab: Lab) -> discord.Message:
    """Publish the three sessions, then let members sign up by reacting: two on 🧡, two on 💛 (one seat), one on 💚."""
    _stage(lab, _three(lab))
    await _published_via_publier(lab, lab.announce)
    posted = lab.announce.last_message
    assert posted is not None
    for who, emoji in ((lab.codir, "🧡"), (lab.admin, "🧡"), (lab.temp, "💛"), (lab.president, "💛"), (lab.codir, "💚")):
        await who.react(posted, emoji)
    await lab.env.advance_time(120)
    return posted


async def _publier(lab: Lab) -> Any:
    """Open the brouillon and click Publier; return the answer."""
    listing = await lab.brouillon(lab.respo)
    return await lab.respo.click(listing.response, custom_id="fmd:publish")


async def _warned(lab: Lab) -> Any:
    """Move the start of 🧡 by a day, then click Publier; return the warning."""
    _change("🧡", start=24)
    return await _publier(lab)


@dataclass(frozen=True)
class Snapshot:
    """Everything an update could touch: the announcement, the draft, the posted message and the DMs sent."""

    published: PublishedMessage | None
    draft: FmMessageDraft | None
    content: str
    reactions: list[tuple[str, list[int]]]
    dms: list[list[str]]


def _snapshot(lab: Lab, posted: discord.Message | None) -> Snapshot:
    """Read the stored announcement and draft, the posted message (None once deleted) and the DMs."""
    sent = lab.env.backend.get_message(lab.announce.id, posted.id) if posted else None
    members = (lab.respo, lab.admin, lab.codir, lab.temp, lab.president, lab.plain)
    return Snapshot(
        formations.current_published(GUILD_ID),
        formations.get_draft(GUILD_ID),
        sent.content if sent else "",
        [(r.emoji, list(r.user_ids)) for r in sent.reactions] if sent else [],
        [[m.content for m in who.user.dm_channel.history()] if who.user.dm_channel else [] for who in members],
    )


async def test_publier_offers_to_update_an_announcement_with_sessions_to_come(lab: Lab) -> None:
    """With sessions still to come and a changed draft, Publier offers to update, to post anew or to give up."""
    await _announced(lab)

    warning = await _warned(lab)

    assert warning.response.ephemeral
    assert warning.response.content == _warning_text(lab)
    assert not warning.response.embeds
    buttons = _buttons(warning.response)
    assert list(buttons) == ["🔄 Mettre à jour l'annonce", "📣 Nouvelle annonce", "Annuler"]
    assert buttons["🔄 Mettre à jour l'annonce"]["style"] == discord.ButtonStyle.primary.value
    assert buttons["📣 Nouvelle annonce"]["style"] == discord.ButtonStyle.danger.value
    assert buttons["Annuler"]["style"] == discord.ButtonStyle.secondary.value
    assert len(lab.announce.history()) == 1


async def test_nouvelle_annonce_goes_on_to_the_channel_question(lab: Lab) -> None:
    """Nouvelle annonce asks where to publish, the channel of the last announcement already chosen."""
    await _announced(lab)
    warning = await _warned(lab)

    asked = await lab.respo.click(warning.response, custom_id="fmd:pub-ok")

    assert asked.response.content == "**Où publier ?** Choisis le salon, puis **Publier**."
    (menu,) = (c for row in asked.response.components for c in row["components"] if c["type"] == 8)
    assert menu["default_values"] == [{"id": lab.announce.id, "type": "channel"}]


async def test_mettre_a_jour_shows_what_would_change_and_changes_nothing(lab: Lab) -> None:
    """The summary lists each change with who is told and the length with every seat taken; nothing is stored or sent."""
    posted = await _announced(lab)
    warning = await _warned(lab)
    _change("💚", seats=1)
    _change("💛", description="Apportez vos lunettes.", seats=3)
    before = _snapshot(lab, posted)
    stored = formations.get_draft(GUILD_ID)
    published = formations.current_published(GUILD_ID)
    assert stored is not None
    assert published is not None
    old = {fm.emoji: fm for fm in published.message.fms}

    shown = await lab.respo.click(warning.response, custom_id="fmd:update")

    assert shown.response.ephemeral
    embeds = shown.response.embeds
    assert len(embeds) == 1
    assert embeds[0].title == "🔄 Mettre à jour l'annonce · 3 changements"
    assert embeds[0].colour is not None
    new_start = int(old["🧡"].start_dt.timestamp()) + 24 * 3600
    full = replace(stored, fms=[replace(fm, registered_users=[{"user_id": n} for n in range(fm.seats)]) for fm in stored.fms])
    length = f"{len(render_message(full)):,}".replace(",", NBSP)
    assert _embed_lines(shown.response) == [
        f"Dans <#{lab.announce.id}>. Les inscriptions et leur ordre d'arrivée restent.",
        "",
        "✏️ 💛 **Brodeuse** : 1 place → 3 places · description modifiée",
        "-# Vers les places : 1 membre en attente",
        "✏️ 💚 **Résine** : 8 places → 1 place",
        f"✏️ 🧡 **Soudure** : <t:{int(old['🧡'].start_dt.timestamp())}:F> → <t:{new_start}:F>",
        "-# À prévenir : 2 inscrits, 0 en attente",
        "",
        f"-# Annonce : {length} / 2 000 caractères, toutes places prises",
    ]
    buttons = _buttons(shown.response)
    assert list(buttons) == ["✅ Confirmer la mise à jour", "Annuler"]
    assert buttons["✅ Confirmer la mise à jour"]["style"] == discord.ButtonStyle.success.value
    assert buttons["✅ Confirmer la mise à jour"]["custom_id"].startswith(f"fmd:upd-ok:{published.message_id}:")
    assert buttons["Annuler"]["style"] == discord.ButtonStyle.secondary.value
    assert _snapshot(lab, posted) == before
    assert len(lab.announce.history()) == 1


async def test_annuler_keeps_everything(lab: Lab) -> None:
    """Annuler on the summary shows the brouillon again with the word that nothing was changed."""
    posted = await _announced(lab)
    warning = await _warned(lab)
    shown = await lab.respo.click(warning.response, custom_id="fmd:update")
    before = _snapshot(lab, posted)

    kept = await lab.respo.click(shown.response, custom_id="fmd:upd-no")

    assert kept.response.content == UNCHANGED
    assert kept.response.embeds[0].title.startswith("📝 Brouillon de la semaine")
    assert _snapshot(lab, posted) == before


@pytest.fixture
def posted_unreacted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stand in for the posted message, which a stubbed click on a stubbed server cannot fetch: there, with no reaction."""
    monkeypatch.setattr(draft_ui, "posted_reacted_emojis", AsyncMock(return_value=frozenset()))


async def _clicked(lab: Lab, custom_id: str, roles: tuple[str, ...]) -> MagicMock:
    """Click an item of the update screens as a member holding these roles, on a stored announcement and a changed draft."""
    _stage(lab, _three(lab))
    published = formations.get_draft(GUILD_ID)
    assert published is not None
    formations.save_published(GUILD_ID, PublishedMessage(message_id=5000, channel_id=lab.announce.id, message=published))
    _change("🧡", start=24)
    return await _forged(lab, custom_id, roles=roles, items=DRAFT_ITEMS)


@pytest.mark.usefixtures("posted_unreacted")
@pytest.mark.parametrize(
    ("role", "may"),
    [
        (RoleNames.TRAININGS_MANAGER, True),
        (RoleNames.ADMIN, True),
        (RoleNames.CODIR, True),
        (RoleNames.ADMIN_TEMP, True),
        (RoleNames.PRESIDENT, False),
        (None, False),
    ],
    ids=["respo-formation", "administrateur", "codir", "admin-temp", "president", "plain-member"],
)
async def test_who_may_update(lab: Lab, role: str | None, may: bool) -> None:
    """Respo Formation, Administrateur, CoDir and Admin -temp- get the summary; Président and a plain member are refused."""
    interaction = await _clicked(lab, "fmd:update", (role,) if role else ())

    if may:
        interaction.response.edit_message.assert_awaited_once()
        assert interaction.response.edit_message.await_args.kwargs["embeds"][0].title.startswith("🔄 Mettre à jour l'annonce")
    else:
        interaction.response.send_message.assert_awaited_once()
        assert interaction.response.send_message.await_args.args[0] == RIGHT_LOST_MESSAGE
        interaction.response.edit_message.assert_not_awaited()


async def test_a_member_who_lost_the_role_is_refused_at_the_click_and_nothing_changes(lab: Lab) -> None:
    """Every item of the update screens re-reads the role: a member who lost it is refused and nothing moves."""
    posted = await _announced(lab)
    warning = await _warned(lab)
    shown = await lab.respo.click(warning.response, custom_id="fmd:update")
    warning = await _publier(lab)
    before = _snapshot(lab, posted)
    lab.lose_roles(lab.respo)
    await asyncio.sleep(0)

    results = [
        await lab.respo.click(shown.response, custom_id=_buttons(shown.response)[CONFIRM]["custom_id"]),
        await lab.respo.click(shown.response, custom_id="fmd:upd-no"),
        await lab.respo.click(warning.response, custom_id="fmd:update"),
        await lab.respo.click(warning.response, custom_id="fmd:pub-ok"),
        await lab.respo.click(warning.response, custom_id="fmd:pub-no"),
    ]

    for result in results:
        assert result.ephemeral
        assert result.response.content == RIGHT_LOST_MESSAGE
        assert not result.response.embeds
    assert _snapshot(lab, posted) == before


async def test_a_server_no_longer_served_refuses_the_click(lab: Lab) -> None:
    """When the server leaves the bot's list, the update items are refused and nothing moves."""
    posted = await _announced(lab)
    warning = await _warned(lab)
    shown = await lab.respo.click(warning.response, custom_id="fmd:update")
    warning = await _publier(lab)
    before = _snapshot(lab, posted)
    bot: Any = lab.env.bot
    bot.guild_access = GuildAccessPolicy(allowed=frozenset({OTHER_GUILD_ID}))

    results = [
        await lab.respo.click(shown.response, custom_id=_buttons(shown.response)[CONFIRM]["custom_id"]),
        await lab.respo.click(shown.response, custom_id="fmd:upd-no"),
        await lab.respo.click(warning.response, custom_id="fmd:update"),
        await lab.respo.click(warning.response, custom_id="fmd:pub-ok"),
    ]

    for result in results:
        assert result.ephemeral
        assert result.response.content == NOT_SERVED
    assert _snapshot(lab, posted) == before


@pytest.mark.usefixtures("posted_unreacted")
async def test_the_update_buttons_work_after_a_restart(started_bot: Any) -> None:
    """A second bot on the same database answers the update buttons of a message the first bot sent."""
    draft = FmMessageDraft(header="h", role_id=77, intro="Salut", fms=[_session("🧡", "Soudure", days=2)], end="Fin")
    formations.save_published(GUILD_ID, PublishedMessage(message_id=5000, channel_id=9, message=draft))
    formations.save_draft(GUILD_ID, replace(draft, fms=[replace(draft.fms[0], seats=9)]))
    store = started_bot._connection._view_store

    async def click(custom_id: str) -> MagicMock:
        interaction = make_interaction(user_id=7, guild_id=GUILD_ID)
        interaction.client = started_bot
        interaction.user.roles = [make_role(RoleNames.CODIR)]
        interaction.message = MagicMock()
        interaction.guild.get_role.return_value = make_role("Formations", role_id=77)
        pattern, item_class = next((p, c) for p, c in store._dynamic_items.items() if p.fullmatch(custom_id))
        item = await item_class.from_custom_id(interaction, MagicMock(), pattern.fullmatch(custom_id))
        assert await item.interaction_check(interaction)
        await item.callback(interaction)
        return interaction

    assert set(DRAFT_ITEMS) <= set(store._dynamic_items.values())
    summary = await click("fmd:update")
    kept = await click("fmd:upd-no")

    (embed,) = summary.response.edit_message.await_args.kwargs["embeds"]
    assert embed.title == "🔄 Mettre à jour l'annonce · 1 changement"
    assert (embed.description or "").splitlines()[:3] == [
        "Dans <#9>. Les inscriptions et leur ordre d'arrivée restent.",
        "",
        "✏️ 🧡 **Soudure** : 5 places → 9 places",
    ]
    assert kept.response.edit_message.await_args.kwargs["content"] == UNCHANGED


async def test_a_long_summary_spans_several_embeds_without_cutting_a_line(lab: Lab) -> None:
    """A summary past one embed's room goes on in a second embed of the same message, every line whole."""
    old = [
        _session(emoji, f"{n:02d} {LONG_OLD}", days=2 + n / 10, trainer=lab.plain) for n, emoji in enumerate(SESSION_EMOJIS[:7])
    ]
    signed = [
        replace(fm, registered_users=[_member(100 + k) for k in range(fm.seats)], waitlisted_users=[_member(200)]) for fm in old
    ]
    draft = _stage(lab, old, intro="a", end="b")
    posted = await lab.respo.send(lab.announce, "L'annonce")
    formations.save_published(
        GUILD_ID, PublishedMessage(message_id=posted.id, channel_id=lab.announce.id, message=replace(draft, fms=signed))
    )
    new = [
        replace(
            fm,
            name=f"{n:02d} {LONG_NEW}",
            start_iso=(fm.start_dt + timedelta(hours=1)).isoformat(),
            trainer_mention=f"<@{lab.admin.id}>",
            duration="2 h",
            seats=7,
            description="ok",
            excusable=False,
        )
        for n, fm in enumerate(old)
    ]
    formations.save_draft(GUILD_ID, replace(draft, fms=new, intro="b"))
    warning = await lab.respo.click((await lab.brouillon(lab.respo)).response, custom_id="fmd:publish")

    shown = await lab.respo.click(warning.response, custom_id="fmd:update")

    embeds = shown.response.embeds
    assert len(embeds) == 2
    assert embeds[0].title == "🔄 Mettre à jour l'annonce · 8 changements"
    assert embeds[1].title is None
    assert embeds[0].colour == embeds[1].colour
    assert all(len(e.description or "") <= 4096 for e in embeds)
    lines = [line for e in embeds for line in (e.description or "").splitlines()]
    changed = [line for line in lines if line.startswith("✏️")]
    assert len(changed) == 7
    assert all(
        line.startswith(f"✏️ {fm.emoji} **{fm.name}** : « {was.name} » → « {fm.name} » · <t:")
        and line.endswith("5 places → 7 places · description modifiée · excusable → non excusable")
        for line, fm, was in zip(changed, new, old, strict=True)
    )
    assert "💬 Texte de l'annonce : intro" in lines
    assert lines[0].startswith("Dans <#")
    assert lines[-1].startswith("-# Annonce : ")
    assert not (embeds[1].description or "").startswith("\n")


class Ticks:
    """The clock the cog stamps reactions with, which a test moves on so that members react seconds apart."""

    shift = timedelta()


@pytest.fixture
def ticks(monkeypatch: pytest.MonkeyPatch) -> type[Ticks]:
    """Make the cog's clock tell the real time plus ``Ticks.shift``, which starts at zero."""
    Ticks.shift = timedelta()

    class Shifted(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> Shifted:
            return super().now(tz) + Ticks.shift

    monkeypatch.setattr(formation_management, "datetime", Shifted)
    return Ticks


def _cog(lab: Lab) -> Any:
    """The formation cog of the running bot."""
    bot: Any = lab.env.bot
    return bot.get_cog("FormationManagement")


def _stored() -> PublishedMessage:
    """The server's current announcement."""
    published = formations.current_published(GUILD_ID)
    assert published is not None
    return published


def _session_of(emoji: str) -> Any:
    """One session of the stored announcement."""
    return next(fm for fm in _stored().message.fms if fm.emoji == emoji)


def _flag(emoji: str, **flags: bool) -> None:
    """Set flags on one session of the stored announcement, as a reminder tick does."""
    published = _stored()
    fms = [replace(fm, **flags) if fm.emoji == emoji else fm for fm in published.message.fms]
    formations.save_published(GUILD_ID, replace(published, message=replace(published.message, fms=fms)))


async def _summary(lab: Lab) -> Any:
    """Click Publier, then Mettre à jour l'annonce; return the summary."""
    warning = await _publier(lab)
    return await lab.respo.click(warning.response, custom_id="fmd:update")


async def _confirm(lab: Lab, summary: Any) -> Any:
    """Click Confirmer la mise à jour on a summary; return the answer."""
    return await lab.respo.click(summary.response, custom_id=_buttons(summary.response)[CONFIRM]["custom_id"])


async def _updated(lab: Lab) -> Any:
    """Open the summary of the changed draft and confirm it; return the answer."""
    return await _confirm(lab, await _summary(lab))


def _done(lab: Lab, parts: str) -> str:
    """The answer of a confirmed update with these parts."""
    return (
        f"✅ **Annonce mise à jour** dans <#{lab.announce.id}> : {parts}.\n"
        "-# Le brouillon reprend l'annonce telle qu'elle est maintenant."
    )


def _dms(who: Any) -> list[str]:
    """The private messages a member received, oldest first."""
    return [m.content for m in who.user.dm_channel.history()] if who.user.dm_channel else []


def _reminders(who: Any, name: str) -> list[str]:
    """The hour-before reminders of a training a member received."""
    return [text for text in _dms(who) if f"**{name}** commence bientôt" in text]


async def _signed_up(lab: Lab, ticks: type[Ticks], members: list[Any], *, seats: int = 5) -> discord.Message:
    """Publish the three sessions with ``seats`` places on 🧡, then let the members react to 🧡 in this order, seconds apart."""
    sessions = _three(lab)
    sessions[0] = replace(sessions[0], seats=seats)
    _stage(lab, sessions)
    await _published_via_publier(lab, lab.announce)
    posted = lab.announce.last_message
    assert posted is not None
    for who in members:
        await who.react(posted, "🧡")
        ticks.shift += timedelta(seconds=10)
    await lab.env.advance_time(120)
    return posted


def _lists(emoji: str = "🧡") -> tuple[list[int], list[int]]:
    """The members registered and waiting on a session of the stored announcement, by ID, in order."""
    fm = _session_of(emoji)
    return [u["user_id"] for u in fm.registered_users], [u["user_id"] for u in fm.waitlisted_users]


async def test_confirmer_changes_the_announcement_in_place_and_keeps_its_reactions(lab: Lab) -> None:
    """Confirmer edits the posted message: same message and channel, the new start in its text, every reaction still there."""
    posted = await _announced(lab)
    before = _stored()
    old = {fm.emoji: fm for fm in before.message.fms}
    reactions = _snapshot(lab, posted).reactions
    _change("🧡", start=24)

    done = await _updated(lab)

    assert done.response.content == _done(lab, "1 session modifiée")
    assert not done.response.embeds
    assert list(_buttons(done.response)) == ["📝 Voir le brouillon"]
    assert len(lab.announce.history()) == 1
    assert (last := lab.announce.last_message) is not None
    assert last.id == posted.id
    stored = _stored()
    assert (stored.message_id, stored.channel_id) == (before.message_id, before.channel_id)
    sent = lab.env.backend.get_message(lab.announce.id, posted.id)
    new_start = int(old["🧡"].start_dt.timestamp()) + 24 * 3600
    assert f"<t:{new_start}:F>" in sent.content
    assert f"<t:{int(old['🧡'].start_dt.timestamp())}:F>" not in sent.content
    assert sent.content == render_message(stored.message)
    assert [(r.emoji, list(r.user_ids)) for r in sent.reactions] == reactions


async def test_a_refused_edit_of_the_posted_message_is_said_and_retried(lab: Lab, monkeypatch: pytest.MonkeyPatch) -> None:
    """When Discord refuses the edit after the update is stored, the answer says the post follows and the guild is retried."""
    posted = await _announced(lab)
    _change("🧡", start=24)
    summary = await _summary(lab)
    with monkeypatch.context() as refusing:
        refusing.setattr(
            discord.Message, "edit", AsyncMock(side_effect=discord.HTTPException(MagicMock(status=500, reason="boom"), "boom"))
        )
        done = await _confirm(lab, summary)

    sent = lab.env.backend.get_message(lab.announce.id, posted.id)
    assert sent.content != render_message(_stored().message)
    assert done.response.content == _done(lab, "1 session modifiée").replace(
        "\n-# Le brouillon",
        "\n⚠️ Discord n'a pas accepté la modification du message : elle sera refaite d'ici 5 minutes.\n-# Le brouillon",
    )
    assert GUILD_ID in _cog(lab)._retry_guilds  # pyright: ignore[reportPrivateUsage]

    await _cog(lab)._update_published_message(GUILD_ID)  # pyright: ignore[reportPrivateUsage]

    assert lab.env.backend.get_message(lab.announce.id, posted.id).content == render_message(_stored().message)


async def test_sign_ups_keep_their_place_and_order_through_an_update(lab: Lab, ticks: type[Ticks]) -> None:
    """Three members react in turn, the last to arrive having the smallest ID: a description change keeps that order."""
    await _signed_up(lab, ticks, [lab.temp, lab.codir, lab.admin])
    arrival = [lab.temp.id, lab.codir.id, lab.admin.id]
    assert _lists() == (arrival, [])
    stamps = [u["ts_iso"] for u in _session_of("🧡").registered_users]
    received = [_dms(who) for who in (lab.temp, lab.codir, lab.admin)]
    _change("🧡", description="Apportez vos lunettes.")

    done = await _updated(lab)

    assert done.response.content == _done(lab, "1 session modifiée")
    assert _lists() == (arrival, [])
    assert [_dms(who) for who in (lab.temp, lab.codir, lab.admin)] == received
    assert [u["ts_iso"] for u in _session_of("🧡").registered_users] == stamps
    assert _session_of("🧡").description == "Apportez vos lunettes."


async def test_fewer_seats_move_the_last_arrived_to_the_waitlist_and_more_seats_bring_them_back(
    lab: Lab, ticks: type[Ticks]
) -> None:
    """Going from 3 to 2 places puts the last member to arrive on the waitlist; back to 3, they are seated again."""
    posted = await _signed_up(lab, ticks, [lab.temp, lab.codir, lab.admin], seats=3)
    arrival = [lab.temp.id, lab.codir.id, lab.admin.id]
    assert _lists() == (arrival, [])
    waiting = len(_dms(lab.admin))

    _change("🧡", seats=2)
    await _updated(lab)
    await lab.env.advance_time(120)

    assert _lists() == (arrival[:2], arrival[2:])
    assert lab.env.backend.get_message(lab.announce.id, posted.id).content == render_message(_stored().message)
    assert "liste d'attente" in _dms(lab.admin)[-1]
    assert len(_dms(lab.admin)) == waiting + 1

    _change("🧡", seats=3)
    await _updated(lab)
    await lab.env.advance_time(120)

    assert _lists() == (arrival, [])
    assert "une place s'est libérée" in _dms(lab.admin)[-1]
    assert len(_dms(lab.admin)) == waiting + 2


@pytest.mark.parametrize("minutes_ahead", [60, 30], ids=["an-hour-ahead", "already-inside-the-hour"])
async def test_a_new_start_gets_its_hour_before_reminder_once(lab: Lab, minutes_ahead: int) -> None:
    """A moved session gets its reminder once, at the next tick even when the new start is already under an hour away."""
    await _announced(lab)
    _flag("🧡", notified_hour_before=True)
    _change("🧡", start=-60)

    await _updated(lab)

    moved = _session_of("🧡")
    assert not moved.notified_hour_before
    tick = moved.start_dt - timedelta(minutes=minutes_ahead)
    await _cog(lab)._send_due_reminders(GUILD_ID, tick)  # pyright: ignore[reportPrivateUsage]
    await _cog(lab)._send_due_reminders(GUILD_ID, tick)  # pyright: ignore[reportPrivateUsage]

    assert len(_reminders(lab.codir, "Soudure")) == 1
    assert len(_reminders(lab.admin, "Soudure")) == 1


async def test_a_new_trainer_after_the_reminder_is_the_only_one_reminded_again(lab: Lab) -> None:
    """A trainer changed once the reminder went out gets it alone: the participants and the Respo do not get it twice."""
    await _announced(lab)
    cog = _cog(lab)
    await cog._send_due_reminders(GUILD_ID, _session_of("🧡").start_dt - timedelta(minutes=60))  # pyright: ignore[reportPrivateUsage]
    told = {who: len(_reminders(who, "Soudure")) for who in (lab.plain, lab.codir, lab.admin, lab.respo, lab.president)}
    assert list(told.values())[:3] == [1, 1, 1]
    assert told[lab.respo] >= 1
    assert told[lab.president] == 0
    _change("🧡", trainer_mention=f"<@{lab.president.id}>")

    await _updated(lab)
    for minutes in (40, 20):
        await cog._send_due_reminders(GUILD_ID, _session_of("🧡").start_dt - timedelta(minutes=minutes))  # pyright: ignore[reportPrivateUsage]

    assert _session_of("🧡").notified_hour_before
    assert {who: len(_reminders(who, "Soudure")) for who in told} == {**told, lab.president: 1}


async def test_a_new_trainer_before_the_reminder_is_reminded_with_the_others_at_the_hour(lab: Lab) -> None:
    """A trainer changed before the reminder went out is reminded with everyone at the hour, once, and not before."""
    await _announced(lab)
    _change("🧡", trainer_mention=f"<@{lab.president.id}>")

    await _updated(lab)

    assert _reminders(lab.president, "Soudure") == []
    tick = _session_of("🧡").start_dt - timedelta(minutes=60)
    await _cog(lab)._send_due_reminders(GUILD_ID, tick)  # pyright: ignore[reportPrivateUsage]
    await _cog(lab)._send_due_reminders(GUILD_ID, tick)  # pyright: ignore[reportPrivateUsage]

    assert [len(_reminders(who, "Soudure")) for who in (lab.president, lab.codir, lab.admin, lab.plain)] == [1, 1, 1, 0]


async def test_other_sessions_get_no_reminder_twice(lab: Lab) -> None:
    """A session left alone by the update keeps its flag: its reminder, already sent, does not go out again."""
    await _announced(lab)
    _flag("💛", notified_hour_before=True)
    _change("🧡", description="Apportez vos lunettes.")

    await _updated(lab)

    assert _session_of("💛").notified_hour_before
    tick = _session_of("💛").start_dt - timedelta(hours=1)
    await _cog(lab)._send_due_reminders(GUILD_ID, tick)  # pyright: ignore[reportPrivateUsage]

    assert _reminders(lab.temp, "Brodeuse") == []
    assert _reminders(lab.president, "Brodeuse") == []


async def test_the_draft_says_what_the_announcement_says_after_an_update(lab: Lab) -> None:
    """The draft is written back as the announcement, so Publier then refuses it as already published."""
    await _announced(lab)
    _flag("💛", notified_at_start=True)
    _change("🧡", start=24)
    _change("💚", seats=2)
    _change("💛", seats=9)  # a session left as it is: the draft takes the announcement's values back

    await _updated(lab)

    assert _session_of("💛").seats == 1
    assert formations.get_draft(GUILD_ID) == as_draft(_stored().message)
    refused = await _publier(lab)
    assert refused.response.content == (
        f"Cette annonce est déjà publiée dans <#{lab.announce.id}> : change le brouillon pour en publier une autre. "
        "Rien n'a été publié."
    )


async def test_a_second_confirm_finds_nothing_to_update(lab: Lab) -> None:
    """With two summaries of one change, confirming the first leaves the second nothing to update."""
    posted = await _announced(lab)
    _change("🧡", start=24)
    first = await _summary(lab)
    second = await _summary(lab)
    await _confirm(lab, first)
    before = _snapshot(lab, posted)

    refused = await _confirm(lab, second)

    assert refused.response.content == NOTHING_TO_UPDATE
    assert _snapshot(lab, posted) == before


@pytest.mark.parametrize("since", ["draft", "frozen", "newer"])
async def test_a_change_since_the_summary_is_refused_and_nothing_changes(
    lab: Lab, monkeypatch: pytest.MonkeyPatch, since: str
) -> None:
    """A draft changed, a session frozen or a newer announcement since the summary: the click is refused, nothing moves."""
    posted = await _announced(lab)
    _change("🧡", seats=2)
    _change("💚", seats=1)
    summary = await _summary(lab)
    cog = _cog(lab)
    if since == "draft":
        _change("💛", seats=3)
    elif since == "newer":
        newer = replace(_stored(), message_id=_stored().message_id + 1)
        formations.save_published(GUILD_ID, newer)
    else:
        # A reminder tick takes the lock first and flags the session as started, between the click and its turn.
        real = cog._published_lock  # pyright: ignore[reportPrivateUsage]
        ticked = False

        def lock(guild_id: int) -> Any:
            inner = real(guild_id)

            class TickFirst:
                async def __aenter__(self) -> None:
                    nonlocal ticked
                    if not ticked:
                        ticked = True
                        _flag("🧡", notified_at_start=True)
                    await inner.__aenter__()

                async def __aexit__(self, *error: object) -> None:
                    await inner.__aexit__(*error)

            return TickFirst()

        monkeypatch.setattr(cog, "_published_lock", lock)
    before = _snapshot(lab, posted)

    refused = await _confirm(lab, summary)

    assert refused.response.content == CHANGED
    if since == "frozen":  # the tick's own flag is the one change
        assert before.published is not None
        fms = [replace(fm, notified_at_start=True) if fm.emoji == "🧡" else fm for fm in before.published.message.fms]
        before = replace(before, published=replace(before.published, message=replace(before.published.message, fms=fms)))
    assert _snapshot(lab, posted) == before


@pytest.mark.parametrize("meanwhile", ["draft", "newer"])
async def test_a_change_while_the_message_is_fetched_is_refused_and_not_overwritten(
    lab: Lab, monkeypatch: pytest.MonkeyPatch, meanwhile: str
) -> None:
    """A draft edit or a new announcement landing while the posted message is read survives: the update is refused."""
    posted = await _announced(lab)
    _change("🧡", start=24)
    summary = await _summary(lab)
    real = formation_management.posted_reacted_emojis
    newer = replace(_stored(), message_id=_stored().message_id + 1)

    async def unreadable(*args: Any) -> Any:
        if meanwhile == "draft":
            _change("💚", description="Écrit pendant la lecture.")
        else:
            formations.save_published(GUILD_ID, newer)
        return await real(*args)

    monkeypatch.setattr(formation_management, "posted_reacted_emojis", unreadable)
    before = _snapshot(lab, posted)

    refused = await _confirm(lab, summary)

    assert refused.response.content == CHANGED
    after = _snapshot(lab, posted)
    if meanwhile == "draft":
        assert after.published == before.published
        assert after.draft is not None
        assert next(fm for fm in after.draft.fms if fm.emoji == "💚").description == "Écrit pendant la lecture."
    else:
        assert after.published == newer
        assert after.draft == before.draft
    assert (after.content, after.reactions, after.dms) == (before.content, before.reactions, before.dms)


async def test_a_deleted_announcement_is_refused_and_nothing_changes(lab: Lab) -> None:
    """Confirming once the posted message is gone says so and keeps the stored announcement and the draft."""
    posted = await _announced(lab)
    _change("🧡", start=24)
    summary = await _summary(lab)
    lab.env.backend.delete_message(lab.announce.id, posted.id)
    before = _snapshot(lab, None)

    refused = await _confirm(lab, summary)

    assert refused.response.content == (
        f"L'annonce publiée n'existe plus dans <#{lab.announce.id}> : publie une nouvelle annonce. {UNCHANGED}"
    )
    assert _snapshot(lab, None) == before


async def test_a_message_id_of_another_server_reads_as_changed(lab: Lab) -> None:
    """A confirmation naming another server's announcement is refused as changed, and neither server's data moves."""
    _stage(lab, _three(lab))
    published = formations.get_draft(GUILD_ID)
    assert published is not None
    formations.save_published(GUILD_ID, PublishedMessage(message_id=5000, channel_id=lab.announce.id, message=published))
    foreign = PublishedMessage(message_id=9000, channel_id=77, message=replace(published, intro="Ailleurs"))
    formations.save_published(OTHER_GUILD_ID, foreign)
    formations.save_draft(OTHER_GUILD_ID, as_draft(foreign.message))
    _change("🧡", start=24)
    before = (formations.current_published(GUILD_ID), formations.get_draft(GUILD_ID))
    elsewhere = (formations.current_published(OTHER_GUILD_ID), formations.get_draft(OTHER_GUILD_ID))

    interaction = await _forged(lab, f"fmd:upd-ok:9000:{'0' * 12}", items=DRAFT_ITEMS)

    interaction.response.send_message.assert_awaited_once()
    assert interaction.response.send_message.await_args.args[0] == CHANGED
    assert interaction.response.send_message.await_args.kwargs["ephemeral"]
    interaction.response.edit_message.assert_not_awaited()
    assert (formations.current_published(GUILD_ID), formations.get_draft(GUILD_ID)) == before
    assert (formations.current_published(OTHER_GUILD_ID), formations.get_draft(OTHER_GUILD_ID)) == elsewhere


async def test_the_confirm_button_works_after_a_restart(started_bot: Any) -> None:
    """A second bot on the same database answers the Confirmer button of a summary the first bot sent."""
    draft = FmMessageDraft(header="h", role_id=77, intro="Salut", fms=[_session("🧡", "Soudure", days=2)], end="Fin")
    formations.save_published(GUILD_ID, PublishedMessage(message_id=5000, channel_id=9, message=draft))
    formations.save_draft(GUILD_ID, replace(draft, fms=[replace(draft.fms[0], seats=9)]))
    store = started_bot._connection._view_store
    channel = MagicMock(spec=discord.TextChannel)
    channel.fetch_message = AsyncMock(return_value=MagicMock())

    async def click(custom_id: str) -> MagicMock:
        interaction = make_interaction(user_id=7, guild_id=GUILD_ID)
        interaction.client = started_bot
        interaction.user.roles = [make_role(RoleNames.CODIR)]
        interaction.message = MagicMock()
        interaction.guild.get_role.return_value = make_role("Formations", role_id=77)
        interaction.guild.get_channel.return_value = channel
        pattern, item_class = next((p, c) for p, c in store._dynamic_items.items() if p.fullmatch(custom_id))
        item = await item_class.from_custom_id(interaction, MagicMock(), pattern.fullmatch(custom_id))
        assert await item.interaction_check(interaction)
        await item.callback(interaction)
        return interaction

    summary = await click("fmd:update")
    view = summary.response.edit_message.await_args.kwargs["view"]
    confirm = next(c.custom_id for c in view.children if (c.custom_id or "").startswith("fmd:upd-ok:"))
    done = await click(confirm)

    done.response.edit_message.assert_awaited_once()
    done.edit_original_response.assert_awaited_once()
    assert done.edit_original_response.await_args.kwargs["content"].startswith(
        "✅ **Annonce mise à jour** dans <#9> : 1 session modifiée."
    )
    stored = _stored()
    assert stored.message.fms[0].seats == 9
    assert formations.get_draft(GUILD_ID) == as_draft(stored.message)


async def test_a_refused_summary_is_private_logged_by_kind_and_changes_nothing(
    lab: Lab, caplog: pytest.LogCaptureFixture
) -> None:
    """A draft saying what the announcement says gets a private refusal, logged by kind, and nothing is stored or sent."""
    posted = await _announced(lab)
    warning = await _warned(lab)
    formations.save_draft(GUILD_ID, as_draft(_stored().message))
    before = _snapshot(lab, posted)
    caplog.set_level(logging.INFO)

    shown = await lab.respo.click(warning.response, custom_id="fmd:update")

    assert shown.ephemeral
    assert shown.response.content == NOTHING_TO_UPDATE
    assert f"refused kind=draft_not_ready guild_id={GUILD_ID} user_id={lab.respo.id}" in caplog.text
    assert _snapshot(lab, posted) == before


async def test_a_summary_past_the_room_of_a_message_is_refused_and_changes_nothing(
    lab: Lab, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A summary whose embeds together pass what Discord accepts in a message is refused in private, with nothing changed."""
    posted = await _announced(lab)
    warning = await _warned(lab)
    # Today's 2 000-character rule keeps a summary under 6 000 characters: the guard is lowered to reach it.
    monkeypatch.setattr(draft_ui, "_MESSAGE_EMBED_CHARS", 100)
    before = _snapshot(lab, posted)

    shown = await lab.respo.click(warning.response, custom_id="fmd:update")

    assert shown.response.content == (
        "Le résumé des changements dépasse les 6 000 caractères que Discord accepte dans un message : annule une partie des "
        f"changements du brouillon, puis presse de nouveau **Publier**. {UNCHANGED}"
    )
    assert not shown.response.embeds
    assert _snapshot(lab, posted) == before


def _bot_id(lab: Lab) -> int:
    """The ID of the bot's own user."""
    assert lab.env.bot.user is not None
    return lab.env.bot.user.id


def _reacted_by(lab: Lab, posted: discord.Message, emoji: str) -> list[int]:
    """The members who reacted to the posted message with an emoji."""
    sent = lab.env.backend.get_message(lab.announce.id, posted.id)
    return next((list(r.user_ids) for r in sent.reactions if r.emoji == emoji), [])


async def test_an_added_session_appears_with_the_bots_reaction(lab: Lab) -> None:
    """Confirming a draft with one more session edits the message to list it last and the bot reacts with its emoji."""
    posted = await _announced(lab)
    before = _snapshot(lab, posted)
    _add(lab, "💜", "Découpe laser")

    summary = await _summary(lab)
    lines = _embed_lines(summary.response)
    done = await _confirm(lab, summary)

    assert any(line.startswith(f"{ADDED} 💜 **Découpe laser** — <t:") and line.endswith(" · 5 places") for line in lines)
    assert f"-# À prévenir : <@{lab.plain.id}>" in lines
    assert done.response.content == _done(lab, "1 ajoutée")
    stored = _stored()
    assert [fm.emoji for fm in stored.message.fms] == ["🧡", "💛", "💚", "💜"]
    assert (stored.message.fms[3].registered_users, stored.message.fms[3].waitlisted_users) == ([], [])
    sent = lab.env.backend.get_message(lab.announce.id, posted.id)
    assert sent.content == render_message(stored.message)
    assert "Découpe laser" in sent.content
    assert _reacted_by(lab, posted, "💜") == [_bot_id(lab)]
    assert [r for r in _snapshot(lab, posted).reactions if r[0] != "💜"] == before.reactions
    assert len(lab.announce.history()) == 1


async def test_a_member_signs_up_on_an_added_session_from_the_first_reaction(lab: Lab) -> None:
    """A member reacting with the added session's emoji right after the update is registered on it."""
    posted = await _announced(lab)
    _add(lab, "💜", "Découpe laser")
    await _updated(lab)

    await lab.plain.react(posted, "💜")
    await lab.env.advance_time(120)

    assert _lists("💜") == ([lab.plain.id], [])
    assert _lists("🧡")[0] == [lab.codir.id, lab.admin.id]
    assert "1/5" in lab.env.backend.get_message(lab.announce.id, posted.id).content


async def test_an_added_session_never_takes_an_emoji_with_reactions_on_this_announcement(lab: Lab) -> None:
    """An emoji a member reacted with on this message, though no session wears it, is not given to the new session."""
    posted = await _announced(lab)
    ReactionLogManager("formation", lambda now: now - timedelta(days=30), logging.getLogger("test")).append(
        GUILD_ID,
        ReactionEvent(
            message_id=posted.id,
            user_id=lab.plain.id,
            user_name="plain",
            emoji="💜",
            action=ReactionAction.ADD,
            ts_iso=datetime.now(UTC).isoformat(timespec="seconds"),
        ),
    )
    _add(lab, "💜", "Découpe laser")

    summary = await _summary(lab)
    lines = _embed_lines(summary.response)
    await _confirm(lab, summary)

    assert any(line.startswith(f"{ADDED} {SESSION_EMOJIS[3]} **Découpe laser**") for line in lines)
    assert [fm.emoji for fm in _stored().message.fms] == ["🧡", "💛", "💚", SESSION_EMOJIS[3]]
    assert _reacted_by(lab, posted, SESSION_EMOJIS[3]) == [_bot_id(lab)]
    assert _reacted_by(lab, posted, "💜") == []


async def test_the_draft_takes_the_emoji_the_update_gave(lab: Lab) -> None:
    """A new session that had to change emoji is written back in the draft under the emoji the announcement gave it."""
    await _announced(lab)
    _add(lab, "🧡", "Découpe laser")

    await _updated(lab)

    stored = _stored()
    assert stored.message.fms[3].emoji == SESSION_EMOJIS[3]
    assert formations.get_draft(GUILD_ID) == as_draft(stored.message)
    refused = await _publier(lab)
    assert "déjà publiée" in refused.response.content


async def test_adding_keeps_every_check_on_its_training(lab: Lab) -> None:
    """A session added before the others by its start is still placed last, so a check at index 0 stays on its training."""
    posted = await _announced(lab)
    first = _stored()
    _flag("🧡", notified_at_start=True)
    start = _session_of("🧡").start_dt
    attendance.open_appel(
        GUILD_ID, first.message_id, 0, trainer_id=lab.plain.id, starts_at=start, ends_at=start + timedelta(hours=1)
    )
    _add(lab, "💜", "Découpe laser", days=1)

    await _updated(lab)

    stored = _stored()
    assert [fm.name for fm in stored.message.fms] == ["Soudure", "Brodeuse", "Résine", "Découpe laser"]
    assert attendance.is_open(GUILD_ID, stored.message_id, 0)
    assert not attendance.is_open(GUILD_ID, stored.message_id, 3)
    assert stored.message.fms[0].emoji == "🧡"
    assert _reacted_by(lab, posted, "💜") == [_bot_id(lab)]


async def test_a_reaction_the_bot_cannot_add_is_named_in_the_answer(lab: Lab, monkeypatch: pytest.MonkeyPatch) -> None:
    """When Discord refuses the bot's reaction, the update stands and the answer names the emoji to add by hand."""
    posted = await _announced(lab)
    _add(lab, "💜", "Découpe laser")
    summary = await _summary(lab)
    with monkeypatch.context() as refusing:
        refusing.setattr(
            discord.Message,
            "add_reaction",
            AsyncMock(side_effect=discord.HTTPException(MagicMock(status=500, reason="x"), "x")),
        )
        done = await _confirm(lab, summary)

    assert done.response.content == _done(lab, "1 ajoutée").replace(
        "\n-# Le brouillon",
        "\n⚠️ La réaction 💜 n'a pas pu être ajoutée : ajoute-la à la main sur l'annonce.\n-# Le brouillon",
    )
    assert [fm.emoji for fm in _stored().message.fms][-1] == "💜"
    assert _reacted_by(lab, posted, "💜") == []


RETRY = "L'annonce n'a pas pu être mise à jour pour l'instant : réessaie dans un instant. Rien n'a été modifié."


@pytest.mark.parametrize(
    ("failing", "kind"),
    [("storage", "update_storage_failed"), ("discord", "discord_unreadable")],
)
async def test_a_failed_update_is_logged_by_its_real_kind(
    lab: Lab, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, failing: str, kind: str
) -> None:
    """A refused save and a refused read of the posted message each log their own kind, and nothing is changed."""
    posted = await _announced(lab)
    _change("🧡", start=24)
    summary = await _summary(lab)
    if failing == "storage":
        monkeypatch.setattr(formations, "save_update", MagicMock(side_effect=sqlite3.OperationalError("disk")))
    else:
        monkeypatch.setattr(
            discord.TextChannel,
            "fetch_message",
            AsyncMock(side_effect=discord.HTTPException(MagicMock(status=500, reason="x"), "x")),
        )
    before = _snapshot(lab, posted)
    caplog.set_level(logging.INFO)

    refused = await _confirm(lab, summary)

    assert refused.response.content == RETRY
    assert f"refused kind={kind} guild_id={GUILD_ID} user_id={lab.respo.id}" in caplog.text
    assert "kind=draft_not_ready" not in caplog.text
    assert _snapshot(lab, posted) == before


def _rename_stored(name: str, *, hours: float = 0) -> None:
    """Store the announcement with its first session renamed (and moved by ``hours``), as an update stored before its edit."""
    published = _stored()
    first, *others = published.message.fms
    moved = replace(first, name=name, start_iso=(first.start_dt + timedelta(hours=hours)).isoformat())
    formations.save_published(GUILD_ID, replace(published, message=replace(published.message, fms=[moved, *others])))


async def test_a_restart_rebuilds_the_posted_text_of_an_announcement_stored_ahead_of_its_message(lab: Lab) -> None:
    """At start the posted text is rebuilt from what is stored, so an update stored before its edit is not left stale."""
    posted = await _announced(lab)
    _rename_stored("Soudure à l'étain")
    sent = lab.env.backend.get_message(lab.announce.id, posted.id)
    assert sent.content != render_message(_stored().message)

    await _cog(lab)._before_check_upcoming_formations()  # pyright: ignore[reportPrivateUsage]

    sent = lab.env.backend.get_message(lab.announce.id, posted.id)
    assert sent.content == render_message(_stored().message)
    assert "Soudure à l'étain" in sent.content
    assert _lists("🧡")[0] == [lab.codir.id, lab.admin.id]


async def test_a_restart_leaves_an_announcement_with_no_session_to_come_alone(lab: Lab) -> None:
    """An announcement whose sessions have all started is not rebuilt at start: its posted text stays as it is."""
    posted = await _announced(lab)
    _rename_stored("Soudure à l'étain", hours=-24 * 30)
    published = _stored()
    past = [replace(fm, start_iso=(fm.start_dt - timedelta(days=30)).isoformat()) for fm in published.message.fms]
    formations.save_published(GUILD_ID, replace(published, message=replace(published.message, fms=past)))
    before = lab.env.backend.get_message(lab.announce.id, posted.id).content

    await _cog(lab)._before_check_upcoming_formations()  # pyright: ignore[reportPrivateUsage]

    assert lab.env.backend.get_message(lab.announce.id, posted.id).content == before


async def test_a_restart_leaves_the_announcement_of_a_server_outside_the_allow_list_alone(lab: Lab) -> None:
    """At start the posted text of a server this instance does not serve is not touched, though it is stale."""
    posted = await _announced(lab)
    _rename_stored("Soudure à l'étain")
    before = _snapshot(lab, posted)
    stale = lab.env.backend.get_message(lab.announce.id, posted.id).content
    assert stale != render_message(_stored().message)
    bot: Any = lab.env.bot
    bot.guild_access = GuildAccessPolicy(allowed=frozenset({OTHER_GUILD_ID}))

    await _cog(lab)._before_check_upcoming_formations()  # pyright: ignore[reportPrivateUsage]

    assert lab.env.backend.get_message(lab.announce.id, posted.id).content == stale
    assert _snapshot(lab, posted) == before


async def test_a_failure_choosing_the_servers_to_rebuild_does_not_stop_the_start(
    lab: Lab, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A server filter that fails, whatever the error, is logged and lets the start go on to the reminder loop."""
    await _announced(lab)
    bot: Any = lab.env.bot
    monkeypatch.setattr(bot.guild_access, "is_served", MagicMock(side_effect=RuntimeError("policy")))
    caplog.set_level(logging.INFO)

    await _cog(lab)._before_check_upcoming_formations()  # pyright: ignore[reportPrivateUsage]

    assert "Start-up rebuild of the announcements skipped" in caplog.text
    assert "Formation notification task started" in caplog.text


@pytest.mark.parametrize(("fails", "rebuilt"), [(False, 1), (True, 0)])
async def test_the_start_up_log_counts_only_the_servers_rebuilt(
    lab: Lab, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, fails: bool, rebuilt: int
) -> None:
    """The line that says how many servers were rebuilt at start leaves out a server whose rebuild failed."""
    await _announced(lab)
    cog = _cog(lab)
    if fails:
        monkeypatch.setattr(cog, "_update_published_message", AsyncMock(side_effect=RuntimeError("discord")))
    caplog.set_level(logging.INFO)

    await cog._rebuild_posted_announcements()  # pyright: ignore[reportPrivateUsage]

    assert f"Rebuilt the announcements of {rebuilt} servers at start." in caplog.text


async def test_a_reaction_on_an_emoji_no_session_wears_is_never_a_sign_up_on_a_session_added_later(lab: Lab) -> None:
    """A member's like on the announcement stays out of the new session wearing that emoji, even after a storage failure."""
    posted = await _announced(lab)
    await lab.plain.react(posted, "💜")
    await lab.plain.react(posted, "👍")
    await lab.env.advance_time(120)
    assert formations.reacted_emojis(GUILD_ID, posted.id) >= {"💜"}
    assert "👍" not in formations.reacted_emojis(GUILD_ID, posted.id)
    _add(lab, "💜", "Découpe laser")

    summary = await _summary(lab)
    lines = _embed_lines(summary.response)
    await _confirm(lab, summary)

    assert any(line.startswith(f"{ADDED} {SESSION_EMOJIS[3]} **Découpe laser**") for line in lines)
    assert [fm.emoji for fm in _stored().message.fms] == ["🧡", "💛", "💚", SESSION_EMOJIS[3]]
    cog = _cog(lab)
    cog._stale_guilds.add(GUILD_ID)  # pyright: ignore[reportPrivateUsage]
    await cog._update_published_message(GUILD_ID)  # pyright: ignore[reportPrivateUsage]
    assert _lists(SESSION_EMOJIS[3]) == ([], [])
    assert not [text for text in _dms(lab.plain) if "inscription" in text]


async def test_a_member_who_took_back_a_reaction_on_an_unworn_emoji_still_retires_it(lab: Lab) -> None:
    """An emoji a member reacted with and then removed on this message is not given to a session added later."""
    posted = await _announced(lab)
    await lab.plain.react(posted, "💜")
    await lab.plain.unreact(posted, "💜")
    await lab.env.advance_time(120)
    _add(lab, "💜", "Découpe laser")

    await _updated(lab)

    assert [fm.emoji for fm in _stored().message.fms] == ["🧡", "💛", "💚", SESSION_EMOJIS[3]]


def _deaf_to(lab: Lab, monkeypatch: pytest.MonkeyPatch, emoji: str) -> None:
    """Make the bot drop every reaction on one emoji, as it does while it is down or cut off from the gateway."""
    cog = _cog(lab)
    heard = cog._reaction_counts  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setattr(cog, "_reaction_counts", lambda guild_id, event: event.emoji != emoji and heard(guild_id, event))


async def test_a_reaction_the_bot_never_logged_is_never_a_sign_up_on_a_session_added_later(
    lab: Lab, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A like the bot never heard of, on an emoji no session wears, stays out of the session added later on that emoji."""
    posted = await _announced(lab)
    _deaf_to(lab, monkeypatch, "💜")
    await lab.plain.react(posted, "💜")
    await lab.env.advance_time(120)
    assert "💜" not in formations.reacted_emojis(GUILD_ID, posted.id)
    _add(lab, "💜", "Découpe laser")

    summary = await _summary(lab)
    lines = _embed_lines(summary.response)
    await _confirm(lab, summary)

    assert any(line.startswith(f"{ADDED} {SESSION_EMOJIS[3]} **Découpe laser**") for line in lines)
    assert [fm.emoji for fm in _stored().message.fms] == ["🧡", "💛", "💚", SESSION_EMOJIS[3]]
    cog = _cog(lab)
    cog._stale_guilds.add(GUILD_ID)  # pyright: ignore[reportPrivateUsage]
    await cog._update_published_message(GUILD_ID)  # pyright: ignore[reportPrivateUsage]
    assert _lists(SESSION_EMOJIS[3]) == ([], [])
    assert not [text for text in _dms(lab.plain) if "inscription" in text]


async def test_a_reaction_placed_after_the_summary_makes_the_confirmation_refuse_and_change_nothing(
    lab: Lab, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A like on the draft's emoji that lands once the summary is shown is read at the confirmation: refused as changed."""
    posted = await _announced(lab)
    _add(lab, "💜", "Découpe laser")
    summary = await _summary(lab)
    _deaf_to(lab, monkeypatch, "💜")
    await lab.plain.react(posted, "💜")
    await lab.env.advance_time(120)
    before = _snapshot(lab, posted)

    refused = await _confirm(lab, summary)

    assert refused.response.content == CHANGED
    assert _snapshot(lab, posted) == before


async def test_the_bots_own_reaction_does_not_retire_an_emoji(lab: Lab) -> None:
    """An emoji only the bot reacted with is still free: the session added on it keeps it."""
    posted = await _announced(lab)
    bot: Any = lab.env.bot
    lab.env.backend.set_reaction(lab.announce.id, posted.id, "💜", bot.user.id, True)
    await lab.env.advance_time(120)
    _add(lab, "💜", "Découpe laser")

    await _updated(lab)

    assert [fm.emoji for fm in _stored().message.fms] == ["🧡", "💛", "💚", "💜"]


async def test_the_summary_is_refused_when_the_posted_message_is_gone(lab: Lab, caplog: pytest.LogCaptureFixture) -> None:
    """Asking for the summary once the posted message is gone says so, logs its kind and changes nothing."""
    posted = await _announced(lab)
    _change("🧡", start=24)
    lab.env.backend.delete_message(lab.announce.id, posted.id)
    before = _snapshot(lab, None)
    caplog.set_level(logging.INFO)

    refused = await _summary(lab)

    assert refused.response.content == (
        f"L'annonce publiée n'existe plus dans <#{lab.announce.id}> : publie une nouvelle annonce. {UNCHANGED}"
    )
    assert not refused.response.embeds
    assert f"refused kind=announcement_gone guild_id={GUILD_ID} user_id={lab.respo.id}" in caplog.text
    assert _snapshot(lab, None) == before


async def test_the_summary_is_refused_when_discord_does_not_let_the_bot_read_the_message(
    lab: Lab, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A refused read of the posted message gives the summary no answer to plan from: refused, kind logged, nothing changed."""
    posted = await _announced(lab)
    _change("🧡", start=24)
    monkeypatch.setattr(
        discord.TextChannel,
        "fetch_message",
        AsyncMock(side_effect=discord.HTTPException(MagicMock(status=500, reason="x"), "x")),
    )
    before = _snapshot(lab, posted)
    caplog.set_level(logging.INFO)

    refused = await _summary(lab)

    assert refused.response.content == RETRY
    assert not refused.response.embeds
    assert f"refused kind=discord_unreadable guild_id={GUILD_ID} user_id={lab.respo.id}" in caplog.text
    assert _snapshot(lab, posted) == before


def _remove(emoji: str) -> None:
    """Take a session out of the stored draft."""
    draft = formations.get_draft(GUILD_ID)
    assert draft is not None
    formations.save_draft(GUILD_ID, replace(draft, fms=[fm for fm in draft.fms if fm.emoji != emoji]))


def _refused_clearing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make Discord refuse to clear a reaction from the message, as it does a bot without Manage Messages."""
    refusal = discord.Forbidden(MagicMock(status=403, reason="Missing Permissions"), "Missing Permissions")
    monkeypatch.setattr(discord.Message, "clear_reaction", AsyncMock(side_effect=refusal))


async def test_a_removed_session_leaves_the_text_and_its_reactions(lab: Lab) -> None:
    """Confirming a draft without a session drops it from the text and clears its reactions; the others keep theirs."""
    posted = await _announced(lab)
    before = _snapshot(lab, posted)
    _remove("💛")

    summary = await _summary(lab)
    lines = _embed_lines(summary.response)
    title = summary.response.embeds[0].title
    done = await _confirm(lab, summary)

    assert title == "🔄 Mettre à jour l'annonce · 1 changement"
    assert any(line.startswith(f"{REMOVED} 💛 **Brodeuse** — <t:") and line.endswith(f" · <@{lab.plain.id}>") for line in lines)
    assert "-# À prévenir : 1 inscrit, 1 en attente · réactions 💛 retirées" in lines
    assert done.response.content == _done(lab, "1 retirée")
    assert [fm.emoji for fm in _stored().message.fms] == ["🧡", "💚"]
    sent = lab.env.backend.get_message(lab.announce.id, posted.id)
    assert sent.content == render_message(_stored().message)
    assert "Brodeuse" not in sent.content
    assert _reacted_by(lab, posted, "💛") == []
    assert [r for r in _snapshot(lab, posted).reactions if r[0] != "💛"] == [r for r in before.reactions if r[0] != "💛"]
    assert formations.get_draft(GUILD_ID) == as_draft(_stored().message)
    assert len(lab.announce.history()) == 1


async def test_a_removed_session_with_nobody_signed_up_says_only_its_reactions(lab: Lab) -> None:
    """A removed session nobody signed up on has the short line, with no one to tell."""
    _stage(lab, _three(lab))
    await _published_via_publier(lab, lab.announce)
    _remove("💚")

    summary = await _summary(lab)

    assert "-# Réactions 💚 retirées" in _embed_lines(summary.response)


async def test_without_manage_messages_the_bot_takes_its_own_reaction_and_says_so(
    lab: Lab, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """When Discord refuses to clear a reaction, the bot removes its own, the members' stay, and the answer names the emoji."""
    posted = await _announced(lab)
    _refused_clearing(monkeypatch)
    _remove("💛")
    caplog.set_level(logging.INFO)

    done = await _updated(lab)

    assert done.response.content == (
        f"✅ **Annonce mise à jour** dans <#{lab.announce.id}> : 1 retirée.\n"
        "⚠️ Discord a refusé de retirer les réactions 💛 : le bot a retiré les siennes, "
        "celles des membres restent visibles mais ne comptent plus.\n"
        "-# Le brouillon reprend l'annonce telle qu'elle est maintenant."
    )
    assert _reacted_by(lab, posted, "💛") == [lab.temp.id, lab.president.id]
    assert [fm.emoji for fm in _stored().message.fms] == ["🧡", "💚"]
    assert f"Guild {GUILD_ID} could not clear the reactions" in caplog.text


async def test_a_transport_error_clearing_reactions_still_reminds_the_new_trainer(
    lab: Lab, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dropped connection while clearing a removed session's reactions does not cost the new trainer their reminder."""
    await _announced(lab)
    _flag("🧡", notified_hour_before=True)
    _change("🧡", trainer_mention=f"<@{lab.president.id}>")
    _remove("💛")
    monkeypatch.setattr(discord.Message, "clear_reaction", AsyncMock(side_effect=aiohttp.ServerDisconnectedError()))

    done = await _updated(lab)

    assert [fm.emoji for fm in _stored().message.fms] == ["🧡", "💚"]
    assert len(_reminders(lab.president, "Soudure")) == 1
    assert done.response.content.startswith(
        f"✅ **Annonce mise à jour** dans <#{lab.announce.id}> : 1 session modifiée, 1 retirée."
    )
    assert "réactions 💛" in done.response.content


async def test_a_transport_error_adding_a_reaction_still_reminds_the_new_trainer(
    lab: Lab, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dropped connection while adding a session's reaction does not cost the new trainer their reminder."""
    await _announced(lab)
    _flag("🧡", notified_hour_before=True)
    _change("🧡", trainer_mention=f"<@{lab.president.id}>")
    _add(lab, "💜", "Découpe laser")
    monkeypatch.setattr(discord.Message, "add_reaction", AsyncMock(side_effect=aiohttp.ServerDisconnectedError()))

    done = await _updated(lab)

    assert [fm.emoji for fm in _stored().message.fms][-1] == "💜"
    assert len(_reminders(lab.president, "Soudure")) == 1
    assert "La réaction 💜 n'a pas pu être ajoutée" in done.response.content


async def test_a_removed_sessions_reactor_reacting_again_counts_for_nothing(lab: Lab) -> None:
    """A member who reacts again on the emoji of a removed session signs up nowhere and is told nothing."""
    posted = await _announced(lab)
    _remove("💛")
    await _updated(lab)
    before = (_lists("🧡"), _lists("💚"), _snapshot(lab, posted).dms)

    await lab.temp.react(posted, "💛")
    await lab.env.advance_time(120)

    assert (_lists("🧡"), _lists("💚"), _snapshot(lab, posted).dms) == before
    assert [fm.emoji for fm in _stored().message.fms] == ["🧡", "💚"]
    sent = lab.env.backend.get_message(lab.announce.id, posted.id)
    assert sent.content == render_message(_stored().message)


async def test_an_emoji_left_with_member_reactions_is_not_given_to_a_session_added_later(
    lab: Lab, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the reactions of a removed session could not be cleared, no session added later takes that emoji."""
    await _announced(lab)
    _refused_clearing(monkeypatch)
    _remove("💛")
    await _updated(lab)
    _add(lab, "💛", "Découpe laser")

    await _updated(lab)

    assert [fm.emoji for fm in _stored().message.fms] == ["🧡", "💚", SESSION_EMOJIS[3]]


async def test_a_started_session_after_a_removed_one_keeps_its_check(lab: Lab) -> None:
    """A check open on the third session still has its registrants and training once the second session is removed."""
    await _announced(lab)
    _flag("💚", notified_at_start=True)
    start = _session_of("💚").start_dt
    appel_id = attendance.open_appel(
        GUILD_ID, _stored().message_id, 2, trainer_id=lab.plain.id, starts_at=start, ends_at=start + timedelta(hours=1)
    )
    registrants = attendance.registrants(appel_id)
    assert [r.user_id for r in registrants] == [lab.codir.id]
    before = attendance.get(appel_id)
    assert before is not None
    assert before.name == "Résine"
    _remove("💛")

    done = await _updated(lab)

    assert done.response.content == _done(lab, "1 retirée")
    assert [fm.emoji for fm in _stored().message.fms] == ["🧡", "💚"]
    assert attendance.registrants(appel_id) == registrants
    checked = attendance.get(appel_id)
    assert checked is not None
    assert (checked.name, checked.position, checked.message_id) == ("Résine", 1, _stored().message_id)
