"""The degree audit engine.

Takes a validated catalog `Program` and a `StudentRecord`, assigns completed
courses to requirement blocks, and returns an `AuditResult`.

Two rules govern everything here:

1. **No double counting.** A course is applied to at most one requirement block.
   GPA blocks are the exception and consume nothing — a course counts toward your
   GPA *and* toward a requirement, which is not double counting.

2. **Never report more progress than the catalog can justify.** The Morgan catalog
   is deliberately incomplete: its disputed requirement blocks are absent rather
   than guessed. An audit against it therefore covers only part of the degree, and
   `AuditResult.coverage` says so explicitly rather than letting a caller read
   "4 of 6 blocks satisfied" as "two thirds of the way to a diploma".

*** NO LLM CODE. Nothing in app/audit/ may import app/llm/ or app/advisor/.
    A degree audit is computed, not predicted. ***
"""

from __future__ import annotations

from decimal import Decimal

from app.audit.evaluators import (
    applied_course,
    evaluate_all_of,
    evaluate_credits_from,
    evaluate_each_of_pool,
    evaluate_gpa,
    evaluate_n_of,
    evaluate_residency,
    hold_for_pass_grades,
)
from app.audit.grades import (
    earns_credit,
    gpa_figures,
    gpa_policy_note,
    is_in_progress,
    is_recognised,
)
from app.audit.optimal import SLOT_BLOCKS, assign_optimally
from app.audit.record import CompletedCourse, StudentRecord
from app.catalog.schema import (
    AllOfBlock,
    CreditsFromBlock,
    EachOfBlock,
    GpaBlock,
    NOfBlock,
    Program,
    RequirementBlock,
    ResidencyBlock,
)
from app.schemas.audit import (
    AuditResult,
    CatalogCoverage,
    GpaSummary,
    MatcherStrategy,
    RequirementBlockResult,
    UnrecognisedCourse,
)


def _is_passing(grade: str) -> bool:
    """True for a grade KNOWN to earn credit (A-D, P, S, CR).

    In-progress grades (IP, REG, INC) earn nothing yet, and a grade the engine does
    not recognise earns nothing until someone says what it means. Before this, REG
    and INC were counted as earned AND in progress at the same time, and any
    unknown grade (WF, NR, ...) was counted as earned.
    """
    return earns_credit(grade)


def _is_in_progress(grade: str) -> bool:
    """Registered but unfinished. Credit attempted, none earned."""
    return is_in_progress(grade)


#: Blocks that decide from the whole record rather than by consuming courses.
_RECORD_SCOPED = (GpaBlock, ResidencyBlock)


def _evaluate(
    program: Program, block: RequirementBlock, pool: list
) -> tuple[RequirementBlockResult, list[int]]:
    """Dispatch one block to its evaluator."""
    match block:
        case AllOfBlock():
            result, consumed = evaluate_all_of(program, block, pool)
        case NOfBlock():
            result, consumed = evaluate_n_of(program, block, pool)
        case EachOfBlock():
            result, consumed = evaluate_each_of_pool(program, block, pool)
        case CreditsFromBlock():
            result, consumed = evaluate_credits_from(program, block, pool)
        case GpaBlock():
            return evaluate_gpa(program, block, pool)
        case ResidencyBlock():
            return evaluate_residency(program, block, pool)
        case _:  # pragma: no cover
            raise ValueError(f"no evaluator for block type {block.type!r}")
    return hold_for_pass_grades(program, block, pool, consumed, result), consumed


def _coverage(program: Program) -> CatalogCoverage:
    """How much of the degree the encoded blocks actually cover.

    A course that satisfies no block is not necessarily an error — free electives
    exist — but in this catalog it mostly means the block that would claim it is
    still unencoded pending a department answer.
    """
    referenced: set[str] = set()
    for block in program.requirement_blocks:
        referenced.update(c.code for c in program.courses_for_block(block))

    uncovered = sorted({c.code for c in program.courses} - referenced)
    demanded = sum(
        (b.credits_required for b in program.requirement_blocks if isinstance(b, CreditsFromBlock)),
        Decimal(0),
    )
    return CatalogCoverage(
        blocks_encoded=len(program.requirement_blocks),
        courses_in_catalog=len(program.courses),
        courses_satisfying_no_block=len(uncovered),
        credits_explicitly_demanded=demanded,
        total_credits_required=program.total_credits_required,
    )


def _credit_totals(
    program: Program, completed: list[CompletedCourse]
) -> tuple[Decimal, Decimal, list[str]]:
    """Earned credit, in-progress credit, and what the engine could not decide.

    EARNED is every trusted credit-earning row on the record, catalog or not. It is
    a different quantity from total_credits_applied, which counts only what landed
    in an encoded block - conflating the two showed a student with 150 credits as
    45% done.

    A course the catalog names is earned ONCE however many times it was passed: a
    retake is one course. Codes the catalog does not name are summed row by row,
    because DegreeWorks gives many distinct transfer courses one bucket code
    (COSC116TR) and collapsing them would drop real credit.
    """
    notes: list[str] = []
    earned = Decimal(0)
    best: dict[str, Decimal] = {}
    passes: dict[str, int] = {}
    unknown: list[str] = []
    for c in completed:
        if not c.is_trusted:
            continue
        if not is_recognised(c.grade):
            unknown.append(f"{c.code} ({c.grade.strip()})")
            continue
        if not _is_passing(c.grade):
            continue
        if program.course(c.code) is not None:
            best[c.code] = max(best.get(c.code, Decimal(0)), c.credits)
            passes[c.code] = passes.get(c.code, 0) + 1
        else:
            earned += c.credits
    earned += sum(best.values(), Decimal(0))

    repeated = sorted(code for code, n in passes.items() if n > 1)
    if repeated:
        notes.append(
            "Passed more than once: " + ", ".join(repeated) + ". Credit is counted once "
            "per course. If one of these is repeatable for credit (a special topics "
            "course, for example), an advisor must confirm it - the catalog does not "
            "say which courses are."
        )
    if unknown:
        notes.append(
            "Unrecognised grade(s): " + ", ".join(unknown) + ". Their credit is NOT "
            "counted as earned until the registrar confirms what the grade means."
        )

    in_progress = sum(
        (c.credits for c in completed if c.is_trusted and _is_in_progress(c.grade)),
        Decimal(0),
    )
    return earned, in_progress, notes


