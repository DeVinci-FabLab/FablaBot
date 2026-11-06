"""Respo Formation and Administrateur manage the training templates with `/fm modeles`, against a simulated Discord."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import date, datetime, time, timedelta
import re
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

from conftest import LISTED_GUILD, OTHER_LISTED_GUILD, make_interaction, make_role
import discord
import pytest

from fablabot.helpers.constants import PARIS_TZ, SESSION_EMOJIS, ErrorMessages, RoleNames
from fablabot.helpers.formation import announcement_header, render_message
from fablabot.helpers.guild_access import GuildAccessPolicy
from fablabot.helpers.utils import RIGHT_LOST_MESSAGE
from fablabot.models import TemplateFields
from fablabot.models.formation import FmMessageDraft, Formation, PublishedMessage
from fablabot.services import catalogue, formations
from fablabot.ui.catalogue import CATALOGUE_ITEMS, deletion_message, templates_message
from fablabot.ui.draft import DRAFT_ITEMS, publish_refusal
from fablabot.ui.session import PICKS_PER_PAGE, SESSION_ITEMS

if TYPE_CHECKING:
    import simcord

GUILD_ID = int(LISTED_GUILD)
OTHER_GUILD_ID = int(OTHER_LISTED_GUILD)
GONE = "Ce modèle n'existe plus."
NOT_SERVED = "Ce bot n'est pas disponible ici. Rien n'a été modifié."
EMPTY = "Aucun modèle pour l'instant. **Nouveau modèle** en crée un."
TITLE = "📚 Modèles de formation"
STAFF_ROLES = (
    RoleNames.TRAININGS_MANAGER,
    RoleNames.ADMIN,
    RoleNames.CODIR,
    RoleNames.ADMIN_TEMP,
    RoleNames.PRESIDENT,
)


class Lab:
    """A server with the staff roles, one member of each, and a member with no role."""

    def __init__(self, env: simcord.Env) -> None:
        """Create the server, its commands channel, its roles and its members."""
        self.env = env
        self.guild = env.create_guild(id=GUILD_ID)
        self.commands = self.guild.create_text_channel("commandes_bot")
        self.roles = {name: self.guild.create_role(name) for name in STAFF_ROLES}
        self.respo = self.member("respo", RoleNames.TRAININGS_MANAGER)
        self.admin = self.member("admin", RoleNames.ADMIN)
        self.codir = self.member("codir", RoleNames.CODIR)
        self.temp = self.member("temp", RoleNames.ADMIN_TEMP)
        self.president = self.member("president", RoleNames.PRESIDENT)
        self.plain = self.member("plain")
        self.notified = self.guild.create_role("Formations")
        self.announce = self.guild.create_text_channel("annonces")
        self.elsewhere = self.guild.create_text_channel("autre-salon")

    def member(self, name: str, *roles: str) -> Any:
        """Add a member holding these roles."""
        return self.guild.add_member(self.env.create_user(name), roles=[self.roles[r] for r in roles])

    async def show(self) -> None:
        """Let every member see `/fm`, so that the refusals come from the command's own check."""
        bot: Any = self.env.bot
        command = next(c for c in await bot.tree.fetch_commands() if c.name == "fm")
        self.guild.set_command_permissions(command, {self.guild.default_role: True})

    async def modeles(self, who: Any) -> Any:
        """Run `/fm modeles` as a member, from the commands channel."""
        await self.show()
        return await who.slash(self.commands, "fm modeles")

    async def brouillon(self, who: Any) -> Any:
        """Run `/fm brouillon` as a member, from the commands channel."""
        await self.show()
        return await who.slash(self.commands, "fm brouillon")

    def lose_roles(self, who: Any) -> None:
        """Take every role of a member, as a server administrator does while a message stays open."""
        self.env.backend.edit_member(GUILD_ID, who.id, {"role_ids": []})


@pytest.fixture
async def lab(simcord_env: simcord.Env) -> Lab:
    """Open a server with the staff roles and a member of each."""
    return Lab(simcord_env)


def _template(name: str = "Soudure", *, guild_id: int = GUILD_ID, seats: int = 4, excusable: bool = False) -> Any:
    """Store a template of a server and return it."""
    return catalogue.create_template(guild_id, TemplateFields(name, "1 h", seats, "Apportez vos lunettes.", excusable))


def _stored(guild_id: int = GUILD_ID) -> list[Any]:
    """Read the templates of a server back."""
    return catalogue.list_templates(guild_id)


def _buttons(message: Any) -> dict[str, dict[str, Any]]:
    """Index the buttons of a message by their label."""
    return {c["label"]: c for row in message.components for c in row["components"] if c["type"] == 2}


def _ids(message: Any) -> list[str]:
    """List the custom IDs of a message's buttons and menus, in order."""
    return [c["custom_id"] for row in message.components for c in row["components"]]


def _listed(message: Any) -> list[str]:
    """List the bold template names of a list message, in order."""
    lines = (message.embeds[0].description or "").splitlines()
    return [line.split("**")[1] for line in lines if line.startswith("**")]


def _form(shown: Any, by_label: dict[str, Any]) -> dict[str, str | list[str]]:
    """Fill a modal as a member would: what ``by_label`` says by visible label, the rest as the form opened with it."""
    controls = []
    for item in shown.modal["components"]:
        if item["type"] == 18:
            controls.append((item["label"], item["component"]))
        else:
            controls.extend((child["label"], child) for child in item["components"])
    filled: dict[str, str | list[str]] = {}
    for label, control in controls:
        if control["type"] == 3:
            preset = [o["value"] for o in control["options"] if o.get("default")]
            filled[control["custom_id"]] = [by_label[label]] if label in by_label else preset
        elif label in by_label or control.get("value"):
            filled[control["custom_id"]] = by_label.get(label) or control["value"]
    return filled


def _values(shown: Any) -> dict[str, str | None]:
    """Read the value each text input of a modal opens with, by label."""
    values: dict[str, str | None] = {}
    for item in shown.modal["components"]:
        for child in item.get("components", []):
            values[child["label"]] = child.get("value")
    return values


FORM = {"Nom": "Brodeuse", "Durée": "1 h", "Places": "3", "Description": "Tissu fourni.", "Excusable": "Non"}


async def _new_template(who: Any, listing: Any, given: dict[str, str]) -> Any:
    """Click Nouveau modèle on a list and submit the form with ``given`` over the usual values."""
    shown = await who.click(listing.response, custom_id="cat:new")
    return await who.submit_modal(shown, _form(shown, {**FORM, **given}))


async def _picked(who: Any, listing: Any, action: str, template: Any) -> Any:
    """Click Modifier or Supprimer on a list and choose a template in the menu that follows."""
    chosen = await who.click(listing.response, custom_id=f"cat:{action}")
    return await who.select(chosen.response, [str(template.id)], custom_id=f"cat:pick:{action}:0")


def _confirm_id(template: Any) -> str:
    """Give the custom ID of the button that confirms the deletion of a template as it is stored now."""
    _, view = deletion_message(template)
    return view.children[0].custom_id  # pyright: ignore[reportAttributeAccessIssue,reportReturnType]


def _session_of(template: Any) -> Formation:
    """A session of the draft, copied from a template as a session form would."""
    return Formation(
        emoji="🧡",
        name=template.fields.name,
        trainer_mention="<@1>",
        start_iso=(datetime.now(PARIS_TZ) + timedelta(days=3)).isoformat(),
        duration=template.fields.duration,
        seats=template.fields.seats,
        excusable=template.fields.excusable,
    )


def _store_sessions(template: Any) -> tuple[FmMessageDraft, FmMessageDraft]:
    """Store a draft and a published announcement that each hold a session copied from the template."""
    draft = FmMessageDraft(header="h", role_id=1, intro="i", fms=[_session_of(template)], end="e")
    formations.save_draft(GUILD_ID, draft)
    published = FmMessageDraft(header="h", role_id=1, intro="i", fms=[_session_of(template)], end="e")
    formations.save_published(GUILD_ID, PublishedMessage(message_id=5000, channel_id=42, message=published))
    return draft, published


async def test_fm_modeles_lists_the_templates_ten_per_page(lab: Lab) -> None:
    """With 14 templates the first page holds 10 and a next button, the second the 4 others, and the count reads 14 modèles."""
    for number in range(14):
        _template(f"Formation {number:02}")

    first = await lab.modeles(lab.respo)

    assert first.ephemeral
    assert first.response.embeds[0].title == TITLE
    assert _listed(first.response) == [f"Formation {number:02}" for number in range(10)]
    assert (first.response.embeds[0].description or "").endswith("-# Page 1/2 · 14 modèles")
    assert first.response.embeds[0].footer.text == "Un modèle pré-remplit /fm brouillon → Ajouter une session"
    buttons = _buttons(first.response)
    assert buttons["Page 1/2"]["disabled"]
    assert buttons["◀ Précédent"]["disabled"]
    assert not buttons["Suivant ▶"]["disabled"]

    second = await lab.respo.click(first.response, custom_id=buttons["Suivant ▶"]["custom_id"])

    assert _listed(second.response) == [f"Formation {number:02}" for number in range(10, 14)]
    assert (second.response.embeds[0].description or "").endswith("-# Page 2/2 · 14 modèles")
    buttons = _buttons(second.response)
    assert buttons["Suivant ▶"]["disabled"]
    assert not buttons["◀ Précédent"]["disabled"]

    back = await lab.respo.click(second.response, custom_id=buttons["◀ Précédent"]["custom_id"])

    assert _listed(back.response) == [f"Formation {number:02}" for number in range(10)]
    assert (back.response.embeds[0].description or "").endswith("-# Page 1/2 · 14 modèles")
    assert _buttons(back.response)["Page 1/2"]["disabled"]


async def test_one_template_counts_in_the_singular(lab: Lab) -> None:
    """A single template reads 1 modèle and its seats read in the singular."""
    catalogue.create_template(GUILD_ID, TemplateFields("Brodeuse", "1 h", 1, "", True))

    listing = await lab.modeles(lab.respo)

    description = listing.response.embeds[0].description or ""
    assert "**Brodeuse** — 1 h · 1 place · excusable" in description
    assert description.endswith("-# Page 1/1 · 1 modèle")


async def test_an_empty_catalogue_says_how_to_start(lab: Lab) -> None:
    """With no template the list says how to create one, and Modifier and Supprimer wait for a template."""
    listing = await lab.modeles(lab.respo)

    assert listing.response.embeds[0].description == EMPTY
    buttons = _buttons(listing.response)
    assert not buttons["➕ Nouveau modèle"]["disabled"]  # noqa: RUF001 - the sign the copy asks for
    assert buttons["✏️ Modifier"]["disabled"]
    assert buttons["🗑️ Supprimer"]["disabled"]


async def test_a_new_template_is_stored_and_listed(lab: Lab) -> None:
    """A filled form stores the template on this server and shows it in the list under a confirmation."""
    listing = await lab.modeles(lab.respo)

    saved = await _new_template(lab.respo, listing, {})

    assert [t.fields for t in _stored()] == [TemplateFields("Brodeuse", "1 h", 3, "Tissu fourni.", False)]
    assert _stored(OTHER_GUILD_ID) == []
    assert saved.response.content == "✅ Modèle **Brodeuse** enregistré."
    assert _listed(saved.response) == ["Brodeuse"]


async def test_the_form_of_a_new_template_opens_empty_with_excusable_preset_to_oui(lab: Lab) -> None:
    """The new form is titled Nouveau modèle, holds no text, and its menu offers Oui, preset, and Non."""
    listing = await lab.modeles(lab.respo)

    shown = await lab.respo.click(listing.response, custom_id="cat:new")

    assert shown.modal["title"] == "Nouveau modèle"
    assert list(_values(shown).values()) == [None, None, None, None]
    assert [row["components"][0]["placeholder"] for row in shown.modal["components"][1:2]] == ["ex. 45 min, 1h30"]
    menu = shown.modal["components"][-1]
    assert (menu["type"], menu["label"]) == (18, "Excusable")
    assert [(o["label"], o.get("default", False)) for o in menu["component"]["options"]] == [("Oui", True), ("Non", False)]


