"""What an update of the published announcement would change, planned from the two messages alone."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any

import pytest

from fablabot.helpers.constants import MAX_SESSIONS, PARIS_TZ, SESSION_EMOJIS
from fablabot.helpers.formation.rendering import announcement_length, render_message
from fablabot.helpers.formation.update import UpdatePlan, as_draft, is_frozen, plan_update
from fablabot.models.formation import FmMessageDraft, Formation

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=PARIS_TZ)
NBSP = "\N{NO-BREAK SPACE}"
UNCHANGED = "Rien n'a été modifié."


def _fm(emoji: str, *, hours: float = 48, name: str = "Soudure", seats: int = 5, signed: int = 0, **values: Any) -> Formation:
    """A session starting ``hours`` after NOW, with ``signed`` members registered and one waiting when it is full."""
    fm = Formation(
        emoji=emoji,
        name=name,
        trainer_mention="<@1>",
        start_iso=(NOW + timedelta(hours=hours)).isoformat(),
        duration="1 h",
        seats=seats,
        registered_users=[{"user_id": 100 + n} for n in range(signed)],
        waitlisted_users=[{"user_id": 200}] if signed >= seats else [],
    )
    return replace(fm, **values)


def _message(*sessions: Formation, **text: Any) -> FmMessageDraft:
    """An announcement of these sessions, with a role, an intro and a conclusion unless ``text`` says otherwise."""
    fields: dict[str, Any] = {"header": "# [FORMATIONS]", "role_id": 7, "intro": "Bonjour", "end": "À vite"}
    return FmMessageDraft(**{**fields, **text}, fms=list(sessions))


def _published() -> FmMessageDraft:
    """An announcement of two sessions to come, each with sign-ups, as it stands once posted."""
    return _message(_fm("🧡", hours=48, signed=3), _fm("💛", hours=72, name="Brodeuse", seats=4, signed=1))


def _plan(published: FmMessageDraft, draft: FmMessageDraft) -> UpdatePlan:
    """Plan an update at NOW and check that it is a plan."""
    plan = plan_update(published, draft, now=NOW, retired=frozenset())
    assert isinstance(plan, UpdatePlan), plan
    return plan


def _full_length(message: FmMessageDraft) -> int:
    """Count the announcement by hand with every seat taken: the rendering of a copy whose sessions are all full."""
    full = [replace(fm, registered_users=[{"user_id": n} for n in range(fm.seats)]) for fm in message.fms]
    return len(render_message(replace(message, fms=full)))


CHANGES = {
    "start": {"start_iso": (NOW + timedelta(hours=50)).isoformat()},
    "trainer": {"trainer_mention": "<@2>"},
    "seats": {"seats": 7},
    "description": {"description": "Apportez vos lunettes."},
    "name": {"name": "Soudure à l'étain"},
    "duration": {"duration": "2 h"},
    "excusable": {"excusable": False},
}


@pytest.mark.parametrize("field", CHANGES)
def test_a_session_is_recognised_by_its_emoji_and_each_changed_value_is_listed(field: str) -> None:
    """A draft session wearing an announced emoji is that session changed; only the value it changes differs."""
    published = _published()
    draft = as_draft(published)
    draft.fms[0] = replace(draft.fms[0], **CHANGES[field])
    draft.fms.reverse()

    plan = _plan(published, draft)

    (step,) = [s for s in plan.steps if s.kind != "frozen"]
    assert step.kind == "changed"
    assert step.before == published.fms[0]
    assert step.after is not None
    assert step.after.emoji == "🧡"
    differing = {name for name in vars(step.before) if getattr(step.before, name) != getattr(step.after, name)}
    expected = {"start": "start_iso", "trainer": "trainer_mention"}.get(field, field)
    assert differing == {expected}
    assert plan.message.fms[0] == step.after
    assert plan.message.fms[1] == published.fms[1]
    assert plan.text_changes == ()


@pytest.mark.parametrize(
    "held",
    [
        {"hours": -1},
        {"hours": 4 / 60},
        {"hours": 48, "notified_at_start": True},
    ],
    ids=["started", "imminent", "flagged-at-start"],
)
def test_a_started_or_imminent_session_is_left_as_it_is(held: dict[str, Any]) -> None:
    """A session started, starting within 5 minutes, or flagged at its start is kept as published, whatever the draft says."""
    frozen = _fm("🧡", name="Soudure", signed=2, **held)
    published = _message(frozen, _fm("💛", hours=72, name="Brodeuse", signed=1))
    draft = as_draft(published)
    draft.fms[0] = replace(draft.fms[0], name="Autre nom", seats=9, start_iso=frozen.start_iso)
    draft.fms[1] = replace(draft.fms[1], name="Brodeuse 2")

    plan = _plan(published, draft)

    assert [(s.kind, s.before.emoji) for s in plan.steps if s.before] == [("changed", "💛"), ("frozen", "🧡")]
    assert plan.message.fms[0] == frozen
    assert is_frozen(frozen, NOW)


def test_a_session_starting_in_six_minutes_is_not_yet_frozen() -> None:
    """The window is 5 minutes: a session 6 minutes ahead can still change."""
    assert not is_frozen(_fm("🧡", hours=6 / 60), NOW)
    assert is_frozen(_fm("🧡", hours=5 / 60), NOW)
    assert is_frozen(_fm("🧡", hours=-1), NOW)


def test_a_draft_saying_what_the_announcement_says_is_refused() -> None:
    """A draft that is the announcement as it stands, sign-ups apart, has nothing to update."""
    published = _published()

    refusal = plan_update(published, as_draft(published), now=NOW, retired=frozenset())

    assert refusal == f"Le brouillon dit déjà ce que dit l'annonce publiée : rien à mettre à jour. {UNCHANGED}"


def test_a_session_to_change_with_a_past_start_is_refused() -> None:
    """A kept session moved to a start before now is refused, naming it and how to correct it."""
    published = _published()
    draft = as_draft(published)
    draft.fms[1] = replace(draft.fms[1], start_iso=(NOW - timedelta(hours=1)).isoformat())

    refusal = plan_update(published, draft, now=NOW, retired=frozenset())

    assert refusal == f"La session **💛 Brodeuse** commence avant maintenant : change sa date ou retire-la. {UNCHANGED}"


def test_a_draft_differing_only_on_a_started_session_says_so() -> None:
    """When the only difference is on a session left as published, the refusal names it instead of saying nothing differs."""
    frozen = _fm("🧡", hours=-1, signed=2)
    published = _message(frozen, _fm("💛", hours=72, name="Brodeuse", signed=1))
    draft = as_draft(published)
    draft.fms[0] = replace(draft.fms[0], name="Autre nom", seats=9)

    refusal = plan_update(published, draft, now=NOW, retired=frozenset())

    assert refusal == (
        f"La session **🧡 Soudure** a déjà commencé ou commence dans moins de 5 minutes : elle reste telle quelle. {UNCHANGED}"
    )


@pytest.mark.parametrize(("minutes", "refused"), [(4, True), (5, True), (6, False)])
def test_a_session_moved_to_start_within_5_minutes_is_refused(minutes: int, refused: bool) -> None:
    """A session cannot be moved to a start the freeze already covers: 5 minutes from now or less."""
    published = _published()
    draft = as_draft(published)
    draft.fms[1] = replace(draft.fms[1], start_iso=(NOW + timedelta(minutes=minutes)).isoformat())

    result = plan_update(published, draft, now=NOW, retired=frozenset())

    if refused:
        assert (
            result == f"La session **💛 Brodeuse** commence dans moins de 5 minutes : change sa date ou retire-la. {UNCHANGED}"
        )
    else:
        assert isinstance(result, UpdatePlan)


@pytest.mark.parametrize(
    ("field", "refusal"),
    [
        ("role_id", "Choisis d'abord le rôle à notifier : **Intro / conclusion**."),
        ("intro", "Écris d'abord l'intro et la conclusion : **Intro / conclusion**."),
        ("end", "Écris d'abord l'intro et la conclusion : **Intro / conclusion**."),
    ],
)
def test_no_role_intro_or_conclusion_is_refused(field: str, refusal: str) -> None:
    """A draft with no role to notify, no intro or no conclusion is refused, even with a session changed."""
    published = _published()
    draft = replace(as_draft(published), **{field: 0 if field == "role_id" else "  "})
    draft.fms[0] = replace(draft.fms[0], seats=9)

    assert plan_update(published, draft, now=NOW, retired=frozenset()) == f"{refusal} {UNCHANGED}"


def _sized(published: FmMessageDraft, target: int) -> FmMessageDraft:
    """The draft of ``published`` with a first description that makes the announcement ``target`` characters, seats full."""
    draft = as_draft(published)
    draft.fms[0] = replace(draft.fms[0], seats=10, description="x")
    base = _full_length(draft)
    draft.fms[0] = replace(draft.fms[0], description="x" * (1 + target - base))
    assert _full_length(draft) == target
    return draft


def test_an_update_past_2000_characters_with_every_seat_taken_is_refused() -> None:
    """The length is judged with every seat taken: 2 001 is refused with its length, exactly 2 000 passes."""
    published = _published()

    refusal = plan_update(published, _sized(published, 2001), now=NOW, retired=frozenset())

    assert refusal == (
        f"L'annonce mise à jour ferait 2{NBSP}001 caractères toutes places prises, Discord en accepte 2 000 : "
        f"retire une session ou raccourcis une description. {UNCHANGED}"
    )
    assert isinstance(plan_update(published, _sized(published, 2000), now=NOW, retired=frozenset()), UpdatePlan)


def test_the_length_counts_each_seat_as_taken() -> None:
    """With no sign-up the announcement is counted as if every seat were taken: ``0/10`` reads ``10/10``."""
    message = _message(_fm("🧡", seats=10, signed=0))

    assert announcement_length(message) == len(render_message(message)) + 1
    assert announcement_length(message) == _full_length(message)


def test_sign_ups_and_flags_of_kept_sessions_are_in_the_planned_message() -> None:
    """The planned message holds each kept session's registered and waiting members and its reminder flag as they are."""
    published = _message(
        _fm("🧡", hours=48, signed=5, notified_hour_before=True),
        _fm("💛", hours=72, name="Brodeuse", seats=4, signed=1),
    )
    draft = as_draft(published)
    draft.fms[0] = replace(draft.fms[0], description="Apportez vos lunettes.")

    plan = _plan(published, draft)

    first = plan.message.fms[0]
    assert first.description == "Apportez vos lunettes."
    assert first.registered_users == published.fms[0].registered_users
    assert first.waitlisted_users == published.fms[0].waitlisted_users
    assert first.notified_hour_before
    assert plan.message.fms[1] == published.fms[1]


