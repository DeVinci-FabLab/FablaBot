"""Container health check: the bot is healthy while its heartbeat file is recent.

Standard library only, so running it often costs almost nothing.
"""

from __future__ import annotations

from pathlib import Path
import sys
import time
from typing import Final

HEARTBEAT_FILE: Final = Path("/tmp/fablabot.heartbeat")
HEARTBEAT_INTERVAL_SECONDS: Final = 30
HEARTBEAT_MAX_AGE_SECONDS: Final = 90


def heartbeat_age(path: Path, *, now: float) -> float | None:
    """Return the age of a heartbeat file in seconds.

    Args:
        path (Path): The heartbeat file.
        now (float): The current time as a Unix timestamp.

    Returns:
        float | None: Seconds since the last modification, or None when the file cannot be read.
    """
    try:
        return now - path.stat().st_mtime
    except OSError:
        return None


def main() -> int:
    """Report whether the last heartbeat is recent enough.

    Returns:
        int: 0 when healthy, 1 otherwise.
    """
    age = heartbeat_age(HEARTBEAT_FILE, now=time.time())
    if age is None:
        print("unhealthy: no heartbeat")
        return 1
    if age > HEARTBEAT_MAX_AGE_SECONDS:
        print(f"unhealthy: last heartbeat {age:.0f} s ago")
        return 1
    print(f"healthy: last heartbeat {age:.0f} s ago")
    return 0


if __name__ == "__main__":
    sys.exit(main())
