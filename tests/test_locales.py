"""The shipped catalogue is complete, typeset for each language, and read safely."""

from __future__ import annotations

from fnmatch import fnmatch
import logging
from pathlib import Path
import re
from string import Formatter
import tomllib
from unittest.mock import MagicMock

from discord import Locale, app_commands
import pytest

from fablabot.helpers import i18n
from fablabot.helpers.help_page import DOMAINS, EVERYONE_DOMAIN, line_key
from fablabot.helpers.i18n import REFERENCE, Catalog, CatalogError, Entry, Texts, catalog, load_catalog, read_locale

ROOT = Path(__file__).parent.parent
TRANSLATIONS = ("fr", "de")
NBSP = "\N{NO-BREAK SPACE}"
NNBSP = "\N{NARROW NO-BREAK SPACE}"


@pytest.fixture(autouse=True)
def _fresh_warnings() -> None:
    """Forget which missing keys were already reported, so each test sees its own warning."""
    i18n._WARNED.clear()


def _forms(entries: dict[str, Entry] | object) -> dict[str, str]:
    """Flatten a locale to one text per key, a plural giving one text per form."""
    flat: dict[str, str] = {}
    for key, entry in entries.items():  # pyright: ignore[reportAttributeAccessIssue]  # a Mapping of entries
        if isinstance(entry, str):
            flat[key] = entry
        else:
            flat.update({f"{key}.{form}": text for form, text in entry.items()})
    return flat


def _placeholders(text: str) -> set[str]:
    """Return the names of the ``{placeholders}`` in a text."""
    return {name for _, name, _, _ in Formatter().parse(text) if name}


def gap_report(source: Catalog, locale: str) -> list[str]:
    """List every way ``locale`` differs from the reference: missing keys, extra keys, changed placeholders."""
    reference = _forms(source.locales[REFERENCE])
    other = _forms(source.locales[locale])
    gaps = [f"missing: {key}" for key in reference if key not in other]
    gaps += [f"unknown: {key}" for key in other if key not in reference]
    gaps += [
        f"placeholders: {key} {sorted(_placeholders(other[key]))} != {sorted(_placeholders(text))}"
        for key, text in reference.items()
        if key in other and _placeholders(other[key]) != _placeholders(text)
    ]
    return gaps


def test_shipped_locales_match_english() -> None:
    """Each shipped translation has the keys and the placeholders of English, and the report lists every gap."""
    shipped = catalog()
    problems = {locale: gap_report(shipped, locale) for locale in TRANSLATIONS}
    assert {locale: gaps for locale, gaps in problems.items() if gaps} == {}


def test_gap_report_lists_each_kind() -> None:
    """The report names a missing key, an unknown key and a changed placeholder."""
    synthetic = Catalog(
        {
            "en": {"a": "one {x}", "b": "two", "c": {"one": "{count} x", "other": "{count} xs"}},
            "xx": {"a": "uno {y}", "z": "tres", "c": {"one": "{count} x", "other": "{n} xs"}},
        }
    )
    report = gap_report(synthetic, "xx")
    assert "missing: b" in report
    assert "unknown: z" in report
    assert any(line.startswith("placeholders: a ") for line in report)
    assert any(line.startswith("placeholders: c.other ") for line in report)
    assert len(report) == 4


def test_french_typography() -> None:
    """French puts a no-break space before a colon and a narrow no-break space before ``?``, ``!`` and ``;``."""
    wrong: list[str] = []
    for key, text in _forms(catalog().locales["fr"]).items():
        for index, char in enumerate(text):
            before = text[index - 1] if index else ""
            if (char == ":" and before != NBSP) or (char in "?!;" and before != NNBSP):
                wrong.append(f"{key}: {text!r} at {index}")
    assert wrong == []


@pytest.mark.parametrize("code", sorted(catalog().locales))
def test_public_help_refusal_names_the_option_to_drop(code: str) -> None:
    """The refusal of a public ``/help`` gives its way out in every language."""
    assert "`show`" in catalog().texts(code).get("help.public_channel_only", channel="<#1>")


def test_informal_address() -> None:
    """French says tu and German says du: no vous or Sie form anywhere in the files."""
    formal = {"fr": r"\b(vous|votre|vos)\b", "de": r"\b(Sie|Ihr|Ihre|Ihren|Ihnen)\b"}
    found = [
        f"{locale}.{key}"
        for locale, pattern in formal.items()
        for key, text in _forms(catalog().locales[locale]).items()
        if re.search(pattern, text, flags=re.IGNORECASE if locale == "fr" else 0)
    ]
    assert found == []


def test_missing_key_falls_back_to_english_once(caplog: pytest.LogCaptureFixture) -> None:
    """A key absent from a locale gives the English text, and the gap is reported once, naming locale and key."""
    texts = Texts(locale="fr", entries={}, reference={"greeting": "Hello {name}"})
    with caplog.at_level(logging.WARNING):
        assert texts.get("greeting", name="Ada") == "Hello Ada"
        assert texts.get("greeting", name="Bob") == "Hello Bob"
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "fr" in warnings[0].getMessage()
    assert "greeting" in warnings[0].getMessage()


