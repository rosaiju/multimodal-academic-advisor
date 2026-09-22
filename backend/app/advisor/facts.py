"""The only source of academic truth the advisor is allowed to use.

Everything in this module is computed by the degree engine. Nothing here calls a
model, and nothing here invents a requirement. `build_facts()` runs the same
`run_audit()` and `build_plan()` the dashboard endpoints run, from the same stored
record, so the chat answer and the audit screen can never disagree - if they ever
did, a student would be told two different things about whether they graduate.

`render_facts()` turns the snapshot into the plain-text block handed to a chat
model. That block is the model's entire permitted universe of academic fact.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from app.audit.engine import run_audit
from app.audit.planning import AdvisingPlan, Recommendation, build_plan
from app.audit.record import StudentRecord
from app.catalog.schema import Program
from app.schemas.audit import AuditResult, BlockStatus, MatcherStrategy


def _subject_of(code: str) -> str:
    """'COSC 220' -> 'COSC'. Empty when the code has no letter prefix."""
    letters = "".join(ch for ch in code if ch.isalpha())
    return letters.upper()


#: How many recommendations to put in front of the model. Eight is what the
#: dashboard shows; a longer list makes the prompt worse, not better.
RECOMMENDATION_LIMIT = 8


@dataclass(frozen=True)
class AdvisorFacts:
    """A deterministic snapshot of one student's standing.

    Frozen because a phrasing layer must not be able to edit the facts it was
    given, even accidentally.
    """

    student_id: str
    program_id: str
    program_name: str
    institution: str
    catalog_year: str

    credits_earned: Decimal
    credits_required: Decimal
    credits_remaining: Decimal
    credits_applied: Decimal
    credits_outside_catalog: Decimal
    credit_progress_percent: float

    #: None whenever the catalog is partial. A caller must render the absence
    #: rather than substitute a number - see `coverage`.
    percent_complete: float | None
    progress_is_partial: bool
    graduation_eligible: bool
    coverage: str

    #: Every subject prefix the catalog defines (COSC, MATH, ENGL...). Used to
    #: tell a real course reference from an ordinary phrase that happens to end
    #: in three digits - "need 101 more credits" is not a course.
    catalog_subjects: frozenset[str] = frozenset()

    completed: list[tuple[str, str, Decimal]] = field(default_factory=list)
    blocks: list[dict] = field(default_factory=list)
    recommended: list[Recommendation] = field(default_factory=list)
    blocked: list[Recommendation] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)

    audit: AuditResult | None = None
    plan: AdvisingPlan | None = None

    @property
    def has_coursework(self) -> bool:
        return bool(self.completed)

    def find_recommendation(self, code: str) -> Recommendation | None:
        """Look up one course among the recommendations, blocked ones included.

        Used to answer "why is this recommended?" from the engine's own reasons
        rather than letting a model improvise a justification.
        """
        wanted = code.replace(" ", "").upper()
        for rec in [*self.recommended, *self.blocked]:
            if rec.course.code.replace(" ", "").upper() == wanted:
                return rec
        return None


def build_facts(
    program: Program,
    record: StudentRecord,
    *,
    limit: int = RECOMMENDATION_LIMIT,
) -> AdvisorFacts:
    """Audit the record and assemble everything the advisor may state as fact."""
    audit = run_audit(program, record, strategy=MatcherStrategy.OPTIMAL_BIPARTITE)
    plan = build_plan(program, record, audit, limit=limit)

    outside = sum((c.credits for c in audit.outside_catalog), Decimal(0))
    remaining = audit.total_credits_required - audit.total_credits_earned
    if remaining < 0:
        remaining = Decimal(0)

    blocks = [
        {
            "block_id": block.block_id,
            "name": block.name,
            "status": block.status.value,
            "satisfied": block.status is BlockStatus.SATISFIED,
            "courses_still_needed": gap.courses_still_needed if gap else None,
            "credits_still_needed": gap.credits_still_needed if gap else None,
            "options": [c.code for c in (gap.options if gap else [])][:12],
        }
        for block in audit.blocks
        for gap in [next((g for g in plan.gaps if g.block_id == block.block_id), None)]
    ]

    return AdvisorFacts(
        student_id=audit.student_id,
        program_id=audit.program_id,
        program_name=program.program,
        institution=program.institution,
        catalog_year=audit.catalog_year,
        credits_earned=audit.total_credits_earned,
        credits_required=audit.total_credits_required,
        credits_remaining=remaining,
        credits_applied=audit.total_credits_applied,
        credits_outside_catalog=outside,
        credit_progress_percent=round(audit.credit_progress_percent, 1),
        percent_complete=(
            None if audit.percent_complete is None else round(audit.percent_complete, 1)
        ),
        progress_is_partial=audit.coverage is None or not audit.coverage.is_complete,
        graduation_eligible=audit.is_graduation_eligible,
        coverage=(
            audit.coverage.summary
            if audit.coverage is not None
            else "coverage was not reported by the engine"
        ),
        catalog_subjects=frozenset(
            _subject_of(course.code) for course in program.courses if _subject_of(course.code)
        ),
        completed=[(c.code, c.title or "", c.credits) for c in plan.completed],
        blocks=blocks,
        recommended=list(plan.recommended),
        blocked=list(plan.blocked),
        caveats=list(plan.caveats),
        audit=audit,
        plan=plan,
    )


def render_facts(facts: AdvisorFacts) -> str:
    """The student's record as plain text, for a chat model's system prompt.

    Everything a model is permitted to assert about this student is in here. The
    numbers are pre-formatted so the model never has to do arithmetic - asking a
    language model to subtract credits is exactly the failure this architecture
    exists to prevent.
    """
    lines: list[str] = [
        f"PROGRAM: {facts.program_name} ({facts.program_id}), {facts.institution}",
        f"CATALOG YEAR: {facts.catalog_year}",
        "",
        "CREDITS (authoritative, already computed - do not recalculate):",
        f"  earned: {facts.credits_earned}",
        f"  required for the degree: {facts.credits_required}",
        f"  still needed: {facts.credits_remaining}",
        f"  progress by credit: {facts.credit_progress_percent}%",
        f"  applied to an encoded requirement: {facts.credits_applied}",
        f"  earned but outside the encoded catalog: {facts.credits_outside_catalog}",
        f"  graduation eligible: {'yes' if facts.graduation_eligible else 'no'}",
    ]

    if facts.percent_complete is None:
        lines.append(
            "  degree completion percent: NOT AVAILABLE - the encoded catalog is "
            "incomplete, so no honest percentage exists. Do not estimate one."
        )
    else:
        lines.append(f"  degree completion percent: {facts.percent_complete}%")

    lines += ["", f"CATALOG COVERAGE: {facts.coverage}", ""]

    lines.append("COMPLETED AND CONFIRMED COURSEWORK:")
    if facts.completed:
        for code, title, credits in facts.completed:
            lines.append(f"  {code} {title} ({credits} cr)")
    else:
        lines.append("  (none confirmed yet)")

    lines += ["", "REQUIREMENT BLOCKS:"]
    for block in facts.blocks:
        head = f"  [{block['status']}] {block['name']} ({block['block_id']})"
        if block["courses_still_needed"]:
            head += (
                f" - still needs {block['courses_still_needed']} course(s),"
                f" {block['credits_still_needed']} credit(s)"
            )
        lines.append(head)
        if block["options"] and not block["satisfied"]:
            lines.append(f"      options: {', '.join(block['options'])}")

    lines += ["", "RECOMMENDED NEXT COURSES (eligible now, engine-derived):"]
    if facts.recommended:
        for rec in facts.recommended:
            why = "; ".join(rec.reasons) if rec.reasons else "advances a requirement"
            lines.append(
                f"  {rec.course.code} {rec.course.title} ({rec.course.credits} cr)"
                f" - serves {', '.join(rec.serves)} - unlocks {rec.unlocks_count}"
                f" later course(s) - {why}"
            )
            for warning in rec.warnings:
                lines.append(f"      warning: {warning}")
    else:
        lines.append("  (none - either nothing is eligible or no record exists)")

    if facts.blocked:
        lines += ["", "BLOCKED COURSES (prerequisites not yet met):"]
        for rec in facts.blocked:
            missing = ", ".join(rec.missing_prerequisites) or "unknown"
            lines.append(f"  {rec.course.code} {rec.course.title} - needs {missing}")

    if facts.caveats:
        lines += ["", "CAVEATS:"]
        lines += [f"  {c}" for c in facts.caveats]

    return "\n".join(lines)
