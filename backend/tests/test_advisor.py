"""The conversational advisor.

Two things are being pinned here, and the second matters more than the first:

1. The advisor answers the five demo questions correctly from the degree engine,
   with no model involved.
2. When a model IS involved, it cannot introduce an academic claim of its own.
   A model that invents a course code has its answer discarded, not annotated.

Every test uses a stub provider. Nothing here calls a real API, so the suite runs
offline, costs nothing, and a CI run is not at the mercy of someone's quota.
"""

from __future__ import annotations

import pytest

from app.advisor.answers import Intent, detect_intent, deterministic_answer
from app.advisor.chat import ConversationStore, _invented_codes, _known_codes, ask, conversations
from app.advisor.facts import build_facts, render_facts
from app.audit.record import CompletedCourse, StudentRecord
from app.catalog.registry import registry
from app.config import get_settings
from app.llm.chat import ChatTurn, ProviderUnavailable
from app.schemas.provenance import Provenance

PROGRAM = "morgan_cosc_bs_2026_2028"

TRANSCRIPT = (
    b"Fall 2024\n"
    b"COSC 111  Introduction to Computer Science I   4.00  A\n"
    b"ENGL 101  Composition I                       3.00  B\n"
    b"ENGL 102  Composition II                      3.00  A\n"
    b"UNIV 101  University First-Year Seminar       1.00  A\n"
    b"Spring 2025\n"
    b"COSC 112  Introduction to Computer Science II  4.00  A\n"
)


def enrol(client, *, program=PROGRAM, data=TRANSCRIPT):
    upload = client.post(
        "/ingest/transcript",
        files={"file": ("t.txt", data, "text/plain")},
        data={"program_id": program},
    )
    rows = upload.json()["extraction"]["courses"]
    client.post(
        "/ingest/confirm",
        json={
            "program_id": program,
            "source_name": "t.txt",
            "extractor": "text-parser",
            "courses": [{"extracted": r} for r in rows],
        },
    )
    return rows


# --------------------------------------------------------------------------
# Stub providers. Deliberately not mocks of a real SDK - the ChatProvider
# protocol is the seam, and a stub that satisfies it proves the seam holds.
# --------------------------------------------------------------------------


class StubProvider:
    provider_id = "stub"

    def __init__(self, reply: str = "Rephrased by the stub.", usable: bool = True) -> None:
        self.name = "stub-model"
        self._reply = reply
        self._usable = usable
        self.calls: list[tuple[str, list[ChatTurn]]] = []

    def available(self):
        return (self._usable, "stub ready" if self._usable else "stub switched off")

    def complete(self, *, system, messages, max_tokens=800):
        self.calls.append((system, list(messages)))
        return self._reply


class ExplodingProvider(StubProvider):
    def complete(self, *, system, messages, max_tokens=800):
        raise ProviderUnavailable("quota exhausted (429)")


@pytest.fixture
def facts():
    """Facts for a student with five confirmed courses against the real catalog."""
    registry.load(get_settings().catalog_dir)
    program = registry.get(PROGRAM)
    record = StudentRecord(
        student_id="fixture-student",
        completed=[
            CompletedCourse(
                code=code,
                term="Fall 2024",
                grade="A",
                credits=credits,
                provenance=Provenance.STUDENT_CONFIRMED,
            )
            for code, credits in [
                ("COSC111", 4),
                ("COSC112", 4),
                ("ENGL101", 3),
                ("ENGL102", 3),
                ("UNIV101", 1),
            ]
        ],
    )
    return build_facts(program, record)


@pytest.fixture(autouse=True)
def _clear_conversations():
    conversations._data.clear()
    yield
    conversations._data.clear()


class TestIntentDetection:
    @pytest.mark.parametrize(
        "question,expected",
        [
            ("What courses do I still need to graduate?", Intent.REMAINING_REQUIREMENTS),
            ("How many credits am I missing?", Intent.CREDITS),
            ("What should I take next semester?", Intent.NEXT_SEMESTER),
            ("Have I completed my major requirements?", Intent.MAJOR_COMPLETE),
            ("Why is COSC 220 recommended?", Intent.WHY_RECOMMENDED),
            ("How am I doing?", Intent.PROGRESS),
            ("What is the capital of France?", Intent.UNKNOWN),
        ],
    )
    def test_the_five_demo_questions_route_correctly(self, question, expected) -> None:
        assert detect_intent(question) is expected

    def test_why_beats_course_info(self) -> None:
        """'Why is COSC 220 recommended' contains a course code AND a why."""
        assert detect_intent("why should i take COSC 220") is Intent.WHY_RECOMMENDED