def test_missing_plural_falls_back_to_english_once(caplog: pytest.LogCaptureFixture) -> None:
    """A plural absent from a locale gives the English forms, and the gap is reported once."""
    reference = {"gap.count": {"one": "{count} item", "other": "{count} items"}}
    texts = Texts(locale="fr", entries={}, reference=reference)
    with caplog.at_level(logging.WARNING):
        assert texts.plural("gap.count", 1) == "1 item"
        assert texts.plural("gap.count", 3) == "3 items"
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "fr" in warnings[0].getMessage()
    assert "gap.count" in warnings[0].getMessage()


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("fr", "aide"),
        ("de", "hilfe"),
        ("en-US", "help"),
        ("en-GB", "help"),
        ("es-ES", "help"),
        ("pt-BR", "help"),
        (None, "help"),
    ],
)
def test_texts_follow_the_discord_locale(code: str | None, expected: str) -> None:
    """A Discord locale picks its own file, then its language's file, then English."""
    assert catalog().texts(code).get("help.slash.name") == expected


def test_plural_picks_one_and_other() -> None:
    """A plural entry gives its ``one`` form for 1 and its ``other`` form otherwise, with the count filled in."""
    entries = {"n": {"one": "{count} item", "other": "{count} items"}}
    texts = Texts(locale="en", entries=entries, reference=entries)
    assert texts.plural("n", 1) == "1 item"
    assert texts.plural("n", 0) == "0 items"
    assert texts.plural("n", 5) == "5 items"


def test_read_locale_flattens_tables_and_keeps_plurals(tmp_path: Path) -> None:
    """Dotted keys come from nested tables, and a table of exactly one and other stays a plural."""
    path = tmp_path / "en.toml"
    path.write_text(
        '[a.b]\nc = "x"\n[a.p]\none = "1"\nother = "n"\n[a.q]\none = "1"\nother = "n"\nmore = "m"\n', encoding="utf-8"
    )
    assert read_locale(path) == {
        "a.b.c": "x",
        "a.p": {"one": "1", "other": "n"},
        "a.q.one": "1",
        "a.q.other": "n",
        "a.q.more": "m",
    }


def test_unreadable_reference_stops_with_its_path(tmp_path: Path) -> None:
    """English missing or broken stops the load with an error naming the file."""
    with pytest.raises(CatalogError, match=r"en\.toml"):
        load_catalog(tmp_path)
    (tmp_path / "en.toml").write_text("not = [valid", encoding="utf-8")
    with pytest.raises(CatalogError, match=r"en\.toml"):
        load_catalog(tmp_path)


def test_unreadable_translation_falls_back_to_english(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """A broken translation is left out with one error naming its file, and its speakers get English."""
    (tmp_path / "en.toml").write_text('[hi]\nname = "hello"\n', encoding="utf-8")
    (tmp_path / "fr.toml").write_text("not = [valid", encoding="utf-8")
    with caplog.at_level(logging.ERROR):
        loaded = load_catalog(tmp_path)
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert "fr.toml" in errors[0].getMessage()
    assert "fr" not in loaded.locales
    assert loaded.texts("fr").get("hi.name") == "hello"


def test_translation_comes_from_shipped_non_english_files_only() -> None:
    """The translator finds a French text, and finds nothing for English or a language with no file."""
    shipped = catalog()
    assert shipped.translation("fr", "help.slash.name") == "aide"
    assert shipped.translation("en-US", "help.slash.name") is None
    assert shipped.translation("es-ES", "help.slash.name") is None
    assert shipped.translation("fr", "no.such.key") is None


async def test_translator_reads_the_key_extra() -> None:
    """The translator answers for a string that carries a catalogue key, and for no other."""
    translator = i18n.CatalogTranslator(catalog())
    keyed = app_commands.locale_str("help", key="help.slash.name")
    context = app_commands.TranslationContext(app_commands.TranslationContextLocation.command_name, MagicMock())
    assert await translator.translate(keyed, Locale.german, context) == "hilfe"
    assert await translator.translate(keyed, Locale.american_english, context) is None
    assert await translator.translate(app_commands.locale_str("plain"), Locale.german, context) is None


def test_package_data_ships_every_locale_file() -> None:
    """Every locale file matches a package-data pattern, so the built wheel carries it."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    patterns = project["tool"]["setuptools"]["package-data"]["fablabot"]
    files = sorted((ROOT / "src" / "fablabot" / "locales").glob("*.toml"))
    assert {f.stem for f in files} >= {"en", *TRANSLATIONS}
    unshipped = [f.name for f in files if not any(fnmatch(f"locales/{f.name}", pattern) for pattern in patterns)]
    assert unshipped == []


def test_catalogue_lines_match_the_domains() -> None:
    """Each listed command has a line in English, and each line of English belongs to a listed command."""
    english = catalog().locales[REFERENCE]
    listed = {
        line_key(name if domain == EVERYONE_DOMAIN else f"{domain} {name}") for domain, names in DOMAINS for name in names
    }
    assert listed <= english.keys()
    lines = {key for key in english if key.startswith("help.cmd.")}
    assert lines == listed
