"""The test run hides discord.py's own deprecation notice and no other warning, so a real warning stands out."""

from __future__ import annotations

import warnings

from discord.utils import escape_markdown


def test_discord_py_s_positional_count_notice_is_not_shown() -> None:
    """``re.sub(..., 0, re.MULTILINE)`` inside discord.py warns on every call; the run's config ignores that message."""
    with warnings.catch_warnings(record=True) as shown:
        escape_markdown("*x*")

    assert [str(item.message) for item in shown] == []


def test_any_other_deprecation_warning_still_shows() -> None:
    """The filter names the exact message: a different deprecation warning is recorded as before."""
    with warnings.catch_warnings(record=True) as shown:
        warnings.warn("something else is deprecated", DeprecationWarning, stacklevel=1)

    assert [str(item.message) for item in shown] == ["something else is deprecated"]
