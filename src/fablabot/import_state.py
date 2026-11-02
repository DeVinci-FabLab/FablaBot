"""Move the JSON state of older releases into the database, with the bot stopped.

``python -m fablabot.import_state --check`` reads the files and reports what they hold, writing nothing;
``python -m fablabot.import_state`` imports them. The files stay untouched, and the import happens at most once.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from types import MappingProxyType, NoneType
from typing import TYPE_CHECKING, Any, Final, NamedTuple, NoReturn

from fablabot import db
from fablabot.export_state import FORMATIONS_FILE, GUILD_CONFIG_FILE, LEGACY_SECTION, MESSAGES_FILE, TEMP_ADMIN_FILE, snapshot
from fablabot.models import FmMessageDraft, MessageDraft, OpExpiry, PublishedMessage, TrackedMessage
from fablabot.services import formations, messages

if TYPE_CHECKING:
    from collections.abc import Mapping

type State = dict[str, dict[str, Any]]

_MAX_ID: Final = 2**63 - 1  # SQLite's largest integer
_INT: Final = (int,)
_TEXT: Final = (str,)
_FLAG: Final = (bool,)
_MAYBE_INT: Final = (int, NoneType)
_MAYBE_TEXT: Final = (str, NoneType)


class Fields(NamedTuple):
    """An object: the keys it must have and those it may have, any other stopping the import."""

    required: Mapping[str, Any]
    optional: Mapping[str, Any]


class Items(NamedTuple):
    """A list of one shape."""

    item: Any


class Keyed(NamedTuple):
    """An object keyed by Discord ID, with the named sections it may hold besides."""

    item: Any
    sections: Mapping[str, Any] = MappingProxyType({})


class OneOf(NamedTuple):
    """A text from a closed list."""

    allowed: tuple[str, ...]


_USER: Final = Fields({"user_id": _INT, "username": _TEXT, "ts_iso": _TEXT}, {})
_FORMATION: Final = Fields(
    {"emoji": _TEXT, "name": _TEXT, "trainer_mention": _TEXT, "start_iso": _TEXT, "duration": _TEXT, "seats": _INT},
    {
        "description": _TEXT,
        "excusable": _FLAG,
        "registered_users": Items(_USER),
        "waitlisted_users": Items(_USER),
        "notified_hour_before": _FLAG,
        "notified_at_start": _FLAG,
    },
)
_MESSAGE: Final = Fields(
    {},
    {"header": _TEXT, "role_id": _INT, "intro": _TEXT, "fms": Items(_FORMATION), "end": _TEXT, "request_forms_url": _TEXT},
)
_PUBLISHED: Final = Fields({"message_id": _INT, "channel_id": _INT, "message": _MESSAGE}, {})
_EVENT: Final = Fields(
    {"message_id": _INT, "user_id": _INT, "emoji": _TEXT, "action": OneOf(("add", "remove")), "ts_iso": _TEXT},
    {"user_name": _MAYBE_TEXT},
)
_LINK: Final = Fields(
    {"emoji": _TEXT, "action_type": OneOf(("channel", "user_dm", "role_dm")), "message_content": _TEXT},
    {"target_id": _MAYBE_INT, "target_name": _MAYBE_TEXT, "message_id": _MAYBE_INT},
)
_TRACKED: Final = Fields(
    {"message_id": _INT, "channel_id": _INT, "content": _TEXT},
    {"reactions": Items(_LINK), "created_by": _MAYBE_INT, "created_at_iso": _MAYBE_TEXT},
)
_SHAPES: Final = {
    GUILD_CONFIG_FILE: Keyed(
        Fields({}, {"commands_channel_id": _INT, "log_channel_id": _INT, "welcome_verify_enabled": _FLAG}),
        {LEGACY_SECTION: Fields({"log_channel_id": _INT}, {})},
    ),
    FORMATIONS_FILE: Keyed(Fields({}, {"draft": _MESSAGE, "published": _PUBLISHED, "reactions_log": Items(_EVENT)})),
    MESSAGES_FILE: Keyed(
        Fields(
            {},
            {
                "draft": Fields({}, {"content": _TEXT, "reactions": Items(_LINK)}),
                "tracked": Keyed(_TRACKED),
                "reactions_log": Items(_EVENT),
            },
        )
    ),
    TEMP_ADMIN_FILE: Keyed(Keyed(Fields({"expires_at": _TEXT, "channel_id": _INT}, {}))),
}
_FIELDS: Final = (
    "settings",
    "drafts",
    "published",
    "formations",
    "registered",
    "waitlisted",
    "fm_events",
    "tracked",
    "links",
    "msg_events",
    "ops",
)


class StateFileError(Exception):
    """The import stops; the message is one line for the operator, with no value from the files."""


def _refuse(where: str, problem: str) -> NoReturn:
    """Stop on a place in a file, never on a value."""
    msg = f"{where}: {problem}."
    raise StateFileError(msg)


def _check_keyed(where: str, value: Any, shape: Keyed) -> None:
    """Check an object keyed by ID, entry by entry."""
    if not isinstance(value, dict):
        _refuse(where, "expected an object")
    for key, entry in value.items():
        if key not in shape.sections and not (key.isascii() and key.isdigit() and int(key) <= _MAX_ID):
            _refuse(where, f"key {key!r} is not an ID")
        _check(f"{where}/{key}", entry, shape.sections.get(key, shape.item))


def _check_fields(where: str, value: Any, shape: Fields) -> None:
    """Check an object key by key."""
    if not isinstance(value, dict):
        _refuse(where, "expected an object")
    for key in shape.required:
        if key not in value:
            _refuse(where, f"key {key} is missing")
    for key, entry in value.items():
        if key not in shape.required and key not in shape.optional:
            _refuse(where, f"unknown key {key!r}")
        _check(f"{where}/{key}", entry, shape.required.get(key) or shape.optional[key])


def _check(where: str, value: Any, shape: Any) -> None:
    """Check a value against its shape, walking down; ``where`` is the path shown in the message."""
    match shape:
        case Keyed():
            _check_keyed(where, value, shape)
        case Fields():
            _check_fields(where, value, shape)
        case Items(item):
            if not isinstance(value, list):
                _refuse(where, "expected a list")
            for index, entry in enumerate(value):
                _check(f"{where}/{index}", entry, item)
        case OneOf(allowed):
            if value not in allowed:
                _refuse(where, f"expected one of {', '.join(allowed)}")
        case _:
            if type(value) not in shape or (type(value) is int and abs(value) > _MAX_ID):
                _refuse(where, "unexpected value")


def _read_trainings(data: dict[str, Any]) -> dict[str, Any]:
    """Give the trainings the defaults older files lack; a server holding nothing has no row to keep."""
    state: dict[str, Any] = {}
    for key, raw in data.items():
        entry: dict[str, Any] = {}
        if "draft" in raw:
            entry["draft"] = FmMessageDraft.from_dict(raw["draft"]).to_dict()
        if "published" in raw:
            entry["published"] = PublishedMessage.from_dict(raw["published"]).to_dict()
        entry["reactions_log"] = [{"user_name": None, **event} for event in raw.get("reactions_log", [])]
        if len(entry) > 1 or entry["reactions_log"]:
            state[key] = entry
    return state


def _read_tracked(data: dict[str, Any]) -> dict[str, Any]:
    """Give the tracked messages the defaults older files lack; a server holding nothing has no row to keep."""
    state: dict[str, Any] = {}
    for key, raw in data.items():
        entry: dict[str, Any] = {}
        if "draft" in raw:
            entry["draft"] = MessageDraft.from_dict(raw["draft"]).to_dict()
        entry["tracked"] = {}
        for message_key, value in raw.get("tracked", {}).items():
            if int(message_key) != value["message_id"]:
                _refuse(f"{MESSAGES_FILE}/{key}/tracked/{message_key}", "the key and message_id differ")
            entry["tracked"][message_key] = TrackedMessage.from_dict(value).to_dict()
        entry["reactions_log"] = [{"user_name": None, **event} for event in raw.get("reactions_log", [])]
        if "draft" in entry or entry["tracked"] or entry["reactions_log"]:
            state[key] = entry
    return state


def _read_ops(data: dict[str, Any]) -> dict[str, Any]:
    """Keep each running op's end: a date the bot could not read is no state to carry over."""
    for key, entries in data.items():
        for member_key, expiry in entries.items():
            try:
                OpExpiry.from_dict(expiry)
            except ValueError:
                _refuse(f"{TEMP_ADMIN_FILE}/{key}/{member_key}", "expires_at is not a date with a timezone")
    return {key: entries for key, entries in data.items() if entries}


