"""A student's completed coursework, as the audit engine consumes it.

Deliberately minimal: the engine needs what a course was, when, what grade, and
how much the claim can be trusted. Everything else about a student belongs
elsewhere.

*** NO LLM CODE. Nothing in app/audit/ may import app/llm/ or app/advisor/.
    tests/test_no_llm_in_engine.py enforces it. ***
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.provenance import TRUSTED_FOR_AUDIT, Provenance


class CompletedCourse(BaseModel):
    """One course a student has taken.

    `provenance` is load-bearing, not decoration. A course parsed out of an uploaded
    transcript by the LLM arrives as UNVERIFIED_EXTRACTION and must not move a degree
    audit until the student confirms it. `is_trusted` is the gate.
    """

    model_config = ConfigDict(frozen=True)

    code: str
    term: str = Field(description="e.g. 'Fall 2024'")
    grade: str
    credits: Decimal
    provenance: Provenance = Provenance.VERIFIED

    @property
    def is_trusted(self) -> bool:
        return self.provenance in TRUSTED_FOR_AUDIT


class StudentRecord(BaseModel):
    """Everything the engine knows about one student's history."""

    model_config = ConfigDict(frozen=True)

    student_id: str
    completed: list[CompletedCourse] = Field(default_factory=list)

    def trusted_courses(self) -> list[CompletedCourse]:
        """Only what the engine is permitted to build on."""
        return [c for c in self.completed if c.is_trusted]
