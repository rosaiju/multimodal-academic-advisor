"""The extractor interface.

A transcript may arrive as text, as a PDF, or as a photograph of a printout. Each
needs a different reader, but every one of them produces the same thing: an
`ExtractionResult` full of `UNVERIFIED_EXTRACTION` rows that a student must confirm.

That uniformity is the point. Swapping a regex for a vision model changes how well
the document is read; it changes nothing about what the output is permitted to do.

**Where the LLM lives.** Nothing in `app/ingestion/` imports `app/llm/`. The model-
backed extractor is registered from the outside, by whoever wires the application
together, so the deterministic path stays independently testable and the dependency
points one way only:

    app/llm/transcript_vision.py  ->  registers itself here
    app/ingestion/               ->  knows only this Protocol

`select_extractor` prefers the deterministic reader whenever it can handle the
input, and falls back to a registered model only for documents it cannot read.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Protocol, runtime_checkable

from app.catalog.schema import Program
from app.ingestion.models import ExtractionResult
from app.ingestion.parser import EXTRACTOR_NAME, parse_transcript_text

logger = logging.getLogger(__name__)

#: Suffixes the deterministic parser can read directly.
TEXT_SUFFIXES = frozenset({".txt", ".text", ".csv", ".md"})


@runtime_checkable
class TranscriptExtractor(Protocol):
    """Anything that can turn a transcript document into extracted rows."""

    name: str

    def can_handle(self, filename: str, content_type: str | None) -> bool: ...

    def extract(
        self,
        data: bytes,
        *,
        filename: str,
        program: Program | None = None,
        content_type: str | None = None,
    ) -> ExtractionResult: ...


class TextTranscriptExtractor:
    """Deterministic reader for text transcripts. The default and the preferred one."""

    name = EXTRACTOR_NAME

    def can_handle(self, filename: str, content_type: str | None) -> bool:
        if content_type and content_type.startswith("text/"):
            return True
        return Path(filename).suffix.lower() in TEXT_SUFFIXES

    def extract(
        self,
        data: bytes,
        *,
        filename: str,
        program: Program | None = None,
        content_type: str | None = None,
    ) -> ExtractionResult:
        # Transcripts come from every registrar system imaginable; refusing on an
        # odd byte helps nobody, so undecodable bytes are replaced and the parser
        # simply will not match those lines.
        text = data.decode("utf-8", errors="replace")
        return parse_transcript_text(text, source_name=filename, program=program)


class UnsupportedDocument(RuntimeError):
    """No registered extractor can read this document."""


_registry: list[TranscriptExtractor] = [TextTranscriptExtractor()]


def register_extractor(extractor: TranscriptExtractor) -> None:
    """Add an extractor, e.g. a model-backed one from `app/llm/`.

    Appended, never prepended: the deterministic parser keeps first refusal, so
    adding a model can widen what the system accepts but cannot quietly take over
    documents that were being read exactly.
    """
    _registry.append(extractor)
    logger.info("registered transcript extractor %r", extractor.name)


def registered_extractors() -> list[str]:
    return [e.name for e in _registry]


def select_extractor(filename: str, content_type: str | None = None) -> TranscriptExtractor:
    for extractor in _registry:
        if extractor.can_handle(filename, content_type):
            return extractor
    raise UnsupportedDocument(
        f"no extractor can read {filename!r} (type {content_type!r}). "
        f"Available: {', '.join(registered_extractors())}. "
        "Plain-text transcripts always work; PDFs and images need the document "
        "extractor, which is not configured."
    )


def extract_transcript(
    data: bytes,
    *,
    filename: str,
    content_type: str | None = None,
    program: Program | None = None,
) -> ExtractionResult:
    """Read a transcript with whichever extractor fits. Output is never trusted."""
    return select_extractor(filename, content_type).extract(
        data, filename=filename, program=program, content_type=content_type
    )