def read_files(folder: Path) -> dict[str, bytes]:
    """Read the state files of a folder; a file that is absent is simply not there.

    Args:
        folder (Path): The folder.

    Returns:
        dict[str, bytes]: The bytes of each file found.

    Raises:
        StateFileError: If a file cannot be read, or none is there.
    """
    files: dict[str, bytes] = {}
    for name in db.STATE_FILES:
        path = folder / name
        try:
            if path.exists():
                files[name] = path.read_bytes()
        except OSError as error:
            msg = f"{path.as_posix()} cannot be read: {error.strerror}."
            raise StateFileError(msg) from error
    if not files:
        msg = f"no state file in {folder.as_posix()}/: check the volume."
        raise StateFileError(msg)
    return files


def parse(files: Mapping[str, bytes]) -> State:
    """Read the files strictly into the shape ``snapshot`` returns: the bytes given are the bytes judged.

    Args:
        files (Mapping[str, bytes]): The bytes of each file found.

    Returns:
        State: The content of every state file, a missing one empty.

    Raises:
        StateFileError: If a file is not valid JSON, or holds anything unexpected.
    """
    state: State = {}
    for name, shape in _SHAPES.items():
        try:
            data: Any = json.loads(files[name].decode("utf-8")) if name in files else {}
        except ValueError as error:
            msg = f"{name} is not valid JSON."
            raise StateFileError(msg) from error
        _check(name, data, shape)
        state[name] = data
    state[FORMATIONS_FILE] = _read_trainings(state[FORMATIONS_FILE])
    state[MESSAGES_FILE] = _read_tracked(state[MESSAGES_FILE])
    state[TEMP_ADMIN_FILE] = _read_ops(state[TEMP_ADMIN_FILE])
    return state


