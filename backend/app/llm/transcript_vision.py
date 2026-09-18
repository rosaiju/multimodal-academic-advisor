"""Model-backed transcript extraction for scans, photos and PDFs.

This is the fallback for documents the deterministic parser cannot read. It
satisfies the same `TranscriptExtractor` protocol and produces the same
`ExtractionResult`, so nothing downstream knows or cares that a model was involved.

Three rules govern this file:

1. **Output is never trusted.** Every row is `UNVERIFIED_EXTRACTION`, like all
   extraction, and must pass through the confirmation gate before it can touch a
   degree audit.

2. **A model row is never HIGH confidence.** The deterministic parser earns HIGH
   because a regex that matched a line will match it identically forever. A model
   reading a photograph is making a claim about pixels, and a clean-looking claim
   is still a claim. Capping at MEDIUM puts every row in `needs_review`, which is
   what "never silently auto-confirm" means in practice rather than in a comment.

3. **The model's output is parsed defensively.** It is untrusted input: fields may
   be missing, malformed, or invented. Anything unusable is dropped with a warning
   naming the row, never coerced into something plausible.

*** This module is imported by NOTHING in app/catalog/, app/audit/ or
    app/ingestion/. It registers itself with the ingestion layer from outside. ***
"""

from __future__ import annotations

import json
import logging
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

from app.catalog.schema import Program
from app.ingestion.models import Confidence, ExtractedCourse, ExtractionResult
from app.llm.provider import VISION_MEDIA_TYPES, ProviderError, VisionProvider

logger = logging.getLogger(__name__)

EXTRACTOR_PREFIX = "vision"

#: Suffixes this extractor accepts when the content type is unhelpful.
VISION_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf"})

PROMPT = """\
You are reading a student's academic transcript. Transcribe what is printed. Do \
not infer, complete, or correct anything.

Return ONLY a JSON object, no prose, in exactly this shape:

{
  "institution": "name printed on the document, or null",
  "courses": [
    {
      "code": "course code exactly as printed, e.g. 'COSC 111'",
      "title": "course title as printed, or null",
      "term": "term this course was taken, e.g. 'Fall 2024', or null",
      "grade": "letter grade as printed, or null",
      "credits": "credit hours as a number, or null",
      "institution": "institution for this row if it differs, else null",
      "raw_line": "the line as printed, verbatim"
    }
  ]
}

Rules you must follow:
- If a field is not legible or not present, use null. Never guess a value.
- Never invent a course that is not printed on the document.
- Do not expand abbreviations or fix apparent typos in course codes.
- Ignore GPA lines, credit totals, honours notes and headers.
- Include every course row you can read, including failures and withdrawals.
"""


def _extract_json(text: str) -> dict:
    """Pull the JSON object out of a model response.

    Models wrap JSON in prose or fences despite instructions. Tolerating that is
    fine; tolerating malformed JSON is not, because the alternative to failing here
    is guessing what a transcript said.
    """
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else None
    if candidate is None:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("no JSON object found in the model response")
        candidate = text[start : end + 1]
    parsed = json.loads(candidate)
    if not isinstance(parsed, dict):
        raise ValueError(f"expected a JSON object, got {type(parsed).__name__}")
    return parsed


def _clean(value: object) -> str | None:
    """Normalise a model-supplied string, treating null-ish text as absent."""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"null", "none", "n/a", "unknown", "-", "--"}:
        return None
    return text


def _credits(value: object) -> Decimal | None:
    text = _clean(value)
    if text is None:
        return None
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None


def _normalise_code(raw: str) -> str | None:
    """'COSC 111' -> 'COSC111'. Returns None if it is not shaped like a course code.

    Never repairs. A code that does not match is reported as unreadable rather than
    bent into something that does, because a wrong code puts a course on a record
    the student never took.
    """
    match = re.match(r"^\s*([A-Za-z]{2,5})\s*[-\s]?\s*(\d{3})\s*$", raw)
    if not match:
        return None
    return f"{match.group(1).upper()}{match.group(2)}"


