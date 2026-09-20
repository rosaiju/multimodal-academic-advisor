"""The confirmation gate and the student record store.

This is the boundary the whole ingestion design exists to protect: extracted data
is a claim about a document, confirmed data is something a student stood behind,
and only the second kind may reach a degree audit.

The end-to-end test at the bottom is the one that matters - it runs a transcript
all the way through to a real audit and checks that unconfirmed rows changed
nothing.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from app.audit.engine import run_audit
from app.catalog.loader import load_program
from app.catalog.schema import Program
from app.ingestion.confirmation import (
    ConfirmationError,
    build_student_record,
    confirm_course,
)
from app.ingestion.parser import parse_transcript_text
from app.ingestion.store import RecordStore, StoredRecord, StoredRecordError
from app.schemas.provenance import TRUSTED_FOR_AUDIT, Provenance

MORGAN = Path(__file__).resolve().parents[2] / "data" / "catalog" / "morgan_cosc_bs_2026_2028.yaml"

TRANSCRIPT = """\
Fall 2024
COSC 111  Introduction to Computer Science I     4.00  A
ENGL 101  Composition I                          3.00  B

Spring 2025
COSC 112  Introduction to Computer Science II    4.00  A
ENGL 102  Composition II                         3.00  A
"""


@pytest.fixture(scope="module")
def morgan() -> Program:
    return load_program(MORGAN)


@pytest.fixture
def extraction(morgan):
    return parse_transcript_text(TRANSCRIPT, source_name="jane.pdf", program=morgan)


def row(extraction, code):
    return next(c for c in extraction.courses if c.code == code)


class TestConfirmationPromotesProvenance:
    def test_confirmed_course_is_student_confirmed(self, extraction) -> None:
        confirmed = confirm_course(row(extraction, "COSC111"), extraction)
        assert confirmed.course.provenance is Provenance.STUDENT_CONFIRMED
        assert confirmed.course.provenance in TRUSTED_FOR_AUDIT

    def test_extraction_itself_is_unchanged(self, extraction) -> None:
        """Confirming must not mutate the extracted claim it came from."""
        extracted = row(extraction, "COSC111")
        confirm_course(extracted, extraction)
        assert extracted.provenance is Provenance.UNVERIFIED_EXTRACTION

    def test_keeps_the_audit_trail(self, extraction) -> None:
        confirmed = confirm_course(row(extraction, "COSC111"), extraction)
        assert confirmed.source_name == "jane.pdf"
        assert confirmed.extractor == "text-parser"
        assert "COSC 111" in confirmed.raw_line


class TestCorrections:
    def test_student_can_correct_a_misread_grade(self, extraction) -> None:
        confirmed = confirm_course(row(extraction, "COSC111"), extraction, grade="B")
        assert confirmed.course.grade == "B"
        assert confirmed.was_corrected
        correction = confirmed.corrections[0]
        assert correction.field == "grade"
        assert correction.extracted == "A"
        assert correction.corrected == "B"

    def test_unchanged_values_are_not_recorded_as_corrections(self, extraction) -> None:
        confirmed = confirm_course(row(extraction, "COSC111"), extraction, grade="A")
        assert not confirmed.was_corrected

    def test_student_supplies_a_missing_term(self, morgan) -> None:
        extraction = parse_transcript_text("COSC 111  Intro  4.00  A\n", program=morgan)
        extracted = extraction.courses[0]
        assert extracted.term is None
        confirmed = confirm_course(extracted, extraction, term="Fall 2024")
        assert confirmed.course.term == "Fall 2024"
        assert confirmed.corrections[0].extracted is None


class TestRefusesToGuess:
    def test_cannot_confirm_without_a_term(self, morgan) -> None:
        extraction = parse_transcript_text("COSC 111  Intro  4.00  A\n", program=morgan)
        with pytest.raises(ConfirmationError, match="cannot confirm without term"):
            confirm_course(extraction.courses[0], extraction)

    def test_extracted_confirm_helper_also_refuses(self, morgan) -> None:
        extraction = parse_transcript_text("COSC 111  Intro  4.00  A\n", program=morgan)
        with pytest.raises(ValueError, match="incomplete row"):
            extraction.courses[0].confirm()

    def test_no_bulk_accept_helper_exists(self) -> None:
        """Confirmation must stay per-row and explicit.

        A convenience that accepted every high-confidence row would make this gate
        decorative: courses would land on an audit because a regex was confident,
        not because a person looked.
        """
        import app.ingestion.confirmation as module

        names = [n.lower() for n in dir(module)]
        assert not any("accept_all" in n or "confirm_all" in n for n in names)


class TestBuildStudentRecord:
    def test_produces_an_engine_ready_record(self, extraction) -> None:
        confirmed = [confirm_course(c, extraction) for c in extraction.courses]
        record = build_student_record("s1", confirmed)
        assert record.student_id == "s1"
        assert len(record.completed) == 4
        assert record.trusted_courses() == record.completed

    def test_rejects_anything_not_confirmed(self, extraction) -> None:
        confirmed = [confirm_course(row(extraction, "COSC111"), extraction)]
        tampered = confirmed[0].model_copy(
            update={
                "course": confirmed[0].course.model_copy(
                    update={"provenance": Provenance.UNVERIFIED_EXTRACTION}
                )
            }
        )
        with pytest.raises(ConfirmationError, match="did not come through confirmation"):
            build_student_record("s1", [tampered])


class TestRecordStore:
    def test_round_trips(self, tmp_path, extraction) -> None:
        store = RecordStore(tmp_path)
        confirmed = [confirm_course(c, extraction) for c in extraction.courses]
        store.save(StoredRecord(student_id="jane", confirmed=confirmed))

        loaded = store.load("jane")
        assert len(loaded.confirmed) == 4
        assert loaded.to_student_record().student_id == "jane"

    def test_writes_one_readable_file_per_student(self, tmp_path, extraction) -> None:
        store = RecordStore(tmp_path)
        store.add_courses("jane", [confirm_course(row(extraction, "COSC111"), extraction)])
        path = tmp_path / "jane.json"
        assert path.is_file()
        assert "COSC111" in path.read_text(encoding="utf-8")

    def test_rejects_unsafe_student_ids(self, tmp_path) -> None:
        """A student id becomes a filename, so it must not escape the directory."""
        store = RecordStore(tmp_path)
        for bad in ("../escape", "a/b", "", "..", "nul\x00"):
            with pytest.raises(StoredRecordError, match="unsafe student id"):
                store.path_for(bad)

    def test_refuses_a_file_containing_unconfirmed_coursework(self, tmp_path, extraction) -> None:
        """A record file is editable on disk; being in the folder is not trust."""
        store = RecordStore(tmp_path)
        confirmed = confirm_course(row(extraction, "COSC111"), extraction)
        store.save(StoredRecord(student_id="jane", confirmed=[confirmed]))

        path = tmp_path / "jane.json"
        path.write_text(
            path.read_text(encoding="utf-8").replace("student_confirmed", "unverified_extraction"),
            encoding="utf-8",
        )
        with pytest.raises(StoredRecordError, match="not marked student_confirmed"):
            store.load("jane")

    def test_corrupt_file_fails_loudly(self, tmp_path) -> None:
        store = RecordStore(tmp_path)
        (tmp_path / "jane.json").write_text("{not json", encoding="utf-8")
        with pytest.raises(StoredRecordError, match="corrupt record file"):
            store.load("jane")

    def test_reupload_replaces_rather_than_duplicates(self, tmp_path, extraction) -> None:
        store = RecordStore(tmp_path)
        first = [confirm_course(c, extraction) for c in extraction.courses]
        store.add_courses("jane", first)
        record = store.add_courses("jane", first)  # same transcript again
        codes = [c.course.code for c in record.confirmed]
        assert len(codes) == len(set(codes)) == 4

    def test_add_courses_merges_new_with_existing(self, tmp_path, extraction) -> None:
        store = RecordStore(tmp_path)
        store.add_courses("jane", [confirm_course(row(extraction, "COSC111"), extraction)])
        record = store.add_courses("jane", [confirm_course(row(extraction, "ENGL101"), extraction)])
        assert {c.course.code for c in record.confirmed} == {"COSC111", "ENGL101"}

    def test_tracks_which_rows_the_student_corrected(self, tmp_path, extraction) -> None:
        store = RecordStore(tmp_path)
        store.add_courses(
            "jane", [confirm_course(row(extraction, "COSC111"), extraction, grade="B")]
        )
        assert [c.course.code for c in store.load("jane").corrected_courses] == ["COSC111"]

    def test_list_and_delete(self, tmp_path, extraction) -> None:
        store = RecordStore(tmp_path)
        assert store.list_students() == []
        store.add_courses("jane", [confirm_course(row(extraction, "COSC111"), extraction)])
        assert store.list_students() == ["jane"]
        assert store.delete("jane") is True
        assert store.delete("jane") is False

    def test_missing_record_is_an_error_not_an_empty_one(self, tmp_path) -> None:
        """An empty record would audit as 'you have completed nothing'."""
        with pytest.raises(StoredRecordError, match="no record for student"):
            RecordStore(tmp_path).load("nobody")


class TestEndToEnd:
    def test_transcript_to_audit_only_through_confirmation(
        self, tmp_path, morgan, extraction
    ) -> None:
        """The full path, and the thing it guarantees.

        The transcript yields four courses. The student confirms three. The fourth
        must have no effect on the audit whatsoever.
        """
        store = RecordStore(tmp_path)
        chosen = ["COSC111", "ENGL101", "ENGL102"]
        store.add_courses(
            "jane",
            [confirm_course(row(extraction, c), extraction) for c in chosen],
            program_id=morgan.program_id,
        )

        record = store.load("jane").to_student_record()
        result = run_audit(morgan, record)

        applied = {a.course.code for b in result.blocks for a in b.applied}
        assert "COSC112" not in applied, "an unconfirmed course reached the audit"
        assert applied <= set(chosen)
        assert all(
            a.provenance is Provenance.STUDENT_CONFIRMED for b in result.blocks for a in b.applied
        )

    def test_confirming_nothing_audits_as_no_progress(self, tmp_path, morgan) -> None:
        record = StoredRecord(student_id="jane").to_student_record()
        result = run_audit(morgan, record)
        assert result.total_credits_applied == Decimal(0)


class TestBlockTransferSurvivesTheStore:
    """Regression: the store re-collapsed what the parser had just separated.

    A DegreeWorks block-transfer bucket such as COSC116TR is one code standing for
    many distinct transfer courses. `add_courses` keyed its merge on the code alone,
    so saving fourteen of them kept the last and silently deleted the rest of the
    credit - after the parser had correctly told them apart.

    All coursework below is invented.
    """

    AUDIT = """\
