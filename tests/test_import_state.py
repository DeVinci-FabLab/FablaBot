"""The import reads strictly, writes all or nothing, happens once, and leaves the JSON files untouched."""

from __future__ import annotations

import copy
import hashlib
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
from typing import Any

import pytest
from state_shapes import (
    FIRST_GUILD,
    GONE_GUILD,
    GUILDS,
    MESSAGE_ID,
    REGISTERED,
    REMINDERS_SENT,
    WAITLISTED,
    state_files,
    write_state,
)

from fablabot import db, export_state, guild_config, import_state
from fablabot.services import formations, messages, op_expiries

DATA = Path("data")


@pytest.fixture(autouse=True)
def _closed() -> None:
    """Close the test's own database: these tests use the real path."""
    db.close_database()


def _folder() -> dict[str, bytes]:
    """Read every file under ``data/``."""
    return {path.relative_to(DATA).as_posix(): path.read_bytes() for path in DATA.rglob("*") if path.is_file()}


def _rows(table: str) -> int:
    """Count a table's rows in the imported database."""
    conn = sqlite3.connect(db.DATABASE_FILE)
    try:
        return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


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


def test_the_production_shapes_are_imported_whole() -> None:
    """Five servers, a server no longer allowed, the global section, 153 events, and no temporary admin file."""
    before = write_state()

    import_state.main([])

    assert {name: _folder()[name] for name in before} == before
    db.open_database()
    published = formations.current_published(FIRST_GUILD)
    assert published is not None
    assert [u["user_id"] for u in published.message.fms[0].registered_users] == list(REGISTERED)
    assert [u["user_id"] for u in published.message.fms[0].waitlisted_users] == list(WAITLISTED)
    assert guild_config.get_commands_channel_id(GONE_GUILD) == 3
    assert guild_config.get_legacy_log_channel_id() == 4
    assert guild_config.is_welcome_verify_enabled(FIRST_GUILD) is True
    assert formations.get_draft(GUILDS[1]) is not None
    assert list(messages.tracked_messages(FIRST_GUILD)) == [MESSAGE_ID + 1, MESSAGE_ID + 2]
    assert [r.action_type for r in messages.tracked_messages(FIRST_GUILD)[MESSAGE_ID + 1].reactions] == ["channel", "role_dm"]
    assert db.connection().execute("SELECT count(*) FROM reaction_event WHERE scope = 'formation'").fetchone()[0] == 153


def test_the_reminder_flags_come_through_as_the_files_hold_them() -> None:
    """A reminder already sent is not sent again after the import: the database holds the flags, not the defaults."""
    write_state()

    import_state.main([])

    db.open_database()
    published = formations.current_published(FIRST_GUILD)
    assert published is not None
    assert [(fm.notified_hour_before, fm.notified_at_start) for fm in published.message.fms] == list(REMINDERS_SENT)


def test_the_journal_keeps_the_order_of_the_file() -> None:
    """Equal times keep their file order, by row number."""
    write_state()

    import_state.main([])

    db.open_database()
    events = db.connection().execute("SELECT user_id, action FROM reaction_event ORDER BY id").fetchall()
    expected = state_files()["formations_state.json"][FIRST]["reactions_log"]
    assert [(row["user_id"], row["action"]) for row in events] == [(e["user_id"], e["action"]) for e in expected]


def _journal(scope: str, guild_id: int) -> list[dict[str, Any]]:
    """Read a history back from the database as lines with the keys a file line holds."""
    rows = db.connection().execute(
        "SELECT message_id, user_id, user_name, emoji, action, ts_iso FROM reaction_event "
        "WHERE scope = ? AND guild_id = ? ORDER BY id",
        (scope, guild_id),
    )
    return [dict(row) for row in rows]


