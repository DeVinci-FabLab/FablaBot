"""Text catalogue: one TOML file per language, English as the reference every other file falls back to."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
import logging
from pathlib import Path
import tomllib
from typing import TYPE_CHECKING, Final, override

from discord import app_commands

if TYPE_CHECKING:
    from collections.abc import Mapping

    from discord import Locale

logger = logging.getLogger(__name__)

LOCALES_DIR: Final = Path(__file__).resolve().parent.parent / "locales"
"""Folder holding one ``<locale>.toml`` file per language."""

REFERENCE: Final = "en"
"""The locale every other one is checked against and falls back to."""

PLURAL_FORMS: Final = frozenset({"one", "other"})

type Entry = str | Mapping[str, str]
"""A text, or its ``one`` and ``other`` forms."""

_WARNED: set[tuple[str, str]] = set()
"""The (locale, key) pairs already reported as missing, so a gap shows once per process."""


class CatalogError(Exception):
    """The reference catalogue cannot be read, so the bot cannot speak at all."""


def _flatten(table: Mapping[str, object], prefix: str = "") -> dict[str, Entry]:
    """Flatten nested tables to dotted keys; a table of exactly ``one`` and ``other`` texts is kept as a plural."""
    flat: dict[str, Entry] = {}
    for name, value in table.items():
        key = f"{prefix}{name}"
        if isinstance(value, str):
            flat[key] = value
        elif isinstance(value, dict):
            if set(value) == PLURAL_FORMS and all(isinstance(form, str) for form in value.values()):
                flat[key] = {form: str(text) for form, text in value.items()}
            else:
                flat.update(_flatten(value, f"{key}."))
        else:
            msg = f"{key}: expected a text or a table, got {type(value).__name__}"
            raise ValueError(msg)  # noqa: TRY004 - a malformed file is a value problem, like a TOML syntax error
    return flat


def read_locale(path: Path) -> dict[str, Entry]:
    """Read one locale file.

    Args:
        path (Path): The ``.toml`` file.

    Returns:
        dict[str, Entry]: The texts by dotted key.

    Raises:
        OSError: If the file cannot be read.
        ValueError: If the file is not valid TOML (``TOMLDecodeError``) or holds something else than texts.
    """
    with path.open("rb") as handle:
        return _flatten(tomllib.load(handle))


@dataclass(frozen=True)
class Texts:
    """The texts of one locale, with English behind them.

    Attributes:
        locale (str): The locale of the file in use.
        entries (Mapping[str, Entry]): Its texts.
        reference (Mapping[str, Entry]): The English texts.
    """

    locale: str
    entries: Mapping[str, Entry]
    reference: Mapping[str, Entry]

    def _entry(self, key: str) -> Entry:
        """Return the entry for ``key``, or the English one with a single warning when the locale lacks it."""
        if key in self.entries:
            return self.entries[key]
        if (self.locale, key) not in _WARNED:
            _WARNED.add((self.locale, key))
            logger.warning(f"Locale {self.locale} has no text for {key}; using English")
        return self.reference[key]

    def get(self, key: str, **values: object) -> str:
        """Return a text with its ``{placeholders}`` filled.

        Args:
            key (str): The dotted key.
            **values (object): The placeholder values.

        Returns:
            str: The text, in English when the locale lacks the key.
        """
        entry = self._entry(key)
        assert isinstance(entry, str), f"{key} is a plural; use plural()"
        return entry.format(**values)

    def plural(self, key: str, count: int, **values: object) -> str:
        """Return the ``one`` form for a count of 1 and the ``other`` form otherwise.

        Args:
            key (str): The dotted key of a plural entry.
            count (int): The number the text is about; also available as ``{count}``.
            **values (object): The other placeholder values.

        Returns:
            str: The text, in English when the locale lacks the key.
        """
        entry = self._entry(key)
        assert not isinstance(entry, str), f"{key} is not a plural; use get()"
        return entry["one" if count == 1 else "other"].format(count=count, **values)


@dataclass(frozen=True)
class Catalog:
    """Every loaded locale, by code.

    Attributes:
        locales (Mapping[str, Mapping[str, Entry]]): The texts of each locale; ``en`` is always there.
    """

    locales: Mapping[str, Mapping[str, Entry]]

    def texts(self, locale: str | None) -> Texts:
        """Pick the texts for a Discord locale: the exact file, then its language, then English.

        Args:
            locale (str | None): A Discord locale such as ``fr`` or ``en-US``, or None.

        Returns:
            Texts: The texts to speak with.
        """
        reference = self.locales[REFERENCE]
        for candidate in self._candidates(locale):
            if candidate in self.locales:
                return Texts(candidate, self.locales[candidate], reference)
        return Texts(REFERENCE, reference, reference)

    def translation(self, locale: str, key: str) -> str | None:
        """Return a text from a shipped non-English file, for Discord's command localizations.

        Args:
            locale (str): A Discord locale.
            key (str): The dotted key.

        Returns:
            str | None: The text, None when no non-English file covers the locale or the key.
        """
        for candidate in self._candidates(locale):
            if candidate == REFERENCE:
                return None
            entry = self.locales.get(candidate, {}).get(key)
            if isinstance(entry, str):
                return entry
        return None

    @staticmethod
    def _candidates(locale: str | None) -> list[str]:
        """List the file names to try for a Discord locale, most specific first."""
        if not locale:
            return []
        code = locale.replace("_", "-")
        return [code, code.split("-")[0]]


def load_catalog(directory: Path = LOCALES_DIR) -> Catalog:
    """Read every locale file of a folder.

    Args:
        directory (Path): The folder with the ``.toml`` files.

    Returns:
        Catalog: English and every other file that could be read.

    Raises:
        CatalogError: If ``en.toml`` cannot be read. Another broken file is logged and left out.
    """
    locales: dict[str, Mapping[str, Entry]] = {}
    reference_path = directory / f"{REFERENCE}.toml"
    try:
        locales[REFERENCE] = read_locale(reference_path)
    except (OSError, ValueError) as exc:
        msg = f"Cannot read the reference catalogue {reference_path}: {exc}"
        raise CatalogError(msg) from exc
    for path in sorted(directory.glob("*.toml")):
        if path.stem == REFERENCE:
            continue
        try:
            locales[path.stem] = read_locale(path)
        except (OSError, ValueError) as exc:
            logger.error(f"Cannot read the catalogue {path}; its speakers get English: {exc}")  # noqa: TRY400 - the cause is in the message
    return Catalog(locales)


@cache
def catalog() -> Catalog:
    """Load the shipped catalogue once.

    Returns:
        Catalog: The shipped locales.
    """
    return load_catalog()


class CatalogTranslator(app_commands.Translator):
    """Give Discord the catalogue's translation of every command string that carries a ``key``."""

    def __init__(self, source: Catalog) -> None:
        """Keep the catalogue to read from.

        Args:
            source (Catalog): The loaded catalogue.
        """
        super().__init__()
        self.source = source

    @override
    async def translate(
        self,
        string: app_commands.locale_str,
        locale: Locale,
        context: app_commands.TranslationContextTypes,
    ) -> str | None:
        """Return the text for ``string.extras["key"]`` in ``locale``.

        Args:
            string (app_commands.locale_str): The string to translate.
            locale (Locale): The target Discord locale.
            context (app_commands.TranslationContextTypes): Where the string is used; unused.

        Returns:
            str | None: The translation, None when the string has no key or the locale no text.
        """
        key = string.extras.get("key")
        if not isinstance(key, str):
            return None
        return self.source.translation(locale.value, key)
