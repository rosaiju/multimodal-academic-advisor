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


class TestWarningCountsMatchTheList:
    """Regression: warnings counted rows SCANNED, the table showed rows KEPT.

    A duplicated in-progress course was counted twice in the warning but appears
    once in the table, so the two disagreed. A warning that contradicts the list
    beside it makes a student doubt both numbers.
    """

    WORKSHEET_WITH_REPEATS = """\
Degree Works Audit
Requirement One    PHIL 109 CRITICAL THINKING   IP   (3)  SPRING 2026
Requirement Two    PHIL 109 CRITICAL THINKING   IP   (3)  SPRING 2026
Requirement Three  MATH 241 CALCULUS I          TRB  3.5  FALL 2023
Requirement Four   MATH 241 CALCULUS I          TRB  3.5  FALL 2023
Requirement Five   ENGL 101 COMPOSITION I       A    3    FALL 2024
"""

    def _parsed(self):
        return parse_degreeworks_text(self.WORKSHEET_WITH_REPEATS, source_name="a.pdf")

    def test_in_progress_warning_counts_kept_rows_only(self) -> None:
        result = self._parsed()
        actual = sum(1 for c in result.courses if c.grade == "IP")
        assert actual == 1
        assert any(f"{actual} course(s) are still in progress" in w for w in result.warnings)

    def test_transfer_warning_counts_kept_rows_only(self) -> None:
        result = self._parsed()
        actual = sum(1 for c in result.courses if c.transfer)
        assert actual == 1
        assert any(f"{actual} course(s) came from transfer" in w for w in result.warnings)

    def test_course_count_matches_distinct_codes(self) -> None:
        result = self._parsed()
        assert len(result.courses) == 3
        assert len({c.code for c in result.courses}) == 3

    def test_duplicate_warning_counts_dropped_rows(self) -> None:
        result = self._parsed()
        assert any("2 row(s) repeated" in w for w in result.warnings)

    def test_transfer_flag_is_structural_not_text_matched(self) -> None:
        """`transfer` is set by the parser, so counting never depends on wording."""
        result = self._parsed()
        math = next(c for c in result.courses if c.code == "MATH241")
        engl = next(c for c in result.courses if c.code == "ENGL101")
        assert math.transfer is True
        assert engl.transfer is False


class TestDetectionRequiresRowEvidence:
    """Regression: a document merely MENTIONING DegreeWorks was routed here.

    A CV describing this very project contains the word, which sent the whole
    document to the audit parser and then failed it as unreadable - a confusing
    way to tell someone they uploaded the wrong file.
    """

    def test_keyword_alone_is_not_enough(self) -> None:
        cv = (
            "Rohan Example — Curriculum Vitae\n"
            "Projects\n"
            "Built a multimodal academic advisor that parses DegreeWorks audits.\n"
            "Skills: Python, FastAPI, React\n"
        )
        assert not looks_like_degreeworks(cv)

    def test_empty_document_is_not_degreeworks(self) -> None:
        assert not looks_like_degreeworks("")

    def test_one_row_plus_a_marker_is_enough(self) -> None:
        text = "Degree Works Audit\nRequirement  ENGL 101 COMPOSITION  A  3  FALL 2024\n"
        assert looks_like_degreeworks(text)

    def test_three_rows_alone_is_enough(self) -> None:
        text = "\n".join(
            [
                "Req A  ENGL 101 COMPOSITION  A  3  FALL 2024",
                "Req B  MATH 241 CALCULUS I   B  4  FALL 2024",
                "Req C  COSC 111 INTRO CS     A  4  FALL 2024",
            ]
        )
        assert looks_like_degreeworks(text)

    def test_a_cv_mentioning_degreeworks_routes_to_the_transcript_parser(self) -> None:
        cv = "I have used DegreeWorks and Banner extensively.\n"
        result = parse_document_text(cv, source_name="cv.txt")
        assert result.extractor == "text-parser"


