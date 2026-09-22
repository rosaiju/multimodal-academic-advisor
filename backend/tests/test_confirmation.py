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
