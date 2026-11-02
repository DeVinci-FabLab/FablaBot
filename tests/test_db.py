"""The database opens itself on an empty folder, moves forward only, and keeps a transaction whole."""

from __future__ import annotations

import os
from pathlib import Path
import sqlite3
from typing import TYPE_CHECKING

import pytest

from fablabot import db
from fablabot.services import op_expiries

if TYPE_CHECKING:
    from types import ModuleType

DB_FILE = Path("elsewhere/test.db")


def _migrations(folder: Path, *sql: str) -> Path:
    """Write one numbered migration per statement."""
    folder.mkdir(exist_ok=True)
    for number, statement in enumerate(sql, start=1):
        (folder / f"{number:04d}_step.sql").write_text(statement, encoding="utf-8")
    return folder


def _version(path: Path) -> int:
    """Read the schema version of a database file."""
    conn = sqlite3.connect(path)
    try:
        return conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()


def _tables() -> set[str]:
    """List the tables of the open database."""
    return {row["name"] for row in db.connection().execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def test_an_empty_folder_gets_the_whole_schema() -> None:
    """A fresh start creates the folder and the database at the latest version."""
    db.open_database(DB_FILE)

    assert DB_FILE.exists()
    assert _version(DB_FILE) == 1
    assert {"guild_settings", "fm_message", "formation", "registration", "tracked_message", "reaction_event"} <= _tables()
    assert {"op_expiry", "state_import", "legacy_log_channel", "tracked_reaction"} <= _tables()


def test_the_connection_keeps_its_settings() -> None:
    """Foreign keys are on, the journal is a plain file, temporary data stays in memory."""
    conn = db.connection()

    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    assert conn.execute("PRAGMA temp_store").fetchone()[0] == 2


def test_no_journal_is_left_at_rest() -> None:
    """After a commit the database is one file."""
    db.open_database(DB_FILE)
    with db.transaction() as conn:
        conn.execute("INSERT INTO guild_settings (guild_id) VALUES (1)")

    assert [path.name for path in DB_FILE.parent.iterdir()] == [DB_FILE.name]


def test_a_transaction_commits_as_a_whole() -> None:
    """Rows written in a block are there after it."""
    with db.transaction() as conn:
        conn.execute("INSERT INTO guild_settings (guild_id) VALUES (1)")
        conn.execute("INSERT INTO guild_settings (guild_id) VALUES (2)")

    assert db.connection().execute("SELECT count(*) FROM guild_settings").fetchone()[0] == 2


def test_a_failed_block_leaves_nothing() -> None:
    """A block that raises takes back its earlier writes, and the next one works."""
    with pytest.raises(ZeroDivisionError), db.transaction() as conn:
        conn.execute("INSERT INTO guild_settings (guild_id) VALUES (1)")
        _ = 1 / 0

    assert db.connection().execute("SELECT count(*) FROM guild_settings").fetchone()[0] == 0
    with db.transaction() as conn:
        conn.execute("INSERT INTO guild_settings (guild_id) VALUES (1)")


def test_a_failed_commit_leaves_nothing_and_the_next_write_works() -> None:
    """A commit that SQLite refuses is rolled back: reads show the old state and later writes go through."""
    with pytest.raises(sqlite3.IntegrityError), db.transaction() as conn:
        conn.execute("PRAGMA defer_foreign_keys = ON")
        conn.execute("INSERT INTO registration VALUES (404, 0, 0, 1, 'a', '')")

    assert not db.connection().in_transaction
    assert db.connection().execute("SELECT count(*) FROM registration").fetchone()[0] == 0
    with db.transaction() as conn:
        conn.execute("INSERT INTO guild_settings (guild_id) VALUES (1)")
    assert db.connection().execute("SELECT count(*) FROM guild_settings").fetchone()[0] == 1


def test_a_block_whose_transaction_ended_by_itself_raises_its_own_error() -> None:
    """A conflict that SQLite settles by rolling back leaves the block's error, not a second one about the rollback."""
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"), db.transaction() as conn:
        conn.execute("INSERT INTO guild_settings (guild_id) VALUES (1)")
        conn.execute("INSERT OR ROLLBACK INTO guild_settings (guild_id) VALUES (1)")

    assert not db.connection().in_transaction
    assert db.connection().execute("SELECT count(*) FROM guild_settings").fetchone()[0] == 0


def test_a_block_cut_short_by_an_exit_signal_takes_back_its_writes() -> None:
    """An interrupt is no ``Exception``, and still ends the block with a rollback."""
    with pytest.raises(KeyboardInterrupt), db.transaction() as conn:
        conn.execute("INSERT INTO guild_settings (guild_id) VALUES (1)")
        raise KeyboardInterrupt

    assert not db.connection().in_transaction
    assert db.connection().execute("SELECT count(*) FROM guild_settings").fetchone()[0] == 0


def test_a_closed_database_says_so() -> None:
    """Using the database before it is opened is an error, not a silent new file."""
    db.close_database()

    with pytest.raises(RuntimeError, match="not open"):
        db.connection()


def test_a_failed_migration_changes_nothing(tmp_path: Path) -> None:
    """A migration that fails halfway keeps the version and undoes its own statements."""
    folder = _migrations(tmp_path / "m", "CREATE TABLE one (a);", "CREATE TABLE two (a); CREATE TABLE broken (;")
    with pytest.raises(db.StorageError):
        db.open_database(DB_FILE, migrations=folder)

    assert _version(DB_FILE) == 1
    conn = sqlite3.connect(DB_FILE)
    names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    conn.close()
    assert names == {"one"}


def test_a_migration_whose_transaction_ended_by_itself_is_refused_for_its_own_error(tmp_path: Path) -> None:
    """A file whose conflict rolls the transaction back is refused with that conflict."""
    folder = _migrations(
        tmp_path / "m", "CREATE TABLE one (a UNIQUE); INSERT INTO one VALUES (1); INSERT OR ROLLBACK INTO one VALUES (1);"
    )

    with pytest.raises(db.StorageError, match="UNIQUE"):
        db.open_database(DB_FILE, migrations=folder)

    assert _version(DB_FILE) == 0


PARENT_AND_CHILD = (
    "CREATE TABLE parent (id INTEGER PRIMARY KEY, label TEXT) STRICT;"
    "CREATE TABLE child (parent_id INTEGER NOT NULL REFERENCES parent (id) ON DELETE CASCADE) STRICT;"
)
REBUILD_PARENT = (
    "CREATE TABLE parent_new (id INTEGER PRIMARY KEY, label TEXT NOT NULL DEFAULT '') STRICT;"
    "INSERT INTO parent_new SELECT id, coalesce(label, '') FROM parent;"
    "DROP TABLE parent;"
    "ALTER TABLE parent_new RENAME TO parent;"
)


def test_rebuilding_a_table_keeps_the_rows_that_point_to_it(tmp_path: Path) -> None:
    """A migration that drops and recreates a parent table does not cascade into its children."""
    db.open_database(DB_FILE, migrations=_migrations(tmp_path / "m", PARENT_AND_CHILD))
    with db.transaction() as conn:
        conn.execute("INSERT INTO parent VALUES (1, 'a')")
        conn.execute("INSERT INTO child VALUES (1)")
    db.close_database()

    db.open_database(DB_FILE, migrations=_migrations(tmp_path / "m", PARENT_AND_CHILD, REBUILD_PARENT))

    assert db.connection().execute("SELECT count(*) FROM child").fetchone()[0] == 1
    assert db.connection().execute("PRAGMA foreign_keys").fetchone()[0] == 1
    with db.transaction() as conn:
        conn.execute("DELETE FROM parent")
    assert db.connection().execute("SELECT count(*) FROM child").fetchone()[0] == 0


def test_a_migration_that_orphans_rows_is_refused(tmp_path: Path) -> None:
    """A file that leaves a row pointing at nothing changes nothing and stops the start."""
    folder = _migrations(tmp_path / "m", PARENT_AND_CHILD, "INSERT INTO child VALUES (404);")
    db.open_database(DB_FILE, migrations=_migrations(tmp_path / "old", PARENT_AND_CHILD))
    db.close_database()
    before = DB_FILE.read_bytes()

    with pytest.raises(db.StorageError, match="child"):
        db.open_database(DB_FILE, migrations=folder)

    assert _version(DB_FILE) == 1
    assert DB_FILE.read_bytes() == before


def test_a_newer_database_stops_the_start(tmp_path: Path) -> None:
    """A database from a later release is refused and left as it was."""
    folder = _migrations(tmp_path / "m", "CREATE TABLE one (a);")
    db.open_database(DB_FILE, migrations=_migrations(tmp_path / "newer", "CREATE TABLE one (a);", "CREATE TABLE two (a);"))
    db.close_database()
    before = DB_FILE.read_bytes()

    with pytest.raises(db.StorageError, match="newer than this release"):
        db.open_database(DB_FILE, migrations=folder)

    assert DB_FILE.read_bytes() == before


def test_a_refused_start_leaves_the_database_closed(tmp_path: Path) -> None:
    """After a refusal nothing is published: the database reads as closed, not as a connection that was closed."""
    folder = _migrations(tmp_path / "m", "CREATE TABLE one (a);", "CREATE TABLE broken (;")

    with pytest.raises(db.StorageError):
        db.open_database(DB_FILE, migrations=folder)

    with pytest.raises(RuntimeError, match="not open"):
        db.connection()


def test_a_file_that_is_no_database_stops_the_start() -> None:
    """A damaged file is refused, never replaced."""
    DB_FILE.parent.mkdir(parents=True)
    DB_FILE.write_bytes(b"not a database, at least not one the bot can read")
    before = DB_FILE.read_bytes()

    with pytest.raises(db.StorageError, match="cannot be used"):
        db.open_database(DB_FILE)

    assert DB_FILE.read_bytes() == before


def test_a_directory_in_place_of_the_file_stops_the_start() -> None:
    """A database path that cannot be opened is a one-line refusal, not a traceback."""
    DB_FILE.mkdir(parents=True)

    with pytest.raises(db.StorageError, match="cannot be used"):
        db.open_database(DB_FILE)


def test_a_file_in_place_of_the_folder_stops_the_start() -> None:
    """A data folder that cannot be created is a one-line refusal."""
    DB_FILE.parent.write_bytes(b"")

    with pytest.raises(db.StorageError, match="cannot be used"):
        db.open_database(DB_FILE)


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads any file")
def test_an_unreadable_file_stops_the_start() -> None:
    """A database the bot has no right to read is refused with one line."""
    db.open_database(DB_FILE)
    db.close_database()
    DB_FILE.chmod(0o000)
    try:
        with pytest.raises(db.StorageError, match="cannot be used"):
            db.open_database(DB_FILE)
    finally:
        DB_FILE.chmod(0o644)


@pytest.mark.skipif(os.geteuid() == 0, reason="root writes anywhere")
def test_a_read_only_folder_stops_the_start_of_a_new_database() -> None:
    """A folder the bot cannot write to refuses a database that does not exist yet."""
    DB_FILE.parent.mkdir(parents=True)
    DB_FILE.parent.chmod(0o555)
    try:
        with pytest.raises(db.StorageError, match="cannot be used"):
            db.open_database(DB_FILE)
    finally:
        DB_FILE.parent.chmod(0o755)


@pytest.mark.skipif(os.geteuid() == 0, reason="root writes anywhere")
def test_a_read_only_file_stops_the_start() -> None:
    """A database the bot can read but not write is refused at start, not at the first reaction."""
    db.open_database(DB_FILE)
    db.close_database()
    DB_FILE.chmod(0o444)
    try:
        with pytest.raises(db.StorageError, match="cannot be used"):
            db.open_database(DB_FILE)
    finally:
        DB_FILE.chmod(0o644)


@pytest.mark.skipif(os.geteuid() == 0, reason="root writes anywhere")
def test_a_read_only_folder_stops_the_start_of_an_existing_database() -> None:
    """A folder that takes no journal file is refused at start."""
    db.open_database(DB_FILE)
    db.close_database()
    DB_FILE.parent.chmod(0o555)
    try:
        with pytest.raises(db.StorageError, match="cannot be used"):
            db.open_database(DB_FILE)
    finally:
        DB_FILE.parent.chmod(0o755)


def test_a_write_check_ended_by_sqlite_is_refused_for_its_own_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A write check that SQLite rolls back itself is reported with its cause, not as a rollback with nothing to roll back."""
    db.open_database(DB_FILE)
    db.close_database()
    connect = sqlite3.connect
    writing: list[str] = []

    def interrupt(database: Path, *, autocommit: bool) -> sqlite3.Connection:
        conn = connect(database, autocommit=autocommit)

        def note_the_check(sql: str) -> None:
            if sql.startswith("PRAGMA user_version = "):
                writing.append(sql)

        def stop_the_check() -> int:
            # A write that SQLite is told to stop takes its transaction back by itself.
            stop = bool(writing)
            writing.clear()
            return int(stop)

        conn.set_trace_callback(note_the_check)
        conn.set_progress_handler(stop_the_check, 1)
        return conn

    monkeypatch.setattr(sqlite3, "connect", interrupt)

    with pytest.raises(db.StorageError, match="interrupted"):
        db.open_database(DB_FILE)


def test_the_start_leaves_no_journal_and_no_open_transaction() -> None:
    """Checking that the database takes writes changes nothing and leaves nothing behind."""
    db.open_database(DB_FILE)

    assert not db.connection().in_transaction
    assert [path.name for path in DB_FILE.parent.iterdir()] == [DB_FILE.name]


def test_an_upgrade_copies_the_database_first(tmp_path: Path) -> None:
    """Before a schema change the bot keeps a copy of the data at the old version."""
    db.open_database(DB_FILE, migrations=_migrations(tmp_path / "m", "CREATE TABLE one (a);"))
    with db.transaction() as conn:
        conn.execute("INSERT INTO one VALUES ('kept')")
    db.close_database()

    db.open_database(DB_FILE, migrations=_migrations(tmp_path / "m", "CREATE TABLE one (a);", "CREATE TABLE two (a);"))

    [copy] = DB_FILE.parent.glob("backups/test-v1-*.db")
    conn = sqlite3.connect(copy)
    assert conn.execute("SELECT a FROM one").fetchall() == [("kept",)]
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    conn.close()
    assert _version(DB_FILE) == 2


def _failed_upgrade(tmp_path: Path) -> Path:
    """Return migrations whose second step fails, with a database at the first step."""
    db.open_database(DB_FILE, migrations=_migrations(tmp_path / "m", "CREATE TABLE one (a);"))
    db.close_database()
    return _migrations(tmp_path / "m", "CREATE TABLE one (a);", "CREATE TABLE one (a);")


def test_repeated_failed_upgrades_leave_one_copy(tmp_path: Path) -> None:
    """A start that keeps failing under a restart policy does not fill the disk with copies."""
    migrations = _failed_upgrade(tmp_path)

    for _ in range(3):
        with pytest.raises(db.StorageError):
            db.open_database(DB_FILE, migrations=migrations)

    copies = list(DB_FILE.parent.glob("backups/test-v1-*.db"))
    assert len(copies) == 1
    assert _version(copies[0]) == 1
    assert _version(DB_FILE) == 1


def test_a_database_changed_since_its_copy_is_copied_again(tmp_path: Path) -> None:
    """An older release may have written in between: the newer data gets its own copy, and the first one stays."""
    migrations = _failed_upgrade(tmp_path)
    with pytest.raises(db.StorageError):
        db.open_database(DB_FILE, migrations=migrations)
    [first] = DB_FILE.parent.glob("backups/test-v1-*.db")
    os.utime(first, (first.stat().st_atime - 10, first.stat().st_mtime - 10))

    with pytest.raises(db.StorageError):
        db.open_database(DB_FILE, migrations=migrations)

    assert first.exists()
    assert len(list(DB_FILE.parent.glob("backups/test-v1-*.db"))) == 2


def test_a_failed_copy_does_not_pass_for_a_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A copy that stops partway leaves no file under a copy's name, so the next start copies again."""
    db.open_database(DB_FILE, migrations=_migrations(tmp_path / "m", "CREATE TABLE one (a);"))
    db.close_database()
    migrations = _migrations(tmp_path / "m", "CREATE TABLE one (a);", "CREATE TABLE two (a);")
    real = db._copy_to

    def _cut_short(conn: sqlite3.Connection, target: Path) -> None:
        real(conn, target)
        raise sqlite3.OperationalError("disk full")

    monkeypatch.setattr(db, "_copy_to", _cut_short)
    with pytest.raises(db.StorageError):
        db.open_database(DB_FILE, migrations=migrations)
    assert list(DB_FILE.parent.glob("backups/test-v1-*.db")) == []

    monkeypatch.setattr(db, "_copy_to", real)
    db.open_database(DB_FILE, migrations=migrations)
    assert len(list(DB_FILE.parent.glob("backups/test-v1-*.db"))) == 1


def test_an_up_to_date_database_is_not_copied() -> None:
    """A restart at the same version writes no backup."""
    db.open_database(DB_FILE)
    db.open_database(DB_FILE)

    assert not (DB_FILE.parent / "backups").exists()


def test_a_new_database_is_not_copied() -> None:
    """There is nothing to keep before the first migration."""
    db.open_database(DB_FILE)

    assert not (DB_FILE.parent / "backups").exists()


def test_migrations_are_numbered_without_a_gap() -> None:
    """The file names count 1, 2, 3, as the version does."""
    numbers = [int(file.name[:4]) for file in sorted(db.MIGRATIONS_DIR.glob("*.sql"))]

    assert numbers == list(range(1, len(numbers) + 1))


def test_migrations_ship_with_the_package() -> None:
    """The image installs a wheel: the SQL files must be declared as package data."""
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"

    assert '"migrations/*.sql"' in pyproject.read_text(encoding="utf-8")


def test_no_transaction_can_span_an_await() -> None:
    """Storage code is synchronous, and commands and views never open a transaction themselves."""
    package = Path(db.__file__).parent
    storage = [package / "db.py", *package.glob("services/*.py")]
    callers = [*package.glob("cogs/*.py"), *package.glob("ui/*.py")]

    assert not [path.name for path in storage if "async " in path.read_text(encoding="utf-8")]
    assert not [path.name for path in callers if "transaction(" in path.read_text(encoding="utf-8")]


def test_an_emoji_can_appear_twice_in_a_draft() -> None:
    """The schema accepts what two open add forms can already store."""
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO fm_message (id, guild_id, header, role_id, intro, end_text, request_forms_url) "
            "VALUES (1, 1, '', 0, '', '', '')"
        )
        for position in (0, 1):
            conn.execute(
                "INSERT INTO formation (fm_message_id, position, emoji, name, trainer_mention, start_iso, duration, seats, "
                "description, excusable, notified_hour_before, notified_at_start) "
                "VALUES (1, ?, '🔧', '', '', '', '', 1, '', 1, 0, 0)",
                (position,),
            )

    assert db.connection().execute("SELECT count(*) FROM formation").fetchone()[0] == 2


