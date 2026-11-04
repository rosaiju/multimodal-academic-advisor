"""End to end: one fictional advanced student through audit, plan, schedule, explain.

"Avery" is invented. 90 earned credits, including:

* a retake        - COSC220 failed (F), then passed with a C;
* transfer credit - ENGL101 from CCBC equated as B, and a COSC116TR bucket course;
* in progress     - COSC349, this term;
* a withdrawal    - MATH312 (W);
* 16 courses the partial Morgan catalog does not name.

Every number below was worked out by hand from the rules in docs/engine-rules.md.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from app.audit.engine import run_audit
from app.audit.explain_audit import explain_block
from app.audit.planning import build_plan
from app.audit.record import CompletedCourse, StudentRecord
from app.audit.schedule import generate_schedule, validate_schedule
from app.catalog.loader import load_program
from app.catalog.schema import Program
from app.schemas.audit import BlockStatus, MatcherStrategy
from app.schemas.provenance import Provenance

MORGAN = Path(__file__).resolve().parents[2] / "data" / "catalog" / "morgan_cosc_bs_2026_2028.yaml"

# (code, term, grade, credits, institution)
ROWS = [
    ("ENGL101", "Fall 2022", "B", 3, "Community College of Baltimore County"),
    ("COSC116TR", "Fall 2022", "B", 3, "Community College of Baltimore County"),
    ("UNIV101", "Fall 2023", "A", 1, None),
    ("COSC111", "Fall 2023", "A", 4, None),
    ("MATH241", "Fall 2023", "B", 4, None),
    ("ENGL102", "Fall 2023", "A", 3, None),
    ("HIST101", "Fall 2023", "B", 3, None),
    ("COSC112", "Spring 2024", "B", 4, None),
    ("MATH242", "Spring 2024", "C", 4, None),
    ("PSYC101", "Spring 2024", "A", 3, None),
    ("BIOL101", "Spring 2024", "B", 4, None),
    ("COSC220", "Fall 2024", "F", 4, None),
    ("COSC201", "Fall 2024", "A", 1, None),
    ("PHIL109", "Fall 2024", "A", 3, None),
    ("CHEM101", "Fall 2024", "C", 3, None),
    ("ECON211", "Fall 2024", "B", 3, None),
    ("COSC220", "Spring 2025", "C", 4, None),
    ("COSC281", "Spring 2025", "B", 3, None),
    ("HLTH103", "Spring 2025", "B", 3, None),
    ("SPAN101", "Spring 2025", "B", 3, None),
    ("SOCI101", "Spring 2025", "A", 3, None),
    ("COSC243", "Fall 2025", "B", 3, None),
    ("MATH312", "Fall 2025", "W", 3, None),
    ("SPAN102", "Fall 2025", "B", 3, None),
    ("ART101", "Fall 2025", "A", 3, None),
    ("MUSC101", "Fall 2025", "B", 3, None),
    ("COSC459", "Spring 2026", "A", 3, None),
    ("PHEC101", "Spring 2026", "A", 1, None),
    ("PHIL220", "Spring 2026", "B", 3, None),
    ("ENGL201", "Spring 2026", "B", 3, None),
    ("COMM201", "Spring 2026", "A", 3, None),
    ("GEOG101", "Spring 2026", "B", 3, None),
    ("COSC349", "Fall 2026", "IP", 3, None),
]


@pytest.fixture(scope="module")
def morgan() -> Program:
    return load_program(MORGAN)


@pytest.fixture(scope="module")
def avery() -> StudentRecord:
    return StudentRecord(
        student_id="avery-fictional",
        completed=[
            CompletedCourse(
                code=code,
                term=term,
                grade=grade,
                credits=Decimal(credits),
                institution=institution,
                provenance=Provenance.STUDENT_CONFIRMED,
            )
            for code, term, grade, credits, institution in ROWS
        ],
    )


@pytest.fixture(scope="module", params=list(MatcherStrategy))
def audit(request, morgan, avery):
    return run_audit(morgan, avery, request.param)


def block(audit, block_id):
    return next(b for b in audit.blocks if b.block_id == block_id)


class TestAudit:
    def test_credit_totals(self, audit) -> None:
        # Catalog courses passed, each once: 43. Outside the catalog: 47.
        # COSC220's F, MATH312's W and COSC349 (IP) earn nothing.
        assert audit.total_credits_earned == Decimal(90)
        assert audit.total_credits_in_progress == Decimal(3)

    def test_major_block(self, audit) -> None:
        major = block(audit, "major_required_courses")
        assert major.status is BlockStatus.IN_PROGRESS
        applied = {a.course.code: a.grade for a in major.applied}
        assert applied == {
            "COSC111": "A",
            "COSC112": "B",
            "COSC220": "C",  # the retake, not the F
            "COSC243": "B",
            "COSC281": "B",
            "COSC459": "A",
        }
        still = {r.code for r in major.still_needed}
        assert "COSC349" in still  # in progress is not done
        assert len(still) == 6

    def test_withdrawal_leaves_supporting_courses_open(self, audit) -> None:
        supporting = block(audit, "supporting_courses")
        assert {a.course.code for a in supporting.applied} == {"MATH241", "MATH242", "COSC201"}
        assert {r.code for r in supporting.still_needed} == {"MATH312", "MATH331"}

    def test_transfer_course_satisfies_composition(self, audit) -> None:
        assert block(audit, "gen_ed_composition").status is BlockStatus.SATISFIED

    def test_gen_ed_and_seminar(self, audit) -> None:
        assert block(audit, "gen_ed_critical_thinking").status is BlockStatus.SATISFIED
        assert block(audit, "gen_ed_health").status is BlockStatus.SATISFIED
        assert block(audit, "gen_ed_contemporary_issues").status is BlockStatus.UNMET
        assert block(audit, "first_year_seminar").status is BlockStatus.SATISFIED

    def test_no_course_counts_twice(self, audit) -> None:
        codes = [a.course.code for b in audit.blocks for a in b.applied]
        assert len(codes) == len(set(codes))

    def test_outside_catalog_credit_is_reported_not_applied(self, audit) -> None:
        outside = {c.code for c in audit.outside_catalog}
        assert len(outside) == 16
        bucket = next(c for c in audit.outside_catalog if c.code == "COSC116TR")
        assert bucket.institution == "Community College of Baltimore County"

    def test_gpa_is_a_range_because_policy_is_not_encoded(self, audit) -> None:
        gpa = audit.gpa
        assert gpa.cumulative is not None
        assert gpa.cumulative_low < gpa.cumulative_high
        assert any("COSC220" in n for n in gpa.notes)
        assert any("transfer" in n for n in gpa.notes)
        # Even the lowest reading clears 2.0, so the requirement is decided.
        assert gpa.cumulative_low >= Decimal("2.0")
        assert block(audit, "cumulative_gpa").status is BlockStatus.SATISFIED

    def test_failed_then_passed_is_not_reported_as_passed_twice(self, audit) -> None:
        assert not any("Passed more than once" in n for n in audit.notes)

    def test_never_graduation_eligible(self, audit) -> None:
        assert audit.is_graduation_eligible is False
        assert audit.percent_complete is None
        assert any("partly encoded" in r for r in audit.graduation_blockers)


class TestPlan:
    def test_plan(self, morgan, avery) -> None:
        audit = run_audit(morgan, avery)
        plan = build_plan(morgan, avery, audit, limit=100)
        recommended = {r.course.code for r in plan.recommended}
        blocked = {r.course.code: r for r in plan.blocked}

        assert {r.code for r in plan.under_way} == {"COSC349"}
        assert "COSC349" not in recommended | set(blocked)
        assert "MATH312" in recommended  # the W is retaken
        assert "COSC352" in recommended  # COSC220's C meets the C prerequisite
        assert "COSC354" in blocked  # needs COSC241
        assert "COSC220" not in recommended | set(blocked)
        assert any("in progress" in c for c in plan.caveats)


class TestSchedule:
    def test_schedule(self, morgan, avery) -> None:
        plan = generate_schedule(morgan, avery, start_term="Spring 2027")
        planned = [c.course.code for t in plan.terms for c in t.courses]

        assert len(planned) == len(set(planned))
        assert "COSC349" not in planned and "COSC220" not in planned
        assert "MATH141" not in planned
        assert {"MATH312", "MATH331", "COSC352", "COSC458", "COSC490"} <= set(planned)
        assert all(t.credits <= plan.credit_target for t in plan.terms)
        assert plan.coverage_complete is False
        assert plan.last_planned_term is not None
        assert "not a graduation date" in plan.graduation_caveat
        assert {u.course.code for u in plan.unscheduled} >= {"MATH141", "COSC354"}

        layout = [(t.term, [c.course.code for c in t.courses]) for t in plan.terms]
        assert validate_schedule(morgan, avery, layout).valid


class TestExplain:
    def test_major_block_explains_the_in_progress_course(self, morgan, avery) -> None:
        audit = run_audit(morgan, avery)
        out = explain_block(morgan, avery, audit, "major_required_courses")
        reasons = {n.code: n.reason for n in out.not_counted}
        assert reasons["COSC349"].startswith("In progress")
        option = next(o for o in out.options if o.code == "COSC349")
        assert option.in_progress and not option.eligible_now