@pytest.mark.parametrize(
    ("given", "refusal"),
    [
        ({"Nom": "soudure"}, "Un modèle s'appelle déjà **soudure** sur ce serveur."),
        ({"Places": "0"}, "Places : un nombre entier de 1 à 500."),
        ({"Places": "x"}, "Places : un nombre entier de 1 à 500."),
    ],
    ids=["taken-name-in-another-case", "seats-zero", "seats-not-a-number"],
)
async def test_a_template_form_refuses_and_stores_nothing(lab: Lab, given: dict[str, str], refusal: str) -> None:
    """A taken name or bad seats get a private refusal naming the problem, and the stored templates stay as they were."""
    _template("Soudure")
    before = _stored()
    listing = await lab.modeles(lab.respo)

    refused = await _new_template(lab.respo, listing, given)

    assert refused.ephemeral
    assert refused.response.content == f"{refusal} Rien n'a été enregistré."
    assert _stored() == before


async def test_a_name_taken_in_another_case_of_an_accented_letter_is_refused(lab: Lab) -> None:
    """The form refuses "électronique" when "Électronique" is stored, and the server keeps one template."""
    _template("Électronique")
    before = _stored()
    listing = await lab.modeles(lab.respo)

    refused = await _new_template(lab.respo, listing, {"Nom": "électronique"})

    assert refused.response.content == "Un modèle s'appelle déjà **électronique** sur ce serveur. Rien n'a été enregistré."
    assert _stored() == before


async def test_a_name_taken_in_another_server_is_free_here(lab: Lab) -> None:
    """Two servers may each have a template of the same name."""
    _template("Brodeuse", guild_id=OTHER_GUILD_ID)
    listing = await lab.modeles(lab.respo)

    saved = await _new_template(lab.respo, listing, {})

    assert saved.response.content == "✅ Modèle **Brodeuse** enregistré."
    assert [t.fields.name for t in _stored(OTHER_GUILD_ID)] == ["Brodeuse"]


async def test_a_blank_name_is_refused(lab: Lab) -> None:
    """A name of spaces only is refused, since "Soudure" and "Soudure " must not both exist."""
    listing = await lab.modeles(lab.respo)

    refused = await _new_template(lab.respo, listing, {"Nom": "   "})

    assert refused.response.content == "Nom : à remplir. Rien n'a été enregistré."
    assert _stored() == []


async def test_editing_a_template_changes_no_session_already_in_the_draft(lab: Lab) -> None:
    """Changing a template stores its new values and leaves the draft and the published announcement as they were."""
    template = _template("Soudure", seats=4)
    draft, published = _store_sessions(template)
    listing = await lab.modeles(lab.respo)

    shown = await _picked(lab.respo, listing, "edit", template)

    assert shown.modal["title"] == "Modifier le modèle"
    assert _values(shown) == {"Nom": "Soudure", "Durée": "1 h", "Places": "4", "Description": "Apportez vos lunettes."}
    saved = await lab.respo.submit_modal(shown, _form(shown, {"Nom": "Soudure avancée", "Places": "6", "Excusable": "Oui"}))

    assert saved.response.content == "✅ Modèle **Soudure avancée** enregistré."
    (stored,) = _stored()
    assert (stored.id, stored.fields.name, stored.fields.seats, stored.fields.excusable) == (
        template.id,
        "Soudure avancée",
        6,
        True,
    )
    assert formations.get_draft(GUILD_ID) == draft
    current = formations.current_published(GUILD_ID)
    assert current is not None
    assert current.message == published


async def test_renaming_a_template_onto_another_is_refused(lab: Lab) -> None:
    """Giving a template the name of another is refused and both keep their values."""
    template = _template("Soudure")
    _template("Brodeuse")
    before = _stored()
    listing = await lab.modeles(lab.respo)

    shown = await _picked(lab.respo, listing, "edit", template)
    refused = await lab.respo.submit_modal(shown, _form(shown, {"Nom": "BRODEUSE"}))

    assert refused.response.content == "Un modèle s'appelle déjà **BRODEUSE** sur ce serveur. Rien n'a été enregistré."
    assert _stored() == before


async def test_editing_a_template_deleted_meanwhile_is_refused(lab: Lab) -> None:
    """A form for a template deleted since is refused as gone, and nothing is stored."""
    template = _template("Soudure")
    listing = await lab.modeles(lab.respo)
    shown = await _picked(lab.respo, listing, "edit", template)
    catalogue.delete_template(GUILD_ID, template.id, expected=template.fields)

    refused = await lab.respo.submit_modal(shown, _form(shown, {"Nom": "Soudure avancée"}))

    assert refused.response.content == f"{GONE} Rien n'a été enregistré."
    assert _stored() == []


async def test_editing_a_template_changed_meanwhile_is_refused(lab: Lab) -> None:
    """A form opened from a copy that another editor changed since is refused, and the other editor's values stay."""
    template = _template("Soudure", seats=4)
    listing = await lab.modeles(lab.respo)
    shown = await _picked(lab.respo, listing, "edit", template)
    catalogue.update_template(GUILD_ID, template.id, TemplateFields("Soudure", "2 h", 4, "", False), expected=template.fields)

    refused = await lab.respo.submit_modal(shown, _form(shown, {"Places": "6"}))

    assert refused.response.content == "Ce modèle a changé depuis : rouvre-le pour le modifier. Rien n'a été enregistré."
    assert [t.fields.duration for t in _stored()] == ["2 h"]


async def test_deleting_a_template_asks_first_and_keeps_the_sessions(lab: Lab) -> None:
    """A deletion shows what goes and waits for confirmation; once confirmed the template is gone, the sessions stay."""
    template = _template("Soudure")
    draft, published = _store_sessions(template)
    listing = await lab.modeles(lab.respo)

    asked = await _picked(lab.respo, listing, "del", template)

    assert asked.response.content == (
        "Supprimer le modèle **Soudure** ? Les sessions déjà ajoutées au brouillon ou publiées ne changent pas."
    )
    assert _stored() == [template]
    buttons = _buttons(asked.response)
    assert set(buttons) == {"🗑️ Supprimer", "Annuler"}
    assert buttons["🗑️ Supprimer"]["custom_id"] == _confirm_id(template)
    assert re.fullmatch(rf"cat:del-ok:{template.id}:[0-9a-f]{{12}}", _confirm_id(template))

    done = await lab.respo.click(asked.response, custom_id=_confirm_id(template))

    assert _stored() == []
    assert done.response.content == "Modèle **Soudure** supprimé."
    assert (done.response.embeds[0].description or "") == EMPTY
    assert formations.get_draft(GUILD_ID) == draft
    current = formations.current_published(GUILD_ID)
    assert current is not None
    assert current.message == published


async def test_a_deletion_confirmed_after_the_template_changed_is_refused(lab: Lab) -> None:
    """A confirmation asked for a template that another editor rewrote since deletes nothing and says it changed."""
    template = _template("Soudure")
    asking = await lab.modeles(lab.respo)
    asked = await _picked(lab.respo, asking, "del", template)
    editing = await lab.modeles(lab.admin)
    shown = await _picked(lab.admin, editing, "edit", template)
    await lab.admin.submit_modal(
        shown, _form(shown, {"Nom": "Découpe laser", "Durée": "3 h", "Places": "8", "Description": "", "Excusable": "Non"})
    )
    rewritten = _stored()

    refused = await lab.respo.click(asked.response, custom_id=_confirm_id(template))

    assert refused.ephemeral
    assert refused.response.content == "Ce modèle a changé depuis : rouvre la liste pour le supprimer. Rien n'a été modifié."
    assert _stored() == rewritten
    assert [t.fields.name for t in rewritten] == ["Découpe laser"]


async def test_cancelling_a_deletion_keeps_the_template(lab: Lab) -> None:
    """Annuler shows the list again with the template in it and says that nothing was deleted."""
    template = _template("Soudure")
    listing = await lab.modeles(lab.respo)
    asked = await _picked(lab.respo, listing, "del", template)

    kept = await lab.respo.click(asked.response, custom_id="cat:del-no")

    assert kept.response.content == "Rien n'a été supprimé."
    assert _listed(kept.response) == ["Soudure"]
    assert _stored() == [template]


async def test_the_menu_of_templates_offers_each_name_with_its_duration_and_seats(lab: Lab) -> None:
    """The choice menu lists the page's templates by name, the description line giving duration, seats and excusable."""
    _template("Soudure", seats=4, excusable=True)
    _template("Brodeuse", seats=1)
    listing = await lab.modeles(lab.respo)

    chosen = await lab.respo.click(listing.response, custom_id="cat:edit")

    assert chosen.response.content == "**Quel modèle modifier ?**"
    (menu,) = (c for row in chosen.response.components for c in row["components"] if c["type"] == 3)
    assert menu["placeholder"] == "Modèles 1 à 2 sur 2…"
    assert [(o["label"], o["description"]) for o in menu["options"]] == [
        ("Brodeuse", "1 h · 1 place"),
        ("Soudure", "1 h · 4 places · excusable"),
    ]


async def test_the_menu_shows_a_duration_with_markdown_characters_as_typed(lab: Lab) -> None:
    """A menu shows no markup, so a duration typed with an underscore or a star has no backslash in the option."""
    catalogue.create_template(GUILD_ID, TemplateFields("Soudure", "1_h *env.*", 4, "", False))
    listing = await lab.modeles(lab.respo)
    listed = listing.response.embeds[0].description or ""

    chosen = await lab.respo.click(listing.response, custom_id="cat:edit")

    (menu,) = (c for row in chosen.response.components for c in row["components"] if c["type"] == 3)
    assert [o["description"] for o in menu["options"]] == ["1_h *env.* · 4 places"]
    assert "1\\_h" in listed  # the list is markdown, so it escapes


async def test_the_menu_of_templates_pages_beyond_discords_limit(lab: Lab) -> None:
    """With 30 templates the menu holds 25 options and its page buttons reach the other 5."""
    for number in range(30):
        _template(f"Formation {number:02}")
    listing = await lab.modeles(lab.respo)

    chosen = await lab.respo.click(listing.response, custom_id="cat:del")

    buttons = _buttons(chosen.response)
    assert buttons["Modèles 1 à 25 sur 30"]["disabled"]
    next_page = await lab.respo.click(chosen.response, custom_id=buttons["Suivant ▶"]["custom_id"])
    (menu,) = (c for row in next_page.response.components for c in row["components"] if c["type"] == 3)
    assert next_page.response.content == "**Quel modèle supprimer ?**"
    assert [o["label"] for o in menu["options"]] == [f"Formation {number:02}" for number in range(25, 30)]
    assert menu["custom_id"] == "cat:pick:del:1"


@pytest.mark.parametrize(
    ("who", "may"),
    [("respo", True), ("admin", True), ("codir", False), ("temp", False), ("president", False), ("plain", False)],
    ids=["respo-formation", "administrateur", "codir", "admin-temp", "president", "plain-member"],
)
async def test_who_may_manage_templates(lab: Lab, who: str, may: bool) -> None:
    """Respo Formation and Administrateur open the list; CoDir, Admin -temp-, Président and a plain member are refused."""
    result = await lab.modeles(getattr(lab, who))

    assert result.ephemeral
    if may:
        assert result.response.embeds[0].title == TITLE
    else:
        assert not result.response.embeds
        assert (result.response.content or "").startswith(ErrorMessages.ROLE_REQUIRED.split("{")[0])


async def test_a_member_who_lost_the_role_is_refused_at_the_click_and_nothing_is_stored(lab: Lab) -> None:
    """Once the role is gone, a click, an open form and a shown confirmation are each refused and store nothing."""
    template = _template("Soudure")
    listing = await lab.modeles(lab.respo)
    asking = await lab.modeles(lab.respo)
    asked = await _picked(lab.respo, asking, "del", template)
    shown = await lab.respo.click(listing.response, custom_id="cat:new")
    before = _stored()
    lab.lose_roles(lab.respo)
    await asyncio.sleep(0)

    submitted = await lab.respo.submit_modal(shown, _form(shown, FORM))
    confirmed = await lab.respo.click(asked.response, custom_id=_confirm_id(template))
    clicked = await lab.respo.click(listing.response, custom_id="cat:new")

    for result in (submitted, confirmed, clicked):
        assert result.ephemeral
        assert result.response.content == RIGHT_LOST_MESSAGE
    assert _stored() == before


