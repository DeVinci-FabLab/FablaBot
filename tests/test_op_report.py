"""At the end of an op, the actions done by hand are counted in the op channel and detailed in the log channel."""

from __future__ import annotations

import asyncio
import sqlite3
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from discord import AllowedMentions, AuditLogAction, HTTPException, Permissions, TextChannel

from fablabot.helpers import op_report

GUILD_ID, MEMBER_ID, LOG_ID = 1, 2, 3


def _entry(
    action: AuditLogAction,
    target: Any,
    *,
    added: tuple[Any, ...] = (),
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        action=action,
        target=target,
        after=SimpleNamespace(roles=list(added)),
        changes=SimpleNamespace(before=before or {}, after=after or {}),
        id=hash(action),
    )


def _posted(server: Any) -> str:
    return "\n".join(call.args[0] for call in server.log.send.await_args_list)


class _Server:
    """A guild whose audit log holds one grant of the role by the bot, then the member's own actions."""

    def __init__(self, actions: list[SimpleNamespace], *, granted: bool = True) -> None:
        self.role = MagicMock(name="Admin -temp-")
        self.member = MagicMock(id=MEMBER_ID, mention="<@2>")
        self.log = MagicMock(spec=TextChannel, mention="<#3>")
        self.log.send = AsyncMock()
        self.guild = MagicMock(id=GUILD_ID)
        self.guild.get_channel = MagicMock(side_effect=lambda cid: self.log if cid == LOG_ID else None)
        grants = [_entry(AuditLogAction.member_role_update, self.member, added=(self.role,))] if granted else []
        self.seen: list[dict[str, Any]] = []

        def audit_logs(**kwargs: Any) -> Any:
            self.seen.append(kwargs)
            rows = actions if kwargs.get("user") is self.member else grants

            async def gen() -> Any:
                for row in rows:
                    yield row

            return gen()

        self.guild.audit_logs = audit_logs

    async def report(self) -> str:
        with patch.object(op_report, "get_log_channel_id", return_value=LOG_ID):
            return await op_report.report_op(self.guild, self.member, self.role)


async def test_actions_are_counted_in_public_and_detailed_in_the_log_channel() -> None:
    """The op channel gets a count only; the log channel gets one line per action, pinging nobody."""
    channel = MagicMock(mention="<#9>")
    server = _Server([_entry(AuditLogAction.channel_update, channel), _entry(AuditLogAction.ban, MagicMock(mention="<@7>"))])

    sentence = await server.report()

    assert sentence == "2 action(s) hors du bot, détail dans <#3>."
    text = server.log.send.await_args.args[0]
    assert "- salon modifié : <#9>" in text
    assert "- membre banni : <@7>" in text
    assert server.log.send.await_args.kwargs["allowed_mentions"].to_dict() == AllowedMentions.none().to_dict()
    assert server.seen[1]["after"].action is AuditLogAction.member_role_update
    assert server.seen[1]["oldest_first"] is True


async def test_no_manual_action_is_said_and_nothing_is_posted() -> None:
    """An op used only through the bot leaves the log channel quiet."""
    server = _Server([])

    assert await server.report() == "Aucune action hors du bot dans le journal d'audit."
    server.log.send.assert_not_awaited()


async def test_a_long_op_is_cut_after_twenty_lines() -> None:
    """The detail stays readable: twenty lines, then the rest as a count."""
    server = _Server([_entry(AuditLogAction.role_update, None)] * 25)

    await server.report()

    text = _posted(server)
    assert text.count("- rôle modifié") == 20
    assert "- … et 5 autres" in text


async def test_an_op_whose_grant_is_not_found_says_so() -> None:
    """Without the grant there is no start: the bot does not claim that nothing was done."""
    server = _Server([_entry(AuditLogAction.kick, None)], granted=False)

    assert "introuvable" in await server.report()
    server.log.send.assert_not_awaited()


async def test_an_unreadable_audit_log_says_so() -> None:
    """A refused audit log read is told, and the end of the op is still announced by the caller."""
    server = _Server([])
    server.guild.audit_logs = MagicMock(side_effect=HTTPException(MagicMock(status=403), "Missing Access"))

    assert "illisible" in await server.report()


async def test_without_a_log_channel_only_the_count_is_given() -> None:
    """No detail goes to the op channel, which members may read."""
    server = _Server([_entry(AuditLogAction.kick, MagicMock(mention="<@7>"))])

    with patch.object(op_report, "get_log_channel_id", return_value=None):
        sentence = await op_report.report_op(server.guild, server.member, server.role)

    assert sentence.startswith("1 action(s) hors du bot")
    assert "<@7>" not in sentence
    assert "/log set" in sentence


async def test_a_database_failure_still_gives_a_sentence() -> None:
    """The log channel lookup reads the database: its failure must not take the end notice with it."""
    server = _Server([_entry(AuditLogAction.kick, None)])

    with patch.object(op_report, "get_log_channel_id", side_effect=sqlite3.OperationalError("locked")):
        sentence = await op_report.report_op(server.guild, server.member, server.role)

    assert "illisible" in sentence


async def test_a_slow_audit_log_gives_up() -> None:
    """A read that hangs ends in a sentence, not in a reply that never comes."""
    server = _Server([])

    async def hang(**_kwargs: Any) -> Any:
        await asyncio.sleep(3600)
        yield

    server.guild.audit_logs = hang
    with patch.object(op_report, "_TIMEOUT", 0.01):
        assert "illisible" in await server.report()


async def test_each_change_shows_before_and_after() -> None:
    """A renamed channel, a permission turned on, roles given and taken, an invite: each says what changed."""
    laser, admin = MagicMock(mention="<@&10>"), MagicMock(mention="<@&11>")
    server = _Server(
        [
            _entry(
                AuditLogAction.channel_update,
                MagicMock(mention="<#9>"),
                before={"name": "numérique"},
                after={"name": "numerique"},
            ),
            _entry(
                AuditLogAction.role_update,
                MagicMock(mention="<@&12>"),
                before={"permissions": Permissions(send_messages=True)},
                after={"permissions": Permissions(send_messages=True, manage_messages=True)},
            ),
            _entry(
                AuditLogAction.member_role_update,
                MagicMock(mention="<@7>"),
                before={"roles": [admin]},
                after={"roles": [laser]},
            ),
            _entry(AuditLogAction.invite_create, SimpleNamespace(code="twdhxe7pt", id="twdhxe7pt"), after={"max_uses": 0}),
        ]
    )

    await server.report()

    text = _posted(server)
    assert "  nom : `numérique` → `numerique`" in text
    assert "  permissions : +manage_messages" in text
    assert "  rôles : +<@&10> -<@&11>" in text
    assert "- invitation créée : `twdhxe7pt`" in text
    assert "  utilisations max : ∅ → `0`" in text


async def test_a_long_report_is_split_under_discord_limit() -> None:
    """Long values are cut, and the detail goes out in several messages rather than failing as one."""
    server = _Server(
        [_entry(AuditLogAction.channel_update, None, before={"topic": "a" * 500}, after={"topic": "b" * 500})] * 20
    )

    await server.report()

    sent = [call.args[0] for call in server.log.send.await_args_list]
    assert len(sent) > 1
    assert all(len(message) <= 2000 for message in sent)
    assert _posted(server).count("- salon modifié") == 20
