"""The export writes the database back in the shapes older releases read, into a new folder."""

from __future__ import annotations

from datetime import datetime, timedelta
import json
from pathlib import Path
import stat
from typing import Any

from conftest import store_settings
import pytest
from state_shapes import FIRST_GUILD, MESSAGE_ID, state_files

from fablabot import db, export_state
from fablabot.helpers.constants import PARIS_TZ
from fablabot.models import FmMessageDraft, PublishedMessage, TemplateFields, TrackedMessage
from fablabot.services import attendance, catalogue, formations, messages, op_expiries

EXPORT = Path("data/exports/one")
START = datetime(2026, 11, 5, 14, 0, tzinfo=PARIS_TZ)


def _load(folder: Path) -> dict[str, Any]:
    """Read every JSON file of a folder."""
    return {path.name: json.loads(path.read_text(encoding="utf-8")) for path in folder.glob("*.json")}


def _fill(files: dict[str, Any]) -> None:
    """Store the production shapes through the services, as the bot would."""
    store_settings(files["guild_config.json"])
    for key, entry in files["formations_state.json"].items():
        formations.save_draft(int(key), FmMessageDraft.from_dict(entry["draft"]))
        formations.save_published(int(key), PublishedMessage.from_dict(entry["published"]))
        with db.transaction() as conn:
            for event in entry["reactions_log"]:
                conn.execute(
                    "INSERT INTO reaction_event (scope, guild_id, message_id, user_id, user_name, emoji, action, ts_iso) "
                    "VALUES ('formation', :guild, :message_id, :user_id, :user_name, :emoji, :action, :ts_iso)",
                    {"guild": int(key), **event},
                )
    for key, entry in files["messages_state.json"].items():
        for tracked in entry["tracked"].values():
            messages.save_tracked(int(key), TrackedMessage.from_dict(tracked))


@pytest.fixture
def filled() -> dict[str, Any]:
    """Fill ``data/fablabot.db`` and close it: the command opens it itself.

    Returns:
        dict[str, Any]: What it holds, as the JSON files.
    """
    files = state_files()
    db.open_database()
    _fill(files)
    db.close_database()
    return files


def test_the_export_writes_the_shapes_of_the_json_files(filled: dict[str, Any]) -> None:
    """Every file comes back in its old shape, a server with nothing tracked left out, and no temporary admin an empty file."""
    export_state.main([str(EXPORT)])

    exported = _load(EXPORT)
    assert exported["guild_config.json"] == filled["guild_config.json"]
    assert exported["formations_state.json"] == filled["formations_state.json"]
    assert exported["messages_state.json"] == {k: v for k, v in filled["messages_state.json"].items() if v["tracked"]}
    assert exported["temp_admin_state.json"] == {}


def test_the_export_gives_a_running_op_and_a_message_draft(filled: dict[str, Any]) -> None:
    """The two shapes production holds none of today go through as well."""
    assert filled
    db.open_database()
    op_expiries.record(FIRST_GUILD, 42, {"expires_at": "2026-11-02T12:00:00+00:00", "channel_id": 7})
    messages.save_draft(FIRST_GUILD, messages.MessageDraft(content="c", reactions=[]))
    db.close_database()

    export_state.main([str(EXPORT)])

    exported = _load(EXPORT)
    assert exported["temp_admin_state.json"] == {
        str(FIRST_GUILD): {"42": {"expires_at": "2026-11-02T12:00:00+00:00", "channel_id": 7}}
    }
    assert exported["messages_state.json"][str(FIRST_GUILD)]["draft"] == {"content": "c", "reactions": []}


@pytest.mark.usefixtures("filled")
def test_the_export_refuses_a_folder_that_exists() -> None:
    """A folder already there is left alone."""
    EXPORT.mkdir(parents=True)

    with pytest.raises(SystemExit, match="already exists"):
        export_state.main([str(EXPORT)])

    assert list(EXPORT.iterdir()) == []


@pytest.mark.usefixtures("filled")
def test_the_export_folder_is_private_and_has_no_partial_left() -> None:
    """It holds member IDs: only the bot's user reads it."""
    export_state.main([str(EXPORT)])

    assert stat.S_IMODE(EXPORT.stat().st_mode) == 0o700
    assert [path.name for path in EXPORT.parent.iterdir()] == [EXPORT.name]


@pytest.mark.usefixtures("filled")
def test_an_interrupted_export_leaves_no_folder_of_that_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """A folder under its final name is always whole."""
    monkeypatch.setattr(export_state.json, "dumps", lambda *_a, **_k: (_ for _ in ()).throw(OSError("disk full")))

    with pytest.raises(SystemExit, match="Cannot export: disk full"):
        export_state.main([str(EXPORT)])

    assert not EXPORT.exists()


