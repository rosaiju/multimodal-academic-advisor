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
    gpa_points,
)
from app.audit.record import StudentRecord
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
)

#: Blocks that decide from the whole record rather than by consuming courses.
_RECORD_SCOPED = (GpaBlock, ResidencyBlock)


def _evaluate(
    program: Program, block: RequirementBlock, pool: list
) -> tuple[RequirementBlockResult, list[int]]:
    """Dispatch one block to its evaluator."""
    match block:
        case AllOfBlock():
            return evaluate_all_of(program, block, pool)
        case NOfBlock():
            return evaluate_n_of(program, block, pool)
        case EachOfBlock():
            return evaluate_each_of_pool(program, block, pool)
        case CreditsFromBlock():
            return evaluate_credits_from(program, block, pool)
        case GpaBlock():
            return evaluate_gpa(program, block, pool)
        case ResidencyBlock():
            return evaluate_residency(program, block, pool)
    raise ValueError(f"no evaluator for block type {block.type!r}")  # pragma: no cover


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
    if strategy is not MatcherStrategy.GREEDY:
        raise NotImplementedError(
            f"strategy {strategy.value!r} is not implemented yet. Only GREEDY is "
            "available; see docs/icebox.md."
        )

    completed = list(record.completed)
    available = list(range(len(completed)))
    blocks: list[RequirementBlockResult] = []

    for block in program.requirement_blocks:
        if isinstance(block, _RECORD_SCOPED):
            # Sees the whole record, consumes nothing.
            result, _ = _evaluate(program, block, completed)
            blocks.append(result)
            continue

        sub_pool = [completed[i] for i in available]
        result, consumed = _evaluate(program, block, sub_pool)
        blocks.append(result)

        taken = {available[i] for i in consumed}
        available = [i for i in available if i not in taken]

    applied_courses = [a for b in blocks for a in b.applied]
    total_applied = sum((a.credits_applied for a in applied_courses), Decimal(0))

    trusted = [c for c in completed if c.is_trusted and program.course(c.code) is not None]
    majors = {s.upper() for s in program.major_subjects}
    major_courses = [c for c in trusted if program.course(c.code).subject in majors]

    unapplied = [
        applied_course(program, completed[i])
        for i in available
        if completed[i].is_trusted and program.course(completed[i].code) is not None
    ]

    return AuditResult(
        student_id=record.student_id,
        program_id=program.program_id,
        catalog_year=program.catalog_year,
        total_credits_required=program.total_credits_required,
        total_credits_applied=total_applied,
        total_credits_in_progress=Decimal(0),
        gpa=GpaSummary(
            cumulative=gpa_points(trusted),
            major=gpa_points(major_courses),
            min_cumulative_required=program.min_gpa,
            min_major_required=program.min_major_gpa,
        ),
        blocks=blocks,
        unapplied=unapplied,
        critical_path_terms=None,
        critical_path=[],
        strategy=strategy,
        coverage=_coverage(program),
    )
