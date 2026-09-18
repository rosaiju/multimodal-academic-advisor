"""Catalog file schema.

A catalog YAML file is the SOURCE OF TRUTH for degree requirements. It is
git-tracked, schema-validated at load time, and the LLM can never modify it.

Adding a new program or a new catalog year means adding a YAML file. No code change.

*** NO LLM CODE BELOW THIS LINE. Nothing in app/catalog/ or app/audit/ may import
    app/llm/ or app/advisor/. tests/test_no_llm_in_engine.py enforces this. ***
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

#: Grades that earn credit, best first. Used for min_grade comparisons.
GRADE_ORDER: list[str] = ["A", "B", "C", "D"]
#: Grades that appear on a transcript but never satisfy a requirement.
NON_PASSING = frozenset({"F", "W", "I", "IP", "NP", "AU"})


def grade_meets(earned: str, minimum: str) -> bool:
    """True if `earned` is at least as good as `minimum`. Unknown grades fail closed."""
    earned = earned.strip().upper().rstrip("+-")
    minimum = minimum.strip().upper().rstrip("+-")
    if earned in NON_PASSING or earned not in GRADE_ORDER or minimum not in GRADE_ORDER:
        return False
    return GRADE_ORDER.index(earned) <= GRADE_ORDER.index(minimum)


class Term(StrEnum):
    FALL = "fall"
    SPRING = "spring"
    SUMMER = "summer"
    WINTER = "winter"


class Course(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str
    title: str
    credits: Decimal
    prerequisites: list[str] = Field(
        default_factory=list,
        description="Course codes that must be completed first. Flat AND-list; "
        "use prerequisite_groups for OR-semantics.",
    )
    prerequisite_groups: list[list[str]] = Field(
        default_factory=list,
        description="Each inner list is an OR-group; all groups must be satisfied. "
        "e.g. [[MATH241], [COSC111, COSC112]] means MATH241 AND (COSC111 OR COSC112).",
    )
    min_prereq_grade: str = "D"
    terms_offered: list[Term] = Field(default_factory=lambda: [Term.FALL, Term.SPRING])

    @property
    def subject(self) -> str:
        return "".join(c for c in self.code if c.isalpha()).upper()

    @property
    def number(self) -> int:
        digits = "".join(c for c in self.code if c.isdigit())
        return int(digits) if digits else 0


class _BlockBase(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    min_grade: str = "D"
    note: str | None = None
    advisor_approval_required: bool = Field(
        default=False,
        description="Set when the catalog defers to human judgment, e.g. an approved "
        "substitute clause. The engine reports NEEDS_ADVISOR instead of guessing.",
    )


class AllOfBlock(_BlockBase):
    """Every listed course is required."""

    type: Literal["all_of"] = "all_of"
    courses: list[str]


class NOfBlock(_BlockBase):
    """Any N courses from the list."""

    type: Literal["n_of"] = "n_of"
    n: int = Field(ge=1)
    courses: list[str]

    @model_validator(mode="after")
    def _n_is_reachable(self) -> NOfBlock:
        if self.n > len(self.courses):
            raise ValueError(f"block {self.id!r}: n={self.n} exceeds {len(self.courses)} choices")
        return self


class CourseFilter(BaseModel):
    model_config = ConfigDict(frozen=True)

    subject: str | None = None
    subjects: list[str] | None = None
    number_min: int | None = None
    number_max: int | None = None
    exclude: list[str] = Field(default_factory=list)

    def matches(self, course: Course) -> bool:
        if course.code in self.exclude:
            return False
        if self.subject and course.subject != self.subject.upper():
            return False
        if self.subjects and course.subject not in {s.upper() for s in self.subjects}:
            return False
        if self.number_min is not None and course.number < self.number_min:
            return False
        if self.number_max is not None and course.number > self.number_max:
            return False
        return True


class CreditsFromBlock(_BlockBase):
    """N credits from any course matching a filter and/or an explicit list."""

    type: Literal["credits_from"] = "credits_from"
    credits_required: Decimal = Field(gt=0)
    courses: list[str] = Field(default_factory=list)
    course_filter: CourseFilter | None = None

    @model_validator(mode="after")
    def _has_a_source(self) -> CreditsFromBlock:
        if not self.courses and self.course_filter is None:
            raise ValueError(f"block {self.id!r}: needs courses or course_filter")
        return self


class GpaBlock(_BlockBase):
    type: Literal["gpa"] = "gpa"
    min_gpa: Decimal
    scope: Literal["cumulative", "major"] = "cumulative"


class ResidencyBlock(_BlockBase):
    type: Literal["residency"] = "residency"
    min_credits_at_institution: Decimal


RequirementBlock = Annotated[
    AllOfBlock | NOfBlock | CreditsFromBlock | GpaBlock | ResidencyBlock,
    Field(discriminator="type"),
]


class Program(BaseModel):
    """One degree program in one catalog year."""

    model_config = ConfigDict(frozen=True)

    program_id: str
    program: str
    institution: str
    catalog_year: str
    total_credits_required: Decimal
    min_gpa: Decimal = Decimal("2.0")
    min_major_gpa: Decimal = Decimal("2.0")
    major_subjects: list[str] = Field(default_factory=list)

    courses: list[Course]
    requirement_blocks: list[RequirementBlock]

    @field_validator("courses")
    @classmethod
    def _unique_codes(cls, v: list[Course]) -> list[Course]:
        seen: set[str] = set()
        for c in v:
            if c.code in seen:
                raise ValueError(f"duplicate course code {c.code!r}")
            seen.add(c.code)
        return v

    @model_validator(mode="after")
    def _referential_integrity(self) -> Program:
        """Every course code named anywhere must actually exist.

        Catches catalog typos at load time instead of producing a silently-wrong audit.
        """
        known = {c.code for c in self.courses}

        for c in self.courses:
            groups: list[list[str]] = [[p] for p in c.prerequisites]
            groups.extend(c.prerequisite_groups)
            for group in groups:
                for code in group:
                    if code not in known:
                        raise ValueError(f"{c.code}: unknown prerequisite {code!r}")

        block_ids: set[str] = set()
        for b in self.requirement_blocks:
            if b.id in block_ids:
                raise ValueError(f"duplicate block id {b.id!r}")
            block_ids.add(b.id)
            for code in getattr(b, "courses", []) or []:
                if code not in known:
                    raise ValueError(f"block {b.id!r}: unknown course {code!r}")
            filt = getattr(b, "course_filter", None)
            if filt is not None:
                for code in filt.exclude:
                    if code not in known:
                        raise ValueError(f"block {b.id!r}: unknown excluded course {code!r}")
        return self

    def course(self, code: str) -> Course | None:
        return {c.code: c for c in self.courses}.get(code)

    def courses_for_block(self, block: RequirementBlock) -> list[Course]:
        """Every course that could possibly count toward `block`."""
        explicit = [c for code in getattr(block, "courses", []) or [] if (c := self.course(code))]
        filt = getattr(block, "course_filter", None)
        if filt is None:
            return explicit
        seen = {c.code for c in explicit}
        return explicit + [c for c in self.courses if c.code not in seen and filt.matches(c)]
