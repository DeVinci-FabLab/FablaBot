"""Log lines name members by Discord ID, never by name or object repr."""

from __future__ import annotations

import ast
from datetime import datetime, timedelta
import logging
from pathlib import Path
import re
from unittest.mock import AsyncMock, MagicMock, patch

from conftest import fill_text_input, make_guild, make_interaction, make_member, make_role
from discord import Forbidden, HTTPException, Member, TextChannel, ui
import pytest

from fablabot.cogs import message_management
from fablabot.cogs.channel_management import ChannelManagement
from fablabot.cogs.message_management import MessageManagement
from fablabot.helpers.constants import PARIS_TZ, RoleNames
from fablabot.helpers.safe_discord_operations import safe_add_roles, safe_remove_roles
from fablabot.helpers.utils import log_request, send_dm_to_member
from fablabot.models.formation import FmMessageDraft, Formation, PublishedMessage
from fablabot.models.message import EasterEggTrigger
from fablabot.services import attendance, catalogue, formations
from fablabot.ui.attendance import AppelValidate
from fablabot.ui.catalogue import TemplateDelete, TemplateForm

NAME = "kevin_secret_name"


def _member(member_id: int = 4242) -> MagicMock:
    """Build a stub member whose name and repr are recognisable.

    Args:
        member_id (int): The member ID.

    Returns:
        MagicMock: The stub member.
    """
    member = MagicMock(spec=Member)
    member.id = member_id
    member.name = NAME
    member.display_name = "Kevin Secret"
    member.bot = False
    member.__str__.return_value = NAME
    return member


def test_log_request_logs_caller_and_member_options_by_id(caplog: pytest.LogCaptureFixture) -> None:
    """A Member keyword is logged as ``<key>_id`` and no name appears."""
    caplog.set_level(logging.DEBUG)
    log_request(logging.getLogger("test"), "x", make_interaction(), member=_member(), count=3)
    assert "member_id=4242" in caplog.text
    assert "count=3" in caplog.text
    assert NAME not in caplog.text
    assert "Kevin Secret" not in caplog.text


@pytest.mark.parametrize("op", [safe_add_roles, safe_remove_roles], ids=["add", "remove"])
async def test_role_change_failure_logs_member_id_not_name(caplog: pytest.LogCaptureFixture, op) -> None:
    """A refused role change names the member by ID."""
    member = _member()
    err = Forbidden(MagicMock(status=403), "x")
    member.add_roles = AsyncMock(side_effect=err)
    member.remove_roles = AsyncMock(side_effect=err)
    role = MagicMock()
    role.name = "Role"
    await op(logging.getLogger("test"), member, role)
    assert "member_id=4242" in caplog.text
    assert NAME not in caplog.text


async def test_dm_failure_logs_member_id_not_name(caplog: pytest.LogCaptureFixture) -> None:
    """A failed DM names the member by ID."""
    caplog.set_level(logging.DEBUG)
    member = _member()
    member.send = AsyncMock(side_effect=Forbidden(MagicMock(status=403), "x"))
    guild = MagicMock()
    guild.id = 1234
    await send_dm_to_member(logging.getLogger("test"), guild, member, "hi", dm_type="t")
    assert "4242" in caplog.text
    assert NAME not in caplog.text
    assert "Kevin Secret" not in caplog.text


async def test_msg_clear_refusal_logs_user_id_not_name(caplog: pytest.LogCaptureFixture) -> None:
    """The /msg clear refusal names the caller by ID."""
    caplog.set_level(logging.DEBUG)
    interaction = make_interaction()
    interaction.channel = MagicMock(spec=TextChannel)
    interaction.permissions.manage_messages = False
    await MessageManagement.msg_clear.callback(MagicMock(), interaction, messages=5)
    assert "user_id=418003123456789012" in caplog.text
    assert NAME not in caplog.text


async def test_deleted_message_debug_line_has_no_message_repr(caplog: pytest.LogCaptureFixture) -> None:
    """The debug line for a deleted message carries its ID, not its repr."""
    caplog.set_level(logging.DEBUG)
    msg = MagicMock()
    msg.id = 99
    msg.author.bot = False
    msg.author.__str__.return_value = NAME
    msg.__str__.return_value = f"message by {NAME}"
    msg.channel = MagicMock(spec=TextChannel)
    await ChannelManagement(MagicMock()).on_message_delete(msg)
    assert "99" in caplog.text
    assert NAME not in caplog.text


async def test_easter_egg_line_logs_author_id_not_name(caplog: pytest.LogCaptureFixture) -> None:
    """The easter egg line names the author by ID."""
    caplog.set_level(logging.DEBUG)
    egg = EasterEggTrigger(keywords=[re.compile("hello")], response="", probability=1.0)
    msg = MagicMock()
    msg.author = _member()
    msg.author.bot = False
    msg.content = "hello"
    msg.guild.id = 987654321
    msg.channel = MagicMock(spec=TextChannel)
    msg.channel.category.name = "General"
    msg.add_reaction = AsyncMock()
    with (
        patch.object(message_management, "EASTER_EGGS_ENABLED", new=True),
        patch.object(message_management, "EASTER_EGGS", [egg]),
    ):
        await MessageManagement(MagicMock()).on_message(msg)
    assert "author_id=4242" in caplog.text
    assert NAME not in caplog.text


