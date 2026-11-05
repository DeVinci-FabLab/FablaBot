"""The schema goes back one version without losing a registration or a temporary admin."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import shutil
import sqlite3
import tomllib

import pytest

from fablabot import db, downgrade_schema
from fablabot.helpers.constants import PARIS_TZ
from fablabot.models.formation import FmMessageDraft, Formation, PublishedMessage
from fablabot.services import attendance, formations

FILE = Path("elsewhere/test.db")
START = datetime(2026, 11, 5, 14, 0, tzinfo=PARIS_TZ)


def _first_release(tmp_path: Path) -> Path:
    """Return a folder holding the shipped first migration alone, as the previous release had."""
    folder = tmp_path / "first-release"
    folder.mkdir()
    shutil.copy(db.MIGRATIONS_DIR / "0001_initial.sql", folder)
    return folder


def _fill() -> None:
    """Open a database at the latest schema and write what the bot keeps: a sign-up, a temporary admin, a check, a count."""
    db.open_database(FILE)
    user = {"user_id": 1, "username": "one", "ts_iso": START.isoformat()}
    laser = Formation(
        emoji="A",
        name="Laser",
        trainer_mention="<@7>",
        start_iso=START.isoformat(),
        duration="2h",
        seats=3,
        registered_users=[user],
    )
    draft = FmMessageDraft(header="h", role_id=1, intro="i", fms=[laser], end="e")
    formations.save_published(5, PublishedMessage(message_id=50, channel_id=4, message=draft))
    attendance.open_appel(5, 50, 0, trainer_id=None, starts_at=START, ends_at=START + timedelta(hours=2))
    with db.transaction() as conn:
        conn.execute("INSERT INTO op_expiry VALUES (5, 2, '2026-11-05T10:00:00+01:00', 3)")
        conn.execute(
            "INSERT INTO training_summary VALUES (5, 50, 0, 'Laser', ?, 1, 0, 0, 1, 0, 'done')",
            (START.isoformat(),),
        )
    db.close_database()


def _read(sql: str) -> list[tuple[object, ...]]:
    """Read rows from the file without the bot's own opening, which would move the schema."""
    conn = sqlite3.connect(FILE)
    try:
        return [tuple(row) for row in conn.execute(sql)]
    finally:
        conn.close()


def _write(sql: str) -> None:
    """Write one row, given with a ``?`` for the start, without the bot's own opening."""
    conn = sqlite3.connect(FILE)
    try:
        with conn:
            conn.execute(sql, (START.isoformat(),))
    finally:
        conn.close()


def test_going_back_keeps_every_registration_and_temporary_admin() -> None:
    """The attendance objects go, the version is 1, and the sign-ups and the temporary admin stay."""
    _fill()

    assert downgrade_schema.downgrade(FILE) == 2
    assert downgrade_schema.downgrade(FILE) == 1

    assert _read("PRAGMA user_version") == [(1,)]
    names = {row[0] for row in _read("SELECT name FROM sqlite_master")}
    assert names.isdisjoint({"appel", "appel_present", "appel_alert", "training_summary", "training_month"})
    assert _read("SELECT guild_id, member_id, channel_id FROM op_expiry") == [(5, 2, 3)]
    assert _read("SELECT user_id FROM registration") == [(1,)]


def test_going_back_one_step_keeps_the_counts_and_fills_the_hidden_ones_with_zero() -> None:
    """The way back from the threshold step keeps every row; counts not kept read 0, as the old schema needs a value."""
    _fill()
    _write("INSERT INTO training_summary VALUES (5, 51, 0, 'Small', ?, 2, 0, 0, NULL, NULL, 'done')")

    assert downgrade_schema.downgrade(FILE) == 2

    rows = _read("SELECT name, registered, present, absent FROM training_summary ORDER BY name")
    assert rows == [("Laser", 1, 1, 0), ("Small", 2, 0, 0)]
    assert _read("SELECT trainings, present FROM training_month") == [(2, 1)]
    with pytest.raises(sqlite3.IntegrityError):
        _write("INSERT INTO training_summary VALUES (5, 52, 0, 'x', ?, 2, 0, 0, NULL, NULL, 'done')")


