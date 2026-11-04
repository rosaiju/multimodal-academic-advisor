"""Model-assisted question understanding: which engine answer does this question want?

Keyword routing (`answers.detect_intent`) reads one message in isolation, so a
follow-up fails. After "How many credits am I missing?", the student wrote "It
says I have earned 138 credits. So I don't think I need anymore. Right?" and got
the identical credits answer back.

Here a model reads the question plus the last few turns and returns STRICT JSON:

    {"intent": "<one Intent value>", "course_codes": ["COSC220"], "follow_up_of": null}

What the model is allowed to decide, and what it is not:

* It picks a ROUTE - one of the existing `Intent` values. It never writes an
  answer, never states a fact, and never sees the student's record. The answer is
  still built by `answers.deterministic_answer` from the degree engine, and any
  rephrasing still goes through `consistency.check_rephrasing`.
* Course codes it returns are kept only if the catalog has them AND they appear in
  the question or the recent conversation - it cannot introduce a course. When
  the question names catalog courses itself, those are used and the model's list
  is ignored.
* Crisis and stress wording are never routed by a model: `answers.is_crisis` and
  the keyword wellbeing check run first, and the model cannot choose `crisis`,
  `wellbeing` or `no_record`. Nothing here infers how a student feels.
* Any failure - provider unavailable, timeout, non-JSON, an intent outside the
  enum - falls back to keyword routing. So does a model "unknown" when keywords
  found a route.

The caller reports which path decided (`source`: "model" or "keywords") so the UI
can say so.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field

from app.advisor.answers import Intent, detect_intent, is_confirmation, is_crisis
from app.llm.chat import ChatProvider, ChatTurn

logger = logging.getLogger(__name__)

#: Routes a model may choose. Crisis, wellbeing and no-record are decided by code
#: only: wellbeing is routed on words the student actually wrote ("overwhelmed"),
#: never on a model's reading of their mood.
_CODE_ONLY = (Intent.CRISIS, Intent.WELLBEING, Intent.NO_RECORD)
MODEL_INTENTS: tuple[Intent, ...] = tuple(i for i in Intent if i not in _CODE_ONLY)

_DESCRIPTIONS: dict[Intent, str] = {
    Intent.REMAINING_REQUIREMENTS: (
        "what is left to graduate, or asking to confirm they are done / need nothing more"
    ),
    Intent.CREDITS: "asking for the number of credits earned or missing",
    Intent.NEXT_SEMESTER: "what to take next term",
    Intent.MAJOR_COMPLETE: "whether major requirements are complete",
    Intent.WHY_RECOMMENDED: "why a course is recommended",
    Intent.COURSE_INFO: "their status in one specific course",
    Intent.PROGRESS: "overall progress or being on track",
    Intent.IN_PROGRESS: "courses they are taking right now",
    Intent.WHAT_IF: "hypothetical: taking, passing or failing a course",
    Intent.UNLOCKS: "what a course unlocks or needs first",
    Intent.PLAN_SEMESTERS: "plan remaining semesters, or when they can graduate",
    Intent.COMPARE: "compare two or more courses",
    Intent.EXPLAIN_REQUIREMENT: "why a course does not count or a requirement is unmet",
    Intent.UNKNOWN: "anything else (professors, parking, dorms...)",
}

SYSTEM_PROMPT = (
    "You route a university student's question to ONE intent. Do not answer it.\n"
    "Intents:\n{intents}\n"
    "Use the conversation to resolve follow-ups like 'right?', 'it', 'this course'.\n"
    'Reply with JSON only: {{"intent": "<intent>", "course_codes": ["COSC220"], '
    '"follow_up_of": "<intent of the earlier question it follows up, or null>"}}\n'
    "course_codes: only codes written in the question or conversation that the "
    "question is about; [] if none."
)

#: Turns of history shown to the router, and how much of each.
HISTORY_TURNS = 4
TURN_CHARS = 240
MAX_TOKENS = 80

_CODE = re.compile(r"^\s*([A-Za-z]{2,5})\s*-?\s*(\d{3})\s*$")
_CODE_IN_TEXT = re.compile(r"\b([A-Za-z]{2,5})[\s\-]?(\d{3})\b")
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


@dataclass(frozen=True)
class Understanding:
    intent: Intent
    #: Catalog course codes the question is about (validated). Empty means "let the
    #: answer read them from the question", as keyword routing always did.
    codes: list[str] = field(default_factory=list)
    follow_up_of: Intent | None = None
    #: "model" when the model's route was used, "keywords" otherwise.
    source: str = "keywords"
    seconds: float | None = None
    #: Why the model's route was not used. Logged, not shown to students.
    reason: str | None = None


def system_prompt() -> str:
    lines = "\n".join(f"- {i.value}: {_DESCRIPTIONS[i]}" for i in MODEL_INTENTS)
    return SYSTEM_PROMPT.format(intents=lines)


def _clip(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= TURN_CHARS else text[:TURN_CHARS] + "..."


def user_message(question: str, history: list[ChatTurn]) -> str:
    recent = history[-HISTORY_TURNS:]
    lines = ["Conversation so far:" if recent else "No earlier conversation."]
    for turn in recent:
        who = "Student" if turn.role == "user" else "Advisor"
        lines.append(f"{who}: {_clip(turn.content)}")
    lines.append(f"\nQuestion: {question.strip()}")
    return "\n".join(lines)


def _codes_in(text: str) -> list[str]:
    return [f"{m.group(1).upper()}{m.group(2)}" for m in _CODE_IN_TEXT.finditer(text)]


def _parse(raw: str) -> dict:
    text = _THINK.sub("", raw).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in the reply")
    data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("the reply is not a JSON object")
    return data


def _intent(value: object) -> Intent | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip().lower()
    return next((i for i in MODEL_INTENTS if i.value == cleaned), None)


def validate_codes(
    proposed: object, question: str, history: list[ChatTurn], catalog: frozenset[str]
) -> list[str]:
    """The question's own catalog codes if it names any; otherwise the model's codes
    that the catalog has AND that appear in the recent conversation."""
    own = [c for c in dict.fromkeys(_codes_in(question)) if c in catalog]
    if own:
        return own
    if not isinstance(proposed, list):
        return []
    seen = set(_codes_in(" ".join(t.content for t in history[-HISTORY_TURNS:])))
    kept: list[str] = []
    for item in proposed:
        match = _CODE.match(item) if isinstance(item, str) else None
        if not match:
            continue
        code = f"{match.group(1).upper()}{match.group(2)}"
        if code in catalog and code in seen and code not in kept:
            kept.append(code)
    return kept


def understand(
    question: str,
    history: list[ChatTurn],
    *,
    provider: ChatProvider | None,
    catalog_codes: frozenset[str],
) -> Understanding:
    """Route one question. Never raises: every failure means keyword routing."""
    keyword = detect_intent(question)

    def keywords(reason: str) -> Understanding:
        return Understanding(intent=keyword, source="keywords", reason=reason)

    if provider is None:
        return keywords("no model configured for understanding")
    if is_crisis(question) or keyword is Intent.WELLBEING:
        return keywords("crisis and wellbeing wording is routed by code, never by a model")

    started = time.perf_counter()
    messages = [ChatTurn(role="user", content=user_message(question, history))]
    try:
        complete_json = getattr(provider, "complete_json", None)
        if callable(complete_json):
            raw = complete_json(system=system_prompt(), messages=messages, max_tokens=MAX_TOKENS)
        else:
            raw = provider.complete(
                system=system_prompt(), messages=messages, max_tokens=MAX_TOKENS
            )
        data = _parse(raw if isinstance(raw, str) else "")
    except Exception as exc:  # noqa: BLE001 - any failure means "use keywords"
        logger.info("question understanding fell back to keywords: %s", exc)
        return keywords(f"model routing failed: {exc}")
    seconds = round(time.perf_counter() - started, 2)

    intent = _intent(data.get("intent"))
    if intent is None:
        return keywords(f"model chose an intent outside the list: {data.get('intent')!r}")
    if intent is Intent.UNKNOWN and keyword is not Intent.UNKNOWN:
        return keywords("model said unknown but keywords found a route")

    # "So I don't need any more, right?" is a question about being done. Credits
    # alone never answer it (seen live: qwen2.5:7b routed it to credits).
    if is_confirmation(question) and intent in (Intent.CREDITS, Intent.PROGRESS):
        intent = Intent.REMAINING_REQUIREMENTS

    return Understanding(
        intent=intent,
        codes=validate_codes(data.get("course_codes"), question, history, catalog_codes),
        follow_up_of=_intent(data.get("follow_up_of")),
        source="model",
        seconds=seconds,
    )