@pytest.mark.parametrize("field", ["start", "trainer", "description", "seats"])
def test_a_new_start_clears_the_hour_before_flag_only(field: str) -> None:
    """A new start gives the hour-before reminder back to everyone; no other change, and no other flag, moves."""
    published = _message(_fm("🧡", signed=2, notified_hour_before=True), _fm("💛", hours=72, name="Brodeuse"))
    draft = as_draft(published)
    draft.fms[0] = replace(draft.fms[0], **CHANGES[field])

    kept = _plan(published, draft).message.fms[0]

    assert kept.notified_hour_before is (field != "start")
    assert kept.notified_at_start is False
    assert kept.registered_users == published.fms[0].registered_users


@pytest.mark.parametrize(
    ("flagged", "changes", "owed"),
    [
        (True, ["trainer"], ("🧡",)),
        (True, ["trainer", "seats"], ("🧡",)),
        (True, ["trainer", "start"], ()),
        (True, ["start"], ()),
        (True, ["seats"], ()),
        (False, ["trainer"], ()),
    ],
    ids=["trainer", "trainer-and-seats", "trainer-and-start", "start", "seats", "not-yet-reminded"],
)
def test_only_a_new_trainer_alone_is_owed_the_reminder_that_already_went_out(
    flagged: bool, changes: list[str], owed: tuple[str, ...]
) -> None:
    """The reminder is owed directly to a new trainer when it went out and the start stays; otherwise the tick sends it."""
    published = _message(_fm("🧡", signed=2, notified_hour_before=flagged), _fm("💛", hours=72, name="Brodeuse"))
    draft = as_draft(published)
    for field in changes:
        draft.fms[0] = replace(draft.fms[0], **CHANGES[field])

    plan = _plan(published, draft)

    assert plan.trainers_to_remind == owed
    assert plan.message.fms[0].notified_hour_before is (flagged and "start" not in changes)