Degree Works Audit
Free Electives
COSC 116TR COSC LWR LVL ELECTIVE  TRA  0.5  FALL 2021
Satisfied by: XXX006 - INTRO TO INFO TECH - EXAMPLE EVALUATION SERVICE
COSC 116TR COSC LWR LVL ELECTIVE  TRB  0.5  SPRING 2022
Satisfied by: XXX007 - C PROGRAMMING - EXAMPLE EVALUATION SERVICE
COSC 116TR COSC LWR LVL ELECTIVE  TRC  3    SUMMER 2022
Satisfied by: XXX020 - NUMERICAL METHOD - EXAMPLE EVALUATION SERVICE
"""

    @pytest.fixture
    def buckets(self, morgan):
        from app.ingestion.degreeworks import parse_degreeworks_text

        return parse_degreeworks_text(self.AUDIT, source_name="audit.pdf", program=morgan)

    def test_every_bucket_row_is_stored(self, tmp_path, buckets) -> None:
        store = RecordStore(tmp_path)
        record = store.add_courses("jane", [confirm_course(c, buckets) for c in buckets.courses])
        assert len(record.confirmed) == 3

    def test_no_credit_is_lost_on_save(self, tmp_path, buckets) -> None:
        store = RecordStore(tmp_path)
        store.add_courses("jane", [confirm_course(c, buckets) for c in buckets.courses])
        reloaded = store.load("jane")
        assert sum(c.course.credits for c in reloaded.confirmed) == Decimal("4.0")

    def test_reupload_still_replaces_rather_than_duplicates(self, tmp_path, buckets) -> None:
        """Widening the key must not reintroduce the doubling it was guarding."""
        store = RecordStore(tmp_path)
        rows = [confirm_course(c, buckets) for c in buckets.courses]
        store.add_courses("jane", rows)
        record = store.add_courses("jane", rows)
        assert len(record.confirmed) == 3
        assert sum(c.course.credits for c in record.confirmed) == Decimal("4.0")

    def test_a_retake_with_a_new_grade_does_not_erase_the_original(
        self, tmp_path, extraction
    ) -> None:
        """Two sittings are two rows. The engine decides which one counts."""
        store = RecordStore(tmp_path)
        first = confirm_course(row(extraction, "COSC111"), extraction)
        store.add_courses("jane", [first])
        retake = confirm_course(row(extraction, "COSC111"), extraction, grade="C")
        record = store.add_courses("jane", [retake])
        assert len(record.confirmed) == 2
        assert {c.course.grade for c in record.confirmed} == {"A", "C"}

    def test_rows_alike_in_every_field_but_their_source_both_survive(
        self, tmp_path, morgan
    ) -> None:
        """The hardest case, and the one the real audit actually contains.

        Two practicals taken in the same term, equated to the same grade, worth the
        same half credit, printed on identical lines. Code, term, grade, credits and
        raw line all match; only the sending course differs. Without carrying that
        reference through confirmation, one of them is deleted on save.
        """
        from app.ingestion.degreeworks import parse_degreeworks_text

        audit = (
            "Degree Works Audit\n"
            "COSC 116TR COSC LWR LVL ELECTIVE  TRB  0.5  SUMMER 2022\n"
            "Satisfied by: XXX015 - DISCRETE STRUCTURE - EXAMPLE EVALUATION SERVICE\n"
            "COSC 116TR COSC LWR LVL ELECTIVE  TRB  0.5  SUMMER 2022\n"
            "Satisfied by: XXX016 - OBJECT ORIENTED PROG - EXAMPLE EVALUATION SERVICE\n"
        )
        extraction = parse_degreeworks_text(audit, source_name="audit.pdf", program=morgan)
        assert len(extraction.courses) == 2
        assert len({c.raw_line for c in extraction.courses}) == 1, "the lines are identical"

        store = RecordStore(tmp_path)
        record = store.add_courses(
            "jane", [confirm_course(c, extraction) for c in extraction.courses]
        )
        assert len(record.confirmed) == 2
        assert sum(c.course.credits for c in record.confirmed) == Decimal("1.0")

    def test_the_source_reference_reaches_the_stored_file(self, tmp_path, buckets) -> None:
        """It is provenance, not just a dedup key: a dispute needs to see it."""
        store = RecordStore(tmp_path)
        store.add_courses("jane", [confirm_course(c, buckets) for c in buckets.courses])
        stored = store.load("jane")
        assert all("EXAMPLE EVALUATION SERVICE" in c.source_reference for c in stored.confirmed)

    def test_an_ordinary_transcript_row_has_no_source_reference(self, tmp_path, extraction) -> None:
        """A plain transcript has no 'Satisfied by' line, and must not grow one."""
        store = RecordStore(tmp_path)
        store.add_courses("jane", [confirm_course(row(extraction, "COSC111"), extraction)])
        assert store.load("jane").confirmed[0].source_reference is None


class TestInProgressIsAStatusNotAGap:
    """An IP row can be confirmed without inventing a final grade.

    "IP" is what the registrar printed, not something the extractor failed to
    read, so demanding a letter grade before the row can be accepted would make a
    student either type a guess or drop a course they are actually sitting in.

    What keeps this safe is downstream, not here: IP is in the catalog's
    NON_PASSING set, so a confirmed in-progress course still satisfies nothing.
    All coursework below is invented.
    """

    AUDIT = """\
