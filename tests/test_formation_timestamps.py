"""Formation dates go out as Discord timestamps; exports, read outside Discord, stay plain text."""

from __future__ import annotations

from datetime import datetime
import time
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from fablabot.helpers.constants import PARIS_TZ
from fablabot.helpers.formation import notifications, rendering
from fablabot.models.formation import Formation

if TYPE_CHECKING:
    from collections.abc import Iterator

# Either side of the spring clock change (2030-03-31, 02:00 -> 03:00): UTC+1 before, UTC+2 after.
WINTER = datetime(2030, 3, 30, 14, 30, tzinfo=PARIS_TZ)
SUMMER = datetime(2030, 4, 1, 14, 30, tzinfo=PARIS_TZ)
WINTER_UNIX = 1901107800  # 2030-03-30 13:30 UTC
SUMMER_UNIX = 1901277000  # 2030-04-01 12:30 UTC


@pytest.fixture(autouse=True)
def _utc_machine(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Run on a UTC machine, so a wall clock read in the local zone cannot pass for the Paris one."""
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def _fm(start: datetime, waitlisted: list[dict[str, Any]] | None = None) -> Formation:
    return Formation(
        emoji="🔧",
        name="Soudure",
        trainer_mention="<@42>",
        start_iso=start.isoformat(),
        duration="2h",
        seats=5,
        registered_users=[{"user_id": 7, "username": "ada", "ts_iso": "2030-03-01T10:00:00+01:00"}],
        waitlisted_users=waitlisted or [],
    )


def test_unix_values_are_the_expected_ones() -> None:
    """Guard the constants of this file against a typo."""
    assert int(WINTER.timestamp()) == WINTER_UNIX
    assert int(SUMMER.timestamp()) == SUMMER_UNIX


@pytest.mark.parametrize(("start", "unix"), [(WINTER, WINTER_UNIX), (SUMMER, SUMMER_UNIX)])
def test_discord_timestamp_uses_the_aware_instant(start: datetime, unix: int) -> None:
    """The value is the absolute instant of the Paris start, not the wall clock read as another zone."""
    assert rendering.discord_timestamp(start) == f"<t:{unix}:F>"


@pytest.mark.parametrize(("start", "unix"), [(WINTER, WINTER_UNIX), (SUMMER, SUMMER_UNIX)])
def test_formation_line_carries_the_timestamp(start: datetime, unix: int) -> None:
    """The formation block shows the bold timestamp and no hand-written date."""
    line = rendering.render_formation(_fm(start)).split("\n")[1]
    assert line.startswith(f"> **<t:{unix}:F>**")
    assert "h30" not in line


def test_export_stays_plain_text() -> None:
    """The export has no Discord timestamp and keeps the registration date as words."""
    export = rendering.format_formation_export(_fm(WINTER))
    assert "<t:" not in export
    assert "inscrit·e le vendredi 01/03 à 10h00" in export


def test_export_waitlist_stays_plain_text() -> None:
    """A waitlisted entry keeps its registration date as words, marked as waiting, with no timestamp."""
    waitlisted = [{"user_id": 8, "username": "bob", "ts_iso": "2030-03-02T11:15:00+01:00"}]
    export = rendering.format_formation_export(_fm(WINTER, waitlisted))
    assert "<t:" not in export
    assert "inscrit·e le samedi 02/03 à 11h15 (en attente)" in export


def test_export_entry_without_date_reads_n_a() -> None:
    """A registered or waitlisted entry with no `ts_iso` shows "n/a", not the placeholder date."""
    fm = _fm(WINTER, [{"user_id": 8, "username": "bob"}])
    fm.registered_users = [{"user_id": 7, "username": "ada"}]
    lines = rendering.format_formation_export(fm).split("\n")
    assert lines[1].endswith("· inscrit·e le n/a")
    assert lines[2].endswith("· inscrit·e le n/a (en attente)")


def test_export_numbers_the_waitlist_after_the_registered() -> None:
    """The waitlist continues the numbering of the registered list instead of restarting at 1."""
    waitlisted = [{"user_id": 9, "username": "cy"}, {"user_id": 10, "username": "di"}]
    fm = _fm(WINTER, waitlisted)
    fm.registered_users.append({"user_id": 8, "username": "bob"})
    lines = rendering.format_formation_export(fm).split("\n")
    assert [line.split(" · ")[0].split(" (")[0] for line in lines[1:]] == ["1. <@7>", "2. <@8>", "3. <@9>", "4. <@10>"]


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Collect the text of every DM the notifications send."""
    messages: list[str] = []

    async def fake_send(_logger: Any, _guild: Any, _member: Any, message: str, **_: Any) -> None:
        messages.append(message)

    member = MagicMock()
    member.display_name = "Ada"
    monkeypatch.setattr(notifications, "send_dm_to_member", fake_send)
    monkeypatch.setattr(notifications, "get_or_fetch_member", AsyncMock(return_value=member))
    monkeypatch.setattr(notifications, "get_members_by_role", MagicMock(return_value=[member]))
    return messages


@pytest.mark.parametrize(("start", "unix"), [(WINTER, WINTER_UNIX), (SUMMER, SUMMER_UNIX)])
async def test_every_dm_carries_the_timestamp(sent: list[str], start: datetime, unix: int) -> None:
    """Registration, promotion, participant, trainer and responsible DMs read "le <timestamp>"."""
    fm = _fm(start)
    guild = MagicMock()
    stamp = f"<t:{unix}:F>"
    await notifications.send_registration_dm(guild, 7, fm, "x")
    await notifications.send_promotion_dm(guild, 7, fm, "x")
    await notifications.notify_participants_before_formation(guild, fm, "x")
    await notifications.notify_trainer_before_formation(guild, fm, "x", "hour_before")
    await notifications.notify_trainer_before_formation(guild, fm, "x", "start")
    await notifications.notify_responsible_before_formation(guild, fm, moment="hour_before")
    await notifications.notify_responsible_before_formation(guild, fm, moment="start")
    assert len(sent) == 7
    for message in sent:
        assert f"le {stamp}" in message
        assert "h30" not in message.split("\n\n")[0]
