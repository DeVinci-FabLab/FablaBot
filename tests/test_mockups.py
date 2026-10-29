"""Screen previews: each one comes from the bot's own code, and a change to it shows in its snapshot."""

from __future__ import annotations

import os
from pathlib import Path
import sys

from mockups.preview import EPHEMERAL_NOTE, Sent, markdown, markup, message_html, page
from mockups.screens import SCREENS
import pytest

SNAPSHOTS = Path(__file__).parent / "mockups" / "snapshots"
UPDATE = os.environ.get("MOCKUP_SNAPSHOTS") == "update"


def _compare(path: Path, built: str, *, update: bool) -> None:
    """Fail when ``path`` differs from ``built``; rewrite it first only when ``update`` is true."""
    if update:
        path.write_text(built, encoding="utf-8")
    assert path.read_text(encoding="utf-8") == built


@pytest.mark.parametrize("name", sorted(SCREENS))
async def test_screen_matches_its_snapshot(name: str) -> None:
    """The markup built from the bot's code equals the committed snapshot; set MOCKUP_SNAPSHOTS=update to accept a change."""
    screen = await SCREENS[name]()
    _compare(SNAPSHOTS / f"{name}.html", markup(screen.sent, screen.names), update=UPDATE)


@pytest.mark.parametrize("name", sorted(SCREENS))
async def test_screen_sends_something(name: str) -> None:
    """Every screen captures at least one message from the bot's code."""
    screen = await SCREENS[name]()
    assert screen.sent


def test_every_snapshot_has_a_screen() -> None:
    """No snapshot outlives its screen."""
    assert sorted(p.stem for p in SNAPSHOTS.glob("*.html")) == sorted(SCREENS)


def test_markdown_styles() -> None:
    """Bold, italic, underline, strike, code, headings, subtext, quote, list and blank line each get their element."""
    assert "<strong>a</strong>" in markdown("**a**")
    assert "<em>a</em>" in markdown("*a*")
    assert "<em>a</em>" in markdown("_a_")
    assert "<u>a</u>" in markdown("__a__")
    assert "<s>a</s>" in markdown("~~a~~")
    assert "<code>a *b*</code>" in markdown("`a *b*`")
    assert "<h1>a</h1>" in markdown("# a")
    assert "<h2>a</h2>" in markdown("## a")
    assert "<h3>a</h3>" in markdown("### a")
    assert '<div class="subtext">a</div>' in markdown("-# a")
    assert "<blockquote>a<br>b</blockquote>" in markdown("> a\n> b")
    assert '<div class="li">a</div>' in markdown("- a")
    assert '<div class="blank"></div>' in markdown("a\n\nb")


def test_markdown_mentions_and_emoji() -> None:
    """Mentions show the known name, unknown ones fall back, and emoji aliases become characters."""
    out = markdown("<@2222> <@&1111> <#3333> :warning:", {2222: "Camille", 1111: "Formations"})
    assert '<span class="mention">@Camille</span>' in out
    assert '<span class="mention">@Formations</span>' in out
    assert '<span class="mention">#inconnu</span>' in out
    assert "⚠️" in out


def test_markdown_escapes_html() -> None:
    """Text from the bot or a member never becomes markup."""
    out = markdown("<script>alert(1)</script> **gras**")
    assert "<script>" not in out
    assert "<strong>gras</strong>" in out


def test_masked_link_must_be_http() -> None:
    """A masked link becomes an anchor only for http(s) targets."""
    assert '<a href="https://x.fr">x</a>' in markdown("[x](https://x.fr)")
    out = markdown("[x](javascript:alert(1))")
    assert "<a " not in out
    assert "[x](javascript:alert(1))" in out


def test_masked_link_cannot_add_an_attribute() -> None:
    """A double quote in the target never closes the href to add an attribute to the tag."""
    out = markdown('[x](https://a"onmouseover="alert(1))')
    assert "<a " not in out


