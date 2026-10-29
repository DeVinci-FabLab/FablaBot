"""The command that renders the screens: its files, its options and its two ways of failing, all without a browser."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tomllib
import types
from typing import TYPE_CHECKING, Self

from mockups import __main__ as command
from mockups.preview import EPHEMERAL_NOTE
from mockups.screens import SCREENS
import pytest

if TYPE_CHECKING:
    from collections.abc import Sequence

ROOT = Path(__file__).parent.parent


def test_html_only_writes_both_themes_per_screen(tmp_path: Path) -> None:
    """Without a browser, every screen gets one HTML file per theme, each with its theme class."""
    assert command.main(["--out", str(tmp_path), "--html-only"]) == 0
    files = sorted(tmp_path.glob("*.html"))
    assert len(files) == 2 * len(SCREENS)
    for name in SCREENS:
        for theme in ("dark", "light"):
            assert f'class="{theme}"' in (tmp_path / f"{name}-{theme}.html").read_text(encoding="utf-8")


def test_unknown_screen_stops_before_writing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """An unknown screen name exits 2, lists the known screens and creates nothing."""
    out = tmp_path / "out"
    with pytest.raises(SystemExit) as stop:
        command.main(["--out", str(out), "nope"])
    assert stop.value.code == 2
    err = capsys.readouterr().err
    assert all(name in err for name in SCREENS)
    assert not out.exists()


def test_missing_out_is_an_error() -> None:
    """Rendering without --out exits 2."""
    with pytest.raises(SystemExit) as stop:
        command.main([])
    assert stop.value.code == 2


def test_payload_renders_like_a_screen(tmp_path: Path) -> None:
    """A hand-written JSON screen gives a page with its message, its modal and the private-message note."""
    payload = tmp_path / "mon-ecran.json"
    payload.write_text(
        json.dumps(
            {
                "names": {"42": "Camille"},
                "sent": [
                    {"message": {"content": "Bonjour <@42>", "ephemeral": True}},
                    {"modal": {"title": "Titre du modal", "components": []}},
                ],
            }
        ),
        encoding="utf-8",
    )
    out = tmp_path / "out"
    assert command.main(["--out", str(out), "--payload", str(payload), "--html-only"]) == 0
    built = (out / "mon-ecran-dark.html").read_text(encoding="utf-8")
    assert "Bonjour" in built
    assert "@Camille" in built
    assert "Titre du modal" in built
    assert EPHEMERAL_NOTE in built


def test_missing_group_says_what_to_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Without the playwright group, PNG mode exits 1 naming the command to run, after writing the HTML."""
    monkeypatch.setitem(sys.modules, "playwright", None)
    assert command.main(["--out", str(tmp_path), "text-help"]) == 1
    assert "uv run --group mockups" in capsys.readouterr().err
    assert (tmp_path / "text-help-dark.html").exists()


def test_preview_dependency_stays_out_of_the_bot() -> None:
    """The browser library sits in its own group, never in the bot's or the dev dependencies, nor in the image."""
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    groups = data["dependency-groups"]
    assert any(d.startswith("playwright") for d in groups["mockups"])
    assert not any("playwright" in d for d in data["project"]["dependencies"])
    assert not any("playwright" in d for d in groups["dev"])
    rules = [
        line.strip()
        for line in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and line[0] != "#"
    ]
    assert rules[0] == "*"
    assert not any(rule.lstrip("!").startswith("tests") for rule in rules)


def test_widths_default_to_phone_and_desktop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Two widths by default, and --width replaces both."""
    assert command.WIDTHS == (390, 900)
    seen: list[Sequence[int]] = []
    real = command.write_pages

    def spy(screens: object, out: Path, widths: Sequence[int]) -> list[tuple[str, Path, int]]:
        seen.append(widths)
        return real(screens, out, widths)  # pyright: ignore[reportArgumentType]  # the spy forwards what it was given

    monkeypatch.setattr(command, "write_pages", spy)
    command.main(["--out", str(tmp_path / "a"), "--html-only"])
    command.main(["--out", str(tmp_path / "b"), "--html-only", "--width", "500"])
    assert seen == [(390, 900), (500,)]


def test_shot_captures_one_page_at_one_width(monkeypatch: pytest.MonkeyPatch) -> None:
    """--shot hands the browser exactly one job: the page's file URI, the PNG path and the width."""
    jobs: list[list[tuple[str, Path, int]]] = []
    monkeypatch.setattr(command, "shoot", lambda j: jobs.append(list(j)))
    assert command.main(["--shot", "page.html", "out.png", "390"]) == 0
    assert jobs == [[(Path("page.html").resolve().as_uri(), Path("out.png"), 390)]]