async def test_msg_purge_logs_the_purged_member_as_target_and_the_caller_as_user(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The purge lines name the purged member as ``target_id`` and only the caller as ``user_id``."""
    caplog.set_level(logging.DEBUG)

    async def confirm(self, _timeout=None) -> bool:
        """Answer the confirmation view as if the caller pressed the confirm button."""
        self.confirmed = True
        return False

    async def no_history(**_kwargs):
        """Yield no message."""
        return
        yield

    monkeypatch.setattr(ui.View, "wait", confirm)
    interaction = make_interaction()
    interaction.permissions.administrator = True
    channel = MagicMock(spec=TextChannel)
    channel.name = "salon"
    channel.history = no_history
    channel.purge = AsyncMock(return_value=[MagicMock(), MagicMock()])

    await MessageManagement.msg_purge.callback(
        MessageManagement(MagicMock()),
        interaction,
        user=_member(),
        count=5,
        channel=channel,
    )

    purged = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Purged")]
    assert len(purged) == 2
    assert all("target_id=4242" in line for line in purged)
    assert [line.count("user_id=418003123456789012") for line in purged] == [0, 1]
    assert not any("user_id=4242" in line for line in purged)


async def test_msg_purge_refuses_anyone_but_a_server_administrator(monkeypatch: pytest.MonkeyPatch) -> None:
    """A caller without the administrator permission is refused before anything is counted."""

    async def confirm(self, _timeout=None) -> bool:
        """Answer the confirmation view as if the caller pressed confirm, so a wrongly open gate would purge."""
        self.confirmed = True
        return False

    monkeypatch.setattr(ui.View, "wait", confirm)
    interaction = make_interaction()
    interaction.permissions.administrator = False
    channel = MagicMock(spec=TextChannel)
    channel.purge = AsyncMock()

    await MessageManagement.msg_purge.callback(
        MessageManagement(MagicMock()),
        interaction,  # pyright: ignore[reportCallIssue]  # cog bound away
        user=_member(),
        count=5,
        channel=channel,
    )

    interaction.response.send_message.assert_awaited_once()
    assert "administrateurs" in interaction.response.send_message.await_args.args[0]
    channel.purge.assert_not_awaited()


_NAMED: frozenset[str] = frozenset(
    {"user", "member", "author", "sender", "selected_user", "members", "delivered", "failed", "modified", "already"}
)
# Names that hold a member in this code base: a name read from any of them is a person's name.
_MEMBERISH: frozenset[str] = _NAMED | {"m", "u", "target", "trainer", "participant", "responsible", "new_member"}
_NAME_ATTRS: frozenset[str] = frozenset({"name", "display_name", "global_name", "nick"})
_LOG_METHODS: frozenset[str] = frozenset({"debug", "info", "warning", "error", "exception", "critical"})
# Free text a member types, which may hold anyone's name.
_FREE_TEXT: frozenset[str] = frozenset({"reason"})


def _base_name(node: ast.expr) -> str:
    """Return the last name of an attribute chain's base: ``interaction.user`` gives ``user``."""
    if isinstance(node, ast.Attribute):
        return node.attr
    return node.id if isinstance(node, ast.Name) else ""


def _offence(node: ast.AST) -> bool:
    """Tell whether a node inside a log call writes a member, a member list, a member's name or free text."""
    if isinstance(node, ast.FormattedValue):
        value = node.value
        if isinstance(value, ast.Call) and _base_name(value.func) == "str" and value.args:
            value = value.args[0]
        return _base_name(value) in _NAMED
    if isinstance(node, ast.Attribute) and node.attr in _NAME_ATTRS:
        return _base_name(node.value) in _MEMBERISH
    return False


def _log_calls(tree: ast.AST) -> list[ast.Call]:
    """List the logger calls, and the log_request calls with their details."""
    calls: list[ast.Call] = []
    for call in ast.walk(tree):
        if not isinstance(call, ast.Call):
            continue
        func = call.func
        logs = isinstance(func, ast.Attribute) and func.attr in _LOG_METHODS and _base_name(func.value) in {"logger", "logging"}
        if logs or _base_name(func) in {"log_request", "ensure_command_context"}:
            calls.append(call)
    return calls


def test_no_log_call_in_the_source_interpolates_a_member() -> None:
    """Every log call writes members by ID: no member, member list, name, display name or typed reason."""
    sources = sorted((Path(__file__).resolve().parents[1] / "src" / "fablabot").rglob("*.py"))
    assert len(sources) > 30
    offenders: list[str] = []
    for path in sources:
        for call in _log_calls(ast.parse(path.read_text(encoding="utf-8"))):
            keys = {kw.arg for kw in call.keywords} | {
                key.value for d in ast.walk(call) if isinstance(d, ast.Dict) for key in d.keys if isinstance(key, ast.Constant)
            }
            if keys & _FREE_TEXT:
                offenders.append(f"{path.name}:{call.lineno} reason")
            offenders += [f"{path.name}:{call.lineno} {ast.unparse(n)}" for n in ast.walk(call) if _offence(n)]
    assert offenders == []


async def _validate_secret_training(log_channel: MagicMock | None, *, log_channel_id: int | None = 77) -> tuple[int, MagicMock]:
    """Store a training named ``Training Secret``, have a member named ``NAME`` validate its check, and return the check."""
    start = datetime.now(PARIS_TZ)
    training = Formation(
        emoji="A",
        name="Training Secret",
        trainer_mention="<@7>",
        start_iso=start.isoformat(),
        duration="2h",
        seats=3,
        registered_users=[{"user_id": 1, "username": NAME, "ts_iso": start.isoformat()}],
    )
    draft = FmMessageDraft(header="h", role_id=1, intro="i", fms=[training], end="e")
    formations.save_published(1234, PublishedMessage(message_id=5000, channel_id=42, message=draft))
    appel_id = attendance.open_appel(1234, 5000, 0, trainer_id=7, starts_at=start, ends_at=start + timedelta(hours=2))
    role = make_role(RoleNames.TRAININGS_MANAGER)
    respo = make_member(9, role)
    respo.send = AsyncMock()
    guild = make_guild(1234, [role], [respo], channel=log_channel)
    if log_channel is None:
        guild.get_channel = MagicMock(return_value=None)
    interaction = make_interaction(user_name=NAME)
    interaction.client.get_guild.return_value = guild

    with patch("fablabot.helpers.formation.attendance.get_log_channel_id", return_value=log_channel_id):
        await AppelValidate(appel_id).callback(interaction)

    return appel_id, respo


async def test_attendance_lines_name_members_by_id_only(caplog: pytest.LogCaptureFixture) -> None:
    """A validation by a member logs IDs and counts: neither the member's name nor the training's name appears."""
    caplog.set_level(logging.DEBUG)

    appel_id, respo = await _validate_secret_training(MagicMock(spec=TextChannel, send=AsyncMock()))

    appel = attendance.get(appel_id)
    assert appel is not None
    assert appel.validated_at is not None
    respo.send.assert_awaited()
    assert "validated" in caplog.text
    assert NAME not in caplog.text
    assert "Training Secret" not in caplog.text


async def test_attendance_lines_stay_nameless_without_a_log_channel(caplog: pytest.LogCaptureFixture) -> None:
    """With no log channel the warning names the check by ID: neither the member's name nor the training's name appears."""
    caplog.set_level(logging.DEBUG)

    await _validate_secret_training(None, log_channel_id=None)

    assert "No log channel for the counts of appel" in caplog.text
    assert NAME not in caplog.text
    assert "Training Secret" not in caplog.text


async def test_attendance_lines_stay_nameless_when_the_log_channel_refuses(caplog: pytest.LogCaptureFixture) -> None:
    """When the log channel refuses the counts the failure line names the check by ID, never the training."""
    channel = MagicMock(spec=TextChannel)
    channel.send = AsyncMock(side_effect=HTTPException(MagicMock(status=500, reason="boom"), "boom"))
    caplog.set_level(logging.DEBUG)

    await _validate_secret_training(channel)

    assert "Could not post the counts of appel" in caplog.text
    assert NAME not in caplog.text
    assert "Training Secret" not in caplog.text


async def _submit_template_form(form: TemplateForm, *, name: str) -> None:
    """Fill the form of a template with the secret name and submit it as a Respo Formation."""
    interaction = make_interaction(user_name=NAME, guild_id=1234)
    interaction.user.roles = [make_role(RoleNames.TRAININGS_MANAGER)]
    fill_text_input(form.name_input, name)
    fill_text_input(form.duration_input, "1 h")
    fill_text_input(form.seats_input, "4")
    fill_text_input(form.description_input, f"{NAME} décrit")
    form.excusable_select._values = ["Oui"]  # pyright: ignore[reportPrivateUsage]
    await form.on_submit(interaction)


async def test_template_lines_name_templates_by_id_only(caplog: pytest.LogCaptureFixture) -> None:
    """Creating, editing and deleting a template logs its ID and the server and member IDs: never its name or description."""
    caplog.set_level(logging.DEBUG)

    await _submit_template_form(TemplateForm(None), name=NAME)
    (stored,) = catalogue.list_templates(1234)
    await _submit_template_form(TemplateForm(stored), name=f"{NAME} 2")
    (stored,) = catalogue.list_templates(1234)
    interaction = make_interaction(user_name=NAME, guild_id=1234)
    interaction.user.roles = [make_role(RoleNames.TRAININGS_MANAGER)]
    await TemplateDelete(stored.id).callback(interaction)

    assert catalogue.list_templates(1234) == []
    assert f"template_id={stored.id}" in caplog.text
    assert "create" in caplog.text
    assert "update" in caplog.text
    assert "delete" in caplog.text
    assert NAME not in caplog.text
    assert "Kevin Secret" not in caplog.text