async def test_a_server_no_longer_served_refuses_the_click(lab: Lab) -> None:
    """When the server leaves the bot's list, a button and a confirmation are refused and store nothing."""
    template = _template("Soudure")
    listing = await lab.modeles(lab.respo)
    asking = await lab.modeles(lab.respo)
    asked = await _picked(lab.respo, asking, "del", template)
    before = _stored()
    bot: Any = lab.env.bot
    bot.guild_access = GuildAccessPolicy(allowed=frozenset({OTHER_GUILD_ID}))

    clicked = await lab.respo.click(listing.response, custom_id="cat:new")
    confirmed = await lab.respo.click(asked.response, custom_id=_confirm_id(template))

    for result in (clicked, confirmed):
        assert result.ephemeral
        assert result.response.content == NOT_SERVED
    assert _stored() == before


async def _forged(
    lab: Lab,
    custom_id: str,
    *,
    client: Any | None = None,
    roles: tuple[str, ...] = (RoleNames.TRAININGS_MANAGER,),
    items: Any = CATALOGUE_ITEMS,
) -> MagicMock:
    """Send a member's click (a Respo Formation by default) on an item and return the interaction the bot answered."""
    interaction = make_interaction(user_id=lab.respo.id, guild_id=GUILD_ID)
    interaction.client = client or lab.env.bot
    interaction.user.roles = [make_role(name) for name in roles]
    interaction.message = MagicMock()
    item_class = next(cls for cls in items if cls.__discord_ui_compiled_template__.fullmatch(custom_id))
    match = item_class.__discord_ui_compiled_template__.fullmatch(custom_id)
    item = await item_class.from_custom_id(interaction, MagicMock(), match)  # pyright: ignore[reportArgumentType]
    if await item.interaction_check(interaction):
        await item.callback(interaction)
    return interaction


EVERY_ITEM_ID = (
    "cat:page:1",
    "cat:new",
    "cat:edit",
    "cat:del",
    "cat:pick:edit:0",
    "cat:pick:del:0",
    "cat:pnav:edit:0",
    "cat:pnav:del:0",
    "cat:del-ok:{template}:0123456789ab",
    "cat:del-no",
)
"""One custom ID of each button and menu of the template screens; ``{template}`` is the ID of a stored template."""


@pytest.mark.parametrize("custom_id", EVERY_ITEM_ID, ids=lambda custom_id: custom_id.split("{")[0].strip(":").replace(":", "-"))
async def test_every_template_item_refuses_a_member_who_lost_the_role(lab: Lab, custom_id: str) -> None:
    """A member holding no staff role is refused on each button and menu of the template screens, and nothing is stored."""
    template = _template("Soudure")
    before = _stored()

    interaction = await _forged(lab, custom_id.format(template=template.id), roles=())

    interaction.response.send_message.assert_awaited_once()
    assert interaction.response.send_message.await_args.args[0] == RIGHT_LOST_MESSAGE
    assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True
    interaction.response.edit_message.assert_not_awaited()
    interaction.response.send_modal.assert_not_awaited()
    assert _stored() == before


@pytest.mark.parametrize("custom_id", EVERY_ITEM_ID, ids=lambda custom_id: custom_id.split("{")[0].strip(":").replace(":", "-"))
async def test_every_template_item_refuses_a_server_no_longer_served(lab: Lab, custom_id: str) -> None:
    """On a server that left the bot's list, each button and menu of the template screens is refused before anything is read."""
    template = _template("Soudure")
    before = _stored()
    bot: Any = lab.env.bot
    bot.guild_access = GuildAccessPolicy(allowed=frozenset({OTHER_GUILD_ID}))

    interaction = await _forged(lab, custom_id.format(template=template.id))

    interaction.response.send_message.assert_awaited_once()
    assert interaction.response.send_message.await_args.args[0] == NOT_SERVED
    assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True
    interaction.response.edit_message.assert_not_awaited()
    interaction.response.send_modal.assert_not_awaited()
    assert _stored() == before


async def test_a_form_left_open_on_a_server_no_longer_served_stores_nothing(lab: Lab) -> None:
    """When the server leaves the bot's list while a form is open, its submit is refused and stores nothing."""
    listing = await lab.modeles(lab.respo)
    shown = await lab.respo.click(listing.response, custom_id="cat:new")
    bot: Any = lab.env.bot
    bot.guild_access = GuildAccessPolicy(allowed=frozenset({OTHER_GUILD_ID}))

    submitted = await lab.respo.submit_modal(shown, _form(shown, FORM))

    assert submitted.ephemeral
    assert submitted.response.content == NOT_SERVED
    assert _stored() == []


async def test_a_forged_template_id_from_another_server_reads_as_missing(lab: Lab) -> None:
    """A confirmation built for another server's template answers like a missing one, and that template stays."""
    theirs = _template("Soudure", guild_id=OTHER_GUILD_ID)

    interaction = await _forged(lab, _confirm_id(theirs))

    interaction.response.send_message.assert_awaited_once()
    assert interaction.response.send_message.await_args.args[0].startswith(GONE)
    assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True
    assert _stored(OTHER_GUILD_ID) == [theirs]


async def test_a_forged_menu_choice_of_another_servers_template_reads_as_missing(lab: Lab) -> None:
    """A template ID chosen in a menu that belongs to another server is answered as missing, with no form opened."""
    theirs = _template("Soudure", guild_id=OTHER_GUILD_ID)
    interaction = make_interaction(user_id=lab.respo.id, guild_id=GUILD_ID)
    interaction.client = lab.env.bot
    interaction.user.roles = [make_role(RoleNames.TRAININGS_MANAGER)]
    interaction.message = MagicMock()
    item_class = next(cls for cls in CATALOGUE_ITEMS if cls.__discord_ui_compiled_template__.fullmatch("cat:pick:edit:0"))
    match = item_class.__discord_ui_compiled_template__.fullmatch("cat:pick:edit:0")
    item = await item_class.from_custom_id(interaction, MagicMock(), match)  # pyright: ignore[reportArgumentType]
    item.item._values = [str(theirs.id)]  # pyright: ignore[reportAttributeAccessIssue]

    await item.callback(interaction)

    interaction.response.send_modal.assert_not_awaited()
    assert interaction.response.send_message.await_args.args[0].startswith(GONE)


@pytest.mark.parametrize("huge", ["9223372036854775808", "99999999999999999999999"])
async def test_a_template_id_past_the_database_limit_reads_as_missing(lab: Lab, huge: str) -> None:
    """A forged button or menu value holding an ID no database can store is answered as a missing template."""
    _template("Soudure")
    confirm = await _forged(lab, f"cat:del-ok:{huge}:0123456789ab")
    interaction = make_interaction(user_id=lab.respo.id, guild_id=GUILD_ID)
    interaction.client = lab.env.bot
    interaction.user.roles = [make_role(RoleNames.TRAININGS_MANAGER)]
    interaction.message = MagicMock()
    item_class = next(cls for cls in CATALOGUE_ITEMS if cls.__discord_ui_compiled_template__.fullmatch("cat:pick:edit:0"))
    match = item_class.__discord_ui_compiled_template__.fullmatch("cat:pick:edit:0")
    item = await item_class.from_custom_id(interaction, MagicMock(), match)  # pyright: ignore[reportArgumentType]
    item.item._values = [huge]  # pyright: ignore[reportAttributeAccessIssue]

    await item.callback(interaction)

    for answered in (confirm, interaction):
        answered.response.send_modal.assert_not_awaited()
        assert answered.response.send_message.await_args.args[0].startswith(GONE)


async def test_the_template_buttons_work_after_a_restart(started_bot: Any) -> None:
    """A second bot on the same database answers the clicks on a list the first bot sent, from the database alone."""
    for number in range(12):
        _template(f"Formation {number:02}", guild_id=GUILD_ID)
    last = _stored()[-1]
    _, _, view = templates_message(GUILD_ID, 0)
    ids = [child.custom_id for child in view.children]  # pyright: ignore[reportAttributeAccessIssue]  # buttons and menus
    store = started_bot._connection._view_store

    async def click(custom_id: str) -> MagicMock:
        interaction = make_interaction(user_id=7, guild_id=GUILD_ID)
        interaction.client = started_bot
        interaction.user.roles = [make_role(RoleNames.TRAININGS_MANAGER)]
        interaction.message = MagicMock()
        pattern, item_class = next((p, c) for p, c in store._dynamic_items.items() if p.fullmatch(custom_id))
        item = await item_class.from_custom_id(interaction, MagicMock(), pattern.fullmatch(custom_id))
        assert await item.interaction_check(interaction)
        await item.callback(interaction)
        return interaction

    assert {"cat:page:1", "cat:new", "cat:edit", "cat:del"} <= set(ids)
    page = await click("cat:page:1")
    new = await click("cat:new")
    confirmed = await click(_confirm_id(last))

    assert page.response.edit_message.await_args.kwargs["embed"].description.endswith("-# Page 2/2 · 12 modèles")
    assert new.response.send_modal.await_args.args[0].title == "Nouveau modèle"
    assert confirmed.response.edit_message.await_args.kwargs["content"] == f"Modèle **{last.fields.name}** supprimé."
    assert last.id not in [t.id for t in _stored()]


async def test_the_catalogue_items_are_registered_on_the_started_bot(started_bot: Any) -> None:
    """Every button and menu of the template screens is registered with the bot, and removed when the cog goes."""
    store = started_bot._connection._view_store
    assert set(CATALOGUE_ITEMS) <= set(store._dynamic_items.values())

    await started_bot.remove_cog("FormationManagement")

    assert not set(CATALOGUE_ITEMS) & set(store._dynamic_items.values())


async def _help_text(who: Any, lab: Lab) -> str:
    """Join what `/help` answers a member."""
    await lab.show()
    result = await who.slash(lab.commands, "help")
    embed = result.response.embeds[0]
    return " ".join([embed.description or "", *[f"{f.name} {f.value}" for f in embed.fields]])


@pytest.mark.parametrize(
    ("who", "listed"),
    [("respo", True), ("admin", True), ("codir", False), ("temp", False), ("president", False), ("plain", False)],
    ids=["respo-formation", "administrateur", "codir", "admin-temp", "president", "plain-member"],
)
async def test_fm_modeles_is_listed_for_respo_formation_and_administrateur_only(lab: Lab, who: str, listed: bool) -> None:
    """The help lists `/fm modeles` for the two roles that may run it and for nobody else."""
    text = await _help_text(getattr(lab, who), lab)

    assert ("/fm modeles" in text) is listed


# region ====== /fm brouillon ======

THIN = "\u00a0"
TOO_LONG = "Discord en accepte 2 000 : retire une session ou raccourcis une description."


def _session(emoji: str, name: str, *, days: float, trainer: Any = None, seats: int = 5) -> Formation:
    """A session starting ``days`` from now (negative: already started), given by `<@trainer>`."""
    return Formation(
        emoji=emoji,
        name=name,
        trainer_mention=f"<@{trainer.id if trainer else 1}>",
        start_iso=(datetime.now(PARIS_TZ) + timedelta(days=days)).isoformat(),
        duration="1 h",
        seats=seats,
    )


def _stage(
    lab: Lab, sessions: list[Formation], *, role: bool = True, intro: str = "Bonjour", end: str = "À vite"
) -> FmMessageDraft:
    """Store the server's draft with these sessions, the notified role unless ``role`` is false, and return it."""
    draft = FmMessageDraft(
        header="# [FORMATIONS] :loudspeaker:", role_id=lab.notified.id if role else 0, intro=intro, fms=sessions, end=end
    )
    formations.save_draft(GUILD_ID, draft)
    return draft


def _three(lab: Lab) -> list[Formation]:
    """Three sessions, out of order of their start."""
    return [
        _session("🧡", "Soudure", days=4, trainer=lab.plain),
        _session("💛", "Brodeuse", days=2, trainer=lab.plain, seats=1),
        _session("💚", "Résine", days=3, trainer=lab.plain, seats=8),
    ]


def _guild(lab: Lab) -> Any:
    """The test server as the bot sees it."""
    guild = lab.env.bot.get_guild(GUILD_ID)
    assert guild is not None
    return guild


def _embed_lines(message: Any) -> list[str]:
    """The lines of the first embed's description."""
    description: str = message.embeds[0].description or ""
    return description.splitlines()


def _grouped(length: int) -> str:
    """Write a length with a no-break space as thousands separator, as the footer does."""
    return f"{length:,}".replace(",", THIN)