def test_the_previous_release_opens_the_file_and_the_next_start_moves_it_forward(tmp_path: Path) -> None:
    """The first release's migrations accept the file as theirs, and the latest ones bring it back to 3."""
    _fill()
    downgrade_schema.downgrade(FILE)
    downgrade_schema.downgrade(FILE)

    db.open_database(FILE, migrations=_first_release(tmp_path))
    db.close_database()
    db.open_database(FILE)

    assert _read("PRAGMA user_version") == [(3,)]
    assert _read("SELECT user_id FROM registration") == [(1,)]
    assert _read("SELECT COUNT(*) FROM appel") == [(0,)]
    assert _read("PRAGMA foreign_key_check") == []


def test_a_file_at_the_first_schema_has_nothing_to_go_back_to() -> None:
    """A third run refuses and changes nothing."""
    _fill()
    downgrade_schema.downgrade(FILE)
    downgrade_schema.downgrade(FILE)
    before = FILE.read_bytes()

    with pytest.raises(db.StorageError, match="already at schema version 1"):
        downgrade_schema.downgrade(FILE)

    assert FILE.read_bytes() == before


def test_a_missing_file_is_not_created() -> None:
    """A wrong volume has no database: the command stops and writes nothing."""
    with pytest.raises(db.StorageError, match="does not exist"):
        downgrade_schema.downgrade(FILE)

    assert not FILE.exists()
    assert not FILE.parent.exists()


def test_a_version_with_no_way_back_is_refused(tmp_path: Path) -> None:
    """Without a step for the file's version, nothing moves."""
    folder = tmp_path / "m"
    (folder / "down").mkdir(parents=True)
    (folder / "0001_a.sql").write_text("CREATE TABLE one (a);", encoding="utf-8")
    (folder / "0002_b.sql").write_text("CREATE TABLE two (a);", encoding="utf-8")
    db.open_database(FILE, migrations=folder)
    db.close_database()

    with pytest.raises(db.StorageError, match="no way back from schema version 2"):
        downgrade_schema.downgrade(FILE, migrations=folder)

    assert _read("PRAGMA user_version") == [(2,)]


def test_a_failing_step_is_taken_back_whole(tmp_path: Path) -> None:
    """A step that fails halfway leaves the tables and the version as they were."""
    folder = tmp_path / "m"
    (folder / "down").mkdir(parents=True)
    (folder / "0001_a.sql").write_text("CREATE TABLE one (a);", encoding="utf-8")
    (folder / "0002_b.sql").write_text("CREATE TABLE two (a);", encoding="utf-8")
    (folder / "down" / "0002_b.sql").write_text("DROP TABLE two; DROP TABLE missing;", encoding="utf-8")
    db.open_database(FILE, migrations=folder)
    db.close_database()

    with pytest.raises(db.StorageError, match="cannot be used"):
        downgrade_schema.downgrade(FILE, migrations=folder)

    assert _read("PRAGMA user_version") == [(2,)]
    assert {row[0] for row in _read("SELECT name FROM sqlite_master")} >= {"one", "two"}


def test_the_command_says_what_it_did(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """It names the versions it moved between."""
    monkeypatch.setattr(db, "DATABASE_FILE", FILE)
    _fill()

    downgrade_schema.main([])

    assert "from schema version 3 to 2" in capsys.readouterr().out


def test_the_command_takes_no_argument_and_reports_a_refusal(capsys: pytest.CaptureFixture[str]) -> None:
    """A stray argument gives the usage; a missing database gives one line starting with the verb."""
    with pytest.raises(SystemExit, match="Usage"):
        downgrade_schema.main(["x"])
    with pytest.raises(SystemExit, match=r"Cannot go back: data/fablabot\.db does not exist"):
        downgrade_schema.main([])

    assert capsys.readouterr().out == ""


def test_every_schema_step_after_the_first_has_a_way_back_that_the_image_ships() -> None:
    """A release that adds a migration must add its step down, and the package data must carry it."""
    root = Path(__file__).resolve().parent.parent
    steps = sorted(path.name for path in db.MIGRATIONS_DIR.glob("*.sql"))[1:]
    down = sorted(path.name for path in (db.MIGRATIONS_DIR / "down").glob("*.sql"))
    package_data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["setuptools"]["package-data"]

    assert down == steps
    assert "migrations/down/*.sql" in package_data["fablabot"]