Degree Works Audit
Requirement One  COSC 490 SENIOR PROJECT      IP  (3)  FALL 2026
Requirement Two  COSC 001 SENIOR COMP EXAM    IP  (0)  FALL 2026
Requirement Three  ENGL 101 COMPOSITION I     A    3   FALL 2024
"""

    @pytest.fixture
    def parsed(self, morgan):
        from app.ingestion.degreeworks import parse_degreeworks_text

        return parse_degreeworks_text(self.AUDIT, source_name="audit.pdf", program=morgan)

    def test_in_progress_is_flagged_structurally(self, parsed) -> None:
        by = {c.code: c for c in parsed.courses}
        assert by["COSC490"].in_progress is True
        assert by["ENGL101"].in_progress is False

    def test_in_progress_needs_no_final_grade_to_confirm(self, parsed) -> None:
        row = next(c for c in parsed.courses if c.code == "COSC490")
        assert row.missing_fields == []
        assert row.can_confirm is True
        assert row.blocking_reason is None

    def test_a_zero_credit_row_with_a_grade_is_a_real_registration(self, parsed) -> None:
        """A comprehensive exam is 0 credits and still a course a student sits."""
        row = next(c for c in parsed.courses if c.code == "COSC001")
        assert row.is_placeholder is False
        assert row.can_confirm is True

    def test_confirming_one_does_not_advance_the_degree(self, tmp_path, morgan, parsed) -> None:
        """The safeguard. Confirmed, visible, and still satisfying nothing."""
        store = RecordStore(tmp_path)
        rows = [c for c in parsed.courses if c.in_progress]
        assert rows, "fixture must contain in-progress rows"
        store.add_courses(
            "jane",
            [confirm_course(c, parsed) for c in rows],
            program_id=morgan.program_id,
        )
        result = run_audit(morgan, store.load("jane").to_student_record())
        assert result.total_credits_applied == Decimal(0)
        applied = {a.course.code for b in result.blocks for a in b.applied}
        assert "COSC490" not in applied

    def test_the_confirmed_row_keeps_its_in_progress_grade(self, tmp_path, parsed) -> None:
        """Stored verbatim, so nothing downstream has to guess what IP meant."""
        store = RecordStore(tmp_path)
        row = next(c for c in parsed.courses if c.code == "COSC490")
        record = store.add_courses("jane", [confirm_course(row, parsed)])
        assert record.confirmed[0].course.grade == "IP"


class TestSummaryRowsAreNotCoursework:
    """A "TRANSFER OF 24 CREDITS" line totals credit listed separately below it.

    Confirming it would count those 24 credits twice - once as the summary and
    again as the individual rows it summarises. So it is refused outright rather
    than merely left unticked, and no edit makes it confirmable.

    All coursework below is invented.
    """

    AUDIT = """\
