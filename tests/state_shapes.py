"""JSON state in the shapes production holds: five servers, a published training, a journal, no temporary admin file."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

FIRST_GUILD = 100000000000000001
GUILDS = (FIRST_GUILD, 100000000000000002, 100000000000000003, 100000000000000004, 100000000000000005)
GONE_GUILD = 100000000000000009  # no longer allowed: its settings stay
MESSAGE_ID = 900000000000000001
CHANNEL_ID = 800000000000000001
ROLE_ID = 700000000000000001
REGISTERED = (500000000000000001, 500000000000000002)
WAITLISTED = (500000000000000003,)
TS = "2026-10-20T18:00:00+02:00"


def user(user_id: int) -> dict[str, Any]:
    """Build a registered member."""
    return {"user_id": user_id, "username": f"name-{user_id}", "ts_iso": TS}


def formation(index: int, *, members: bool = False) -> dict[str, Any]:
    """Build a formation with its twelve keys."""
    return {
        "emoji": "🔧🖨️🪚🧵"[index],
        "name": f"Training {index}",
        "trainer_mention": "<@500000000000000009>",
        "start_iso": "2026-12-10T18:30:00+01:00",
        "duration": "2h",
        "seats": 2,
        "description": "",
        "excusable": True,
        "registered_users": [user(u) for u in REGISTERED] if members and index == 0 else [],
        "waitlisted_users": [user(u) for u in WAITLISTED] if members and index == 0 else [],
        "notified_hour_before": False,
        "notified_at_start": False,
    }


def message(*, members: bool = False) -> dict[str, Any]:
    """Build a training message with its six keys and four formations."""
    return {
        "header": "Header",
        "role_id": ROLE_ID,
        "intro": "Intro",
        "fms": [formation(index, members=members) for index in range(4)],
        "end": "End",
        "request_forms_url": "https://forms.example/request",
    }


def journal(guild_id: int) -> list[dict[str, Any]]:
    """Build a reaction history: the first server holds 153 events, its members' adds first."""
    if guild_id != FIRST_GUILD:
        return []

    def event(user_id: int, action: str = "add") -> dict[str, Any]:
        return {
            "message_id": MESSAGE_ID,
            "user_id": user_id,
            "user_name": f"name-{user_id}",
            "emoji": "🔧",
            "action": action,
            "ts_iso": TS,
        }

    members = [event(u) for u in (*REGISTERED, *WAITLISTED)]
    noise = [event(600000000000000000 + i, "add" if i % 2 else "remove") for i in range(150)]
    return members + noise


def link(action_type: str) -> dict[str, Any]:
    """Build a reaction link with its six keys."""
    return {
        "emoji": "✅",
        "action_type": action_type,
        "message_content": "Hello",
        "target_id": 800000000000000002,
        "target_name": "target",
        "message_id": None,
    }


def tracked(message_id: int, *links: str) -> dict[str, Any]:
    """Build a tracked message with its six keys."""
    return {
        "message_id": message_id,
        "channel_id": CHANNEL_ID,
        "content": "Content",
        "reactions": [link(action_type) for action_type in links],
        "created_by": 500000000000000009,
        "created_at_iso": TS,
    }


def state_files() -> dict[str, Any]:
    """Build the content of each JSON file production holds, ``temp_admin_state.json`` being absent there.

    Returns:
        dict[str, Any]: The parsed content of each file.
    """
    settings: dict[str, Any] = {
        str(FIRST_GUILD): {"commands_channel_id": 1, "welcome_verify_enabled": True, "log_channel_id": 2},
        **{str(guild): {"log_channel_id": 2 + guild % 10} for guild in GUILDS[1:]},
        str(GONE_GUILD): {"commands_channel_id": 3},
        "__global__": {"log_channel_id": 4},
    }
    trainings = {
        str(guild): {
            "draft": message(),
            "published": {"message_id": MESSAGE_ID, "channel_id": CHANNEL_ID, "message": message(members=guild == FIRST_GUILD)},
            "reactions_log": journal(guild),
        }
        for guild in GUILDS
    }
    followed = {
        str(GUILDS[0]): {
            "tracked": {
                str(MESSAGE_ID + 1): tracked(MESSAGE_ID + 1, "channel", "role_dm"),
                str(MESSAGE_ID + 2): tracked(MESSAGE_ID + 2, "channel"),
            },
            "reactions_log": [],
        },
        **{str(guild): {"tracked": {}, "reactions_log": []} for guild in GUILDS[1:4]},
    }
    return {"guild_config.json": settings, "formations_state.json": trainings, "messages_state.json": followed}


def write_state(files: dict[str, Any] | None = None, folder: Path = Path("data")) -> dict[str, bytes]:
    """Write state files as the bot wrote them, and return their bytes.

    Args:
        files (dict[str, Any] | None): The parsed content, or None for ``state_files()``.
        folder (Path): Where to write.

    Returns:
        dict[str, bytes]: The bytes of each file written.
    """
    folder.mkdir(parents=True, exist_ok=True)
    written: dict[str, bytes] = {}
    for name, content in (state_files() if files is None else files).items():
        (folder / name).write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")
        written[name] = (folder / name).read_bytes()
    return written
