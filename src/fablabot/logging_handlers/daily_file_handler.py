"""Daily rotating file handler with automatic cleanup of old log files."""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
from pathlib import Path
from typing import override

from discord import File

from fablabot.helpers.constants import PARIS_TZ

_LOG_PATH = "logs"
_MAX_LOG_AGE_DAYS = 15


class DailyFileHandler(logging.FileHandler):
    """File handler that creates a new log file each day based on the current date.

    The log files are named using the pattern: YYYY-MM-DD.log
    The handler automatically switches to a new file when the date changes.
    Log files older than MAX_LOG_AGE_DAYS are automatically deleted.
    """

    def __init__(
        self,
        *,
        log_dir: str = _LOG_PATH,
        level: int = logging.DEBUG,
        formatter: logging.Formatter,
        max_age_days: int = _MAX_LOG_AGE_DAYS,
    ) -> None:
        """Initialize the daily file handler.

        Args:
            log_dir (str): Directory where log files will be stored. Defaults to `LOG_PATH`.
            level (int): Logging level for this handler. Defaults to `logging.DEBUG`.
            formatter (logging.Formatter): Formatter for log messages.
            max_age_days (int): Maximum age in days for log files. Older files will be deleted. Defaults to `MAX_LOG_AGE_DAYS`.
        """
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.current_date = datetime.now(PARIS_TZ).date()
        self.max_age_days = max_age_days

        log_file = self.log_dir / f"{self.current_date}.log"
        super().__init__(log_file, mode="a", encoding="utf-8")
        self.setLevel(level)
        self.setFormatter(formatter)

        self._cleanup_old_logs()

    @override
    def emit(self, record: logging.LogRecord) -> None:
        """Emit a log record, switching to a new file if the date has changed.

        Args:
            record (logging.LogRecord): The log record to emit.
        """
        current_date = datetime.now(PARIS_TZ).date()

        if current_date != self.current_date:
            self.close()
            self.current_date = current_date
            self.baseFilename = str(self.log_dir / f"{self.current_date}.log")
            self.stream = self._open()

            self._cleanup_old_logs()

        super().emit(record)

    async def export_logs(self, date_str: str) -> File | None:
        """Export log file for the given date.

        Args:
            date_str (str): The date for which to export logs in YYYY-MM-DD format.

        Returns:
            File | None: The Discord File object for the log file, or None if the file
        """
        log_file = self.log_dir / f"{date_str}.log"

        if log_file.exists():
            return File(log_file, filename=f"{date_str}.log")

        return None

    def _cleanup_old_logs(self) -> None:
        """Delete log files older than max_age_days."""
        if not self.log_dir.exists():
            return

        cutoff_date = datetime.now(PARIS_TZ).date() - timedelta(days=self.max_age_days)

        for log_file in self.log_dir.glob("*.log"):
            try:
                file_date_str = log_file.stem
                file_date = datetime.strptime(file_date_str, "%Y-%m-%d").replace(tzinfo=PARIS_TZ).date()

                if file_date < cutoff_date:
                    log_file.unlink()
                    logging.getLogger(__name__).info(f"Deleted old log file: {log_file.name}")
            except (ValueError, OSError) as e:
                logging.getLogger(__name__).warning(f"Could not process log file {log_file.name}: {e}")