Degree Works Audit
Freshman Orientation  ORTR 101 TRANSFER OF 24 CREDITS  TR  0  FALL 2023
Satisfied by: - EXAMPLE EVALUATION SERVICE
Requirement Two  ENGL 101 COMPOSITION I  A  3  FALL 2024
Requirement Three  MATH 241 CALCULUS I   B  4  FALL 2024
"""

    @pytest.fixture
    def parsed(self, morgan):
        from app.ingestion.degreeworks import parse_degreeworks_text

        return parse_degreeworks_text(self.AUDIT, source_name="audit.pdf", program=morgan)

    def test_a_summary_row_is_recognised(self, parsed) -> None:
        row = next(c for c in parsed.courses if c.code == "ORTR101")
        assert row.is_placeholder is True
        assert row.can_confirm is False

    def test_it_says_why_in_words_a_student_can_act_on(self, parsed) -> None:
        row = next(c for c in parsed.courses if c.code == "ORTR101")
        assert row.blocking_reason is not None
        assert "summary line" in row.blocking_reason
        assert "listed separately" in row.blocking_reason

    def test_confirming_it_is_refused(self, parsed) -> None:
        row = next(c for c in parsed.courses if c.code == "ORTR101")
        with pytest.raises(ConfirmationError, match="summary line"):
            confirm_course(row, parsed)

    def test_editing_a_grade_onto_it_does_not_make_it_a_course(self, parsed) -> None:
        """The whole point. Otherwise the 24 credits land twice."""
        row = next(c for c in parsed.courses if c.code == "ORTR101")
        with pytest.raises(ConfirmationError, match="summary line"):
            confirm_course(row, parsed, grade="A", credits=Decimal(24))

    def test_extracted_confirm_refuses_it_too(self, parsed) -> None:
        """Both doors into a CompletedCourse are shut, not just the API one."""
        row = next(c for c in parsed.courses if c.code == "ORTR101")
        with pytest.raises(ValueError, match="summary line"):
            row.confirm()

    def test_it_is_excluded_from_confirmable_and_named_in_blocked(self, parsed) -> None:
        assert "ORTR101" not in {c.code for c in parsed.confirmable}
        assert "ORTR101" in {c.code for c in parsed.blocked}

    def test_one_bad_row_does_not_block_the_others(self, parsed) -> None:
        """The reported bug: 63 rows ticked, one unusable, nothing confirmable."""
        assert len(parsed.blocked) == 1
        assert {c.code for c in parsed.confirmable} == {"ENGL101", "MATH241"}

    def test_the_rest_confirm_and_reach_the_audit(self, tmp_path, morgan, parsed) -> None:
        store = RecordStore(tmp_path)
        store.add_courses(
            "jane",
            [confirm_course(c, parsed) for c in parsed.confirmable],
            program_id=morgan.program_id,
        )
        result = run_audit(morgan, store.load("jane").to_student_record())
        assert result.total_credits_applied > Decimal(0)


class TestBlockingReasonsAreSpecific:
    """ "Some rows are missing information" makes a student hunt. Name the field."""

    def test_a_row_missing_a_term_names_the_term(self, morgan) -> None:
        from app.ingestion.parser import parse_transcript_text

        # No term heading, so the row parses but cannot say when it was taken.
        result = parse_transcript_text(
            "COSC 111  Intro to CS I  4.00  A\n", source_name="t.txt", program=morgan
        )
        extracted = result.courses[0]
        assert extracted.missing_fields == ["term"]
        assert extracted.blocking_reason is not None
        assert "term" in extracted.blocking_reason

    def test_a_row_missing_only_a_grade_is_not_a_summary_line(self) -> None:
        """Real credits with an unread grade is a fixable row, not a heading."""
        from app.ingestion.models import ExtractedCourse

        extracted = ExtractedCourse(
            code="COSC111", term="Fall 2024", grade=None, credits=Decimal(4), raw_line="x"
        )
        assert extracted.is_placeholder is False
        assert extracted.missing_fields == ["grade"]
        assert "grade" in extracted.blocking_reason

    def test_a_complete_row_has_no_reason(self, extraction) -> None:
        assert row(extraction, "COSC111").blocking_reason is None
        assert row(extraction, "COSC111").missing_fields == []

    def test_confirmable_and_blocked_partition_every_row(self, extraction) -> None:
        total = len(extraction.confirmable) + len(extraction.blocked)
        assert total == len(extraction.courses)


class TestTheReviewFieldsReachTheBrowser:
    """The UI renders these verbatim, so they must survive serialisation."""

    def test_status_fields_are_serialised(self, extraction) -> None:
        payload = extraction.model_dump(mode="json")
        first = payload["courses"][0]
        for field in ("in_progress", "is_placeholder", "missing_fields", "blocking_reason"):
            assert field in first, f"{field} missing from the API payload"

    def test_a_placeholder_serialises_its_reason(self, morgan) -> None:
        from app.ingestion.degreeworks import parse_degreeworks_text

        audit = (
            "Degree Works Audit\n"
            "Freshman Orientation  ORTR 101 TRANSFER OF 24 CREDITS  TR  0  FALL 2023\n"
            "Requirement  ENGL 101 COMPOSITION I  A  3  FALL 2024\n"
            "Requirement  MATH 241 CALCULUS I     B  4  FALL 2024\n"
        )
        payload = parse_degreeworks_text(audit, source_name="a.pdf", program=morgan).model_dump(
            mode="json"
        )
        ortr = next(c for c in payload["courses"] if c["code"] == "ORTR101")
        assert ortr["is_placeholder"] is True
        assert "summary line" in ortr["blocking_reason"]
