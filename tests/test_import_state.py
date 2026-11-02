"""The import reads strictly, writes all or nothing, happens once, and leaves the JSON files untouched."""

from __future__ import annotations

import copy
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest
from state_shapes import FIRST_GUILD, GONE_GUILD, MESSAGE_ID, state_files, write_state

from fablabot import db, import_state

DATA = Path("data")


@pytest.fixture(autouse=True)
def _closed() -> None:
    """Close the test's own database: these tests use the real path."""
    db.close_database()


def _folder() -> dict[str, bytes]:
    """Read every file under ``data/``."""
    return {path.relative_to(DATA).as_posix(): path.read_bytes() for path in DATA.rglob("*") if path.is_file()}


def _changed(path: tuple[Any, ...], value: Any) -> dict[str, Any]:
    """Return the production shapes with one value replaced at ``path``, the first element being the file."""
    files = copy.deepcopy(state_files())
    node: Any = files
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    return files


FIRST = str(FIRST_GUILD)
PUBLISHED = ("formations_state.json", FIRST, "published")
FM = (*PUBLISHED, "message", "fms", 0)
TRACKED = ("messages_state.json", FIRST, "tracked", str(MESSAGE_ID + 1))


@pytest.mark.parametrize(
    "raw",
    [b"", b"{", b'{"a"', b"\xff\xfe", b"[]", b"null", b"1"],
    ids=["empty", "cut", "cut-key", "not-utf8", "list", "null", "number"],
)
@pytest.mark.parametrize("name", db.STATE_FILES)
def test_a_corrupt_file_stops_before_anything_is_written(name: str, raw: bytes) -> None:
    """A file the bot would have read as empty stops the check, and the folder stays byte for byte."""
    write_state()
    (DATA / name).write_bytes(raw)
    before = _folder()

    with pytest.raises(SystemExit, match=f"Cannot import: .*{name}"):
        import_state.main(["--check"])

    assert _folder() == before


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("guild_config.json", FIRST, "unknown"), 1, "unknown key"),
        (("guild_config.json", "not-an-id"), {}, "not an ID"),
        (("guild_config.json", FIRST, "log_channel_id"), "2", "log_channel_id: unexpected value"),
        (("guild_config.json", FIRST, "welcome_verify_enabled"), 1, "unexpected value"),
        (("guild_config.json", "__global__", "other"), 1, "unknown key"),
        (("formations_state.json", "__global__"), {}, "not an ID"),
        (("formations_state.json", FIRST, "unknown"), 1, "unknown key"),
        ((*PUBLISHED, "unknown"), 1, "unknown key"),
        ((*PUBLISHED, "message_id"), None, "unexpected value"),
        ((*PUBLISHED, "message", "unknown"), 1, "unknown key"),
        ((*FM, "unknown"), 1, "unknown key"),
        ((*FM, "duration"), 90, "unexpected value"),
        ((*FM, "seats"), True, "unexpected value"),
        ((*FM, "registered_users", 0, "unknown"), 1, "unknown key"),
        ((*FM, "registered_users", 0, "ts_iso"), None, "unexpected value"),
        (("formations_state.json", FIRST, "reactions_log", 0, "action"), "ADD", "expected one of"),
        (("formations_state.json", FIRST, "reactions_log", 0, "unknown"), 1, "unknown key"),
        (("formations_state.json", FIRST, "reactions_log", 0, "user_id"), 2**63, "unexpected value"),
        (("messages_state.json", FIRST, "unknown"), 1, "unknown key"),
        ((*TRACKED, "message_id"), 5, "differ"),
        ((*TRACKED, "unknown"), 1, "unknown key"),
        ((*TRACKED, "reactions", 0, "action_type"), "webhook", "expected one of"),
        ((*TRACKED, "reactions", 0, "unknown"), 1, "unknown key"),
    ],
)
def test_an_unexpected_value_stops_with_its_place_and_not_its_content(path: tuple[Any, ...], value: Any, message: str) -> None:
    """A key or a type the code never wrote is a file this import does not know: it stops, and nothing is written."""
    write_state(_changed(path, value))
    before = _folder()

    with pytest.raises(SystemExit, match=message) as refused:
        import_state.main(["--check"])

    assert _folder() == before
    assert "name-" not in str(refused.value)


def test_a_missing_key_with_no_default_stops() -> None:
    """A registered member with no ID has no place in the schema."""
    files = state_files()
    del files["formations_state.json"][FIRST]["published"]["message"]["fms"][0]["registered_users"][0]["user_id"]
    write_state(files)

    with pytest.raises(SystemExit, match="key user_id is missing"):
        import_state.main(["--check"])


def test_a_key_older_files_lack_takes_the_default_of_the_old_read() -> None:
    """A draft from before the form link reads as it did."""
    files = state_files()
    del files["formations_state.json"][FIRST]["draft"]["request_forms_url"]

    state = import_state.parse(write_state(files))

    assert state["formations_state.json"][FIRST]["draft"]["request_forms_url"].startswith("https://")


def test_no_state_file_stops_rather_than_passing_on_an_empty_volume() -> None:
    """A wrong mount looks like this."""
    DATA.mkdir()

    with pytest.raises(SystemExit, match="no state file"):
        import_state.main(["--check"])

    assert _folder() == {}


def test_check_reports_each_server_and_writes_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    """Counts per server, no name, no text, and not one file created."""
    write_state()
    before = _folder()

    import_state.main(["--check"])

    out = capsys.readouterr().out
    assert _folder() == before
    line = next(line for line in out.splitlines() if line.startswith(f"guild_id={FIRST_GUILD} "))
    assert "published=1" in line
    assert "registered=2" in line
    assert "waitlisted=1" in line
    assert "fm_events=153" in line
    assert "tracked=2" in line
    assert "links=3" in line
    assert f"guild_id={GONE_GUILD} " in out
    assert "guild_id=__global__ settings=1 " in out
    assert "name-" not in out


def test_the_import_needs_no_token_and_loads_no_bot() -> None:
    """It runs with nothing but the data volume."""
    src = Path(db.__file__).resolve().parent.parent
    code = "import sys, fablabot.import_state, fablabot.export_state; sys.exit('fablabot.main' in sys.modules)"
    env = {key: value for key, value in os.environ.items() if key != "DISCORD_TOKEN_FILE"}

    done = subprocess.run([sys.executable, "-c", code], env={**env, "PYTHONPATH": str(src)}, capture_output=True, check=False)

    assert done.returncode == 0, done.stderr
