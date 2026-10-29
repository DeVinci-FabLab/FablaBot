"""Checks on the hardening of the production compose files."""

from pathlib import Path

import pytest

from fablabot.healthcheck import HEARTBEAT_FILE

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = (ROOT / "compose.yml").read_text()
README = (ROOT / "README.md").read_text()
CHECK_SCRIPT = (ROOT / "docker" / "check-image.sh").read_text()

HARDENING = """\
    read_only: true
    tmpfs:
      - /tmp:size=1m,mode=1777,noexec,nosuid,nodev
    cap_drop:
      - ALL
    security_opt:
      - no-new-privileges:true"""


def hardening_block(text: str) -> str:
    """Return the hardening lines of a compose file.

    Args:
        text: The compose file content.

    Returns:
        The lines from `read_only:` through `no-new-privileges:true`, or an empty
        string when the file has no such block.
    """
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith("    read_only:")), None)
    end = next((i for i, line in enumerate(lines) if line.strip() == "- no-new-privileges:true"), None)
    if start is None or end is None or end < start:
        return ""
    return "\n".join(lines[start : end + 1])


@pytest.mark.parametrize("name", ["compose.yml", "compose.prod-local.yml"])
def test_production_compose_is_hardened(name: str) -> None:
    """Each production stack drops every privilege and adds none."""
    text = (ROOT / name).read_text()

    assert hardening_block(text) == HARDENING
    for forbidden in ("user:", "privileged:", "cap_add:"):
        assert not any(line.strip().startswith(forbidden) for line in text.splitlines())


def test_tmpfs_is_the_heartbeat_folder() -> None:
    """The in-memory folder is the one holding the heartbeat file."""
    block = hardening_block(COMPOSE).splitlines()
    entry = block[block.index("    tmpfs:") + 1].strip().removeprefix("- ")

    assert entry.startswith(f"{HEARTBEAT_FILE.parent}:")


def test_readme_shows_the_compose_hardening() -> None:
    """The README shows the same hardening lines as the stack."""
    block = hardening_block(COMPOSE)

    assert block
    assert block in README


def test_image_check_uses_the_compose_restrictions() -> None:
    """The image check runs the container with the stack's restrictions."""
    block = hardening_block(COMPOSE).splitlines()
    tmpfs_value = block[block.index("    tmpfs:") + 1].strip().removeprefix("- ")

    assert "--read-only" in CHECK_SCRIPT
    assert "--cap-drop ALL" in CHECK_SCRIPT
    assert "--security-opt no-new-privileges:true" in CHECK_SCRIPT
    assert f"--tmpfs {tmpfs_value}" in CHECK_SCRIPT
