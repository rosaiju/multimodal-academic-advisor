"""Model-backed transcript extraction.

Every test here uses a stub provider returning canned responses. Nothing calls a
real API, and the fixtures are synthetic transcripts for a fictional student.

What is actually under test is not extraction quality — that depends on a model we
do not control — but what the system does with whatever the model returns:

* a model row is never HIGH confidence, so it can never look auto-confirmable,
* malformed, missing, or invented fields are dropped rather than coerced,
* the output still cannot reach a degree audit without confirmation.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.audit.engine import run_audit
from app.catalog.loader import load_program
from app.catalog.schema import Program
from app.ingestion.confirmation import confirm_course
from app.ingestion.extractor import (
    TranscriptExtractor,
    register_extractor,
    select_extractor,
)
from app.ingestion.models import Confidence
from app.llm.provider import ProviderError, VisionProvider
from app.llm.transcript_vision import VisionTranscriptExtractor
from app.schemas.provenance import Provenance

MORGAN = Path(__file__).resolve().parents[2] / "data" / "catalog" / "morgan_cosc_bs_2026_2028.yaml"

#: A synthetic transcript as a model would report it. Fictional student.
CLEAN_RESPONSE = json.dumps(
    {
        "institution": "Morgan State University",
        "courses": [
            {
                "code": "COSC 111",
                "title": "Introduction to Computer Science I",
                "term": "Fall 2024",
                "grade": "A",
                "credits": "4.00",
                "institution": None,
                "raw_line": "COSC 111  Introduction to Computer Science I  4.00  A",
            },
            {
                "code": "ENGL 101",
                "title": "Composition I",
                "term": "Fall 2024",
                "grade": "B",
                "credits": "3.00",
                "institution": None,
                "raw_line": "ENGL 101  Composition I  3.00  B",
            },
        ],
    }
)


class StubProvider:
    """Returns a canned response. Records what it was asked, for assertions."""

    def __init__(self, response: str, name: str = "stub-vision") -> None:
        self.name = name
        self._response = response
        self.calls: list[dict] = []

    def read_document(self, data, *, media_type, prompt, max_tokens=4096) -> str:
        self.calls.append({"bytes": len(data), "media_type": media_type, "prompt": prompt})
        return self._response


class ExplodingProvider:
    name = "exploding"

    def read_document(self, data, *, media_type, prompt, max_tokens=4096) -> str:
        raise RuntimeError("upstream 503")


@pytest.fixture(scope="module")
def morgan() -> Program:
    return load_program(MORGAN)


def extractor(response: str = CLEAN_RESPONSE) -> VisionTranscriptExtractor:
    return VisionTranscriptExtractor(StubProvider(response))


class TestNeverAutoConfirmable:
    """The central guarantee: a model row always needs a person."""

    def test_a_clean_model_row_is_never_high_confidence(self, morgan) -> None:
        """Even a perfectly-read row caps at MEDIUM.

        The parser earns HIGH because a regex match is reproducible forever. A
        model reading pixels is making a claim, and a tidy claim is still a claim.
        """
        result = extractor().extract(b"fake", filename="scan.png", program=morgan)
        assert result.courses
        assert all(c.confidence is not Confidence.HIGH for c in result.courses)

    def test_every_row_needs_review(self, morgan) -> None:
        result = extractor().extract(b"fake", filename="scan.png", program=morgan)
        assert result.needs_review == result.courses

    def test_every_row_is_an_unverified_extraction(self, morgan) -> None:
        result = extractor().extract(b"fake", filename="scan.png", program=morgan)
        assert all(c.provenance is Provenance.UNVERIFIED_EXTRACTION for c in result.courses)

    def test_every_row_says_it_was_machine_read(self, morgan) -> None:
        result = extractor().extract(b"fake", filename="scan.png", program=morgan)
        for course in result.courses:
            assert any("scanned or photographed" in i for i in course.issues)

    def test_result_warns_that_all_rows_need_checking(self, morgan) -> None:
        result = extractor().extract(b"fake", filename="scan.png", program=morgan)
        assert any("needs checking" in w for w in result.warnings)

    def test_model_output_cannot_reach_an_audit_unconfirmed(self, morgan) -> None:
        """The end-to-end guarantee, exercised against the real engine.

        Simulates the exact mistake this design prevents: model-extracted courses
        pushed straight into a StudentRecord without confirmation. The engine must
        apply none of them.
        """
        from app.audit.record import CompletedCourse, StudentRecord

        result = extractor().extract(b"fake", filename="scan.png", program=morgan)
        assert len(result.courses) == 2

        smuggled = StudentRecord(
            student_id="s1",
            completed=[
                CompletedCourse(
                    code=c.code,
                    term=c.term,
                    grade=c.grade,
                    credits=c.credits,
                    provenance=c.provenance,  # still UNVERIFIED_EXTRACTION
                )
                for c in result.courses
            ],
        )
        audit = run_audit(morgan, smuggled)
        assert audit.total_credits_applied == Decimal(0), "unconfirmed rows reached the audit"
        assert audit.gpa.cumulative is None
        assert all(not b.applied for b in audit.blocks)

    def test_the_same_courses_count_once_confirmed(self, morgan) -> None:
        """The supported path: confirmation makes exactly those rows countable."""
        from app.audit.record import StudentRecord

        result = extractor().extract(b"fake", filename="scan.png", program=morgan)
        confirmed = [confirm_course(c, result) for c in result.courses]
        assert all(c.course.provenance is Provenance.STUDENT_CONFIRMED for c in confirmed)

        record = StudentRecord(student_id="s1", completed=[c.course for c in confirmed])
        audit = run_audit(morgan, record)
        assert audit.total_credits_applied == Decimal(7)  # COSC111 4 + ENGL101 3


class TestExtraction:
    def test_reads_code_title_grade_credits_term(self, morgan) -> None:
        result = extractor().extract(b"fake", filename="scan.png", program=morgan)
        row = result.courses[0]
        assert row.code == "COSC111"
        assert row.title == "Introduction to Computer Science I"
        assert row.term == "Fall 2024"
        assert row.grade == "A"
        assert row.credits == Decimal("4.00")

    def test_reads_institution_at_document_and_row_level(self, morgan) -> None:
        result = extractor().extract(b"fake", filename="scan.png", program=morgan)
        assert result.institution == "Morgan State University"
        assert all(c.institution == "Morgan State University" for c in result.courses)

    def test_row_institution_overrides_the_document(self, morgan) -> None:
        response = json.dumps(
            {
                "institution": "Morgan State University",
                "courses": [
                    {
                        "code": "MATH 141",
                        "term": "Fall 2023",
                        "grade": "A",
                        "credits": "3",
                        "institution": "Baltimore City Community College",
                        "raw_line": "MATH 141 ...",
                    }
                ],
            }
        )
        result = extractor(response).extract(b"x", filename="s.png", program=morgan)
        assert result.courses[0].institution == "Baltimore City Community College"

    def test_institution_survives_confirmation_into_the_record(self, morgan) -> None:
        """Residency needs this, and it must not be lost at the gate."""
        result = extractor().extract(b"fake", filename="scan.png", program=morgan)
        confirmed = confirm_course(result.courses[0], result)
        assert confirmed.course.institution == "Morgan State University"

    def test_normalises_codes_without_repairing_them(self, morgan) -> None:
        result = extractor().extract(b"fake", filename="scan.png", program=morgan)
        assert [c.code for c in result.courses] == ["COSC111", "ENGL101"]

    def test_extractor_name_records_the_model(self) -> None:
        assert extractor().name == "vision:stub-vision"

    def test_unknown_catalog_course_is_flagged_not_corrected(self, morgan) -> None:
        response = json.dumps(
            {
                "courses": [
                    {
                        "code": "ZZZZ 999",
                        "term": "Fall 2024",
                        "grade": "A",
                        "credits": "3",
                        "raw_line": "ZZZZ 999 ...",
                    }
                ]
            }
        )
        row = extractor(response).extract(b"x", filename="s.png", program=morgan).courses[0]
        assert row.code == "ZZZZ999"
        assert any("not in the catalog" in i for i in row.issues)


class TestDefensiveParsing:
    """The model's output is untrusted input."""

    def test_tolerates_a_fenced_json_block(self, morgan) -> None:
        wrapped = f"Here is the transcript:\n```json\n{CLEAN_RESPONSE}\n```\nHope that helps!"
        result = extractor(wrapped).extract(b"x", filename="s.png", program=morgan)
        assert len(result.courses) == 2

    def test_unparseable_response_extracts_nothing_and_says_so(self, morgan) -> None:
        result = extractor("I'm sorry, I can't read this.").extract(
            b"x", filename="s.png", program=morgan
        )
        assert result.courses == []
        assert any("unreadable" in w for w in result.warnings)

    def test_malformed_json_extracts_nothing(self, morgan) -> None:
        result = extractor('{"courses": [').extract(b"x", filename="s.png", program=morgan)
        assert result.courses == []
        assert any("unreadable" in w for w in result.warnings)

    def test_missing_course_list_is_reported(self, morgan) -> None:
        result = extractor('{"institution": "Somewhere"}').extract(
            b"x", filename="s.png", program=morgan
        )
        assert result.courses == []
        assert any("no course list" in w for w in result.warnings)

    def test_row_with_an_unusable_code_is_skipped_not_guessed(self, morgan) -> None:
        response = json.dumps(
            {"courses": [{"code": "the first computer science class", "grade": "A"}]}
        )
        result = extractor(response).extract(b"x", filename="s.png", program=morgan)
        assert result.courses == []
        assert any("not shaped like a course code" in w for w in result.warnings)

    def test_null_like_strings_become_absent(self, morgan) -> None:
        response = json.dumps(
            {
                "courses": [
                    {
                        "code": "COSC 111",
                        "term": "N/A",
                        "grade": "unknown",
                        "credits": "null",
                        "title": "--",
                        "raw_line": "COSC 111",
                    }
                ]
            }
        )
        row = extractor(response).extract(b"x", filename="s.png", program=morgan).courses[0]
        assert row.term is None and row.grade is None
        assert row.credits is None and row.title is None
        assert row.confidence is Confidence.LOW

    def test_non_numeric_credits_become_absent(self, morgan) -> None:
        response = json.dumps(
            {
                "courses": [
                    {"code": "COSC 111", "grade": "A", "credits": "four", "term": "Fall 2024"}
                ]
            }
        )
        row = extractor(response).extract(b"x", filename="s.png", program=morgan).courses[0]
        assert row.credits is None
        assert row.confidence is Confidence.LOW

    def test_incomplete_row_cannot_be_confirmed(self, morgan) -> None:
        response = json.dumps({"courses": [{"code": "COSC 111", "raw_line": "COSC 111"}]})
        result = extractor(response).extract(b"x", filename="s.png", program=morgan)
        assert result.confirmable == []

    def test_non_object_rows_are_skipped(self, morgan) -> None:
        response = json.dumps({"courses": ["COSC 111", 42, None]})
        result = extractor(response).extract(b"x", filename="s.png", program=morgan)
        assert result.courses == []
        assert len([w for w in result.warnings if "not a course object" in w]) == 3

    def test_provider_failure_raises_rather_than_returning_empty(self, morgan) -> None:
        """An empty result would read as 'your transcript has no courses'."""
        bad = VisionTranscriptExtractor(ExplodingProvider())
        with pytest.raises(ProviderError, match="upstream 503"):
            bad.extract(b"x", filename="s.png", program=morgan)