def _too_long(lab: Lab, *, extra: int = 1) -> FmMessageDraft:
    """Store a draft whose announcement is ``extra`` characters past Discord's limit and return it."""
    draft = _stage(lab, [_session("🧡", "Soudure", days=2, trainer=lab.plain)])
    draft.fms[0].description = "x"
    base = len(render_message(draft))
    draft.fms[0].description = "x" * (1 + 2000 + extra - base)
    formations.save_draft(GUILD_ID, draft)
    assert len(render_message(draft)) == 2000 + extra
    return draft


async def test_fm_brouillon_shows_the_draft_sorted_by_start(lab: Lab) -> None:
    """The brouillon lists the sessions by start with intro, trainer, seats and the role to notify, in a private message."""
    draft = _stage(lab, _three(lab))

    result = await lab.brouillon(lab.respo)

    embed = result.response.embeds[0]
    ordered = sorted(draft.fms, key=lambda fm: fm.start_dt)
    assert result.ephemeral
    assert embed.title == "📝 Brouillon de la semaine · 3 sessions"
    assert embed.colour is not None
    assert _embed_lines(result.response) == [
        "**Intro** : Bonjour",
        "",
        *[
            f"{fm.emoji} **{fm.name}** — <t:{int(fm.start_dt.timestamp())}:F> · <@{lab.plain.id}> · "
            f"{fm.seats} place{'s' if fm.seats > 1 else ''}"
            for fm in ordered
        ],
        "",
        f"-# Triées par date · rôle notifié : <@&{lab.notified.id}> · "
        f"annonce : {len(render_message(draft))} / 2 000 caractères",
    ]
    assert list(_buttons(result.response)) == ["➕ Ajouter une session", "💬 Intro / conclusion", "👁️ Aperçu", "📣 Publier"]  # noqa: RUF001 - the sign the copy asks for
    assert _ids(result.response) == ["fmd:add", "fmd:text", "fmd:preview", "fmd:publish"]
    assert not any(b["disabled"] for b in _buttons(result.response).values())


async def test_an_empty_brouillon_says_so_and_greys_publier(lab: Lab) -> None:
    """With no draft stored the brouillon says there is no session, no intro and no role, and Publier takes no click."""
    result = await lab.brouillon(lab.respo)

    assert result.response.embeds[0].title == "📝 Brouillon de la semaine · 0 session"
    assert _embed_lines(result.response)[0] == "**Intro** : _(vide)_"
    assert "_Aucune session pour l'instant._" in _embed_lines(result.response)
    assert "rôle notifié : aucun" in _embed_lines(result.response)[-1]
    buttons = _buttons(result.response)
    assert buttons["📣 Publier"]["disabled"]
    assert not buttons["💬 Intro / conclusion"]["disabled"]
    assert not buttons["👁️ Aperçu"]["disabled"]


async def test_the_brouillon_counts_the_announcement_and_warns_past_2000(lab: Lab) -> None:
    """The last line counts the announcement with a no-break thousands separator; past 2 000 a warning comes before it."""
    fine = _stage(lab, _three(lab))
    shown = await lab.brouillon(lab.respo)
    assert not any(line.startswith("⚠️") for line in _embed_lines(shown.response))
    assert _embed_lines(shown.response)[-1].endswith(f"annonce : {len(render_message(fine))} / 2 000 caractères")

    long = _too_long(lab, extra=345)
    shown = await lab.brouillon(lab.respo)

    lines = _embed_lines(shown.response)
    length = _grouped(len(render_message(long)))
    assert length == f"2{THIN}345"
    assert lines[-2] == f"⚠️ **L'annonce fait {length} caractères**, {TOO_LONG}"
    assert lines[-1].endswith(f"annonce : {length} / 2 000 caractères")


async def test_intro_and_conclusion_set_the_text_the_role_and_the_header(lab: Lab) -> None:
    """The form opens on the stored text and role, and saves the intro, closing, link, role and header, sessions kept."""
    draft = _stage(lab, _three(lab), intro="Avant", end="Avant aussi")
    other_role = lab.guild.create_role("Autre")
    result = await lab.brouillon(lab.respo)

    shown = await lab.respo.click(result.response, custom_id="fmd:text")

    assert shown.modal["title"] == "Intro et conclusion"
    assert _values(shown)["Intro"] == "Avant"
    assert _values(shown)["Conclusion"] == "Avant aussi"
    assert _values(shown)["URL du formulaire (optionnel)"] == draft.request_forms_url
    (label,) = (i for i in shown.modal["components"] if i["type"] == 18 and i["label"] == "Rôle à notifier")
    assert label["component"]["type"] == 6
    assert label["component"]["default_values"] == [{"id": lab.notified.id, "type": "role"}]

    saved = await lab.respo.submit_modal(
        shown,
        _form(
            shown,
            {
                "Intro": "Après",
                "Conclusion": "Fin",
                "URL du formulaire (optionnel)": "https://example.org/propose",
                "Rôle à notifier": [other_role],
            },
        ),
    )

    stored = formations.get_draft(GUILD_ID)
    assert stored is not None
    assert (stored.intro, stored.end, stored.role_id, stored.header) == (
        "Après",
        "Fin",
        other_role.id,
        "# [FORMATIONS] :loudspeaker:",
    )
    assert stored.request_forms_url == "https://example.org/propose"
    assert stored.header == announcement_header(_guild(lab))
    assert stored.fms == draft.fms
    assert saved.response.content == "✅ Brouillon mis à jour."
    assert saved.response.embeds[0].title == "📝 Brouillon de la semaine · 3 sessions"
    assert _embed_lines(saved.response)[0] == "**Intro** : Après"


async def test_the_form_gives_a_draft_with_no_header_the_start_header(lab: Lab) -> None:
    """A draft that holds no header gets the line `/fm start` writes when the intro form is sent."""
    draft = _stage(lab, _three(lab))
    formations.save_draft(GUILD_ID, replace(draft, header=""))
    shown = await lab.respo.click((await lab.brouillon(lab.respo)).response, custom_id="fmd:text")

    await lab.respo.submit_modal(shown, _form(shown, {"Intro": "Bonjour", "Rôle à notifier": [lab.notified]}))

    stored = formations.get_draft(GUILD_ID)
    assert stored is not None
    assert stored.header == announcement_header(_guild(lab)) != ""


async def test_a_session_added_while_the_form_is_open_survives_its_submit(lab: Lab) -> None:
    """The form saves onto the draft as stored when it is sent, so a session added meanwhile is not erased."""
    draft = _stage(lab, _three(lab))
    shown = await lab.respo.click((await lab.brouillon(lab.respo)).response, custom_id="fmd:text")
    late = _session("💙", "Laser", days=5, trainer=lab.plain)
    formations.save_draft(GUILD_ID, replace(draft, fms=[*draft.fms, late]))

    await lab.respo.submit_modal(shown, _form(shown, {"Intro": "Après", "Rôle à notifier": [lab.notified]}))

    stored = formations.get_draft(GUILD_ID)
    assert stored is not None
    assert stored.intro == "Après"
    assert [fm.emoji for fm in stored.fms] == ["🧡", "💛", "💚", "💙"]


async def test_a_blank_link_in_the_form_keeps_the_usual_one(lab: Lab) -> None:
    """Leaving the URL empty stores the usual forms link, as `/fm start` does."""
    draft = _stage(lab, _three(lab))
    shown = await lab.respo.click((await lab.brouillon(lab.respo)).response, custom_id="fmd:text")

    await lab.respo.submit_modal(shown, _form(shown, {"URL du formulaire (optionnel)": " ", "Rôle à notifier": [lab.notified]}))

    stored = formations.get_draft(GUILD_ID)
    assert stored is not None
    assert stored.request_forms_url == draft.request_forms_url


async def test_apercu_shows_the_announcement_as_publier_posts_it_and_notifies_nobody(lab: Lab) -> None:
    """Aperçu shows the rendered announcement privately and asks Discord to notify nobody."""
    draft = _stage(lab, _three(lab))
    listing = await lab.brouillon(lab.respo)

    preview = await lab.respo.click(listing.response, custom_id="fmd:preview")

    assert preview.ephemeral
    assert preview.response.content == render_message(draft)
    assert f"<@&{lab.notified.id}>" in preview.response.content
    sent = lab.env.backend.get_message(preview.response.message.channel.id, preview.response.message.id)
    assert sent.ping_role_ids == []
    assert sent.ping_user_ids == []
    assert not sent.mention_everyone


async def test_apercu_of_a_too_long_announcement_shows_the_warning(lab: Lab) -> None:
    """Past 2 000 characters Aperçu says so with the length instead of the announcement."""
    draft = _too_long(lab)
    listing = await lab.brouillon(lab.respo)

    preview = await lab.respo.click(listing.response, custom_id="fmd:preview")

    assert preview.ephemeral
    length = _grouped(len(render_message(draft)))
    assert preview.response.content == (
        f"L'annonce fait {length} caractères, Discord en accepte 2 000 : elle ne peut pas être publiée ainsi."
    )


async def test_publier_starts_on_the_last_announcement_channel(lab: Lab) -> None:
    """Publier asks for the channel with the last announcement's channel chosen, and no choice when there is none."""
    _stage(lab, _three(lab))
    listing = await lab.brouillon(lab.respo)

    fresh = await lab.respo.click(listing.response, custom_id="fmd:publish")

    assert fresh.response.content == "**Où publier ?** Choisis le salon, puis **Publier**."
    assert not fresh.response.embeds
    (menu,) = (c for row in fresh.response.components for c in row["components"] if c["type"] == 8)
    assert menu["placeholder"] == "Salon de l'annonce"
    assert menu["channel_types"] == [0, 5]
    assert not menu.get("default_values")
    assert _buttons(fresh.response)["📣 Publier"]["disabled"]

    published = FmMessageDraft(header="h", role_id=1, intro="i", fms=_three(lab), end="e")
    formations.save_published(GUILD_ID, PublishedMessage(message_id=5000, channel_id=lab.announce.id, message=published))
    listing = await lab.brouillon(lab.respo)
    preset = await lab.respo.click(listing.response, custom_id="fmd:publish")

    (menu,) = (c for row in preset.response.components for c in row["components"] if c["type"] == 8)
    assert menu["default_values"] == [{"id": lab.announce.id, "type": "channel"}]
    go = _buttons(preset.response)["📣 Publier"]
    assert go["custom_id"] == f"fmd:pub-go:{lab.announce.id}"
    assert not go["disabled"]


async def _published_via_publier(lab: Lab, channel: Any) -> Any:
    """Open the brouillon, click Publier, choose the channel and click Publier again; return the last result."""
    listing = await lab.brouillon(lab.respo)
    asked = await lab.respo.click(listing.response, custom_id="fmd:publish")
    chosen = await lab.respo.select(asked.response, [channel], custom_id="fmd:pub-ch")
    return await lab.respo.click(chosen.response, custom_id=f"fmd:pub-go:{channel.id}")


async def test_publier_posts_in_the_chosen_channel_like_fm_publish(lab: Lab) -> None:
    """Publier posts the same message with the same reactions as `/fm publish` and stores it as the published one."""
    _stage(lab, _three(lab))

    await _published_via_publier(lab, lab.elsewhere)

    by_button = lab.elsewhere.last_message
    assert by_button is not None
    stored = formations.current_published(GUILD_ID)
    assert stored is not None
    assert (stored.message_id, stored.channel_id) == (by_button.id, lab.elsewhere.id)
    await lab.show()
    await lab.respo.slash(lab.commands, "fm publish", channel=lab.announce)
    by_command = lab.announce.last_message
    assert by_command is not None
    assert by_button.content == by_command.content
    assert [str(r.emoji) for r in by_button.reactions] == [str(r.emoji) for r in by_command.reactions] == ["🧡", "💛", "💚"]


async def test_publier_pings_the_chosen_role_and_never_everyone(lab: Lab) -> None:
    """A typed @everyone, @here or other role in the intro is posted as text; only the chosen role and trainers are pinged."""
    other = lab.guild.create_role("Autre")
    _stage(lab, _three(lab), intro=f"@everyone @here <@&{other.id}> venez")

    await _published_via_publier(lab, lab.announce)

    posted = lab.announce.last_message
    assert posted is not None
    assert "@everyone" in posted.content
    sent = lab.env.backend.get_message(lab.announce.id, posted.id)
    assert not sent.mention_everyone
    assert sent.ping_role_ids == [lab.notified.id]
    assert sent.ping_user_ids == [lab.plain.id]


