"""The staff updates the published announcement from `/fm brouillon`: the offer and the summary, against a simulated Discord."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import timedelta
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

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

from fablabot.helpers.constants import SESSION_EMOJIS, RoleNames
from fablabot.helpers.formation import render_message
from fablabot.helpers.guild_access import GuildAccessPolicy
from fablabot.helpers.utils import RIGHT_LOST_MESSAGE
from fablabot.models.formation import FmMessageDraft, PublishedMessage
from fablabot.services import formations
from fablabot.ui.draft import DRAFT_ITEMS

if TYPE_CHECKING:
    import simcord

NBSP = "\N{NO-BREAK SPACE}"
UNCHANGED = "Rien n'a été modifié."


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


def _snapshot(lab: Lab, posted: discord.Message) -> Snapshot:
    """Read the stored announcement and draft, the posted message's text and reactions, and the DMs of every member."""
    sent = lab.env.backend.get_message(lab.announce.id, posted.id)
    members = (lab.respo, lab.admin, lab.codir, lab.temp, lab.president, lab.plain)
    return Snapshot(
        formations.current_published(GUILD_ID),
        formations.get_draft(GUILD_ID),
        sent.content,
        [(r.emoji, list(r.user_ids)) for r in sent.reactions],
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


async def _clicked(lab: Lab, custom_id: str, roles: tuple[str, ...]) -> MagicMock:
    """Click an item of the update screens as a member holding these roles, on a stored announcement and a changed draft."""
    _stage(lab, _three(lab))
    published = formations.get_draft(GUILD_ID)
    assert published is not None
    formations.save_published(GUILD_ID, PublishedMessage(message_id=5000, channel_id=lab.announce.id, message=published))
    _change("🧡", start=24)
    return await _forged(lab, custom_id, roles=roles, items=DRAFT_ITEMS)


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
        await lab.respo.click(shown.response, custom_id="fmd:upd-no"),
        await lab.respo.click(warning.response, custom_id="fmd:update"),
        await lab.respo.click(warning.response, custom_id="fmd:pub-ok"),
    ]

    for result in results:
        assert result.ephemeral
        assert result.response.content == NOT_SERVED
    assert _snapshot(lab, posted) == before


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
    formations.save_published(
        GUILD_ID, PublishedMessage(message_id=5000, channel_id=lab.announce.id, message=replace(draft, fms=signed))
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
