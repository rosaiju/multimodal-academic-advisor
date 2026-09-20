"""The AuditResult contract.

This is the single most important interface in the project. Person 1 (audit engine)
produces it; Person 4 (frontend) and Person 2 (LLM tools) consume it. Agreeing on it
in Week 1 is what lets all three build in parallel against a mock.

Nothing in this file imports the LLM layer. A degree audit is computed, not predicted.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, Field, computed_field

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


class UnrecognisedCourse(BaseModel):
    """Confirmed coursework the catalog has no entry for.

    Transfer credit dominates this list: a student who arrives with 88 hours from
    another institution carries courses that Morgan's catalog never names. They are
    real credit on a real record, and leaving them out of the accounting is how a
    dashboard comes to show 54 credits to someone holding 150.

    They are reported, never applied. Deciding that an outside course satisfies a
    requirement is an evaluator's job, not a parser's.
    """

    code: str
    term: str
    grade: str
    credits: Decimal
    institution: str | None = None
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


class CatalogCoverage(BaseModel):
    """How much of the degree the encoded catalog can actually speak to.

    The Morgan catalog deliberately omits every requirement its official sources
    contradict, rather than guessing one. An audit against it is therefore PARTIAL,
    and a caller must be able to tell the difference between "you have not done
    these requirements" and "we have not encoded these requirements".

    Without this, a UI showing "5 of 6 requirements complete" reads as almost-done
    when the real degree has roughly twice that many requirement areas.
    """

    blocks_encoded: int
    courses_in_catalog: int
    courses_satisfying_no_block: int
    credits_explicitly_demanded: Decimal
    total_credits_required: Decimal

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_complete(self) -> bool:
        """True only when every catalog course is claimed by some requirement."""
        return self.courses_satisfying_no_block == 0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def summary(self) -> str:
        if self.is_complete:
            return f"{self.blocks_encoded} requirement blocks; all courses covered."
        return (
            f"PARTIAL: {self.blocks_encoded} requirement blocks encoded; "
            f"{self.courses_satisfying_no_block} of {self.courses_in_catalog} catalog "
            "courses satisfy no encoded requirement. Requirements still awaiting "
            "department clarification are absent, not failed."
        )


class AuditResult(BaseModel):
    student_id: str
    program_id: str
    catalog_year: str

    total_credits_required: Decimal

    #: Credit the matcher placed into an ENCODED requirement block. Scoped to what
    #: the catalog currently describes, so it is NOT a measure of what a student
    #: has done - on a partial catalog it is far smaller.
    total_credits_applied: Decimal

    #: All trusted, passing credit on the record, whether or not the catalog names
    #: the course. This is the student's own total, and the number that should be
    #: compared against total_credits_required.
    total_credits_earned: Decimal = Decimal(0)

    #: Trusted credit currently being attempted. Counts toward nothing yet.
    total_credits_in_progress: Decimal

    gpa: GpaSummary
    blocks: list[RequirementBlockResult]

    #: Completed courses the catalog KNOWS but no block claimed.
    unapplied: list[AppliedCourse] = Field(default_factory=list)

    #: Confirmed coursework the catalog has no entry for at all - mostly transfer
    #: credit. Previously dropped on the floor, which made the credit totals
    #: irreconcilable with the student's own audit.
    outside_catalog: list[UnrecognisedCourse] = Field(default_factory=list)

    #: Longest remaining prerequisite chain, in terms. Computed from the DAG,
    #: not estimated. This is the honest answer to "can I still graduate on time?"
    critical_path_terms: int | None = None
    critical_path: list[CourseRef] = Field(default_factory=list)

    strategy: MatcherStrategy

    #: Set by the engine. None only on hand-built mocks from before this existed.
    coverage: CatalogCoverage | None = None
    #: Always VERIFIED — this object is engine output by construction.
    provenance: Provenance = Provenance.VERIFIED

    @computed_field  # type: ignore[prop-decorator]
    @property
    def percent_complete(self) -> float | None:
        """How far through the DEGREE the student is, or None when unknowable.

        This used to divide credit applied to encoded blocks by the credits the
        whole degree requires. The numerator was scoped to the handful of blocks
        the catalog describes and the denominator to all 120 credits, so the two
        did not belong in the same fraction: a student holding 150 credits, 138 of
        them passing, was shown "45% complete" because only 54 had landed in an
        encoded block.

        There is no honest degree percentage while requirements are missing from
        the catalog, so this returns None rather than a number that reads as one.
        `credit_progress_percent` and `encoded_requirements_percent` are each well
        defined and are what a caller should show instead.
        """
        if self.coverage is not None and not self.coverage.is_complete:
            return None
        if self.total_credits_required == 0:
            return None
        return float(self.total_credits_applied / self.total_credits_required * 100)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def credit_progress_percent(self) -> float:
        """Credit EARNED against credit required. Uncapped, and may exceed 100.

        Always meaningful: both sides come from the student's own record and the
        catalog's headline total, neither of which depends on how many requirement
        blocks have been encoded. It answers "have I done enough credit?", which is
        not the same question as "have I done the right credit?".
        """
        if self.total_credits_required == 0:
            return 0.0
        return float(self.total_credits_earned / self.total_credits_required * 100)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def encoded_requirements_percent(self) -> float:
        """Satisfied blocks as a share of the blocks that ARE encoded.

        Honest within its scope and useless outside it: with six blocks encoded out
        of a real degree's many, three satisfied is 50% of what we have modelled and
        says nothing about the degree. Callers must show it beside the coverage
        caveat, never alone.
        """
        if not self.blocks:
            return 0.0
        satisfied = sum(1 for b in self.blocks if b.is_complete)
        return float(satisfied / len(self.blocks) * 100)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_graduation_eligible(self) -> bool:
        """Never True while the catalog itself is known to be incomplete.

        Satisfying every ENCODED block is not the same as satisfying the degree when
        requirements are still missing from the catalog. Answering "yes, you can
        graduate" on a partial catalog is the single worst thing this system could
        do, so an incomplete catalog fails closed.
        """
        if self.coverage is not None and not self.coverage.is_complete:
            return False
        return (
            self.total_credits_applied >= self.total_credits_required
            and self.gpa.meets_requirements
            and all(b.is_complete for b in self.blocks)
        )

    def unmet_blocks(self) -> list[RequirementBlockResult]:
        return [b for b in self.blocks if not b.is_complete]