async def _asked_where(lab: Lab, channel: Any, who: Any = None) -> Any:
    """Open the brouillon as a member, click Publier and choose the channel; return the question with its Publier button."""
    who = who or lab.respo
    listing = await lab.brouillon(who)
    asked = await who.click(listing.response, custom_id="fmd:publish")
    return (await who.select(asked.response, [channel], custom_id="fmd:pub-ch")).response


async def test_a_publier_left_open_elsewhere_is_refused_once_the_draft_is_posted(lab: Lab) -> None:
    """A question another member left open is refused after the draft is posted; the posts and the current message stay."""
    _stage(lab, _three(lab))
    mine = await _asked_where(lab, lab.announce)
    theirs = await _asked_where(lab, lab.announce, lab.admin)
    await lab.respo.click(mine, custom_id=f"fmd:pub-go:{lab.announce.id}")
    first = formations.current_published(GUILD_ID)
    assert first is not None

    again = await lab.admin.click(theirs, custom_id=f"fmd:pub-go:{lab.announce.id}")

    assert again.ephemeral
    assert again.response.content == (
        f"Cette annonce est déjà publiée dans <#{lab.announce.id}> : change le brouillon pour en publier une autre. "
        "Rien n'a été publié."
    )
    assert len(lab.announce.history()) == 1
    assert formations.current_published(GUILD_ID) == first


async def _after_the_post(lab: Lab, how: str) -> Any:
    """Publish the draft, then let a member sign up on the post or the hour-before reminder run; return the stored post."""
    await _published_via_publier(lab, lab.announce)
    posted = lab.announce.last_message
    assert posted is not None
    if how == "signed-up":
        await lab.codir.react(posted, "🧡")
        await lab.env.advance_time(120)
    else:
        current = formations.current_published(GUILD_ID)
        assert current is not None
        for fm in current.message.fms:
            fm.notified_hour_before = True
        formations.save_published(GUILD_ID, current)
    stored = formations.current_published(GUILD_ID)
    assert stored is not None
    assert stored.message != _without_sign_ups(stored)
    return stored


def _without_sign_ups(stored: PublishedMessage) -> FmMessageDraft:
    """The stored post as the draft that made it: the same message with no sign-up and no reminder."""
    return replace(
        stored.message,
        fms=[
            replace(fm, registered_users=[], waitlisted_users=[], notified_hour_before=False, notified_at_start=False)
            for fm in stored.message.fms
        ],
    )


@pytest.mark.parametrize("how", ["signed-up", "reminded"])
async def test_publier_still_refuses_the_posted_draft_once_members_signed_up_or_were_reminded(lab: Lab, how: str) -> None:
    """A sign-up or a reminder on the post does not make the unchanged draft new: no second post, the sign-ups stay."""
    _stage(lab, _three(lab))
    left_open = await _asked_where(lab, lab.announce, lab.admin)
    stored = await _after_the_post(lab, how)
    refusal = (
        f"Cette annonce est déjà publiée dans <#{lab.announce.id}> : change le brouillon pour en publier une autre. "
        "Rien n'a été publié."
    )

    listing = await lab.brouillon(lab.respo)
    asked = await lab.respo.click(listing.response, custom_id="fmd:publish")
    again = await lab.admin.click(left_open, custom_id=f"fmd:pub-go:{lab.announce.id}")

    assert asked.ephemeral
    assert asked.response.content == refusal
    assert again.ephemeral
    assert again.response.content == refusal
    assert len(lab.announce.history()) == 1
    assert formations.current_published(GUILD_ID) == stored


async def test_publier_with_a_changed_draft_after_a_sign_up_publishes_again(lab: Lab) -> None:
    """A draft changed after members signed up is a new announcement, which Publier posts."""
    draft = _stage(lab, _three(lab))
    first = await _after_the_post(lab, "signed-up")
    formations.save_draft(GUILD_ID, replace(draft, intro="Autre intro"))

    await _published_via_publier(lab, lab.announce)

    assert len(lab.announce.history()) == 2
    current = formations.current_published(GUILD_ID)
    assert current is not None
    assert current.message_id != first.message_id
    assert current.message.intro == "Autre intro"


async def test_publier_with_a_changed_draft_after_a_post_publishes_again(lab: Lab) -> None:
    """Once the draft changes, Publier posts it as a new announcement."""
    draft = _stage(lab, _three(lab))
    await _published_via_publier(lab, lab.announce)
    formations.save_draft(GUILD_ID, replace(draft, intro="Autre intro"))

    await _published_via_publier(lab, lab.announce)

    assert len(lab.announce.history()) == 2


async def test_publier_leaves_no_button_to_click_twice_on_the_question(lab: Lab) -> None:
    """The click answers on the question itself, which no longer offers Publier and says where the post went."""
    _stage(lab, _three(lab))
    question = await _asked_where(lab, lab.announce)

    await lab.respo.click(question, custom_id=f"fmd:pub-go:{lab.announce.id}")

    left = lab.env.backend.get_message(question.message.channel.id, question.message.id)
    assert left.content == f"📣 Annonce publiée dans <#{lab.announce.id}>."
    assert not left.components


async def test_a_publication_that_comes_while_another_is_posting_is_refused(lab: Lab, monkeypatch: pytest.MonkeyPatch) -> None:
    """While one publication is still posting, a second one on the server is refused, so the announcement is posted once."""
    _stage(lab, _three(lab))
    question = await _asked_where(lab, lab.announce)
    sending, release = asyncio.Event(), asyncio.Event()
    real_send = discord.TextChannel.send

    async def slow_send(self: Any, *args: Any, **kwargs: Any) -> Any:
        sending.set()
        await release.wait()
        return await real_send(self, *args, **kwargs)

    monkeypatch.setattr(discord.TextChannel, "send", slow_send)
    first = asyncio.create_task(lab.respo.click(question, custom_id=f"fmd:pub-go:{lab.announce.id}"))
    await asyncio.wait_for(sending.wait(), timeout=5)
    bot: Any = lab.env.bot
    cog = bot.get_cog("FormationManagement")
    second = make_interaction(user_id=lab.admin.id, guild_id=GUILD_ID)
    second.guild = _guild(lab)

    try:
        await asyncio.wait_for(cog.publish_draft(second, _guild(lab).get_channel(lab.announce.id)), timeout=5)
    finally:
        release.set()
    await first

    refusal = "Une publication est déjà en cours sur ce serveur. Rien n'a été publié."
    assert second.response.send_message.await_args.args == (refusal,)
    assert second.response.send_message.await_args.kwargs["ephemeral"]
    second.response.defer.assert_not_awaited()
    assert len(lab.announce.history()) == 1


_BAD_DRAFTS = {
    "empty": ("Le brouillon ne contient aucune session. Rien n'a été publié."),
    "no-role": "Choisis d'abord le rôle à notifier : **Intro / conclusion**. Rien n'a été publié.",
    "started": "La session **🧡 Soudure** a déjà commencé : retire-la ou change sa date. Rien n'a été publié.",
    "too-long": None,
}


def _bad_draft(lab: Lab, case: str) -> str:
    """Store the draft of a refusal case and return the refusal Publier gives for it."""
    if case == "empty":
        _stage(lab, [])
    elif case == "no-role":
        _stage(lab, _three(lab), role=False)
    elif case == "started":
        _stage(lab, [_session("🧡", "Soudure", days=-0.1, trainer=lab.plain), *_three(lab)[1:]])
    else:
        long = _too_long(lab, extra=1)
        return f"L'annonce fait {_grouped(len(render_message(long)))} caractères, {TOO_LONG} Rien n'a été publié."
    refusal = _BAD_DRAFTS[case]
    assert refusal is not None
    return refusal


@pytest.mark.parametrize("case", ["empty", "no-role", "started", "too-long"])
async def test_publier_refuses_and_posts_nothing(lab: Lab, case: str) -> None:
    """An empty draft, no role, a started session or 2 001 characters are refused at both clicks, nothing is posted."""
    _stage(lab, _three(lab))
    listing = await lab.brouillon(lab.respo)
    asked = await lab.respo.click((await lab.brouillon(lab.respo)).response, custom_id="fmd:publish")
    chosen = await lab.respo.select(asked.response, [lab.announce], custom_id="fmd:pub-ch")
    refusal = _bad_draft(lab, case)
    before = formations.get_draft(GUILD_ID)

    at_publier = await lab.respo.click(listing.response, custom_id="fmd:publish")
    at_go = await lab.respo.click(chosen.response, custom_id=f"fmd:pub-go:{lab.announce.id}")

    for result in (at_publier, at_go):
        assert result.ephemeral
        assert result.response.content == refusal
    assert lab.announce.last_message is None
    assert formations.current_published(GUILD_ID) is None
    assert formations.get_draft(GUILD_ID) == before


def test_publish_refusal_orders_its_checks_and_says_nothing_of_a_good_draft() -> None:
    """The refusal comes in the order empty, no role, started, too long; a draft that passes gives none."""
    started = _session("🧡", "Soudure", days=-1)
    fine = _session("💛", "Brodeuse", days=1)

    assert (
        publish_refusal(FmMessageDraft("h", 0, "", [], "")) == "Le brouillon ne contient aucune session. Rien n'a été publié."
    )
    assert (publish_refusal(FmMessageDraft("h", 0, "", [started], "")) or "").startswith("Choisis d'abord le rôle")
    assert (publish_refusal(FmMessageDraft("h", 5, "", [started], "")) or "").startswith("La session **🧡 Soudure**")
    assert publish_refusal(FmMessageDraft("h", 5, "", [fine], "")) is None


async def test_an_announcement_of_exactly_2000_characters_publishes(lab: Lab) -> None:
    """Discord accepts 2 000 characters, so an announcement of exactly that length is posted."""
    _too_long(lab, extra=0)

    done = await _published_via_publier(lab, lab.announce)

    posted = lab.announce.last_message
    assert posted is not None
    assert len(posted.content) == 2000
    assert not done.response.content.startswith("L'annonce fait")


@pytest.mark.parametrize(
    ("who", "may"),
    [("respo", True), ("admin", True), ("codir", True), ("temp", True), ("president", False), ("plain", False)],
    ids=["respo-formation", "administrateur", "codir", "admin-temp", "president", "plain-member"],
)
async def test_who_may_use_the_brouillon(lab: Lab, who: str, may: bool) -> None:
    """Respo Formation, Administrateur, CoDir and Admin -temp- open the brouillon; Président and a plain member are refused."""
    result = await lab.brouillon(getattr(lab, who))

    assert result.ephemeral
    if may:
        assert result.response.embeds[0].title.startswith("📝 Brouillon de la semaine")
    else:
        assert not result.response.embeds
        assert (result.response.content or "").startswith(ErrorMessages.ROLE_REQUIRED.split("{")[0])


EVERY_DRAFT_ITEM_ID = ("fmd:add", "fmd:text", "fmd:preview", "fmd:publish", "fmd:show", "fmd:pub-ch", "fmd:pub-go:5")
"""One custom ID of each button and menu of the brouillon."""


@pytest.mark.parametrize("custom_id", EVERY_DRAFT_ITEM_ID, ids=lambda custom_id: custom_id.replace(":", "-"))
async def test_every_brouillon_item_refuses_a_member_who_lost_the_role(lab: Lab, custom_id: str) -> None:
    """A member holding no formation role is refused on each item of the brouillon, and the draft stays as it was."""
    _stage(lab, _three(lab))
    before = formations.get_draft(GUILD_ID)

    interaction = await _forged(lab, custom_id, roles=(), items=DRAFT_ITEMS)

    interaction.response.send_message.assert_awaited_once()
    assert interaction.response.send_message.await_args.args[0] == RIGHT_LOST_MESSAGE
    assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True
    interaction.response.edit_message.assert_not_awaited()
    interaction.response.send_modal.assert_not_awaited()
    assert formations.get_draft(GUILD_ID) == before
    assert lab.announce.last_message is None


