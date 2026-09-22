"""Degree audit endpoints.

Closes the loop: a student uploads a transcript, confirms what it says, and asks
how far through the degree they are.

Everything here is a thin shell over `app.audit.engine`. No requirement logic lives
in this file, and nothing in it may import the LLM layer - the answer to "can I
graduate" is computed, never generated.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.audit.engine import run_audit
from app.audit.planning import AdvisingPlan, build_plan
from app.catalog.loader import CatalogError
from app.catalog.registry import registry
from app.catalog.schema import Program
from app.config import get_settings
from app.ingestion.store import RecordStore, StoredRecordError
from app.schemas.audit import AuditResult, BlockStatus, MatcherStrategy

router = APIRouter(tags=["audit"])


def _store() -> RecordStore:
    return RecordStore(get_settings().student_record_dir)


def _program(program_id: str) -> Program:
    try:
        return registry.get(program_id)
    except CatalogError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


class AuditSummary(BaseModel):
    """The short answer, for a dashboard that does not want every block."""

    student_id: str
    program_id: str
    catalog_year: str

    credits_applied: str
    credits_required: str
    percent_complete: float

    satisfied: int
    in_progress: int
    unmet: int
    needs_advisor: int

    #: False whenever the catalog is incomplete, whatever the blocks say.
    graduation_eligible: bool
    #: Why the answer above may be understated. Never omitted.
    coverage: str


@router.get("/students/{student_id}/audit", response_model=AuditResult)
def get_audit(
    student_id: str,
    program_id: Annotated[
        str | None, Query(description="Defaults to the program stored on the record.")
    ] = None,
    strategy: Annotated[
        MatcherStrategy,
        Query(description="optimal_bipartite never reports less progress than greedy."),
    ] = MatcherStrategy.OPTIMAL_BIPARTITE,
) -> AuditResult:
    """Audit a student's confirmed coursework against a degree program.

    Only STUDENT_CONFIRMED coursework is read - the store refuses to load a record
    containing anything else, and the engine filters on provenance again.

    The default strategy is optimal because greedy can UNDER-report: it may spend a
    course on an early requirement that was the only thing able to satisfy a later
    one. Telling a student to retake something they have already done is the worse
    error, so the better matcher is the default rather than the opt-in.
    """
    try:
        stored = _store().load(student_id)
    except StoredRecordError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    resolved = program_id or stored.program_id
    if resolved is None:
        raise HTTPException(
            status_code=400,
            detail=(
                f"no program on record for {student_id!r} and none supplied. "
                "Pass ?program_id= to say which degree to audit against."
            ),
        )

    return run_audit(_program(resolved), stored.to_student_record(), strategy=strategy)


@router.get("/students/{student_id}/audit/summary", response_model=AuditSummary)
def get_audit_summary(
    student_id: str,
    program_id: Annotated[str | None, Query()] = None,
    strategy: Annotated[MatcherStrategy, Query()] = MatcherStrategy.OPTIMAL_BIPARTITE,
) -> AuditSummary:
    """Headline numbers only. Carries the coverage caveat with them, always."""
    result = get_audit(student_id, program_id=program_id, strategy=strategy)
    counts = {status: 0 for status in BlockStatus}
    for block in result.blocks:
        counts[block.status] += 1

    coverage = (
        result.coverage.summary
        if result.coverage is not None
        else "coverage was not reported by the engine"
    )
    return AuditSummary(
        student_id=result.student_id,
        program_id=result.program_id,
        catalog_year=result.catalog_year,
        credits_applied=str(result.total_credits_applied),
        credits_required=str(result.total_credits_required),
        percent_complete=round(result.percent_complete, 1),
        satisfied=counts[BlockStatus.SATISFIED],
        in_progress=counts[BlockStatus.IN_PROGRESS],
        unmet=counts[BlockStatus.UNMET],
        needs_advisor=counts[BlockStatus.NEEDS_ADVISOR],
        graduation_eligible=result.is_graduation_eligible,
        coverage=coverage,
    )


class StrategyComparison(BaseModel):
    """Side-by-side greedy vs optimal, for the Milestone 1 demo.

    Exists because the difference is the point: naive assignment under-reports a
    real student's progress, and showing that on one record is more convincing
    than describing it.
    """

    student_id: str
    greedy_satisfied: list[str]
    optimal_satisfied: list[str]
    only_with_optimal: list[str] = Field(
        description="Requirements the student HAS met that greedy fails to notice."
    )


@router.get("/students/{student_id}/audit/compare", response_model=StrategyComparison)
def compare_strategies(
    student_id: str, program_id: Annotated[str | None, Query()] = None
) -> StrategyComparison:
    greedy = get_audit(student_id, program_id=program_id, strategy=MatcherStrategy.GREEDY)
    optimal = get_audit(
        student_id, program_id=program_id, strategy=MatcherStrategy.OPTIMAL_BIPARTITE
    )

    def satisfied(result: AuditResult) -> list[str]:
        return sorted(b.block_id for b in result.blocks if b.status is BlockStatus.SATISFIED)

    greedy_ids, optimal_ids = satisfied(greedy), satisfied(optimal)
    return StrategyComparison(
        student_id=student_id,
        greedy_satisfied=greedy_ids,
        optimal_satisfied=optimal_ids,
        only_with_optimal=sorted(set(optimal_ids) - set(greedy_ids)),
    )


@router.get("/students/{student_id}/plan", response_model=AdvisingPlan)
def get_plan(
    student_id: str,
    program_id: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=30)] = 8,
) -> AdvisingPlan:
    """What is done, what is left, and what to take next.

    Built from the audit rather than a second computation, so the plan and the
    progress report can never disagree about what a student has finished.

    Every recommendation is derived from the catalog by deterministic rules and is
    tagged VERIFIED. The LLM layer may phrase this for a student; it may not
    decide it.
    """
    try:
        stored = _store().load(student_id)
    except StoredRecordError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    resolved = program_id or stored.program_id
    if resolved is None:
        raise HTTPException(
            status_code=400,
            detail=(
                f"no program on record for {student_id!r} and none supplied. "
                "Pass ?program_id= to say which degree to plan against."
            ),
        )

    program = _program(resolved)
    record = stored.to_student_record()
    audit = run_audit(program, record, strategy=MatcherStrategy.OPTIMAL_BIPARTITE)
    return build_plan(program, record, audit, limit=limit)
