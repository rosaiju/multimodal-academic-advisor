"""Course unlock explorer: built on the planner's own prerequisite functions."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.audit.impact import explore_unlocks
from app.audit.prereq_graph import unlocks
from app.audit.record import CompletedCourse, StudentRecord
from app.catalog.registry import registry
from app.config import get_settings


@pytest.fixture(scope="module")
def program():
    if not registry.is_loaded:
        registry.load(get_settings().catalog_dir)
    return registry.get("morgan_cosc_bs_2026_2028")


def rec(*codes: str, grade: str = "A") -> StudentRecord:
    return StudentRecord(
        student_id="s",
        completed=[
            CompletedCourse(code=c, term="Fall 2024", grade=grade, credits=Decimal(4))
            for c in codes
        ],
    )


def test_direct_unlocks_match_the_graph(program) -> None:
    report = explore_unlocks(program, rec("COSC111", "COSC112"), "cosc 220")
    assert report.known and report.code == "COSC220"
    assert {e.course.code for e in report.direct_unlocks} == set(unlocks(program, "COSC220"))
    assert all(e.depth == 1 and e.path[0] == "COSC220" for e in report.direct_unlocks)


def test_downstream_includes_indirect_dependents(program) -> None:
    report = explore_unlocks(program, rec("COSC111"), "COSC112")
    direct = {e.course.code for e in report.direct_unlocks}
    downstream = {e.course.code for e in report.downstream_unlocks}
    assert "COSC220" in direct
    assert downstream and not downstream & direct
    for e in report.downstream_unlocks:
        assert e.depth >= 2 and len(e.path) == e.depth + 1


def test_student_specific_eligibility_and_missing_after(program) -> None:
    report = explore_unlocks(program, rec("COSC111", "COSC112", "MATH141"), "COSC220")
    entry = next(e for e in report.direct_unlocks if e.course.code == "COSC354")
    assert entry.status == "blocked"
    assert entry.missing_prerequisites == ["COSC220", "COSC241"]
    assert entry.missing_after == ["COSC241"]  # finishing COSC220 leaves only COSC241
    easy = next(e for e in report.direct_unlocks if e.course.code == "COSC352")
    assert easy.missing_after == []


def test_status_reflects_the_record(program) -> None:
    report = explore_unlocks(program, rec("COSC111", "COSC112", "COSC220"), "COSC220")
    assert report.status == "completed"
    entry = next(e for e in report.direct_unlocks if e.course.code == "COSC352")
    assert entry.status == "eligible"


def test_prerequisite_tree(program) -> None:
    report = explore_unlocks(program, rec("COSC111"), "COSC220")
    tree = report.prerequisite_tree
    assert tree is not None and tree.code == "COSC220"
    child = tree.children[0]
    assert child.code == "COSC112" and child.status == "eligible"
    assert child.children[0].code == "COSC111" and child.children[0].status == "completed"


def test_unknown_course_is_not_invented(program) -> None:
    report = explore_unlocks(program, rec("COSC111"), "COSC999")
    assert report.known is False and report.course is None
    assert report.direct_unlocks == [] and report.downstream_unlocks == []


def test_edges_cover_every_listed_unlock(program) -> None:
    report = explore_unlocks(program, rec(), "COSC112")
    listed = {e.course.code for e in [*report.direct_unlocks, *report.downstream_unlocks]}
    assert listed <= {child for _, child in report.edges}