@pytest.mark.parametrize(
    ("text", "named"),
    [
        ({"intro": "Autre intro"}, ("intro",)),
        ({"end": "Autre fin"}, ("conclusion",)),
        ({"request_forms_url": "https://example.org/form"}, ("lien",)),
        ({"role_id": 8}, ("rôle",)),
        ({"intro": "Autre intro", "end": "Autre fin", "role_id": 8, "request_forms_url": "https://example.org/f"}, None),
    ],
    ids=["intro", "conclusion", "link", "role", "all"],
)
def test_text_changes_are_named(text: dict[str, Any], named: tuple[str, ...] | None) -> None:
    """The plan names what changed in the text, in the order intro, conclusion, lien, rôle; the text alone is enough."""
    published = _published()

    plan = _plan(published, replace(as_draft(published), **text))

    assert plan.text_changes == (named or ("intro", "conclusion", "lien", "rôle"))
    assert not [s for s in plan.steps if s.kind != "frozen"]
    assert plan.message.intro == text.get("intro", published.intro)
    assert plan.message.end == text.get("end", published.end)
    assert plan.message.role_id == text.get("role_id", published.role_id)
    assert plan.message.fms == published.fms


def test_the_digest_ignores_sign_ups_but_not_a_value() -> None:
    """Two plans of the same change agree whoever signed up; a changed value, text or message ID changes the digest."""
    published = _published()
    draft = as_draft(published)
    draft.fms[0] = replace(draft.fms[0], seats=9)
    more = replace(
        published, fms=[replace(published.fms[0], registered_users=[{"user_id": n} for n in range(5)]), published.fms[1]]
    )
    digest = _plan(published, draft).digest(10)

    assert len(digest) == 12
    assert _plan(more, draft).digest(10) == digest
    other_value = replace(draft, fms=[replace(draft.fms[0], seats=8), draft.fms[1]])
    assert _plan(published, other_value).digest(10) != digest
    assert _plan(published, replace(draft, intro="Autre")).digest(10) != digest
    assert _plan(published, draft).digest(11) != digest


