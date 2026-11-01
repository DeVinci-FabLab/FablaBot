"""The bot never starts over JSON state it does not hold, and an empty folder still starts."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from fablabot import db

if TYPE_CHECKING:
    from types import ModuleType

DATA = Path("data")
DATABASE = DATA / "fablabot.db"
STATE = '{"111": {"published": {"message_id": 1}}}'


@pytest.fixture(autouse=True)
def _closed() -> None:
    """Close the test's own database: these tests open the real path."""
    db.close_database()


def _write(*names: str) -> dict[str, bytes]:
    """Write state files under ``data/`` and return their content."""
    DATA.mkdir(exist_ok=True)
    for name in names:
        (DATA / name).write_text(STATE, encoding="utf-8")
    return _content()


def _content() -> dict[str, bytes]:
    """Read every file under ``data/``."""
    return {path.name: path.read_bytes() for path in DATA.iterdir()}


def _imported(*names: str) -> None:
    """Start a database that notes these files as imported, then write them beside it."""
    db.open_database()
    with db.transaction() as conn:
        for name in names:
            conn.execute(
                "INSERT INTO state_import (file_name, sha256, imported_at) VALUES (?, ?, ?)",
                (name, hashlib.sha256(STATE.encode()).hexdigest(), datetime.now(UTC).isoformat()),
            )
    db.close_database()
    _write(*names)


def test_an_empty_folder_starts() -> None:
    """No folder at all: the database is created and opened."""
    db.open_database()

    assert DATABASE.exists()


def test_a_started_bot_starts_again() -> None:
    """A second start finds its database and refuses nothing."""
    db.open_database()
    db.close_database()

    db.open_database()

    assert db.connection().execute("SELECT count(*) FROM state_import").fetchone()[0] == 0


@pytest.mark.parametrize("name", db.STATE_FILES)
def test_state_never_imported_stops_the_start_and_touches_nothing(name: str) -> None:
    """Each JSON state file alone stops the start, and the folder is left byte for byte, with no database."""
    before = _write(name)

    with pytest.raises(db.StorageError, match="not imported") as refused:
        db.open_database()

    assert f"data/{name}" in str(refused.value)
    assert _content() == before
    assert not DATABASE.exists()


def test_state_next_to_a_database_without_its_import_stops_the_start() -> None:
    """A database started on its own holds nothing of the file beside it: refused, the file untouched."""
    db.open_database()
    db.close_database()
    before = _write("formations_state.json")["formations_state.json"]

    with pytest.raises(db.StorageError, match="not imported"):
        db.open_database()

    assert (DATA / "formations_state.json").read_bytes() == before


def test_the_command_sync_record_is_no_state_to_import() -> None:
    """The fingerprint file stays a file: a bot with only that one starts."""
    _write("command_sync_state.json")

    db.open_database()

    assert (DATA / "command_sync_state.json").exists()


def test_state_recorded_as_imported_starts() -> None:
    """With every file noted and unchanged, the start goes on."""
    _imported(*db.STATE_FILES)

    db.open_database()

    assert db.connection().execute("SELECT count(*) FROM state_import").fetchone()[0] == len(db.STATE_FILES)


def test_one_file_left_out_stops_the_start_and_is_named() -> None:
    """A file that appears after the import is refused, by name."""
    _imported("guild_config.json")
    _write("messages_state.json")

    with pytest.raises(db.StorageError) as refused:
        db.open_database()

    assert "data/messages_state.json" in str(refused.value)
    assert "guild_config" not in str(refused.value)


def test_a_file_changed_since_its_import_stops_the_start() -> None:
    """A JSON file that moved on after the import means the database is behind: refused, nothing written."""
    _imported("formations_state.json")
    (DATA / "formations_state.json").write_text('{"changed": true}', encoding="utf-8")
    before = _content()

    with pytest.raises(db.StorageError, match="changed since"):
        db.open_database()

    assert _content() == before


def test_main_stops_before_the_bot_exists(fablabot_main: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """Refused, the process ends with one `Cannot start` line and never builds a client."""
    _write("temp_admin_state.json")
    bot = MagicMock(side_effect=AssertionError("the bot must not be built"))
    monkeypatch.setattr(fablabot_main, "Fablabot", bot)

    with pytest.raises(SystemExit) as stopped:
        fablabot_main.main()

    assert str(stopped.value).startswith("Cannot start: data/temp_admin_state.json not imported")
    bot.assert_not_called()


def test_the_store_only_serves_the_command_sync_record() -> None:
    """No code path of the bot still writes a JSON state file."""
    sources = [path for path in Path(db.__file__).resolve().parent.rglob("*.py") if path.name != "db.py"]

    assert not [path.name for path in sources if any(name in path.read_text(encoding="utf-8") for name in db.STATE_FILES)]


def test_the_operator_guide_covers_the_database() -> None:
    """The guide stops the stack before the archive and names every refusal."""
    guide = (Path(__file__).resolve().parent.parent / "docs" / "OPERATIONS.md").read_text(encoding="utf-8")
    before_redeploy = guide[guide.index("### Before every redeploy") : guide.index("### Releasing and redeploying")]

    assert before_redeploy.index("Stop the stack") < before_redeploy.index("tar -czf")
    assert "not imported into data/fablabot.db" in guide
    assert "changed since it was imported" in guide
    assert "is at schema version" in guide
    assert "cannot be used" in guide
    assert "data/backups/" in guide