def test_a_server_has_one_draft() -> None:
    """A second draft row for a server is refused by the schema."""
    insert = (
        "INSERT INTO fm_message (guild_id, header, role_id, intro, end_text, request_forms_url) VALUES (1, '', 0, '', '', '')"
    )
    db.connection().execute(insert)

    with pytest.raises(sqlite3.IntegrityError):
        db.connection().execute(insert)


def test_a_server_has_one_tracked_draft() -> None:
    """A second tracked draft on one server is refused, another server keeps its own."""
    insert = "INSERT INTO tracked_message (guild_id, content) VALUES (?, 'draft')"
    db.connection().execute(insert, (1,))

    with pytest.raises(sqlite3.IntegrityError):
        db.connection().execute(insert, (1,))
    db.connection().execute(insert, (2,))


def test_an_expiry_is_kept_per_server_and_member() -> None:
    """The same member on two servers keeps two expiries, and two members on one server keep two."""
    for guild_id, member_id in ((1, 7), (2, 7), (1, 8)):
        op_expiries.record(guild_id, member_id, {"expires_at": f"2026-11-01T0{guild_id}:00:00+00:00", "channel_id": 99})

    assert op_expiries.get(1, 7) == {"expires_at": "2026-11-01T01:00:00+00:00", "channel_id": 99}
    assert op_expiries.get(2, 7) == {"expires_at": "2026-11-01T02:00:00+00:00", "channel_id": 99}
    assert sorted(op_expiries.member_ids(1)) == [7, 8]


