"""Deterministic PDF transcript reading.

A PDF exported by a registrar system contains the real text. Extracting it and
parsing it with a regex is exact and repeatable; handing the same document to a
vision model is strictly worse. So this extractor sits between the plain-text
parser and the model, and claims a PDF only when it can actually read words out
of it.

A scanned PDF - a photograph wrapped in a PDF container - has no text layer. This
declines those, and they fall through to the model-backed extractor.

*** NO LLM CODE in this module. ***
"""

from __future__ import annotations

import io
import logging
from pathlib import Path

from app.catalog.schema import Program
from app.ingestion.degreeworks import looks_like_degreeworks, parse_degreeworks_text
from app.ingestion.errors import DocumentNotReadable
from app.ingestion.models import ExtractionResult
from app.ingestion.parser import parse_transcript_text

logger = logging.getLogger(__name__)

EXTRACTOR_NAME = "pdf-text-parser"
PDF_MEDIA_TYPE = "application/pdf"
PDF_MAGIC = b"%PDF-"

#: Below this many characters a "text layer" is stray metadata, not a transcript.
MIN_USEFUL_CHARS = 40

#: Enough text that finding no courses means we failed to read it, not that the
#: document is empty.
SUBSTANTIAL_TEXT = 400

DEGREEWORKS_EXTRACTOR = "degreeworks-parser"


class PdfTextUnavailable(DocumentNotReadable):
    """This PDF has no usable text layer, or pdfplumber is not installed.

    A DocumentNotReadable, so a scanned PDF falls through to the model-backed
    reader instead of failing the upload.
    """


def pdf_text(data: bytes) -> str:
    """Pull the text layer out of a PDF. Raises when there is not one worth having."""
    try:
        import pdfplumber
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise PdfTextUnavailable(
            "pdfplumber is not installed; install the ingestion extra: "
            'pip install -e ".[ingestion]"'
        ) from exc

    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            pages = [page.extract_text() or "" for page in pdf.pages]
    except Exception as exc:
        raise PdfTextUnavailable(f"could not open the PDF: {exc}") from exc

    text = "\n".join(pages)
    if len(text.strip()) < MIN_USEFUL_CHARS:
        raise PdfTextUnavailable(
            "this PDF has no text layer - it is probably a scan, which needs the "
            "document reader instead"
        )
    return text


class PdfTranscriptExtractor:
    """Reads text-bearing PDFs exactly, and declines scans so a model can try."""

    name = EXTRACTOR_NAME

    def can_handle(self, filename: str, content_type: str | None) -> bool:
        return content_type == PDF_MEDIA_TYPE or Path(filename).suffix.lower() == ".pdf"

    def extract(
        self,
        data: bytes,
        *,
        filename: str,
        program: Program | None = None,
        content_type: str | None = None,
    ) -> ExtractionResult:
        text = pdf_text(data)
        result = parse_document_text(text, source_name=filename, program=program)

        # A PDF with plenty of text but no recognisable coursework has not been
        # READ, it has merely been opened. Reporting "0 courses found" would tell a
        # student their transcript is empty, which is a different and wrong claim.
        # Declining lets a document reader try instead of ending the upload here.
        if not result.courses and len(text.strip()) >= SUBSTANTIAL_TEXT:
            raise DocumentNotReadable(
                f"{filename}: this PDF has {len(text.strip())} characters of text but no "
                "course rows in a layout we recognise. It may be an unsupported "
                "worksheet format."
            )

        if result.extractor == DEGREEWORKS_EXTRACTOR:
            return result
        return result.model_copy(update={"extractor": self.name})


def looks_like_pdf(data: bytes) -> bool:
    return data[:5] == PDF_MAGIC


def parse_document_text(
    text: str,
    *,
    source_name: str,
    program: Program | None = None,
) -> ExtractionResult:
    """Route text to the parser that matches its layout.

    A DegreeWorks audit and a transcript are different documents with different
    shapes, and one regex cannot read both without becoming wrong about each.
    """
    if looks_like_degreeworks(text):
        return parse_degreeworks_text(text, source_name=source_name, program=program)
    return parse_transcript_text(text, source_name=source_name, program=program)
