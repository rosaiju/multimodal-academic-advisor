"""Deterministic transcript parsing.

This is the baseline extractor and it uses no language model at all. Most academic
transcripts are tabular text, and a regex reads them exactly and repeatably. The
LLM extractor exists for the documents this cannot handle — scans, unusual layouts,
photographs — and lives behind the same interface in `app/ingestion/extractor.py`.

Preferring this path is a correctness decision, not a cost one: the same file
parsed twice here gives the same answer twice, which is not true of a model.

Confidence is assigned by explicit rules (see `_grade_row`), never by a
self-reported score. Every downgrade names the reason, so a student is told what
to check rather than shown a number.

*** NO LLM CODE in this module. ***
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from app.catalog.schema import GRADE_ORDER, NON_PASSING, Program
from app.ingestion.models import Confidence, ExtractedCourse, ExtractionResult

EXTRACTOR_NAME = "text-parser"

#: Grades a transcript may legitimately show.
KNOWN_GRADES: frozenset[str] = frozenset(GRADE_ORDER) | NON_PASSING | {"P", "S", "U", "CR"}

#: "Fall 2024", "FALL 2024", "Spring 2025" - a term header on its own line.
_TERM_LINE = re.compile(
    r"^\s*(FALL|SPRING|SUMMER|WINTER)\s+(\d{4})\s*$",
    re.IGNORECASE,
)
#: Same, but embedded in a longer header like "Term: Fall 2024 (Undergraduate)".
_TERM_INLINE = re.compile(r"\b(FALL|SPRING|SUMMER|WINTER)\s+(\d{4})\b", re.IGNORECASE)

#: A course row: subject, number, optional title, credits, grade - in that order.
#: Credits may be "4", "4.0" or "4.00"; the grade is the last token on the line.
_COURSE_ROW = re.compile(
    r"""^\s*
    (?P<subject>[A-Z]{2,5})     \s*[-\s]\s*
    (?P<number>\d{3})
    (?P<middle>.*?)
    (?P<credits>\d{1,2}(?:\.\d{1,2})?)  \s+
    (?P<grade>[A-Z]{1,2}[+-]?)
    \s*$""",
    re.VERBOSE,
)

#: Lines that look like course rows but are totals, not courses.
_NOT_A_COURSE = re.compile(
    r"\b(TERM|CUMULATIVE|SEMESTER|TOTAL|GPA|EARNED|ATTEMPTED|TRANSFER CREDIT)\b",
    re.IGNORECASE,
)


def normalise_code(subject: str, number: str) -> str:
    """'COSC 111' / 'cosc-111' -> 'COSC111', matching catalog course codes."""
    return f"{subject.strip().upper()}{number.strip()}"


def _clean_title(middle: str) -> str | None:
    title = re.sub(r"\s+", " ", middle).strip(" -\t")
    return title or None


def _grade_row(
    code: str,
    term: str | None,
    grade: str | None,
    credits: Decimal | None,
    program: Program | None,
) -> tuple[Confidence, list[str]]:
    """Decide how much a human needs to check this row, and say why.

    Rules are explicit and additive. Anything that could change which requirement a
    course satisfies - or whether it counts at all - downgrades the row.
    """
    issues: list[str] = []

    if term is None:
        issues.append("no term found for this course; which semester was it taken?")
    if grade is None:
        issues.append("no grade found")
    elif grade.rstrip("+-") not in KNOWN_GRADES:
        issues.append(f"unrecognised grade {grade!r}")
    if credits is None:
        issues.append("no credit value found")
    elif credits <= 0 or credits > 12:
        issues.append(f"unusual credit value {credits}")

    if program is not None and program.course(code) is None:
        issues.append(
            f"{code} is not in the catalog for this program - it may be a transfer "
            "course, an elective from another department, or a misread code"
        )

    if grade is None or credits is None:
        return Confidence.LOW, issues
    if issues:
        return Confidence.MEDIUM, issues
    return Confidence.HIGH, issues


def parse_transcript_text(
    text: str,
    *,
    source_name: str = "transcript.txt",
    program: Program | None = None,
) -> ExtractionResult:
    """Read course rows out of a plain-text transcript.

    `program` is optional. When given, course codes are checked against the catalog
    and unknown ones are flagged for review - checking, not correcting. The parser
    never rewrites a code to a catalog one that looks similar, because a wrong
    correction is far worse than an unrecognised row.
    """
    courses: list[ExtractedCourse] = []
    warnings: list[str] = []
    current_term: str | None = None

    lines = text.splitlines()
    for number, raw in enumerate(lines, start=1):
        line = raw.rstrip()
        if not line.strip():
            continue

        term_match = _TERM_LINE.match(line) or _TERM_INLINE.search(line)
        if term_match and not _COURSE_ROW.match(line):
            current_term = f"{term_match.group(1).title()} {term_match.group(2)}"
            continue

        if _NOT_A_COURSE.search(line):
            continue

        row = _COURSE_ROW.match(line)
        if not row:
            continue

        try:
            credits: Decimal | None = Decimal(row.group("credits"))
        except InvalidOperation:  # pragma: no cover - regex already constrains this
            credits = None

        code = normalise_code(row.group("subject"), row.group("number"))
        grade = row.group("grade").upper()
        confidence, issues = _grade_row(code, current_term, grade, credits, program)

        courses.append(
            ExtractedCourse(
                code=code,
                term=current_term,
                grade=grade,
                credits=credits,
                title=_clean_title(row.group("middle")),
                confidence=confidence,
                issues=issues,
                raw_line=line.strip(),
                line_number=number,
            )
        )

    if not courses:
        warnings.append(
            "No course rows were recognised. If this is a scanned or image-based "
            "transcript, the text parser cannot read it and it needs the "
            "document extractor instead."
        )
    if courses and all(c.term is None for c in courses):
        warnings.append(
            "No term headings were found, so no course has a term. The audit engine "
            "needs a term for every course before any of these can be confirmed."
        )

    seen: dict[str, int] = {}
    for c in courses:
        seen[c.code] = seen.get(c.code, 0) + 1
    repeats = sorted(code for code, n in seen.items() if n > 1)
    if repeats:
        warnings.append(
            f"Repeated course code(s): {', '.join(repeats)}. If these are retakes that "
            "is expected - the audit engine counts a retaken course once."
        )

    return ExtractionResult(
        source_name=source_name,
        extractor=EXTRACTOR_NAME,
        courses=courses,
        warnings=warnings,
    )