class TestDeterministicAnswers:
    """These run with no provider at all. This is the demo's floor."""

    def test_credits_answer_uses_engine_numbers(self, facts) -> None:
        answer = deterministic_answer(facts, "How many credits am I missing?")
        assert answer.intent is Intent.CREDITS
        assert str(facts.credits_earned) in answer.text
        assert str(facts.credits_remaining) in answer.text
        assert str(facts.credits_required) in answer.text

    def test_remaining_lists_real_blocks(self, facts) -> None:
        answer = deterministic_answer(facts, "What do I still need to graduate?")
        assert answer.intent is Intent.REMAINING_REQUIREMENTS
        outstanding = [b for b in facts.blocks if not b["satisfied"]]
        assert outstanding, "fixture should not already satisfy the whole degree"
        assert outstanding[0]["name"] in answer.text

    def test_next_semester_only_recommends_eligible_courses(self, facts) -> None:
        answer = deterministic_answer(facts, "What should I take next semester?")
        assert answer.intent is Intent.NEXT_SEMESTER
        for rec in facts.recommended[:6]:
            assert rec.course.code in answer.text
        # A blocked course must never be offered as a recommendation.
        for blocked in facts.blocked:
            assert "Not yet eligible" in answer.text or blocked.course.code not in answer.text

    def test_major_complete_answers_no_while_blocks_remain(self, facts) -> None:
        answer = deterministic_answer(facts, "Have I completed my major requirements?")
        assert answer.intent is Intent.MAJOR_COMPLETE
        assert "Not yet" in answer.text

    def test_why_recommended_uses_engine_reasons(self, facts) -> None:
        assert facts.recommended, "fixture should produce recommendations"
        code = facts.recommended[0].course.code
        answer = deterministic_answer(facts, f"Why is {code} recommended?")
        assert answer.intent is Intent.WHY_RECOMMENDED
        assert code in answer.text
        for reason in facts.recommended[0].reasons:
            assert reason in answer.text

    def test_never_promises_graduation_while_catalog_is_partial(self, facts) -> None:
        for question in [
            "What do I still need to graduate?",
            "How many credits am I missing?",
            "How am I doing?",
        ]:
            text = deterministic_answer(facts, question).text
            assert "partial" in text.lower() or "advisor" in text.lower()

    def test_refuses_a_course_it_has_no_data_for(self, facts) -> None:
        answer = deterministic_answer(facts, "Why is COSC 999 recommended?")
        assert answer.grounded is False
        assert "will not invent" in answer.text or "not on your recommendation list" in answer.text

    def test_no_record_gives_instructions_not_numbers(self) -> None:
        answer = deterministic_answer(None, "How many credits am I missing?")
        assert answer.intent is Intent.NO_RECORD
        assert "Upload a transcript" in answer.text

    def test_unknown_question_offers_the_demo_questions(self, facts) -> None:
        answer = deterministic_answer(facts, "What is the capital of France?")
        assert answer.intent is Intent.UNKNOWN
        assert "I will not guess" in answer.text


class TestFactsRendering:
    def test_facts_block_carries_the_coverage_caveat(self, facts) -> None:
        rendered = render_facts(facts)
        assert "CATALOG COVERAGE" in rendered
        assert "PARTIAL" in rendered

    def test_facts_block_forbids_inventing_a_percentage(self, facts) -> None:
        assert facts.percent_complete is None
        assert "Do not estimate one." in render_facts(facts)

    def test_known_codes_covers_record_and_recommendations(self, facts) -> None:
        known = _known_codes(facts)
        assert "COSC111" in known
        assert facts.recommended[0].course.code.replace(" ", "").upper() in known


