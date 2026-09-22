"""What a transcript extraction produces.

The whole point of this layer is that its output is NOT trusted. A transcript is
parsed — or read by a model — into `ExtractedCourse` rows tagged
`UNVERIFIED_EXTRACTION`, and `tests/test_no_llm_in_engine.py` plus
`Provenance.TRUSTED_FOR_AUDIT` guarantee those rows cannot reach a degree audit.

A student confirms them first. Confirmation is the only path from "we think your
transcript says this" to "this counts toward your degree", and it is deliberately
an explicit step rather than a default.

*** This module is data only. It performs no extraction and imports no LLM. ***
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.audit.record import CompletedCourse
from app.schemas.provenance import Provenance


class Confidence(StrEnum):
    """How much of a row the extractor actually recognised.

    Deliberately three coarse buckets rather than a float. A number like 0.82
    invites a UI to hide anything above a threshold, and every hidden row is a
    course silently added to or dropped from a student's degree audit. These
    buckets map to what a person should DO, not to a model's self-report.
    """

    #: Every field parsed cleanly and the course exists in the catalog.
    HIGH = "high"
    #: Parsed, but something needs a human eye — unknown course code, odd credits.
    MEDIUM = "medium"
    #: Recognisably a course row, but a required field is missing or ambiguous.
    LOW = "low"


class ExtractedCourse(BaseModel):
    """One course row read off a transcript. Never audit-ready as-is."""

    model_config = ConfigDict(frozen=True)

    code: str = Field(description="Normalised, e.g. 'COSC111'")
    term: str | None = Field(default=None, description="e.g. 'Fall 2024'; None if not found")
    grade: str | None = None
    credits: Decimal | None = None

    title: str | None = Field(default=None, description="As printed, for the student to check")
    institution: str | None = Field(
        default=None,
        description="Where the course was taken, when the document says. Feeds the "
        "residency requirement, which is NEEDS_ADVISOR without it.",
    )
    confidence: Confidence = Confidence.LOW
    issues: list[str] = Field(
        default_factory=list,
        description="Why this row is not HIGH confidence, in words a student can act on.",
    )
    raw_line: str = Field(description="The source line verbatim, so a human can check it.")
    line_number: int | None = None

    #: Fixed. An extraction is never anything else, whatever produced it.
    provenance: Provenance = Provenance.UNVERIFIED_EXTRACTION

    @property
    def is_complete(self) -> bool:
        """True when the row has everything the audit engine would need."""
        return all((self.code, self.term, self.grade, self.credits is not None))

    def confirm(self, *, institution: str | None = None) -> CompletedCourse:
        """Promote to an audit-ready course. Call ONLY after a student has agreed.

        Raises rather than filling a gap with a default: a missing grade or credit
        value silently guessed here would flow straight into a graduation decision.
        """
        if not self.is_complete:
            missing = [
                name
                for name, value in (
                    ("term", self.term),
                    ("grade", self.grade),
                    ("credits", self.credits),
                )
                if value is None
            ]
            raise ValueError(
                f"{self.code}: cannot confirm an incomplete row (missing {', '.join(missing)}). "
                "The student must supply the missing field first."
            )
        assert self.term is not None and self.grade is not None and self.credits is not None
        return CompletedCourse(
            code=self.code,
            term=self.term,
            grade=self.grade,
            credits=self.credits,
            provenance=Provenance.STUDENT_CONFIRMED,
            institution=institution if institution is not None else self.institution,
        )


class ExtractionResult(BaseModel):
    """Everything one pass over one transcript produced."""

    model_config = ConfigDict(frozen=True)

    source_name: str = Field(description="Original filename, for the audit trail")
    extractor: str = Field(description="Which extractor ran, e.g. 'text-parser' or a model id")
    institution: str | None = Field(
        default=None,
        description="Institution named on the document as a whole, if it names one.",
    )
    courses: list[ExtractedCourse] = Field(default_factory=list)
    warnings: list[str] = Field(
        default_factory=list,
        description="Problems with the document as a whole, not with one row.",
    )

    @property
    def needs_review(self) -> list[ExtractedCourse]:
        """Rows a student must look at before anything is confirmed."""
        return [c for c in self.courses if c.confidence is not Confidence.HIGH]

    @property
    def confirmable(self) -> list[ExtractedCourse]:
        return [c for c in self.courses if c.is_complete]

    def summary(self) -> str:
        high = sum(1 for c in self.courses if c.confidence is Confidence.HIGH)
        return (
            f"{len(self.courses)} course(s) found in {self.source_name}: "
            f"{high} clean, {len(self.needs_review)} needing review. "
            "Nothing counts toward your degree until you confirm it."
        )
