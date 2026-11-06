"""``/info`` tells Respo Numérique and the admin roles how the bot and their server stand, privately, for their server only."""

# ruff: noqa: RUF001 - the settings paths use the separator of Discord's own menus

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
import importlib.metadata
import json
import logging
from pathlib import Path
import re
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

from conftest import LISTED_GUILD, OTHER_LISTED_GUILD
from discord import Permissions
import pytest
from test_server_check import codes, global_command, problems, ready_server, role_entries, run

from fablabot import db, guild_config
from fablabot.cogs import audit_watch, info_management
from fablabot.helpers import i18n, problem_text, server_check
from fablabot.helpers.constants import ADMIN_ROLES, INFO_ROLES, ErrorMessages, RoleNames
from fablabot.helpers.info_page import Facts, build_info, short_revision
from fablabot.helpers.server_check import FIXES, INVITE_PERMISSIONS, Problem

if TYPE_CHECKING:
    from collections.abc import Iterator

    import discord
    import simcord

GUILD = int(LISTED_GUILD)
OTHER = int(OTHER_LISTED_GUILD)
UNSERVED = 333333333333333333
GONE_CHANNEL = 444444444444444444
GREEN = 0x57F287
ORANGE = 0xF5A623
RED = 0xED4245
PATH = "Paramètres du serveur › Intégrations › FablaBot"
SHA = "8822a20c5d1e4b7f9a0b3c6d8e1f2a4b5c6d7e8f"


@pytest.fixture(autouse=True)
def _fresh_watch_list() -> Iterator[None]:
    """Read the watched accounts again for each test, and leave the next test the list it found."""
    audit_watch.watched_ids.cache_clear()
    yield
    audit_watch.watched_ids.cache_clear()


def _facts(**changes: Any) -> Facts:
    """The facts of the validated example: version 0.14.0, up for 3 hours, schema 4, six copies, four servers."""
    return replace(
        Facts(
            version="0.14.0",
            uptime=timedelta(hours=3),
            latency_ms=42,
            schema_version=4,
            database_bytes=1_200_000,
            copies=6,
            servers=4,
            commands=9,
            watched=1,
            commands_channel_id=1,
            log_channel_id=2,
            language=("fr", "fr"),
            revision="8822a20",
        ),
        **changes,
    )


def _warning(code: str, detail: str) -> Problem:
    """A problem of a code that FIXES lists as a warning."""
    assert FIXES[code][0] == "warning"
    return Problem(code, detail)


LOG_CLOSED = _warning("integrations_role_missing", "/log: Pôle Numérique")
USER_CLOSED = _warning("integrations_role_missing", "/user: CoDir")
BLOCKING = Problem("commands_channel_missing")


def _fields(embed: discord.Embed) -> dict[str, str]:
    """Index the fields of an embed by name."""
    return {field.name or "": field.value or "" for field in embed.fields}


# region ====== The layout of the validated example ======


def test_the_embed_has_the_layout_of_the_validated_example() -> None:
    """Two warnings give the orange embed with its three inline blocks, its settings, its two problems and its footer."""
    embed = build_info(_facts(), [LOG_CLOSED, USER_CLOSED])

    assert embed.title == "📊 FablaBot : 2 points à corriger sur ce serveur"
    assert embed.colour is not None
    assert embed.colour.value == ORANGE
    assert embed.description == "✅ Le bot tourne normalement · ⚠️ ce serveur a 2 avertissements, 0 bloquant."
    assert [(field.name, field.inline) for field in embed.fields] == [
        ("🤖 Bot", True),
        ("🗃️ Base de données", True),
        ("🛰️ Instance", True),
        ("⚙️ Réglages de ce serveur", False),
        ("🔍 À corriger sur ce serveur", False),
    ]
    fields = _fields(embed)
    assert fields["🤖 Bot"] == "Version **0.14.0**\nEn ligne depuis **3 h**\nLatence **42 ms**"
    assert fields["🗃️ Base de données"] == "Version **4** · 1,2 Mo\n**6** sauvegardes"
    assert fields["🛰️ Instance"] == "**4** serveurs servis\n**9** commandes\n**1** compte surveillé"
    assert fields["⚙️ Réglages de ce serveur"] == "Commandes : <#1>\nLogs : <#2>\nLangue : français"
    assert fields["🔍 À corriger sur ce serveur"] == (
        f"⚠️ **`/log` n'est pas ouvert au rôle Pôle Numérique**\n→ {PATH} › `/log` : ajoute Pôle Numérique"
        f"\n\n⚠️ **`/user` n'est pas ouvert au rôle CoDir**\n→ {PATH} › `/user` : ajoute CoDir"
    )
    assert embed.footer.text == "Contrôle relancé à l'instant · révision 8822a20"