@pytest.mark.usefixtures("filled")
def test_the_export_says_what_has_no_place_in_the_files(capsys: pytest.CaptureFixture[str]) -> None:
    """A replaced message stays in the database, the newest goes to the file, and the operator is told."""
    db.open_database()
    newer = PublishedMessage(message_id=MESSAGE_ID + 5, channel_id=1, message=FmMessageDraft("h", 1, "i", [], "e"))
    formations.save_published(FIRST_GUILD, newer)
    db.close_database()

    export_state.main([str(EXPORT)])

    assert "1 replaced message(s)" in capsys.readouterr().out
    assert _load(EXPORT)["formations_state.json"][str(FIRST_GUILD)]["published"]["message_id"] == MESSAGE_ID + 5


@pytest.mark.usefixtures("filled")
def test_the_export_counts_the_attendance_data_that_stays_in_the_database(capsys: pytest.CaptureFixture[str]) -> None:
    """Attendance checks and training counts have no JSON file: the operator is told how many stay behind."""
    db.open_database()
    attendance.open_appel(FIRST_GUILD, MESSAGE_ID, 0, trainer_id=None, starts_at=START, ends_at=START + timedelta(hours=2))
    with db.transaction() as conn:
        conn.executemany(
            "INSERT INTO training_summary (guild_id, message_id, position, name, starts_at, registered, waitlisted, withdrawn, "
            "present, absent, outcome) VALUES (?, ?, ?, 'Laser', ?, 3, 0, 0, 2, 1, 'done')",
            [(FIRST_GUILD, 1, 0, START.isoformat()), (FIRST_GUILD, 1, 1, START.isoformat())],
        )
    db.close_database()

    export_state.main([str(EXPORT)])

    out = capsys.readouterr().out
    assert "1 attendance check and 2 training counts have no place in these files" in out
    assert "stay in the database" in out
    assert "replaced" not in out
    assert "attendance" not in json.dumps(_load(EXPORT))


@pytest.mark.usefixtures("filled")
def test_the_export_counts_the_templates_that_stay_in_the_database(capsys: pytest.CaptureFixture[str]) -> None:
    """Training templates have no JSON file: the operator is told how many stay behind."""
    db.open_database()
    catalogue.create_template(FIRST_GUILD, TemplateFields("Laser", "1h", 6, "", True))
    db.close_database()

    export_state.main([str(EXPORT)])

    out = capsys.readouterr().out
    assert "1 training template has no place in these files; they stay in the database." in out
    assert "Laser" not in json.dumps(_load(EXPORT))


@pytest.mark.usefixtures("filled")
def test_the_export_stays_silent_when_there_is_no_attendance_data(capsys: pytest.CaptureFixture[str]) -> None:
    """With no check and no count, the export prints no word about attendance."""
    export_state.main([str(EXPORT)])

    assert "attendance" not in capsys.readouterr().out


@pytest.mark.usefixtures("filled")
def test_the_export_writes_nothing_into_the_database() -> None:
    """The file stays byte for byte."""
    before = db.DATABASE_FILE.read_bytes()

    export_state.main([str(EXPORT)])

    assert db.DATABASE_FILE.read_bytes() == before


def test_the_export_needs_a_folder() -> None:
    """No folder, no guess."""
    with pytest.raises(SystemExit, match="Usage"):
        export_state.main([])


def test_the_export_refuses_a_volume_with_no_database(capsys: pytest.CaptureFixture[str]) -> None:
    """A wrong or empty volume has no database: the command creates nothing and says so, as the import does."""
    with pytest.raises(SystemExit, match=r"Cannot export: .*fablabot\.db") as stopped:
        export_state.main([str(EXPORT)])

    assert stopped.value.code != 0
    assert not db.DATABASE_FILE.exists()
    assert not EXPORT.exists()
    assert capsys.readouterr().out == ""


def test_the_export_refuses_a_database_holding_nothing() -> None:
    """A database no import and no row filled is what an empty volume leaves behind: no empty files are written."""
    db.open_database()
    db.close_database()

    with pytest.raises(SystemExit, match=r"Cannot export: .*holds no data") as stopped:
        export_state.main([str(EXPORT)])

    assert stopped.value.code != 0
    assert not EXPORT.exists()
    assert not EXPORT.with_name(f"{EXPORT.name}.partial").exists()


def test_the_export_serves_a_database_the_bot_filled_without_an_import() -> None:
    """A new instance never imported anything: its rows are enough."""
    db.open_database()
    op_expiries.record(FIRST_GUILD, 42, {"expires_at": "2026-11-02T12:00:00+00:00", "channel_id": 7})
    db.close_database()

    export_state.main([str(EXPORT)])

    assert _load(EXPORT)["temp_admin_state.json"][str(FIRST_GUILD)]["42"]["channel_id"] == 7