def test_embed_parts() -> None:
    """An embed shows its colour border, author, title, description, fields, footer and image frame."""
    embed = {
        "color": 0xFF8800,
        "author": {"name": "Auteur"},
        "title": "Titre",
        "description": "Texte",
        "fields": [
            {"name": "Petit", "value": "a", "inline": True},
            {"name": "Grand", "value": "b"},
        ],
        "footer": {"text": "Pied"},
        "image": {"url": "https://example.org/a.png"},
        "thumbnail": {"url": "https://example.org/b.png"},
    }
    out = message_html({"embeds": [embed]})
    assert "border-left-color:#ff8800" in out
    assert "border-left-color:#00ff00" in message_html({"embeds": [{"color": 0x00FF00}]})
    assert '<div class="embed-author">Auteur</div>' in out
    assert '<div class="embed-title">Titre</div>' in out
    assert "Texte" in out
    assert '<div class="embed-field inline">' in out
    assert '<div class="embed-field">' in out
    assert '<div class="embed-footer">Pied</div>' in out
    assert 'class="embed-image"' in out
    assert 'class="embed-thumbnail"' in out
    assert "example.org" not in out


def test_button_styles() -> None:
    """Styles 1 to 5 each get their class, and emoji, disabled state and an escaped label show."""
    for style, css in {1: "primary", 2: "secondary", 3: "success", 4: "danger", 5: "link"}.items():
        row = {"type": 1, "components": [{"type": 2, "style": style, "label": "Go"}]}
        assert f'class="button {css}"' in message_html({"components": [row]})
    button = {"type": 2, "style": 1, "label": "<b>x</b>", "emoji": {"name": "🔧"}, "disabled": True}
    out = message_html({"components": [{"type": 1, "components": [button]}]})
    assert 'class="button primary disabled"' in out
    assert '<span class="emoji">🔧</span>' in out
    assert "&lt;b&gt;x&lt;/b&gt;" in out
    assert "<b>" not in out
    enabled = {"type": 2, "style": 1, "label": "Ok", "emoji": {"name": "✅"}}
    out = message_html({"components": [{"type": 1, "components": [enabled]}]})
    assert 'class="button primary"' in out
    assert "disabled" not in out


def test_ephemeral_note() -> None:
    """The private-message note shows only when the message is ephemeral."""
    assert EPHEMERAL_NOTE in message_html({"content": "a", "ephemeral": True})
    assert EPHEMERAL_NOTE not in message_html({"content": "a", "ephemeral": False})


def test_page_themes_and_no_external_resource() -> None:
    """A page carries its theme and loads nothing from outside."""
    sent = [Sent("message", {"content": "a", "embeds": [], "components": [], "ephemeral": False})]
    assert 'class="dark"' in page(sent, theme="dark")
    assert 'class="light"' in page(sent, theme="light")
    for theme in ("dark", "light"):
        built = page(sent, theme=theme)
        for forbidden in ("<script", "<link", 'src="http', "url(http"):
            assert forbidden not in built


async def test_markup_is_deterministic() -> None:
    """Two builds are equal and no component id leaks into the markup."""
    first = await SCREENS["fm-edit"]()
    second = await SCREENS["fm-edit"]()
    assert markup(first.sent, first.names) == markup(second.sent, second.names)
    ids = [c["custom_id"] for s in first.sent for row in s.payload["components"] for c in row["components"] if "custom_id" in c]
    assert ids
    assert all(i not in markup(first.sent, first.names) for i in ids)


async def test_screens_need_no_browser() -> None:
    """Building every screen never imports playwright."""
    for build in SCREENS.values():
        await build()
    assert "playwright" not in sys.modules


async def test_screens_leave_data_and_logs_alone() -> None:
    """Building every screen from a throwaway folder creates no data or logs folder."""
    for build in SCREENS.values():
        await build()
    assert not Path("data").exists()
    assert not Path("logs").exists()


def test_snapshot_is_written_only_on_request(tmp_path: Path) -> None:
    """A stale snapshot fails and stays as it is, unless the update flag is set."""
    path = tmp_path / "x.html"
    path.write_text("old", encoding="utf-8")
    with pytest.raises(AssertionError):
        _compare(path, "new", update=False)
    assert path.read_text(encoding="utf-8") == "old"
    _compare(path, "new", update=True)
    assert path.read_text(encoding="utf-8") == "new"