def test_missing_browser_says_how_to_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """When the browser cannot start, PNG mode exits 1 with the install line, after writing the HTML."""

    class FakeError(Exception):
        """Stand-in for the library's error."""

    class Chromium:
        """A browser type that cannot launch."""

        def launch(self) -> None:
            """Fail as a missing browser does."""
            raise FakeError

    class Session:
        """A started library session."""

        chromium = Chromium()

        def __enter__(self) -> Self:
            """Open the session."""
            return self

        def __exit__(self, *exc: object) -> None:
            """Close the session."""

    fake = types.ModuleType("playwright.sync_api")
    fake.Error = FakeError  # pyright: ignore[reportAttributeAccessIssue]  # a bare module stands in for the library
    fake.sync_playwright = Session  # pyright: ignore[reportAttributeAccessIssue]  # a bare module stands in for the library
    monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake)
    assert command.main(["--out", str(tmp_path), "text-help"]) == 1
    assert "playwright install chromium --only-shell" in capsys.readouterr().err
    assert (tmp_path / "text-help-light.html").exists()


def test_screens_are_built_away_from_the_repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Screens are built from a throwaway folder, so the caller's folder gets no ``data/`` or ``logs/``."""
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.chdir(repo)
    seen: list[Path] = []
    real = command.SCREENS["text-help"]

    async def spy() -> object:
        seen.append(Path.cwd())
        return await real()

    monkeypatch.setitem(command.SCREENS, "text-help", spy)
    assert command.main(["--out", str(tmp_path / "out"), "--html-only"]) == 0
    assert seen
    assert all(folder != repo for folder in seen)
    assert Path.cwd() == repo
    assert sorted(path.name for path in repo.iterdir()) == []


def test_pages_run_without_script_and_without_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each capture context turns script and service workers off, so no page can open a WebSocket or fetch."""
    contexts: list[dict[str, object]] = []

    class Tab:
        """A page that captures nothing."""

        def goto(self, url: str) -> None:
            """Load a page."""

        def screenshot(self, **kwargs: object) -> None:
            """Take a picture."""

    class Context:
        """A browser context that records how it was made."""

        def route(self, pattern: str, handler: object) -> None:
            """Register the request filter."""

        def new_page(self) -> Tab:
            """Open a tab."""
            return Tab()

        def close(self) -> None:
            """Close the context."""

    class Browser:
        """A browser that records every context it makes."""

        def new_context(self, **kwargs: object) -> Context:
            """Make a context."""
            contexts.append(kwargs)
            return Context()

        def close(self) -> None:
            """Close the browser."""

    class Chromium:
        """A browser type that launches the recorder."""

        def launch(self) -> Browser:
            """Start the browser."""
            return Browser()

    class Session:
        """A started library session."""

        chromium = Chromium()

        def __enter__(self) -> Self:
            """Open the session."""
            return self

        def __exit__(self, *exc: object) -> None:
            """Close the session."""

    fake = types.ModuleType("playwright.sync_api")
    fake.Error = Exception  # pyright: ignore[reportAttributeAccessIssue]  # a bare module stands in for the library
    fake.sync_playwright = Session  # pyright: ignore[reportAttributeAccessIssue]  # a bare module stands in for the library
    monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake)
    assert command.main(["--shot", "page.html", "out.png", "390"]) == 0
    assert len(contexts) == 1
    assert contexts[0]["java_script_enabled"] is False
    assert contexts[0]["service_workers"] == "block"


def test_contributing_explains_the_preview() -> None:
    """CONTRIBUTING has a section with the install, render and snapshot-update commands."""
    text = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    assert "## Preview a screen as an image" in text
    section = text.split("## Preview a screen as an image")[1].split("\n## ")[0]
    assert "uv run --group mockups playwright install chromium --only-shell" in section
    assert "uv run --group mockups python -m tests.mockups --out" in section
    assert "MOCKUP_SNAPSHOTS=update uv run --group dev pytest -q tests/test_mockups.py" in section
