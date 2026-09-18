"""The transcript upload API and the extractor interface.

The API-level guarantee under test: uploading a transcript stores nothing. The
only way a course reaches a student's record is an explicit confirm call naming
that course. If those two ever collapse into one endpoint, these tests fail.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.ingestion.extractor import (
    TextTranscriptExtractor,
    TranscriptExtractor,
    UnsupportedDocument,
    extract_transcript,
    register_extractor,
    registered_extractors,
    select_extractor,
)
from app.ingestion.models import ExtractionResult
from app.schemas.provenance import Provenance

TRANSCRIPT = (
    b"Fall 2024\n"
    b"COSC 111  Introduction to Computer Science I   4.00  A\n"
    b"ENGL 101  Composition I                       3.00  B\n"
    b"Spring 2025\n"
    b"COSC 112  Introduction to Computer Science II  4.00  A\n"
)
PROGRAM = "morgan_cosc_bs_2026_2028"


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A client whose student records land in a throwaway directory."""
    monkeypatch.setenv("STUDENT_RECORD_DIR", str(tmp_path))
    get_settings.cache_clear()
    from app.main import app

    with TestClient(app) as c:
        yield c
    get_settings.cache_clear()


def upload(client, data: bytes = TRANSCRIPT, name: str = "jane.txt", program: str | None = PROGRAM):
    payload = {"program_id": program} if program else {}
    return client.post(
        "/ingest/transcript",
        files={"file": (name, data, "text/plain")},
        data=payload,
    )


class TestUploadStoresNothing:
    def test_upload_reports_stored_false(self, client) -> None:
        body = upload(client).json()
        assert body["stored"] is False
        assert "Nothing counts toward your degree until you do" in body["next_step"]

    def test_upload_creates_no_record(self, client) -> None:
        upload(client)
        assert client.get("/students/jane/record").status_code == 404

    def test_every_returned_row_is_unverified(self, client) -> None:
        rows = upload(client).json()["extraction"]["courses"]
        assert rows
        assert all(r["provenance"] == Provenance.UNVERIFIED_EXTRACTION.value for r in rows)


class TestUploadValidation:
    def test_empty_file_is_rejected(self, client) -> None:
        response = upload(client, data=b"")
        assert response.status_code == 400

    def test_oversized_file_is_rejected(self, client) -> None:
        limit = get_settings().max_upload_bytes
        response = upload(client, data=b"x" * (limit + 1))
        assert response.status_code == 413

    def test_unknown_program_is_404(self, client) -> None:
        assert upload(client, program="no_such_program").status_code == 404

    def test_unreadable_document_type_is_415(self, client) -> None:
        response = client.post(
            "/ingest/transcript",
            files={"file": ("scan.png", b"\x89PNG\r\n\x1a\n", "image/png")},
        )
        assert response.status_code == 415
        assert "not configured" in response.json()["detail"]

    def test_program_is_optional(self, client) -> None:
        assert upload(client, program=None).status_code == 200


class TestConfirmFlow:
    def test_only_named_rows_are_stored(self, client) -> None:
        rows = upload(client).json()["extraction"]["courses"]
        response = client.post(
            "/ingest/confirm",
            json={
                "student_id": "jane",
                "program_id": PROGRAM,
                "source_name": "jane.txt",
                "extractor": "text-parser",
                "courses": [{"extracted": rows[0]}],
            },
        )
        assert response.status_code == 200
        assert response.json()["confirmed_now"] == 1

        stored = client.get("/students/jane/record").json()
        assert [c["course"]["code"] for c in stored["confirmed"]] == ["COSC111"]

    def test_stored_courses_are_student_confirmed(self, client) -> None:
        rows = upload(client).json()["extraction"]["courses"]
        client.post(
            "/ingest/confirm",
            json={
                "student_id": "jane",
                "source_name": "jane.txt",
                "extractor": "text-parser",
                "courses": [{"extracted": r} for r in rows],
            },
        )
        stored = client.get("/students/jane/record").json()
        assert all(
            c["course"]["provenance"] == Provenance.STUDENT_CONFIRMED.value
            for c in stored["confirmed"]
        )

    def test_corrections_are_reported_and_kept(self, client) -> None:
        rows = upload(client).json()["extraction"]["courses"]
        response = client.post(
            "/ingest/confirm",
            json={
                "student_id": "jane",
                "source_name": "jane.txt",
                "extractor": "text-parser",
                # The extracted row goes back untouched; the fix travels beside it.
                "courses": [{"extracted": rows[0], "grade": "B"}],
            },
        )
        assert response.json()["corrected"] == ["COSC111"]
        stored = client.get("/students/jane/record").json()
        assert stored["confirmed"][0]["course"]["grade"] == "B"
        assert stored["confirmed"][0]["corrections"][0]["extracted"] == "A"

    def test_incomplete_row_is_422_with_a_usable_message(self, client) -> None:
        body = upload(client, data=b"COSC 111  Intro  4.00  A\n").json()
        row = body["extraction"]["courses"][0]
        assert row["term"] is None
        response = client.post(
            "/ingest/confirm",
            json={
                "student_id": "jane",
                "source_name": "jane.txt",
                "extractor": "text-parser",
                "courses": [{"extracted": row}],
            },
        )
        assert response.status_code == 422
        assert "term" in response.json()["detail"]

    def test_empty_confirm_is_rejected(self, client) -> None:
        response = client.post(
            "/ingest/confirm",
            json={
                "student_id": "jane",
                "source_name": "x",
                "extractor": "text-parser",
                "courses": [],
            },
        )
        assert response.status_code == 400

    def test_unsafe_student_id_is_rejected(self, client) -> None:
        rows = upload(client).json()["extraction"]["courses"]
        response = client.post(
            "/ingest/confirm",
            json={
                "student_id": "../escape",
                "source_name": "jane.txt",
                "extractor": "text-parser",
                "courses": [{"extracted": rows[0]}],
            },
        )
        assert response.status_code == 400
        assert "unsafe student id" in response.json()["detail"]

    def test_reupload_does_not_duplicate(self, client) -> None:
        rows = upload(client).json()["extraction"]["courses"]
        payload = {
            "student_id": "jane",
            "source_name": "jane.txt",
            "extractor": "text-parser",
            "courses": [{"extracted": r} for r in rows],
        }
        client.post("/ingest/confirm", json=payload)
        second = client.post("/ingest/confirm", json=payload)
        assert second.json()["total_on_record"] == 3


