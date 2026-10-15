"""Ask, don't guess, when a spoken course number has no subject.

"What do I need before four fifty nine?" names a number but not a course.
`course_codes.normalize_course_mentions` rightly leaves it alone: it only rewrites
"<subject> <number>" into a code that exists. This module finds the leftover bare
numbers and, where the catalog has a course with that number, offers it back as a
QUESTION - "Did you mean COSC 459?" - for the student to confirm.

Rules, in order of importance:

* Nothing is ever applied here. The transcript is untouched; the suggestion goes
  alongside it and the student accepts it or ignores it.
* A suggestion is a code that exists in the catalog. A number nothing in the
  catalog uses produces no suggestion at all.
* More than one catalog course with that number means more than one candidate and
  a question that names them all. Conversation context only reorders them (a
  subject the student was just discussing comes first); it never picks for them.
* Deterministic throughout. No model is involved.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from app.speech.course_codes import (
    SUBJECT_ALIASES,
    _parse_course_number,
    _tokens_after,
)

_WORD = re.compile(r"[A-Za-z]+|\d+")
_CODE = re.compile(r"^([A-Z]+)(\d{3})$")
#: A number followed by one of these is a quantity, not a course ("120 credits").
_QUANTITY_WORDS = {"credit", "credits", "percent", "hours", "hour", "points", "more", "semesters"}


@dataclass(frozen=True)
class Clarification:
    #: The exact text in the transcript to replace if the student accepts.
    spoken: str
    #: Catalog courses with that number, "COSC 459" form, most likely first.
    candidates: tuple[str, ...]
    question: str

    def as_dict(self) -> dict:
        return {
            "spoken": self.spoken,
            "candidates": list(self.candidates),
            "question": self.question,
        }


def _subject_words(subjects: set[str]) -> set[str]:
    words = {s.lower() for s in subjects}
    for subject in subjects:
        for alias in SUBJECT_ALIASES.get(subject, ()):
            words.update(alias.lower().split())
    return words


def _question(candidates: Sequence[str]) -> str:
    if len(candidates) == 1:
        return f"Did you mean {candidates[0]}?"
    return "Did you mean " + ", ".join(candidates[:-1]) + f" or {candidates[-1]}?"


def subjects_in(text: str, catalog_codes: Iterable[str]) -> list[str]:
    """Catalog subjects named in `text`, in order of first mention (for context)."""
    codes = {c.upper() for c in catalog_codes}
    known = {m[1] for c in codes if (m := _CODE.match(c))}
    found: list[str] = []
    for match in re.finditer(r"\b([A-Za-z]{2,5})[\s\-]?\d{3}\b", text):
        subject = match[1].upper()
        if subject in known and subject not in found:
            found.append(subject)
    return found


def suggest_clarifications(
    text: str,
    catalog_codes: Iterable[str],
    context_subjects: Sequence[str] = (),
) -> list[Clarification]:
    """Bare course numbers in `text` that match catalog courses, as questions."""
    codes = sorted({c.upper().replace(" ", "") for c in catalog_codes})
    by_number: dict[str, list[str]] = {}
    for code in codes:
        if match := _CODE.match(code):
            by_number.setdefault(match[2], []).append(code)
    if not by_number:
        return []

    subject_words = _subject_words({m[1] for c in codes if (m := _CODE.match(c))})
    context = [s.upper() for s in context_subjects]

    out: list[Clarification] = []
    consumed_until = 0
    for match in _WORD.finditer(text):
        if match.start() < consumed_until:
            continue
        rest = _tokens_after(text, match.end())
        words = [match[0], *(word for word, _ in rest)]
        parsed = _parse_course_number(words)
        if parsed is None:
            continue
        number, used = parsed
        end = match.end() if used == 1 else rest[used - 2][1]
        consumed_until = end

        # "COSC 999", "computer science ..." - a subject right before means the
        # student already named one; this is not a bare number.
        before = _WORD.findall(text[: match.start()])
        previous = before[-1] if before else ""
        if previous.lower() in subject_words or (previous.isupper() and 2 <= len(previous) <= 5):
            continue
        after = _tokens_after(text, end)
        if after and after[0][0].lower() in _QUANTITY_WORDS:
            continue

        matches = by_number.get(number)
        if not matches:
            continue
        pairs = [(m[1], m[2]) for c in matches if (m := _CODE.match(c))]
        pairs.sort(key=lambda sn: (sn[0] not in context, sn[0]))
        shown = tuple(f"{subject} {num}" for subject, num in pairs)
        out.append(Clarification(text[match.start() : end], shown, _question(shown)))
    return out
