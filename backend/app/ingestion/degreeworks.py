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

Three more things the format does that a course-per-code reader gets wrong:

* Lower-level transfer credit is held in BUCKETS - "COSC 116TR COSC LWR LVL
  ELECTIVE" - and one bucket code stands for many separate courses, each with its
  own term, credits and sending course. They are not repeats of each other.
* A term can be "WINTER MINI-MESTER", and the year then wraps to the next line.
* The "Satisfied by" value is split across up to four lines when the PDF column is
  narrow, with the label stranded on a line of its own.

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
#:
#: The number carries an optional TR suffix. "COSC 116TR" is a BLOCK TRANSFER
#: bucket - a holding pen for lower-level credit that equates to no single Morgan
#: course - and an audit can list a dozen of them. Requiring a bare three-digit
#: number dropped every one, which is how 24 credits of transfer work went missing.
_ROW = re.compile(
    r"""
    \b(?P<subject>[A-Z]{2,5})\s(?P<number>\d{3})(?P<suffix>TR)?\s+
    (?P<title>.+?)\s+
    (?P<grade>[A-Z]{1,3}[+-]?)\s+
    (?P<credits>\(?\d+(?:\.\d+)?\)?)\s+
    (?P<term>FALL|SPRING|SUMMER|WINTER)(?P<minimester>\s+MINI-MESTER)?\s+(?P<year>\d{4})
    \s*$
    """,
    re.VERBOSE,
)

#: A row whose term wrapped: "... 0.5 WINTER MINI-MESTER" with "2023" on the next
#: line. The year is what anchors _ROW, so without rejoining these the row is lost.
_TERM_WITHOUT_YEAR = re.compile(r"\b(?:FALL|SPRING|SUMMER|WINTER)(?:\s+MINI-MESTER)?\s*$")

#: A continuation line holding nothing but the wrapped year.
_YEAR_ONLY = re.compile(r"^\d{4}$")

#: "Satisfied by: ART113 - HISTORY OF ART I - BALTIMORE CITY COMM COLL LBRTY"
#:
#: The detail is `.*`, not `.+`: a narrow PDF column strands the label on a line of
#: its own with the value above and below it. Requiring a value on the same line
#: meant those lines matched nothing, so they were neither excluded as labels nor
#: read as sources.
_SATISFIED_BY = re.compile(r"satisfied by:\s*(?P<detail>.*)$", re.IGNORECASE)

#: Lines that name courses but describe what is OUTSTANDING, not what was taken.
_STILL_NEEDED = re.compile(r"still needed:", re.IGNORECASE)

#: Transfer credit: TR alone, or TR followed by the equated letter grade.
_TRANSFER = re.compile(r"^TR([A-F])?$")

#: In progress / registered. Credits appear parenthesised because none are earned.
_IN_PROGRESS = frozenset({"IP", "REG", "INC"})

#: How far past a row to look for its "Satisfied by" source. The PDF text layer
#: splits that field across up to four lines when the column is narrow.
_SOURCE_LOOKAHEAD = 4


def _rejoin_wrapped_terms(lines: list[str]) -> list[tuple[int, str]]:
    """Pair each line with its original index, gluing a wrapped year back on.

    Returns (original_line_number, text) so `line_number` still points a student at
    the line the row STARTS on, not at the stray year underneath it.
    """
    joined: list[tuple[int, str]] = []
    index = 0
    while index < len(lines):
        text = lines[index].strip()
        if (
            text
            and _TERM_WITHOUT_YEAR.search(text)
            and index + 1 < len(lines)
            and _YEAR_ONLY.match(lines[index + 1].strip())
        ):
            joined.append((index, f"{text} {lines[index + 1].strip()}"))
            index += 2
            continue
        joined.append((index, text))
        index += 1
    return joined