class TestRecordEndpoints:
    def test_missing_record_is_404(self, client) -> None:
        assert client.get("/students/nobody/record").status_code == 404

    def test_delete_removes_the_record(self, client) -> None:
        rows = upload(client).json()["extraction"]["courses"]
        client.post(
            "/ingest/confirm",
            json={
                "student_id": "jane",
                "source_name": "jane.txt",
                "extractor": "text-parser",
                "courses": [{"extracted": r} for r in rows],
            },
        )
        assert client.delete("/students/jane/record").status_code == 200
        assert client.get("/students/jane/record").status_code == 404

    def test_delete_missing_record_is_404(self, client) -> None:
        assert client.delete("/students/nobody/record").status_code == 404


class TestExtractorInterface:
    def test_text_extractor_satisfies_the_protocol(self) -> None:
        assert isinstance(TextTranscriptExtractor(), TranscriptExtractor)

    def test_deterministic_parser_is_the_default(self) -> None:
        assert registered_extractors()[0] == "text-parser"

    def test_selects_by_content_type_and_by_suffix(self) -> None:
        assert select_extractor("a.txt", None).name == "text-parser"
        assert select_extractor("a.unknown", "text/plain").name == "text-parser"

    def test_unreadable_document_raises_with_guidance(self) -> None:
        with pytest.raises(UnsupportedDocument, match="Plain-text transcripts always work"):
            select_extractor("scan.png", "image/png")

    def test_ingestion_layer_does_not_import_the_llm_layer(self) -> None:
        """The separation this interface exists to enforce.

        A model-backed extractor registers itself from outside; app/ingestion/
        never reaches into app/llm/.
        """
        import pkgutil

        import app.ingestion

        for module in pkgutil.iter_modules(app.ingestion.__path__):
            source = (
                __import__("pathlib").Path(app.ingestion.__path__[0]) / f"{module.name}.py"
            ).read_text(encoding="utf-8")
            assert "app.llm" not in source, f"{module.name} imports the LLM layer"
            assert "app.advisor" not in source, f"{module.name} imports the advisor layer"

    def test_a_registered_extractor_never_preempts_the_parser(self) -> None:
        """Adding a model may widen what is accepted, never take over text."""

        class Greedy:
            name = "greedy-model"

            def can_handle(self, filename: str, content_type: str | None) -> bool:
                return True

            def extract(self, data, *, filename, program=None) -> ExtractionResult:
                return ExtractionResult(source_name=filename, extractor=self.name)

        register_extractor(Greedy())
        try:
            assert select_extractor("a.txt", "text/plain").name == "text-parser"
            assert select_extractor("scan.png", "image/png").name == "greedy-model"
        finally:
            from app.ingestion import extractor as module

            module._registry = [e for e in module._registry if e.name != "greedy-model"]

    def test_extract_transcript_reads_bytes(self) -> None:
        result = extract_transcript(TRANSCRIPT, filename="t.txt", content_type="text/plain")
        assert len(result.courses) == 3
        assert result.extractor == "text-parser"

    def test_undecodable_bytes_do_not_crash(self) -> None:
        result = extract_transcript(b"\xff\xfe\x00bad", filename="t.txt")
        assert result.courses == []
        assert result.warnings
