"""Daily rotating file handler that also keeps one log file per server."""

from __future__ import annotations

from datetime import date, datetime
import logging
from pathlib import Path
from typing import override

from discord import File

from fablabot.helpers.constants import PARIS_TZ

_LOG_PATH = "logs"


class DailyFileHandler(logging.FileHandler):
    """File handler that creates a new log file each day based on the current date.

    The log files are named using the pattern: YYYY-MM-DD.log
    The handler automatically switches to a new file when the date changes.
    Lines tied to a server are also appended to ``guilds/<server id>/YYYY-MM-DD.log``.
    No log file is ever deleted by the bot.
    """

    def __init__(
        self,
        *,
        log_dir: str = _LOG_PATH,
        level: int = logging.DEBUG,
        formatter: logging.Formatter,
    ) -> None:
        """Initialize the daily file handler.

        Args:
            log_dir (str): Directory where log files will be stored. Defaults to `LOG_PATH`.
            level (int): Logging level for this handler. Defaults to `logging.DEBUG`.
            formatter (logging.Formatter): Formatter for log messages.
        """
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.current_date = datetime.now(PARIS_TZ).date()

        log_file = self.log_dir / f"{self.current_date}.log"
        super().__init__(log_file, mode="a", encoding="utf-8")
        self.setLevel(level)
        self.setFormatter(formatter)

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

        super().emit(record)

        guild_id = getattr(record, "guild_id", None)
        if guild_id is not None:
            try:
                path = self.guild_log_path(guild_id, self.current_date)
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as guild_file:
                    guild_file.write(self.format(record) + self.terminator)
            except RecursionError:
                raise
            except Exception:  # logging must never raise to its caller
                self.handleError(record)

    def guild_log_path(self, guild_id: int, day: date) -> Path:
        """Locate the log file of one server for one day.

        Args:
            guild_id (int): The server ID.
            day (date): The day of the log file.

        Returns:
            Path: ``<log_dir>/guilds/<guild_id>/<day>.log``.
        """
        return self.log_dir / "guilds" / str(guild_id) / f"{day.isoformat()}.log"

    async def export_logs(self, guild_id: int, day: date) -> File | None:
        """Export one server's log file for the given day.

        The shared file is never opened: it holds every server's lines.

        Args:
            guild_id (int): The server whose lines are exported.
            day (date): The day to export.

        Returns:
            File | None: The Discord File object for the server's log file, or None if there is none.
        """
        log_file = self.guild_log_path(guild_id, day)

        if log_file.exists():
            return File(log_file, filename=f"{day.isoformat()}.log")

        return None