def _write_events(conn: sqlite3.Connection, scope: str, guild_id: int, events: list[dict[str, Any]]) -> None:
    """Write a whole history in file order: the bot's own retention decides later, as it always did."""
    conn.executemany(
        "INSERT INTO reaction_event (scope, guild_id, message_id, user_id, user_name, emoji, action, ts_iso) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (scope, guild_id, e["message_id"], e["user_id"], e["user_name"], e["emoji"], e["action"], e["ts_iso"])
            for e in events
        ],
    )


def _write(conn: sqlite3.Connection, state: State) -> None:
    """Write every row, inside the caller's transaction."""
    for key, entry in state[GUILD_CONFIG_FILE].items():
        if key == LEGACY_SECTION:
            conn.execute("INSERT INTO legacy_log_channel (singleton, channel_id) VALUES (1, ?)", (entry["log_channel_id"],))
            continue
        enabled = entry.get("welcome_verify_enabled")
        conn.execute(
            "INSERT INTO guild_settings (guild_id, commands_channel_id, log_channel_id, welcome_verify_enabled) "
            "VALUES (?, ?, ?, ?)",
            (
                int(key),
                entry.get("commands_channel_id"),
                entry.get("log_channel_id"),
                None if enabled is None else int(enabled),
            ),
        )
    for key, entry in state[FORMATIONS_FILE].items():
        guild_id = int(key)
        if "draft" in entry:
            formations.write_message(conn, guild_id, None, None, FmMessageDraft.from_dict(entry["draft"]))
        if "published" in entry:
            published = PublishedMessage.from_dict(entry["published"])
            formations.write_message(conn, guild_id, published.message_id, published.channel_id, published.message)
        _write_events(conn, "formation", guild_id, entry["reactions_log"])
    for key, entry in state[MESSAGES_FILE].items():
        guild_id = int(key)
        if "draft" in entry:
            messages.write_draft(conn, guild_id, MessageDraft.from_dict(entry["draft"]))
        for tracked in entry["tracked"].values():
            messages.write_tracked(conn, guild_id, TrackedMessage.from_dict(tracked))
        _write_events(conn, "message", guild_id, entry["reactions_log"])
    for key, entries in state[TEMP_ADMIN_FILE].items():
        conn.executemany(
            "INSERT INTO op_expiry (guild_id, member_id, expires_at, channel_id) VALUES (?, ?, ?, ?)",
            [(int(key), int(member), e["expires_at"], e["channel_id"]) for member, e in entries.items()],
        )


