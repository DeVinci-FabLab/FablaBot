"""Entry point for the FablaBot Discord bot."""

from __future__ import annotations

from datetime import datetime
import logging
from pathlib import Path
from typing import override

LOG_PATH = "logs"


class DailyFileHandler(logging.FileHandler):
    """File handler that creates a new log file each day based on the current date.

    The log files are named using the pattern: YYYY-MM-DD.log
    The handler automatically switches to a new file when the date changes.
    """

    def __init__(self, log_dir: str = LOG_PATH, level: int = logging.DEBUG) -> None:
        """Initialize the daily file handler.

        Args:
            log_dir: Directory where log files will be stored.
            level: Logging level for this handler.
        """
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.current_date = datetime.now().date()

        log_file = self.log_dir / f"{self.current_date}.log"
        super().__init__(log_file, mode="a", encoding="utf-8")
        self.setLevel(level)
        self.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s:%(name)s: %(message)s"))

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

        super().emit(record)
