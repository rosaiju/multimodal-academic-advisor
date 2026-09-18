"""Deterministic PDF transcript reading, and the fallback chain.

A registrar-exported PDF contains real text. Reading it with a regex is exact and
repeatable, so it must never be handed to a vision model just because it is a PDF.
A scanned PDF has no text layer and must fall through to the model instead of
failing the upload.

PDFs here are built in-process with no third-party writer, so the fixtures are
synthetic and the tests need no binary assets in the repo.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.catalog.loader import load_program
from app.catalog.schema import Program
from app.ingestion.errors import DocumentNotReadable, UnsupportedDocument
from app.ingestion.extractor import (
    TranscriptExtractor,
    candidate_extractors,
    extract_transcript,
    register_extractor,
    registered_extractors,
)
from app.ingestion.models import Confidence
from app.ingestion.pdf import (
    MIN_USEFUL_CHARS,
    PdfTextUnavailable,
    PdfTranscriptExtractor,
    looks_like_pdf,
    pdf_text,
)
from app.llm.transcript_vision import VisionTranscriptExtractor
from app.schemas.provenance import Provenance

MORGAN = Path(__file__).resolve().parents[2] / "data" / "catalog" / "morgan_cosc_bs_2026_2028.yaml"

TRANSCRIPT_LINES = [
    "MORGAN STATE UNIVERSITY",
    "Fall 2024",
    "COSC 111  Introduction to Computer Science I   4.00  A",
    "ENGL 101  Composition I                       3.00  B",
    "Spring 2025",
    "COSC 112  Introduction to Computer Science II  4.00  A",
]


def make_pdf(lines: list[str], font_size: int = 10) -> bytes:
    """A minimal, valid, text-bearing PDF. No third-party writer needed."""
    ops = ["BT", f"/F1 {font_size} Tf", "1 0 0 1 40 750 Tm", f"{font_size + 4} TL"]
    for line in lines:
        escaped = line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        ops.append(f"({escaped}) Tj T*")
    ops.append("ET")
    content = "\n".join(ops).encode("latin-1")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n" f"startxref\n{xref_at}\n%%EOF\n"
    ).encode()
    return bytes(out)


@pytest.fixture(scope="module")
def morgan() -> Program:
    return load_program(MORGAN)


@pytest.fixture(scope="module")
def transcript_pdf() -> bytes:
    return make_pdf(TRANSCRIPT_LINES)


@pytest.fixture(scope="module")
def scanned_pdf() -> bytes:
    """A PDF with no meaningful text layer, like a photographed transcript."""
    return make_pdf(["."])


class TestPdfTextExtraction:
    def test_reads_the_text_layer(self, transcript_pdf) -> None:
        text = pdf_text(transcript_pdf)
        assert "COSC 111" in text
        assert "Fall 2024" in text

    def test_rejects_a_pdf_without_a_useful_text_layer(self, scanned_pdf) -> None:
        with pytest.raises(PdfTextUnavailable, match="no text layer"):
            pdf_text(scanned_pdf)

    def test_rejects_a_file_that_is_not_a_pdf(self) -> None:
        with pytest.raises(PdfTextUnavailable, match="could not open"):
            pdf_text(b"this is not a pdf at all")

    def test_magic_number_helper(self, transcript_pdf) -> None:
        assert looks_like_pdf(transcript_pdf)
        assert not looks_like_pdf(b"plain text")

    def test_threshold_is_a_named_constant(self) -> None:
        assert MIN_USEFUL_CHARS > 0


class TestPdfExtractor:
    def test_satisfies_the_protocol(self) -> None:
        assert isinstance(PdfTranscriptExtractor(), TranscriptExtractor)

    def test_parses_courses_out_of_a_pdf(self, transcript_pdf, morgan) -> None:
        result = PdfTranscriptExtractor().extract(transcript_pdf, filename="t.pdf", program=morgan)
        assert [c.code for c in result.courses] == ["COSC111", "ENGL101", "COSC112"]

    def test_assigns_terms_from_headings(self, transcript_pdf, morgan) -> None:
        result = PdfTranscriptExtractor().extract(transcript_pdf, filename="t.pdf", program=morgan)
        by_code = {c.code: c for c in result.courses}
        assert by_code["COSC111"].term == "Fall 2024"
        assert by_code["COSC112"].term == "Spring 2025"

    def test_rows_are_high_confidence_because_the_text_is_exact(
        self, transcript_pdf, morgan
    ) -> None:
        """Unlike a model read, a PDF text layer is the real characters."""
        result = PdfTranscriptExtractor().extract(transcript_pdf, filename="t.pdf", program=morgan)
        assert all(c.confidence is Confidence.HIGH for c in result.courses)

    def test_output_is_still_unverified(self, transcript_pdf, morgan) -> None:
        """Exact text is not the same as confirmed by a student."""
        result = PdfTranscriptExtractor().extract(transcript_pdf, filename="t.pdf", program=morgan)
        assert all(c.provenance is Provenance.UNVERIFIED_EXTRACTION for c in result.courses)

    def test_records_itself_as_the_extractor(self, transcript_pdf, morgan) -> None:
        result = PdfTranscriptExtractor().extract(transcript_pdf, filename="t.pdf", program=morgan)
        assert result.extractor == "pdf-text-parser"

    def test_declines_a_scan(self, scanned_pdf, morgan) -> None:
        with pytest.raises(DocumentNotReadable):
            PdfTranscriptExtractor().extract(scanned_pdf, filename="s.pdf", program=morgan)

    def test_claims_pdfs_by_type_and_suffix(self) -> None:
        extractor = PdfTranscriptExtractor()
        assert extractor.can_handle("t.pdf", None)
        assert extractor.can_handle("t", "application/pdf")
        assert not extractor.can_handle("t.txt", "text/plain")


class TestFallbackChain:
    def test_pdf_reader_is_registered_ahead_of_any_model(self) -> None:
        names = registered_extractors()
        assert names[0] == "text-parser"
        assert "pdf-text-parser" in names

    def test_text_pdf_goes_to_the_deterministic_reader(self, transcript_pdf, morgan) -> None:
        """A model must not be used on a document with exact text."""
        result = extract_transcript(
            transcript_pdf, filename="t.pdf", content_type="application/pdf", program=morgan
        )
        assert result.extractor == "pdf-text-parser"
        assert len(result.courses) == 3

    def test_scanned_pdf_falls_through_to_the_model(self, scanned_pdf, morgan) -> None:
        """The PDF reader declining must not fail the upload."""

        class StubProvider:
            name = "stub"

            def read_document(self, data, *, media_type, prompt, max_tokens=4096):
                return json.dumps(
                    {
                        "institution": "Morgan State University",
                        "courses": [
                            {
                                "code": "COSC 111",
                                "term": "Fall 2024",
                                "grade": "A",
                                "credits": "4",
                                "raw_line": "COSC 111 ... 4 A",
                            }
                        ],
                    }
                )

        vision = VisionTranscriptExtractor(StubProvider())
        register_extractor(vision)
        try:
            result = extract_transcript(
                scanned_pdf,
                filename="s.pdf",
                content_type="application/pdf",
                program=morgan,
            )
            assert result.extractor == vision.name
            assert [c.code for c in result.courses] == ["COSC111"]
            # Still a model read, so still capped below HIGH.
            assert result.courses[0].confidence is not Confidence.HIGH
        finally:
            from app.ingestion import extractor as module

            module._registry = [e for e in module._registry if e.name != vision.name]

    def test_scanned_pdf_without_a_model_reports_why(self, scanned_pdf, morgan) -> None:
        with pytest.raises(UnsupportedDocument, match="could not be read"):
            extract_transcript(
                scanned_pdf,
                filename="s.pdf",
                content_type="application/pdf",
                program=morgan,
            )

    def test_candidates_are_listed_in_preference_order(self) -> None:
        candidates = candidate_extractors("t.pdf", "application/pdf")
        assert [c.name for c in candidates][0] == "pdf-text-parser"

    def test_text_file_never_reaches_the_pdf_reader(self) -> None:
        candidates = candidate_extractors("t.txt", "text/plain")
        assert [c.name for c in candidates] == ["text-parser"]

    def test_importing_pdf_first_does_not_break(self) -> None:
        """Regression: a circular import between pdf.py and extractor.py.

        It only surfaced when app.ingestion.pdf was imported before
        app.ingestion.extractor, which no test happened to do.
        """
        import importlib
        import subprocess
        import sys

        result = subprocess.run(
            [sys.executable, "-c", "import app.ingestion.pdf; print('ok')"],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).resolve().parents[1]),
        )
        assert result.returncode == 0, result.stderr
        assert "ok" in result.stdout
        importlib.import_module("app.ingestion.extractor")