@pytest.mark.parametrize("custom_id", EVERY_DRAFT_ITEM_ID, ids=lambda custom_id: custom_id.replace(":", "-"))
async def test_every_brouillon_item_refuses_a_server_no_longer_served(lab: Lab, custom_id: str) -> None:
    """On a server that left the bot's list each item of the brouillon is refused before it reads the draft."""
    _stage(lab, _three(lab))
    before = formations.get_draft(GUILD_ID)
    bot: Any = lab.env.bot
    bot.guild_access = GuildAccessPolicy(allowed=frozenset({OTHER_GUILD_ID}))

    interaction = await _forged(lab, custom_id, items=DRAFT_ITEMS)

    interaction.response.send_message.assert_awaited_once()
    assert interaction.response.send_message.await_args.args[0] == NOT_SERVED
    assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True
    interaction.response.edit_message.assert_not_awaited()
    interaction.response.send_modal.assert_not_awaited()
    assert formations.get_draft(GUILD_ID) == before


async def test_the_text_form_of_a_member_who_lost_the_role_stores_nothing(lab: Lab) -> None:
    """A form left open while the role is taken away is refused at submit and the draft stays as it was."""
    _stage(lab, _three(lab))
    shown = await lab.respo.click((await lab.brouillon(lab.respo)).response, custom_id="fmd:text")
    before = formations.get_draft(GUILD_ID)
    lab.lose_roles(lab.respo)
    await asyncio.sleep(0)

    submitted = await lab.respo.submit_modal(shown, _form(shown, {"Intro": "Autre", "Rôle à notifier": [lab.notified]}))

    assert submitted.ephemeral
    assert submitted.response.content == RIGHT_LOST_MESSAGE
    assert formations.get_draft(GUILD_ID) == before


async def test_the_text_form_on_a_server_no_longer_served_stores_nothing(lab: Lab) -> None:
    """A form left open on a server that left the bot's list is refused at submit and stores nothing."""
    _stage(lab, _three(lab))
    shown = await lab.respo.click((await lab.brouillon(lab.respo)).response, custom_id="fmd:text")
    before = formations.get_draft(GUILD_ID)
    bot: Any = lab.env.bot
    bot.guild_access = GuildAccessPolicy(allowed=frozenset({OTHER_GUILD_ID}))

    submitted = await lab.respo.submit_modal(shown, _form(shown, {"Intro": "Autre", "Rôle à notifier": [lab.notified]}))

    assert submitted.ephemeral
    assert submitted.response.content == NOT_SERVED
    assert formations.get_draft(GUILD_ID) == before


async def test_publier_in_a_channel_that_is_gone_is_refused(lab: Lab) -> None:
    """A Publier button holding a channel the server no longer has says so and posts nothing."""
    _stage(lab, _three(lab))

    interaction = await _forged(lab, "fmd:pub-go:123456789", items=DRAFT_ITEMS)

    interaction.response.send_message.assert_awaited_once()
    assert (
        interaction.response.send_message.await_args.args[0]
        == "Ce salon n'existe plus : choisis-en un autre. Rien n'a été publié."
    )
    assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True
    assert formations.current_published(GUILD_ID) is None


async def test_the_brouillon_buttons_work_after_a_restart(started_bot: Any) -> None:
    """A second bot on the same database answers the clicks on a brouillon the first bot sent, from the database alone."""
    draft = FmMessageDraft(
        header="# [FORMATIONS] :loudspeaker:", role_id=77, intro="Salut", fms=[_session("🧡", "Soudure", days=2)], end="Fin"
    )
    formations.save_draft(GUILD_ID, draft)
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
    text = await click("fmd:text")
    preview = await click("fmd:preview")
    publish = await click("fmd:publish")
    shown = await click("fmd:show")

    assert text.response.send_modal.await_args.args[0].title == "Intro et conclusion"
    assert preview.response.send_message.await_args.args[0] == render_message(draft)
    assert publish.response.edit_message.await_args.kwargs["content"] == "**Où publier ?** Choisis le salon, puis **Publier**."
    assert shown.response.edit_message.await_args.kwargs["embed"].title == "📝 Brouillon de la semaine · 1 session"

    await started_bot.remove_cog("FormationManagement")

    assert not set(DRAFT_ITEMS) & set(store._dynamic_items.values())


@pytest.mark.parametrize(
    ("who", "listed"),
    [("respo", True), ("admin", True), ("codir", True), ("temp", True), ("president", False), ("plain", False)],
    ids=["respo-formation", "administrateur", "codir", "admin-temp", "president", "plain-member"],
)
async def test_fm_brouillon_is_listed_for_the_formation_roles(lab: Lab, who: str, listed: bool) -> None:
    """The help lists `/fm brouillon` for the four roles that may run it and for nobody else."""
    text = await _help_text(getattr(lab, who), lab)

    assert ("/fm brouillon" in text) is listed


async def test_the_text_form_keeps_the_header_the_draft_holds(lab: Lab) -> None:
    """Sending the intro form leaves a header the draft already has as it is."""
    draft = _stage(lab, _three(lab))
    formations.save_draft(GUILD_ID, replace(draft, header="# Mon en-tête"))
    shown = await lab.respo.click((await lab.brouillon(lab.respo)).response, custom_id="fmd:text")

    await lab.respo.submit_modal(shown, _form(shown, {"Intro": "Après", "Rôle à notifier": [lab.notified]}))

    stored = formations.get_draft(GUILD_ID)
    assert stored is not None
    assert stored.header == "# Mon en-tête"


async def test_apercu_shows_a_role_the_server_lost_as_publier_posts_it(lab: Lab) -> None:
    """A role that no longer exists is no mention in Aperçu, as it is none in the post."""
    draft = _stage(lab, _three(lab))
    formations.save_draft(GUILD_ID, replace(draft, role_id=999_999))
    listing = await lab.brouillon(lab.respo)

    preview = await lab.respo.click(listing.response, custom_id="fmd:preview")

    assert "<@&999999>" not in preview.response.content
    assert preview.response.content == render_message(replace(draft, role_id=0))


async def test_publier_pings_the_trainers_but_nobody_typed_in_the_intro(lab: Lab) -> None:
    """A user mention typed in the intro is posted as text and pings nobody; the sessions' trainers are pinged."""
    _stage(lab, _three(lab), intro=f"Salut <@{lab.respo.id}> et <@{lab.admin.id}>")

    await _published_via_publier(lab, lab.announce)

    posted = lab.announce.last_message
    assert posted is not None
    assert f"<@{lab.respo.id}>" in posted.content
    sent = lab.env.backend.get_message(lab.announce.id, posted.id)
    assert sent.ping_user_ids == [lab.plain.id]
    assert sent.ping_role_ids == [lab.notified.id]