def test_each_history_comes_through_with_every_value_of_its_file() -> None:
    """The rows hold what the lines hold, member names included, for both scopes: the read-back cannot see an earlier loss."""
    files = state_files()
    formation_lines = files["formations_state.json"][FIRST]["reactions_log"]
    assert all(line["user_name"] for line in formation_lines)
    named = {"message_id": MESSAGE_ID + 1, "emoji": "🧵", "action": "add", "ts_iso": "2026-10-21T09:00:00+02:00"}
    message_lines = [
        {**named, "user_id": 500000000000000011, "user_name": "name-500000000000000011"},
        {**named, "user_id": 500000000000000012, "user_name": "name-500000000000000012", "action": "remove"},
        {
            "message_id": MESSAGE_ID + 2,
            "user_id": 500000000000000011,
            "emoji": "✅",
            "action": "add",
            "ts_iso": "2026-10-22T09:00:00+00:00",
        },
    ]
    files["messages_state.json"][str(GUILDS[1])]["reactions_log"] = message_lines
    write_state(files)

    import_state.main([])

    db.open_database()
    assert _journal("formation", FIRST_GUILD) == formation_lines
    # A line from before names were kept has none: it reads as it did, a name of None.
    assert _journal("message", GUILDS[1]) == [*message_lines[:2], {**message_lines[2], "user_name": None}]


def test_the_gate_opens_on_the_exact_contract_it_reads() -> None:
    """One row per file present, the fingerprint of its bytes, and none for an absent file."""
    before = write_state()

    import_state.main([])

    db.open_database()
    rows = {row["file_name"]: row for row in db.connection().execute("SELECT * FROM state_import")}
    assert {name: row["sha256"] for name, row in rows.items()} == {
        name: hashlib.sha256(raw).hexdigest() for name, raw in before.items()
    }
    assert "temp_admin_state.json" not in rows
    assert all(row["imported_at"].endswith("+00:00") for row in rows.values())


def test_the_two_histories_stay_apart() -> None:
    """A tracked message's journal keeps its own scope, on its own server."""
    files = state_files()
    event = files["formations_state.json"][FIRST]["reactions_log"][0]
    files["messages_state.json"][str(GUILDS[1])]["reactions_log"] = [event]
    write_state(files)

    import_state.main([])

    db.open_database()
    rows = (
        db.connection().execute("SELECT guild_id, count(*) FROM reaction_event WHERE scope = 'message' GROUP BY 1").fetchall()
    )
    assert [tuple(row) for row in rows] == [(GUILDS[1], 1)]


def test_a_running_op_keeps_its_end() -> None:
    """When the file exists, each expiry comes through, per server."""
    write_state()
    write_state({"temp_admin_state.json": {FIRST: {"42": {"expires_at": "2026-11-02T12:00:00+00:00", "channel_id": 7}}}})

    import_state.main([])

    db.open_database()
    assert op_expiries.get(FIRST_GUILD, 42) == {"expires_at": "2026-11-02T12:00:00+00:00", "channel_id": 7}
    assert op_expiries.get(GUILDS[1], 42) is None


@pytest.mark.parametrize(
    "raw",
    [b"", b"{", b'{"a"', b"\xff\xfe", b"[]", b"null", b"1"],
    ids=["empty", "cut", "cut-key", "not-utf8", "list", "null", "number"],
)
@pytest.mark.parametrize("name", db.STATE_FILES)
@pytest.mark.parametrize("check", [True, False], ids=["check", "import"])
def test_a_corrupt_file_stops_before_anything_is_written(name: str, raw: bytes, *, check: bool) -> None:
    """A file the bot would have read as empty stops both commands, and the folder stays byte for byte."""
    write_state()
    (DATA / name).write_bytes(raw)
    before = _folder()

    with pytest.raises(SystemExit, match=f"Cannot import: .*{name}"):
        import_state.main(["--check"] if check else [])

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
        import_state.main([])

    assert _folder() == before
    assert "name-" not in str(refused.value)


def test_an_expiry_the_bot_could_not_read_stops() -> None:
    """A date with no timezone never came from the bot."""
    write_state()
    write_state({"temp_admin_state.json": {FIRST: {"42": {"expires_at": "2026-11-02T12:00:00", "channel_id": 7}}}})

    with pytest.raises(SystemExit, match="expires_at is not a date"):
        import_state.main([])


def test_a_missing_key_with_no_default_stops() -> None:
    """A registered member with no ID has no place in the schema."""
    files = state_files()
    del files["formations_state.json"][FIRST]["published"]["message"]["fms"][0]["registered_users"][0]["user_id"]
    write_state(files)

    with pytest.raises(SystemExit, match="key user_id is missing"):
        import_state.main([])


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
        import_state.main([])

    assert _folder() == {}