def test_a_server_with_no_problem_is_green_and_has_no_list() -> None:
    """Nothing to fix gives the green title, the green colour and no problem field."""
    embed = build_info(_facts(), [])

    assert embed.title == "📊 FablaBot : tout va bien"
    assert embed.colour is not None
    assert embed.colour.value == GREEN
    assert embed.description is not None
    assert "aucun problème" in embed.description
    assert "À corriger" not in " ".join(_fields(embed))


def test_one_warning_reads_in_the_singular() -> None:
    """One warning is one point, and the orange colour holds."""
    embed = build_info(_facts(), [LOG_CLOSED])

    assert embed.title == "📊 FablaBot : 1 point à corriger sur ce serveur"
    assert embed.colour is not None
    assert embed.colour.value == ORANGE
    assert embed.description == "✅ Le bot tourne normalement · ⚠️ ce serveur a 1 avertissement, 0 bloquant."


def test_anything_that_blocks_makes_the_embed_red_and_is_said_in_words() -> None:
    """A blocking problem turns the embed red, comes before the warnings, and says "bloquant" in text, not by colour alone."""
    embed = build_info(_facts(), [LOG_CLOSED, BLOCKING, USER_CLOSED])

    assert embed.title == "📊 FablaBot : 1 point bloquant sur ce serveur"
    assert embed.colour is not None
    assert embed.colour.value == RED
    assert embed.description == "✅ Le bot tourne normalement · ❌ ce serveur a 2 avertissements, 1 bloquant."
    first = _fields(embed)["🔍 À corriger sur ce serveur"].split("\n\n")[0]
    assert first.startswith("❌ **Aucun salon de commandes**")
    assert "bloquant" in first


def test_two_blocking_problems_read_in_the_plural() -> None:
    """Two blocking problems are two points bloquants."""
    embed = build_info(_facts(), [BLOCKING, Problem("log_channel_missing")])

    assert embed.title == "📊 FablaBot : 2 points bloquants sur ce serveur"


def test_the_footer_has_no_revision_when_the_image_gives_none() -> None:
    """Without a revision the footer stops after the control."""
    assert build_info(_facts(revision=None), []).footer.text == "Contrôle relancé à l'instant"


def test_a_line_with_no_value_is_left_out() -> None:
    """A latency Discord has not measured yet is not written, nor is a setting the server lacks shown as a channel."""
    embed = build_info(_facts(latency_ms=None, commands_channel_id=None, log_channel_id=None), [])

    fields = _fields(embed)
    assert fields["🤖 Bot"] == "Version **0.14.0**\nEn ligne depuis **3 h**"
    assert fields["⚙️ Réglages de ce serveur"] == "Commandes : aucun salon\nLogs : aucun salon\nLangue : français"


@pytest.mark.parametrize(
    ("count", "text"),
    [(0, "**0** compte surveillé"), (1, "**1** compte surveillé"), (2, "**2** comptes surveillés")],
)
def test_watched_accounts_are_a_count_in_the_right_number(count: int, text: str) -> None:
    """The watched accounts show as a count, singular up to one."""
    assert text in _fields(build_info(_facts(watched=count), []))["🛰️ Instance"]


@pytest.mark.parametrize(
    ("elapsed", "text"),
    [
        (timedelta(seconds=30), "moins d'une minute"),
        (timedelta(minutes=5), "5 min"),
        (timedelta(hours=3), "3 h"),
        (timedelta(hours=3, minutes=12), "3 h 12 min"),
        (timedelta(days=2, hours=4, minutes=30), "2 j 4 h"),
        (timedelta(days=2), "2 j"),
    ],
)
def test_the_uptime_is_written_in_words(elapsed: timedelta, text: str) -> None:
    """The uptime reads in minutes, hours or days, the way a person would say it."""
    assert f"En ligne depuis **{text}**" in _fields(build_info(_facts(uptime=elapsed), []))["🤖 Bot"]


