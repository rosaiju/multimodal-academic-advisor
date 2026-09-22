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

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.audit.record import CompletedCourse
from app.catalog.schema import IN_PROGRESS_GRADES
from app.schemas.provenance import Provenance

#: Re-exported so the ingestion layer reads it from one place. A student can
#: confirm an in-progress row without inventing a final grade; the catalog's
#: NON_PASSING set is what stops it satisfying a requirement.
__all__ = ["IN_PROGRESS_GRADES", "Confidence", "ExtractedCourse", "ExtractionResult"]


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
    transfer: bool = Field(
        default=False, description="Row came from transfer credit at another institution"
    )
    source_reference: str | None = Field(
        default=None,
        description="The document's own 'Satisfied by' text, verbatim. What makes two "
        "rows sharing a block-transfer code (COSC116TR) different courses rather than "
        "one repeated course - their code, term, grade and credits can all match.",
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
    def is_transfer(self) -> bool:
        """True when this row came from another institution.

        Set by the extractor rather than inferred from text, so counting transfer
        rows does not depend on matching issue wording.
        """
        return self.transfer

    @computed_field
    @property
    def in_progress(self) -> bool:
        """Registered but unfinished - a status, not a gap in the reading.

        Computed from the grade rather than stored, so it is true for every
        extractor that reads an IP row, not only the one that thought to set a flag.
        """
        return (self.grade or "").strip().upper() in IN_PROGRESS_GRADES

    @computed_field
    @property
    def is_placeholder(self) -> bool:
        """A summary line, not a course: no grade AND no credit hours.

        A DegreeWorks audit opens its transfer section with a row like
        "ORTR 101 TRANSFER OF 24 CREDITS  TR  0  FALL 2023". It carries no grade and
        no credits because it is a HEADING for credit itemised further down - those
        same 24 credits appear again as individual 116TR bucket rows.

        Confirming it would count that credit twice, so this is not merely
        unconfirmable-for-now like a row with an unread grade. No edit makes it a
        course, and `confirm` refuses it outright.

        A zero-credit row that DOES carry a grade is a real registration - a
        comprehensive exam, say - and is not caught here.
        """
        return self.grade is None and (self.credits is None or self.credits == 0)

    @computed_field
    @property
    def missing_fields(self) -> list[str]:
        """Which audit-required fields this row does not supply."""
        return [
            name
            for name, value in (
                ("term", self.term),
                ("grade", self.grade),
                ("credits", self.credits),
            )
            if value is None
        ]

    @computed_field
    @property
    def blocking_reason(self) -> str | None:
        """Why this row cannot be confirmed as it stands, in words, or None.

        The UI shows this verbatim. A student who is told "some rows are missing
        information" has to hunt; one told which row and which field can act.
        """
        if self.is_placeholder:
            return (
                "This is a summary line, not a course - it has no grade and no credit "
                "hours, and the credit it totals is listed separately below. It cannot "
                "be added to your record."
            )
        if self.missing_fields:
            return (
                f"Missing {', '.join(self.missing_fields)}. Fill this in from your "
                "transcript, or leave the row unticked."
            )
        return None

    @property
    def is_complete(self) -> bool:
        """True when the row has everything the audit engine would need."""
        return all((self.code, self.term, self.grade, self.credits is not None))

    @property
    def can_confirm(self) -> bool:
        """True when a student could accept this row as it stands."""
        return self.is_complete and not self.is_placeholder

    def confirm(self, *, institution: str | None = None) -> CompletedCourse:
        """Promote to an audit-ready course. Call ONLY after a student has agreed.

        Raises rather than filling a gap with a default: a missing grade or credit
        value silently guessed here would flow straight into a graduation decision.
        """
        if self.is_placeholder:
            raise ValueError(
                f"{self.code}: this is a summary line, not a course - no grade and no "
                "credit hours, and the credit it totals appears separately as its own "
                "rows. Confirming it would count that credit twice."
            )
        if not self.is_complete:
            raise ValueError(
                f"{self.code}: cannot confirm an incomplete row "
                f"(missing {', '.join(self.missing_fields)}). "
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
        """Rows a student could accept as they stand."""
        return [c for c in self.courses if c.can_confirm]

    @property
    def blocked(self) -> list[ExtractedCourse]:
        """Rows that cannot be confirmed yet, each carrying its own reason."""
        return [c for c in self.courses if not c.can_confirm]

    def summary(self) -> str:
        high = sum(1 for c in self.courses if c.confidence is Confidence.HIGH)
        return (
            f"{len(self.courses)} course(s) found in {self.source_name}: "
            f"{high} clean, {len(self.needs_review)} needing review. "
            "Nothing counts toward your degree until you confirm it."
        )