class TestBlockTransferBuckets:
    """Regression: every lower-level transfer row was dropped, then re-collapsed.

    An audit parks transfer credit that equates to no single Morgan course in a
    BUCKET - "COSC 116TR COSC LWR LVL ELECTIVE". Two separate bugs hid them:

    * `_ROW` required a bare three-digit number, so the TR suffix matched nothing
      and the rows were never seen at all.
    * Keying the result on the course code alone then treated a dozen distinct
      transfer courses as one repeated course and kept only the first.

    Together those silently deleted most of a transfer student's credit. All
    coursework below is invented.
    """

    BUCKETS = """\
Degree Works Audit
Free Electives
Course Title Grade Credits Term Repeated
COSC 116TR COSC LWR LVL ELECTIVE  TRA  0.5  FALL 2021
Satisfied by: XXX006 - INTRO TO INFO TECH (PRACTICAL) - EXAMPLE EVALUATION SERVICE
COSC 116TR COSC LWR LVL ELECTIVE  TRB  0.5  FALL 2021
Satisfied by: XXX007 - C PROGRAMMING (PRACTICAL) - EXAMPLE EVALUATION SERVICE
COSC 116TR COSC LWR LVL ELECTIVE  TRC  3    SPRING 2022
Satisfied by: XXX020 - NUMERICAL METHOD (THEORY) - EXAMPLE EVALUATION SERVICE
MATH 116TR MATH LWR LVL ELECTIVE  TRB  3    SPRING 2022
Satisfied by: XXX022 - STATISTICS II (THEORY) - EXAMPLE EVALUATION SERVICE
"""

    @pytest.fixture
    def parsed(self, morgan):
        return parse_degreeworks_text(self.BUCKETS, source_name="audit.pdf", program=morgan)

    def test_the_tr_suffix_no_longer_hides_the_row(self, parsed) -> None:
        assert {c.code for c in parsed.courses} == {"COSC116TR", "MATH116TR"}

    def test_every_bucket_row_survives(self, parsed) -> None:
        """Four rows in, four rows out - not two, one per distinct code."""
        assert len(parsed.courses) == 4

    def test_no_credit_is_lost(self, parsed) -> None:
        assert sum(c.credits for c in parsed.courses) == Decimal("7.0")

    def test_rows_sharing_a_code_keep_their_own_fields(self, parsed) -> None:
        cosc = [c for c in parsed.courses if c.code == "COSC116TR"]
        assert len(cosc) == 3
        assert {c.grade for c in cosc} == {"A", "B", "C"}
        assert {str(c.credits) for c in cosc} == {"0.5", "3"}

    def test_nothing_is_reported_as_a_repeat(self, parsed) -> None:
        """These are distinct courses. Calling them repeats is the bug."""
        assert not any("repeated a course" in w for w in parsed.warnings)

    def test_a_bucket_is_explained_not_called_a_bad_code(self, parsed) -> None:
        """'COSC116TR is not in the catalog' invites a student to correct it."""
        bucket = next(c for c in parsed.courses if c.code == "COSC116TR")
        assert any("block transfer credit" in i for i in bucket.issues)
        assert not any("not in the catalog" in i for i in bucket.issues)

    def test_buckets_are_never_high_confidence(self, parsed) -> None:
        assert all(c.confidence is not Confidence.HIGH for c in parsed.courses)

    def test_an_identical_row_is_still_a_repeat(self, morgan) -> None:
        """Widening the key must not stop collapsing genuine duplicates."""
        text = (
            "Degree Works Audit\n"
            "Req One  COSC 116TR COSC LWR LVL ELECTIVE  TRA  0.5  FALL 2021\n"
            "Satisfied by: XXX006 - INTRO TO INFO TECH - EXAMPLE EVALUATION SERVICE\n"
            "Req Two  COSC 116TR COSC LWR LVL ELECTIVE  TRA  0.5  FALL 2021\n"
            "Satisfied by: XXX006 - INTRO TO INFO TECH - EXAMPLE EVALUATION SERVICE\n"
        )
        result = parse_degreeworks_text(text, source_name="a.pdf", program=morgan)
        assert len(result.courses) == 1
        assert any("repeated a course" in w for w in result.warnings)

    def test_same_code_and_term_but_a_different_source_is_two_courses(self, morgan) -> None:
        """Two practicals taken in one term differ ONLY by their sending course."""
        text = (
            "Degree Works Audit\n"
            "COSC 116TR COSC LWR LVL ELECTIVE  TRB  0.5  SUMMER 2022\n"
            "Satisfied by: XXX015 - DISCRETE STRUCTURE - EXAMPLE EVALUATION SERVICE\n"
            "COSC 116TR COSC LWR LVL ELECTIVE  TRB  0.5  SUMMER 2022\n"
            "Satisfied by: XXX016 - OBJECT ORIENTED PROG - EXAMPLE EVALUATION SERVICE\n"
        )
        result = parse_degreeworks_text(text, source_name="a.pdf", program=morgan)
        assert len(result.courses) == 2
        assert sum(c.credits for c in result.courses) == Decimal("1.0")

    def test_an_ordinary_course_does_not_acquire_a_suffix(self, morgan) -> None:
        """The suffix is optional; a normal code must not pick one up."""
        result = parse_degreeworks_text(WORKSHEET, source_name="a.pdf", program=morgan)
        assert {c.code for c in result.courses} == {
            "ENGL101",
            "COSC111",
            "MATH241",
            "ART101",
            "PHIL109",
            "COSC220",
            "ORTR101",
        }


