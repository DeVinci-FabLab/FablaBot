"""A process killed between two writes of a save leaves the previous state whole, and the next start reads it."""

from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

from fablabot import db
from fablabot.models import FmMessageDraft, Formation, PublishedMessage
from fablabot.services import formations

if TYPE_CHECKING:
    from collections.abc import Sequence

GUILD_ID = 111111111111111111
MESSAGE_ID = 333333333333333333

# Runs in its own process. One page of cache forces each change to disk during the transaction, journal first.
CHILD = """
import json, os, signal, sys
from pathlib import Path
from fablabot import db
from fablabot.models import PublishedMessage
from fablabot.services import formations

path, kill_at, payload = sys.argv[1], int(sys.argv[2]), json.loads(sys.argv[3])
db.open_database(Path(path))
db.connection().execute("PRAGMA cache_size = 1")
seen = []

def trace(statement):
    seen.append(statement)
    if len(seen) == kill_at:
        os.kill(os.getpid(), signal.SIGKILL)

db.connection().set_trace_callback(trace)
formations.save_published(payload["guild_id"], PublishedMessage.from_dict(payload["published"]))
db.connection().set_trace_callback(None)
print(len(seen))
"""


def _published(label: str) -> PublishedMessage:
    """Build a message large enough to take many pages, every member carrying ``label``."""
    users = [{"user_id": i + 1, "username": f"{label}-{i}" * 40, "ts_iso": "2026-11-01T10:00:00+01:00"} for i in range(60)]
    fms = [
        Formation(
            emoji=f"{i}",
            name=f"{label} {i}",
            trainer_mention="<@1>",
            start_iso="2026-12-01T18:00:00+01:00",
            duration="2h",
            seats=60,
            registered_users=users,
        )
        for i in range(6)
    ]
    return PublishedMessage(
        message_id=MESSAGE_ID, channel_id=7, message=FmMessageDraft(header="h", role_id=5, intro=label, fms=fms, end="e")
    )


def _run(path: Path, kill_at: int, published: PublishedMessage) -> subprocess.CompletedProcess[str]:
    """Save ``published`` in a child process, killed at the given statement when it gets there."""
    src = Path(db.__file__).resolve().parent.parent
    payload = json.dumps({"guild_id": GUILD_ID, "published": published.to_dict()})
    return subprocess.run(
        [sys.executable, "-c", CHILD, str(path), str(kill_at), payload],
        env={**os.environ, "PYTHONPATH": str(src)},
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture
def seeded() -> tuple[Path, PublishedMessage, int]:
    """A database file holding a published message, and the number of statements a full save takes."""
    path = Path("killed/fablabot.db")
    before = _published("old")
    db.open_database(path)
    formations.save_published(GUILD_ID, before)
    db.close_database()
    done = _run(path, 10**9, before)
    assert done.returncode == 0, done.stderr
    return path, before, int(done.stdout)


def _at_quarter_points(total: int) -> Sequence[int]:
    """Pick four statements inside the transaction, none of them its BEGIN or COMMIT."""
    return [total // 8, total // 4, total // 2, total - 2]


def test_a_killed_save_leaves_the_old_message_whole(seeded: tuple[Path, PublishedMessage, int]) -> None:
    """At each point the process dies by SIGKILL, and the next open reads the state from before the save."""
    path, before, total = seeded
    after = _published("new")
    journals = 0
    for kill_at in _at_quarter_points(total):
        killed = _run(path, kill_at, after)

        assert killed.returncode == -signal.SIGKILL, killed.stderr
        journals += path.with_name(f"{path.name}-journal").exists()
        db.open_database(path)
        assert formations.current_published(GUILD_ID) == before
        db.close_database()

    assert journals, "the kill never caught a transaction with changes on disk"


def test_a_kill_after_the_commit_keeps_the_new_message(seeded: tuple[Path, PublishedMessage, int]) -> None:
    """Once the save has returned, a kill changes nothing."""
    path, _before, total = seeded
    after = _published("new")

    done = _run(path, total + 10, after)

    assert done.returncode == 0, done.stderr
    db.open_database(path)
    assert formations.current_published(GUILD_ID) == after
