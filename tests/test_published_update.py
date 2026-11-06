"""What an update of the published announcement would change, planned from the two messages alone."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any

import pytest

from fablabot.helpers.constants import PARIS_TZ
from fablabot.helpers.formation.rendering import announcement_length, render_message
from fablabot.helpers.formation.update import UpdatePlan, as_draft, is_frozen, plan_update
from fablabot.models.formation import FmMessageDraft, Formation

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=PARIS_TZ)
NBSP = "\N{NO-BREAK SPACE}"
UNCHANGED = "Rien n'a été modifié."
INTERIM = (
    "Ajouter ou retirer une session d'une annonce publiée arrive bientôt : pour l'instant, publie une nouvelle annonce. "
    f"{UNCHANGED}"
)


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
    plan = plan_update(published, draft, now=NOW)
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

    refusal = plan_update(published, as_draft(published), now=NOW)

    assert refusal == f"Le brouillon dit déjà ce que dit l'annonce publiée : rien à mettre à jour. {UNCHANGED}"


def test_a_session_to_change_with_a_past_start_is_refused() -> None:
    """A kept session moved to a start before now is refused, naming it and how to correct it."""
    published = _published()
    draft = as_draft(published)
    draft.fms[1] = replace(draft.fms[1], start_iso=(NOW - timedelta(hours=1)).isoformat())

    refusal = plan_update(published, draft, now=NOW)

    assert refusal == f"La session **💛 Brodeuse** commence avant maintenant : change sa date ou retire-la. {UNCHANGED}"


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

    assert plan_update(published, draft, now=NOW) == f"{refusal} {UNCHANGED}"


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

    refusal = plan_update(published, _sized(published, 2001), now=NOW)

    assert refusal == (
        f"L'annonce mise à jour ferait 2{NBSP}001 caractères toutes places prises, Discord en accepte 2 000 : "
        f"retire une session ou raccourcis une description. {UNCHANGED}"
    )
    assert isinstance(plan_update(published, _sized(published, 2000), now=NOW), UpdatePlan)


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
def test_a_new_start_or_trainer_clears_the_hour_before_flag_only(field: str) -> None:
    """A new start or trainer gives the hour-before reminder back; no other change, and no other flag, moves."""
    published = _message(_fm("🧡", signed=2, notified_hour_before=True), _fm("💛", hours=72, name="Brodeuse"))
    draft = as_draft(published)
    draft.fms[0] = replace(draft.fms[0], **CHANGES[field])

    kept = _plan(published, draft).message.fms[0]

    assert kept.notified_hour_before is (field not in ("start", "trainer"))
    assert kept.notified_at_start is False
    assert kept.registered_users == published.fms[0].registered_users


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
    after = plan_update(published, draft, now=later)

    assert isinstance(after, UpdatePlan)
    assert [s.kind for s in after.steps] == ["changed", "frozen"]
    assert after.digest(10) != before.digest(10)


def test_an_added_or_removed_session_is_refused_for_now() -> None:
    """A draft that adds a session, or drops one that is not frozen, is refused for the moment."""
    published = _published()
    added = as_draft(published)
    added.fms.append(_fm("💚", hours=90, name="Résine"))
    removed = as_draft(published)
    removed.fms.pop()

    assert plan_update(published, added, now=NOW) == INTERIM
    assert plan_update(published, removed, now=NOW) == INTERIM


def test_as_draft_empties_the_lists_and_the_flags() -> None:
    """The draft written back says what the announcement says: no sign-up and no reminder flag."""
    published = _message(_fm("🧡", signed=5, notified_hour_before=True, notified_at_start=True))

    (fm,) = as_draft(published).fms

    assert (fm.registered_users, fm.waitlisted_users) == ([], [])
    assert (fm.notified_hour_before, fm.notified_at_start) == (False, False)
    assert (fm.name, fm.seats, fm.start_iso) == ("Soudure", 5, published.fms[0].start_iso)
    assert published.fms[0].registered_users
