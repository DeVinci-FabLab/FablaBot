"""Initialization module for the fablabot package."""

from fablabot.discord_log_handler import DiscordLogHandler

if __name__ == "__main__":
    from fablabot.main import main

    main()

__all__ = [
    "DiscordLogHandler",
]
