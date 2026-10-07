"""Spoken course codes in a transcript become catalog codes - and only catalog codes.

The first three cases are real Deepgram output (Sep 22 2026) for questions read
aloud. Deepgram keeps spoken numbers as words, and its `numerals` option splits
them ("2 43"), so this conversion is ours.
"""

from __future__ import annotations

import pytest

from app.speech.course_codes import normalize_course_mentions

CODES = [
    "UNIV101",
    "COSC111",
    "COSC241",
    "COSC243",
    "COSC490",
    "MATH141",
    "MATH241",
    "ENGL101",
    "CLCO261",
]


def norm(text: str) -> str:
    return normalize_course_mentions(text, CODES)


class TestRealDeepgramOutput:
    def test_subject_names_and_spoken_numbers(self) -> None:
        assert (
            norm("Can I take University one zero one or Computer Science two forty three?")
            == "Can I take UNIV 101 or COSC 243?"
        )

    def test_subject_codes_with_spoken_numbers(self) -> None:
        assert (
            norm("What do I need before COSC two forty one and MATH one forty one?")
            == "What do I need before COSC 241 and MATH 141?"
        )

    def test_already_correct_text_is_unchanged(self) -> None:
        assert norm("Is COSC 490 a senior project?") == "Is COSC 490 a senior project?"

    def test_numerals_split_by_deepgram_are_joined(self) -> None:
        assert norm("Computer Science 2 43 and University 1 0 1") == "COSC 243 and UNIV 101"


class TestSpokenNumberForms:
    @pytest.mark.parametrize(
        "spoken",
        [
            "two forty three",
            "two four three",
            "two-forty-three",
            "two forty-three",
            "two hundred forty three",
            "two hundred and forty three",
            "243",
        ],
    )
    def test_ways_to_say_243(self, spoken: str) -> None:
        assert norm(f"computer science {spoken}") == "COSC 243"

    @pytest.mark.parametrize(
        "spoken", ["one zero one", "one oh one", "one o one", "one hundred one", "1 0 1"]
    )
    def test_ways_to_say_101(self, spoken: str) -> None:
        assert norm(f"university {spoken}") == "UNIV 101"

    def test_tens_with_no_units(self) -> None:
        assert norm("computer science four ninety") == "COSC 490"

    def test_teens(self) -> None:
        assert normalize_course_mentions("english one fifteen", ["ENGL115"]) == "ENGL 115"


class TestSubjects:
    def test_lowercase_code_as_spoken(self) -> None:
        assert norm("cosc two forty one") == "COSC 241"

    def test_multi_word_alias(self) -> None:
        assert norm("cloud computing two sixty one") == "CLCO 261"

    def test_mathematics_and_math(self) -> None:
        assert norm("mathematics two forty one or math one forty one") == "MATH 241 or MATH 141"


class TestNeverInvents:
    def test_code_not_in_catalog_is_left_alone(self) -> None:
        text = "Can I take computer science nine ninety nine?"
        assert norm(text) == text

    def test_subject_not_in_catalog_is_left_alone(self) -> None:
        text = "What about chemistry one oh one?"
        assert norm(text) == text

    @pytest.mark.parametrize(
        "text",
        [
            "Which university should I transfer to?",
            "I took one history class and two math classes.",
            "Is english one of my requirements?",
            "Math 2 classes left?",
            "What should I take next semester?",
            "",
        ],
    )
    def test_ordinary_sentences_are_unchanged(self, text: str) -> None:
        assert norm(text) == text

    def test_punctuation_after_the_number_is_kept(self) -> None:
        assert norm("After computer science two forty one, then what?") == (
            "After COSC 241, then what?"
        )
