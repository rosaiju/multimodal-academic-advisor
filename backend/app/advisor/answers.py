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
3. Intent detection is keyword-based on purpose. A classifier that is itself a
   model would put a model back on the path between a student and a fact.

The tone is deliberately plain. These answers are read by someone deciding what
to register for.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from app.advisor.facts import AdvisorFacts, _subject_of
from app.audit.impact import PrereqNode, UnlockEntry, explore_unlocks
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
)
#: Mentioning a course alongside one of these is an unlock/prerequisite question.
_UNLOCK_PHRASES = re.compile(
    r"\bunlock|\bopens? up|depends? on|leads? to|\bafter\b|"
    r"\bneed before|\bbefore (?:i can )?(?:take|taking)|prerequisite|prereq|required for"
)
#: These make a what-if mean "once I have passed it" rather than "this coming term".
_COMPLETION_WORDS = re.compile(r"\b(complete|completed|finish|finished|pass|passed|done with)\b")


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


def detect_intent(question: str) -> Intent:
    """Keyword routing. Order matters: the most specific pattern wins."""
    q = _norm(question)

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


def answer_credits(facts: AdvisorFacts) -> GroundedAnswer:
    lines = [
        f"You have earned {facts.credits_earned} credits of the "
        f"{facts.credits_required} your degree requires, so you still need "
        f"{facts.credits_remaining}.",
        f"That puts you {facts.credit_progress_percent}% of the way through by credit count.",
    ]
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
        citations=[],
    )


def answer_remaining(facts: AdvisorFacts) -> GroundedAnswer:
    outstanding = [b for b in facts.blocks if not b["satisfied"]]
    satisfied = [b for b in facts.blocks if b["satisfied"]]

    if not outstanding:
        head = (
            "Every requirement block encoded in the catalog is satisfied. "
            f"You have {facts.credits_earned} of {facts.credits_required} credits."
        )
        if not facts.graduation_eligible:
            head += (
                "\n\nThat is still not a clearance to graduate: the encoded catalog "
                "is partial, so requirements that have not been encoded cannot have "
                "been checked. Only your advisor can confirm graduation."
            )
        return GroundedAnswer(
            text=head + _coverage_note(facts),
            intent=Intent.REMAINING_REQUIREMENTS,
            citations=[b["block_id"] for b in satisfied],
        )

    lines = [
        f"You still need {facts.credits_remaining} credits, and "
        f"{len(outstanding)} requirement block(s) are outstanding:"
    ]
    citations: list[str] = []
    for block in outstanding:
        citations.append(block["block_id"])
        piece = f"- {block['name']}"
        if block["courses_still_needed"]:
            piece += (
                f": {block['courses_still_needed']} course(s), "
                f"{block['credits_still_needed']} credit(s) still needed"
            )
        else:
            piece += f": {block['status']}"
        if block["options"]:
            shown = ", ".join(block["options"][:6])
            more = "" if len(block["options"]) <= 6 else ", ..."
            piece += f"\n  Options: {shown}{more}"
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


def answer_why_recommended(facts: AdvisorFacts, question: str) -> GroundedAnswer:
    codes = _codes_in(question)
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


def answer_course_info(facts: AdvisorFacts, question: str) -> GroundedAnswer:
    codes = _codes_in(question)
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
        return answer_why_recommended(facts, question)

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
    lines = [
        f"You have {facts.credits_earned} of {facts.credits_required} credits "
        f"({facts.credit_progress_percent}% by credit), with "
        f"{facts.credits_remaining} to go.",
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


def answer_what_if(facts: AdvisorFacts, question: str) -> GroundedAnswer:
    codes = _catalog_codes_in(facts, question)
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


def answer_unlocks(facts: AdvisorFacts, question: str) -> GroundedAnswer:
    codes = _catalog_codes_in(facts, question)
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


def deterministic_answer(facts: AdvisorFacts | None, question: str) -> GroundedAnswer:
    """Route a question to its answer. The entry point for the whole module."""
    if facts is None or not facts.has_coursework:
        # A student with no confirmed coursework gets the same honest answer
        # whatever they asked - there is nothing to reason about yet.
        return _no_record_answer()

    intent = detect_intent(question)
    if intent is Intent.CREDITS:
        return answer_credits(facts)
    if intent is Intent.REMAINING_REQUIREMENTS:
        return answer_remaining(facts)
    if intent is Intent.NEXT_SEMESTER:
        return answer_next_semester(facts)
    if intent is Intent.MAJOR_COMPLETE:
        return answer_major_complete(facts)
    if intent is Intent.WHY_RECOMMENDED:
        return answer_why_recommended(facts, question)
    if intent is Intent.COURSE_INFO:
        return answer_course_info(facts, question)
    if intent is Intent.PROGRESS:
        return answer_progress(facts)
    if intent is Intent.IN_PROGRESS:
        return answer_in_progress(facts)
    if intent is Intent.WHAT_IF:
        return answer_what_if(facts, question)
    if intent is Intent.UNLOCKS:
        return answer_unlocks(facts, question)
    return answer_unknown(facts)
