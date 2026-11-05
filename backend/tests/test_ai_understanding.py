"""Model-assisted question understanding (app/advisor/understanding.py).

Every test uses a scripted provider; nothing here calls a real model. The model
only ever picks WHICH engine answer a question gets - these tests pin that it
cannot do more, and that every failure falls back to keyword routing.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from app.advisor.answers import Intent
from app.advisor.chat import ask, conversations
from app.advisor.facts import build_facts
from app.advisor.understanding import (
    MODEL_INTENTS,
    system_prompt,
    understand,
    user_message,
)
from app.audit.record import CompletedCourse, StudentRecord
from app.catalog.loader import load_program
from app.config import get_settings
from app.llm.chat import ChatTurn, ProviderUnavailable
from app.schemas.provenance import Provenance

PROGRAM_FILE = "morgan_cosc_bs_2026_2028.yaml"
FOLLOW_UP = "It says I have earned 138 credits. So I don't think I need anymore. Right?"


class Router:
    """Scripted routing provider: returns a fixed reply, records what it was sent."""

    provider_id = "stub"

    def __init__(self, reply="", *, usable=True, raises=None, json_mode=True):
        self.name = "router-model"
        self._reply = reply if isinstance(reply, str) else json.dumps(reply)
        self._usable = usable
        self._raises = raises
        self.calls: list[tuple[str, list[ChatTurn]]] = []
        if json_mode:
            self.complete_json = self._complete

    def available(self):
        return (self._usable, "ok" if self._usable else "off")

    def _complete(self, *, system, messages, max_tokens=800):
        self.calls.append((system, list(messages)))
        if self._raises:
            raise self._raises
        return self._reply

    def complete(self, *, system, messages, max_tokens=800):
        return self._complete(system=system, messages=messages, max_tokens=max_tokens)


@pytest.fixture(scope="module")
def program():
    return load_program(get_settings().catalog_dir / PROGRAM_FILE)


@pytest.fixture(scope="module")
def catalog(program):
    return frozenset(c.code for c in program.courses)


@pytest.fixture(scope="module")
def facts(program):
    rows = [
        ("COSC111", "Fall 2025", "A", "4"),
        ("COSC112", "Spring 2026", "B", "4"),
        ("ENGL101", "Fall 2025", "A", "3"),
        ("COSC220", "Fall 2026", "IP", "4"),
    ]
    record = StudentRecord(
        student_id="ai-understanding",
        completed=[
            CompletedCourse(
                code=c,
                term=t,
                grade=g,
                credits=Decimal(cr),
                provenance=Provenance.STUDENT_CONFIRMED,
                institution="Morgan State University",
            )
            for c, t, g, cr in rows
        ],
    )
    return build_facts(program, record)


HISTORY = [
    ChatTurn(role="user", content="Can I take COSC 354?"),
    ChatTurn(role="assistant", content="COSC354 needs COSC220 (in progress now) and COSC241."),
]


# --------------------------------------------------------------------------- parsing


def test_model_route_is_used(catalog) -> None:
    router = Router({"intent": "explain_requirement", "course_codes": ["COSC354"]})
    u = understand(
        "Why does this course not count?", HISTORY, provider=router, catalog_codes=catalog
    )
    assert (u.intent, u.codes, u.source) == (Intent.EXPLAIN_REQUIREMENT, ["COSC354"], "model")


def test_prompt_is_small_and_lists_only_model_intents(catalog) -> None:
    router = Router({"intent": "credits", "course_codes": []})
    understand("How many credits?", HISTORY, provider=router, catalog_codes=catalog)
    system, messages = router.calls[0]
    assert system == system_prompt()
    for intent in MODEL_INTENTS:
        assert intent.value in system
    for code_only in ("crisis", "wellbeing", "no_record"):
        assert f"- {code_only}:" not in system
    assert "JSON" in system and len(system) < 2000
    assert "Can I take COSC 354?" in messages[0].content  # history is included


def test_long_history_turns_are_clipped() -> None:
    long = [ChatTurn(role="assistant", content="x" * 5000)]
    assert len(user_message("q", long)) < 400


@pytest.mark.parametrize(
    "reply",
    [
        "not json at all",
        "{broken",
        json.dumps(["credits"]),
        json.dumps({"intent": "drop_all_my_classes"}),
        json.dumps({"intent": 7}),
        json.dumps({"intent": "crisis"}),
        json.dumps({"intent": "no_record"}),
        json.dumps({"intent": "wellbeing"}),
        "",
    ],
)
def test_bad_replies_fall_back_to_keywords(catalog, reply) -> None:
    u = understand(
        "How many credits am I missing?", [], provider=Router(reply), catalog_codes=catalog
    )
    assert u.source == "keywords"
    assert u.intent is Intent.CREDITS
    assert u.reason


@pytest.mark.parametrize(
    "error", [ProviderUnavailable("the model did not respond in time"), RuntimeError("boom")]
)
def test_provider_errors_fall_back_to_keywords(catalog, error) -> None:
    u = understand(
        "What should I take next semester?",
        [],
        provider=Router(raises=error),
        catalog_codes=catalog,
    )
    assert (u.intent, u.source) == (Intent.NEXT_SEMESTER, "keywords")


def test_no_provider_means_keywords(catalog) -> None:
    u = understand("What should I take next semester?", [], provider=None, catalog_codes=catalog)
    assert u.source == "keywords"


def test_reasoning_block_and_prose_around_json_are_tolerated(catalog) -> None:
    raw = '<think>hmm 12345</think> Sure: {"intent": "plan_semesters", "course_codes": []}'
    u = understand("lay it all out", [], provider=Router(raw), catalog_codes=catalog)
    assert (u.intent, u.source) == (Intent.PLAN_SEMESTERS, "model")


def test_provider_without_json_mode_uses_complete(catalog) -> None:
    router = Router({"intent": "progress"}, json_mode=False)
    u = understand("am I on track or what", [], provider=router, catalog_codes=catalog)
    assert u.intent is Intent.PROGRESS and router.calls


def test_unknown_from_model_loses_to_a_keyword_route(catalog) -> None:
    u = understand(
        "How many credits am I missing?",
        [],
        provider=Router({"intent": "unknown"}),
        catalog_codes=catalog,
    )
    assert (u.intent, u.source) == (Intent.CREDITS, "keywords")


def test_confirmation_is_never_answered_with_credits_alone(catalog) -> None:
    # Seen live: qwen2.5:7b routed this follow-up to "credits".
    u = understand(FOLLOW_UP, [], provider=Router({"intent": "credits"}), catalog_codes=catalog)
    assert (u.intent, u.source) == (Intent.REMAINING_REQUIREMENTS, "model")


# --------------------------------------------------------------------------- safety


def test_crisis_and_wellbeing_never_reach_the_model(catalog) -> None:
    for question in ("I want to kill myself", "I'm so overwhelmed with everything"):
        router = Router({"intent": "credits"})
        u = understand(question, [], provider=router, catalog_codes=catalog)
        assert u.source == "keywords"
        assert router.calls == []


# --------------------------------------------------------------------------- codes


def test_codes_must_be_in_the_catalog_and_the_conversation(catalog) -> None:
    router = Router(
        {
            "intent": "course_info",
            # COSC354 is in the history; COSC459 is real but was never mentioned;
            # COSC999 does not exist; the last is not a code at all.
            "course_codes": ["COSC 354", "COSC459", "COSC999", "rm -rf"],
        }
    )
    u = understand("tell me about it", HISTORY, provider=router, catalog_codes=catalog)
    assert u.codes == ["COSC354"]


def test_question_codes_beat_model_codes(catalog) -> None:
    router = Router({"intent": "unlocks", "course_codes": ["COSC354"]})
    u = understand("and what about COSC 241?", HISTORY, provider=router, catalog_codes=catalog)
    assert u.codes == ["COSC241"]


# --------------------------------------------------------------------------- ask()


class Rephraser:
    provider_id = "stub"
    name = "stub-model"

    def __init__(self):
        self.calls = 0

    def available(self):
        return (True, "ok")

    def complete(self, *, system, messages, max_tokens=800):
        self.calls += 1
        return "garbled reply that drops every figure"


def test_follow_up_is_routed_by_the_model_end_to_end(facts) -> None:
    conversations.clear("ai-u", "c1")
    router = Router({"intent": "remaining_requirements", "follow_up_of": "credits"})
    ask(
        facts,
        "How many credits am I missing?",
        student_id="ai-u",
        conversation_id="c1",
        use_llm=False,
    )
    reply = ask(
        facts,
        FOLLOW_UP,
        student_id="ai-u",
        conversation_id="c1",
        provider=Rephraser(),
        understanding_provider=router,
    )
    assert reply.intent is Intent.REMAINING_REQUIREMENTS
    assert reply.understood_by == "model"
    assert reply.answer.startswith("No, not yet.")
    # The model saw the earlier turn.
    assert "How many credits am I missing?" in router.calls[0][1][0].content
    # The rephrasing failed the check, so the engine text is shown and labelled so.
    assert reply.source == "engine"


def test_model_route_still_gets_the_consistency_check(facts) -> None:
    rephraser = Rephraser()
    reply = ask(
        facts,
        "lay out my remaining terms",
        student_id="ai-u",
        conversation_id="c2",
        provider=rephraser,
        understanding_provider=Router({"intent": "plan_semesters"}),
    )
    assert reply.intent is Intent.PLAN_SEMESTERS
    assert reply.source == "engine" and reply.discarded
    assert rephraser.calls == 1


def test_injected_provider_alone_keeps_keyword_routing(facts) -> None:
    rephraser = Rephraser()
    reply = ask(facts, "How many credits am I missing?", student_id="ai-u", provider=rephraser)
    assert reply.understood_by == "keywords"
    assert rephraser.calls == 1  # rephrasing only, no routing call


def test_unavailable_router_means_keywords(facts) -> None:
    reply = ask(
        facts,
        "How many credits am I missing?",
        student_id="ai-u",
        provider=Rephraser(),
        understanding_provider=Router({"intent": "progress"}, usable=False),
    )
    assert (reply.understood_by, reply.intent) == ("keywords", Intent.CREDITS)


def test_use_llm_false_never_routes_by_model(facts) -> None:
    router = Router({"intent": "progress"})
    reply = ask(
        facts,
        "How many credits am I missing?",
        student_id="ai-u",
        use_llm=False,
        understanding_provider=router,
    )
    assert reply.understood_by == "keywords" and router.calls == []


def test_crisis_reply_is_never_rephrased(facts) -> None:
    rephraser = Rephraser()
    reply = ask(facts, "I want to end my life", student_id="ai-u", provider=rephraser)
    assert reply.intent is Intent.CRISIS
    assert "988" in reply.answer and rephraser.calls == 0


def test_chat_endpoint_reports_understood_by(client) -> None:
    response = client.post("/advisor/chat", json={"message": "hello", "use_llm": False})
    assert response.status_code == 200
    assert response.json()["understood_by"] == "keywords"


def test_rephrase_off_keeps_model_routing_and_skips_the_rephrasing_call(facts) -> None:
    rephraser = Rephraser()
    reply = ask(
        facts,
        "lay out my remaining terms",
        student_id="ai-u",
        conversation_id="c-rephrase-off",
        provider=rephraser,
        understanding_provider=Router({"intent": "plan_semesters"}),
        rephrase=False,
    )
    assert reply.intent is Intent.PLAN_SEMESTERS
    assert reply.understood_by == "model"
    assert rephraser.calls == 0
    # Engine text, labelled as engine text: the model only chose the answer.
    assert reply.source == "engine" and reply.answer == reply.prepared
    assert "language model read your question" in reply.notice
    assert "switched off" in reply.notice


def test_rephrase_off_with_keyword_routing_says_engine_only(facts) -> None:
    reply = ask(
        facts,
        "How many credits am I missing?",
        student_id="ai-u",
        conversation_id="c-rephrase-off-kw",
        provider=Rephraser(),
        rephrase=False,
    )
    assert reply.understood_by == "keywords"
    assert reply.source == "engine"
    assert "language model read" not in reply.notice
    assert "switched off" in reply.notice


def test_every_model_intent_has_a_description() -> None:
    """A route the prompt cannot describe would raise while building the prompt and
    silently drop every question back to keywords (seen when PR #16 added
    GRADUATE_EARLY next to this module)."""
    from app.advisor.understanding import _DESCRIPTIONS, MODEL_INTENTS

    assert set(MODEL_INTENTS) <= set(_DESCRIPTIONS)
