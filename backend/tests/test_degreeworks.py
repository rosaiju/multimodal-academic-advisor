"""Parsing DegreeWorks audit worksheets.

All fixtures below are SYNTHETIC - invented courses and grades in the DegreeWorks
layout. No real student record appears in this repository.

The bug these cover: a DegreeWorks audit put through the transcript parser yielded
zero courses, because an audit groups courses under REQUIREMENT headings with the
term at the end of each row, while a transcript groups them under TERM headings
with the grade last.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from app.catalog.loader import load_program
from app.catalog.schema import Program
from app.ingestion.degreeworks import looks_like_degreeworks, parse_degreeworks_text
from app.ingestion.errors import DocumentNotReadable
from app.ingestion.models import Confidence
from app.ingestion.parser import parse_transcript_text
from app.ingestion.pdf import PdfTranscriptExtractor, parse_document_text
from app.schemas.provenance import Provenance

MORGAN = Path(__file__).resolve().parents[2] / "data" / "catalog" / "morgan_cosc_bs_2026_2028.yaml"

#: A DegreeWorks worksheet in the real layout, with invented coursework.
WORKSHEET = """\
Morgan State University
Degree Works Audit  Audit date 09/19/2026

Program Requirements
Freshman Composition, with C or better  ENGL 101 FRESHMAN COMPOSITION I (EC)  A    3    FALL 2024
Freshman Composition II, with C or better  Still needed: 1 Class in ENGL 102 or 112
Introduction to Computer Science I  COSC 111 INTRO COMPUTER SCIENCE I (IM)  B    4    FALL 2024
Mathematics and Quantitative Reasoning (MQ)  MATH 241 CALCULUS I  TRB  3.5  SPRING 2023
Satisfied by: MAT201 - CALCULUS ONE - EXAMPLE COMMUNITY COLLEGE
Arts and Humanities Requirement (AH)  ART 101 SURVEY OF ART (AH)  TRA  3    FALL 2023
Satisfied by: ART100 - ART APPRECIATION - EXAMPLE COMMUNITY COLLEGE
Critical Thinking (CT)  PHIL 109 CRITICAL THINKING (CT)  IP   (3)  SPRING 2026
Data Structures  COSC 220 DATA STRUCTURES  IP   (4)  SPRING 2026
Freshman Orientation  ORTR 101 TRANSFER OF 24 CREDITS  TR   0    FALL 2023

