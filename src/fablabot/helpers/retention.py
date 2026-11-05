"""Delete personal data past its retention: registrations and checks, reaction history, log files and database copies."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
import logging
import re
from typing import TYPE_CHECKING, Final

from fablabot.helpers.constants import PARIS_TZ, RETENTION
from fablabot.services import retention

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

_LOG_NAME: Final = re.compile(r"^(\d{4}-\d{2}-\d{2})\.log$")
_COPY_STAMP: Final = re.compile(r"-(\d{8}T\d{12}Z)\.db$")


@dataclass(frozen=True)
class RetentionReport:
    """What one run did.

    Attributes:
        summarised (int): Trainings whose anonymous counts were written.
        messages (int): Published messages deleted with their trainings, registrations and checks.
        reactions (int): Reactions deleted from the history.
        log_files (int): Log files deleted.
        copies (int): Database copies deleted.
    """

    summarised: int
    messages: int
    reactions: int
    log_files: int
    copies: int


def _day(name: str) -> date | None:
    """Read the day of a log file name, None for any other name."""
    match = _LOG_NAME.match(name)
    if match is None:
        return None
    try:
        return date.fromisoformat(match[1])
    except ValueError:
        return None


def old_log_files(log_dir: Path, cutoff: date) -> list[Path]:
    """List the shared and per-server log files of a day before ``cutoff``; other files are not listed.

    Args:
        log_dir (Path): The log folder.
        cutoff (date): The oldest day kept.

    Returns:
        list[Path]: The files to delete.
    """
    files = [*log_dir.glob("*.log"), *log_dir.glob("guilds/*/*.log")]
    return sorted(path for path in files if (day := _day(path.name)) is not None and day < cutoff)


def old_copies(folder: Path, cutoff: datetime) -> list[Path]:
    """List the database copies made before ``cutoff``, by the time in their name; other files are not listed.

    Args:
        folder (Path): The copies' folder.
        cutoff (datetime): The oldest copy time kept, timezone-aware.

    Returns:
        list[Path]: The copies to delete.
    """
    old: list[Path] = []
    for path in folder.glob("*.db"):
        match = _COPY_STAMP.search(path.name)
        if match and datetime.strptime(match[1], "%Y%m%dT%H%M%S%fZ").replace(tzinfo=UTC) < cutoff:
            old.append(path)
    return sorted(old)


def _delete(paths: list[Path]) -> int:
    """Delete files, logging each one that resists; return how many went."""
    deleted = 0
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.exception(f"Retention could not delete {path.as_posix()}; next run tries again.")
            continue
        deleted += 1
    return deleted


def apply_retention(now: datetime, *, log_dir: Path, backups: Path) -> RetentionReport:
    """Summarise the trainings whose check closed, then delete what is older than the retention.

    Args:
        now (datetime): The current time, timezone-aware.
        log_dir (Path): The log folder.
        backups (Path): The folder of the database copies.

    Returns:
        RetentionReport: What was done.

    Raises:
        sqlite3.Error: The database refused a step; the files are left for the next run.
    """
    cutoff = now - RETENTION
    summarised = retention.summarise(now)
    messages = retention.forget(cutoff)
    reactions = retention.forget_reactions(cutoff)
    report = RetentionReport(
        summarised=summarised,
        messages=messages,
        reactions=reactions,
        log_files=_delete(old_log_files(log_dir, cutoff.astimezone(PARIS_TZ).date())),
        copies=_delete(old_copies(backups, cutoff)),
    )
    logger.info(
        f"Retention: {report.summarised} trainings summarised; deleted {report.messages} training messages, "
        f"{report.reactions} reactions, {report.log_files} log files and {report.copies} database copies."
    )
    return report