@pytest.mark.parametrize(("size", "text"), [(900, "900 o"), (4_096, "4,1 Ko"), (1_200_000, "1,2 Mo"), (35_000_000, "35,0 Mo")])
def test_the_file_size_is_written_in_octets(size: int, text: str) -> None:
    """The database size uses French units and a decimal comma."""
    assert f" · {text}\n" in _fields(build_info(_facts(database_bytes=size), []))["🗃️ Base de données"] + "\n"


@pytest.mark.parametrize(
    ("language", "text"),
    [
        (("fr", "fr"), "français"),
        (("en-US", "en"), "anglais"),
        (("en-GB", "en"), "anglais"),
        (("de", "de"), "allemand"),
        (("es-ES", "en"), "es-ES, textes en anglais"),
    ],
)
def test_the_language_is_said_in_words(language: tuple[str, str], text: str) -> None:
    """A shipped language is named, and a declared one the bot lacks says which language its texts use instead."""
    assert f"Langue : {text}" in _fields(build_info(_facts(language=language), []))["⚙️ Réglages de ce serveur"]


def test_every_shipped_language_has_a_name() -> None:
    """Each locale file of the bot has its French name, so no shipped language shows as a code."""
    shipped = {path.stem for path in i18n.LOCALES_DIR.glob("*.toml")}
    assert shipped == set(problem_text.LANGUAGE_NAMES)


# endregion


# region ====== The revision ======


@pytest.mark.parametrize(
    ("raw", "short"),
    [
        (SHA, "8822a20"),
        ("8822a20", "8822a20"),
        (" 8822A20C ", None),
        ("", None),
        (None, None),
        ("latest", None),
        ("12345", None),
    ],
)
def test_the_revision_is_the_first_seven_characters_of_a_commit_or_nothing(raw: str | None, short: str | None) -> None:
    """A commit hash shortens to seven characters; anything else, which could be a pasted secret, is never shown."""
    assert short_revision(raw) == short


# endregion


# region ====== Each problem code, in French ======

SAMPLES: dict[str, str] = {
    "commands_channel_missing": "",
    "commands_channel_no_post": "channel_id=42",
    "log_channel_missing": "stored_id=None",
    "log_channel_no_post": "channel_id=43",
    "permissions_missing": "manage_roles, attach_files",
    "role_order_temp": "",
    "role_order": "Pôle Numérique, role_id=77",
    "roles_missing": "CoDir, one of Administrateur | Président",
    "welcome_setup_missing": "category Validation, role Membre ✓",
    "suggest_recipient_unreachable": "projet, numerique",
    "language_not_shipped": "locale=es-ES",
    "integrations_no_role": "/fm",
    "integrations_role_missing": "/fm: Respo Formation, role_id=9",
    "integrations_everyone_allowed": "/fm",
    "member_command_denied": "/help denied for @everyone, /help denied for All Channels",
    "leftover_commands": "/old command_id=5, /older command_id=6",
    "check_failed": "step=integrations status=HTTP 403",
}
LEAKS = ("step=", "status=", "channel_id=", "role_id=", "command_id=", "stored_id=", "locale=", "denied for", "one of")


def test_every_code_has_a_sample_here_and_a_sentence_in_french() -> None:
    """A code added to the checks is told in French too, or this test names it."""
    assert set(SAMPLES) == set(FIXES)
    assert set(problem_text.SENTENCES) == set(FIXES)


@pytest.mark.parametrize("code", sorted(FIXES))
def test_a_problem_is_a_french_sentence_and_a_fix_with_no_code_in_it(code: str) -> None:
    """Each problem reads as a bold sentence, then an arrow and the path to fix it; no code, English fix or raw detail shows."""
    severity, english_fix = FIXES[code]

    block = problem_text.describe(Problem(code, SAMPLES[code]))

    sentence, fix = block.split("\n")
    assert sentence.startswith(("⚠️ **", "❌ **"))
    assert fix.startswith("→ ")
    assert (severity == "blocking") == sentence.startswith("❌")
    assert code not in block
    assert english_fix not in block
    assert not [leak for leak in LEAKS if leak in block], block