def _source_detail(rows: list[tuple[int, str]], position: int) -> str | None:
    """The 'Satisfied by' source for the row at `position`, however it is wrapped.

    DegreeWorks prints one line - "Satisfied by: ART113 - HISTORY OF ART I - BALTIMORE
    CITY COMM COLL" - but a narrow column makes the PDF text layer emit the label
    alone with its value split above and below it. Reading only the next line found
    the first form and missed the second, which is why most transfer rows came back
    with no institution.
    """
    head: str | None = None

    for offset in range(1, _SOURCE_LOOKAHEAD + 1):
        cursor = position + offset
        if cursor >= len(rows):
            break
        text = rows[cursor][1]
        if not text or _ROW.search(text) or _STILL_NEEDED.search(text):
            break

        marker = _SATISFIED_BY.search(text)
        if marker is None:
            # Keep only the NEAREST preceding line. A repeated page header sits
            # between a row and its source often enough that breaking here would
            # lose the institution, and keeping every line swept the next section's
            # heading - and a masked student id - into the field.
            head = text
            continue

        inline = marker.group("detail").strip()
        if inline:
            # The whole value is on the label's own line; nothing else belongs.
            return inline

        # The label stands alone: its value wrapped around it, one line each side.
        tail = rows[cursor + 1][1] if cursor + 1 < len(rows) else ""
        if tail and (_ROW.search(tail) or _SATISFIED_BY.search(tail)):
            tail = ""
        detail = re.sub(r"\s+", " ", f"{head or ''} {tail}").strip()
        return detail or None

    return None


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
    rows = _rejoin_wrapped_terms([line.rstrip() for line in text.splitlines()])
    found: dict[tuple[object, ...], ExtractedCourse] = {}
    duplicates: list[str] = []
    warnings: list[str] = []

    for position, (index, stripped) in enumerate(rows):
        if not stripped or _STILL_NEEDED.search(stripped) or _SATISFIED_BY.search(stripped):
            continue

        row = _ROW.search(stripped)
        if not row:
            continue

        suffix = (row.group("suffix") or "").upper()
        code = f"{row.group('subject').upper()}{row.group('number')}{suffix}"
        raw_grade = row.group("grade").upper()
        raw_credits = row.group("credits")
        season = row.group("term").title()
        if row.group("minimester"):
            season = f"{season} Mini-Mester"
        term = f"{season} {row.group('year')}"

        issues: list[str] = []
        institution: str | None = None
        source = _source_detail(rows, position)

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
            if source is not None:
                institution = _institution_from(source)
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

        if suffix:
            # Saying "COSC116TR is not in the catalog" invites a student to correct a
            # code that is not wrong. It is a bucket, and the useful fact is what it
            # can and cannot do: carry credit, but satisfy no named requirement.
            issues.append(
                "block transfer credit held against a lower-level elective bucket, "
                "not a specific course - it carries credit but satisfies no named "
                "requirement"
            )
        elif program is not None and program.course(code) is None:
            issues.append(
                f"{code} is not in the catalog for this program - it may be a "
                "transfer course, from another department, or misread"
            )

        # A DegreeWorks audit lists the SAME course under every requirement it
        # satisfies. Those repeats are one course, not a retake, so they are
        # collapsed here.
        #
        # The identity is the whole row, NOT the code alone. One elective bucket
        # code stands for many separate transfer courses, each with its own term,
        # credits and sending course; keying on the code collapsed fourteen of them
        # into one and silently discarded the rest of the credit.
        identity = (code, term, grade, credits, source)
        if identity in found:
            duplicates.append(code)
            continue

        confidence = Confidence.HIGH
        if grade is None or credits is None:
            confidence = Confidence.LOW
        elif issues:
            confidence = Confidence.MEDIUM

        found[identity] = ExtractedCourse(
            code=code,
            term=term,
            grade=grade,
            credits=credits,
            title=_clean_title(row.group("title")),
            institution=institution,
            transfer=bool(transfer),
            source_reference=source,
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
