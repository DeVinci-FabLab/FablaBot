"""The bot touches a heartbeat file while connected, and the health check reads its age."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from fablabot import healthcheck
from fablabot.healthcheck import HEARTBEAT_FILE, HEARTBEAT_INTERVAL_SECONDS, HEARTBEAT_MAX_AGE_SECONDS

NOW = 1_800_000_000.0


@pytest.fixture
def bot(fablabot_main, monkeypatch, tmp_path: Path):
    """A bot whose heartbeat file lives in ``tmp_path``."""
    monkeypatch.setenv("ALLOWED_GUILD_IDS", "111111111111111111")
    instance = fablabot_main.Fablabot()
    instance.heartbeat_file = tmp_path / "beat"
    return instance


@pytest.mark.parametrize(("age", "code"), [(10, 0), (90, 0), (91, 1)])
def test_health_by_heartbeat_age(monkeypatch, tmp_path: Path, capsys, age: int, code: int) -> None:
    """A heartbeat up to 90 s old is healthy; an older one is not."""
    beat = tmp_path / "beat"
    beat.touch()
    os.utime(beat, (NOW - age, NOW - age))
    monkeypatch.setattr(healthcheck, "HEARTBEAT_FILE", beat)
    monkeypatch.setattr(time, "time", lambda: NOW)
    assert healthcheck.main() == code
    out = capsys.readouterr().out
    if age == 10:
        assert "healthy: last heartbeat 10 s ago" in out
    if code:
        assert out.startswith("unhealthy:")


def test_missing_heartbeat_is_unhealthy(monkeypatch, tmp_path: Path, capsys) -> None:
    """Without a heartbeat file the check fails."""
    monkeypatch.setattr(healthcheck, "HEARTBEAT_FILE", tmp_path / "absent")
    assert healthcheck.main() == 1
    assert "unhealthy: no heartbeat" in capsys.readouterr().out


def test_healthcheck_imports_no_discord() -> None:
    """The check loads no Discord code, so a container can run it often."""
    result = subprocess.run(
        [sys.executable, "-c", "import sys, fablabot.healthcheck; print('discord' in sys.modules)"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "False"


def test_heartbeat_file_is_in_tmp() -> None:
    """The container mounts /tmp as a tmpfs, so the file must live there."""
    assert Path("/tmp") == HEARTBEAT_FILE.parent


async def test_on_ready_starts_the_loop_once(bot) -> None:
    """The first ready starts the loop; a second one changes nothing."""
    try:
        await bot.on_ready()
        await asyncio.sleep(0)
        assert bot.heartbeat.is_running()
        assert bot.heartbeat_file.exists()
        await bot.on_ready()
    finally:
        bot.heartbeat.cancel()


async def test_beats_only_while_connected(bot) -> None:
    """No beat before the gateway is up or after it drops."""
    assert bot.write_heartbeat() is False
    assert not bot.heartbeat_file.exists()
    await bot.on_resumed()
    assert bot.write_heartbeat() is True
    assert bot.heartbeat_file.exists()
    old = time.time() - 100
    os.utime(bot.heartbeat_file, (old, old))
    await bot.on_disconnect()
    assert bot.write_heartbeat() is False
    assert bot.heartbeat_file.stat().st_mtime == pytest.approx(old)


async def test_loop_beat_renews_the_file(bot) -> None:
    """One loop iteration moves the heartbeat forward."""
    await bot.on_resumed()
    bot.heartbeat_file.touch()
    old = time.time() - 100
    os.utime(bot.heartbeat_file, (old, old))
    await bot.heartbeat()
    assert bot.heartbeat_file.stat().st_mtime > old + 50


async def test_no_beat_after_close(bot, monkeypatch) -> None:
    """A closed client never beats, even if the gateway flag is still set."""
    await bot.on_resumed()
    monkeypatch.setattr(bot, "is_closed", lambda: True)
    assert bot.write_heartbeat() is False
    assert not bot.heartbeat_file.exists()


async def test_failed_write_is_logged_once(bot, caplog, tmp_path: Path) -> None:
    """A failing write logs one error until a write succeeds again."""
    await bot.on_resumed()
    folder = tmp_path / "gone"
    bot.heartbeat_file = folder / "beat"
    with caplog.at_level(logging.ERROR):
        assert bot.write_heartbeat() is False
        assert bot.write_heartbeat() is False
        records = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(records) == 1
        assert str(bot.heartbeat_file) in records[0].getMessage()
        folder.mkdir()
        assert bot.write_heartbeat() is True
        folder.joinpath("beat").unlink()
        folder.rmdir()
        assert bot.write_heartbeat() is False
        assert len([r for r in caplog.records if r.levelno == logging.ERROR]) == 2


def test_loop_interval_matches_the_health_check(bot) -> None:
    """The loop beats three times within the health check's tolerance."""
    assert bot.heartbeat.seconds == HEARTBEAT_INTERVAL_SECONDS == 30
    assert HEARTBEAT_MAX_AGE_SECONDS == 3 * HEARTBEAT_INTERVAL_SECONDS


def test_event_handlers_are_not_marked_as_overrides(fablabot_main) -> None:
    """Discord's client defines no on_ready, on_resumed or on_disconnect to override."""
    cls = fablabot_main.Fablabot
    for name in ("on_ready", "on_resumed", "on_disconnect"):
        assert not any(hasattr(base, name) for base in cls.__mro__[1:])
        assert not getattr(getattr(cls, name), "__override__", False), name