def test_a_role_the_server_named_itself_shows_as_a_mention_never_as_an_id() -> None:
    """A role the checks only know by ID is shown as a role mention, so the reader sees its name."""
    block = problem_text.describe(Problem("role_order", "Pôle Numérique, role_id=77"))

    assert "Pôle Numérique" in block
    assert "<@&77>" in block


def test_a_channel_is_shown_as_a_channel_mention() -> None:
    """The channel a problem names is a mention, which Discord shows as its name."""
    assert "<#42>" in problem_text.describe(Problem("commands_channel_no_post", "channel_id=42"))


def test_permissions_are_named_in_french() -> None:
    """Every permission of the invitation has its French name, so none shows as an API word."""
    for name, wanted in INVITE_PERMISSIONS:
        if wanted:
            assert name in problem_text.PERMISSIONS, name
    assert "gérer les rôles" in problem_text.describe(Problem("permissions_missing", "manage_roles"))


def test_a_denied_command_names_each_way_it_is_denied() -> None:
    """A command denied to everyone and in every channel says both, once."""
    block = problem_text.describe(Problem("member_command_denied", SAMPLES["member_command_denied"]))

    assert block.count("`/help`") == 2
    assert "à tout le monde" in block
    assert "dans tous les salons" in block


def test_several_roles_missing_from_a_command_read_in_the_plural() -> None:
    """Two roles to allow on a command give "aux rôles" and "et" between them."""
    block = problem_text.describe(Problem("integrations_role_missing", "/log: Pôle Numérique, CoDir"))

    assert "n'est pas ouvert aux rôles Pôle Numérique et CoDir" in block
    assert "ajoute Pôle Numérique et CoDir" in block


def test_leftover_commands_are_counted_and_named() -> None:
    """The commands left on the server are counted in the right number and named, without their IDs."""
    block = problem_text.describe(Problem("leftover_commands", "/old command_id=5, /older command_id=6"))

    assert "2 commandes en trop" in block
    assert "`/old`" in block
    assert "`/older`" in block
    assert "5" not in block


def test_a_long_list_of_roles_is_cut_with_the_count_of_the_others() -> None:
    """Twelve roles named in one problem show eight and the count of the four others."""
    roles = ", ".join(f"role_id={number}" for number in range(100, 112))

    block = problem_text.describe(Problem("role_order", roles))

    assert block.count("<@&") == 8
    assert "… et 4 autres" in block


# endregion


# region ====== Discord's limits ======


def _many(count: int) -> list[Problem]:
    """Problems that each name a dozen roles, as a server with a long role list could produce."""
    roles = ", ".join(f"role_id={number}" for number in range(100, 112))
    return [
        Problem("role_order", roles) if number % 2 else Problem("leftover_commands", f"/x{number} command_id=1")
        for number in range(count)
    ]


def test_a_long_problem_list_is_cut_to_the_field_limit_with_the_count_of_the_others() -> None:
    """With sixty problems the field stays within 1024 characters and ends on how many are left out."""
    problems = [Problem("integrations_role_missing", f"/command{number}: Pôle Numérique, CoDir") for number in range(60)]

    embed = build_info(_facts(), problems)

    value = _fields(embed)["🔍 À corriger sur ce serveur"]
    assert len(value) <= 1024
    shown = value.count("→ ")
    assert 0 < shown < 60
    assert value.endswith(f"… et {60 - shown} autres")


def test_the_blocking_problems_are_kept_when_the_list_is_cut() -> None:
    """The cut drops warnings first: a blocking problem listed last is still shown."""
    warnings = [Problem("integrations_role_missing", f"/command{number}: Pôle Numérique, CoDir") for number in range(60)]

    value = _fields(build_info(_facts(), [*warnings, BLOCKING]))["🔍 À corriger sur ce serveur"]

    assert value.startswith("❌ **Aucun salon de commandes**")


def test_the_whole_embed_stays_within_discords_limits() -> None:
    """Whatever the number of problems, the embed holds under 6000 characters, 25 fields and 1024 per field value."""
    for count in (0, 1, 10, 60, 400):
        embed = build_info(_facts(), _many(count))

        assert len(embed) <= 6000
        assert len(embed.fields) <= 25
        assert all(len(field.value or "") <= 1024 for field in embed.fields)
        assert len(embed.title or "") <= 256
        assert len(embed.description or "") <= 4096