def test_a_text_value_in_a_number_column_is_refused() -> None:
    """Settings tables are strict: a channel ID that is text does not get stored."""
    with pytest.raises(sqlite3.IntegrityError):
        db.connection().execute("INSERT INTO guild_settings (guild_id, commands_channel_id) VALUES (1, 'general')")


def test_a_reaction_event_has_a_known_scope() -> None:
    """The history takes the two scopes it is read by and refuses any other."""
    insert = (
        "INSERT INTO reaction_event (scope, guild_id, message_id, user_id, emoji, action, ts_iso) "
        "VALUES (?, 1, 2, 3, '👍', 'add', '2026-11-01T00:00:00+00:00')"
    )
    for scope in ("formation", "message"):
        db.connection().execute(insert, (scope,))

    with pytest.raises(sqlite3.IntegrityError):
        db.connection().execute(insert, ("other",))


def test_a_reaction_event_is_an_add_or_a_remove() -> None:
    """The history takes the two actions it is read by and refuses any other."""
    insert = (
        "INSERT INTO reaction_event (scope, guild_id, message_id, user_id, emoji, action, ts_iso) "
        "VALUES ('message', 1, 2, 3, '👍', ?, '2026-11-01T00:00:00+00:00')"
    )
    for action in ("add", "remove"):
        db.connection().execute(insert, (action,))

    with pytest.raises(sqlite3.IntegrityError):
        db.connection().execute(insert, ("other",))


