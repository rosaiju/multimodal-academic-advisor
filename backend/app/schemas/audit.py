"""The AuditResult contract.

This is the single most important interface in the project. Person 1 (audit engine)
produces it; Person 4 (frontend) and Person 2 (LLM tools) consume it. Agreeing on it
in Week 1 is what lets all three build in parallel against a mock.

Nothing in this file imports the LLM layer. A degree audit is computed, not predicted.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, Field

from app.schemas.provenance import Provenance


class BlockStatus(StrEnum):
    SATISFIED = "satisfied"
    IN_PROGRESS = "in_progress"
    UNMET = "unmet"
    #: The catalog defers this to a human ("or approved substitute"). The engine
    #: refuses to guess; the UI says "requires advisor confirmation".
    NEEDS_ADVISOR = "needs_advisor"


class CourseRef(BaseModel):
    """A course as the catalog knows it."""

    code: str = Field(description="Canonical code, e.g. 'COSC220'")
    subject: str
    number: int
    title: str
    credits: Decimal


class AppliedCourse(BaseModel):
    """One completed course, as assigned to one requirement block by the matcher.

    A course appears at most once across all blocks in an AuditResult — that is the
    no-double-counting invariant, and `tests/test_matcher.py` asserts it.
    """

    course: CourseRef
    term: str = Field(description="e.g. 'Fall 2024'")
    grade: str
    credits_applied: Decimal
    provenance: Provenance


class RequirementBlockResult(BaseModel):
    block_id: str
    name: str
    status: BlockStatus

    credits_required: Decimal
    credits_applied: Decimal

    #: For all_of / n_of blocks: how many courses are needed vs. satisfied.
    courses_required: int | None = None
    courses_applied: int | None = None

    applied: list[AppliedCourse] = Field(default_factory=list)
    still_needed: list[CourseRef] = Field(
        default_factory=list,
        description="Courses that would satisfy the remainder. Empty for open-ended blocks.",
    )
    note: str | None = Field(
        default=None,
        description="Catalog footnote or advisor-discretion explanation. Never LLM-written.",
    )

    @property
    def is_complete(self) -> bool:
        return self.status is BlockStatus.SATISFIED


class GpaSummary(BaseModel):
    cumulative: Decimal | None
    major: Decimal | None
    min_cumulative_required: Decimal
    min_major_required: Decimal

    @property
    def meets_requirements(self) -> bool:
        if self.cumulative is None or self.major is None:
            return False
        return (
            self.cumulative >= self.min_cumulative_required
            and self.major >= self.min_major_required
        )


class MatcherStrategy(StrEnum):
    """Which assignment algorithm produced this audit.

    Kept in the result on purpose: the Milestone 1 demo toggles between these two
    on the same student to show that naive iteration under-reports progress.
    """

    GREEDY = "greedy"
    OPTIMAL_BIPARTITE = "optimal_bipartite"


class AuditResult(BaseModel):
    student_id: str
    program_id: str
    catalog_year: str

    total_credits_required: Decimal
    total_credits_applied: Decimal
    total_credits_in_progress: Decimal

    gpa: GpaSummary
    blocks: list[RequirementBlockResult]

    #: Completed courses the matcher could not apply to any requirement.
    unapplied: list[AppliedCourse] = Field(default_factory=list)

    #: Longest remaining prerequisite chain, in terms. Computed from the DAG,
    #: not estimated. This is the honest answer to "can I still graduate on time?"
    critical_path_terms: int | None = None
    critical_path: list[CourseRef] = Field(default_factory=list)

    strategy: MatcherStrategy
    #: Always VERIFIED — this object is engine output by construction.
    provenance: Provenance = Provenance.VERIFIED

    @property
    def percent_complete(self) -> float:
        if self.total_credits_required == 0:
            return 0.0
        return float(self.total_credits_applied / self.total_credits_required * 100)

    @property
    def is_graduation_eligible(self) -> bool:
        return (
            self.total_credits_applied >= self.total_credits_required
            and self.gpa.meets_requirements
            and all(b.is_complete for b in self.blocks)
        )

    def unmet_blocks(self) -> list[RequirementBlockResult]:
        return [b for b in self.blocks if not b.is_complete]
