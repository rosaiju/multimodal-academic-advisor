"""Semester planning.

Lays a student's remaining required courses out across future terms, and checks any
hand-edited layout against the same rules.

Nothing here is generated. A course lands in a term because the catalog says it
counts toward an unfinished requirement, the student will have met its
prerequisites by then, and the catalog says it runs that term. Term ordering reuses
the ranking in `planning.py`, so the planner and the advice cannot disagree.

How it works: each term is planned against a SIMULATED record in which every course
already scheduled is treated as passed. That is what lets a prerequisite scheduled
in Fall 2027 unlock a course in Spring 2028, and what stops a "pick one of five"
requirement from being filled five times over. The simulated record never leaves
this module and is never stored.

Limits the output always states, because a plan that hides them is a wrong plan:

* It covers the requirements the catalog currently encodes, no more. It therefore
  reports no graduation date.
* Courses in progress are assumed to be passed, and planned courses are assumed to
  be passed. A failed course invalidates everything downstream of it.
* A course the catalog lists as "offered as needed" is scheduled only with a
  warning, never silently.

*** NO LLM CODE. Nothing in app/audit/ may import app/llm/ or app/advisor/. ***
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from app.audit.engine import run_audit
from app.audit.planning import build_plan
from app.audit.prereq_graph import passed_courses, unmet_prerequisites
from app.audit.record import CompletedCourse, StudentRecord
from app.catalog.schema import IN_PROGRESS_GRADES, Program, Term
from app.schemas.audit import BlockStatus, CourseRef, MatcherStrategy
from app.schemas.provenance import Provenance

DEFAULT_CREDIT_TARGET = Decimal(15)

#: Grade the simulation gives a course it assumes will be passed. High enough to
#: satisfy any block's minimum, so the plan never fails on a grade nobody earned.
ASSUMED_GRADE = "A"

#: Stop after this many consecutive terms that place nothing. Two full year cycles:
#: long enough for a fall-only course to come round, short enough to terminate.
_MAX_EMPTY_TERMS = 4

_PLANNABLE = (Term.SPRING, Term.SUMMER, Term.FALL)


class TermError(ValueError):
    """A term label the planner cannot read."""


def parse_term(label: str) -> tuple[Term, int]:
    """'Fall 2027' -> (Term.FALL, 2027)."""
    parts = label.strip().split()
    if len(parts) != 2 or not parts[1].isdigit():
        raise TermError(f"{label!r} is not a term. Use a season and a year, e.g. 'Fall 2027'.")
    try:
        season = Term(parts[0].lower())
    except ValueError as exc:
        raise TermError(f"{label!r} has an unknown season {parts[0]!r}.") from exc
    if season not in _PLANNABLE:
        raise TermError(f"The planner schedules fall, spring and summer, not {parts[0]!r}.")
    return season, int(parts[1])


def term_label(season: Term, year: int) -> str:
    return f"{season.value.capitalize()} {year}"


def _order(season: Term, year: int) -> int:
    return year * 3 + _PLANNABLE.index(season)


def next_term(season: Term, year: int, *, include_summer: bool = False) -> tuple[Term, int]:
    """The term after this one. Summer is skipped unless asked for."""
    if season is Term.SPRING:
        return (Term.SUMMER, year) if include_summer else (Term.FALL, year)
    if season is Term.SUMMER:
        return Term.FALL, year
    return Term.SPRING, year + 1


def default_start_term(today: date | None = None, *, include_summer: bool = False) -> str:
    """The first term a student has not yet started, from the calendar.

    In the autumn the current term is fall, so planning starts in spring. From
    January to July it starts in the coming fall (or summer, when asked for).
    """
    today = today or date.today()
    if today.month >= 8:
        return term_label(Term.SPRING, today.year + 1)
    if include_summer and today.month <= 5:
        return term_label(Term.SUMMER, today.year)
    return term_label(Term.FALL, today.year)


class PlannedCourse(BaseModel):
    course: CourseRef
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class PlannedTerm(BaseModel):
    term: str
    courses: list[PlannedCourse]
    credits: Decimal


class UnscheduledCourse(BaseModel):
    course: CourseRef
    reason: str


class SchedulePlan(BaseModel):
    student_id: str
    program_id: str
    catalog_year: str
    start_term: str
    credit_target: Decimal
    terms: list[PlannedTerm] = Field(default_factory=list)
    unscheduled: list[UnscheduledCourse] = Field(default_factory=list)
    #: The last term with a planned course. NOT a graduation date: requirements the
    #: catalog does not encode yet are absent from the plan, not met.
    last_planned_term: str | None = None
    caveats: list[str] = Field(default_factory=list)
    #: Always VERIFIED: derived from catalog data by deterministic rules.
    provenance: Provenance = Provenance.VERIFIED


class ScheduleIssue(BaseModel):
    severity: Literal["error", "warning"]
    term: str
    course: str | None = None
    message: str


class ScheduleValidation(BaseModel):
    #: False when any issue is an error. Warnings alone leave a plan valid.
    valid: bool
    issues: list[ScheduleIssue] = Field(default_factory=list)


def _ref(program: Program, code: str) -> CourseRef:
    course = program.course(code)
    assert course is not None
    return CourseRef(
        code=course.code,
        subject=course.subject,
        number=course.number,
        title=course.title,
        credits=course.credits,
    )


def _assumed(code: str, term: str, credits: Decimal) -> CompletedCourse:
    return CompletedCourse(
        code=code,
        term=term,
        grade=ASSUMED_GRADE,
        credits=credits,
        provenance=Provenance.VERIFIED,
    )


def _with_in_progress_passed(record: StudentRecord) -> StudentRecord:
    """Treat courses the student is sitting in as finished by the first planned term."""
    rows = [
        (
            c.model_copy(update={"grade": ASSUMED_GRADE})
            if c.is_trusted and c.grade.strip().upper() in IN_PROGRESS_GRADES
            else c
        )
        for c in record.completed
    ]
    return StudentRecord(student_id=record.student_id, completed=rows)


def _extend(record: StudentRecord, extra: list[CompletedCourse]) -> StudentRecord:
    return StudentRecord(student_id=record.student_id, completed=[*record.completed, *extra])


def _prerequisite_only(program: Program, sim: StudentRecord, blocked) -> dict[str, set[str]]:
    """Courses no requirement lists, but that a blocked required course needs first.

    Without these the planner strands a required course behind a prerequisite it never
    schedules. Maps each such course to the blocked courses waiting on it.
    """
    passed = passed_courses(program, sim.completed)
    needed: dict[str, set[str]] = {}

    def walk(code: str, waiting: str, seen: frozenset[str]) -> None:
        for missing in unmet_prerequisites(program, code, passed):
            if " or " in missing or missing in seen or program.course(missing) is None:
                continue
            needed.setdefault(missing, set()).add(waiting)
            walk(missing, waiting, seen | {missing})

    for rec in blocked:
        walk(rec.course.code, rec.course.code, frozenset({rec.course.code}))
    return needed


def generate_schedule(
    program: Program,
    record: StudentRecord,
    *,
    start_term: str | None = None,
    credit_target: Decimal = DEFAULT_CREDIT_TARGET,
    include_summer: bool = False,
    max_terms: int = 10,
) -> SchedulePlan:
    """Plan the student's remaining required courses, term by term."""
    start = start_term or default_start_term(include_summer=include_summer)
    season, year = parse_term(start)
    if season is Term.SUMMER and not include_summer:
        # Asked to start in summer, so summer is wanted.
        include_summer = True

    sim = _with_in_progress_passed(record)
    in_progress_assumed = any(
        c.is_trusted and c.grade.strip().upper() in IN_PROGRESS_GRADES for c in record.completed
    )

    terms: list[PlannedTerm] = []
    empty_run = 0

    for _ in range(max_terms):
        audit = run_audit(program, sim, MatcherStrategy.OPTIMAL_BIPARTITE)
        plan = build_plan(program, sim, audit, limit=10_000)
        if not plan.recommended and not plan.blocked:
            break

        label = term_label(season, year)
        # (course code, reasons, warnings, requirement blocks it serves)
        candidates: list[tuple[str, list[str], list[str], list[str]]] = []
        by_code = {r.course.code: r for r in plan.recommended}
        for code, needed_for in _prerequisite_only(program, sim, plan.blocked).items():
            if code in by_code or unmet_prerequisites(
                program, code, passed_courses(program, sim.completed)
            ):
                continue
            course = program.course(code)
            warnings = []
            if course is not None and course.title.upper().startswith("UNVERIFIED"):
                warnings.append(
                    "the catalog names this course only as a prerequisite and has no "
                    "verified title or credits for it. Confirm it with an advisor."
                )
            candidates.append(
                (code, [f"prerequisite for {', '.join(sorted(needed_for))}"], warnings, [])
            )
        # Courses a catalog lists as irregular go after the dependable ones, so
        # they are used only when a term would otherwise leave the slot empty.
        for rec in sorted(
            plan.recommended,
            key=lambda r: bool(program.course(r.course.code).offered_as_needed),  # type: ignore[union-attr]
        ):
            candidates.append((rec.course.code, rec.reasons, list(rec.warnings), rec.serves))

        chosen: list[PlannedCourse] = []
        chosen_rows: list[CompletedCourse] = []
        total = Decimal(0)

        for code, reasons, warnings, serves in candidates:
            course = program.course(code)
            if course is None or season not in course.terms_offered:
                continue
            if total + course.credits > credit_target:
                continue
            if chosen_rows and serves:
                # An earlier pick this term may already have finished the block this
                # course was serving ("pick one of five" must not be filled twice).
                after = run_audit(
                    program, _extend(sim, chosen_rows), MatcherStrategy.OPTIMAL_BIPARTITE
                )
                status = {b.block_id: b.status for b in after.blocks}
                if all(status.get(b) is BlockStatus.SATISFIED for b in serves):
                    continue

            chosen.append(
                PlannedCourse(course=_ref(program, code), reasons=reasons, warnings=warnings)
            )
            chosen_rows.append(_assumed(code, label, course.credits))
            total += course.credits

        if chosen:
            terms.append(PlannedTerm(term=label, courses=chosen, credits=total))
            sim = _extend(sim, chosen_rows)
            empty_run = 0
        else:
            empty_run += 1
            if empty_run >= _MAX_EMPTY_TERMS:
                break

        season, year = next_term(season, year, include_summer=include_summer)

    # Whatever is still needed after the last planned term.
    audit = run_audit(program, sim, MatcherStrategy.OPTIMAL_BIPARTITE)
    leftover = build_plan(program, sim, audit, limit=10_000)
    unscheduled = [
        UnscheduledCourse(
            course=r.course,
            reason="not offered in any planned term, or the planning horizon ended first",
        )
        for r in leftover.recommended
    ] + [
        UnscheduledCourse(
            course=r.course,
            reason="prerequisites still outstanding: " + ", ".join(r.missing_prerequisites),
        )
        for r in leftover.blocked
    ]

    caveats: list[str] = []
    if audit.coverage is not None and not audit.coverage.is_complete:
        caveats.append(
            "This plan covers only the requirements currently encoded in the catalog. "
            "Requirements still awaiting department clarification are absent, not met, "
            "so the last planned term is not a graduation date."
        )
    guessed = [
        c.course.code
        for t in terms
        for c in t.courses
        if "terms_offered" not in program.course(c.course.code).model_fields_set  # type: ignore[union-attr]
    ]
    if guessed:
        caveats.append(
            "The catalog does not record which terms most courses run, so they are assumed "
            "to run in both fall and spring. Check the schedule of classes before registering."
        )
    if in_progress_assumed:
        caveats.append("Courses you are taking now are assumed to be passed.")
    caveats.append(
        "Every planned course is assumed to be passed. A failed or withdrawn course "
        "moves everything that depends on it."
    )
    if unscheduled:
        caveats.append(
            f"{len(unscheduled)} required course(s) could not be placed. See the list "
            "below the plan; talk to an advisor before relying on this schedule."
        )

    return SchedulePlan(
        student_id=record.student_id,
        program_id=program.program_id,
        catalog_year=program.catalog_year,
        start_term=start,
        credit_target=credit_target,
        terms=terms,
        unscheduled=unscheduled,
        last_planned_term=terms[-1].term if terms else None,
        caveats=caveats,
    )