def test_a_second_run_changes_nothing_after_the_bot_has_moved_on() -> None:
    """Rows written since the import stay: a second run never replaces them."""
    write_state()
    import_state.main([])
    db.open_database()
    guild_config.set_log_channel_id(FIRST_GUILD, 99)
    db.close_database()
    before = db.DATABASE_FILE.read_bytes()

    import_state.main([])

    assert db.DATABASE_FILE.read_bytes() == before


def test_a_file_changed_since_the_import_is_refused() -> None:
    """Which side is newer is the operator's call, not the import's."""
    write_state()
    import_state.main([])
    before = db.DATABASE_FILE.read_bytes()
    write_state(_changed(("guild_config.json", FIRST, "log_channel_id"), 3))

    with pytest.raises(SystemExit, match=r"guild_config\.json changed since the import"):
        import_state.main([])

    assert db.DATABASE_FILE.read_bytes() == before


def test_a_database_the_bot_filled_is_refused() -> None:
    """A database that never imported holds someone's data: nothing is merged into it."""
    db.open_database()
    guild_config.set_log_channel_id(FIRST_GUILD, 99)
    db.close_database()
    write_state()
    before = db.DATABASE_FILE.read_bytes()

    with pytest.raises(SystemExit, match="already holds data no import wrote"):
        import_state.main([])

    assert db.DATABASE_FILE.read_bytes() == before


def test_a_write_the_database_refuses_leaves_no_row_and_no_mark() -> None:
    """The start gate stays closed, and the next run works."""
    write_state()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(import_state, "_write_events", lambda *_a: (_ for _ in ()).throw(sqlite3.OperationalError("full")))
        with pytest.raises(SystemExit, match="Cannot import: full"):
            import_state.main([])

    assert _rows("state_import") == _rows("fm_message") == 0
    with pytest.raises(db.StorageError, match="not imported"):
        db.open_database()
    import_state.main([])
    assert _rows("state_import") == 3


def test_what_does_not_read_back_the_same_is_not_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    """A lossy write stops the import inside its transaction."""
    write_state()
    real = export_state.snapshot

    def _lossy() -> dict[str, Any]:
        back = real()
        back["guild_config.json"].pop(str(GONE_GUILD))
        return back

    monkeypatch.setattr(import_state, "snapshot", _lossy)

    with pytest.raises(SystemExit, match=f"guild_config.json does not read back the same for {GONE_GUILD}"):
        import_state.main([])

    assert _rows("guild_settings") == 0


def test_the_export_gives_back_what_the_import_took(capsys: pytest.CaptureFixture[str]) -> None:
    """The way back of a rollback: the new files import into a new database with the same counts."""
    original = write_state()
    import_state.main([])
    capsys.readouterr()
    export_state.main(["data/exports/one"])
    archive = Path("archive")
    archive.mkdir()
    db.DATABASE_FILE.rename(archive / "fablabot.db")
    for name in original:
        (DATA / name).rename(archive / name)
    for path in Path("data/exports/one").iterdir():
        path.rename(DATA / path.name)

    import_state.main([])

    assert _rows("reaction_event") == 153
    assert _rows("registration") == 3
    assert _rows("guild_settings") == 6
    assert _rows("legacy_log_channel") == 1


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


def test_check_after_the_import_compares_the_database(capsys: pytest.CaptureFixture[str]) -> None:
    """The counts match the files, by the same query on both sides."""
    write_state()
    import_state.main([])
    capsys.readouterr()

    import_state.main(["--check"])

    assert "matches the files" in capsys.readouterr().out


def test_check_sees_a_database_that_moved_on() -> None:
    """Once the bot has run the numbers differ, and the message says that is normal only then."""
    write_state()
    import_state.main([])
    db.open_database()
    formations.save_draft(GONE_GUILD, formations.get_draft(FIRST_GUILD))  # type: ignore[arg-type]
    db.close_database()

    with pytest.raises(SystemExit, match=r"differs from formations_state\.json"):
        import_state.main(["--check"])


def test_check_works_on_a_read_only_copy() -> None:
    """A rehearsal on a copy of the archive needs no write access."""
    write_state()
    import_state.main([])
    DATA.chmod(0o500)
    try:
        import_state.main(["--check"])
    finally:
        DATA.chmod(0o700)