def test_the_same_sentence_is_listed_once() -> None:
    """Problems that read the same, such as two reads that failed the same way, are one entry."""
    twice = [Problem("check_failed", "step=a status=HTTP 403"), Problem("check_failed", "step=b status=HTTP 403")]

    value = _fields(build_info(_facts(), twice))["🔍 À corriger sur ce serveur"]

    assert value.count("→ ") == 1


# endregion


# region ====== The command ======

COMMANDS_CHANNEL = "commandes_bot"


async def _world(env: simcord.Env, **kwargs: Any) -> Any:
    """Build the server of the first listed ID with everything in place (less ``without``) and a channel to run commands in."""
    await ready_server(env, **kwargs)
    env.guild.create_text_channel("salon-libre")
    await env.settle()
    return env.guild


def _member(env: simcord.Env, name: str, *roles: str) -> Any:
    """Add a member holding these roles to the first server."""
    return env.guild.add_member(env.create_user(name), roles=[env.guild.roles[role] for role in roles])


async def _info(env: simcord.Env, who: Any) -> Any:
    """Run ``/info`` as a member, from a channel that is not the commands channel."""
    return await who.slash(env.guild.channels["salon-libre"], "info")


async def _open_to_everyone(env: simcord.Env) -> None:
    """Show ``/info`` to every member in Integrations, so that a refusal comes from the command's own check."""
    bot: Any = env.bot
    command = next(c for c in await bot.tree.fetch_commands() if c.name == "info")
    env.guild.set_command_permissions(command, {env.guild.default_role: True})


def _watch(monkeypatch: pytest.MonkeyPatch, ids: str) -> None:
    """Set the watched accounts and drop the list the bot read at its start."""
    monkeypatch.setenv(audit_watch.AUDIT_WATCH_ENV, ids)
    audit_watch.watched_ids.cache_clear()


def _embed(result: Any) -> discord.Embed:
    """The one embed of a private answer."""
    assert result.ephemeral
    assert len(result.response.embeds) == 1
    return result.response.embeds[0]


def _text(embed: discord.Embed) -> str:
    """Everything an embed shows, as one string."""
    return json.dumps(embed.to_dict(), ensure_ascii=False)


@pytest.mark.parametrize("role", sorted(INFO_ROLES))
async def test_each_allowed_role_gets_a_private_embed_in_any_channel(simcord_env: simcord.Env, role: str) -> None:
    """Respo Numérique and the four admin roles each get the embed, privately, outside the commands channel."""
    await _world(simcord_env)
    who = _member(simcord_env, "someone", role)

    embed = _embed(await _info(simcord_env, who))

    assert (embed.title or "").startswith("📊 FablaBot")


def test_the_allowed_roles_are_respo_numerique_and_the_admin_roles() -> None:
    """The role set is Respo Numérique and the four admin roles, no more."""
    assert ADMIN_ROLES | {RoleNames.DIGITAL_MANAGER} == INFO_ROLES
    assert len(INFO_ROLES) == 5


