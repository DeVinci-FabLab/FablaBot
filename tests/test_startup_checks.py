"""The bot checks its folders and token before it connects, and says what is wrong."""

from __future__ import annotations

import contextlib
import importlib
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from fablabot.helpers.startup_checks import StartupError, check_runtime_access, read_token

if TYPE_CHECKING:
    from collections.abc import Iterator

non_root = pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file modes")

UID = os.getuid()


@contextlib.contextmanager
def mode(path: Path, value: int) -> Iterator[None]:
    """Set a mode on ``path`` and restore full access afterwards."""
    path.chmod(value)
    try:
        yield
    finally:
        path.chmod(0o755 if path.is_dir() else 0o644)


@pytest.fixture
def token(tmp_path: Path) -> Path:
    """A readable token file holding ``fake-token``."""
    path = tmp_path / "token"
    path.write_text("fake-token")
    return path


def test_missing_folders_are_created(token: Path) -> None:
    """Absent data/ and logs/ are created empty and the token comes back."""
    assert check_runtime_access(str(token)) == "fake-token"
    assert list(Path("data").iterdir()) == []
    assert list(Path("logs").iterdir()) == []


@non_root
@pytest.mark.parametrize("name", ["data", "logs"])
def test_unwritable_folder_stops(token: Path, name: str) -> None:
    """A top folder without write access stops the start with one problem."""
    Path(name).mkdir()
    with mode(Path(name), 0o555), pytest.raises(StartupError) as error:
        check_runtime_access(str(token))
    assert error.value.problems == [f"{name}/ is not writable by uid {UID}."]


@non_root
@pytest.mark.parametrize(
    ("relative", "value", "problem"),
    [
        ("data/guild_config.json", 0o444, f"data/guild_config.json is not writable by uid {UID}."),
        ("data/guild_config.json", 0o200, f"data/guild_config.json is not readable by uid {UID}."),
        ("logs/guilds/1234", 0o555, f"logs/guilds/1234/ is not writable by uid {UID}."),
    ],
)
def test_unusable_entry_stops(token: Path, relative: str, value: int, problem: str) -> None:
    """A file or subfolder the bot cannot use is found by walking the tree."""
    target = Path(relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    if relative.endswith("1234"):
        target.mkdir()
    else:
        target.write_text("{}")
    with mode(target, value), pytest.raises(StartupError) as error:
        check_runtime_access(str(token))
    assert error.value.problems == [problem]


@non_root
@pytest.mark.parametrize(
    ("setup", "expected"),
    [
        ("unset", "DISCORD_TOKEN_FILE is not set."),
        ("missing", "the token file {path} is not readable by uid {uid}: No such file or directory."),
        ("unreadable", "the token file {path} is not readable by uid {uid}:"),
        ("empty", "the token file {path} is empty."),
        ("blank", "the token file {path} is empty."),
    ],
)
def test_token_problems(tmp_path: Path, setup: str, expected: str) -> None:
    """Each way the token can be unusable gives its own line, never the token itself."""
    path = tmp_path / "token"
    if setup == "empty":
        path.write_text("")
    elif setup == "blank":
        path.write_text("  \n")
    elif setup == "unreadable":
        path.write_text("s3cr3t-token")
        path.chmod(0o000)
    try:
        with pytest.raises(StartupError) as error:
            read_token(None if setup == "unset" else str(path))
    finally:
        if setup == "unreadable":
            path.chmod(0o644)
    (problem,) = error.value.problems
    message = expected.format(path=path, uid=UID)
    if setup == "unreadable":
        assert problem.startswith(message)
        assert "s3cr3t" not in problem
    else:
        assert problem == message


def test_token_is_stripped(tmp_path: Path) -> None:
    """Surrounding whitespace is removed from the token."""
    path = tmp_path / "token"
    path.write_text("fake-token\n")
    assert read_token(str(path)) == "fake-token"


@non_root
def test_every_problem_is_reported() -> None:
    """All problems come back together: data, logs, then the token."""
    Path("data").mkdir()
    Path("logs").mkdir()
    with mode(Path("data"), 0o555), mode(Path("logs"), 0o555), pytest.raises(StartupError) as error:
        check_runtime_access(None)
    assert error.value.problems == [
        f"data/ is not writable by uid {UID}.",
        f"logs/ is not writable by uid {UID}.",
        "DISCORD_TOKEN_FILE is not set.",
    ]
    assert str(error.value) == "\n".join(error.value.problems)


@non_root
def test_main_stops_before_anything_is_written(fablabot_main, monkeypatch) -> None:
    """An unusable data/ ends the process with a message before the bot is built."""
    bot_class = MagicMock()
    monkeypatch.setattr(fablabot_main, "Fablabot", bot_class)
    Path("data").mkdir()
    before = {entry.name for entry in Path.cwd().iterdir()}
    with mode(Path("data"), 0o555), pytest.raises(SystemExit) as exit_info:
        fablabot_main.main()
    assert exit_info.value.code == f"Cannot start: data/ is not writable by uid {UID}."
    bot_class.assert_not_called()
    assert list(Path("logs").iterdir()) == []
    assert {entry.name for entry in Path.cwd().iterdir()} == before | {"logs"}


def test_main_runs_the_bot_with_the_token(fablabot_main, monkeypatch) -> None:
    """With usable folders, main builds the bot once and runs it with the token."""
    bot_class = MagicMock()
    monkeypatch.setattr(fablabot_main, "Fablabot", bot_class)
    fablabot_main.main()
    bot_class.assert_called_once_with()
    bot_class.return_value.run.assert_called_once_with("fake-token")


def test_import_needs_no_token(fablabot_main, monkeypatch) -> None:
    """Importing the module no longer reads the token file."""
    monkeypatch.delenv("DISCORD_TOKEN_FILE", raising=False)
    importlib.reload(fablabot_main)


@pytest.fixture
def restored_logging() -> Iterator[None]:
    """Restore the root handlers and the levels of the root and discord loggers."""
    root = logging.getLogger()
    discord_logger = logging.getLogger("discord")
    handlers = list(root.handlers)
    root_level = root.level
    discord_level = discord_logger.level
    yield
    for handler in list(root.handlers):
        if handler not in handlers:
            root.removeHandler(handler)
            handler.close()
    root.setLevel(root_level)
    discord_logger.setLevel(discord_level)


@pytest.mark.usefixtures("restored_logging")
async def test_startup_writes_only_inside_data_and_logs(fablabot_main, monkeypatch, tmp_path: Path) -> None:
    """A full setup leaves nothing in the working directory besides data/ and logs/."""
    app = tmp_path / "app"
    app.mkdir()
    monkeypatch.chdir(app)
    monkeypatch.setenv("ALLOWED_GUILD_IDS", "111111111111111111")
    bot = fablabot_main.Fablabot()
    bot.tree.sync = AsyncMock(return_value=[])
    await bot.setup_hook()
    await bot.close()
    assert {entry.name for entry in app.iterdir()} == {"data", "logs"}
