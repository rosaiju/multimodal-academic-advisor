"""The rephrasing check, and the in-progress answers it depends on.

Several replies below are verbatim text from qwen2.5:7b via Ollama, captured in
the first live run (Oct 2026). They are recorded here as fixtures; this suite
never calls a model. Each one either passed the original course-code-only guard
while being wrong, or was rejected by it while being right.

All coursework is synthetic.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.advisor.answers import Intent, detect_intent, deterministic_answer
from app.advisor.chat import _strip_reasoning, ask, conversations
from app.advisor.consistency import check_rephrasing, numbers
from app.advisor.facts import build_facts, render_facts
from app.audit.record import CompletedCourse, StudentRecord
from app.catalog.registry import registry
from app.config import get_settings
from app.schemas.provenance import Provenance

PROGRAM = "morgan_cosc_bs_2026_2028"


def _record(rows) -> StudentRecord:
    return StudentRecord(
        student_id="synthetic-student",
        completed=[
            CompletedCourse(
                code=code,
                term=term,
                grade=grade,
                credits=Decimal(credits),
                provenance=Provenance.STUDENT_CONFIRMED,
            )
            for code, term, grade, credits in rows
        ],
    )


#: Same student as scripts/live_llm_smoke.py. COSC 220 is in progress, which
#: blocks COSC 352 (needs 220) and COSC 354 (needs 220 and 241).
ROWS = [
    ("COSC111", "Fall 2025", "A", 4),
    ("COSC112", "Spring 2026", "B", 4),
    ("MATH141", "Spring 2026", "B", 4),
    ("ENGL101", "Fall 2025", "A", 3),
    ("COSC220", "Fall 2026", "IP", 4),
]


@pytest.fixture
def facts():
    registry.load(get_settings().catalog_dir)
    return build_facts(registry.get(PROGRAM), _record(ROWS))


@pytest.fixture(autouse=True)
def _clear_conversations():
    conversations._data.clear()
    yield
    conversations._data.clear()


def check(facts, question: str, text: str) -> list[str]:
    return check_rephrasing(deterministic_answer(facts, question), text, facts, question=question)


class StubProvider:
    provider_id = "stub"
    name = "stub-model"

    def __init__(self, reply) -> None:
        self._reply = reply

    def available(self):
        return True, "stub"

    def complete(self, *, system, messages, max_tokens=800):
        if isinstance(self._reply, BaseException):
            raise self._reply
        return self._reply


# ---------------------------------------------------------------------------
# In-progress coursework reaches the advisor
# ---------------------------------------------------------------------------


class TestInProgressFacts:
    def test_facts_carry_in_progress_courses(self, facts) -> None:
        assert [c for c, _, _ in facts.in_progress] == ["COSC220"]
        assert facts.credits_in_progress == Decimal(4)
        assert "COSC220" not in [c for c, _, _ in facts.completed]

    def test_in_progress_credits_are_not_earned(self, facts) -> None:
        assert facts.credits_earned == Decimal(15)

    def test_facts_block_tells_the_model_they_are_not_counted(self, facts) -> None:
        rendered = render_facts(facts)
        assert "IN PROGRESS NOW (not completed, not counted" in rendered
        assert "COSC220" in rendered.split("IN PROGRESS NOW")[1].split("REQUIREMENT BLOCKS")[0]

    @pytest.mark.parametrize(
        "question",
        [
            "What courses am I taking right now?",
            "What am I currently enrolled in right now?",
            "Which of my courses are in progress?",
        ],
    )
    def test_questions_about_now_route_to_in_progress(self, question) -> None:
        assert detect_intent(question) is Intent.IN_PROGRESS

    def test_next_semester_still_routes_to_next_semester(self) -> None:
        assert detect_intent("What should I enroll in next semester?") is Intent.NEXT_SEMESTER

    def test_in_progress_answer(self, facts) -> None:
        answer = deterministic_answer(facts, "What courses am I taking right now?")
        assert answer.intent is Intent.IN_PROGRESS
        assert "COSC220" in answer.text
        assert "4 credits in progress" in answer.text
        assert "15 earned credits" in answer.text
        assert "COSC352" in answer.text and "COSC354" in answer.text

    def test_asking_about_an_in_progress_course(self, facts) -> None:
        answer = deterministic_answer(facts, "Tell me about COSC 220")
        assert "taking COSC220" in answer.text
        assert "does not count" in answer.text

    def test_blockers_say_which_prerequisite_is_under_way(self, facts) -> None:
        answer = deterministic_answer(facts, "Can I take COSC 354?")
        assert "NOT eligible" in answer.text
        assert "COSC220 (in progress now), COSC241" in answer.text

    def test_a_student_with_only_in_progress_courses_has_a_record(self) -> None:
        registry.load(get_settings().catalog_dir)
        only_ip = build_facts(registry.get(PROGRAM), _record([("COSC111", "Fall 2026", "IP", 4)]))
        answer = deterministic_answer(only_ip, "What am I taking right now?")
        assert answer.intent is Intent.IN_PROGRESS
        assert "COSC111" in answer.text

    def test_no_in_progress_courses(self) -> None:
        registry.load(get_settings().catalog_dir)
        done = build_facts(registry.get(PROGRAM), _record([("COSC111", "Fall 2025", "A", 4)]))
        answer = deterministic_answer(done, "What am I taking right now?")
        assert "no courses marked as in progress" in answer.text


# ---------------------------------------------------------------------------
# The rephrasing check
# ---------------------------------------------------------------------------


class TestRecordedLiveReplies:
    """Verbatim qwen2.5:7b output from the first live run."""

    def test_progress_percentage_passed_off_as_credits_required(self, facts) -> None:
        reply = (
            "You need 105 more credits to complete your degree in BS Computer Science. "
            "That's 12.5% of the total credits required. The catalog is still partial, so "
            "we can't give a percentage of the degree you've completed yet."
        )
        problems = check(facts, "How many credits am I missing?", reply)
        assert any("12.5%" in p for p in problems)
        assert any("drops credit figures" in p for p in problems), "15 and 120 were dropped"

    def test_dropped_second_prerequisite(self, facts) -> None:
        """Said COSC 352 and 354 need only COSC 220; 354 also needs COSC 241."""
        reply = (
            "- MATH241 Calculus I (4 cr)\n- COSC281 Discrete Structure (3 cr)\n"
            "- COSC349 Computer Networks (3 cr), offered irregularly\n"
            "- MATH242 Calculus II (4 cr)\n- COSC201 Computer Ethics (1 cr)\n"
            "- COSC243 Computer Organization and Architecture (3 cr)\n"
            "Courses like COSC352 and COSC354 are blocked because they require COSC220 "
            "(which you're currently taking). Remember the catalog is still partial."
        )
        problems = check(facts, "What should I take next semester?", reply)
        assert problems == ["drops course codes from the engine's answer: COSC241"]

    def test_correct_remaining_answer_is_accepted(self, facts) -> None:
        """Rejected by an earlier draft because '4 requirement blocks' collided with
        the 4 credits in progress. Matching by context fixed it."""
        reply = (
            "You still need to earn 105 more credits to graduate. Here are the specific "
            "requirements you need to meet:\n\n"
            "- Required Courses for Computer Science Major: You need to complete 10 more "
            "courses totaling 31 credits. The options include COSC220, COSC243, COSC281, "
            "COSC349, COSC351, and more.\n"
            "- Supporting Courses: You need to complete 5 courses totaling 15 credits. The "
            "options are MATH241, MATH242, MATH312, MATH331, and COSC201.\n"
            "- General Education - English Composition: You need to complete 1 more course "
            "for 3 credits. The options are ENGL102 or ENGL112.\n"
            "- University First-Year Seminar: You need to complete 1 more course for 1 "
            "credit. The option is UNIV101.\n\n"
            "Remember, the catalog is still partial, so some requirements might change."
        )
        assert check(facts, "What do I still need to graduate?", reply) == []

    def test_polite_refusal_is_accepted(self, facts) -> None:
        """Mentions COSC 241, which the original guard called invented."""
        question = "Who is the best professor for COSC 241 and what is the parking policy?"
        reply = (
            "I'm sorry, but the facts provided do not include information about specific "
            "professors for COSC 241 or the parking policy at Morgan State University. "
            "You might want to check with the Computer Science Department."
        )
        assert check(facts, question, reply) == []


class TestEachCheck:
    def test_invented_code(self, facts) -> None:
        problems = check(facts, "Can I take COSC 354?", "Take COSC 499 instead. " * 1)
        assert any("COSC499" in p for p in problems)

    def test_missing_prerequisite_named_by_engine_is_not_invented(self, facts) -> None:
        assert "COSC241" not in {code for code, _, _ in facts.completed}
        reply = (
            "No - you're not eligible for COSC354 yet. You still need COSC220, which "
            "you're taking now, and COSC241. The catalog is still partial."
        )
        assert check(facts, "Can I take COSC 354?", reply) == []

    def test_changed_number(self, facts) -> None:
        reply = (
            "You've earned 15 of 120 credits and need 106 more; 12.5% of the way "
            "through. The catalog is partial."
        )
        problems = check(facts, "How many credits am I missing?", reply)
        assert any("106" in p for p in problems)
        assert any("105" in p for p in problems)

    def test_spelled_out_and_list_numbers_are_not_new_figures(self, facts) -> None:
        assert Decimal(4) in numbers("four blocks remain")
        assert numbers("1. first\n2. second") == set()

    def test_blocked_course_called_available(self, facts) -> None:
        reply = (
            "Good news - you can take COSC354 now. You've earned 15 of 120 credits. "
            "The catalog is partial. Prerequisites COSC220, COSC241."
        )
        problems = check(facts, "Can I take COSC 354?", reply)
        assert any("calls a blocked course available: COSC354" in p for p in problems)

    def test_conditional_eligibility_is_not_a_claim(self, facts) -> None:
        reply = (
            "Not yet - once you finish COSC220 and COSC241 you can take COSC354. "
            "The catalog is still partial."
        )
        assert check(facts, "Can I take COSC 354?", reply) == []

    def test_eligible_course_called_blocked(self, facts) -> None:
        code = facts.recommended[0].course.code
        problems = check(
            facts,
            "Tell me about " + code,
            f"You are not eligible for {code}. The catalog is partial.",
        )
        assert any("calls an eligible course blocked" in p for p in problems)

    def test_in_progress_called_completed(self, facts) -> None:
        reply = (
            "You have completed COSC220 Data Structures and Algorithms (4 cr). That is "
            "4 credits in progress and 15 earned credits. It clears COSC352 and COSC354. "
            "The catalog is partial."
        )
        problems = check(facts, "What courses am I taking right now?", reply)
        assert any("calls an in-progress course completed: COSC220" in p for p in problems)

    def test_in_progress_described_correctly(self, facts) -> None:
        reply = (
            "Right now you're taking COSC220 Data Structures and Algorithms - 4 credits "
            "in progress. Until it has a final grade it doesn't count, so you stay at "
            "15 earned credits. Finishing it clears a prerequisite for COSC352 and "
            "COSC354. The catalog is still partial."
        )
        assert check(facts, "What courses am I taking right now?", reply) == []

    def test_dropped_partial_catalog_warning(self, facts) -> None:
        reply = "You've earned 15 of 120 credits, 105 to go, 12.5% of the way through."
        problems = check(facts, "How many credits am I missing?", reply)
        assert problems == ["drops the warning that the encoded catalog is partial"]

    def test_graduation_promise(self, facts) -> None:
        reply = (
            "You've earned 15 of 120 credits, 105 to go, 12.5% of the way through, so "
            "you will graduate on time. The catalog is partial."
        )
        problems = check(facts, "How many credits am I missing?", reply)
        assert any("implies the student can graduate" in p for p in problems)

    def test_refusal_turned_into_an_answer(self, facts) -> None:
        question = "Who is the best professor for COSC 241?"
        problems = check(facts, question, "Dr. Smith is the best choice for COSC 241.")
        assert "answers a question the engine declined to answer" in problems

    def test_dropped_blocker(self, facts) -> None:
        reply = "COSC354 Operating Systems is a 3 credit major course. The catalog is partial."
        problems = check(facts, "Can I take COSC 354?", reply)
        assert any("COSC220" in p and "COSC241" in p for p in problems)

    def test_empty(self, facts) -> None:
        assert check(facts, "How many credits am I missing?", "   ") == ["the rephrasing was empty"]


# ---------------------------------------------------------------------------
# ask(): every failure ends in the engine's answer, never an error
# ---------------------------------------------------------------------------


class TestAskFallbacks:
    QUESTION = "How many credits am I missing?"

    def _ask(self, facts, reply):
        return ask(facts, self.QUESTION, student_id="s1", provider=StubProvider(reply))

    def test_inconsistent_reply_falls_back_and_says_why(self, facts) -> None:
        bad = "You need 105 more credits. That's 12.5% of the total credits required."
        reply = self._ask(facts, bad)
        assert reply.source == "engine"
        assert reply.answer == reply.prepared
        assert "did not match the degree engine" in reply.notice
        assert reply.discarded == bad

    @pytest.mark.parametrize(
        "error",
        [RuntimeError("sdk bug"), AttributeError("'list' object has no attribute 'get'")],
    )
    def test_unexpected_provider_exception_falls_back(self, facts, error) -> None:
        reply = self._ask(facts, error)
        assert reply.source == "engine"
        assert "could not be used" in reply.notice
        assert "105" in reply.answer

    def test_timeout_falls_back(self, facts) -> None:
        from app.llm.chat import ProviderUnavailable

        reply = self._ask(facts, ProviderUnavailable("the model did not respond in time"))
        assert reply.source == "engine"
        assert "did not respond in time" in reply.notice

    @pytest.mark.parametrize("raw", [None, 42, {"text": "hi"}])
    def test_non_text_reply_falls_back(self, facts, raw) -> None:
        reply = self._ask(facts, raw)
        assert reply.source == "engine"
        assert "rephrasing was empty" in reply.notice

    def test_reasoning_block_is_stripped_before_checking(self, facts) -> None:
        raw = (
            "<think>The student has 15 credits, maybe 17 if I count IP...</think>\n"
            "You've earned 15 of the 120 credits you need, leaving 105 - that's 12.5% "
            "of the way through by credit. The catalog is still partial."
        )
        reply = self._ask(facts, raw)
        assert reply.source == "engine+llm", reply.notice
        assert "<think>" not in reply.answer
        assert "17" not in reply.answer

    def test_unterminated_reasoning_block_is_not_an_answer(self) -> None:
        assert _strip_reasoning("<think>still thinking about 17 credits") == ""