def test_the_digest_sees_a_session_that_became_frozen() -> None:
    """A session that became frozen since the summary gives another digest."""
    published = _published()
    draft = as_draft(published)
    draft.fms[1] = replace(draft.fms[1], seats=9)

    before = _plan(published, draft)
    later = NOW + timedelta(hours=47, minutes=57)
    after = plan_update(published, draft, now=later, retired=frozenset())

    assert isinstance(after, UpdatePlan)
    assert [s.kind for s in after.steps] == ["changed", "frozen"]
    assert after.digest(10) != before.digest(10)


def test_a_removed_session_is_planned_with_its_members() -> None:
    """A draft that drops a session that has not started plans it as removed, with its sign-ups, and the rest as published."""
    published = _published()
    removed = as_draft(published)
    removed.fms.pop()

    plan = _plan(published, removed)

    (step,) = plan.steps
    assert (step.kind, step.before, step.after) == ("removed", published.fms[1], None)
    assert step.before is not None
    assert len(step.before.registered_users) == 1
    assert plan.message.fms == [published.fms[0]]
    assert plan.moves == {}


def test_removing_closes_the_gap_and_maps_each_moved_place() -> None:
    """The kept sessions keep their order, a removal closes the gap, and each place that moves is mapped old to new."""
    published = _message(
        _fm("🧡", hours=48), _fm("💛", hours=72), _fm("💚", hours=96), _fm("💙", hours=120), _fm("💜", hours=144)
    )
    draft = as_draft(published)
    draft.fms = [fm for fm in draft.fms if fm.emoji not in {"💛", "💙"}]

    plan = _plan(published, draft)

    assert [fm.emoji for fm in plan.message.fms] == ["🧡", "💚", "💜"]
    assert plan.moves == {2: 1, 4: 2}
    assert sorted(s.before.emoji for s in plan.steps if s.before and s.kind == "removed") == ["💙", "💛"]