class TestInterfaceCompliance:
    def test_satisfies_the_extractor_protocol(self) -> None:
        assert isinstance(extractor(), TranscriptExtractor)

    def test_stub_provider_satisfies_the_provider_protocol(self) -> None:
        assert isinstance(StubProvider("{}"), VisionProvider)

    def test_handles_images_and_pdfs(self) -> None:
        vision = extractor()
        for filename, content_type in (
            ("scan.png", "image/png"),
            ("photo.jpg", "image/jpeg"),
            ("transcript.pdf", "application/pdf"),
            ("scan.PNG", None),
        ):
            assert vision.can_handle(filename, content_type), filename

    def test_does_not_claim_text_transcripts(self) -> None:
        assert not extractor().can_handle("transcript.txt", "text/plain")

    def test_media_type_is_passed_to_the_provider(self, morgan) -> None:
        provider = StubProvider(CLEAN_RESPONSE)
        VisionTranscriptExtractor(provider).extract(
            b"x", filename="t.pdf", program=morgan, content_type="application/pdf"
        )
        assert provider.calls[0]["media_type"] == "application/pdf"

    def test_media_type_falls_back_to_the_suffix(self, morgan) -> None:
        provider = StubProvider(CLEAN_RESPONSE)
        VisionTranscriptExtractor(provider).extract(b"x", filename="t.jpg", program=morgan)
        assert provider.calls[0]["media_type"] == "image/jpeg"

    def test_prompt_forbids_guessing(self, morgan) -> None:
        provider = StubProvider(CLEAN_RESPONSE)
        VisionTranscriptExtractor(provider).extract(b"x", filename="t.png", program=morgan)
        prompt = provider.calls[0]["prompt"]
        assert "Never guess a value" in prompt
        assert "Never invent a course" in prompt

    def test_registering_it_does_not_displace_the_text_parser(self, morgan) -> None:
        vision = extractor()
        register_extractor(vision)
        try:
            assert select_extractor("t.txt", "text/plain").name == "text-parser"
            assert select_extractor("scan.png", "image/png").name == vision.name
        finally:
            from app.ingestion import extractor as module

            module._registry = [e for e in module._registry if e.name != vision.name]


class TestIsolation:
    def test_llm_layer_does_not_import_the_engine_internals(self) -> None:
        """app/llm/ may read catalog/ingestion TYPES, never the audit engine."""
        llm_dir = Path(__file__).resolve().parents[1] / "app" / "llm"
        for path in llm_dir.glob("*.py"):
            source = path.read_text(encoding="utf-8")
            assert "app.audit" not in source, f"{path.name} imports the audit engine"

    def test_ingestion_layer_still_does_not_import_the_llm_layer(self) -> None:
        ingestion_dir = Path(__file__).resolve().parents[1] / "app" / "ingestion"
        for path in ingestion_dir.glob("*.py"):
            source = path.read_text(encoding="utf-8")
            assert "app.llm" not in source, f"{path.name} imports the LLM layer"