def _row_issues(
    code: str,
    term: str | None,
    grade: str | None,
    credits: Decimal | None,
    program: Program | None,
) -> list[str]:
    issues = ["read from a scanned or photographed document - please check it"]
    if term is None:
        issues.append("no term could be read; which semester was this?")
    if grade is None:
        issues.append("no grade could be read")
    if credits is None:
        issues.append("no credit value could be read")
    elif credits <= 0 or credits > 12:
        issues.append(f"unusual credit value {credits}")
    if program is not None and program.course(code) is None:
        issues.append(
            f"{code} is not in the catalog for this program - it may be a transfer "
            "course, from another department, or misread"
        )
    return issues


class VisionTranscriptExtractor:
    """Reads transcripts a regex cannot: scans, photographs, PDFs."""

    def __init__(self, provider: VisionProvider, *, max_tokens: int = 4096) -> None:
        self._provider = provider
        self._max_tokens = max_tokens
        self.name = f"{EXTRACTOR_PREFIX}:{provider.name}"

    def can_handle(self, filename: str, content_type: str | None) -> bool:
        if content_type in VISION_MEDIA_TYPES:
            return True
        if content_type and content_type.startswith("image/"):
            return True
        return Path(filename).suffix.lower() in VISION_SUFFIXES

    @staticmethod
    def _media_type(filename: str, content_type: str | None) -> str:
        if content_type in VISION_MEDIA_TYPES:
            return content_type
        suffix = Path(filename).suffix.lower()
        return {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".webp": "image/webp",
            ".pdf": "application/pdf",
        }.get(suffix, "image/png")

    def extract(
        self,
        data: bytes,
        *,
        filename: str,
        program: Program | None = None,
        content_type: str | None = None,
    ) -> ExtractionResult:
        media_type = self._media_type(filename, content_type)
        try:
            raw = self._provider.read_document(
                data,
                media_type=media_type,
                prompt=PROMPT,
                max_tokens=self._max_tokens,
            )
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(f"{self.name}: reading {filename!r} failed: {exc}") from exc

        return self._build(raw, filename=filename, program=program)

    def _build(self, raw: str, *, filename: str, program: Program | None) -> ExtractionResult:
        warnings: list[str] = []
        try:
            payload = _extract_json(raw)
        except (ValueError, json.JSONDecodeError) as exc:
            return ExtractionResult(
                source_name=filename,
                extractor=self.name,
                warnings=[
                    f"the document reader returned something unreadable ({exc}). "
                    "Nothing was extracted. Try uploading a clearer image, or a "
                    "plain-text transcript."
                ],
            )

        document_institution = _clean(payload.get("institution"))
        rows = payload.get("courses")
        if not isinstance(rows, list):
            return ExtractionResult(
                source_name=filename,
                extractor=self.name,
                institution=document_institution,
                warnings=["the document reader returned no course list."],
            )

        courses: list[ExtractedCourse] = []
        for index, row in enumerate(rows, start=1):
            if not isinstance(row, dict):
                warnings.append(f"row {index}: not a course object; skipped")
                continue

            raw_code = _clean(row.get("code"))
            if raw_code is None:
                warnings.append(f"row {index}: no course code was readable; skipped")
                continue
            code = _normalise_code(raw_code)
            if code is None:
                warnings.append(
                    f"row {index}: {raw_code!r} is not shaped like a course code; skipped "
                    "rather than guessed"
                )
                continue

            term = _clean(row.get("term"))
            grade = _clean(row.get("grade"))
            credits = _credits(row.get("credits"))
            issues = _row_issues(code, term, grade, credits, program)

            courses.append(
                ExtractedCourse(
                    code=code,
                    term=term,
                    grade=grade,
                    credits=credits,
                    title=_clean(row.get("title")),
                    institution=_clean(row.get("institution")) or document_institution,
                    # Never HIGH. See rule 2 in the module docstring.
                    confidence=(
                        Confidence.LOW if (grade is None or credits is None) else Confidence.MEDIUM
                    ),
                    issues=issues,
                    raw_line=_clean(row.get("raw_line")) or raw_code,
                    line_number=None,
                )
            )

        if not courses:
            warnings.append(
                "No course rows could be read from this document. A clearer photo, "
                "or a plain-text transcript, will work better."
            )
        else:
            warnings.append(
                f"All {len(courses)} row(s) were read by a document reader, not parsed "
                "exactly. Every one needs checking before it counts toward your degree."
            )

        return ExtractionResult(
            source_name=filename,
            extractor=self.name,
            institution=document_institution,
            courses=courses,
            warnings=warnings,
        )