def test_adding_with_a_removal_leaves_the_added_session_last_and_unmapped() -> None:
    """A session added in the same update goes after the kept ones and is not in the moves."""
    published = _published()
    draft = as_draft(published)
    draft.fms = [draft.fms[1], _fm("💜", hours=90, name="Résine")]

    plan = _plan(published, draft)

    assert [fm.emoji for fm in plan.message.fms] == ["💛", "💜"]
    assert plan.moves == {1: 0}
    assert sorted(s.kind for s in plan.steps) == ["added", "removed"]


def test_a_frozen_session_is_never_removed() -> None:
    """A started or imminent session missing from the draft stays in the announcement, as a frozen step, and keeps its place."""
    published = _message(_fm("🧡", hours=-1, signed=2), _fm("💛", hours=72), _fm("💚", hours=96))
    draft = as_draft(published)
    draft.fms = [replace(draft.fms[2], seats=9)]

    plan = _plan(published, draft)

    assert [fm.emoji for fm in plan.message.fms] == ["🧡", "💚"]
    assert plan.message.fms[0] == published.fms[0]
    assert plan.moves == {2: 1}
    kinds = {s.before.emoji: s.kind for s in plan.steps if s.before}
    assert kinds == {"🧡": "frozen", "💛": "removed", "💚": "changed"}


def test_a_draft_missing_only_a_frozen_session_says_it_stays() -> None:
    """When the draft differs from the announcement only by dropping a started session, nothing is planned and it is named."""
    published = _message(_fm("🧡", hours=-1), _fm("💛", hours=72))
    draft = as_draft(published)
    draft.fms.pop(0)

    refusal = plan_update(published, draft, now=NOW, retired=frozenset())

    assert isinstance(refusal, str)
    assert "🧡" in refusal
    assert refusal.endswith(UNCHANGED)


def test_removing_every_session_is_refused() -> None:
    """A draft with no session left for the announcement is refused: publish a new announcement instead."""
    published = _published()
    draft = as_draft(published)
    draft.fms = []

    assert plan_update(published, draft, now=NOW, retired=frozenset()) == (
        f"L'annonce n'aurait plus aucune session : publie plutôt une nouvelle annonce. {UNCHANGED}"
    )


def _with_added(published: FmMessageDraft, *added: Formation) -> FmMessageDraft:
    """The draft of ``published`` with these sessions added to it."""
    draft = as_draft(published)
    draft.fms.extend(added)
    return draft


