"""Block explanations, course comparison and deadlines.

What these protect: every explanation is derived from the catalog and the confirmed
record, never invented; a date the calendar does not have is reported as missing,
not guessed; and nothing claims more than the partial catalog can support.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.audit.compare import ComparisonError, compare_courses
from app.audit.deadlines import (
    CalendarError,
    build_deadlines,
    load_calendar,
    standing_for,
    to_ics,
)
from app.audit.engine import run_audit
from app.audit.explain_audit import BlockNotFound, explain_all, explain_block
from app.audit.record import CompletedCourse, StudentRecord
from app.catalog.loader import load_program
from app.catalog.schema import Program
from app.schemas.audit import BlockStatus
from app.schemas.provenance import Provenance

DATA = Path(__file__).resolve().parents[2] / "data"


@pytest.fixture(scope="module")
def morgan() -> Program:
    return load_program(DATA / "catalog" / "morgan_cosc_bs_2026_2028.yaml")


@pytest.fixture(scope="module")
def calendar():
    return load_calendar(DATA / "calendar" / "morgan_2026_2027.yaml")


def record(*rows, provenance=Provenance.STUDENT_CONFIRMED) -> StudentRecord:
    return StudentRecord(
        student_id="jane",
        completed=[
            CompletedCourse(
                code=r[0],
                term="Fall 2025",
                grade=r[1],
                credits=Decimal(r[2]) if len(r) > 2 else Decimal(3),
                provenance=provenance,
            )
            for r in rows
        ],
    )


def explain(morgan, rec, block_id):
    return explain_block(morgan, rec, run_audit(morgan, rec), block_id)


class TestExplain:
    def test_says_what_counted(self, morgan: Program) -> None:
        e = explain(morgan, record(("COSC112", "A", 4)), "major_required_courses")
        assert [c.code for c in e.counted] == ["COSC112"]
        assert "more course" in e.headline

    def test_a_grade_below_the_minimum_is_named(self, morgan: Program) -> None:
        e = explain(morgan, record(("COSC111", "D", 4)), "major_required_courses")
        reason = next(n.reason for n in e.not_counted if n.code == "COSC111")
        assert "below the C" in reason

    def test_an_in_progress_course_counts_later_not_now(self, morgan: Program) -> None:
        e = explain(morgan, record(("COSC220", "IP", 4)), "major_required_courses")
        reason = next(n.reason for n in e.not_counted if n.code == "COSC220")
        assert "In progress" in reason

    def test_unconfirmed_coursework_is_not_counted_and_says_why(self, morgan: Program) -> None:
        rec = record(("COSC112", "A", 4), provenance=Provenance.UNVERIFIED_EXTRACTION)
        e = explain(morgan, rec, "major_required_courses")
        assert not e.counted
        assert any("Not confirmed" in n.reason for n in e.not_counted)

    def test_options_say_who_can_take_what_now(self, morgan: Program) -> None:
        e = explain(morgan, record(("COSC111", "A", 4)), "major_required_courses")
        by_code = {o.code: o for o in e.options}
        assert by_code["COSC112"].eligible_now
        assert not by_code["COSC354"].eligible_now
        assert by_code["COSC354"].missing_prerequisites
        # Courses a student can take are listed before courses they can't.
        flags = [o.eligible_now for o in e.options]
        assert flags == sorted(flags, reverse=True)

    def test_a_course_in_progress_is_not_offered_as_something_to_take(
        self, morgan: Program
    ) -> None:
        e = explain(
            morgan, record(("COSC111", "A", 4), ("COSC220", "IP", 4)), "major_required_courses"
        )
        option = next(o for o in e.options if o.code == "COSC220")
        assert option.in_progress and not option.eligible_now
        assert e.options[-1].in_progress or all(not o.in_progress for o in e.options[:-1])

    def test_gives_a_next_step_a_student_can_act_on(self, morgan: Program) -> None:
        e = explain(morgan, record(("COSC111", "A", 4)), "major_required_courses")
        assert e.next_step and e.next_step.startswith("Take ")

    def test_a_met_requirement_says_done(self, morgan: Program) -> None:
        e = explain(morgan, record(("COSC111", "A", 4)), "cumulative_gpa")
        assert e.status is BlockStatus.SATISFIED
        assert e.headline.startswith("Done")
        assert e.next_step is None

    def test_explains_every_block_and_marks_it_verified(self, morgan: Program) -> None:
        rec = record(("COSC111", "A", 4))
        audit = run_audit(morgan, rec)
        out = explain_all(morgan, rec, audit)
        assert len(out) == len(audit.blocks)
        assert all(e.provenance is Provenance.VERIFIED for e in out)

    def test_an_unknown_block_is_an_error(self, morgan: Program) -> None:
        with pytest.raises(BlockNotFound):
            explain(morgan, record(), "no_such_block")


def compare(morgan, rec, *codes):
    return compare_courses(morgan, rec, run_audit(morgan, rec), list(codes))


class TestCompare:
    def test_says_which_course_you_can_take_now(self, morgan: Program) -> None:
        c = compare(morgan, record(("COSC111", "A", 4)), "COSC112", "COSC354")
        states = {f.code: f.state for f in c.courses}
        assert states == {"COSC112": "eligible_now", "COSC354": "blocked"}
        assert any("You can take COSC112 now" in d for d in c.differences)
        assert any("COSC354 needs" in d for d in c.differences)

    def test_names_the_missing_prerequisites(self, morgan: Program) -> None:
        c = compare(morgan, record(), "COSC111", "COSC354")
        blocked = next(f for f in c.courses if f.code == "COSC354")
        assert set(blocked.missing_prerequisites) >= {"COSC220", "COSC241"}

    def test_a_passed_course_is_reported_as_passed(self, morgan: Program) -> None:
        c = compare(morgan, record(("COSC111", "A", 4)), "COSC111", "COSC112")
        assert next(f for f in c.courses if f.code == "COSC111").state == "passed"
        assert any("already passed COSC111" in d for d in c.differences)

    def test_in_progress_is_reported_as_such(self, morgan: Program) -> None:
        c = compare(morgan, record(("COSC112", "IP", 4), ("COSC111", "A", 4)), "COSC112", "COSC220")
        assert next(f for f in c.courses if f.code == "COSC112").state == "in_progress"

    def test_flags_a_course_that_advances_nothing_open(self, morgan: Program) -> None:
        c = compare(morgan, record(), "COSC111", "COSC251")
        by_code = {f.code: f for f in c.courses}
        assert by_code["COSC111"].advances_open_requirement
        if not by_code["COSC251"].advances_open_requirement:
            assert any("wouldn't advance" in d for d in c.differences)

    def test_lists_what_each_course_unlocks(self, morgan: Program) -> None:
        c = compare(morgan, record(), "COSC111", "COSC490")
        by_code = {f.code: f for f in c.courses}
        assert "COSC112" in by_code["COSC111"].unlocks
        assert by_code["COSC490"].unlocks == []

    def test_warns_about_irregular_offerings_and_unverified_stubs(self, morgan: Program) -> None:
        c = compare(morgan, record(), "MATH141", "COSC349")
        by_code = {f.code: f for f in c.courses}
        assert any("no verified title" in w for w in by_code["MATH141"].warnings)
        assert any("irregularly" in w for w in by_code["COSC349"].warnings)

    def test_says_when_the_catalog_is_partial(self, morgan: Program) -> None:
        c = compare(morgan, record(), "COSC111", "COSC112")
        assert any("Ask an advisor before ruling a course out" in x for x in c.caveats)

    def test_accepts_codes_with_spaces_and_any_case(self, morgan: Program) -> None:
        c = compare(morgan, record(), "cosc 111", "COSC112")
        assert [f.code for f in c.courses] == ["COSC111", "COSC112"]

    @pytest.mark.parametrize(
        "codes",
        [
            ["COSC111"],
            ["COSC111", "COSC111"],
            ["A1", "A2", "A3", "A4", "A5"],
            ["COSC111", "ZZZ999"],
        ],
    )
    def test_rejects_what_it_cannot_compare(self, morgan: Program, codes: list[str]) -> None:
        with pytest.raises(ComparisonError):
            compare(morgan, record(), *codes)


class TestCalendar:
    def test_every_item_has_an_official_source(self, calendar) -> None:
        assert calendar.items
        for item in calendar.items:
            assert str(item.source).startswith("https://www.morgan.edu/")

    def test_the_gaps_are_declared_not_hidden(self, calendar) -> None:
        titles = " ".join(n.title for n in calendar.not_yet_sourced)
        assert "add/drop" in titles
        assert "apply for graduation" in titles

    @pytest.mark.parametrize(
        ("credits", "expected"),
        [
            (0, "freshman"),
            (24, "freshman"),
            (25, "sophomore"),
            (55, "sophomore"),
            (56, "junior"),
            (89, "junior"),
            (90, "senior"),
            (150, "senior"),
        ],
    )
    def test_standing_follows_earned_credits(self, calendar, credits: int, expected: str) -> None:
        assert standing_for(calendar, Decimal(credits)).id == expected

    def test_a_student_sees_only_their_own_registration_date(self, calendar) -> None:
        report = build_deadlines(calendar, Decimal(60), today=date(2026, 11, 4))
        mine = [d for d in report.deadlines if d.is_yours]
        assert len(mine) == 1 and mine[0].date == date(2026, 11, 9)
        assert report.standing.id == "junior"

    def test_everyone_sees_general_registration_and_commencement(self, calendar) -> None:
        report = build_deadlines(calendar, Decimal(10), today=date(2026, 11, 4))
        ids = {d.id for d in report.deadlines}
        assert {"spring2027-general-registration", "fall2026-commencement"} <= ids

    def test_states_follow_the_calendar(self, calendar) -> None:
        d = {
            x.id: x
            for x in build_deadlines(calendar, Decimal(60), today=date(2026, 12, 20)).deadlines
        }
        assert d["spring2027-priority-junior"].state == "past"
        assert d["spring2027-general-registration"].state == "open"
        assert d["fall2026-commencement"].state == "past"
        assert d["spring2027-commencement"].state == "upcoming"

    def test_an_open_window_counts_down_to_its_close(self, calendar) -> None:
        d = {
            x.id: x
            for x in build_deadlines(calendar, Decimal(60), today=date(2027, 1, 31)).deadlines
        }
        assert d["spring2027-general-registration"].days_away == 2

    def test_soon_is_flagged_and_past_is_not(self, calendar) -> None:
        d = {
            x.id: x
            for x in build_deadlines(calendar, Decimal(60), today=date(2026, 11, 4)).deadlines
        }
        assert d["spring2027-priority-junior"].soon
        assert not d["spring2027-commencement"].soon

    def test_open_and_upcoming_sort_before_past(self, calendar) -> None:
        report = build_deadlines(calendar, Decimal(60), today=date(2026, 12, 20))
        states = [d.state for d in report.deadlines]
        assert states == sorted(states, key=lambda s: s == "past")

    def test_the_report_always_warns_dates_can_change(self, calendar) -> None:
        report = build_deadlines(calendar, Decimal(60), today=date(2026, 11, 4))
        assert any("subject to change" in c for c in report.caveats)
        assert report.not_yet_sourced

    def test_ics_has_an_event_and_a_reminder_for_each_future_date(self, calendar) -> None:
        report = build_deadlines(calendar, Decimal(60), today=date(2026, 12, 20))
        ics = to_ics(report)
        future = [d for d in report.deadlines if d.state != "past"]
        assert ics.count("BEGIN:VEVENT") == len(future)
        assert ics.count("TRIGGER:-P1D") == len(future)
        assert ics.startswith("BEGIN:VCALENDAR") and ics.rstrip().endswith("END:VCALENDAR")
        assert "\r\n" in ics

    def test_a_multi_day_window_ends_the_day_after_it_closes(self, calendar) -> None:
        ics = to_ics(build_deadlines(calendar, Decimal(60), today=date(2026, 11, 4)))
        assert "DTSTART;VALUE=DATE:20261130" in ics
        assert "DTEND;VALUE=DATE:20270203" in ics

    def test_a_malformed_calendar_stops_loudly(self, tmp_path: Path) -> None:
        bad = tmp_path / "c.yaml"
        bad.write_text("version: 1\n")
        with pytest.raises(CalendarError):
            load_calendar(bad)
        with pytest.raises(CalendarError):
            load_calendar(tmp_path / "missing.yaml")
