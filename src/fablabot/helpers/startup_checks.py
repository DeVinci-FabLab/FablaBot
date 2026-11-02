"""Checks run before the bot connects: can it use its folders and its token?"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final

DATA_DIR: Final = Path("data")
LOGS_DIR: Final = Path("logs")
TOKEN_FILE_ENV: Final = "DISCORD_TOKEN_FILE"


class StartupError(Exception):
    """The bot cannot start; each problem is one line for the operator."""

    def __init__(self, problems: list[str]) -> None:
        """Keep the problems and join them for ``str()``.

        Args:
            problems (list[str]): One message per problem.
        """
        super().__init__("\n".join(problems))
        self.problems = problems


def _entry_problem(path: Path, *, is_dir: bool) -> str | None:
    """Return the first access problem of one folder or file.

    Args:
        path (Path): The entry to test.
        is_dir (bool): Whether the entry is a folder.

    Returns:
        str | None: The problem, or None when the entry can be read and written.
    """
    label = f"{path.as_posix()}/" if is_dir else path.as_posix()
    read_mode = os.R_OK | os.X_OK if is_dir else os.R_OK
    if not os.access(path, read_mode):
        return f"{label} is not readable by uid {os.getuid()}."
    if not os.access(path, os.W_OK):
        return f"{label} is not writable by uid {os.getuid()}."
    return None


def folder_problem(root: Path) -> str | None:
    """Create ``root`` if missing, then find the first entry under it the bot cannot use.

    Args:
        root (Path): The folder to check.

    Returns:
        str | None: The first problem found, or None when every folder and file can be read and written.
    """
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError:
        return f"{root.as_posix()}/ is not writable by uid {os.getuid()}."
    problem = _entry_problem(root, is_dir=True)
    if problem:
        return problem
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            problem = _entry_problem(Path(dirpath) / name, is_dir=False)
            if problem:
                return problem
        for name in dirnames:
            problem = _entry_problem(Path(dirpath) / name, is_dir=True)
            if problem:
                return problem
    return None


def read_token(token_file: str | None) -> str:
    """Read the Discord token from its file.

    Args:
        token_file (str | None): The path held by ``DISCORD_TOKEN_FILE``.

    Returns:
        str: The token without surrounding whitespace.

    Raises:
        StartupError: If the variable is unset, the file cannot be read, or it is empty.
    """
    if not token_file:
        raise StartupError([f"{TOKEN_FILE_ENV} is not set."])
    try:
        token = Path(token_file).read_text(encoding="utf-8").strip()
    except UnicodeError:
        raise StartupError([f"the token file {token_file} is not readable by uid {os.getuid()}: not valid UTF-8."]) from None
    except OSError as error:
        raise StartupError([f"the token file {token_file} is not readable by uid {os.getuid()}: {error.strerror}."]) from None
    if not token:
        raise StartupError([f"the token file {token_file} is empty."])
    return token


def check_runtime_access(token_file: str | None, *, folders: tuple[Path, ...] = (DATA_DIR, LOGS_DIR)) -> str:
    """Check every folder and the token, and report all problems together.

    Args:
        token_file (str | None): The path held by ``DISCORD_TOKEN_FILE``.
        folders (tuple[Path, ...]): The folders the bot reads and writes.

    Returns:
        str: The Discord token.

    Raises:
        StartupError: With every problem found, folders first, then the token.
    """
    problems = [problem for folder in folders if (problem := folder_problem(folder))]
    token = ""
    try:
        token = read_token(token_file)
    except StartupError as error:
        problems.extend(error.problems)
    if problems:
        raise StartupError(problems)
    return token