def test_an_added_session_goes_last_with_no_sign_up() -> None:
    """A session new to the announcement is appended after the kept ones, with no sign-up and no flag, as one added step."""
    published = _message(_fm("🧡", hours=72, signed=2), _fm("💛", hours=96, name="Brodeuse"))
    early = _fm("💚", hours=24, name="Résine", registered_users=[{"user_id": 1}], notified_hour_before=True)

    plan = _plan(published, _with_added(published, early))

    assert [fm.emoji for fm in plan.message.fms] == ["🧡", "💛", "💚"]
    assert plan.message.fms[:2] == published.fms
    added = plan.message.fms[2]
    assert (added.registered_users, added.waitlisted_users) == ([], [])
    assert (added.notified_hour_before, added.notified_at_start) == (False, False)
    (step,) = [s for s in plan.steps if s.kind == "added"]
    assert (step.before, step.after) == (None, added)
    assert [s.kind for s in plan.steps] == ["added"]


def test_added_sessions_are_appended_in_the_start_order_of_the_draft() -> None:
    """Several added sessions follow the kept ones ordered by start, whatever their order in the draft."""
    published = _published()
    late = _fm("💚", hours=120, name="Résine")
    early = _fm("💙", hours=100, name="Laser")

    plan = _plan(published, _with_added(published, late, early))

    assert [fm.emoji for fm in plan.message.fms] == ["🧡", "💛", "💙", "💚"]
    assert [s.after.emoji for s in plan.steps if s.after and s.kind == "added"] == ["💙", "💚"]


def test_an_added_session_keeps_its_emoji_when_free() -> None:
    """A new session wearing an emoji no session of the announcement wears, and none was retired, keeps it."""
    published = _published()

    plan = _plan(published, _with_added(published, _fm("💜", hours=90, name="Résine")))

    assert plan.message.fms[-1].emoji == "💜"


def test_an_added_session_wearing_an_announced_emoji_gets_the_first_free_one() -> None:
    """A new session whose emoji an announced session wears gets the first emoji of the pool the announcement lacks."""
    published = _published()
    twin = _fm("🧡", hours=90, name="Résine")
    draft = _with_added(published, twin)

    plan = _plan(published, draft)

    assert plan.message.fms[-1].emoji == SESSION_EMOJIS[2]
    assert plan.message.fms[-1].name == "Résine"
    assert plan.message.fms[0] == published.fms[0]


def test_an_emoji_worn_on_this_announcement_is_never_given_again() -> None:
    """An emoji with reactions on this message is not given to a new session, which gets the next free one instead."""
    published = _published()
    draft = _with_added(published, _fm("💜", hours=90, name="Résine"))

    plan = plan_update(published, draft, now=NOW, retired=frozenset({"💜", SESSION_EMOJIS[2]}))

    assert isinstance(plan, UpdatePlan)
    assert plan.message.fms[-1].emoji == SESSION_EMOJIS[3]


def test_a_free_emoji_is_not_one_another_added_session_keeps() -> None:
    """The first free emoji given to one added session is never one that another added session of the draft keeps."""
    published = _published()
    keeper = _fm(SESSION_EMOJIS[2], hours=100, name="Laser")
    twin = _fm("🧡", hours=90, name="Résine")

    plan = _plan(published, _with_added(published, keeper, twin))

    assert {fm.name: fm.emoji for fm in plan.message.fms if fm.name in {"Laser", "Résine"}} == {
        "Laser": SESSION_EMOJIS[2],
        "Résine": SESSION_EMOJIS[3],
    }


def test_a_frozen_sessions_emoji_with_a_future_start_is_a_new_session() -> None:
    """A draft session on a frozen session's emoji with another, future start is a new session; the frozen one stays."""
    frozen = _fm("🧡", hours=-1, signed=2)
    published = _message(frozen, _fm("💛", hours=72, name="Brodeuse"))
    draft = as_draft(published)
    draft.fms[0] = replace(draft.fms[0], name="Autre", start_iso=(NOW + timedelta(hours=90)).isoformat())

    plan = _plan(published, draft)

    assert plan.message.fms[0] == frozen
    added = plan.message.fms[-1]
    assert (added.name, added.emoji) == ("Autre", SESSION_EMOJIS[2])
    assert [s.kind for s in plan.steps] == ["added", "frozen"]


