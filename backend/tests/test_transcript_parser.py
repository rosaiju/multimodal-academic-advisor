"""Deterministic transcript parsing.

Two properties matter more than parsing accuracy itself:

* everything this produces is UNVERIFIED_EXTRACTION and cannot reach an audit, and
* the parser never repairs what it is unsure of - it flags and moves on.

A silently "corrected" course code puts a course on a student's record that they
never took, which is worse than an unrecognised line they can fix themselves.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from app.catalog.loader import load_program
from app.catalog.schema import Program
from app.ingestion.models import Confidence
from app.ingestion.parser import normalise_code, parse_transcript_text
from app.schemas.provenance import TRUSTED_FOR_AUDIT, Provenance

MORGAN = Path(__file__).resolve().parents[2] / "data" / "catalog" / "morgan_cosc_bs_2026_2028.yaml"

TRANSCRIPT = """\
MORGAN STATE UNIVERSITY - OFFICIAL TRANSCRIPT
Student: Jane Doe

Fall 2024
COSC 111  Introduction to Computer Science I     4.00  A
ENGL 101  Composition I                          3.00  B
MATH 241  Calculus I                             4.00  C
Term GPA: 3.10   Cumulative GPA: 3.10

Spring 2025
COSC 112  Introduction to Computer Science II    4.00  A
ENGL 102  Composition II                         3.00  A
Term GPA: 4.00
"""


@pytest.fixture(scope="module")
def morgan() -> Program:
    return load_program(MORGAN)


class TestNothingIsTrusted:
    def test_every_row_is_an_unverified_extraction(self, morgan) -> None:
        result = parse_transcript_text(TRANSCRIPT, program=morgan)
        assert result.courses
        for course in result.courses:
            assert course.provenance is Provenance.UNVERIFIED_EXTRACTION

    def test_extraction_provenance_is_not_audit_trusted(self) -> None:
        """The guarantee the whole layer rests on."""
        assert Provenance.UNVERIFIED_EXTRACTION not in TRUSTED_FOR_AUDIT

    def test_summary_says_nothing_counts_yet(self, morgan) -> None:
        result = parse_transcript_text(TRANSCRIPT, program=morgan)
        assert "until you confirm it" in result.summary()


class TestParsing:
    def test_finds_every_course(self, morgan) -> None:
        result = parse_transcript_text(TRANSCRIPT, program=morgan)
        assert [c.code for c in result.courses] == [
            "COSC111",
            "ENGL101",
            "MATH241",
            "COSC112",
            "ENGL102",
        ]

    def test_assigns_the_term_from_the_preceding_heading(self, morgan) -> None:
        result = parse_transcript_text(TRANSCRIPT, program=morgan)
        by_code = {c.code: c for c in result.courses}
        assert by_code["COSC111"].term == "Fall 2024"
        assert by_code["COSC112"].term == "Spring 2025"

    def test_reads_grades_and_credits(self, morgan) -> None:
        result = parse_transcript_text(TRANSCRIPT, program=morgan)
        cosc111 = next(c for c in result.courses if c.code == "COSC111")
        assert cosc111.grade == "A"
        assert cosc111.credits == Decimal("4.00")
        assert cosc111.title == "Introduction to Computer Science I"

    def test_keeps_the_raw_line_for_a_human_to_check(self, morgan) -> None:
        result = parse_transcript_text(TRANSCRIPT, program=morgan)
        cosc111 = next(c for c in result.courses if c.code == "COSC111")
        assert "COSC 111" in cosc111.raw_line
        assert cosc111.line_number == 5

    def test_ignores_gpa_and_total_lines(self, morgan) -> None:
        """'Term GPA: 3.10' must not become a course."""
        result = parse_transcript_text(TRANSCRIPT, program=morgan)
        assert all(c.code.isalnum() and not c.code.startswith("TERM") for c in result.courses)
        assert len(result.courses) == 5

    def test_normalises_codes_to_catalog_form(self) -> None:
        assert normalise_code("cosc", "111") == "COSC111"
        assert normalise_code(" CoSc ", " 111 ") == "COSC111"

    def test_handles_hyphenated_and_tight_codes(self, morgan) -> None:
        text = "Fall 2024\nCOSC-111  Intro  4.00  A\nENGL 102  Comp II  3.00  B\n"
        result = parse_transcript_text(text, program=morgan)
        assert {c.code for c in result.courses} == {"COSC111", "ENGL102"}


class TestConfidence:
    def test_clean_known_course_is_high(self, morgan) -> None:
        result = parse_transcript_text(TRANSCRIPT, program=morgan)
        assert all(c.confidence is Confidence.HIGH for c in result.courses)
        assert result.needs_review == []

    def test_unknown_course_is_flagged_not_corrected(self, morgan) -> None:
        """BIOL205 is not in the Morgan CS catalog.

        The parser must NOT rewrite it to a similar catalog code. A wrong
        correction puts a course on a student's record they never took.
        """
        text = "Fall 2024\nBIOL 205  Some Transfer Course  3.00  B\n"
        result = parse_transcript_text(text, program=morgan)
        row = result.courses[0]
        assert row.code == "BIOL205"
        assert row.confidence is Confidence.MEDIUM
        assert any("not in the catalog" in i for i in row.issues)

    def test_no_catalog_means_no_catalog_claims(self, morgan) -> None:
        """Without a program, the parser cannot and does not comment on codes."""
        text = "Fall 2024\nBIOL 205  Some Course  3.00  B\n"
        result = parse_transcript_text(text)
        assert result.courses[0].confidence is Confidence.HIGH
        assert result.courses[0].issues == []

    def test_missing_term_downgrades_and_explains(self, morgan) -> None:
        text = "COSC 111  Intro to CS I  4.00  A\n"
        result = parse_transcript_text(text, program=morgan)
        row = result.courses[0]
        assert row.term is None
        assert row.confidence is Confidence.MEDIUM
        assert any("which semester" in i for i in row.issues)

    def test_unusual_credit_value_is_flagged(self, morgan) -> None:
        text = "Fall 2024\nCOSC 111  Intro  99.00  A\n"
        result = parse_transcript_text(text, program=morgan)
        assert result.courses[0].confidence is Confidence.MEDIUM
        assert any("unusual credit" in i for i in result.courses[0].issues)

    def test_issues_are_written_for_a_student_not_a_developer(self, morgan) -> None:
        text = "BIOL 205  Some Course  3.00  B\n"
        row = parse_transcript_text(text, program=morgan).courses[0]
        assert row.issues
        for issue in row.issues:
            assert issue == issue.lower() or issue[0].isupper() or issue[0].isalnum()
            assert "None" not in issue and "Traceback" not in issue


class TestWarnings:
    def test_empty_document_warns_about_scans(self) -> None:
        result = parse_transcript_text("This is a picture of a transcript.\n")
        assert result.courses == []
        assert any("scanned or image-based" in w for w in result.warnings)

    def test_no_terms_anywhere_is_a_document_level_warning(self, morgan) -> None:
        text = "COSC 111  Intro  4.00  A\nCOSC 112  Intro II  4.00  B\n"
        result = parse_transcript_text(text, program=morgan)
        assert any("No term headings" in w for w in result.warnings)

    def test_retakes_are_reported_not_removed(self, morgan) -> None:
        text = "Fall 2024\nCOSC 111  Intro  4.00  D\nSpring 2025\nCOSC 111  Intro  4.00  A\n"
        result = parse_transcript_text(text, program=morgan)
        assert len(result.courses) == 2, "both attempts stay; the engine decides"
        assert any("Repeated course code" in w for w in result.warnings)


class TestResultHelpers:
    def test_needs_review_lists_only_non_high_rows(self, morgan) -> None:
        text = "Fall 2024\nCOSC 111  Intro  4.00  A\nBIOL 205  Transfer  3.00  B\n"
        result = parse_transcript_text(text, program=morgan)
        assert [c.code for c in result.needs_review] == ["BIOL205"]

    def test_confirmable_requires_every_audit_field(self, morgan) -> None:
        text = "COSC 111  Intro  4.00  A\n"  # no term
        result = parse_transcript_text(text, program=morgan)
        assert result.courses[0].is_complete is False
        assert result.confirmable == []

    def test_extractor_is_recorded_for_the_audit_trail(self, morgan) -> None:
        result = parse_transcript_text(TRANSCRIPT, source_name="jane.txt", program=morgan)
        assert result.extractor == "text-parser"
        assert result.source_name == "jane.txt"
