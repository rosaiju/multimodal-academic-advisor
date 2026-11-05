"""Grade classification and GPA, in one place.

Three questions every part of the engine asks about a transcript row, answered once:

* **Is it finished?** In-progress grades (IP, REG, INC) are attempted credit,
  never earned credit, and never a satisfied prerequisite.
* **Did it earn credit?** Letter grades A-D and the pass-type grades P, S and CR do.
  F, W, I, NP, AU do not. A grade the engine does not recognise (WF, NR, ...) earns
  NOTHING until someone says what it means - counting unknown credit is how an audit
  over-reports progress.
* **Does it carry quality points?** Only A-F. P/S/CR earn credit but sit outside
  the GPA, as do W, I and in-progress grades.

GPA is where Morgan's policy is NOT encoded, and this module says so rather than
picking one silently:

* **Repeated courses.** Whether a retake replaces the earlier grade, or both are
  averaged, is a registrar policy the catalog file does not carry.
* **Transfer grades.** Whether grades earned at another institution enter the GPA
  is the same kind of policy.

`gpa_figures` therefore computes the GPA under every reading of those two rules and
returns the spread. The headline number averages every attempt at every
institution (what the transcript literally shows); `low`/`high` bound it. A GPA
requirement is decided only when every reading agrees - otherwise it is
NEEDS_ADVISOR, never a guess.

*** NO LLM CODE. Nothing in app/audit/ may import app/llm/ or app/advisor/. ***
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from itertools import product

from app.audit.record import CompletedCourse
from app.catalog.schema import IN_PROGRESS_GRADES, NON_PASSING, Program

#: Standard four-point scale. Morgan states minimums as "2.0 or better", which is
#: this scale. Suffixes are stripped (A- and A+ both count as A), matching
#: `grade_meets` in the catalog schema. If Morgan weights +/- grades this under- and
#: over-states by up to a third of a point.
GRADE_POINTS: dict[str, Decimal] = {
    "A": Decimal(4),
    "B": Decimal(3),
    "C": Decimal(2),
    "D": Decimal(1),
    "F": Decimal(0),
}

#: Pass-type grades: credit earned, no quality points, and - because the catalog
#: does not say whether a pass meets a "C or better" minimum - no requirement
#: decided by them without an advisor.
PASS_GRADES = frozenset({"P", "S", "CR"})


def normalise(grade: str) -> str:
    return grade.strip().upper()


def letter(grade: str) -> str:
    """'b+ ' -> 'B'. Suffixes stripped, as `grade_meets` does."""
    return normalise(grade).rstrip("+-")


def is_in_progress(grade: str) -> bool:
    """Registered but unfinished. Credit attempted, none earned."""
    return normalise(grade) in IN_PROGRESS_GRADES


def is_pass_grade(grade: str) -> bool:
    return normalise(grade) in PASS_GRADES


def earns_credit(grade: str) -> bool:
    """True only for a grade KNOWN to earn credit. Unknown grades fail closed."""
    if is_in_progress(grade):
        return False
    g = letter(grade)
    if g in NON_PASSING:
        return False
    return (g in GRADE_POINTS and g != "F") or normalise(grade) in PASS_GRADES


def is_recognised(grade: str) -> bool:
    """A grade this engine knows the meaning of, passing or not."""
    g = normalise(grade)
    return (
        letter(grade) in GRADE_POINTS
        or g in PASS_GRADES
        or g in NON_PASSING
        or letter(grade) in NON_PASSING
        or g in IN_PROGRESS_GRADES
    )


_LEADING_LETTERS = re.compile(r"[A-Za-z]+")


def subject_of(program: Program, code: str) -> str:
    """Course subject. Catalog subject when known, else the code's leading letters.

    Leading letters, not all letters: a DegreeWorks transfer bucket like COSC116TR
    is a COSC course, not a "COSCTR" one.
    """
    course = program.course(code)
    if course is not None:
        return course.subject
    match = _LEADING_LETTERS.match(code.strip())
    return match.group(0).upper() if match else ""


def is_transfer(program: Program, row: CompletedCourse) -> bool:
    """Taken somewhere else, by the record's own say-so. Unknown is NOT transfer."""
    if row.institution is None:
        return False
    return row.institution.strip().lower() != program.institution.strip().lower()