async def test_publier_logs_the_channel_and_the_sessions_by_id_only(
    lab: Lab, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The lines of a publication, a failed reaction included, name the channel by ID, never its name or a session's."""
    caplog.set_level("DEBUG")
    sessions = _three(lab)
    _stage(lab, sessions)

    async def refuse(_message: discord.Message, _emoji: Any) -> None:
        raise discord.HTTPException(MagicMock(status=500, reason="boom"), "boom")

    monkeypatch.setattr(discord.Message, "add_reaction", refuse)

    await _published_via_publier(lab, lab.announce)

    assert "failed to add reaction" in caplog.text
    assert all(fm.name not in caplog.text for fm in sessions)
    assert str(lab.announce.name) not in caplog.text
    assert f"channel_id={lab.announce.id}" in caplog.text


async def test_publier_in_a_channel_of_another_server_is_refused(lab: Lab) -> None:
    """A Publier button forged with a channel of another server says the channel is gone and posts nothing."""
    other = lab.env.create_guild(id=OTHER_GUILD_ID).create_text_channel("annonces")
    _stage(lab, _three(lab))
    interaction = make_interaction(user_id=lab.respo.id, guild_id=GUILD_ID)
    interaction.client = lab.env.bot
    interaction.guild = _guild(lab)
    interaction.user.roles = [make_role(RoleNames.TRAININGS_MANAGER)]
    interaction.message = MagicMock()
    match = DRAFT_ITEMS[2].__discord_ui_compiled_template__.fullmatch(f"fmd:pub-go:{other.id}")
    assert match is not None
    item = await DRAFT_ITEMS[2].from_custom_id(interaction, MagicMock(), match)  # pyright: ignore[reportArgumentType]

    await item.callback(interaction)

    interaction.response.send_message.assert_awaited_once()
    assert (
        interaction.response.send_message.await_args.args[0]
        == "Ce salon n'existe plus : choisis-en un autre. Rien n'a été publié."
    )
    assert other.last_message is None
    assert formations.current_published(GUILD_ID) is None


async def test_publier_on_a_draft_already_posted_is_refused_at_the_first_click(lab: Lab) -> None:
    """Right after a post, Publier on the unchanged brouillon refuses before asking where, and posts nothing."""
    _stage(lab, _three(lab))
    await _published_via_publier(lab, lab.announce)

    listing = await lab.brouillon(lab.respo)
    asked = await lab.respo.click(listing.response, custom_id="fmd:publish")

    assert asked.ephemeral
    assert asked.response.content == (
        f"Cette annonce est déjà publiée dans <#{lab.announce.id}> : change le brouillon pour en publier une autre. "
        "Rien n'a été publié."
    )
    assert len(lab.announce.history()) == 1


@pytest.mark.parametrize("change", ["seats", "start"])
async def test_a_draft_changed_only_in_a_session_seats_or_start_is_a_new_announcement(lab: Lab, change: str) -> None:
    """Changing a session's seats, or only its start, makes the draft new: Publier posts it again."""
    draft = _stage(lab, _three(lab))
    await _published_via_publier(lab, lab.announce)
    first = draft.fms[0]
    moved = (
        replace(first, seats=first.seats + 1)
        if change == "seats"
        else replace(first, start_iso=(first.start_dt + timedelta(hours=1)).isoformat())
    )
    formations.save_draft(GUILD_ID, replace(draft, fms=[moved, *draft.fms[1:]]))

    await _published_via_publier(lab, lab.announce)

    assert len(lab.announce.history()) == 2


# region ====== adding a session from a template ======

PICKER = (
    "**Quelle formation ?** Choisis un modèle, ou une session libre.\n"
    "-# Plus rapide au clavier : `/fm ajouter modele:` puis les premières lettres."
)
NOT_ADDED = "Rien n'a été ajouté."


def _day(days: int = 3) -> date:
    """The date ``days`` from now, in Paris."""
    return (datetime.now(PARIS_TZ) + timedelta(days=days)).date()


def _jjmm(days: int = 3) -> str:
    """The date ``days`` from now as a member types it, ``JJ/MM``."""
    return _day(days).strftime("%d/%m")


def _at(days: int = 3, hour: time = time(18, 30)) -> datetime:
    """The start ``days`` from now at ``hour``, in Paris."""
    return datetime.combine(_day(days), hour, tzinfo=PARIS_TZ)


def _soudure(**changes: Any) -> Any:
    """Store the Soudure template of five seats, one hour, with a description, that is not excusable."""
    fields = TemplateFields("Soudure", "1 h", 5, "Apportez vos lunettes.", False)
    return catalogue.create_template(GUILD_ID, replace(fields, **changes))


def _drafted() -> list[Formation]:
    """The sessions the stored draft holds."""
    draft = formations.get_draft(GUILD_ID)
    return draft.fms if draft else []


async def _picker(lab: Lab, who: Any = None) -> Any:
    """Open the brouillon as a member and click Ajouter une session; return the template picker."""
    who = who or lab.respo
    listing = await lab.brouillon(who)
    return await who.click(listing.response, custom_id="fmd:add")


async def _pick(lab: Lab, template: Any, who: Any = None) -> Any:
    """Choose a template in the picker and return the form that opens."""
    who = who or lab.respo
    picker = await _picker(lab, who)
    return await who.select(picker.response, [str(template.id)], custom_id="ses:pick:0")


def _filled(lab: Lab, shown: Any, **given: str | list[Any]) -> dict[str, str | list[str]]:
    """Fill a session form with the plain member as trainer, three days from now at 18:30, ``given`` over it."""
    usual: dict[str, Any] = {"Formateur": [lab.plain], "Date": _jjmm(), "Heure": "18:30"}
    return _form(shown, {**usual, **given})


async def _add(lab: Lab, template: Any, *, who: Any = None, **given: str | list[Any]) -> Any:
    """Add a session of a template through the picker and the form, as a member would."""
    who = who or lab.respo
    shown = await _pick(lab, template, who)
    return await who.submit_modal(shown, _filled(lab, shown, **given))


def _options(message: Any) -> list[dict[str, Any]]:
    """The options of the first menu of a message."""
    return next(c for row in message.components for c in row["components"] if c["type"] == 3)["options"]


async def test_ajouter_lists_the_templates_with_their_duration_and_seats(lab: Lab) -> None:
    """Ajouter une session answers in place with the templates by name, each with its duration, seats and excusable."""
    _soudure()
    catalogue.create_template(GUILD_ID, TemplateFields("Brodeuse", "1 h 30", 1, "", True))
    _template("Autre serveur", guild_id=OTHER_GUILD_ID)

    picker = await _picker(lab)

    assert picker.response.content == PICKER
    assert not picker.response.embeds
    (menu,) = (c for row in picker.response.components for c in row["components"] if c["type"] == 3)
    assert menu["placeholder"] == "Choisis un modèle (1/1)…"
    assert [(o["label"], o["description"]) for o in menu["options"]] == [
        ("Brodeuse", "1 h 30 · 1 place · excusable"),
        ("Soudure", "1 h · 5 places"),
    ]
    assert menu["custom_id"] == "ses:pick:0"
    assert _buttons(picker.response)["Modèles 1 à 2 sur 2"]["disabled"]


async def test_the_template_menu_pages_past_twenty_four(lab: Lab) -> None:
    """Thirty templates are 24 in the menu, then 6 on the next page, which the page buttons reach and leave."""
    for number in range(30):
        _template(f"Formation {number:02}")

    first = await _picker(lab)

    assert PICKS_PER_PAGE == 24
    assert [o["label"] for o in _options(first.response)] == [f"Formation {n:02}" for n in range(24)]
    (menu,) = (c for row in first.response.components for c in row["components"] if c["type"] == 3)
    assert menu["placeholder"] == "Choisis un modèle (1/2)…"
    buttons = _buttons(first.response)
    assert buttons["◀ Précédent"]["disabled"]
    assert buttons["Modèles 1 à 24 sur 30"]["disabled"]
    assert not buttons["Suivant ▶"]["disabled"]

    second = await lab.respo.click(first.response, custom_id=buttons["Suivant ▶"]["custom_id"])

    assert [o["label"] for o in _options(second.response)] == [f"Formation {n:02}" for n in range(24, 30)]
    (menu,) = (c for row in second.response.components for c in row["components"] if c["type"] == 3)
    assert menu["placeholder"] == "Choisis un modèle (2/2)…"
    buttons = _buttons(second.response)
    assert buttons["Modèles 25 à 30 sur 30"]["disabled"]
    assert buttons["Suivant ▶"]["disabled"]

    shown = await lab.respo.select(second.response, [str(_stored()[29].id)], custom_id="ses:pick:1")
    assert shown.modal["title"] == "Formation 29"

    back = await lab.respo.click(second.response, custom_id=buttons["◀ Précédent"]["custom_id"])

    assert len(_options(back.response)) == 24


async def test_no_template_says_who_creates_them(lab: Lab) -> None:
    """With no template the picker says who creates them and offers no menu to pick from."""
    picker = await _picker(lab)

    assert picker.response.content == (
        f"{PICKER}\n-# Aucun modèle pour l'instant : un Respo Formation ou un Administrateur les crée avec /fm modeles."
    )
    assert not [c for row in picker.response.components for c in row["components"] if c["type"] == 3]


async def test_the_session_form_is_prefilled_from_the_template(lab: Lab) -> None:
    """The form is titled with the training, asks the trainer, the date and the hour, and holds the template's seats."""
    template = _soudure()

    shown = await _pick(lab, template)

    assert shown.modal["title"] == "Soudure"
    (trainer,) = (i for i in shown.modal["components"] if i["type"] == 18 and i["label"] == "Formateur")
    assert trainer["component"]["type"] == 5
    assert trainer["component"]["placeholder"] == "Choisis le formateur"
    assert not trainer["component"].get("default_values")
    assert _values(shown) == {"Date": None, "Heure": None, "Places": "5", "Description": "Apportez vos lunettes."}
    placeholders = {c["label"]: c.get("placeholder") for item in shown.modal["components"] for c in item.get("components", [])}
    assert placeholders["Date"] == "JJ/MM"
    assert placeholders["Heure"] == "HH:MM"
    description = next(
        c for item in shown.modal["components"] for c in item.get("components", []) if c["label"] == "Description"
    )
    assert (description["style"], description["required"], description["max_length"]) == (2, False, 500)


async def test_a_sent_form_adds_a_session_with_a_new_emoji_and_keeps_the_template(lab: Lab) -> None:
    """A sent form puts a session in the draft with the next free emoji, its own seats, and leaves the template as it was."""
    template = _soudure()

    done = await _add(lab, template, Places="6", Description="Gants fournis.")

    (session,) = _drafted()
    assert (session.emoji, session.name, session.trainer_mention) == (SESSION_EMOJIS[0], "Soudure", f"<@{lab.plain.id}>")
    assert (session.duration, session.seats, session.description, session.excusable) == ("1 h", 6, "Gants fournis.", False)
    assert session.start_dt == _at()
    assert catalogue.get_template(GUILD_ID, template.id) == template
    assert template.fields.seats == 5
    assert done.response.content == (
        f"✅ **Soudure** ajoutée : <t:{int(_at().timestamp())}:F> avec <@{lab.plain.id}>, 6 places, émoji 🧡.\n"
        "-# Le brouillon compte maintenant 1 session."
    )
    assert list(_buttons(done.response)) == ["🔁 Encore une session de Soudure", "➕ Autre formation", "📝 Voir le brouillon"]  # noqa: RUF001 - the sign the copy asks for
    assert _ids(done.response) == [f"ses:again:{template.id}:🧡", "fmd:add", "fmd:show"]
    sent = lab.env.backend.get_message(done.response.message.channel.id, done.response.message.id)
    assert sent.ping_user_ids == []


async def test_the_draft_stays_sorted_by_start(lab: Lab) -> None:
    """A session that starts before the ones already in the draft goes before them, whatever the order of adding."""
    template = _soudure()

    await _add(lab, template, Date=_jjmm(5))
    await _add(lab, template, Date=_jjmm(2))
    done = await _add(lab, template, Date=_jjmm(4))

    assert [fm.start_dt for fm in _drafted()] == [_at(2), _at(4), _at(5)]
    assert [fm.emoji for fm in _drafted()] == [SESSION_EMOJIS[1], SESSION_EMOJIS[2], SESSION_EMOJIS[0]]
    assert "3 sessions" in done.response.content


async def test_encore_reopens_the_form_with_the_date_and_trainer_kept(lab: Lab) -> None:
    """Encore une session opens the form on the same date and trainer, the template's seats and no second description."""
    template = _soudure()
    done = await _add(lab, template, Places="6")

    shown = await lab.respo.click(done.response, custom_id=f"ses:again:{template.id}:🧡")

    assert shown.modal["title"] == "Soudure"
    (trainer,) = (i for i in shown.modal["components"] if i["type"] == 18 and i["label"] == "Formateur")
    assert trainer["component"]["default_values"] == [{"id": lab.plain.id, "type": "user"}]
    assert _values(shown) == {"Date": _jjmm(), "Heure": None, "Places": "5", "Description": None}

    again = await lab.respo.submit_modal(shown, _filled(lab, shown, Heure="19:30"))

    assert [(fm.emoji, fm.start_dt) for fm in _drafted()] == [
        (SESSION_EMOJIS[0], _at()),
        (SESSION_EMOJIS[1], _at(3, time(19, 30))),
    ]
    assert "2 sessions" in again.response.content
    assert f"ses:again:{template.id}:💛" in _ids(again.response)


async def test_encore_with_the_session_gone_opens_the_template_values_only(lab: Lab) -> None:
    """When the session Encore came from was removed, the form opens with what the template holds and nothing more."""
    template = _soudure()
    done = await _add(lab, template)
    draft = formations.get_draft(GUILD_ID)
    assert draft is not None
    formations.save_draft(GUILD_ID, replace(draft, fms=[]))

    shown = await lab.respo.click(done.response, custom_id=f"ses:again:{template.id}:🧡")

    (trainer,) = (i for i in shown.modal["components"] if i["type"] == 18 and i["label"] == "Formateur")
    assert not trainer["component"].get("default_values")
    assert _values(shown) == {"Date": None, "Heure": None, "Places": "5", "Description": "Apportez vos lunettes."}


async def test_two_forms_sent_in_turn_get_two_emojis(lab: Lab) -> None:
    """The emoji is chosen when a form is sent, so two forms opened together do not end with the same one."""
    template = _soudure()
    first = await _pick(lab, template)
    second = await _pick(lab, template, lab.admin)

    one = await lab.respo.submit_modal(first, _filled(lab, first))
    two = await lab.admin.submit_modal(second, _filled(lab, second, Heure="19:30"))

    assert [fm.emoji for fm in _drafted()] == [SESSION_EMOJIS[0], SESSION_EMOJIS[1]]
    assert "émoji 🧡" in one.response.content
    assert "émoji 💛" in two.response.content


async def test_an_emoji_set_by_fm_add_is_never_taken_again(lab: Lab) -> None:
    """Emojis other forms put in the draft are skipped, those of the pool and those outside it alike."""
    template = _soudure()
    _stage(lab, [_session("💛", "Brodeuse", days=2), _session("🔧", "Résine", days=3), _session("💚", "Laser", days=4)])

    done = await _add(lab, template)

    assert "émoji 🧡" in done.response.content
    again = await _add(lab, template, Heure="19:30")
    assert "émoji 💙" in again.response.content


async def test_adding_a_session_keeps_the_rest_of_the_draft(lab: Lab) -> None:
    """A session added from a template changes only the sessions: header, role, intro, conclusion and link stay."""
    staged = replace(
        _stage(lab, [_session("💛", "Brodeuse", days=2)], intro="Au programme", end="À bientôt"),
        header="# Cette semaine",
        request_forms_url="https://example.org/propose",
    )
    formations.save_draft(GUILD_ID, staged)

    await _add(lab, _soudure())

    stored = formations.get_draft(GUILD_ID)
    assert stored is not None
    assert replace(stored, fms=[]) == replace(staged, fms=[])
    assert (stored.header, stored.role_id, stored.intro, stored.end, stored.request_forms_url) == (
        "# Cette semaine",
        lab.notified.id,
        "Au programme",
        "À bientôt",
        "https://example.org/propose",
    )
    assert [fm.name for fm in stored.fms] == ["Brodeuse", "Soudure"]


async def test_an_fm_add_form_open_while_a_template_session_takes_its_emoji_is_refused(lab: Lab) -> None:
    """`/fm add` checks the emoji when it runs; the form sent later checks it again against the draft as stored."""
    await lab.show()
    opened = await lab.respo.slash(
        lab.commands,
        "fm add",
        emoji=SESSION_EMOJIS[0],
        trainer=lab.plain,
        date=_jjmm() + f"/{_day(3).year}",
        hour="18:30",
        duration="2 h",
        seats=4,
    )
    assert opened.modal is not None
    await _add(lab, _soudure())

    refused = await lab.respo.submit_modal(opened, _form(opened, {"Nom de la formation": "Arduino"}))

    assert refused.response.content == ErrorMessages.EMOJI_ALREADY_USED
    assert [(fm.emoji, fm.name) for fm in _drafted()] == [(SESSION_EMOJIS[0], "Soudure")]


OUTSIDE_THE_POOL = (
    "🔧",
    "🔨",
    "🪛",
    "🧰",
    "⚙️",
    "🔩",
    "🪚",
    "🧲",
    "🔬",
    "🔭",
    "💡",
    "🔌",
    "🔋",
    "🧪",
    "🧵",
    "🧶",
    "🪡",
    "🎨",
    "🖌️",
    "🪵",
)
"""Twenty emojis none of which the bot gives, as `/fm add` forms may have put in a draft."""
FULL = "Le brouillon compte déjà 20 sessions : Discord n'accepte pas plus de 20 réactions sur un message."


async def test_the_twenty_first_session_is_refused(lab: Lab) -> None:
    """The twentieth session is added, the twenty-first is refused with the reason and the draft stays as it was."""
    template = _soudure()
    sessions = [
        _session(emoji, f"Séance {number}", days=2 + number / 100) for number, emoji in enumerate(OUTSIDE_THE_POOL[:19])
    ]
    _stage(lab, sessions)

    twentieth = await _add(lab, template)
    before = formations.get_draft(GUILD_ID)
    refused = await _add(lab, template, Heure="19:30")

    assert "20 sessions" in twentieth.response.content
    assert len(_drafted()) == 20
    assert refused.ephemeral
    assert refused.response.content == f"{FULL} {NOT_ADDED}"
    assert formations.get_draft(GUILD_ID) == before


async def test_a_draft_that_holds_every_emoji_of_the_pool_refuses_too(lab: Lab) -> None:
    """Twenty sessions holding the pool's twenty emojis leave none to give: the next one is refused for the same reason."""
    template = _soudure()
    _stage(lab, [_session(emoji, f"Séance {number}", days=2) for number, emoji in enumerate(SESSION_EMOJIS)])

    refused = await _add(lab, template)

    assert refused.response.content == f"{FULL} {NOT_ADDED}"
    assert len(_drafted()) == 20


@pytest.mark.parametrize(
    ("given", "refusal"),
    [
        ({"Date": "31/02"}, "Date illisible : écris JJ/MM, par exemple 12/10."),
        ({"Date": "12-10"}, "Date illisible : écris JJ/MM, par exemple 12/10."),
        ({"Heure": "25:00"}, "Heure illisible : écris HH:MM, par exemple 18:30."),
        ({"Heure": "18h70"}, "Heure illisible : écris HH:MM, par exemple 18:30."),
        ({"Date": "{past_date}", "Heure": "{past_hour}"}, ErrorMessages.PAST_DATETIME),
        ({"Places": "0"}, "Places : un nombre entier de 1 à 500."),
        ({"Places": "5 places"}, "Places : un nombre entier de 1 à 500."),
    ],
    ids=["no-31-feb", "dash", "hour-25", "minute-70", "past", "seats-0", "seats-with-a-word"],
)
async def test_a_bad_value_is_refused_and_nothing_is_stored(lab: Lab, given: dict[str, str], refusal: str) -> None:
    """A date, hour or seats that cannot be read, or a start already gone, is refused privately with the reason."""
    template = _soudure()
    _stage(lab, [_session("💛", "Brodeuse", days=2)])
    before = formations.get_draft(GUILD_ID)
    hour_ago = datetime.now(PARIS_TZ) - timedelta(hours=1)
    given = {
        key: value.format(past_date=hour_ago.strftime("%d/%m/%Y"), past_hour=hour_ago.strftime("%H:%M"))
        for key, value in given.items()
    }

    refused = await _add(lab, template, **given)

    assert refused.ephemeral
    assert refused.response.content == f"{refusal} {NOT_ADDED}"
    assert not refused.response.embeds
    assert formations.get_draft(GUILD_ID) == before
    assert _stored() == [template]


async def test_a_trainer_who_is_not_a_member_is_refused(lab: Lab) -> None:
    """A user who is not on the server cannot be the trainer: the choice is refused and nothing is stored."""
    template = _soudure()
    stranger = lab.env.create_user("passant")

    refused = await _add(lab, template, Formateur=[stranger])

    assert refused.ephemeral
    assert refused.response.content == f"Choisis un formateur parmi les membres du serveur. {NOT_ADDED}"
    assert _drafted() == []


async def test_a_template_deleted_meanwhile_is_refused(lab: Lab) -> None:
    """A form sent after its template was deleted says the template is gone and adds nothing."""
    template = _soudure()
    shown = await _pick(lab, template)
    assert catalogue.delete_template(GUILD_ID, template.id, expected=template.fields)

    refused = await lab.respo.submit_modal(shown, _filled(lab, shown))

    assert refused.ephemeral
    assert refused.response.content == f"{GONE} {NOT_ADDED}"
    assert _drafted() == []


async def test_a_template_changed_meanwhile_gives_the_session_its_new_name_and_duration(lab: Lab) -> None:
    """The session takes the name and duration the template holds when the form is sent."""
    template = _soudure()
    shown = await _pick(lab, template)
    assert catalogue.update_template(
        GUILD_ID, template.id, replace(template.fields, name="Soudure étain", duration="2 h"), expected=template.fields
    )

    await lab.respo.submit_modal(shown, _filled(lab, shown))

    (session,) = _drafted()
    assert (session.name, session.duration) == ("Soudure étain", "2 h")


async def test_admin_temp_adds_a_session_but_cannot_change_a_template(lab: Lab) -> None:
    """Admin -temp- picks a template and adds a session, yet `/fm modeles` and its buttons refuse it; templates stay."""
    template = _soudure()

    done = await _add(lab, template, who=lab.temp)
    refused = await lab.modeles(lab.temp)
    forged = [await _forged(lab, f"cat:{kind}", roles=(RoleNames.ADMIN_TEMP,)) for kind in ("new", "edit", "del")]

    assert "ajoutée" in done.response.content
    assert len(_drafted()) == 1
    assert (refused.response.content or "").startswith(ErrorMessages.ROLE_REQUIRED.split("{")[0])
    for interaction in forged:
        interaction.response.send_modal.assert_not_awaited()
        assert interaction.response.send_message.await_args.args[0] == RIGHT_LOST_MESSAGE
    assert _stored() == [template]


@pytest.mark.parametrize(
    "who", ["respo", "admin", "codir", "temp"], ids=["respo-formation", "administrateur", "codir", "admin-temp"]
)
async def test_who_may_add_a_session(lab: Lab, who: str) -> None:
    """The four formation roles reach the picker from the brouillon and, once the form is sent, add a session."""
    template = _soudure()

    done = await _add(lab, template, who=getattr(lab, who))

    assert "ajoutée" in done.response.content
    assert len(_drafted()) == 1


EVERY_SESSION_ITEM_ID = ("ses:pick:0", "ses:pnav:1", "ses:again:{template}:🧡")
"""One custom ID of each session item; ``{template}`` is the ID of a stored template."""


@pytest.mark.parametrize(
    "custom_id", EVERY_SESSION_ITEM_ID, ids=lambda custom_id: custom_id.split("{")[0].strip(":").replace(":", "-")
)
async def test_every_session_item_refuses_a_member_who_lost_the_role(lab: Lab, custom_id: str) -> None:
    """A member holding no formation role is refused on each session item; the draft and the template stay as they were."""
    template = _soudure()
    _stage(lab, _three(lab))
    before = formations.get_draft(GUILD_ID)

    interaction = await _forged(lab, custom_id.format(template=template.id), roles=(), items=SESSION_ITEMS)

    interaction.response.send_message.assert_awaited_once()
    assert interaction.response.send_message.await_args.args[0] == RIGHT_LOST_MESSAGE
    assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True
    interaction.response.edit_message.assert_not_awaited()
    interaction.response.send_modal.assert_not_awaited()
    assert formations.get_draft(GUILD_ID) == before
    assert _stored() == [template]


@pytest.mark.parametrize(
    "custom_id", EVERY_SESSION_ITEM_ID, ids=lambda custom_id: custom_id.split("{")[0].strip(":").replace(":", "-")
)
async def test_every_session_item_refuses_a_server_no_longer_served(lab: Lab, custom_id: str) -> None:
    """On a server that left the bot's list each session item is refused before it reads anything."""
    template = _soudure()
    _stage(lab, _three(lab))
    before = formations.get_draft(GUILD_ID)
    bot: Any = lab.env.bot
    bot.guild_access = GuildAccessPolicy(allowed=frozenset({OTHER_GUILD_ID}))

    interaction = await _forged(lab, custom_id.format(template=template.id), items=SESSION_ITEMS)

    interaction.response.send_message.assert_awaited_once()
    assert interaction.response.send_message.await_args.args[0] == "Ce bot n'est pas disponible ici. Rien n'a été modifié."
    interaction.response.edit_message.assert_not_awaited()
    interaction.response.send_modal.assert_not_awaited()
    assert formations.get_draft(GUILD_ID) == before


async def test_a_session_form_of_a_member_who_lost_the_role_stores_nothing(lab: Lab) -> None:
    """A form left open while the role is taken away is refused at submit and the draft stays as it was."""
    template = _soudure()
    shown = await _pick(lab, template)
    lab.lose_roles(lab.respo)
    await asyncio.sleep(0)

    refused = await lab.respo.submit_modal(shown, _filled(lab, shown))

    assert refused.ephemeral
    assert refused.response.content == RIGHT_LOST_MESSAGE
    assert _drafted() == []


async def test_a_session_form_on_a_server_no_longer_served_stores_nothing(lab: Lab) -> None:
    """A form left open on a server that left the bot's list is refused at submit and stores nothing."""
    template = _soudure()
    shown = await _pick(lab, template)
    bot: Any = lab.env.bot
    bot.guild_access = GuildAccessPolicy(allowed=frozenset({OTHER_GUILD_ID}))

    refused = await lab.respo.submit_modal(shown, _filled(lab, shown))

    assert refused.ephemeral
    assert refused.response.content == NOT_SERVED
    assert _drafted() == []


async def test_a_forged_menu_choice_of_another_servers_template_opens_no_session_form(lab: Lab) -> None:
    """A template of another server chosen in the picker is answered as missing, with no form opened and no session added."""
    theirs = _template("Soudure", guild_id=OTHER_GUILD_ID)
    interaction = make_interaction(user_id=lab.respo.id, guild_id=GUILD_ID)
    interaction.client = lab.env.bot
    interaction.user.roles = [make_role(RoleNames.TRAININGS_MANAGER)]
    interaction.message = MagicMock()
    item_class = SESSION_ITEMS[0]
    match = item_class.__discord_ui_compiled_template__.fullmatch("ses:pick:0")
    item = await item_class.from_custom_id(interaction, MagicMock(), match)  # pyright: ignore[reportArgumentType]
    item.item._values = [str(theirs.id)]  # pyright: ignore[reportAttributeAccessIssue]

    await item.callback(interaction)

    interaction.response.send_modal.assert_not_awaited()
    assert interaction.response.send_message.await_args.args[0] == f"{GONE} {NOT_ADDED}"
    assert _drafted() == []


async def test_encore_of_another_servers_template_reads_as_missing(lab: Lab) -> None:
    """An Encore button built with a template of another server answers as missing and opens no form."""
    theirs = _template("Soudure", guild_id=OTHER_GUILD_ID)

    interaction = await _forged(lab, f"ses:again:{theirs.id}:🧡", items=SESSION_ITEMS)

    interaction.response.send_modal.assert_not_awaited()
    assert interaction.response.send_message.await_args.args[0] == f"{GONE} {NOT_ADDED}"


async def test_encore_works_after_a_restart(started_bot: Any) -> None:
    """A second bot on the same database opens the form of an Encore button the first bot sent, from the database alone."""
    template = catalogue.create_template(GUILD_ID, TemplateFields("Soudure", "1 h", 5, "", False))
    session = replace(_session("🧡", "Soudure", days=3), trainer_mention="<@4242>")
    formations.save_draft(GUILD_ID, FmMessageDraft(header="h", role_id=1, intro="i", fms=[session], end="e"))
    store = started_bot._connection._view_store
    custom_id = f"ses:again:{template.id}:🧡"
    interaction = make_interaction(user_id=7, guild_id=GUILD_ID)
    interaction.client = started_bot
    interaction.user.roles = [make_role(RoleNames.CODIR)]
    interaction.message = MagicMock()
    pattern, item_class = next((p, c) for p, c in store._dynamic_items.items() if p.fullmatch(custom_id))
    item = await item_class.from_custom_id(interaction, MagicMock(), pattern.fullmatch(custom_id))
    assert set(SESSION_ITEMS) <= set(store._dynamic_items.values())
    assert await item.interaction_check(interaction)

    await item.callback(interaction)

    form = interaction.response.send_modal.await_args.args[0]
    assert form.title == "Soudure"
    assert form.date_input.default == session.start_dt.strftime("%d/%m")
    assert [value.id for value in form.trainer_select.default_values] == [4242]
    await started_bot.remove_cog("FormationManagement")
    assert not set(SESSION_ITEMS) & set(store._dynamic_items.values())
