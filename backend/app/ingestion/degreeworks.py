"""Parsing Ellucian DegreeWorks audit worksheets.

A DegreeWorks audit is not a transcript. A transcript lists courses under term
headings; an audit lists them under REQUIREMENT headings, with the term at the end
of each line:

    Calculus II                    MATH 242 CALCULUS II            B    4    SUMMER 2025
    Critical Thinking (CT)         PHIL 109 CRITICAL THINKING (CT) IP  (3)   SPRING 2026
    Freshman Composition           ENGL 101 FRESHMAN COMPOSITION I TRA  3    SUMMER 2024

Three consequences, each of which broke the linear transcript parser:

* The term is per-row, not a heading, so term tracking finds nothing.
* The order is GRADE then CREDITS, the reverse of a transcript row.
* Grades include transfer codes (TR, TRA, TRB, TRC) and in-progress (IP), whose
  credits are printed in parentheses because they have not been earned yet.

Two line types must be excluded even though they contain course-code-shaped text:

* "Still needed: 1 Class in ENGL 102 or 112" - a requirement, not coursework.
* "Satisfied by: ART113 - HISTORY OF ART I - BALTIMORE CITY COMM COLL" - the
  transfer SOURCE course. Its institution is useful; its code is not this course.

*** NO LLM CODE in this module. ***
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from app.catalog.schema import Program
from app.ingestion.models import Confidence, ExtractedCourse, ExtractionResult

EXTRACTOR_NAME = "degreeworks-parser"

#: Phrases that identify a DegreeWorks worksheet rather than a transcript.
_MARKERS = ("still needed:", "satisfied by:", "degreeworks", "degree works", "audit date")

#: A worksheet row: CODE, title, grade, credits, term. The term anchors the end.
_ROW = re.compile(
    r"""
    \b(?P<subject>[A-Z]{2,5})\s(?P<number>\d{3})\s+
    (?P<title>.+?)\s+
    (?P<grade>[A-Z]{1,3}[+-]?)\s+
    (?P<credits>\(?\d+(?:\.\d+)?\)?)\s+
    (?P<term>FALL|SPRING|SUMMER|WINTER)\s+(?P<year>\d{4})
    \s*$
    """,
    re.VERBOSE,
)

#: "Satisfied by: ART113 - HISTORY OF ART I - BALTIMORE CITY COMM COLL LBRTY"
_SATISFIED_BY = re.compile(r"satisfied by:\s*(?P<detail>.+)$", re.IGNORECASE)

#: Lines that name courses but describe what is OUTSTANDING, not what was taken.
_STILL_NEEDED = re.compile(r"still needed:", re.IGNORECASE)

#: Transfer credit: TR alone, or TR followed by the equated letter grade.
_TRANSFER = re.compile(r"^TR([A-F])?$")

#: In progress / registered. Credits appear parenthesised because none are earned.
_IN_PROGRESS = frozenset({"IP", "REG", "INC"})


def looks_like_degreeworks(text: str) -> bool:
    """True when this document is an audit worksheet rather than a transcript.

    Detection requires actual ROW EVIDENCE, never a keyword alone. A CV that
    mentions DegreeWorks in a project description would otherwise be routed here
    and then fail as unreadable, which is a confusing way to tell someone they
    uploaded the wrong file.
    """
    rows = sum(1 for line in text.splitlines() if _ROW.search(line.strip()))
    if rows == 0:
        return False
    lowered = text.lower()
    has_marker = any(marker in lowered for marker in _MARKERS)
    # One row plus a worksheet marker, or several rows on shape alone.
    return rows >= 3 or (rows >= 1 and has_marker)


def _clean_title(raw: str) -> str | None:
    """Strip the requirement label that precedes the course code on the same line."""
    title = re.sub(r"\s+", " ", raw).strip(" -\t")
    # Gen-ed tags such as "(IM)" or "(EC)" are categories, not part of the title.
    title = re.sub(r"\s*\([A-Z]{2}\)\s*$", "", title).strip()
    return title or None


def _institution_from(detail: str) -> str | None:
    """The transfer source is the last dash-separated field of a 'Satisfied by' line."""
    parts = [p.strip() for p in detail.split(" - ") if p.strip()]
    return parts[-1] if len(parts) >= 2 else None


def parse_degreeworks_text(
    text: str,
    *,
    source_name: str = "audit.pdf",
    program: Program | None = None,
) -> ExtractionResult:
    """Read coursework out of a DegreeWorks worksheet.

    Nothing here marks a course complete. Every row is an UNVERIFIED_EXTRACTION
    like any other, and in-progress rows are flagged so a student does not confirm
    a course they are still sitting in.
    """
    lines = [line.rstrip() for line in text.splitlines()]
    found: dict[str, ExtractedCourse] = {}
    duplicates: list[str] = []
    warnings: list[str] = []

    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or _STILL_NEEDED.search(stripped) or _SATISFIED_BY.search(stripped):
            continue

        row = _ROW.search(stripped)
        if not row:
            continue

        code = f"{row.group('subject').upper()}{row.group('number')}"
        raw_grade = row.group("grade").upper()
        raw_credits = row.group("credits")
        term = f"{row.group('term').title()} {row.group('year')}"

        issues: list[str] = []
        institution: str | None = None

        # Transfer credit: the grade carries the equated letter, or none at all.
        transfer = _TRANSFER.match(raw_grade)
        if transfer:
            grade = transfer.group(1)
            if grade is None:
                issues.append(
                    "transfer credit with no equated letter grade - confirm what "
                    "grade, if any, should count"
                )
            else:
                issues.append(f"transfer credit, equated as {grade}")
            # The following line usually names the sending institution.
            if index + 1 < len(lines):
                nxt = _SATISFIED_BY.search(lines[index + 1].strip())
                if nxt:
                    institution = _institution_from(nxt.group("detail"))
        elif raw_grade in _IN_PROGRESS:
            grade = raw_grade
            issues.append(
                "currently in progress - this has no final grade yet and will not "
                "count toward a requirement until it does"
            )
        else:
            grade = raw_grade

        # Parenthesised credits mean attempted, not earned.
        attempted_only = raw_credits.startswith("(")
        try:
            credits: Decimal | None = Decimal(raw_credits.strip("()"))
        except InvalidOperation:
            credits = None
        if attempted_only:
            issues.append("credits shown as attempted, not yet earned")

        if credits is not None and credits == 0:
            issues.append("recorded with 0 credits - usually a block transfer entry")

        if program is not None and program.course(code) is None:
            issues.append(
                f"{code} is not in the catalog for this program - it may be a "
                "transfer course, from another department, or misread"
            )

        # A DegreeWorks audit lists the SAME course under every requirement it
        # satisfies. Those repeats are one course, not a retake, so they are
        # collapsed here rather than shown to the student as duplicates.
        if code in found:
            duplicates.append(code)
            continue

        confidence = Confidence.HIGH
        if grade is None or credits is None:
            confidence = Confidence.LOW
        elif issues:
            confidence = Confidence.MEDIUM

        found[code] = ExtractedCourse(
            code=code,
            term=term,
            grade=grade,
            credits=credits,
            title=_clean_title(row.group("title")),
            institution=institution,
            transfer=bool(transfer),
            confidence=confidence,
            issues=issues,
            raw_line=stripped,
            line_number=index + 1,
        )

    courses = list(found.values())

    # Counted from the FINAL list, not from rows scanned. A duplicated row was
    # already counted once, and a warning that disagrees with the table beside it
    # makes a student doubt both numbers.
    in_progress_count = sum(1 for c in courses if c.grade in _IN_PROGRESS)
    transfer_count = sum(1 for c in courses if c.is_transfer)

    if duplicates:
        warnings.append(
            f"{len(duplicates)} row(s) repeated a course already listed "
            f"({', '.join(sorted(set(duplicates)))}). A DegreeWorks audit shows a "
            "course under every requirement it satisfies; each is counted once here."
        )
    if in_progress_count:
        warnings.append(
            f"{in_progress_count} course(s) are still in progress. They are listed so "
            "you can see them, but they carry no grade yet and will not satisfy a "
            "requirement until they do."
        )
    if transfer_count:
        warnings.append(
            f"{transfer_count} course(s) came from transfer credit. Check the equated "
            "grade and credit hours, which sometimes differ from the original."
        )

    return ExtractionResult(
        source_name=source_name,
        extractor=EXTRACTOR_NAME,
        courses=courses,
        warnings=warnings,
    )
