"""The shape of the help page: domains, role summary, escaping, and the split into several messages."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from discord import Embed, Permissions, app_commands
from mockups.screens import declared_commands
import pytest

from fablabot.helpers.access import AnyRole, Caller, access
from fablabot.helpers.constants import RoleNames
from fablabot.helpers.help_page import DOMAINS, FIELD_VALUE_CHARS, FIELDS, MESSAGE_CHARS, build_help, paginate
from fablabot.helpers.i18n import catalog

if TYPE_CHECKING:
    from collections.abc import Sequence

ADMIN = (RoleNames.ADMIN,)
ZERO = Permissions.none()
SERVER_ROLES = tuple(value for key, value in vars(RoleNames).items() if key.isupper())


class _ByPermission:
    """A rule that opens a command to a member who can manage messages, whatever their roles."""

    def admits(self, caller: Caller) -> bool:
        """Admit a caller who can manage messages."""
        return caller.permissions.manage_messages


def _caller(*roles: str, permissions: Permissions = ZERO) -> Caller:
    """Build a caller holding ``roles`` (highest first), on a server that has every role the bot knows."""
    return Caller(roles=roles, permissions=permissions, server_roles=SERVER_ROLES)


def _commands(rules: dict[str, Any]) -> list[app_commands.Command[Any, ..., Any]]:
    """Build fm commands carrying the given rules, by name."""
    group = app_commands.Group(name="fm", description="x")

    async def callback(interaction: Any) -> None:
        """Do nothing."""

    return [
        app_commands.Command(name=name, description="x", callback=callback, parent=group, extras=access(rule))
        for name, rule in rules.items()
    ]


def _fields(embeds: Sequence[Embed]) -> list[tuple[str, str]]:
    """Every (name, value) of the embeds, in order."""
    return [(field.name or "", field.value or "") for embed in embeds for field in embed.fields]


def test_domains_follow_the_reply_order() -> None:
    """The fields come in the order of DOMAINS."""
    texts = catalog().texts("en")
    embeds = build_help(declared_commands(), _caller(RoleNames.ADMIN), texts)
    names = [name for name, _ in _fields(embeds)]
    order = [texts.get(f"help.domain.{domain}") for domain, _ in DOMAINS]
    assert names == [name for name in order if name in names]
    assert {"Text channels", "Trainings", "Bot messages", "For everyone"} <= set(names)


def test_empty_domain_is_left_out() -> None:
    """A trainer sees no field for channels, messages or logs."""
    embeds = build_help(declared_commands(), _caller(RoleNames.TRAININGS_MANAGER), catalog().texts("en"))
    assert [name for name, _ in _fields(embeds)] == ["Trainings", "For everyone"]


def test_summary_names_the_opening_roles_highest_first() -> None:
    """An admin who is also Bureau gets both roles named, highest first, with the count of staff commands."""
    embeds = build_help(
        declared_commands(), _caller(RoleNames.ADMIN, RoleNames.BUREAU, permissions=Permissions.all()), catalog().texts("en")
    )
    assert embeds[0].description == "Your roles **Administrateur** and **Bureau** open 38 commands."
    assert all(name != "Want to do more?" for name, _ in _fields(embeds))


@pytest.mark.parametrize(
    ("locale", "expected"),
    [("en", "Your roles **A**, **B** and **C** open 3 commands."), ("fr", "A**, **B** et **C**")],
)
def test_summary_joins_three_roles(locale: str, expected: str) -> None:
    """Three roles are joined with commas and the locale's "and"."""
    commands = _commands({"start": AnyRole({"A"}), "add": AnyRole({"B"}), "edit": AnyRole({"C"})})
    embeds = build_help(commands, _caller("A", "B", "C"), catalog().texts(locale))
    assert expected in (embeds[0].description or "")


def test_summary_singular_and_plural() -> None:
    """One listed command reads singular, several plural."""
    texts = catalog().texts("en")
    one = build_help(_commands({"start": AnyRole({"A"})}), _caller("A"), texts)
    several = build_help(_commands({"start": AnyRole({"A"}), "add": AnyRole({"A"})}), _caller("A"), texts)
    assert one[0].description == "Your role **A** opens 1 command."
    assert several[0].description == "Your role **A** opens 2 commands."


def test_summary_for_permissions_only() -> None:
    """When no single role opens a command, the summary speaks of the permissions."""
    caller = Caller(roles=("A",), permissions=Permissions(manage_messages=True), server_roles=())
    embeds = build_help(_commands({"start": _ByPermission()}), caller, catalog().texts("en"))
    assert embeds[0].description == "Your permissions on this server open 1 command."