def test_more_than_twenty_sessions_is_refused() -> None:
    """An announcement with more sessions than there are reactions to carry is refused, with its count."""
    kept = [_fm(emoji, hours=48 + n, name=f"S{n}") for n, emoji in enumerate(SESSION_EMOJIS)]
    published = _message(*kept)

    refusal = plan_update(
        published,
        _with_added(published, _fm("🔥", hours=90, name="De trop")),
        now=NOW,
        retired=frozenset(),
    )

    assert len(kept) == MAX_SESSIONS
    assert refusal == (
        f"L'annonce compterait {MAX_SESSIONS + 1} sessions : Discord n'accepte pas plus de 20 réactions sur un message. "
        f"{UNCHANGED}"
    )


def test_exactly_twenty_sessions_is_planned_with_the_added_one_last(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nineteen published sessions and one added make twenty: planned, the added session last."""
    # Twenty sessions never fit in 2 000 characters, so the length limit is lifted to reach the count limit alone.
    monkeypatch.setattr("fablabot.helpers.formation.update.ANNOUNCEMENT_MAX_CHARS", 10**6)
    kept = [_fm(emoji, hours=48 + n, name=f"S{n}") for n, emoji in enumerate(SESSION_EMOJIS[: MAX_SESSIONS - 1])]
    published = _message(*kept)

    plan = _plan(published, _with_added(published, _fm(SESSION_EMOJIS[-1], hours=90, name="La vingtième")))

    assert len(plan.message.fms) == MAX_SESSIONS
    assert plan.message.fms[-1].name == "La vingtième"
    assert [s.kind for s in plan.steps].count("added") == 1


def test_no_free_emoji_is_refused() -> None:
    """When every emoji of the pool is held or retired, a new session that cannot keep its own is refused."""
    published = _published()
    draft = _with_added(published, _fm("🧡", hours=90, name="Résine"))

    refusal = plan_update(published, draft, now=NOW, retired=frozenset(SESSION_EMOJIS))

    assert refusal == (
        f"Plus aucun émoji libre pour une session de plus sur cette annonce : publie une nouvelle annonce. {UNCHANGED}"
    )


def test_an_added_session_in_the_past_is_refused() -> None:
    """A new session starting before now is refused like a kept one, naming it."""
    published = _published()

    refusal = plan_update(
        published,
        _with_added(published, _fm("💜", hours=-2, name="Résine")),
        now=NOW,
        retired=frozenset(),
    )

    assert refusal == f"La session **💜 Résine** commence avant maintenant : change sa date ou retire-la. {UNCHANGED}"


def test_an_added_session_counts_in_the_length_and_the_digest() -> None:
    """The announcement is measured with the added session, and the digest differs from the plan without it."""
    published = _published()
    plan = _plan(published, _with_added(published, _fm("💜", hours=90, name="Résine")))
    other = _plan(published, _with_added(published, _fm("💜", hours=91, name="Résine")))

    assert announcement_length(plan.message) > announcement_length(published)
    assert plan.digest(10) != other.digest(10)
    assert plan.digest(10) != _plan(published, replace(as_draft(published), intro="Autre")).digest(10)


def test_as_draft_empties_the_lists_and_the_flags() -> None:
    """The draft written back says what the announcement says: no sign-up and no reminder flag."""
    published = _message(_fm("🧡", signed=5, notified_hour_before=True, notified_at_start=True))

    (fm,) = as_draft(published).fms

    assert (fm.registered_users, fm.waitlisted_users) == ([], [])
    assert (fm.notified_hour_before, fm.notified_at_start) == (False, False)
    assert (fm.name, fm.seats, fm.start_iso) == ("Soudure", 5, published.fms[0].start_iso)
    assert published.fms[0].registered_users
