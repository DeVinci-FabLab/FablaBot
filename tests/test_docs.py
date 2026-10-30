"""Pin what the user guide, the operator guide and the README say about /help."""

from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent.parent
DOMAINS = (
    "Text channels",
    "Voice channels",
    "Trainings",
    "Bot messages",
    "Members and roles",
    "Welcoming newcomers",
    "Bot logs",
    "For everyone",
)


def _read(relative: str) -> str:
    """Return a repository file as text."""
    return (ROOT / relative).read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    """Return the body of the section that starts at ``heading``, up to the next heading of the same or higher level."""
    assert f"{heading}\n" in text, heading
    depth = len(heading) - len(heading.lstrip("#"))
    body = text.split(f"{heading}\n", 1)[1]
    return re.split(rf"\n#{{1,{depth}}} ", body)[0]


def test_user_guide_documents_help() -> None:
    """The user guide has a /help section, the three menu names and one section per domain."""
    text = _read("docs/USERGUIDE.md")
    assert "## /help" in text
    section = _section(text, "## /help")
    for word in ("`show`", "/aide", "/hilfe", "/suggest"):
        assert word in section, word
    for domain in DOMAINS:
        assert f"\n## {domain}\n" in text, domain


def test_user_guide_keeps_the_old_details() -> None:
    """The details the old helps carried are in the user guide."""
    text = _read("docs/USERGUIDE.md")
    for detail in ("DD/MM/YYYY", "5 minutes", "90", "user_dm", "role_dm", "Manage Messages", "Administrator", "F - "):
        assert detail in text, detail


def test_operator_guide_explains_the_server_language() -> None:
    """The operator guide explains the server language and the new sync line."""
    text = _read("docs/OPERATIONS.md")
    section = _section(text, "## Server language")
    for word in ("Community", "en-US", "src/fablabot/locales"):
        assert word in section, word
    assert text.index("## Commands channel") < text.index("## Server language")
    assert "Commands synced (9, global)." in _section(text, '## "This command is outdated"')


def test_readme_check_names_help() -> None:
    """The slash-menu section of the README names /help next to /suggest, in its paragraph and in step 4."""
    text = _read("README.md")
    section = _section(text, "### Staff commands in the slash menu")
    assert "`/suggest` and `/help` stay visible to everyone" in section
    assert "it sees `/suggest` and `/help` and no staff group" in section