def test_summary_with_permissions_only() -> None:
    """A member who can only manage messages is told their permissions open the one command."""
    caller = Caller(roles=(), permissions=Permissions(manage_messages=True), server_roles=())
    embeds = build_help(declared_commands(), caller, catalog().texts("fr"))
    assert embeds[0].description == "Tes permissions sur ce serveur t\u2019ouvrent 1 commande."


def test_role_names_are_escaped() -> None:
    """A role name cannot add markdown or a mention to the summary."""
    name = "**x** @everyone"
    embeds = build_help(_commands({"start": AnyRole({name})}), _caller(name), catalog().texts("en"))
    assert embeds[0].description == "Your role **\\*\\*x\\*\\* @​everyone** opens 1 command."


@pytest.mark.parametrize("locale", ["en", "fr", "de"])
def test_a_command_line_is_the_same_for_every_profile(locale: str) -> None:
    """The /fm start line does not depend on who is asking."""
    texts = catalog().texts(locale)
    lines: list[str] = []
    for roles in [(RoleNames.TRAININGS_MANAGER,), (RoleNames.ADMIN,)]:
        embeds = build_help(declared_commands(), _caller(*roles), texts)
        value = next(value for _, value in _fields(embeds) if "`/fm start`" in value)
        lines.append(next(line for line in value.split("\n") if line.startswith("`/fm start`")))
    assert lines[0] == lines[1]


def test_one_message_when_it_fits() -> None:
    """An admin's help is one message, one field per domain, with the summary as description."""
    texts = catalog().texts("en")
    embeds = build_help(declared_commands(), _caller(RoleNames.ADMIN), texts)
    names = [name for name, _ in _fields(embeds)]
    assert len(embeds) == 1
    assert len(names) == len(set(names))
    assert embeds[0].description == texts.plural("help.summary.one_role", 35, roles="**Administrateur**")


def test_long_field_continues_under_its_heading() -> None:
    """A domain longer than a field value is cut at line ends, each piece under the same heading."""
    lines = [f"line {i:03} " + "x" * 90 for i in range(30)]
    embeds = paginate("T", "D", [("Heading", lines)], "F")
    fields = _fields(embeds)
    assert len(fields) > 1
    assert {name for name, _ in fields} == {"Heading"}
    assert all(len(value) <= FIELD_VALUE_CHARS for _, value in fields)
    assert "\n".join(value for _, value in fields) == "\n".join(lines)


def test_the_limits_are_the_ones_discord_enforces() -> None:
    """The constants hold Discord's documented limits, so a test that imports them cannot follow a wrong edit."""
    assert (MESSAGE_CHARS, FIELDS, FIELD_VALUE_CHARS) == (6000, 25, 1024)


@pytest.mark.parametrize(("joined", "fields"), [(FIELD_VALUE_CHARS, 1), (FIELD_VALUE_CHARS + 1, 2)])
def test_a_field_holds_exactly_the_limit(joined: int, fields: int) -> None:
    """Two lines joining to the limit stay one field value; one character more starts another."""
    lines = ["a" * 1000, "b" * (joined - 1001)]
    assert len("\n".join(lines)) == joined
    values = [value for _, value in _fields(paginate("T", "D", [("Heading", lines)], "F"))]
    assert len(values) == fields
    assert all(len(value) <= FIELD_VALUE_CHARS for value in values)


def test_many_fields_spill_into_more_messages() -> None:
    """Thirty domains do not fit 25 fields, so a second message with no title follows."""
    embeds = paginate("T", "D", [(f"Domain {i}", ["a line"]) for i in range(30)], "F")
    assert len(embeds) == 2
    assert len(embeds[0].fields) == FIELDS
    assert embeds[1].title is None


@pytest.mark.parametrize("count", [300, *range(40, 70)])
def test_no_line_is_lost_or_cut(count: int) -> None:
    """Lines of 100 characters come out whole, once, in order, within every limit, whatever the count."""
    lines = [f"{i:03} " + "y" * 96 for i in range(count)]
    footer = "Footer " + "z" * 600
    embeds = paginate("Title", "Description", [("Domain", lines)], footer)
    assert [line for _, value in _fields(embeds) for line in value.split("\n")] == lines
    assert all(len(embed) <= MESSAGE_CHARS and len(embed.fields) <= FIELDS for embed in embeds)
    assert embeds[0].title == "Title"
    assert embeds[0].description == "Description"
    assert all(embed.title is None and embed.description is None for embed in embeds[1:])
    assert [embed.footer.text for embed in embeds] == [None] * (len(embeds) - 1) + [footer]
    assert all(embed.colour is not None for embed in embeds)