def gpa_points(courses: list[CompletedCourse]) -> Decimal | None:
    """Quality-point GPA over rows carrying an A-F grade. None means unknown, not 0."""
    quality = Decimal(0)
    credits = Decimal(0)
    for c in courses:
        g = letter(c.grade)
        if g not in GRADE_POINTS:
            continue  # W, I, IP, P, NP, AU carry no quality points
        quality += GRADE_POINTS[g] * c.credits
        credits += c.credits
    if credits == 0:
        return None
    return (quality / credits).quantize(Decimal("0.01"))


@dataclass
class GpaFigures:
    #: Every attempt, every institution - what the transcript literally shows.
    value: Decimal | None
    #: Lowest and highest GPA across the plausible readings of the repeat and
    #: transfer rules. Equal to `value` when no reading changes anything.
    low: Decimal | None
    high: Decimal | None
    repeated: list[str] = field(default_factory=list)
    transfer_graded: int = 0

    @property
    def policy_dependent(self) -> bool:
        return self.low != self.high


def gpa_rows(program: Program, rows: list[CompletedCourse], scope: str) -> list[CompletedCourse]:
    """Trusted rows with an A-F grade, inside `scope` ("cumulative" or "major").

    Catalog or not: a Morgan course the partial catalog does not name is still on
    the student's GPA.
    """
    graded = [c for c in rows if c.is_trusted and letter(c.grade) in GRADE_POINTS]
    if scope == "major":
        majors = {s.upper() for s in program.major_subjects}
        graded = [c for c in graded if subject_of(program, c.code) in majors]
    return graded


def _one_attempt(rows: list[CompletedCourse], pick_best: bool) -> list[CompletedCourse]:
    """Keep one attempt per course code (best or worst), as a replacement policy would."""
    chosen: dict[str, CompletedCourse] = {}
    order: list[str] = []
    for c in rows:
        current = chosen.get(c.code)
        if current is None:
            chosen[c.code] = c
            order.append(c.code)
            continue
        better = GRADE_POINTS[letter(c.grade)] > GRADE_POINTS[letter(current.grade)]
        if better == pick_best:
            chosen[c.code] = c
    return [chosen[code] for code in order]


def gpa_figures(program: Program, rows: list[CompletedCourse], scope: str) -> GpaFigures:
    graded = gpa_rows(program, rows, scope)
    counts: dict[str, int] = {}
    for c in graded:
        counts[c.code] = counts.get(c.code, 0) + 1
    repeated = sorted(code for code, n in counts.items() if n > 1)
    transfer_graded = sum(1 for c in graded if is_transfer(program, c))

    values: list[Decimal] = []
    for include_transfer, repeat_rule in product((True, False), ("all", "best", "worst")):
        subset = graded if include_transfer else [c for c in graded if not is_transfer(program, c)]
        if repeat_rule != "all":
            subset = _one_attempt(subset, pick_best=repeat_rule == "best")
        v = gpa_points(subset)
        if v is not None:
            values.append(v)

    value = gpa_points(graded)
    return GpaFigures(
        value=value,
        low=min(values) if values else None,
        high=max(values) if values else None,
        repeated=repeated,
        transfer_graded=transfer_graded,
    )


def gpa_policy_note(figures: GpaFigures, scope: str) -> str | None:
    """Plain statement of why a GPA is a range, or None when it is not."""
    if not figures.policy_dependent:
        return None
    causes: list[str] = []
    if figures.repeated:
        causes.append(
            "repeated course(s) " + ", ".join(figures.repeated) + " (whether a retake "
            "replaces the earlier grade is not encoded)"
        )
    if figures.transfer_graded:
        causes.append(
            f"{figures.transfer_graded} graded transfer row(s) (whether transfer grades "
            "enter the GPA is not encoded)"
        )
    return (
        f"Your {scope} GPA is between {figures.low} and {figures.high} depending on "
        + " and ".join(causes)
        + ". Confirm with the registrar or an advisor."
    )


__all__ = [
    "GRADE_POINTS",
    "PASS_GRADES",
    "GpaFigures",
    "earns_credit",
    "gpa_figures",
    "gpa_points",
    "gpa_policy_note",
    "gpa_rows",
    "is_in_progress",
    "is_pass_grade",
    "is_recognised",
    "is_transfer",
    "letter",
    "normalise",
    "subject_of",
]