class TestWinterMiniMester:
    """Regression: a wrapped 'WINTER MINI-MESTER' term lost the whole row.

    The year is what anchors the row regex, and DegreeWorks wraps it onto the
    following line when the term name is long. The row matched nothing and vanished.
    """

    WRAPPED = """\
Degree Works Audit
Free Electives
COSC 116TR COSC LWR LVL ELECTIVE  TRB  0.5  WINTER MINI-MESTER
2023
Satisfied by: XXX026 - COMPUTER ARCH (PRACTICAL) - EXAMPLE EVALUATION SERVICE
MATH 241 CALCULUS I  A  4  SPRING 2024
"""

    @pytest.fixture
    def parsed(self, morgan):
        return parse_degreeworks_text(self.WRAPPED, source_name="audit.pdf", program=morgan)

    def test_the_row_is_found_at_all(self, parsed) -> None:
        assert "COSC116TR" in {c.code for c in parsed.courses}

    def test_the_wrapped_year_is_reattached(self, parsed) -> None:
        bucket = next(c for c in parsed.courses if c.code == "COSC116TR")
        assert bucket.term == "Winter Mini-Mester 2023"

    def test_the_stray_year_is_not_a_row_of_its_own(self, parsed) -> None:
        assert len(parsed.courses) == 2

    def test_line_number_points_at_the_row_not_the_year(self, parsed) -> None:
        """A student checking the source needs the line the row starts on."""
        bucket = next(c for c in parsed.courses if c.code == "COSC116TR")
        source = self.WRAPPED.splitlines()[bucket.line_number - 1]
        assert source.startswith("COSC 116TR")

    def test_an_unwrapped_term_still_reads(self, parsed) -> None:
        assert next(c for c in parsed.courses if c.code == "MATH241").term == "Spring 2024"

    def test_mini_mester_on_one_line_also_works(self, morgan) -> None:
        text = (
            "Degree Works Audit\n"
            "Req  COSC 116TR COSC LWR LVL ELECTIVE  TRB  0.5  WINTER MINI-MESTER 2023\n"
            "Req  MATH 241 CALCULUS I  A  4  SPRING 2024\n"
            "Req  ENGL 101 COMPOSITION  A  3  FALL 2024\n"
        )
        result = parse_degreeworks_text(text, source_name="a.pdf", program=morgan)
        bucket = next(c for c in result.courses if c.code == "COSC116TR")
        assert bucket.term == "Winter Mini-Mester 2023"


class TestWrappedSatisfiedBy:
    """Regression: most transfer rows came back with no sending institution.

    A narrow PDF column strands the label on a line of its own, with its value
    split above and below it. The old reader looked at exactly the next line and
    required text after the colon, so it found neither half.
    """

    WRAPPED = """\
Degree Works Audit
Social and Behavioral Sciences  ECON 211 PRIN OF ECONOMICS I (SB)  TRA  3  FALL 2024
ECO201 - THE AMERICAN ECONOMY - EXAMPLE COMMUNITY
Satisfied by:
COLLEGE
Arts and Humanities are required from 2 disciplines, only 1 foreign language can apply.
Mathematics (MQ)  MATH 241 CALCULUS I  TRB  3.5  FALL 2021
Example University  Doe, Jane - *****123
XXX004 - MATHEMATICS I - EXAMPLE EVALUATION
Satisfied by:
SERVICE
"""

    @pytest.fixture
    def parsed(self, morgan):
        return parse_degreeworks_text(self.WRAPPED, source_name="audit.pdf", program=morgan)

    def test_a_label_on_its_own_line_is_read(self, parsed) -> None:
        assert by_code(parsed)["ECON211"].institution == "EXAMPLE COMMUNITY COLLEGE"

    def test_a_page_header_between_row_and_source_is_skipped(self, parsed) -> None:
        """The repeated header sits between many rows and their source line."""
        assert by_code(parsed)["MATH241"].institution == "EXAMPLE EVALUATION SERVICE"

    def test_the_following_section_is_not_swallowed(self, parsed) -> None:
        """Sweeping every nearby line pulled captions into the institution field."""
        for course in parsed.courses:
            assert course.institution is None or "foreign language" not in course.institution

    def test_a_masked_student_id_never_becomes_an_institution(self, parsed) -> None:
        """The page header carries a masked id. It is not a college."""
        for course in parsed.courses:
            assert course.institution is None or "*****" not in course.institution

    def test_an_inline_satisfied_by_is_unchanged(self) -> None:
        result = parse_degreeworks_text(WORKSHEET, source_name="a.pdf")
        assert by_code(result)["MATH241"].institution == "EXAMPLE COMMUNITY COLLEGE"

    def test_a_bare_label_is_never_read_as_a_course(self, parsed) -> None:
        assert all(c.raw_line.strip() != "Satisfied by:" for c in parsed.courses)
