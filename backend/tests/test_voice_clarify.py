"""Spoken course numbers: normalization variants and ask-don't-guess clarification."""

from __future__ import annotations

import pytest

from app.speech.clarify import subjects_in, suggest_clarifications
from app.speech.course_codes import normalize_course_mentions
from app.speech.live import Final, UtteranceEnd
from tests.test_listen_api import ScriptedLive, converse, use, voice_on  # noqa: F401
from tests.test_voice_to_advisor import enrol

CODES = ["COSC459", "COSC243", "MATH459", "MATH141", "ENGL101", "UNIV101", "CLCO261"]


@pytest.mark.parametrize(
    "spoken",
    [
        "computer science four fifty nine",
        "CS four fifty nine",
        "cs four fifty nine",
        "COSC four fifty nine",
        "C O S C four five nine",
        "c. o. s. c. four five nine",
        "C-O-S-C four fifty nine",
        "a C S four fifty nine",
    ],
)
def test_spoken_variants_all_become_the_catalog_code(spoken) -> None:
    assert normalize_course_mentions(spoken, CODES).endswith("COSC 459")


def test_spelled_letters_that_are_not_a_subject_are_untouched() -> None:
    assert normalize_course_mentions("I am a C student", CODES) == "I am a C student"
    assert normalize_course_mentions("A B C four fifty nine", CODES) == "A B C four fifty nine"


def test_unknown_numbers_are_still_not_invented() -> None:
    assert normalize_course_mentions("CS nine ninety nine", CODES) == "CS nine ninety nine"


class TestSuggestions:
    def test_single_candidate_is_a_question_not_a_rewrite(self) -> None:
        [c] = suggest_clarifications("what about two forty three", CODES)
        assert c.spoken == "two forty three"
        assert c.candidates == ("COSC 243",)
        assert c.question == "Did you mean COSC 243?"

    def test_multiple_matches_ask_and_context_only_reorders(self) -> None:
        plain = suggest_clarifications("before four fifty nine", CODES)[0]
        assert plain.candidates == ("COSC 459", "MATH 459")
        assert plain.question == "Did you mean COSC 459 or MATH 459?"
        math_first = suggest_clarifications("before four fifty nine", CODES, ["MATH"])[0]
        assert math_first.candidates == ("MATH 459", "COSC 459")

    def test_number_nothing_uses_gets_no_suggestion(self) -> None:
        assert suggest_clarifications("before nine ninety nine", CODES) == []
        assert suggest_clarifications("before 999", CODES) == []

    def test_a_named_subject_is_not_a_bare_number(self) -> None:
        assert suggest_clarifications("COSC 459", CODES) == []
        assert suggest_clarifications("cosc 999 and computer science nine ninety nine", CODES) == []

    def test_quantities_are_not_courses(self) -> None:
        assert suggest_clarifications("I need 101 more credits", CODES) == []
        assert suggest_clarifications("about one oh one credits", CODES) == []

    def test_several_numbers_each_get_their_own_question(self) -> None:
        found = suggest_clarifications("two forty three and four fifty nine", CODES)
        assert [c.spoken for c in found] == ["two forty three", "four fifty nine"]

    def test_text_is_never_modified(self) -> None:
        text = "before four fifty nine"
        suggest_clarifications(text, CODES)
        assert text == "before four fifty nine"


def test_subjects_in_context() -> None:
    # "need 101" is not a course reference; only catalog subjects count.
    assert subjects_in("we covered cosc 220, MATH141 and need 101", CODES) == ["COSC", "MATH"]
    assert subjects_in("MATH141 then xyz 220", CODES) == ["MATH"]


@pytest.mark.usefixtures("voice_on")
class TestThroughTheSocket:
    def hear(self, client, monkeypatch, phrase: str) -> dict:
        use(monkeypatch, ScriptedLive([Final(phrase, 0.97), UtteranceEnd()]))
        return converse(client, after_ready=[b"\x00" * 64])[-1]

    def test_done_carries_a_question_and_leaves_the_text_alone(self, client, monkeypatch) -> None:
        enrol(client)
        done = self.hear(client, monkeypatch, "what do I need before four fifty nine")
        assert done["text"] == "what do I need before four fifty nine"
        [question] = done["clarifications"]
        assert question["candidates"] == ["COSC 459"]  # the Morgan catalog has only COSC 459
        assert question["question"] == "Did you mean COSC 459?"

    def test_no_bare_number_means_no_key(self, client, monkeypatch) -> None:
        enrol(client)
        done = self.hear(client, monkeypatch, "Can I take computer science three fifty four?")
        assert "clarifications" not in done
        assert done["text"] == "Can I take COSC 354?"

    def test_a_catalog_miss_is_not_suggested(self, client, monkeypatch) -> None:
        enrol(client)
        done = self.hear(client, monkeypatch, "what about nine ninety nine")
        assert "clarifications" not in done