Elective Requirements
Still needed: 1 Class in COSC 349 or 351
General Elective  COSC 111 INTRO COMPUTER SCIENCE I (IM)  B    4    FALL 2024
"""


@pytest.fixture(scope="module")
def morgan() -> Program:
    return load_program(MORGAN)


@pytest.fixture
def parsed(morgan):
    return parse_degreeworks_text(WORKSHEET, source_name="audit.pdf", program=morgan)


def by_code(result):
    return {c.code: c for c in result.courses}


class TestTheOriginalBug:
    """A DegreeWorks audit used to parse to zero courses."""

    def test_transcript_parser_finds_nothing_here(self) -> None:
        """Confirms the cause: the layout genuinely does not match a transcript."""
        assert parse_transcript_text(WORKSHEET).courses == []

    def test_degreeworks_parser_finds_the_coursework(self, parsed) -> None:
        assert len(parsed.courses) == 7

    def test_routing_picks_the_right_parser(self, morgan) -> None:
        result = parse_document_text(WORKSHEET, source_name="audit.pdf", program=morgan)
        assert result.extractor == "degreeworks-parser"
        assert result.courses

    def test_a_plain_transcript_still_routes_to_the_transcript_parser(self, morgan) -> None:
        transcript = "Fall 2024\nCOSC 111  Intro to CS I  4.00  A\n"
        result = parse_document_text(transcript, source_name="t.txt", program=morgan)
        assert result.extractor == "text-parser"
        assert len(result.courses) == 1


class TestDetection:
    def test_recognises_worksheet_markers(self) -> None:
        assert looks_like_degreeworks(WORKSHEET)

    def test_recognises_by_row_shape_without_markers(self) -> None:
        rows = "\n".join(
            [
                "Requirement A  ENGL 101 COMPOSITION  A  3  FALL 2024",
                "Requirement B  MATH 241 CALCULUS I   B  4  FALL 2024",
                "Requirement C  COSC 111 INTRO CS     A  4  FALL 2024",
            ]
        )
        assert looks_like_degreeworks(rows)

    def test_does_not_claim_a_transcript(self) -> None:
        assert not looks_like_degreeworks("Fall 2024\nCOSC 111  Intro  4.00  A\n")


class TestFieldExtraction:
    def test_reads_code_title_grade_credits_and_term(self, parsed) -> None:
        engl = by_code(parsed)["ENGL101"]
        assert engl.grade == "A"
        assert engl.credits == Decimal(3)
        assert engl.term == "Fall 2024"
        assert engl.title and "FRESHMAN COMPOSITION" in engl.title

    def test_term_comes_from_the_row_not_a_heading(self, parsed) -> None:
        """Every row carries its own term in this format."""
        codes = by_code(parsed)
        assert codes["ENGL101"].term == "Fall 2024"
        assert codes["MATH241"].term == "Spring 2023"
        assert codes["PHIL109"].term == "Spring 2026"

    def test_strips_the_gen_ed_tag_from_the_title(self, parsed) -> None:
        assert not by_code(parsed)["ENGL101"].title.endswith("(EC)")

    def test_handles_fractional_credits(self, parsed) -> None:
        assert by_code(parsed)["MATH241"].credits == Decimal("3.5")


class TestTransferCredit:
    def test_equated_letter_grade_is_used(self, parsed) -> None:
        """TRB means transfer credit equated to a B."""
        math = by_code(parsed)["MATH241"]
        assert math.grade == "B"
        assert any("transfer credit" in i for i in math.issues)

    def test_captures_the_sending_institution(self, parsed) -> None:
        """Residency needs this, and the Satisfied-by line is where it lives."""
        assert by_code(parsed)["MATH241"].institution == "EXAMPLE COMMUNITY COLLEGE"
        assert by_code(parsed)["ART101"].institution == "EXAMPLE COMMUNITY COLLEGE"

    def test_bare_transfer_with_no_grade_is_flagged(self, parsed) -> None:
        block = by_code(parsed)["ORTR101"]
        assert block.grade is None
        assert block.confidence is Confidence.LOW
        assert any("no equated letter grade" in i for i in block.issues)

    def test_zero_credit_entry_is_flagged(self, parsed) -> None:
        assert any("0 credits" in i for i in by_code(parsed)["ORTR101"].issues)

    def test_warns_about_transfer_rows(self, parsed) -> None:
        assert any("transfer credit" in w for w in parsed.warnings)


class TestInProgress:
    def test_in_progress_keeps_its_marker(self, parsed) -> None:
        assert by_code(parsed)["PHIL109"].grade == "IP"

    def test_in_progress_is_flagged_as_incomplete(self, parsed) -> None:
        phil = by_code(parsed)["PHIL109"]
        assert any("in progress" in i for i in phil.issues)
        assert any("attempted, not yet earned" in i for i in phil.issues)

    def test_in_progress_cannot_satisfy_a_requirement_even_if_confirmed(self, morgan) -> None:
        """IP is non-passing in the schema, so the engine ignores it regardless."""
        from app.audit.engine import run_audit
        from app.audit.record import CompletedCourse, StudentRecord

        rec = StudentRecord(
            student_id="s",
            completed=[
                CompletedCourse(
                    code="COSC220",
                    term="Spring 2026",
                    grade="IP",
                    credits=Decimal(4),
                    provenance=Provenance.STUDENT_CONFIRMED,
                )
            ],
        )
        audit = run_audit(morgan, rec)
        assert audit.total_credits_applied == Decimal(0)

    def test_warns_about_in_progress_rows(self, parsed) -> None:
        assert any("still in progress" in w for w in parsed.warnings)


class TestExclusions:
    def test_still_needed_lines_are_not_courses(self, parsed) -> None:
        """'Still needed: 1 Class in ENGL 102 or 112' is a requirement."""
        codes = set(by_code(parsed))
        assert "ENGL102" not in codes
        assert "ENGL112" not in codes
        assert "COSC349" not in codes

    def test_satisfied_by_source_courses_are_not_added(self, parsed) -> None:
        """The transfer SOURCE course is not a Morgan course the student took."""
        codes = set(by_code(parsed))
        assert "MAT201" not in codes
        assert "ART100" not in codes


class TestRepeatedRows:
    def test_a_course_listed_twice_is_counted_once(self, parsed) -> None:
        """DegreeWorks shows a course under every requirement it satisfies."""
        assert [c.code for c in parsed.courses].count("COSC111") == 1

    def test_repeats_are_reported_not_hidden(self, parsed) -> None:
        assert any("repeated a course" in w for w in parsed.warnings)
        assert any("COSC111" in w for w in parsed.warnings)


class TestNothingIsAutoCompleted:
    def test_every_row_is_unverified(self, parsed) -> None:
        """The review-and-confirm workflow is unchanged by this format."""
        assert all(c.provenance is Provenance.UNVERIFIED_EXTRACTION for c in parsed.courses)

    def test_rows_needing_attention_are_not_high_confidence(self, parsed) -> None:
        for course in parsed.courses:
            if course.issues:
                assert course.confidence is not Confidence.HIGH

    def test_incomplete_rows_are_not_confirmable(self, parsed) -> None:
        assert by_code(parsed)["ORTR101"] not in parsed.confirmable


class TestUnreadableDocuments:
    def test_pdf_with_text_but_no_courses_raises_instead_of_reporting_zero(self) -> None:
        """'0 courses found' tells a student their transcript is empty. It is not."""
        from tests.test_pdf_extractor import make_pdf

        prose = ["This document contains no course rows at all." for _ in range(20)]
        with pytest.raises(DocumentNotReadable, match="no course rows in a layout we recognise"):
            PdfTranscriptExtractor().extract(make_pdf(prose), filename="prose.pdf")

    def test_the_error_names_the_file_and_what_was_found(self) -> None:
        from tests.test_pdf_extractor import make_pdf

        prose = ["Nothing resembling coursework appears here." for _ in range(20)]
        try:
            PdfTranscriptExtractor().extract(make_pdf(prose), filename="mystery.pdf")
        except DocumentNotReadable as exc:
            assert "mystery.pdf" in str(exc)
            assert "characters of text" in str(exc)
        else:  # pragma: no cover
            pytest.fail("expected DocumentNotReadable")


class TestEndToEndThroughPdf:
    def test_a_degreeworks_pdf_parses(self, morgan) -> None:
        from tests.test_pdf_extractor import make_pdf

        pdf = make_pdf(WORKSHEET.splitlines(), font_size=7)
        result = PdfTranscriptExtractor().extract(pdf, filename="audit.pdf", program=morgan)
        assert result.extractor == "degreeworks-parser"
        assert len(result.courses) == 7
        assert all(c.provenance is Provenance.UNVERIFIED_EXTRACTION for c in result.courses)
