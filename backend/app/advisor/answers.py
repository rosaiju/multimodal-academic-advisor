"""Deterministic answers to the questions a student actually asks.

This module answers advising questions with no model involved at all. Every
sentence it produces is assembled from `AdvisorFacts`, which came from the degree
engine.

Why this exists rather than just prompting a model:

1. The demo must work with no API key, no credits and no internet. A capstone
   that cannot be shown because a quota reset is a capstone that does not work.
2. It gives the model something to be graded against. When a provider IS
   configured, the model is asked to rephrase THIS text. If phrasing fails or
   the provider dies mid-sentence, the fallback is not an apology - it is the
   correct answer, already written.
3. Intent detection here is keyword-based. A model MAY choose the route instead
   (`understanding.py`, optional) - which of these engine answers to give - but
   it never writes one, and crisis and stress routing stay keyword-only.

The tone is deliberately plain. These answers are read by someone deciding what
to register for.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from app.advisor.facts import AdvisorFacts, _subject_of, every_option_required
from app.audit.compare import ComparisonError, compare_courses
from app.audit.explain_audit import BlockExplanation, explain_all
from app.audit.impact import PrereqNode, UnlockEntry, explore_unlocks
from app.audit.schedule import generate_schedule
from app.audit.simulation import SimulationRequest, simulate

#: Matches "COSC 220", "cosc220", "COSC-220".
COURSE_CODE = re.compile(r"\b([A-Za-z]{2,5})[\s\-]?(\d{3})\b")

_WHAT_IF_PHRASES = (
    "what if",
    "if i take",
    "if i took",
    "if i complete",
    "if i finish",
    "if i pass",
    "if i add",
    "if i were to",
    "would my plan look",
    "what would happen if",
    "what happens if",
    "if i fail",
    "if i don't pass",
    "if i do not pass",
    "if i withdraw",
    "if i drop",
)
#: Mentioning a course alongside one of these is an unlock/prerequisite question.
_UNLOCK_PHRASES = re.compile(
    r"\bunlock|\bopens? up|depends? on|leads? to|\bafter\b|"
    r"\bneed before|\bbefore (?:i can )?(?:take|taking)|prerequisite|prereq|required for"
)
#: These make a what-if mean "once I have passed it" rather than "this coming term".
_COMPLETION_WORDS = re.compile(r"\b(complete|completed|finish|finished|pass|passed|done with)\b")
#: A what-if about NOT passing a course. Checked before _COMPLETION_WORDS, because
#: "if I don't pass" contains "pass".
_FAIL_WORDS = re.compile(
    r"\b(fail|fails|failed|failing|flunk\w*|bomb\w*|tank\w*|don't pass|do not pass|not pass|"
    r"withdraw|drop)\b"
)

#: Words a student uses about themselves when they are struggling. Matched on what
#: they WROTE - nothing here infers a feeling from tone, and nothing diagnoses.
_CRISIS_PHRASES = (
    "kill myself",
    "killing myself",
    "suicide",
    "suicidal",
    "end my life",
    "end it all",
    "hurt myself",
    "harm myself",
    "self-harm",
    "self harm",
    "want to die",
    "don't want to live",
    "do not want to live",
    "no reason to live",
)
_STRESS_STRONG = re.compile(
    r"\b(overwhelm\w*|stressed|stressing|stress(?:ed)? out|anxious|anxiety|panic\w*|"
    r"freaking out|burn(?:ed|t)? ?out|hopeless|can'?t cope|cannot cope|giving up|"
    r"want to quit|losing my mind|breaking down|exhausted|struggling)\b"
)
_STRESS_WEAK = re.compile(r"\b(worried|worry|worrying|scared|afraid|nervous|terrified)\b")
_STRESS_TOPIC = re.compile(r"\b(graduat\w*|on time|behind|fail\w*|finish|degree|semester)\b")

#: "So I don't need any more, right?" - a student checking a conclusion. Routed to
#: the requirement view, because credits alone never finish a degree.
_CONFIRMATION = re.compile(
    r"\b((?:don'?t|do not|no longer) (?:\w+ ){0,3}need (?:any|more|anything|to take)|"
    r"(?:need|needs) no more|am i done|i'?m done|i am done|"
    r"can i graduate|ready to graduate|eligible to graduate|able to graduate|"
    r"allowed to graduate|good to graduate|finished with my degree)"
)
_PLAN_PHRASES = (
    "plan my",
    "plan out",
    "plan the rest",
    "semester plan",
    "semester-by-semester",
    "semester by semester",
    "term by term",
    "remaining semesters",
    "remaining terms",
    "rest of my semesters",
    "roadmap",
    "road map",
    "map out",
    "four year plan",
    "4 year plan",
    "4-year plan",
    "graduation plan",
    "degree plan",
    "when will i graduate",
    "when can i graduate",
    "when could i graduate",
    "how many semesters",
    "how many more semesters",
    "schedule my",
)
_COMPARE_WORDS = re.compile(r"\b(better|compare|comparison|versus|vs\.?|which|or|difference)\b")
_EXPLAIN = re.compile(
    r"\bwhy\b.*\b(not count|n'?t count|not counting|not apply|n'?t apply|not applied|"
    r"not (?:been )?(?:satisfied|met|done|complete|completed|fulfilled)|"
    r"(?:isn'?t|aren'?t|wasn'?t) (?:satisfied|met|done|complete|completed|fulfilled|counted|"
    r"counting)|still (?:open|outstanding|unmet|incomplete)|unsatisfied|unmet|incomplete)"
    r"|\bexplain\b.*\b(requirement|block|audit|count)"
    r"|\b(?:does|did|do|will) (?:\w+ ){0,3}count (?:toward|towards|for)\b"
)


class Intent(StrEnum):
    REMAINING_REQUIREMENTS = "remaining_requirements"
    CREDITS = "credits"
    NEXT_SEMESTER = "next_semester"
    MAJOR_COMPLETE = "major_complete"
    WHY_RECOMMENDED = "why_recommended"
    COURSE_INFO = "course_info"
    PROGRESS = "progress"
    IN_PROGRESS = "in_progress"
    WHAT_IF = "what_if"
    UNLOCKS = "unlocks"
    PLAN_SEMESTERS = "plan_semesters"
    COMPARE = "compare"
    EXPLAIN_REQUIREMENT = "explain_requirement"
    WELLBEING = "wellbeing"
    #: The student wrote about hurting themselves. Never routed by a model, never
    #: rephrased by one: the reply is fixed text pointing to people who can help.
    CRISIS = "crisis"
    NO_RECORD = "no_record"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class GroundedAnswer:
    """A deterministic answer plus the metadata the UI needs to label it."""

    text: str
    intent: Intent
    #: Course codes and block ids this answer rests on, so the UI can show the
    #: student exactly which catalog entries produced it.
    citations: list[str]
    #: False when the answer had to say "I cannot tell you that from the catalog".
    grounded: bool = True


def _norm(text: str) -> str:
    return " ".join(text.lower().strip().split())


def _q(text: str) -> str:
    """Normalised, with a typographic apostrophe (phones insert them) made plain."""
    return _norm(text).replace("\u2019", "'")


def detect_intent(question: str) -> Intent:
    """Keyword routing. Order matters: the most specific pattern wins."""
    q = _q(question)

    # Safety first, and keyword-only on purpose: a model is never on this path.
    if is_crisis(q):
        return Intent.CRISIS
    if is_stress(q):
        return Intent.WELLBEING

    # "Why does this course not count" must beat WHY_RECOMMENDED ("this course").
    if _EXPLAIN.search(q):
        return Intent.EXPLAIN_REQUIREMENT

    # "Why is COSC 220 recommended" must beat the generic course-info branch.
    if "why" in q and (
        "recommend" in q or "suggest" in q or "should i take" in q or "this course" in q
    ):
        return Intent.WHY_RECOMMENDED

    # Before NEXT_SEMESTER and MAJOR_COMPLETE: "what if I take COSC 220 next
    # semester" mentions next semester but is a simulation, not a recommendation.
    if any(k in q for k in _WHAT_IF_PHRASES):
        return Intent.WHAT_IF

    if _UNLOCK_PHRASES.search(q) and COURSE_CODE.search(q):
        return Intent.UNLOCKS

    if len({c for c in _codes_in(q)}) >= 2 and _COMPARE_WORDS.search(q):
        return Intent.COMPARE

    if any(k in q for k in _PLAN_PHRASES):
        return Intent.PLAN_SEMESTERS

    # Before CREDITS: "I have 138 credits, so I don't need any more, right?" is a
    # question about being done, and credits alone never answer that.
    if _CONFIRMATION.search(q):
        return Intent.REMAINING_REQUIREMENTS

    if any(k in q for k in ("major requirement", "major complete", "finished my major")) or (
        "major" in q and any(k in q for k in ("done", "complete", "finish", "satisfied"))
    ):
        return Intent.MAJOR_COMPLETE

    if "credit" in q and any(
        k in q for k in ("how many", "missing", "left", "remain", "need", "short")
    ):
        return Intent.CREDITS

    # Before NEXT_SEMESTER: "what am I enrolled in" is about now, not next term.
    if any(
        k in q
        for k in (
            "in progress",
            "taking right now",
            "taking now",
            "currently taking",
            "am i taking",
            "currently enrolled",
            "enrolled in right now",
            "taking this semester",
            "taking this term",
        )
    ):
        return Intent.IN_PROGRESS

    if any(
        k in q
        for k in (
            "next semester",
            "next term",
            "what should i take",
            "what do i take",
            "register for",
            "sign up for",
            "enroll",
        )
    ):
        return Intent.NEXT_SEMESTER

    if any(
        k in q
        for k in (
            "still need",
            "what do i need",
            "left to graduate",
            "to graduate",
            "requirements left",
            "remaining requirement",
            "what is left",
            "what's left",
        )
    ):
        return Intent.REMAINING_REQUIREMENTS

    if any(k in q for k in ("how am i doing", "my progress", "how far", "status", "on track")):
        return Intent.PROGRESS

    if COURSE_CODE.search(q):
        return Intent.COURSE_INFO

    return Intent.UNKNOWN


def is_crisis(question: str) -> bool:
    q = _q(question)
    return any(p in q for p in _CRISIS_PHRASES)


def is_stress(question: str) -> bool:
    q = _q(question)
    return bool(_STRESS_STRONG.search(q)) or bool(
        _STRESS_WEAK.search(q) and _STRESS_TOPIC.search(q)
    )


def is_confirmation(question: str) -> bool:
    """A yes/no check of a conclusion: "so I'm done, right?"."""
    return bool(_CONFIRMATION.search(_q(question)))


def _codes_in(question: str) -> list[str]:
    return [f"{m.group(1).upper()}{m.group(2)}" for m in COURSE_CODE.finditer(question)]


def _coverage_note(facts: AdvisorFacts) -> str:
    if not facts.progress_is_partial:
        return ""
    return (
        "\n\nNote: the encoded catalog is still partial, so this covers the "
        "requirements that have been verified against the Morgan State catalog, "
        "not the whole degree. Requirements awaiting department clarification are "
        "absent rather than failed - check with your advisor before relying on a "
        "total."
    )


def _key(code: str) -> str:
    return code.replace(" ", "").upper()


def _in_progress_codes(facts: AdvisorFacts) -> set[str]:
    return {_key(code) for code, _, _ in facts.in_progress}


def _mark_in_progress(codes: list[str], facts: AdvisorFacts) -> str:
    """'COSC220 (in progress now), COSC241' - a missing prerequisite the student is
    already sitting in is a different situation from one they have not started."""
    under_way = _in_progress_codes(facts)
    return ", ".join(f"{c} (in progress now)" if _key(c) in under_way else c for c in codes)


def _no_record_answer() -> GroundedAnswer:
    return GroundedAnswer(
        text=(
            "I do not have any confirmed coursework for you yet, so I cannot say "
            "anything about your degree progress.\n\n"
            "Upload a transcript on the Upload tab, review what was read from it, "
            "and confirm the rows that are correct. Nothing counts toward your "
            "degree until you confirm it - that is deliberate. Once you have "
            "confirmed a few courses, ask me again."
        ),
        intent=Intent.NO_RECORD,
        citations=[],
        grounded=True,
    )


def _outstanding(facts: AdvisorFacts) -> list[dict]:
    return [b for b in facts.blocks if not b["satisfied"]]


def _block_gap(block: dict) -> str:
    piece = f"- {block['name']}"
    if block["courses_still_needed"]:
        piece += (
            f": {block['courses_still_needed']} course(s), "
            f"{block['credits_still_needed']} credit(s) still needed"
        )
    else:
        piece += f": {block['status']}"
    return piece


def _credit_total_line(facts: AdvisorFacts) -> str:
    """The headline credit sentence, honest when the total is already met.

    "You still need 0" next to four unmet requirement blocks reads as "you are
    done"; it is not, so a met total says what it does and does not mean.
    """
    if facts.credits_remaining > 0:
        return (
            f"You have earned {facts.credits_earned} credits of the "
            f"{facts.credits_required} your degree requires, so you still need "
            f"{facts.credits_remaining}."
        )
    over = facts.credits_earned > facts.credits_required
    return (
        f"You have earned {facts.credits_earned} credits, which "
        f"{'exceeds' if over else 'meets'} the {facts.credits_required}-credit total "
        "your degree requires. That total is only one requirement."
    )


def _percent_line(facts: AdvisorFacts) -> str | None:
    """'X% of the way' only makes sense below 100; above it, say so plainly."""
    if facts.credits_remaining > 0:
        return (
            f"That puts you {facts.credit_progress_percent}% of the way through " "by credit count."
        )
    return None


def answer_credits(facts: AdvisorFacts) -> GroundedAnswer:
    lines = [_credit_total_line(facts)]
    percent = _percent_line(facts)
    if percent:
        lines.append(percent)
    citations: list[str] = []
    if facts.credits_remaining <= 0:
        outstanding = _outstanding(facts)
        if outstanding:
            citations = [b["block_id"] for b in outstanding]
            lines.append(
                f"You are not done yet: {len(outstanding)} requirement block(s) are still "
                "outstanding, and every one must be satisfied regardless of your credit "
                "total:\n" + "\n".join(_block_gap(b) for b in outstanding)
            )
        elif not facts.graduation_eligible:
            lines.append(
                "Every encoded requirement block is satisfied, but the engine does not "
                "show you as eligible to graduate, and only your advisor can clear you."
            )
    if facts.credits_outside_catalog and facts.credits_outside_catalog > 0:
        lines.append(
            f"{facts.credits_outside_catalog} of your earned credits do not currently "
            "map to an encoded requirement. They still count toward your total; they "
            "just have no verified home in the catalog yet."
        )
    if facts.percent_complete is None:
        lines.append(
            "I am not giving you a 'percent of degree complete' number, because the "
            "encoded catalog is incomplete and any such number would be misleading."
        )
    return GroundedAnswer(
        text="\n\n".join(lines) + _coverage_note(facts),
        intent=Intent.CREDITS,
        citations=citations,
    )


def answer_remaining(facts: AdvisorFacts, *, confirming: bool = False) -> GroundedAnswer:
    """What is still outstanding.

    `confirming` is set when the student is checking a conclusion ("so I don't
    need any more, right?"); the answer then opens with a direct yes or no, which
    the engine decides, not the wording of the question.
    """
    outstanding = _outstanding(facts)
    satisfied = [b for b in facts.blocks if b["satisfied"]]

    if not outstanding:
        head = (
            "Every requirement block encoded in the catalog is satisfied. "
            f"You have {facts.credits_earned} of {facts.credits_required} credits."
        )
        if facts.credits_remaining > 0:
            head += f" You still need {facts.credits_remaining} credits in total."
        if not facts.graduation_eligible:
            head += (
                "\n\nThat is still not a clearance to graduate: the encoded catalog "
                "is partial, so requirements that have not been encoded cannot have "
                "been checked. Only your advisor can confirm graduation."
            )
        if confirming:
            head = (
                "Not quite - the engine does not show you as eligible to graduate.\n\n"
                if not facts.graduation_eligible or facts.credits_remaining > 0
                else "The engine shows every encoded requirement met.\n\n"
            ) + head
        return GroundedAnswer(
            text=head + _coverage_note(facts),
            intent=Intent.REMAINING_REQUIREMENTS,
            citations=[b["block_id"] for b in satisfied],
        )

    lines: list[str] = []
    if confirming:
        lines.append(
            "No, not yet. Earned credits are only one requirement; every requirement "
            "block must also be satisfied, and some are not."
        )
    if facts.credits_remaining > 0:
        lines.append(
            f"You still need {facts.credits_remaining} credits, and "
            f"{len(outstanding)} requirement block(s) are outstanding:"
        )
    else:
        lines.append(
            f"Your {facts.credits_earned} earned credits cover the "
            f"{facts.credits_required}-credit total, but {len(outstanding)} requirement "
            "block(s) are still outstanding:"
        )
    citations: list[str] = []
    for block in outstanding:
        citations.append(block["block_id"])
        piece = _block_gap(block)
        if block["options"]:
            shown = ", ".join(block["options"][:6])
            more = "" if len(block["options"]) <= 6 else ", ..."
            label = "All of these are needed" if every_option_required(block) else "Options"
            piece += f"\n  {label}: {shown}{more}"
            citations.extend(block["options"][:6])
        lines.append(piece)

    if satisfied:
        lines.append("Already satisfied: " + ", ".join(b["name"] for b in satisfied) + ".")

    return GroundedAnswer(
        text="\n".join(lines) + _coverage_note(facts),
        intent=Intent.REMAINING_REQUIREMENTS,
        citations=citations,
    )


def answer_next_semester(facts: AdvisorFacts) -> GroundedAnswer:
    if not facts.recommended:
        return GroundedAnswer(
            text=(
                "I do not have any courses to recommend right now. Either everything "
                "encoded is already satisfied, or every remaining course is blocked "
                "by a prerequisite you have not finished." + _coverage_note(facts)
            ),
            intent=Intent.NEXT_SEMESTER,
            citations=[],
            grounded=True,
        )

    lines = [
        "Based on your confirmed record and the catalog prerequisite graph, "
        "these are eligible now:"
    ]
    citations: list[str] = []
    for rec in facts.recommended[:6]:
        citations.append(rec.course.code)
        why = rec.reasons[0] if rec.reasons else "advances a requirement"
        bit = f"- {rec.course.code} {rec.course.title} ({rec.course.credits} cr) - {why}"
        if rec.unlocks_count:
            bit += f"; unlocks {rec.unlocks_count} later course(s)"
        for warning in rec.warnings:
            bit += f"\n  Warning: {warning}"
        lines.append(bit)

    if facts.blocked:
        blocked_bits = [
            f"{r.course.code} "
            f"(needs {_mark_in_progress(r.missing_prerequisites, facts) or 'unknown'})"
            for r in facts.blocked[:4]
        ]
        lines.append("Not yet eligible: " + "; ".join(blocked_bits) + ".")
        citations.extend(r.course.code for r in facts.blocked[:4])

    lines.append(
        "This is a prerequisite-and-requirements view, not a schedule. It does not "
        "know which sections actually run next term, so confirm availability before "
        "you register."
    )
    return GroundedAnswer(
        text="\n".join(lines) + _coverage_note(facts),
        intent=Intent.NEXT_SEMESTER,
        citations=citations,
    )


def answer_major_complete(facts: AdvisorFacts) -> GroundedAnswer:
    major = [b for b in facts.blocks if "major" in b["block_id"].lower()]
    if not major:
        return GroundedAnswer(
            text=(
                "The catalog I have does not contain a requirement block tagged as "
                "the major, so I cannot answer that from verified data. Here is what "
                "I do have:\n\n" + answer_remaining(facts).text
            ),
            intent=Intent.MAJOR_COMPLETE,
            citations=[],
            grounded=False,
        )

    done = [b for b in major if b["satisfied"]]
    left = [b for b in major if not b["satisfied"]]
    if not left:
        text = (
            "Yes - every encoded major requirement block is satisfied: "
            + ", ".join(b["name"] for b in done)
            + "."
        )
    else:
        pieces = []
        for block in left:
            piece = f"- {block['name']}"
            if block["courses_still_needed"]:
                piece += (
                    f": {block['courses_still_needed']} course(s), "
                    f"{block['credits_still_needed']} credit(s) to go"
                )
            pieces.append(piece)
        text = "Not yet. These major blocks are still outstanding:\n" + "\n".join(pieces)
        if done:
            text += "\n\nSatisfied so far: " + ", ".join(b["name"] for b in done) + "."

    return GroundedAnswer(
        text=text + _coverage_note(facts),
        intent=Intent.MAJOR_COMPLETE,
        citations=[b["block_id"] for b in major],
    )


def answer_why_recommended(
    facts: AdvisorFacts, question: str, codes: list[str] | None = None
) -> GroundedAnswer:
    codes = codes or _codes_in(question)
    target = None
    for code in codes:
        target = facts.find_recommendation(code)
        if target:
            break

    if target is None:
        if codes and facts.recommended:
            return GroundedAnswer(
                text=(
                    f"{codes[0]} is not on your recommendation list, so I have no "
                    "engine-derived reason to give you for it. I will not invent one.\n\n"
                    "Currently recommended: "
                    + ", ".join(r.course.code for r in facts.recommended[:6])
                    + ". Ask me why any of those is recommended."
                ),
                intent=Intent.WHY_RECOMMENDED,
                citations=[r.course.code for r in facts.recommended[:6]],
                grounded=False,
            )
        if facts.recommended:
            target = facts.recommended[0]
        else:
            return GroundedAnswer(
                text="There are no recommendations on your record to explain yet."
                + _coverage_note(facts),
                intent=Intent.WHY_RECOMMENDED,
                citations=[],
                grounded=False,
            )

    lines = [f"{target.course.code} {target.course.title} ({target.course.credits} cr):"]
    for reason in target.reasons:
        lines.append(f"- {reason}")
    if target.serves:
        lines.append(f"- Counts toward: {', '.join(target.serves)}")
    if target.unlocks_count:
        lines.append(
            f"- {target.unlocks_count} later course(s) list it as a prerequisite, so "
            "taking it sooner keeps more of the degree reachable."
        )
    if target.eligible_now:
        lines.append("- You meet every prerequisite for it now.")
    else:
        lines.append(
            "- You are NOT eligible yet; still missing "
            + (_mark_in_progress(target.missing_prerequisites, facts) or "an unlisted prerequisite")
            + "."
        )
    for warning in target.warnings:
        lines.append(f"- Warning: {warning}")

    lines.append(
        "\nEvery line above comes from the catalog's prerequisite graph and "
        "requirement blocks, not from a language model's opinion."
    )
    return GroundedAnswer(
        text="\n".join(lines) + _coverage_note(facts),
        intent=Intent.WHY_RECOMMENDED,
        citations=[target.course.code, *target.serves],
    )


def answer_course_info(
    facts: AdvisorFacts, question: str, codes: list[str] | None = None
) -> GroundedAnswer:
    codes = codes or _codes_in(question)
    if not codes:
        return answer_progress(facts)

    code = codes[0]
    completed = {c[0].replace(" ", "").upper(): c for c in facts.completed}
    key = code.replace(" ", "").upper()

    if key in completed:
        _, title, credits = completed[key]
        return GroundedAnswer(
            text=(
                f"You have already completed {code} {title} ({credits} cr), and it is "
                "confirmed on your record."
            ),
            intent=Intent.COURSE_INFO,
            citations=[code],
        )

    under_way = {_key(c): (c, t, cr) for c, t, cr in facts.in_progress}
    if key in under_way:
        _, title, credits = under_way[key]
        return GroundedAnswer(
            text=(
                f"You are taking {code} {title} ({credits} cr) right now - it is "
                "confirmed on your record as in progress. It does not count toward "
                "any requirement or prerequisite until a final passing grade is "
                "recorded."
            ),
            intent=Intent.COURSE_INFO,
            citations=[code],
        )

    rec = facts.find_recommendation(code)
    if rec is not None:
        return answer_why_recommended(facts, question, [code])

    return GroundedAnswer(
        text=(
            f"{code} is not on your confirmed record and is not currently recommended "
            "for you. I can only speak to courses in the encoded catalog and on your "
            "record, so I will not guess at its content or prerequisites."
        ),
        intent=Intent.COURSE_INFO,
        citations=[],
        grounded=False,
    )


def answer_in_progress(facts: AdvisorFacts) -> GroundedAnswer:
    if not facts.in_progress:
        return GroundedAnswer(
            text=(
                "Your confirmed record has no courses marked as in progress. If you "
                "are enrolled this term, those courses only appear here once a "
                "transcript listing them (grade IP or REG) is uploaded and confirmed."
            ),
            intent=Intent.IN_PROGRESS,
            citations=[],
        )
    lines = ["These courses are confirmed on your record as in progress:"]
    for code, title, credits in facts.in_progress:
        lines.append(f"- {code} {title} ({credits} cr)")
    lines.append(
        f"That is {facts.credits_in_progress} credits in progress. They are not "
        "counted yet: they satisfy no requirement or prerequisite until a final "
        "passing grade is recorded, so your total stays at "
        f"{facts.credits_earned} earned credits for now."
    )
    blocked_on = [
        r.course.code
        for r in facts.blocked
        if _in_progress_codes(facts) & {_key(c) for c in r.missing_prerequisites}
    ]
    if blocked_on:
        lines.append(
            "Finishing them would clear a prerequisite for: " + ", ".join(blocked_on) + "."
        )
    return GroundedAnswer(
        text="\n".join(lines) + _coverage_note(facts),
        intent=Intent.IN_PROGRESS,
        citations=[code for code, _, _ in facts.in_progress],
    )


def answer_progress(facts: AdvisorFacts) -> GroundedAnswer:
    satisfied = [b for b in facts.blocks if b["satisfied"]]
    if facts.credits_remaining > 0:
        credit_line = (
            f"You have {facts.credits_earned} of {facts.credits_required} credits "
            f"({facts.credit_progress_percent}% by credit), with "
            f"{facts.credits_remaining} to go."
        )
    else:
        credit_line = _credit_total_line(facts)
    lines = [
        credit_line,
        f"{len(satisfied)} of {len(facts.blocks)} encoded requirement blocks are satisfied.",
    ]
    if facts.in_progress:
        lines.append(
            "In progress (not counted yet): "
            + ", ".join(code for code, _, _ in facts.in_progress)
            + "."
        )
    if facts.recommended:
        lines.append(
            "Eligible next: " + ", ".join(r.course.code for r in facts.recommended[:5]) + "."
        )
    lines.append("Graduation eligible: " + ("yes" if facts.graduation_eligible else "no") + ".")
    return GroundedAnswer(
        text="\n\n".join(lines) + _coverage_note(facts),
        intent=Intent.PROGRESS,
        citations=[b["block_id"] for b in satisfied],
    )


def _catalog_codes_in(facts: AdvisorFacts, question: str) -> list[str]:
    """Course codes in the question, in order, de-duplicated, catalog subjects only.

    The raw pattern also matches "need 101 more"; only a subject the catalog
    actually defines can be a course reference here.
    """
    seen: list[str] = []
    for code in _codes_in(question):
        if _subject_of(code) in facts.catalog_subjects and code not in seen:
            seen.append(code)
    return seen


def _names(codes: list[str], limit: int = 8) -> str:
    shown = ", ".join(codes[:limit])
    return shown + (f" and {len(codes) - limit} more" if len(codes) > limit else "")


def answer_what_if(
    facts: AdvisorFacts, question: str, codes: list[str] | None = None
) -> GroundedAnswer:
    codes = codes or _catalog_codes_in(facts, question)
    if codes and _FAIL_WORDS.search(_q(question)):
        return answer_if_not_passed(facts, codes[0])
    if not codes or facts.program is None or facts.record is None:
        return GroundedAnswer(
            text=(
                "I can run a what-if on your record, but I need to know which courses. "
                'Try, for example, "What if I take COSC 220 and COSC 281 together?" '
                "Your actual record is never changed by a what-if."
            ),
            intent=Intent.WHAT_IF,
            citations=[],
            grounded=False,
        )

    mode = "completed" if _COMPLETION_WORDS.search(_norm(question)) else "same_term"
    result = simulate(facts.program, facts.record, SimulationRequest(courses=codes, mode=mode))
    applied = [c.code for c in result.applied]
    lines: list[str] = []

    if applied:
        lines.append(
            f"What-if (your real record is unchanged): if you pass {_names(applied)}, then:"
        )
        before, after = result.before, result.after
        lines.append(
            f"- Credits earned go from {before.credits_earned} to {after.credits_earned} "
            f"(+{result.credits_added}); credits still needed go from "
            f"{before.credits_remaining} to {after.credits_remaining}."
        )
        if result.newly_satisfied_blocks:
            names = "; ".join(b.name for b in result.newly_satisfied_blocks)
            lines.append(f"- Requirements newly satisfied: {names}.")
        moved = [b for b in result.changed_blocks if b not in result.newly_satisfied_blocks]
        if moved:
            lines.append(
                "- Requirements that move forward: " + "; ".join(b.name for b in moved) + "."
            )
        if not result.changed_blocks:
            lines.append("- No encoded requirement changes (they count as general credit).")
        if result.newly_unlocked:
            lines.append(
                "- Newly eligible courses: " + _names([c.code for c in result.newly_unlocked]) + "."
            )
        else:
            lines.append("- No new courses become eligible.")
        if result.still_blocked:
            blocked = "; ".join(
                f"{b.course.code} (needs {', '.join(b.missing_prerequisites) or 'unknown'})"
                for b in result.still_blocked[:4]
            )
            lines.append(f"- Still blocked: {blocked}.")
    else:
        lines.append("Nothing could be simulated, so there is no change to report.")

    for skip in result.skipped:
        extra = f" ({', '.join(skip.missing_prerequisites)} missing)"
        if not skip.missing_prerequisites:
            extra = ""
        lines.append(f"- Not simulated: {skip.code} - {skip.detail}{extra}.")
    lines += [f"Note: {w}" for w in result.warnings]
    return GroundedAnswer(
        text="\n".join(lines),
        intent=Intent.WHAT_IF,
        citations=[*applied, *(c.code for c in result.newly_unlocked)],
    )


def _label(node: PrereqNode) -> str:
    return f"{node.code} ({node.status})" if node.status else str(node.code)


def _flatten(node: PrereqNode) -> list[str]:
    parts: list[str] = []
    for child in node.children:
        if child.any_of:
            parts.append("(" + " or ".join(_label(c) for c in child.children) + ")")
        else:
            parts.append(_label(child))
    return parts


def _describe_unlock(e: UnlockEntry) -> str:
    if e.status == "completed":
        return f"{e.course.code} (already completed)"
    if e.status == "in_progress":
        return f"{e.course.code} (in progress now)"
    if not e.missing_prerequisites:
        return f"{e.course.code} (you can take it now)"
    if not e.missing_after:
        return f"{e.course.code} (this course is all you are missing)"
    return f"{e.course.code} (would still need {', '.join(e.missing_after)})"


def answer_unlocks(
    facts: AdvisorFacts, question: str, codes: list[str] | None = None
) -> GroundedAnswer:
    codes = codes or _catalog_codes_in(facts, question)
    if not codes or facts.program is None or facts.record is None:
        return answer_unknown(facts)
    report = explore_unlocks(facts.program, facts.record, codes[0])
    if not report.known or report.course is None or report.prerequisite_tree is None:
        return GroundedAnswer(
            text=(
                f"{report.code} is not in the encoded catalog, so I will not guess what it "
                "needs or unlocks."
            ),
            intent=Intent.UNLOCKS,
            citations=[],
            grounded=False,
        )

    lines = [f"{report.code} {report.course.title}: status for you is {report.status}."]
    needs = _flatten(report.prerequisite_tree)
    lines.append("Prerequisites: " + (", ".join(needs) if needs else "none") + ".")
    if report.direct_unlocks:
        direct = "; ".join(_describe_unlock(e) for e in report.direct_unlocks)
        lines.append(f"Directly unlocks: {direct}.")
    else:
        lines.append("It is not a prerequisite for any course in the encoded catalog.")
    if report.downstream_unlocks:
        later = _names([e.course.code for e in report.downstream_unlocks])
        lines.append(f"Further down the chain: {later}.")
    lines.append(
        "This comes from the catalog's prerequisite graph; it does not know which sections "
        "run in a given term."
    )
    return GroundedAnswer(
        text="\n".join(lines) + _coverage_note(facts),
        intent=Intent.UNLOCKS,
        citations=[report.code, *(e.course.code for e in report.direct_unlocks)],
    )


def answer_if_not_passed(facts: AdvisorFacts, code: str) -> GroundedAnswer:
    """ "What happens if I fail COSC 220?" - from the prerequisite graph.

    The simulator models passing a course. Not passing one changes nothing that is
    already earned, so the honest answer is what stays out of reach: the courses
    that list it as a prerequisite, directly and further down the chain.
    """
    if facts.program is None or facts.record is None:
        return answer_unknown(facts)
    report = explore_unlocks(facts.program, facts.record, code)
    if not report.known or report.course is None:
        return GroundedAnswer(
            text=(
                f"{report.code} is not in the encoded catalog, so I cannot say what not "
                "passing it would change."
            ),
            intent=Intent.WHAT_IF,
            citations=[],
            grounded=False,
        )

    course = report.course
    waiting = ("completed", "in_progress")
    held_back = [e.course.code for e in report.direct_unlocks if e.status not in waiting]
    later = [e.course.code for e in report.downstream_unlocks if e.status not in waiting]
    lines = [
        f"What-if (your real record is unchanged): if you do not pass {report.code} "
        f"{course.title}:"
    ]
    if report.status == "completed":
        lines.append(
            f"- Your confirmed record already shows {report.code} as passed, so there is "
            "nothing for the engine to change. If a grade on your record is wrong, upload "
            "a corrected transcript."
        )
    else:
        if report.status == "in_progress":
            lines.append(
                f"- {report.code} is in progress now. Without a passing grade its "
                f"{course.credits} credits are not earned and it satisfies no requirement "
                "or prerequisite, so you would need to take it again."
            )
        else:
            lines.append(
                f"- {report.code} is not on your record yet, so nothing you have already "
                "earned changes."
            )
        if held_back:
            lines.append(f"- These stay blocked until you pass it: {_names(held_back)}.")
        else:
            lines.append("- No course in the encoded catalog lists it as a direct prerequisite.")
        if later:
            lines.append(f"- Further down the chain, these also wait on it: {_names(later)}.")
    lines.append(
        "- Retake, grade-replacement and GPA rules are not encoded here, so I cannot tell "
        "you how a failing grade would affect your GPA. Ask your advisor before you decide "
        "to withdraw or retake."
    )
    return GroundedAnswer(
        text="\n".join(lines) + _coverage_note(facts),
        intent=Intent.WHAT_IF,
        citations=[report.code, *held_back[:6]],
    )


#: How many planned terms the chat lists before pointing at the planner page.
_PLAN_TERMS_SHOWN = 8


def answer_plan(facts: AdvisorFacts) -> GroundedAnswer:
    """The semester planner's draft, term by term (app/audit/schedule.py)."""
    if facts.program is None or facts.record is None:
        return answer_unknown(facts)
    try:
        plan = generate_schedule(facts.program, facts.record)
    except ValueError:
        return GroundedAnswer(
            text=(
                "I could not build a semester plan from your record just now. The "
                "semester planner page shows the same plan with more detail."
            ),
            intent=Intent.PLAN_SEMESTERS,
            citations=[],
            grounded=False,
        )

    if not plan.terms and not plan.unscheduled:
        return GroundedAnswer(
            text=(
                "There is nothing left to schedule among the requirements the catalog "
                "encodes. That is not a graduation clearance - only your advisor can give "
                "one." + _coverage_note(facts)
            ),
            intent=Intent.PLAN_SEMESTERS,
            citations=[],
        )

    lines = [
        "Here is a term-by-term draft for the requirements the catalog encodes, "
        f"starting {plan.start_term} with up to {plan.credit_target} credits a term:"
    ]
    citations: list[str] = []
    for term in plan.terms[:_PLAN_TERMS_SHOWN]:
        codes = [c.course.code for c in term.courses]
        citations.extend(codes)
        lines.append(f"- {term.term} ({term.credits} cr): {', '.join(codes)}")
    if len(plan.terms) > _PLAN_TERMS_SHOWN:
        lines.append(
            f"- ...and {len(plan.terms) - _PLAN_TERMS_SHOWN} more term(s) on the semester "
            "planner page."
        )
    if plan.unscheduled:
        lines.append(
            "Could not be placed: "
            + "; ".join(f"{u.course.code} ({u.reason})" for u in plan.unscheduled[:4])
            + "."
        )
    lines += [f"Note: {c}" for c in plan.caveats]
    lines.append(
        "This is a draft to take to your advisor, not a graduation date. You can edit it "
        "on the semester planner page."
    )
    return GroundedAnswer(
        text="\n".join(lines), intent=Intent.PLAN_SEMESTERS, citations=citations[:12]
    )


def _compare_state(state: str, missing: list[str]) -> str:
    if state == "passed":
        return "you have already passed it"
    if state == "in_progress":
        return "you are taking it now"
    if state == "eligible_now":
        return "you can take it now"
    return f"not yet eligible - needs {', '.join(missing) or 'an unlisted prerequisite'} first"


def answer_compare(
    facts: AdvisorFacts, question: str, codes: list[str] | None = None
) -> GroundedAnswer:
    """Two to four courses side by side (app/audit/compare.py). Ranks nothing."""
    codes = codes or _catalog_codes_in(facts, question)
    if facts.program is None or facts.record is None or facts.audit is None:
        return answer_unknown(facts)
    try:
        comparison = compare_courses(facts.program, facts.record, facts.audit, codes[:4])
    except ComparisonError as exc:
        return GroundedAnswer(
            text=(
                f"I can compare two to four catalog courses side by side, but not here: {exc} "
                'Name the courses, for example "Which is better for me, COSC 220 or '
                'COSC 241?"'
            ),
            intent=Intent.COMPARE,
            citations=[],
            grounded=False,
        )

    lines = ["Side by side, from the catalog and your confirmed record:"]
    for course in comparison.courses:
        still_open = [r.name for r in course.serves if r.still_open]
        piece = (
            f"- {course.code} {course.title} ({course.credits} cr): "
            f"{_compare_state(course.state, course.missing_prerequisites)}. "
        )
        piece += (
            f"Counts toward {', '.join(still_open)}, which you still need."
            if still_open
            else "Does not advance a requirement you still have open."
        )
        piece += f" Unlocks {len(course.unlocks)} later course(s)."
        for warning in course.warnings:
            piece += f"\n  Warning: {warning}"
        lines.append(piece)
    if comparison.differences:
        lines.append("Where they differ:")
        lines += [f"- {d}" for d in comparison.differences]
    lines += [f"Note: {c}" for c in comparison.caveats]
    lines.append(
        "Which one to take is your decision - this lays out the facts and ranks nothing. "
        "Your advisor can weigh what it cannot, like your interests and which sections run."
    )
    return GroundedAnswer(
        text="\n".join(lines),
        intent=Intent.COMPARE,
        citations=[c.code for c in comparison.courses],
    )


#: Words too common in block names and questions to identify a block.
_BLOCK_STOP = frozenset(
    {
        "requirement",
        "requirements",
        "course",
        "courses",
        "block",
        "blocks",
        "this",
        "that",
        "does",
        "count",
        "with",
        "have",
        "satisfied",
        "what",
        "done",
        "still",
        "isn't",
        "doesn't",
    }
)


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", text.lower()) if len(w) > 3} - _BLOCK_STOP


def _match_blocks(explanations: list[BlockExplanation], question: str) -> list[BlockExplanation]:
    q = _q(question)
    named = [e for e in explanations if e.name.lower() in q or e.block_id.lower() in q]
    if named:
        return named
    asked = _words(q)
    scored = [(len(_words(e.name) & asked), e) for e in explanations]
    best = max((score for score, _ in scored), default=0)
    return [e for score, e in scored if best and score == best]


def answer_explain(
    facts: AdvisorFacts, question: str, codes: list[str] | None = None
) -> GroundedAnswer:
    """Why a course did or did not count, or why a block is not done
    (app/audit/explain_audit.py)."""
    if facts.program is None or facts.record is None or facts.audit is None:
        return answer_unknown(facts)
    explanations = explain_all(facts.program, facts.record, facts.audit)
    codes = codes or _catalog_codes_in(facts, question)

    if codes:
        code = _key(codes[0])
        counted_in = [e for e in explanations if any(_key(c.code) == code for c in e.counted)]
        refused = [(e, n) for e in explanations for n in e.not_counted if _key(n.code) == code]
        open_in = [
            e
            for e in explanations
            if e.status.value != "satisfied" and any(_key(o.code) == code for o in e.options)
        ]
        lines: list[str] = []
        if counted_in:
            lines.append(
                f"{code} does count: it is applied to "
                + ", ".join(e.name for e in counted_in)
                + "."
            )
        for block, why in refused:
            lines.append(f"{code} does not count toward {block.name}: {why.reason}")
        if not counted_in and not refused:
            if code in _in_progress_codes(facts):
                lines.append(
                    f"{code} is in progress, so it counts toward nothing until a final "
                    "passing grade is recorded."
                )
            elif code in {_key(c) for c, _, _ in facts.completed}:
                lines.append(
                    f"{code} is on your confirmed record, but no encoded requirement block "
                    "uses it."
                )
            else:
                lines.append(
                    f"{code} is not on your confirmed record, so it has not been counted "
                    "anywhere yet."
                )
        if open_in:
            lines.append(
                f"It is still one of the options for: {', '.join(e.name for e in open_in)}."
            )
        return GroundedAnswer(
            text="\n".join(lines) + _coverage_note(facts),
            intent=Intent.EXPLAIN_REQUIREMENT,
            citations=[code, *(e.block_id for e in [*counted_in, *open_in])],
        )

    targets = _match_blocks(explanations, question) or [
        e for e in explanations if e.status.value != "satisfied"
    ]
    if not targets:
        return GroundedAnswer(
            text=(
                "Every encoded requirement block is satisfied, so there is nothing missing "
                "to explain." + _coverage_note(facts)
            ),
            intent=Intent.EXPLAIN_REQUIREMENT,
            citations=[],
        )

    lines = []
    for block in targets[:5]:
        lines.append(f"{block.name}: {block.headline}")
        lines.append(f"  What it asks for: {block.requirement}")
        for why in block.not_counted[:4]:
            lines.append(f"  {why.code} did not count: {why.reason}")
        if block.next_step:
            lines.append(f"  Next step: {block.next_step}")
    if len(targets) > 5:
        lines.append(f"...and {len(targets) - 5} more on the dashboard.")
    return GroundedAnswer(
        text="\n".join(lines) + _coverage_note(facts),
        intent=Intent.EXPLAIN_REQUIREMENT,
        citations=[b.block_id for b in targets[:5]],
    )


_SUPPORT = (
    "You do not have to sort this out alone. Your academic advisor is the right person "
    "for the degree side, and your campus counseling center is there for students who "
    "are feeling stressed or overwhelmed - reaching out to them is a normal thing to do."
)


def answer_wellbeing(facts: AdvisorFacts | None) -> GroundedAnswer:
    """A student who says they are overwhelmed gets the facts, kindly, and people.

    Acknowledges what they wrote, without naming a feeling they did not name,
    diagnosing anything, or promising a graduation date. The facts are the same
    engine figures as every other answer - a worried student deserves the real
    picture, not reassurance the engine cannot back.
    """
    opening = (
        "That sounds like a lot to carry, and it makes sense to worry about something "
        "this important."
    )
    if facts is None or not facts.has_coursework:
        return GroundedAnswer(
            text=(
                f"{opening} I do not have any confirmed coursework for you yet, so I cannot "
                "show you where you stand. Once you upload and confirm your transcript, I "
                "can lay out exactly what is left, one piece at a time.\n\n" + _SUPPORT
            ),
            intent=Intent.WELLBEING,
            citations=[],
        )

    outstanding = _outstanding(facts)
    lines = [
        f"{opening} I cannot promise you a graduation date, but I can show you where you "
        "actually stand, from your confirmed record:"
    ]
    if facts.credits_remaining > 0:
        lines.append(
            f"- Credits: {facts.credits_earned} of {facts.credits_required} earned; "
            f"{facts.credits_remaining} still needed."
        )
    else:
        lines.append(
            f"- Credits: {facts.credits_earned} earned, which covers the "
            f"{facts.credits_required}-credit total."
        )
    if outstanding:
        names = [b["name"] for b in outstanding]
        more = f" and {len(names) - 4} more" if len(names) > 4 else ""
        lines.append(
            f"- Requirement blocks still open ({len(names)}): {'; '.join(names[:4])}{more}."
        )
    else:
        lines.append("- Every encoded requirement block is satisfied.")
    if facts.in_progress:
        lines.append(
            "- In progress now (not counted yet): "
            + ", ".join(code for code, _, _ in facts.in_progress)
            + "."
        )
    top = [r.course.code for r in facts.recommended[:3]]
    if top:
        lines.append(f"- Courses you can take now that move you forward: {', '.join(top)}.")
    lines.append(
        "A manageable next step: bring this list to your academic advisor, who can check "
        "it against the full degree and help with timing."
    )
    lines.append(_SUPPORT)
    return GroundedAnswer(
        text="\n".join(lines) + _coverage_note(facts),
        intent=Intent.WELLBEING,
        citations=[*(b["block_id"] for b in outstanding[:4]), *top],
    )


def answer_crisis() -> GroundedAnswer:
    """Fixed text. Never rephrased by a model, never routed by one.

    911 and 988 (the US Suicide & Crisis Lifeline, call or text) are national
    numbers, not local guesses. No campus office, number or URL is named because
    none has been verified for this project.
    """
    return GroundedAnswer(
        text=(
            "I'm really sorry you're feeling this way. I'm an advising tool, and this is "
            "something a person should help you with right now.\n\n"
            "If you are in immediate danger or might act on these thoughts, call 911.\n"
            "In the US you can call or text 988, the Suicide & Crisis Lifeline, any time, "
            "day or night, to talk to someone.\n"
            "Your campus counseling center, and people you trust, can help too.\n\n"
            "Your degree questions can wait. I'll be here when you want to look at them."
        ),
        intent=Intent.CRISIS,
        citations=[],
    )


def answer_unknown(facts: AdvisorFacts) -> GroundedAnswer:
    base = answer_progress(facts)
    return GroundedAnswer(
        text=(
            "I answer questions about your degree progress from your confirmed "
            "record and the Morgan State catalog. I cannot answer things outside "
            "that - I will not guess.\n\n"
            "Try: what do I still need to graduate? / how many credits am I "
            "missing? / what should I take next semester? / have I completed my "
            "major requirements? / why is COSC 220 recommended?\n\n"
            "Where you stand right now:\n\n" + base.text
        ),
        intent=Intent.UNKNOWN,
        citations=base.citations,
        grounded=True,
    )


def deterministic_answer(
    facts: AdvisorFacts | None,
    question: str,
    intent: Intent | None = None,
    codes: list[str] | None = None,
) -> GroundedAnswer:
    """Route a question to its answer. The entry point for the whole module.

    `intent` and `codes` let a caller that has already understood the question
    (see `understanding.py`) choose the route. They only ever choose WHICH engine
    answer is given; every sentence of it is still built from `facts` here. With
    neither given, keyword routing decides, exactly as before.
    """
    # A student who writes about hurting themselves gets the same fixed reply
    # whatever else was decided, record or no record.
    if is_crisis(question):
        return answer_crisis()

    intent = intent or detect_intent(question)
    if intent is Intent.CRISIS:
        return answer_crisis()
    if facts is None or not facts.has_coursework:
        if intent is Intent.WELLBEING:
            return answer_wellbeing(None)
        # A student with no confirmed coursework gets the same honest answer
        # whatever they asked - there is nothing to reason about yet.
        return _no_record_answer()

    codes = [c for c in (codes or []) if c] or None
    if intent is Intent.CREDITS:
        return answer_credits(facts)
    if intent is Intent.REMAINING_REQUIREMENTS:
        return answer_remaining(facts, confirming=is_confirmation(question))
    if intent is Intent.NEXT_SEMESTER:
        return answer_next_semester(facts)
    if intent is Intent.MAJOR_COMPLETE:
        return answer_major_complete(facts)
    if intent is Intent.WHY_RECOMMENDED:
        return answer_why_recommended(facts, question, codes)
    if intent is Intent.COURSE_INFO:
        return answer_course_info(facts, question, codes)
    if intent is Intent.PROGRESS:
        return answer_progress(facts)
    if intent is Intent.IN_PROGRESS:
        return answer_in_progress(facts)
    if intent is Intent.WHAT_IF:
        return answer_what_if(facts, question, codes)
    if intent is Intent.UNLOCKS:
        return answer_unlocks(facts, question, codes)
    if intent is Intent.PLAN_SEMESTERS:
        return answer_plan(facts)
    if intent is Intent.COMPARE:
        return answer_compare(facts, question, codes)
    if intent is Intent.EXPLAIN_REQUIREMENT:
        return answer_explain(facts, question, codes)
    if intent is Intent.WELLBEING:
        return answer_wellbeing(facts)
    return answer_unknown(facts)