class TestModelIsOnlyAllowedToPhrase:
    #: A faithful rephrasing of the engine's credits answer for the fixture student.
    FAITHFUL_CREDITS = (
        "So far you've earned 15 of the 120 credits your degree needs, which leaves "
        "105 to go - you're 12.5% of the way through by credit count. Keep in mind "
        "the encoded catalog is still partial, so check with your advisor before "
        "relying on that total."
    )

    def test_uses_the_model_when_one_is_available(self, facts) -> None:
        stub = StubProvider(self.FAITHFUL_CREDITS)
        reply = ask(facts, "How many credits am I missing?", student_id="s1", provider=stub)
        assert reply.source == "engine+llm"
        assert reply.answer == self.FAITHFUL_CREDITS
        assert stub.calls, "the provider should have been called"

    def test_a_model_that_changes_a_credit_figure_is_discarded(self, facts) -> None:
        """This reply passed the original course-code-only guard. It says 101 where
        the engine computed 105, and it drops the partial-catalog warning."""
        stub = StubProvider("You need 101 more credits. Take COSC 220 next.")
        reply = ask(facts, "How many credits am I missing?", student_id="s1", provider=stub)
        assert reply.source == "engine"
        assert "105" in reply.answer
        assert reply.discarded == "You need 101 more credits. Take COSC 220 next."

    def test_the_prompt_contains_the_facts_and_the_prepared_answer(self, facts) -> None:
        stub = StubProvider()
        ask(facts, "How many credits am I missing?", student_id="s1", provider=stub)
        system, _ = stub.calls[0]
        assert "VERIFIED FACTS FOR THIS STUDENT" in system
        assert "PREPARED ANSWER" in system
        assert str(facts.credits_remaining) in system

    def test_unavailable_provider_still_answers_correctly(self, facts) -> None:
        reply = ask(
            facts,
            "How many credits am I missing?",
            student_id="s1",
            provider=StubProvider(usable=False),
        )
        assert reply.source == "engine"
        assert str(facts.credits_remaining) in reply.answer
        assert "not available" in (reply.notice or "")

    def test_exhausted_quota_degrades_to_the_engine_answer(self, facts) -> None:
        reply = ask(
            facts, "How many credits am I missing?", student_id="s1", provider=ExplodingProvider()
        )
        assert reply.source == "engine"
        assert str(facts.credits_remaining) in reply.answer
        assert "quota exhausted" in (reply.notice or "")

    def test_a_model_that_invents_a_course_is_discarded(self, facts) -> None:
        """The guarantee the whole architecture exists to provide."""
        stub = StubProvider("You should take COSC 499 Advanced Wizardry next semester.")
        reply = ask(facts, "What should I take next semester?", student_id="s1", provider=stub)
        assert reply.source == "engine"
        assert "COSC 499" not in reply.answer
        assert "COSC499" in (reply.notice or "")

    def test_a_model_reusing_real_codes_is_accepted(self, facts) -> None:
        eligible = ", ".join(r.course.code for r in facts.recommended[:6])
        stub = StubProvider(
            f"You're eligible now for {eligible}. COSC354 needs COSC220 and COSC241 "
            "first, and COSC352 needs COSC220, so those aren't available yet. The "
            "catalog is still partial, so confirm with your advisor."
        )
        reply = ask(facts, "What should I take next semester?", student_id="s1", provider=stub)
        assert reply.source == "engine+llm", reply.notice
        assert facts.recommended[0].course.code in reply.answer

    def test_a_model_naming_only_one_course_is_discarded(self, facts) -> None:
        """Real codes, but the other eligible courses and every blocker are gone."""
        code = facts.recommended[0].course.code
        stub = StubProvider(f"Take {code} next term - it opens up the rest of the major.")
        reply = ask(facts, "What should I take next semester?", student_id="s1", provider=stub)
        assert reply.source == "engine"
        assert "drops course codes" in (reply.notice or "")

    def test_use_llm_false_skips_the_provider_entirely(self, facts) -> None:
        stub = StubProvider()
        reply = ask(
            facts, "How many credits am I missing?", student_id="s1", provider=stub, use_llm=False
        )
        assert reply.source == "engine"
        assert stub.calls == []

    def test_no_record_never_reaches_the_provider(self) -> None:
        stub = StubProvider()
        reply = ask(None, "How many credits?", student_id="s1", provider=stub)
        assert reply.intent is Intent.NO_RECORD
        assert stub.calls == []


class TestInventedCodeDetection:
    SUBJECTS = frozenset({"COSC", "MATH", "ENGL"})

    def test_spots_a_fabricated_code(self) -> None:
        assert _invented_codes("take COSC 499", {"COSC220"}, self.SUBJECTS) == ["COSC499"]

    def test_accepts_a_known_code_however_spaced(self) -> None:
        assert (
            _invented_codes("take COSC 220 and cosc-281", {"COSC220", "COSC281"}, self.SUBJECTS)
            == []
        )

    def test_ignores_plain_numbers(self) -> None:
        assert _invented_codes("you need 31 credits across 10 courses", set(), self.SUBJECTS) == []

    def test_ordinary_prose_is_not_a_course_code(self) -> None:
        """Regression: 'need 101' parsed as NEED101 and discarded a correct answer.

        A false positive here is not harmless - it throws away a good model reply
        and makes the advisor look broken, so the subject must come from the
        catalog rather than from whatever word preceded the digits.
        """
        assert _invented_codes("You need 101 more credits.", set(), self.SUBJECTS) == []
        assert _invented_codes("that covers 3 of 120 credits", set(), self.SUBJECTS) == []

    def test_an_unknown_subject_is_not_guessed_at(self) -> None:
        assert _invented_codes("take BIOL 101", {"COSC220"}, self.SUBJECTS) == []


