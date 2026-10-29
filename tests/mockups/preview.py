"""Turn what the bot sends into a Discord-like HTML page, with no browser and no network."""

from __future__ import annotations

from dataclasses import dataclass, field
import html
import re
from typing import TYPE_CHECKING, Any, Literal
from unittest.mock import AsyncMock, MagicMock

from emoji import emojize

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from discord import Embed, ui

BOT_NAME = "FablaBot"
SENT_AT = "Aujourd'hui à 14:02"
EPHEMERAL_NOTE = "Vous seul·e pouvez voir ceci · Ignorer le message"

# Button styles as Discord numbers them (primary, secondary, success, danger, link).
BUTTON_CLASS = {1: "primary", 2: "secondary", 3: "success", 4: "danger", 5: "link"}


@dataclass
class Sent:
    """One thing the bot sent, as Discord's API would receive it: a message (or its edit) or a modal."""

    kind: Literal["message", "modal"]
    payload: dict[str, Any]


@dataclass
class Capture:
    """Record every message a stub interaction is asked to send."""

    sent: list[Sent] = field(default_factory=list[Sent])

    def interaction(self, *, guild_id: int = 1234) -> MagicMock:
        """Build a stub interaction whose replies land in ``sent``."""
        interaction = MagicMock()
        interaction.guild.id = guild_id
        interaction.guild_id = guild_id
        interaction.guild.emojis = []
        interaction.response.send_message = AsyncMock(side_effect=self._message)
        interaction.response.edit_message = AsyncMock(side_effect=self._message)
        interaction.followup.send = AsyncMock(side_effect=self._message)
        interaction.response.defer = AsyncMock()
        return interaction

    async def _message(
        self,
        content: str | None = None,
        *,
        embed: Embed | None = None,
        embeds: list[Embed] | None = None,
        view: ui.View | None = None,
        ephemeral: bool = False,
        **_: object,
    ) -> None:
        """Keep a message as Discord's API would receive it."""
        all_embeds = [*(embeds or []), *([embed] if embed else [])]
        self.sent.append(
            Sent(
                "message",
                {
                    "content": content or "",
                    "embeds": [e.to_dict() for e in all_embeds],
                    "components": view.to_components() if view else [],
                    "ephemeral": ephemeral,
                },
            )
        )


# region ====== Markdown ======

_CODE = re.compile(r"`([^`\n]+)`")
_LINK = re.compile(r"\[([^\]\n]+)\]\((https?://[^)\s\"]+)\)")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_UNDERLINE = re.compile(r"__(.+?)__")
_ITALIC = re.compile(r"(?<![\w*])[*_](?![\s*_])(.+?)(?<![\s*_])[*_](?![\w*])")
_STRIKE = re.compile(r"~~(.+?)~~")
_MENTION = re.compile(r"&lt;(@&amp;|@!?|#)(\d+)&gt;")


def _inline(text: str, names: Mapping[int, str]) -> str:
    """Render one line of Discord inline markdown; ``text`` is already escaped."""
    codes: list[str] = []

    def keep_code(match: re.Match[str]) -> str:
        codes.append(f"<code>{match[1]}</code>")
        return f"\x00{len(codes) - 1}\x00"

    def mention(match: re.Match[str]) -> str:
        sigil = "#" if match[1] == "#" else "@"
        name = names.get(int(match[2]), "inconnu")
        return f'<span class="mention">{sigil}{html.escape(name)}</span>'

    text = _CODE.sub(keep_code, text)
    text = _LINK.sub(r'<a href="\2">\1</a>', text)
    text = _BOLD.sub(r"<strong>\1</strong>", text)
    text = _UNDERLINE.sub(r"<u>\1</u>", text)
    text = _ITALIC.sub(r"<em>\1</em>", text)
    text = _STRIKE.sub(r"<s>\1</s>", text)
    text = _MENTION.sub(mention, text)
    return re.sub(r"\x00(\d+)\x00", lambda m: codes[int(m[1])], text)


def markdown(text: str, names: Mapping[int, str] | None = None) -> str:
    """Render the Discord markdown the bot uses: headings, quotes, lists, inline styles, mentions, emoji.

    Args:
        text (str): The raw message or embed text.
        names (Mapping[int, str] | None): Display names for user, role and channel IDs in mentions.

    Returns:
        str: The HTML.
    """
    names = names or {}
    blocks: list[str] = []
    quote: list[str] = []

    def flush_quote() -> None:
        if quote:
            blocks.append(f"<blockquote>{'<br>'.join(quote)}</blockquote>")
            quote.clear()

    for raw in emojize(text, language="alias").split("\n"):
        line = html.escape(raw, quote=False)
        if line.startswith("&gt; "):
            quote.append(_inline(line[5:], names))
            continue
        flush_quote()
        heading = re.match(r"(#{1,3}) (.+)", line)
        if heading:
            level = len(heading[1])
            blocks.append(f"<h{level}>{_inline(heading[2], names)}</h{level}>")
        elif line.startswith("-# "):
            blocks.append(f'<div class="subtext">{_inline(line[3:], names)}</div>')
        elif line.startswith(("- ", "* ")):
            blocks.append(f'<div class="li">{_inline(line[2:], names)}</div>')
        elif line:
            blocks.append(f"<div>{_inline(line, names)}</div>")
        else:
            blocks.append('<div class="blank"></div>')
    flush_quote()
    return "\n".join(blocks)