def _write_and_verify(conn: sqlite3.Connection, state: State) -> None:
    """Write the state and read it back, inside the caller's transaction: what does not come back the same is not kept.

    Args:
        conn (sqlite3.Connection): The connection, inside a transaction.
        state (State): What the files hold.

    Raises:
        StateFileError: If the database does not give back what the files hold.
    """
    _write(conn, state)
    back = snapshot()
    for name, content in state.items():
        if back[name] != content:
            different = sorted(key for key in {*content, *back[name]} if content.get(key) != back[name].get(key))
            msg = f"{name} does not read back the same for {', '.join(different)}; nothing was written."
            raise StateFileError(msg)


def counts(state: State) -> dict[str, dict[str, int]]:
    """Count what the state holds, per server.

    Args:
        state (State): The state.

    Returns:
        dict[str, dict[str, int]]: The counts of each server, every field present.
    """
    found: dict[str, dict[str, int]] = {}

    def tally(server: str, **fields: int) -> None:
        line = found.setdefault(server, dict.fromkeys(_FIELDS, 0))
        for field, count in fields.items():
            line[field] += count

    for key in state[GUILD_CONFIG_FILE]:
        tally(key, settings=1)
    for key, entry in state[FORMATIONS_FILE].items():
        fms = entry["published"]["message"]["fms"] if "published" in entry else []
        tally(
            key,
            drafts="draft" in entry,
            published="published" in entry,
            formations=len(fms),
            registered=sum(len(fm["registered_users"]) for fm in fms),
            waitlisted=sum(len(fm["waitlisted_users"]) for fm in fms),
            fm_events=len(entry["reactions_log"]),
        )
    for key, entry in state[MESSAGES_FILE].items():
        tracked = entry["tracked"].values()
        tally(
            key,
            drafts="draft" in entry,
            tracked=len(tracked),
            links=sum(len(message["reactions"]) for message in tracked),
            msg_events=len(entry["reactions_log"]),
        )
    for key, entries in state[TEMP_ADMIN_FILE].items():
        tally(key, ops=len(entries))
    return found


def _report(state: State) -> None:
    """Print one line per server: IDs and counts, no value from the files."""
    for server, line in sorted(counts(state).items()):
        print(f"guild_id={server} " + " ".join(f"{field}={count}" for field, count in line.items()))


def _hashes(files: Mapping[str, bytes]) -> dict[str, str]:
    """Fingerprint each file, by the bytes that were judged."""
    return {name: hashlib.sha256(raw).hexdigest() for name, raw in files.items()}