@pytest.mark.parametrize(
    "role",
    [RoleNames.BUREAU, RoleNames.TRAININGS_MANAGER, RoleNames.CODIR, RoleNames.ADMIN_TEMP, RoleNames.DIGITAL_POLE, None],
    ids=["bureau", "respo-formation", "codir", "admin-temp", "pole-numerique", "plain-member"],
)
async def test_every_other_member_is_refused_and_nothing_is_read(
    simcord_env: simcord.Env, role: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Any other role, or none, gets the usual private refusal, and no check of the server runs."""
    await _world(simcord_env)
    await _open_to_everyone(simcord_env)
    spy = AsyncMock(return_value=[])
    monkeypatch.setattr(info_management, "check_guild", spy)
    who = _member(simcord_env, "someone", *([role] if role else []))

    result = await _info(simcord_env, who)

    assert result.ephemeral
    assert not result.response.embeds
    assert (result.response.content or "").startswith(ErrorMessages.ROLE_REQUIRED.split("{")[0])
    spy.assert_not_awaited()


CLEANED = datetime(2026, 11, 5, 4, 0, tzinfo=UTC)


def test_the_last_cleanup_shows_as_a_relative_time_in_the_database_block() -> None:
    """A known run gives a third line with Discord's relative timestamp, which the reader's client keeps up to date."""
    embed = build_info(_facts(last_cleanup=CLEANED), [])

    assert (
        _fields(embed)["🗃️ Base de données"]
        == f"Version **4** · 1,2 Mo\n**6** sauvegardes\nNettoyage <t:{int(CLEANED.timestamp())}:R>"
    )


def test_a_database_that_was_never_cleaned_shows_no_cleanup_line() -> None:
    """Without a recorded run, the block keeps its two lines."""
    embed = build_info(_facts(last_cleanup=None), [])

    assert "Nettoyage" not in _fields(embed)["🗃️ Base de données"]


async def test_info_reads_the_last_cleanup_from_the_database(simcord_env: simcord.Env) -> None:
    """The command shows the run the retention recorded, and none before there is one."""
    await _world(simcord_env)
    who = _member(simcord_env, "someone", RoleNames.DIGITAL_MANAGER)
    with db.transaction() as conn:
        conn.execute("DELETE FROM bot_state")
    assert "Nettoyage" not in _fields(_embed(await _info(simcord_env, who)))["🗃️ Base de données"]

    with db.transaction() as conn:
        conn.execute("INSERT INTO bot_state VALUES ('retention_last_run', ?)", (CLEANED.isoformat(),))

    shown = _fields(_embed(await _info(simcord_env, who)))["🗃️ Base de données"]
    assert shown.endswith(f"\nNettoyage <t:{int(CLEANED.timestamp())}:R>")


async def test_a_ready_server_is_green_with_the_facts_of_the_instance(
    simcord_env: simcord.Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A server with everything in place gets the green embed, its own channels, the version and the counts of the instance."""
    monkeypatch.setenv("FABLABOT_REVISION", SHA)
    _watch(monkeypatch, "111,222,333")
    guild = await _world(simcord_env)
    started: float = simcord_env.bot.started_at  # pyright: ignore[reportAttributeAccessIssue]
    monkeypatch.setattr(info_management, "monotonic", lambda: started + 3 * 3600 + 12 * 60)
    who = _member(simcord_env, "someone", RoleNames.DIGITAL_MANAGER)
    channels = guild.channels

    embed = _embed(await _info(simcord_env, who))

    assert guild.id == GUILD
    assert embed.title == "📊 FablaBot : tout va bien"
    assert embed.colour is not None
    assert embed.colour.value == GREEN
    fields = _fields(embed)
    version = importlib.metadata.version("fablabot")
    assert fields["🤖 Bot"].startswith(f"Version **{version}**\nEn ligne depuis **3 h 12 min**")
    database = fields["🗃️ Base de données"]
    assert re.fullmatch(r"Version \*\*\d+\*\* · \d+,\d [KM]o\n\*\*0\*\* sauvegarde(\nNettoyage <t:\d+:R>)?", database)
    assert fields["🛰️ Instance"] == "**1** serveur servi\n**10** commandes\n**3** comptes surveillés"
    assert (
        fields["⚙️ Réglages de ce serveur"]
        == f"Commandes : <#{channels[COMMANDS_CHANNEL].id}>\nLogs : <#{channels['logs'].id}>\nLangue : français"
    )
    assert embed.footer.text == "Contrôle relancé à l'instant · révision 8822a20"
    assert "À corriger" not in " ".join(fields)


async def test_a_blocking_problem_is_red_and_told_in_french_with_its_fix(simcord_env: simcord.Env) -> None:
    """A server with no commands channel gets the red embed, the sentence in French and the way to fix it, and no code."""
    await _world(simcord_env, without=["commands"])
    who = _member(simcord_env, "someone", RoleNames.ADMIN)

    embed = _embed(await _info(simcord_env, who))

    assert embed.title == "📊 FablaBot : 1 point bloquant sur ce serveur"
    assert embed.colour is not None
    assert embed.colour.value == RED
    value = _fields(embed)["🔍 À corriger sur ce serveur"]
    assert value.startswith("❌ **Aucun salon de commandes** · bloquant\n→ Crée un salon textuel nommé `commandes_bot`")
    assert "commands_channel_missing" not in _text(embed)
    assert _fields(embed)["⚙️ Réglages de ce serveur"].startswith("Commandes : aucun salon\n")


async def _close_info(env: simcord.Env) -> None:
    """Take every role off ``/info`` in Integrations, the state of a server right after the release that brought it."""
    command = await global_command(env, "info")
    env.backend.set_command_permissions(GUILD, command.id, role_entries([]))


async def test_info_not_allowed_in_integrations_is_reported_at_start_and_by_info(
    simcord_env: simcord.Env, caplog: pytest.LogCaptureFixture
) -> None:
    """Until /info is allowed in Integrations, the start logs it as blocking and /info itself shows red with the path to fix."""
    caplog.set_level(logging.INFO)
    await _world(simcord_env)
    await _close_info(simcord_env)
    # With nothing allowed on /info, only the Administrator permission still shows it, as for the person who sets it up.
    simcord_env.guild.create_role("Direction", permissions=Permissions(administrator=True))
    await simcord_env.settle()
    who = _member(simcord_env, "someone", RoleNames.ADMIN, "Direction")

    await run(simcord_env)
    embed = _embed(await _info(simcord_env, who))

    assert codes(caplog) == ["integrations_no_role"]
    assert "severity=blocking" in problems(caplog)[0]
    assert "detail=[/info]" in problems(caplog)[0]
    assert embed.title == "📊 FablaBot : 1 point bloquant sur ce serveur"
    assert embed.colour is not None
    assert embed.colour.value == RED
    assert _fields(embed)["🔍 À corriger sur ce serveur"] == (
        f"❌ **`/info` n'est ouvert à aucun des rôles qui l'utilisent** · bloquant\n"
        f"→ {PATH} › `/info` : ajoute les rôles du staff concernés"
    )
    assert [r.getMessage() for r in caplog.records if r.getMessage().startswith("Info:")] == [
        f"Info: guild_id={GUILD} blocking=1 warnings=0"
    ]


async def test_a_warning_alone_is_orange(simcord_env: simcord.Env) -> None:
    """A server where nobody can take the suggestions for a pôle gets the orange embed and one point to fix."""
    await _world(simcord_env, without=["holder"])
    who = _member(simcord_env, "someone", RoleNames.PRESIDENT)

    embed = _embed(await _info(simcord_env, who))

    assert embed.title == "📊 FablaBot : 1 point à corriger sur ce serveur"
    assert embed.colour is not None
    assert embed.colour.value == ORANGE
    assert "Pôle Projet" in _fields(embed)["🔍 À corriger sur ce serveur"]


async def test_a_problem_fixed_since_the_start_is_gone_at_once(simcord_env: simcord.Env) -> None:
    """The checks run again at each call: a role created after the first answer is not reported at the second."""
    await _world(simcord_env, without=[RoleNames.CODIR])
    who = _member(simcord_env, "someone", RoleNames.ADMIN)
    first = _embed(await _info(simcord_env, who))
    simcord_env.guild.create_role(RoleNames.CODIR)
    await simcord_env.settle()

    second = _embed(await _info(simcord_env, who))

    assert "CoDir" in _fields(first)["🔍 À corriger sur ce serveur"]
    assert "manquant" not in _fields(second).get("🔍 À corriger sur ce serveur", "")


async def test_nothing_of_another_server_shows(simcord_env: simcord.Env) -> None:
    """Another server's problems, channels and members never reach the answer, which counts the servers and names none."""
    await _world(simcord_env)
    other = await ready_server(simcord_env, OTHER, without=["commands", RoleNames.CODIR])
    who = _member(simcord_env, "someone", RoleNames.ADMIN)

    embed = _embed(await _info(simcord_env, who))

    text = _text(embed)
    assert embed.title == "📊 FablaBot : tout va bien"
    assert "À corriger" not in " ".join(_fields(embed))
    assert "**2** serveurs servis" in text
    assert str(OTHER) not in text
    for channel in other.text_channels:
        assert str(channel.id) not in text
    assert f"holder_{OTHER}" not in text


async def test_a_server_the_bot_does_not_serve_is_not_counted(simcord_env: simcord.Env) -> None:
    """A server the bot is in but does not serve is left out of the count of servers served."""
    await _world(simcord_env)
    simcord_env.create_guild(id=UNSERVED)
    await simcord_env.settle()
    bot: Any = simcord_env.bot
    assert UNSERVED in {guild.id for guild in bot.guilds}
    assert not bot.guild_access.is_served(UNSERVED)
    who = _member(simcord_env, "someone", RoleNames.ADMIN)

    embed = _embed(await _info(simcord_env, who))

    assert "**1** serveur servi" in _fields(embed)["🛰️ Instance"]
    assert str(UNSERVED) not in _text(embed)


async def test_a_stored_log_channel_that_is_gone_is_not_shown_as_set(simcord_env: simcord.Env) -> None:
    """A log channel stored for the server but deleted since shows as "aucun salon", with its blocking problem."""
    await _world(simcord_env)
    guild_config.set_log_channel_id(GUILD, GONE_CHANNEL)
    who = _member(simcord_env, "someone", RoleNames.ADMIN)

    embed = _embed(await _info(simcord_env, who))

    assert "Logs : aucun salon" in _fields(embed)["⚙️ Réglages de ce serveur"]
    assert str(GONE_CHANNEL) not in _text(embed)
    assert embed.title == "📊 FablaBot : 1 point bloquant sur ce serveur"


async def test_no_member_name_is_shown(simcord_env: simcord.Env) -> None:
    """The answer names neither the member who asked nor the holder of any role."""
    await _world(simcord_env)
    who = _member(simcord_env, "kevin_secret_name", RoleNames.ADMIN)

    text = _text(_embed(await _info(simcord_env, who)))

    assert "kevin_secret_name" not in text
    assert f"holder_{GUILD}" not in text


async def test_the_watched_accounts_are_a_count_and_never_an_id(
    simcord_env: simcord.Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Three watched accounts show as "3 comptes surveillés", and their IDs show nowhere."""
    _watch(monkeypatch, "424242424242,515151515151,606060606060")
    await _world(simcord_env)
    who = _member(simcord_env, "someone", RoleNames.ADMIN)

    text = _text(_embed(await _info(simcord_env, who)))

    assert "**3** comptes surveillés" in text
    for watched in ("424242424242", "515151515151", "606060606060"):
        assert watched not in text


async def test_the_answer_mentions_nobody(simcord_env: simcord.Env) -> None:
    """The reply is sent with no mention allowed, since it holds channel and role mentions."""
    await _world(simcord_env, without=["commands"])
    who = _member(simcord_env, "someone", RoleNames.ADMIN)

    await _info(simcord_env, who)

    (sent,) = [r for r in simcord_env.http_requests if isinstance(r.json, dict) and r.json.get("embeds")]
    assert isinstance(sent.json, dict)
    assert sent.json["allowed_mentions"] == {"parse": []}


async def test_the_log_names_the_server_and_the_counts_by_id_only(
    simcord_env: simcord.Env, caplog: pytest.LogCaptureFixture
) -> None:
    """The call is logged with the server and member IDs and the counts, never a name."""
    caplog.set_level(logging.INFO)
    await _world(simcord_env, without=["commands"])
    who = _member(simcord_env, "kevin_secret_name", RoleNames.ADMIN)

    await _info(simcord_env, who)

    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Info:")]
    assert lines == [f"Info: guild_id={GUILD} blocking=1 warnings=0"]
    assert "kevin_secret_name" not in caplog.text


# endregion


# region ====== The database ======


def test_the_database_status_reads_the_schema_the_size_and_the_copies() -> None:
    """The status gives the schema version, the file size and the copies, counting neither a partial nor another file."""
    path = Path(db.connection().execute("PRAGMA database_list").fetchone()["file"])
    backups = path.parent / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    for name in ("fablabot-v3-20260101T000000000000Z.db", "fablabot-v2-20260102T000000000000Z.db"):
        (backups / name).write_bytes(b"x")
    (backups / "fablabot-v3.partial").write_bytes(b"x")
    (backups / "notes.txt").write_bytes(b"x")

    status = db.status()

    assert status.schema_version == db.connection().execute("PRAGMA user_version").fetchone()[0]
    assert status.schema_version >= 4
    assert status.size == path.stat().st_size
    assert status.copies == 2


def test_the_database_status_counts_no_copy_when_there_is_no_folder() -> None:
    """No copies folder yet means no copy."""
    assert db.status().copies == 0


# endregion


def test_the_check_of_one_server_is_public() -> None:
    """The start's checks can be run for one server on request."""
    assert callable(server_check.check_guild)
