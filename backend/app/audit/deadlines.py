"""Dates a student plans around, personalised from their own record.

The calendar is a curated, sourced file (`data/calendar/`). This module reads it,
works out which registration group a student falls in from their earned credits,
and says what is open, upcoming or past relative to a given day.

It invents no date. A deadline the calendar does not have is reported as "not yet
sourced" with where to look, never as an absence of a deadline.

*** NO LLM CODE. Nothing in app/audit/ may import app/llm/ or app/advisor/. ***
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, HttpUrl, ValidationError, model_validator

from app.schemas.provenance import Provenance

Kind = Literal["registration", "graduation", "academic", "financial"]
State = Literal["past", "open", "upcoming"]

#: How far ahead an upcoming date is flagged as "soon".
SOON_DAYS = 14


class CalendarError(Exception):
    """The calendar file is missing or malformed."""


class Standing(BaseModel):
    id: str
    label: str
    min_credits: int


class CalendarItem(BaseModel):
    id: str
    title: str
    kind: Kind
    date: date
    end_date: date | None = None
    standing: str | None = Field(default=None, description="Only this registration group.")
    detail: str = ""
    source: HttpUrl

    @model_validator(mode="after")
    def _range_is_ordered(self) -> CalendarItem:
        if self.end_date is not None and self.end_date < self.date:
            raise ValueError(f"{self.id}: end_date is before date")
        return self


class NotYetSourced(BaseModel):
    title: str
    where_to_look: str
    source: HttpUrl


class Calendar(BaseModel):
    version: int
    institution: str
    retrieved: date
    standings: list[Standing]
    items: list[CalendarItem]
    not_yet_sourced: list[NotYetSourced] = Field(default_factory=list)

    @model_validator(mode="after")
    def _standings_resolve(self) -> Calendar:
        known = {s.id for s in self.standings}
        ids: set[str] = set()
        for item in self.items:
            if item.id in ids:
                raise ValueError(f"duplicate calendar item {item.id!r}")
            ids.add(item.id)
            if item.standing is not None and item.standing not in known:
                raise ValueError(f"{item.id}: unknown standing {item.standing!r}")
        return self


def load_calendar(path: str | Path) -> Calendar:
    try:
        raw = yaml.safe_load(Path(path).read_text())
        return Calendar.model_validate(raw)
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise CalendarError(f"{path}: {exc}") from exc


class Deadline(BaseModel):
    id: str
    title: str
    kind: Kind
    date: date
    end_date: date | None
    detail: str
    source: str
    state: State
    #: Days until the date (negative once past). For an open window, until it closes.
    days_away: int
    soon: bool
    is_yours: bool = Field(description="True when it targets this student's own group.")


class DeadlineReport(BaseModel):
    as_of: date
    calendar_retrieved: date
    earned_credits: Decimal
    standing: Standing | None
    deadlines: list[Deadline]
    not_yet_sourced: list[NotYetSourced]
    caveats: list[str]
    provenance: Provenance = Provenance.VERIFIED


def standing_for(calendar: Calendar, earned_credits: Decimal) -> Standing | None:
    """The registration group for this many earned credits, highest threshold first."""
    for standing in sorted(calendar.standings, key=lambda s: s.min_credits, reverse=True):
        if earned_credits >= standing.min_credits:
            return standing
    return None


def _state(item: CalendarItem, today: date) -> tuple[State, int]:
    last = item.end_date or item.date
    if today > last:
        return "past", (last - today).days
    if today >= item.date:
        return "open", (last - today).days
    return "upcoming", (item.date - today).days


def build_deadlines(
    calendar: Calendar, earned_credits: Decimal, *, today: date | None = None
) -> DeadlineReport:
    today = today or date.today()
    standing = standing_for(calendar, earned_credits)

    out: list[Deadline] = []
    for item in calendar.items:
        # A group-specific date is shown only to that group.
        if item.standing is not None and (standing is None or item.standing != standing.id):
            continue
        state, days = _state(item, today)
        out.append(
            Deadline(
                id=item.id,
                title=item.title,
                kind=item.kind,
                date=item.date,
                end_date=item.end_date,
                detail=item.detail,
                source=str(item.source),
                state=state,
                days_away=days,
                soon=state != "past" and days <= SOON_DAYS,
                is_yours=item.standing is not None,
            )
        )
    # Open and upcoming first, soonest first; past ones last.
    out.sort(key=lambda d: (d.state == "past", d.date))

    caveats = [
        "Morgan says these dates are subject to change. Check the linked page before acting.",
        "Earned credits include transfer credit.",
    ]
    if standing is not None:
        caveats.append(
            "Honors, athlete, veteran and SDSS students open priority registration in the "
            "first window whatever their standing. This page can't tell if that's you."
        )
    return DeadlineReport(
        as_of=today,
        calendar_retrieved=calendar.retrieved,
        earned_credits=earned_credits,
        standing=standing,
        deadlines=out,
        not_yet_sourced=calendar.not_yet_sourced,
        caveats=caveats,
    )


def _ics_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", r"\;").replace(",", "\\,").replace("\n", "\\n")


def to_ics(report: DeadlineReport, *, now: datetime | None = None) -> str:
    """An iCalendar file with one all-day event per open or upcoming date.

    Each event carries a one-day-before alert, which is how a reminder reaches a
    student who isn't in this app: their own calendar app does the reminding.
    """
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Academic Advisor//Deadlines//EN",
        "CALSCALE:GREGORIAN",
    ]
    for d in report.deadlines:
        if d.state == "past":
            continue
        # An all-day DTEND is exclusive, so a one-day event ends the next day.
        last = d.end_date or d.date
        description = f"{d.detail} Source: {d.source} Dates are subject to change.".strip()
        lines += [
            "BEGIN:VEVENT",
            f"UID:{d.id}@academic-advisor",
            f"DTSTAMP:{stamp}",
            f"DTSTART;VALUE=DATE:{d.date:%Y%m%d}",
            f"DTEND;VALUE=DATE:{last + timedelta(days=1):%Y%m%d}",
            f"SUMMARY:{_ics_escape(d.title)}",
            f"DESCRIPTION:{_ics_escape(description)}",
            "BEGIN:VALARM",
            "ACTION:DISPLAY",
            f"DESCRIPTION:{_ics_escape(d.title)}",
            "TRIGGER:-P1D",
            "END:VALARM",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"