def validate_schedule(
    program: Program,
    record: StudentRecord,
    layout: list[tuple[str, list[str]]],
    *,
    credit_target: Decimal = DEFAULT_CREDIT_TARGET,
) -> ScheduleValidation:
    """Check a (possibly hand-edited) layout against the same rules as the generator.

    `layout` is [(term label, [course codes])] in the order the student wants it.
    A course counts as available to later terms only: a prerequisite placed in the
    same term does not help, because it is not finished until the term ends.
    """
    issues: list[ScheduleIssue] = []
    sim = _with_in_progress_passed(record)
    passed = passed_courses(program, sim.completed)
    taken_in_plan: set[str] = set()
    previous_order: int | None = None

    for label, codes in layout:
        try:
            season, year = parse_term(label)
        except TermError as exc:
            issues.append(ScheduleIssue(severity="error", term=label, message=str(exc)))
            continue

        order = _order(season, year)
        if previous_order is not None and order <= previous_order:
            issues.append(
                ScheduleIssue(
                    severity="error",
                    term=label,
                    message="Terms must be in chronological order, each one later than the last.",
                )
            )
        previous_order = order

        total = Decimal(0)
        placed_now: list[str] = []
        for raw in codes:
            code = raw.replace(" ", "").upper()
            course = program.course(code)
            if course is None:
                issues.append(
                    ScheduleIssue(
                        severity="error",
                        term=label,
                        course=code,
                        message=f"{code} is not in this program's catalog.",
                    )
                )
                continue

            total += course.credits
            if code in passed or code in taken_in_plan or code in placed_now:
                issues.append(
                    ScheduleIssue(
                        severity="error",
                        term=label,
                        course=code,
                        message=f"{code} is already completed or planned in another term.",
                    )
                )
                continue

            missing = unmet_prerequisites(program, code, passed)
            if missing:
                issues.append(
                    ScheduleIssue(
                        severity="error",
                        term=label,
                        course=code,
                        message=f"{code} needs {', '.join(missing)} finished in an earlier term.",
                    )
                )

            if season not in course.terms_offered:
                offered = ", ".join(t.value for t in course.terms_offered) or "no listed term"
                issues.append(
                    ScheduleIssue(
                        severity="error",
                        term=label,
                        course=code,
                        message=f"{code} is not offered in {season.value} (offered: {offered}).",
                    )
                )
            elif course.offered_as_needed:
                issues.append(
                    ScheduleIssue(
                        severity="warning",
                        term=label,
                        course=code,
                        message=f"{code} is offered irregularly. Confirm it runs in {label}.",
                    )
                )
            placed_now.append(code)

        if total > credit_target:
            issues.append(
                ScheduleIssue(
                    severity="warning",
                    term=label,
                    message=f"{total} credits is over the {credit_target}-credit target.",
                )
            )

        for code in placed_now:
            taken_in_plan.add(code)
            course = program.course(code)
            assert course is not None
            sim = _extend(sim, [_assumed(code, label, course.credits)])
        passed = passed_courses(program, sim.completed)

    return ScheduleValidation(
        valid=not any(i.severity == "error" for i in issues),
        issues=issues,
    )