def test_the_bot_keeps_its_gate() -> None:
    """Only the import opens past it: the bot's own path takes no argument."""
    source = Path(db.__file__).with_name("main.py").read_text(encoding="utf-8")

    assert "db.open_database()" in source
    assert "gate=" not in source


def test_the_import_needs_no_token_and_loads_no_bot() -> None:
    """It runs with nothing but the data volume."""
    src = Path(db.__file__).resolve().parent.parent
    code = "import sys, fablabot.import_state, fablabot.export_state; sys.exit('fablabot.main' in sys.modules)"
    env = {key: value for key, value in os.environ.items() if key != "DISCORD_TOKEN_FILE"}

    done = subprocess.run([sys.executable, "-c", code], env={**env, "PYTHONPATH": str(src)}, capture_output=True, check=False)

    assert done.returncode == 0, done.stderr


# Runs in its own process. One page of cache forces each change to disk during the transaction, journal first.
CHILD = """
import os, signal, sys
from fablabot import db, import_state

kill_at = int(sys.argv[1])
seen = []
real = db.open_database

def traced(*args, **kwargs):
    real(*args, **kwargs)
    conn = db.connection()
    conn.execute("PRAGMA cache_size = 1")
    def trace(statement):
        seen.append(statement)
        if len(seen) == kill_at:
            os.kill(os.getpid(), signal.SIGKILL)
    conn.set_trace_callback(trace)

db.open_database = traced
if kill_at == 0:
    import_state._report = lambda _state: os.kill(os.getpid(), signal.SIGKILL)
import_state.main([])
print("statements", seen.index("COMMIT") + 1)
"""


def _child(kill_at: int) -> subprocess.CompletedProcess[str]:
    """Import in a child process, killed at the given statement when it gets there."""
    src = Path(db.__file__).resolve().parent.parent
    return subprocess.run(
        [sys.executable, "-c", CHILD, str(kill_at)],
        env={**os.environ, "PYTHONPATH": str(src)},
        capture_output=True,
        text=True,
        check=False,
    )


def test_a_kill_mid_import_leaves_the_gate_closed_and_the_next_run_finishes() -> None:
    """At each point the process dies by SIGKILL: no partial state, then the same result as a clean run."""
    before = write_state()
    clean = _child(10**9)
    assert clean.returncode == 0, clean.stderr
    total = int(clean.stdout.rsplit(maxsplit=1)[-1])
    expected = _content()
    db.DATABASE_FILE.unlink()

    journals = 0
    for kill_at in (2, total // 4, total // 2, total):
        killed = _child(kill_at)
        assert killed.returncode == -signal.SIGKILL, killed.stderr
        journals += db.DATABASE_FILE.with_name("fablabot.db-journal").exists()

        hot = db.DATABASE_FILE.with_name("fablabot.db-journal").exists()
        import_state.main(["--check"])
        assert db.DATABASE_FILE.with_name("fablabot.db-journal").exists() == hot
        with pytest.raises(db.StorageError, match="not imported"):
            db.open_database()
        db.close_database()
        assert _rows("state_import") == _rows("fm_message") == _rows("reaction_event") == 0
    assert journals > 0

    import_state.main([])

    assert _content() == expected
    assert {name: _folder()[name] for name in before} == before


def test_a_kill_right_after_the_commit_keeps_the_import() -> None:
    """Past the commit the import is done: the gate opens, and a second run has nothing to do."""
    write_state()
    assert _child(10**9).returncode == 0
    expected = _content()
    db.DATABASE_FILE.unlink()

    killed = _child(0)

    assert killed.returncode == -signal.SIGKILL, killed.stderr
    db.open_database()
    db.close_database()
    import_state.main([])
    assert _content() == expected


def _content() -> list[Any]:
    """Read every row of the state tables, ``state_import`` dates aside."""
    conn = sqlite3.connect(db.DATABASE_FILE)
    try:
        tables = ("guild_settings", "legacy_log_channel", "registration", "tracked_reaction", "reaction_event", "op_expiry")
        rows = [sorted(conn.execute(f"SELECT * FROM {table}").fetchall(), key=repr) for table in tables]
        rows.append(conn.execute("SELECT file_name, sha256 FROM state_import ORDER BY 1").fetchall())
        return rows
    finally:
        conn.close()
