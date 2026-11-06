"""Respo Formation and Administrateur manage the training templates with `/fm modeles`, against a simulated Discord."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import re
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

from conftest import LISTED_GUILD, OTHER_LISTED_GUILD, make_interaction, make_role
import pytest

from fablabot.helpers.constants import PARIS_TZ, ErrorMessages, RoleNames
from fablabot.helpers.guild_access import GuildAccessPolicy
from fablabot.helpers.utils import RIGHT_LOST_MESSAGE
from fablabot.models import TemplateFields
from fablabot.models.formation import FmMessageDraft, Formation, PublishedMessage
from fablabot.services import catalogue, formations
from fablabot.ui.catalogue import CATALOGUE_ITEMS, deletion_message, templates_message

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


def _form(shown: Any, by_label: dict[str, str]) -> dict[str, str | list[str]]:
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
    lab: Lab, custom_id: str, *, client: Any | None = None, roles: tuple[str, ...] = (RoleNames.TRAININGS_MANAGER,)
) -> MagicMock:
    """Send a member's click (a Respo Formation by default) on an item and return the interaction the bot answered."""
    interaction = make_interaction(user_id=lab.respo.id, guild_id=GUILD_ID)
    interaction.client = client or lab.env.bot
    interaction.user.roles = [make_role(name) for name in roles]
    interaction.message = MagicMock()
    item_class = next(cls for cls in CATALOGUE_ITEMS if cls.__discord_ui_compiled_template__.fullmatch(custom_id))
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