def test_the_welcome_check_setting_is_on_off_or_unset() -> None:
    """A server's welcome check is 0, 1 or empty, and nothing else."""
    insert = "INSERT INTO guild_settings (guild_id, welcome_verify_enabled) VALUES (?, ?)"
    for guild_id, value in ((1, 0), (2, 1), (3, None)):
        db.connection().execute(insert, (guild_id, value))

    with pytest.raises(sqlite3.IntegrityError):
        db.connection().execute(insert, (4, 2))


def test_every_table_is_strict() -> None:
    """No table takes a value of the wrong type, so a table added without STRICT is caught here."""
    loose = db.connection().execute(
        "SELECT name FROM pragma_table_list "
        "WHERE schema = 'main' AND type = 'table' AND strict = 0 AND name NOT LIKE 'sqlite_%'"
    )

    assert [row["name"] for row in loose] == []


def test_main_stops_before_login_when_the_database_cannot_be_used(fablabot_main: ModuleType) -> None:
    """A bad database ends the process with one line, before the bot is even built."""
    db.close_database()
    db.DATABASE_FILE.parent.mkdir(exist_ok=True)
    db.DATABASE_FILE.write_bytes(b"not a database, at least not one the bot can read")

    with pytest.raises(SystemExit) as stopped:
        fablabot_main.main()

    assert str(stopped.value).startswith("Cannot start: data/fablabot.db cannot be used")
