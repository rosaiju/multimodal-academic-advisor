"""Does a model's rephrasing still say what the engine said?

`chat.ask()` lets a language model reword the deterministic answer. This module
decides whether the reworded text may be shown. It is deterministic - no model
grades another model here - and it errs towards rejecting: a rejected rephrasing
costs a student nothing, because the engine's own answer is shipped instead.

Checking only for invented course codes was not enough. The first live run against
a real model (qwen2.5:7b via Ollama, Oct 2026) produced replies that named only
real courses and were still wrong:

- "You need 105 more credits ... That's 12.5% of the total credits required."
  12.5% was progress made, not credits missing, and the earned and required
  totals had been dropped.
- Asked what they were taking now, a student was told what they had completed,
  with the in-progress course left out entirely.

Each check below is aimed at a failure of that kind. Each is a narrow pattern
rather than an understanding of English, so it cannot prove a reply right; it
catches the specific ways a rephrasing has been seen to go wrong, and the rest is
covered by the prompt and by shipping the engine's text whenever in doubt.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from app.advisor.answers import COURSE_CODE, GroundedAnswer, Intent
from app.advisor.facts import AdvisorFacts, render_facts

_NUMBER = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)(?![\w])")
_LIST_MARKER = re.compile(r"^\s*\d+[.)]\s", re.MULTILINE)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?;])\s+|\n+")

_NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}  # fmt: skip

_NEGATION = re.compile(
    r"\b(not|cannot|can't|can not|isn't|aren't|won't|no longer|until|unless|once|after|"
    r"before|first|yet|if you|when you|still missing|missing|need to (?:finish|complete|pass))\b",
    re.IGNORECASE,
)
_ELIGIBLE_CLAIM = re.compile(
    r"\b(eligible|can take|can register|can enroll|able to take|ready to take|qualify|"
    r"are cleared|go ahead and take|open to you|available to you)\b",
    re.IGNORECASE,
)
_INELIGIBLE_CLAIM = re.compile(
    r"\b(not eligible|ineligible|can't take|cannot take|not able to take|"
    r"not yet eligible|blocked)\b",
    re.IGNORECASE,
)
_DONE_CLAIM = re.compile(
    r"\b(completed|finished|passed|already took|already taken|earned credit|done with)\b",
    re.IGNORECASE,
)
_UNDERWAY_WORDS = re.compile(
    r"\b(in progress|currently|right now|this (?:term|semester)|taking|enrolled|"
    r"not (?:yet )?(?:counted|complete|finished)|until)\b",
    re.IGNORECASE,
)
_GRADUATION_CLAIM = re.compile(
    r"\b(eligible to graduate|ready to graduate|will graduate|can graduate|"
    r"cleared to graduate|on track to graduate|graduation eligible: yes)\b",
    re.IGNORECASE,
)
_PROGRESS_WORDS = re.compile(
    r"\b(way through|through|progress|completed|complete|done|earned|finished|along|"
    r"by credit)\b",
    re.IGNORECASE,
)
_PARTIAL_CATALOG = re.compile(
    r"\b(partial|incomplete|not (?:yet )?complete|not the whole|not all|awaiting|"
    r"clarification|not (?:been |yet )?(?:encoded|verified))\b",
    re.IGNORECASE,
)
_REFUSAL = re.compile(
    r"\b(cannot|can't|can not|unable|not able|don't have|do not have|won't|will not|"
    r"no information|outside|not something i|sorry|"
    r"(?:do|does|don't|doesn't)(?: not)? (?:include|cover|contain)|"
    r"not (?:part of|included in|covered by))\b",
    re.IGNORECASE,
)
_BLOCKER_KEPT = re.compile(
    r"\b(not eligible|not yet eligible|ineligible|can't take|cannot take|not yet|"
    r"prerequisite|need to (?:finish|complete|pass)|first|until|missing|blocked)\b",
    re.IGNORECASE,
)


def _code(match: re.Match[str]) -> str:
    return f"{match.group(1).upper()}{match.group(2)}"


def course_codes(text: str, subjects: frozenset[str]) -> set[str]:
    """Course codes in `text` whose subject the catalog defines.

    Restricting to catalog subjects is what stops "need 101 more" being read as
    a course called NEED101.
    """
    return {
        _code(m)
        for m in COURSE_CODE.finditer(text)
        if not subjects or m.group(1).upper() in subjects
    }


def _strip_codes(text: str, subjects: frozenset[str]) -> str:
    """Remove course codes so their digits are not read as quantities.

    Only codes with a catalog subject: the bare pattern also matches "of 120"
    and "need 106", and stripping those hid a changed credit total from the check.
    """
    return COURSE_CODE.sub(lambda m: " " if m.group(1).upper() in subjects else m.group(0), text)


def _digits(cleaned: str) -> set[Decimal]:
    found: set[Decimal] = set()
    for match in _NUMBER.finditer(cleaned):
        try:
            found.add(Decimal(match.group(1)))
        except InvalidOperation:  # pragma: no cover - the regex only matches digits
            continue
    return found


def numbers(text: str, subjects: frozenset[str] = frozenset()) -> set[Decimal]:
    """Every quantity in `text`, digits or a small spelled-out number.

    Decimal compares and hashes by value, so 15.00 and 15 are the same member.
    """
    cleaned = _LIST_MARKER.sub(" ", _strip_codes(text, subjects))
    found = _digits(cleaned)
    for word, value in _NUMBER_WORDS.items():
        if re.search(rf"\b{word}\b", cleaned, re.IGNORECASE):
            found.add(Decimal(value))
    return found


def numbers_in_digits(text: str, subjects: frozenset[str] = frozenset()) -> set[Decimal]:
    """Only figures written as digits. Spelled-out words are matched too loosely
    ("one of these") to count as a new figure."""
    return _digits(_LIST_MARKER.sub(" ", _strip_codes(text, subjects)))


def _fmt(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _sentences(text: str) -> list[str]:
    return [s for s in _SENTENCE_SPLIT.split(text) if s.strip()]


def allowed_codes(facts: AdvisorFacts, prepared: str, question: str = "") -> set[str]:
    """Every course code a rephrasing may mention.

    Anything in the facts block the model was given (which includes prerequisites
    the engine named as missing), anything in the engine's answer, and anything
    the student asked about - "I can't tell you who teaches COSC 241" is not an
    invention just because COSC 241 is not on the record.
    """
    subjects = facts.catalog_subjects
    return (
        course_codes(render_facts(facts), subjects)
        | course_codes(prepared, subjects)
        | course_codes(question, subjects)
    )


def invented_codes(
    text: str, allowed: set[str], subjects: frozenset[str] = frozenset()
) -> list[str]:
    """Course codes in `text` that are not in `allowed`.

    With no subjects supplied nothing is flagged, because guessing which words
    are course subjects is exactly the mistake that made "need 101" a course.
    """
    if not subjects:
        return []
    return sorted(course_codes(text, subjects) - allowed)


def _required_codes(prepared: str, subjects: frozenset[str]) -> set[str]:
    """Codes the rephrasing must keep.

    Everything the engine named except the "Options:" lists under an unmet
    block - those are a menu, and "COSC 220, COSC 243 and more" loses nothing a
    student acts on. Eligible courses, blockers, missing prerequisites and
    in-progress courses are all outside those lists, so all of them are kept.
    """
    kept = "\n".join(
        line for line in prepared.splitlines() if not line.strip().lower().startswith("options:")
    )
    return course_codes(kept, subjects)


#: "105 credits", "31 credit(s)", "12.5%". A figure stated as credits or as a
#: percentage is one a student acts on; a bare count ("4 requirement blocks",
#: "unlocks 1 later course") or a per-course "(3 cr)" may be reworded away.
_CREDIT_FIGURE = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s*(?:%|credit)", re.IGNORECASE)


def _required_numbers(facts: AdvisorFacts, prepared: str) -> set[Decimal]:
    """Credit figures the rephrasing must keep.

    The four headline totals wherever the engine's answer states them, plus every
    figure it states as credits or a percentage. Matching by context rather than
    by value matters: in the first live run "4 requirement blocks" was confused
    with the 4 credits a student had in progress, and a correct reply was thrown
    away.
    """
    headline = {
        facts.credits_earned,
        facts.credits_required,
        facts.credits_remaining,
        Decimal(str(facts.credit_progress_percent)),
    }
    required = {f for f in headline if f} & numbers(prepared, facts.catalog_subjects)
    required |= {
        Decimal(m.group(1))
        for m in _CREDIT_FIGURE.finditer(_strip_codes(prepared, facts.catalog_subjects))
    }
    return required


def check_rephrasing(
    prepared: GroundedAnswer | str,
    text: str,
    facts: AdvisorFacts,
    *,
    question: str = "",
) -> list[str]:
    """Every way `text` departs from the engine's answer. Empty means it may ship."""
    if isinstance(prepared, GroundedAnswer):
        prepared_text, intent, grounded = prepared.text, prepared.intent, prepared.grounded
    else:
        prepared_text, intent, grounded = prepared, None, True

    problems: list[str] = []
    subjects = facts.catalog_subjects

    if not text.strip():
        return ["the rephrasing was empty"]

    # 1. Course codes: nothing new, nothing that mattered dropped.
    invented = invented_codes(text, allowed_codes(facts, prepared_text, question), subjects)
    if invented:
        problems.append(f"mentions course codes the engine did not supply: {', '.join(invented)}")
    dropped = sorted(_required_codes(prepared_text, subjects) - course_codes(text, subjects))
    if dropped:
        problems.append(f"drops course codes from the engine's answer: {', '.join(dropped)}")

    # 2. Numbers: credit figures kept, and no figure the engine never produced.
    prepared_numbers = numbers(prepared_text, subjects)
    stated = numbers(text, subjects)
    missing = _required_numbers(facts, prepared_text) - stated
    if missing:
        problems.append(
            "drops credit figures from the engine's answer: "
            + ", ".join(_fmt(n) for n in sorted(missing))
        )
    known = prepared_numbers | numbers(render_facts(facts), subjects) | numbers(question, subjects)
    # Spelled-out small numbers are matched loosely ("one" in "one of these"), so
    # only digits can count as a new figure.
    new = sorted(n for n in numbers_in_digits(text, subjects) if n not in known)
    if new:
        problems.append(
            "states numbers the engine did not produce: " + ", ".join(_fmt(n) for n in new)
        )

    percent = f"{facts.credit_progress_percent}%"
    for sentence in _sentences(text):
        # 3. The progress percentage must stay a measure of progress.
        if percent in sentence and not _PROGRESS_WORDS.search(sentence):
            problems.append(
                f"uses {percent} without saying it is progress made: {sentence.strip()!r}"
            )

        codes_here = course_codes(sentence, subjects)
        blocked_here = codes_here & _codes(facts.blocked)
        eligible_here = codes_here & _codes(facts.recommended)
        under_way_here = codes_here & {c.replace(" ", "").upper() for c, _, _ in facts.in_progress}

        # 4. Eligibility must keep its polarity.
        if blocked_here and _ELIGIBLE_CLAIM.search(sentence) and not _NEGATION.search(sentence):
            problems.append(f"calls a blocked course available: {', '.join(sorted(blocked_here))}")
        if (
            eligible_here
            and not blocked_here
            and _INELIGIBLE_CLAIM.search(sentence)
            and not under_way_here
        ):
            problems.append(f"calls an eligible course blocked: {', '.join(sorted(eligible_here))}")

        # 5. An in-progress course is not a completed one.
        if under_way_here and _DONE_CLAIM.search(sentence) and not _UNDERWAY_WORDS.search(sentence):
            problems.append(
                f"calls an in-progress course completed: {', '.join(sorted(under_way_here))}"
            )

        # 6. No graduation promise the engine did not make.
        if (
            not facts.graduation_eligible
            and _GRADUATION_CLAIM.search(sentence)
            and not _NEGATION.search(sentence)
        ):
            problems.append(f"implies the student can graduate: {sentence.strip()!r}")

    # 7. The partial-catalog warning survives.
    if _PARTIAL_CATALOG.search(prepared_text) and not _PARTIAL_CATALOG.search(text):
        problems.append("drops the warning that the encoded catalog is partial")

    # 8. A blocker stays a blocker.
    if re.search(r"not (?:yet )?eligible", prepared_text, re.IGNORECASE) and not (
        _BLOCKER_KEPT.search(text)
    ):
        problems.append("drops the statement that a course is not yet available")

    # 9. A refusal stays a refusal - a model must not answer what the engine would not.
    if (not grounded or intent is Intent.UNKNOWN) and not _REFUSAL.search(text):
        problems.append("answers a question the engine declined to answer")

    return list(dict.fromkeys(problems))


def _codes(recs) -> set[str]:
    return {r.course.code.replace(" ", "").upper() for r in recs}