def _already_done(conn: sqlite3.Connection, files: Mapping[str, bytes], path: Path) -> bool:
    """Say whether these exact files are imported; any other state of the database refuses the import.

    Args:
        conn (sqlite3.Connection): The connection.
        files (Mapping[str, bytes]): The bytes of each file found.
        path (Path): The database file, for the message.

    Returns:
        bool: True if these files are imported already, False if the database is empty and ready.

    Raises:
        StateFileError: If it holds anything else: the bot's newer data must never be replaced or merged.
    """
    imported = {row[0]: row[1] for row in conn.execute("SELECT file_name, sha256 FROM state_import")}
    hashes = _hashes(files)
    if imported == hashes:
        return True
    if imported:
        names = sorted(name for name in {*imported, *hashes} if imported.get(name) != hashes.get(name))
        msg = f"{', '.join(names)} changed since the import into {path.as_posix()}; nothing was changed."
        raise StateFileError(msg)
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' AND name <> 'state_import'"
    ).fetchall()
    filled = sorted(row[0] for row in tables if conn.execute(f"SELECT 1 FROM {row[0]} LIMIT 1").fetchone())
    if filled:
        msg = f"{path.as_posix()} already holds data no import wrote ({', '.join(filled)}); nothing was changed."
        raise StateFileError(msg)
    return False


def run_import(path: Path = db.DATABASE_FILE) -> None:
    """Import the state files beside the database, once; a second run changes nothing.

    Args:
        path (Path): The database file.
    """
    files = read_files(path.parent)
    state = parse(files)
    db.open_database(path, gate=False)
    try:
        conn = db.connection()
        if _already_done(conn, files, path):
            print("Already imported: nothing to do.")
            return
        now = datetime.now(UTC).isoformat()
        with db.transaction():
            _write_and_verify(conn, state)
            # The last write of the transaction: the start gate opens only when everything above is there.
            conn.executemany(
                "INSERT INTO state_import (file_name, sha256, imported_at) VALUES (?, ?, ?)",
                [(name, sha256, now) for name, sha256 in _hashes(files).items()],
            )
        _report(state)
        print(f"Imported {', '.join(files)} into {path.as_posix()}.")
    finally:
        db.close_database()


def _compare(path: Path, files: Mapping[str, bytes], state: State) -> None:
    """Compare the database with the files, on a copy in memory: a read-only open leaves the file as it is.

    Args:
        path (Path): The database file.
        files (Mapping[str, bytes]): The bytes of each file found.
        state (State): What the files hold.

    Raises:
        StateFileError: If the database holds something else.
    """
    source = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        source.backup(db.connection())
    finally:
        source.close()
    conn = db.connection()
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'state_import'").fetchone() or not _already_done(
        conn, files, path
    ):
        print(f"{path.as_posix()} holds no import yet.")
        return
    if (back := snapshot()) != state:
        different = sorted(name for name in state if back[name] != state[name])
        msg = f"{path.as_posix()} differs from {', '.join(different)}: normal only once the bot has run."
        raise StateFileError(msg)
    print(f"{path.as_posix()} matches the files.")


def run_check(path: Path = db.DATABASE_FILE) -> None:
    """Run the whole import in memory and report what the files hold; nothing is written.

    Args:
        path (Path): The database file.
    """
    files = read_files(path.parent)
    state = parse(files)
    db.open_database(Path(":memory:"), gate=False)
    try:
        with db.transaction() as conn:
            _write_and_verify(conn, state)
        _report(state)
        if path.with_name(f"{path.name}-journal").exists():
            # A read-only open cannot finish the recovery, and this command writes nothing: the import does it.
            print(f"{path.as_posix()} was left by an interrupted write: run the import.")
        elif path.exists():
            _compare(path, files, state)
    finally:
        db.close_database()
    print(f"Check passed for {', '.join(files)}: nothing was written.")


def main(argv: list[str] | None = None) -> None:
    """Run the import, or its check, from the command line.

    Args:
        argv (list[str] | None): The arguments, or None for the command line.
    """
    parser = argparse.ArgumentParser(prog="python -m fablabot.import_state", description=__doc__)
    parser.add_argument("--check", action="store_true", help="read the files and report what they hold; write nothing")
    run = run_check if parser.parse_args(argv).check else run_import
    try:
        run()
    except (StateFileError, db.StorageError, sqlite3.Error, OSError) as error:
        sys.exit(f"Cannot import: {error}")


if __name__ == "__main__":
    main()