# endregion ====== Markdown ======

# region ====== Message parts ======


def _embed(data: Mapping[str, Any], names: Mapping[int, str]) -> str:
    """Render one embed from its API dict."""
    color = data.get("color")
    border = f' style="border-left-color:#{color:06x}"' if isinstance(color, int) else ""
    parts: list[str] = []
    if data.get("thumbnail"):
        parts.append('<div class="embed-thumbnail"></div>')
    if author := data.get("author"):
        parts.append(f'<div class="embed-author">{html.escape(author.get("name", ""))}</div>')
    if title := data.get("title"):
        parts.append(f'<div class="embed-title">{_inline(html.escape(title, quote=False), names)}</div>')
    if description := data.get("description"):
        parts.append(f'<div class="embed-description">{markdown(description, names)}</div>')
    if fields := data.get("fields"):
        cells = "".join(
            f'<div class="embed-field{" inline" if f.get("inline") else ""}">'
            f'<div class="embed-field-name">{_inline(html.escape(f["name"], quote=False), names)}</div>'
            f'<div class="embed-field-value">{markdown(f["value"], names)}</div></div>'
            for f in fields
        )
        parts.append(f'<div class="embed-fields">{cells}</div>')
    if data.get("image"):
        parts.append('<div class="embed-image"></div>')
    if footer := data.get("footer"):
        parts.append(f'<div class="embed-footer">{html.escape(footer.get("text", ""))}</div>')
    return f'<div class="embed"{border}>\n' + "\n".join(parts) + "\n</div>"


def _button(data: Mapping[str, Any]) -> str:
    """Render one button from its API dict."""
    style = BUTTON_CLASS.get(data.get("style", 2), "secondary")
    emoji = data.get("emoji") or {}
    icon = f'<span class="emoji">{html.escape(emoji["name"])}</span>' if emoji.get("name") else ""
    label = html.escape(data.get("label") or "")
    link = '<span class="link-icon">↗</span>' if style == "link" else ""
    disabled = " disabled" if data.get("disabled") else ""
    return f'<span class="button {style}{disabled}">{icon}{label}{link}</span>'


def _row(data: Mapping[str, Any]) -> str:
    """Render one action row."""
    items: list[str] = []
    for child in data.get("components", []):
        kind = child.get("type")
        if kind == 2:
            items.append(_button(child))
    return f'<div class="row">{"".join(items)}</div>'


def message_html(payload: Mapping[str, Any], names: Mapping[int, str] | None = None) -> str:
    """Render one bot message: author line, content, embeds, components and the ephemeral note."""
    names = names or {}
    body = [
        '<div class="avatar">F</div><div class="main">',
        f'<div class="header"><span class="name">{BOT_NAME}</span><span class="badge">APP</span>'
        f'<span class="time">{SENT_AT}</span></div>',
    ]
    if payload.get("content"):
        body.append(f'<div class="content">{markdown(payload["content"], names)}</div>')
    body.extend(_embed(e, names) for e in payload.get("embeds", []))
    body.extend(_row(r) for r in payload.get("components", []))
    if payload.get("ephemeral"):
        body.append(f'<div class="ephemeral"><span class="eye">👁</span>{EPHEMERAL_NOTE}</div>')
    body.append("</div>")
    css = "message ephemeral-message" if payload.get("ephemeral") else "message"
    return f'<div class="{css}">' + "\n".join(body) + "</div>"


# endregion ====== Message parts ======

