"""The confirmation gate.

Extraction produces claims about a transcript. This module turns the ones a
student has explicitly agreed to into audit-ready coursework, and nothing else.

There is no "accept all high-confidence rows" helper here, and that absence is
deliberate. It would be one line, it would be used everywhere, and it would make
the confirmation step decorative - a course would land on a degree audit because a
regex was confident, not because a person looked. Confirming is per-row and
explicit.

A student may also CORRECT a row while confirming it: the extractor read a grade
wrong, or the term is missing. Corrections are recorded alongside what was
originally extracted, so a later dispute can see both.

*** NO LLM CODE. This module decides what counts toward a degree. ***
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.audit.record import CompletedCourse, StudentRecord
from app.ingestion.models import ExtractedCourse, ExtractionResult
from app.schemas.provenance import Provenance


class Correction(BaseModel):
    """A field the student changed while confirming a row."""

    model_config = ConfigDict(frozen=True)

    field: str
    extracted: str | None = Field(description="What the extractor read, verbatim")
    corrected: str = Field(description="What the student says it should be")


class ConfirmedCourse(BaseModel):
    """One course a student has explicitly accepted onto their record."""

    model_config = ConfigDict(frozen=True)

    course: CompletedCourse
    source_name: str = Field(description="Which document this came from")
    extractor: str
    raw_line: str = Field(description="The transcript line, kept for disputes")
    corrections: list[Correction] = Field(default_factory=list)

    @property
    def was_corrected(self) -> bool:
        return bool(self.corrections)


class ConfirmationError(ValueError):
    """Raised when a row cannot be confirmed as asked."""


def confirm_course(
    extracted: ExtractedCourse,
    result: ExtractionResult,
    *,
    term: str | None = None,
    grade: str | None = None,
    credits: Decimal | None = None,
    institution: str | None = None,
) -> ConfirmedCourse:
    """Accept one extracted row, optionally correcting fields the student fixed.

    Passing a field overrides what was extracted and records the change. Omitting
    one keeps the extracted value - and if that value is missing, this raises
    rather than inventing a default, because a guessed grade decides a degree.
    """
    corrections: list[Correction] = []
    for name, supplied, original in (
        ("term", term, extracted.term),
        ("grade", grade, extracted.grade),
        ("credits", str(credits) if credits is not None else None, extracted.credits),
    ):
        if supplied is not None and str(supplied) != str(original):
            corrections.append(
                Correction(
                    field=name,
                    extracted=str(original) if original is not None else None,
                    corrected=str(supplied),
                )
            )

    final_institution = institution if institution is not None else extracted.institution
    final_term = term if term is not None else extracted.term
    final_grade = grade if grade is not None else extracted.grade
    final_credits = credits if credits is not None else extracted.credits

    missing = [
        name
        for name, value in (
            ("term", final_term),
            ("grade", final_grade),
            ("credits", final_credits),
        )
        if value is None
    ]
    if missing:
        raise ConfirmationError(
            f"{extracted.code}: cannot confirm without {', '.join(missing)}. "
            "The transcript did not supply this, so the student must."
        )

    assert final_term is not None and final_grade is not None and final_credits is not None
    return ConfirmedCourse(
        course=CompletedCourse(
            code=extracted.code,
            term=final_term,
            grade=final_grade,
            credits=final_credits,
            provenance=Provenance.STUDENT_CONFIRMED,
            institution=final_institution,
        ),
        source_name=result.source_name,
        extractor=result.extractor,
        raw_line=extracted.raw_line,
        corrections=corrections,
    )


def build_student_record(student_id: str, confirmed: list[ConfirmedCourse]) -> StudentRecord:
    """Assemble the record the audit engine will consume.

    Every course in it is STUDENT_CONFIRMED by construction. The engine filters on
    provenance anyway - belt and braces, because these two checks protect the same
    thing from different directions.
    """
    for entry in confirmed:
        if entry.course.provenance is not Provenance.STUDENT_CONFIRMED:
            raise ConfirmationError(
                f"{entry.course.code}: provenance is {entry.course.provenance.value!r}, "
                "which did not come through confirmation"
            )
    return StudentRecord(student_id=student_id, completed=[e.course for e in confirmed])
