"""Render the bot's screens to HTML and PNG.

Run from the repository root: ``uv run --group mockups python -m tests.mockups --out DIR [SCREEN ...]``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from typing import TYPE_CHECKING

from .preview import Sent, page
from .screens import SCREENS, Screen

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from typing import Any

THEMES = ("dark", "light")
WIDTHS = (390, 900)
INSTALL = "uv run --group mockups playwright install chromium --only-shell"
RUN = "uv run --group mockups python -m tests.mockups"
NO_GROUP = f"The browser library is not installed. Run it through its group: {RUN} --out DIR"
NO_BROWSER = f"The headless browser is missing. Install it once with: {INSTALL}"
LOCAL_SCHEMES = ("file:", "data:", "about:")


class PreviewError(Exception):
    """A failure the person running the command can fix, with the line to run."""


def load_payload(path: Path) -> Screen:
    """Read a hand-written screen: ``{"names": {id: name}, "sent": [{"message": {...}} | {"modal": {...}}]}``."""
    data = json.loads(path.read_text(encoding="utf-8"))
    names = {int(k): v for k, v in data.get("names", {}).items()}
    sent = [Sent("modal", i["modal"]) if "modal" in i else Sent("message", i["message"]) for i in data["sent"]]
    return Screen(sent, names)


def build_screens(names: Sequence[str]) -> dict[str, Screen]:
    """Build each screen from a throwaway working folder, since the cogs open their state files under ``data/``."""
    here = Path.cwd()
    with tempfile.TemporaryDirectory() as scratch:
        os.chdir(scratch)
        try:
            return {name: asyncio.run(SCREENS[name]()) for name in names}
        finally:
            os.chdir(here)


def write_pages(screens: Mapping[str, Screen], out: Path, widths: Sequence[int]) -> list[tuple[str, Path, int]]:
    """Write ``<screen>-<theme>.html`` for each screen and theme, and return the capture job for each width."""
    jobs: list[tuple[str, Path, int]] = []
    for name, screen in screens.items():
        for theme in THEMES:
            text = page(screen.sent, theme=theme, names=screen.names)
            (out / f"{name}-{theme}.html").write_text(text, encoding="utf-8")
            jobs.extend((text, out / f"{name}-{theme}-{width}.png", width) for width in widths)
    return jobs


def shoot(jobs: Sequence[tuple[str, Path, int]]) -> None:
    """Capture each (html text or file URI, png path, width) with the pinned headless browser, offline and with script off."""
    try:
        # The browser group is installed only where PNGs are made, never in CI, so the checker may not find it.
        from playwright.sync_api import Error, sync_playwright  # pyright: ignore[reportMissingImports]
    except ImportError as exc:
        raise PreviewError(NO_GROUP) from exc

    def local_only(route: Any) -> None:
        """Let a local page load and abort every request to a host."""
        if route.request.url.startswith(LOCAL_SCHEMES):
            route.continue_()
        else:
            route.abort()

    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch()
        except Error as exc:
            raise PreviewError(NO_BROWSER) from exc
        for source, png, width in jobs:
            context = browser.new_context(
                viewport={"width": width, "height": 200},
                device_scale_factor=2,
                java_script_enabled=False,
                service_workers="block",
            )
            context.route("**/*", local_only)
            tab = context.new_page()
            if source.startswith("file://"):
                tab.goto(source)
                tab.screenshot(path=str(png), full_page=True, animations="disabled")
            else:
                tab.set_content(source)
                tab.locator("#screen").screenshot(path=str(png), animations="disabled")
            context.close()
        browser.close()


def main(argv: Sequence[str] | None = None) -> int:
    """Write the HTML and PNG files of the screens, or capture one page with ``--shot``; return the exit code."""
    parser = argparse.ArgumentParser(prog="python -m tests.mockups", description=__doc__)
    parser.add_argument("screens", nargs="*", help=f"screens to render (default: all of {', '.join(SCREENS)})")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--payload", type=Path, help="a hand-written screen in JSON, for a screen not coded yet")
    parser.add_argument("--html-only", action="store_true")
    parser.add_argument(
        "--width", type=int, action="append", help="a viewport width in pixels, repeatable (default: 390 and 900)"
    )
    parser.add_argument("--shot", nargs=3, metavar=("HTML", "PNG", "WIDTH"), help="capture one HTML page at one width")
    args = parser.parse_args(argv)
    try:
        if args.shot:
            html_path, png, width = args.shot
            shoot([(Path(html_path).resolve().as_uri(), Path(png), int(width))])
            return 0
        if args.out is None:
            parser.error("--out is required")
        unknown = sorted(set(args.screens) - set(SCREENS))
        if unknown:
            parser.error(f"unknown screen(s): {', '.join(unknown)}; known: {', '.join(SCREENS)}")
        args.out.mkdir(parents=True, exist_ok=True)
        started = time.perf_counter()
        screens = (
            {args.payload.stem: load_payload(args.payload)} if args.payload else build_screens(args.screens or list(SCREENS))
        )
        jobs = write_pages(screens, args.out, tuple(args.width) if args.width else WIDTHS)
        print(f"html: {len(screens) * len(THEMES)} pages in {time.perf_counter() - started:.2f}s")
        if not args.html_only:
            started = time.perf_counter()
            shoot(jobs)
            print(f"png: {len(jobs)} images in {time.perf_counter() - started:.2f}s")
    except PreviewError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