class TestConversationMemory:
    def test_history_accumulates_within_a_session(self, facts) -> None:
        stub = StubProvider()
        ask(facts, "How many credits am I missing?", student_id="s1", provider=stub)
        ask(facts, "And what should I take?", student_id="s1", provider=stub)
        _, messages = stub.calls[-1]
        assert len(messages) > 1
        assert messages[0].content == "How many credits am I missing?"

    def test_history_is_capped(self, facts) -> None:
        stub = StubProvider()
        for i in range(20):
            ask(facts, f"question {i}", student_id="s1", provider=stub, history_turns=3)
        convo = conversations.get("s1", "default")
        assert len(convo.turns) <= 6

    def test_one_student_cannot_see_another_conversation(self, facts) -> None:
        stub = StubProvider()
        ask(facts, "my secret question", student_id="alice", provider=stub)
        ask(facts, "bob question", student_id="bob", provider=stub)
        _, messages = stub.calls[-1]
        assert all("secret" not in m.content for m in messages)

    def test_clearing_is_scoped_to_one_student(self) -> None:
        store = ConversationStore()
        store.get("alice", "default").add("user", "hello", keep=5)
        store.get("bob", "default").add("user", "hi", keep=5)
        assert store.clear("alice", "default") is True
        assert store.get("bob", "default").turns, "bob's history must survive"

    def test_store_evicts_oldest(self) -> None:
        store = ConversationStore(max_conversations=2)
        for name in ["a", "b", "c"]:
            store.get(name, "default")
        assert len(store._data) == 2


class TestAdvisorApi:
    def test_health_reports_the_provider(self, client) -> None:
        response = client.get("/advisor/health")
        assert response.status_code == 200
        body = response.json()
        assert body["provider"] in {"anthropic", "openai", "gemini", "ollama"}
        assert len(body["suggested_questions"]) == 5

    def test_chat_requires_a_token(self, anon_client) -> None:
        response = anon_client.post("/advisor/chat", json={"message": "hi"})
        assert response.status_code == 401

    def test_chat_answers_from_the_record(self, client) -> None:
        enrol(client)
        response = client.post("/advisor/chat", json={"message": "How many credits am I missing?"})
        assert response.status_code == 200
        body = response.json()
        assert body["intent"] == "credits"
        assert body["source"] in {"engine", "engine+llm"}
        assert "15" in body["answer"]  # 15 credits earned in the fixture transcript

    def test_chat_without_a_record_explains_what_to_do(self, client) -> None:
        response = client.post("/advisor/chat", json={"message": "How many credits?"})
        assert response.status_code == 200
        assert response.json()["intent"] == "no_record"

    def test_each_demo_question_gets_a_grounded_answer(self, client) -> None:
        enrol(client)
        for question in [
            "What courses do I still need to graduate?",
            "How many credits am I missing?",
            "What should I take next semester?",
            "Have I completed my major requirements?",
        ]:
            body = client.post("/advisor/chat", json={"message": question}).json()
            assert body["grounded"] is True, question
            assert len(body["answer"]) > 40, question

    def test_conversation_is_scoped_to_the_signed_in_student(self, client, other_student) -> None:
        """Another student's conversation id must not surface their history."""
        enrol(client)
        client.post(
            "/advisor/chat",
            json={"message": "How many credits am I missing?", "conversation_id": "shared"},
        )
        response = client.post(
            "/advisor/chat",
            json={"message": "What should I take next semester?", "conversation_id": "shared"},
            headers=other_student["headers"],
        )
        assert response.status_code == 200
        # The other student has no record, so they get the no-record answer -
        # never the first student's numbers.
        assert response.json()["intent"] == "no_record"

    def test_rejects_an_empty_message(self, client) -> None:
        assert client.post("/advisor/chat", json={"message": ""}).status_code == 422

    def test_rejects_an_enormous_message(self, client) -> None:
        response = client.post("/advisor/chat", json={"message": "x" * 5000})
        assert response.status_code == 422

    def test_conversation_can_be_reset(self, client) -> None:
        enrol(client)
        client.post("/advisor/chat", json={"message": "hi", "conversation_id": "c1"})
        response = client.delete("/advisor/chat/c1")
        assert response.status_code == 200
        assert response.json()["cleared"] is True
