"""Daily rotating file handler with automatic cleanup of old log files."""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
from pathlib import Path
from typing import override

LOG_PATH = "logs"
MAX_LOG_AGE_DAYS = 15


class DailyFileHandler(logging.FileHandler):
    """File handler that creates a new log file each day based on the current date.

    The log files are named using the pattern: YYYY-MM-DD.log
    The handler automatically switches to a new file when the date changes.
    Log files older than MAX_LOG_AGE_DAYS are automatically deleted.
    """

    def __init__(self, log_dir: str = LOG_PATH, level: int = logging.DEBUG, max_age_days: int = MAX_LOG_AGE_DAYS) -> None:
        """Initialize the daily file handler.

        Args:
            log_dir: Directory where log files will be stored.
            level: Logging level for this handler.
            max_age_days: Maximum age in days for log files. Older files will be deleted.
        """
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.current_date = datetime.now().date()
        self.max_age_days = max_age_days

        log_file = self.log_dir / f"{self.current_date}.log"
        super().__init__(log_file, mode="a", encoding="utf-8")
        self.setLevel(level)
        self.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s:%(name)s: %(message)s"))

        self._cleanup_old_logs()

    def _cleanup_old_logs(self) -> None:
        """Delete log files older than max_age_days."""
        if not self.log_dir.exists():
            return

        cutoff_date = datetime.now().date() - timedelta(days=self.max_age_days)

        for log_file in self.log_dir.glob("*.log"):
            try:
                file_date_str = log_file.stem
                file_date = datetime.strptime(file_date_str, "%Y-%m-%d").date()

                if file_date < cutoff_date:
                    log_file.unlink()
                    logging.getLogger(__name__).info(f"Deleted old log file: {log_file.name}")
            except (ValueError, OSError) as e:
                logging.getLogger(__name__).warning(f"Could not process log file {log_file.name}: {e}")

    @override
    def emit(self, record: logging.LogRecord) -> None:
        """Emit a log record, switching to a new file if the date has changed.

        Args:
            record: The log record to emit.
        """
        current_date = datetime.now().date()

        if current_date != self.current_date:
            self.close()
            self.current_date = current_date
            self.baseFilename = str(self.log_dir / f"{self.current_date}.log")
            self.stream = self._open()

            self._cleanup_old_logs()

        super().emit(record)