def run_audit(
    program: Program,
    record: StudentRecord,
    strategy: MatcherStrategy = MatcherStrategy.GREEDY,
) -> AuditResult:
    """Audit `record` against `program`.

    GREEDY walks the blocks in catalog order, letting each take what it can from
    what is left. It is the honest baseline, and it can UNDER-report: a course spent
    on an elective block may have been the only thing that could satisfy a
    concentration later in the list. `demo_university_cs.yaml` is built to exhibit
    exactly that, and OPTIMAL_BIPARTITE (not yet implemented) is the fix.
    """
    completed = list(record.completed)
    available = list(range(len(completed)))
    blocks: list[RequirementBlockResult] = []

    # OPTIMAL decides every slot-based assignment up front, so no block can spend a
    # course a later block needed. GREEDY leaves the dict empty and each block takes
    # what it can from what is left, in catalog order.
    preassigned: dict[str, list[int]] = (
        assign_optimally(program, completed)
        if strategy is MatcherStrategy.OPTIMAL_BIPARTITE
        else {}
    )

    reserved: set[int] = {i for indices in preassigned.values() for i in indices}

    # Course CODES already applied somewhere. A retake is two transcript rows but
    # one course as far as the degree is concerned, so consuming one row must put
    # the other out of reach of every later block - under either strategy.
    used_codes: set[str] = set()

    def _free() -> list[int]:
        return [i for i in available if completed[i].code not in used_codes]

    for block in program.requirement_blocks:
        if isinstance(block, _RECORD_SCOPED):
            # Sees the whole record, consumes nothing.
            result, _ = _evaluate(program, block, completed)
            blocks.append(result)
            continue

        if strategy is MatcherStrategy.OPTIMAL_BIPARTITE and isinstance(block, SLOT_BLOCKS):
            # Hand the evaluator exactly what the global matching allotted, and let
            # it render status and still_needed the same way it always does.
            free = set(_free())
            indices = [i for i in preassigned.get(block.id, []) if i in free]
            result, consumed = _evaluate(program, block, [completed[i] for i in indices])
            blocks.append(result)
            taken = {indices[i] for i in consumed}
        else:
            pool_indices = _free()
            if strategy is MatcherStrategy.OPTIMAL_BIPARTITE:
                # credits_from is resolved from what the global matching LEFT OVER.
                # Without this, a credits_from block listed before a slot block in
                # the catalog spent the course the matching had given to that block.
                pool_indices = [i for i in pool_indices if i not in reserved]
            result, consumed = _evaluate(program, block, [completed[i] for i in pool_indices])
            blocks.append(result)
            taken = {pool_indices[i] for i in consumed}

        used_codes.update(completed[i].code for i in taken)
        available = [i for i in available if i not in taken]

    applied_courses = [a for b in blocks for a in b.applied]
    total_applied = sum((a.credits_applied for a in applied_courses), Decimal(0))

    unapplied = [
        applied_course(program, completed[i])
        for i in available
        if completed[i].is_trusted
        and program.course(completed[i].code) is not None
        and completed[i].code not in used_codes
    ]

    # Credit the catalog does not name - overwhelmingly transfer work. Reported so
    # the totals reconcile with the student's own audit, never applied: deciding
    # that an outside course satisfies a requirement is an evaluator's job.
    outside_catalog = [
        UnrecognisedCourse(
            code=c.code,
            term=c.term,
            grade=c.grade,
            credits=c.credits,
            institution=c.institution,
            provenance=c.provenance,
        )
        for c in completed
        if c.is_trusted and program.course(c.code) is None
    ]

    earned, in_progress_credits, notes = _credit_totals(program, completed)

    cumulative = gpa_figures(program, completed, "cumulative")
    major = gpa_figures(program, completed, "major")
    gpa_notes = [
        n
        for n in (gpa_policy_note(cumulative, "cumulative"), gpa_policy_note(major, "major"))
        if n is not None
    ]

    return AuditResult(
        student_id=record.student_id,
        program_id=program.program_id,
        catalog_year=program.catalog_year,
        total_credits_required=program.total_credits_required,
        total_credits_applied=total_applied,
        total_credits_earned=earned,
        total_credits_in_progress=in_progress_credits,
        gpa=GpaSummary(
            cumulative=cumulative.value,
            major=major.value,
            min_cumulative_required=program.min_gpa,
            min_major_required=program.min_major_gpa,
            cumulative_low=cumulative.low,
            cumulative_high=cumulative.high,
            major_low=major.low,
            major_high=major.high,
            notes=gpa_notes,
        ),
        blocks=blocks,
        unapplied=unapplied,
        outside_catalog=outside_catalog,
        critical_path_terms=None,
        critical_path=[],
        strategy=strategy,
        coverage=_coverage(program),
        notes=notes,
    )