STYLE = """
* { box-sizing: border-box; }
body { margin: 0; font: 16px/1.375 "gg sans", "Noto Sans", "Helvetica Neue", Helvetica, Arial, sans-serif; }
.dark { --bg:#313338; --text:#dbdee1; --muted:#949ba4; --strong:#f2f3f5; --embed:#2b2d31; --line:#1e1f22;
  --code:#2b2d31; --secondary:#4e5058; --select:#1e1f22; --modal:#313338; --input:#1e1f22; --ephemeral:#393c47;
  --mention:rgba(88,101,242,.3); --mention-text:#c9cdfb; --quote:#4e5058; --link:#00a8fc; }
.light { --bg:#ffffff; --text:#313338; --muted:#5c5e66; --strong:#060607; --embed:#f2f3f5; --line:#e3e5e8;
  --code:#f2f3f5; --secondary:#6d6f78; --select:#ebedef; --modal:#ffffff; --input:#ebedef; --ephemeral:#eef0fd;
  --mention:rgba(88,101,242,.15); --mention-text:#505cdc; --quote:#c4c9ce; --link:#006ce7; }
#screen { background: var(--bg); color: var(--text); padding: 16px 0 20px; }
.message { display: flex; gap: 16px; padding: 4px 16px 4px 16px; }
.ephemeral-message { background: var(--ephemeral); border-left: 2px solid #5865f2; padding-left: 14px; }
.avatar { flex: 0 0 40px; height: 40px; border-radius: 50%; background: #ea5b25; color: #fff; font-weight: 700;
  display: flex; align-items: center; justify-content: center; margin-top: 2px; }
.main { min-width: 0; flex: 1; }
.header { display: flex; align-items: baseline; gap: 6px; }
.name { color: var(--strong); font-weight: 600; }
.badge { background: #5865f2; color: #fff; font-size: 10px; font-weight: 600; padding: 1px 4px; border-radius: 3px;
  position: relative; top: -2px; }
.time { color: var(--muted); font-size: 12px; }
.content { overflow-wrap: anywhere; }
.content div, .embed-description div { min-height: 1px; }
.blank { height: 1.375em; }
h1 { font-size: 24px; margin: 8px 0 0; color: var(--strong); line-height: 1.375; }
h2 { font-size: 20px; margin: 8px 0 0; color: var(--strong); }
h3 { font-size: 16px; margin: 8px 0 0; color: var(--strong); }
.subtext { font-size: 12px; color: var(--muted); }
.li { position: relative; padding-left: 20px; }
.li::before { content: "•"; position: absolute; left: 4px; }
blockquote { margin: 0; padding: 0 8px 0 12px; border-left: 4px solid var(--quote); }
code { background: var(--code); border-radius: 4px; padding: 0 3px; font: 85% Menlo, Consolas, monospace; }
a { color: var(--link); text-decoration: none; }
.mention { background: var(--mention); color: var(--mention-text); border-radius: 3px; padding: 0 2px; font-weight: 500; }
.embed { margin-top: 4px; max-width: 520px; background: var(--embed); border-left: 4px solid var(--line);
  border-radius: 4px; padding: 8px 16px 16px 12px; display: grid; gap: 8px; font-size: 14px; }
.embed-title { color: var(--strong); font-weight: 600; font-size: 16px; margin-top: 8px; }
.embed-author { font-weight: 600; font-size: 14px; margin-top: 8px; }
.embed-fields { display: flex; flex-wrap: wrap; gap: 8px 16px; }
.embed-field { flex: 1 1 100%; }
.embed-field.inline { flex: 1 1 30%; }
.embed-field-name { color: var(--strong); font-weight: 600; }
.embed-image { width: 100%; aspect-ratio: 16 / 9; border-radius: 4px; background: var(--line); }
.embed-thumbnail { justify-self: end; width: 80px; height: 80px; border-radius: 4px; background: var(--line); }
.embed-footer { font-size: 12px; color: var(--muted); }
.row { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }
.button { display: inline-flex; align-items: center; gap: 6px; min-height: 32px; padding: 2px 16px; border-radius: 8px;
  color: #fff; font-size: 14px; font-weight: 500; }
.primary { background: #5865f2; } .secondary { background: var(--secondary); } .success { background: #248046; }
.danger { background: #da373c; } .link { background: var(--secondary); } .disabled { opacity: .5; }
.ephemeral { margin-top: 4px; font-size: 12px; color: var(--muted); }
.eye { margin-right: 4px; }
.placeholder { color: var(--muted); }
"""


def markup(sent: Sequence[Sent], names: Mapping[int, str] | None = None) -> str:
    """Build the markup of one screen, one message or modal per line, so a change reads as a short diff.

    Args:
        sent (Sequence[Sent]): What the bot sent, in order.
        names (Mapping[int, str] | None): Display names for mentioned IDs.

    Returns:
        str: The screen's markup, without the page around it.
    """
    parts = [message_html(s.payload, names) for s in sent if s.kind == "message"]
    return "\n".join(parts) + "\n"


def page(sent: Sequence[Sent], *, theme: Literal["dark", "light"], names: Mapping[int, str] | None = None) -> str:
    """Build the whole HTML page for one screen.

    Args:
        sent (Sequence[Sent]): What the bot sent, in order.
        theme (str): ``dark`` or ``light``.
        names (Mapping[int, str] | None): Display names for mentioned IDs.

    Returns:
        str: A standalone HTML document.
    """
    return (
        f'<!doctype html><html lang="fr"><head><meta charset="utf-8"><style>{STYLE}</style></head>'
        f'<body class="{theme}"><div id="screen">\n{markup(sent, names)}</div></body></html>\n'
    )
