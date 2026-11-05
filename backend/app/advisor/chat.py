"""The conversational advisor.

The order of operations is the whole design:

1. Build `AdvisorFacts` from the degree engine. Deterministic.
2. Produce a complete, correct answer from those facts. Deterministic.
3. ONLY THEN, if a chat provider is configured and reachable, ask it to rephrase
   that answer conversationally, constrained to the same facts.
4. Check what came back (`consistency.check_rephrasing`). If the model invented
   a course code, dropped or changed a credit figure, flipped a course's
   eligibility, called an in-progress course finished, lost the partial-catalog
   warning or answered something the engine declined to, throw its answer away
   and ship the deterministic one.

Step 2 means the advisor is never down. Step 4 means a fluent wrong answer loses
to a plain right one. Neither step is optional, and the LLM is never consulted
about what a student needs - only about how to say what the engine already
decided.
"""

from __future__ import annotations

import logging
import re
import time
from collections import OrderedDict
from dataclasses import dataclass, field, replace

from app.advisor.answers import GroundedAnswer, Intent, detect_intent, deterministic_answer
from app.advisor.consistency import allowed_codes, check_rephrasing, invented_codes
from app.advisor.facts import AdvisorFacts, render_facts
from app.advisor.links import Link, links_for
from app.advisor.understanding import understand
from app.config import get_settings
from app.llm.chat import (
    ChatProvider,
    ChatTurn,
    ProviderUnavailable,
    get_chat_provider,
    get_understanding_provider,
)
from app.speech.speakable import brief_text

logger = logging.getLogger(__name__)

#: Conversations held in memory, oldest evicted first. A demo does not need these
#: to survive a restart, and writing every half-finished question to disk beside
#: the academic records would be a worse trade than losing them.
MAX_CONVERSATIONS = 200

SYSTEM_PROMPT = """You are an academic advising assistant for {institution}.

You are speaking to one student about their own degree progress in {program}.

THE ONLY ACADEMIC FACTS YOU MAY USE ARE BELOW. They were computed by a
deterministic degree-audit engine from the student's confirmed transcript and the
official catalog. They are correct. Your job is to communicate them clearly.

ABSOLUTE RULES:
- Never state a requirement, course, prerequisite, credit count or percentage
  that does not appear in the facts block. If asked something the facts do not
  answer, say plainly that you cannot tell them that and suggest they ask an
  advisor.
- Never do arithmetic on credits. Every number you need is already computed.
- Never invent a course code. Only use codes that appear below.
- Do not promise a student they will graduate. The catalog is partial and only a
  human advisor can clear a degree.
- Keep it short and concrete - a few sentences or a short list. This is read by
  someone deciding what to register for.
- A course listed as IN PROGRESS is not completed and counts toward nothing yet.
  A BLOCKED course is not available to take. Never say otherwise.
- Keep every caveat in the prepared answer, including any note that the catalog
  is partial. If the prepared answer declines a question, decline it too.

--- VERIFIED FACTS FOR THIS STUDENT ---
{facts}
--- END OF FACTS ---

A deterministic answer to the student's question has already been prepared:

--- PREPARED ANSWER ---
{prepared}
--- END PREPARED ANSWER ---

Rephrase the prepared answer as the student's academic advisor would explain it
in person - warm, direct and encouraging, never vague:
- Lead with the answer to their question in one sentence.
- Then explain WHY, using the verified facts: which requirement a course belongs
  to, what it unlocks, what is still blocking something. Only facts from the block
  above - if the facts do not explain why, do not guess.
- Give one concrete next step the facts support (for example, the eligible course
  that unlocks the most), and say a human advisor confirms registration.
- If they sound worried, acknowledge it in a sentence and point to what is in their
  control. Do not diagnose, and do not promise outcomes or dates.
- You may end with ONE short offer to help further ("Want me to show what COSC220
  unlocks?"), naming only things in the facts.
- On a follow-up, use the conversation: answer what they are actually asking now,
  not the earlier question again.

Leave out any sentence about where the answer came from (for example "Every line
above comes from the catalog..."): you are writing it, and the app labels that.

Keep every fact, number and course code from the prepared answer, each with the
same meaning - a percentage of progress stays a percentage of progress. You may
reorder and reword. You may not add academic claims that are not in the facts block.

Format the answer in Markdown for a chat window: short paragraphs, a "-" bullet
list when naming several courses or requirements, and **bold** for the main
takeaway. No headings, tables or code blocks."""

#: Live voice: the same facts and prepared answer, but the model SUMMARIZES for
#: speech. Checked with check_rephrasing(brief=True): it may leave details out,
#: never distort them, and every caveat, blocker and refusal must survive.
LIVE_PROMPT = (
    SYSTEM_PROMPT.rsplit("Rephrase the prepared answer", 1)[0]
    + """This answer will be SPOKEN ALOUD to the student, so it must be short.

Answer the student's immediate question in a natural, friendly advising voice.
Start with the main takeaway and, when supported, the next action. Default to two
or three short sentences.

Summarize rather than reading the full prepared answer. You may omit supporting
details that are unnecessary to answer the question, but preserve every caveat,
limitation, and refusal in the prepared answer. Never omit information that would
change the student's registration decision or misrepresent a course's status.

For every fact you include, preserve its meaning and reproduce numbers and course
codes exactly. Use only the verified facts. If the necessary caveats require a
longer answer, prioritize accuracy over brevity. No lists, headings or markdown.

The student can see the full prepared answer on their screen while you speak. Name
at most three courses or requirements; for the rest, say they are on the screen
instead of reading them out."""
)


#: "Could you list out the full list?" / "list them all": a follow-up asking for
#: everything the last answer had. It names no topic, so it reuses the last one.
_LIST_ALL = re.compile(
    r"\b(full|whole|entire|complete) list\b"
    r"|\blist (them|it|those|these|everything|all)\b"
    r"|\ball of (them|those|these)\b"
    r"|\b(read|tell|say|name|give) (me )?(them|those|these|everything) all\b"
)

#: Prefixed to a spoken reply the student talked over.
INTERRUPTED_NOTE = "[The student interrupted this reply, so they may not have heard all of it.]"


@dataclass
class Conversation:
    """One session's turns. Capped, so a long chat cannot grow without limit."""

    turns: list[ChatTurn] = field(default_factory=list)
    updated_at: float = field(default_factory=time.time)
    #: The last question the engine could answer, for "list them all".
    last_question: str | None = None

    def add(self, role: str, content: str, *, keep: int) -> None:
        self.turns.append(ChatTurn(role=role, content=content))  # type: ignore[arg-type]
        if len(self.turns) > keep * 2:
            self.turns = self.turns[-keep * 2 :]
        self.updated_at = time.time()


class ConversationStore:
    """In-memory conversation history, keyed by (student_id, conversation_id).

    Keyed by student as well as conversation so one student's id can never
    address another student's history, even if a conversation id is guessed.
    """

    def __init__(self, max_conversations: int = MAX_CONVERSATIONS) -> None:
        self._data: OrderedDict[tuple[str, str], Conversation] = OrderedDict()
        self._max = max_conversations

    def recent_text(self, student_id: str, conversation_id: str) -> str:
        """What was said lately, without creating a conversation to find out."""
        found = self._data.get((student_id, conversation_id))
        return " ".join(turn.content for turn in found.turns) if found else ""

    def mark_interrupted(self, student_id: str, conversation_id: str) -> None:
        """The student talked over the last spoken reply: say so in history, so
        the model does not assume they heard all of it. Idempotent."""
        found = self._data.get((student_id, conversation_id))
        if not found:
            return
        for i in range(len(found.turns) - 1, -1, -1):
            turn = found.turns[i]
            if turn.role == "assistant":
                if not turn.content.startswith(INTERRUPTED_NOTE):
                    found.turns[i] = ChatTurn(
                        role="assistant", content=f"{INTERRUPTED_NOTE} {turn.content}"
                    )
                return

    def get(self, student_id: str, conversation_id: str) -> Conversation:
        key = (student_id, conversation_id)
        if key not in self._data:
            self._data[key] = Conversation()
            self._data.move_to_end(key)
            while len(self._data) > self._max:
                self._data.popitem(last=False)
        else:
            self._data.move_to_end(key)
        return self._data[key]

    def clear(self, student_id: str, conversation_id: str) -> bool:
        return self._data.pop((student_id, conversation_id), None) is not None

    def clear_student(self, student_id: str) -> int:
        keys = [k for k in self._data if k[0] == student_id]
        for key in keys:
            del self._data[key]
        return len(keys)


#: Process-wide. FastAPI runs one process in development; a multi-worker
#: deployment would need this in Redis, which is noted in the docs rather than
#: pretended away.
conversations = ConversationStore()


@dataclass(frozen=True)
class AdvisorReply:
    answer: str
    intent: Intent
    #: "engine" when the deterministic text is what the student sees;
    #: "engine+llm" when a model rephrased it. Surfaced in the UI - a student
    #: should be able to tell which they are reading.
    source: str
    grounded: bool
    citations: list[str]
    provider: str | None = None
    model: str | None = None
    #: Why the model was not used, or why its answer was rejected. Shown in the
    #: UI as a quiet note, never as an error.
    notice: str | None = None
    #: The engine's own answer. Equal to `answer` unless a model rephrased it;
    #: kept so a live check can compare the two.
    prepared: str = ""
    #: A model reply that failed the consistency check. Never shown to the student
    #: and not returned by the API; kept for logs and the live smoke test.
    discarded: str | None = None
    #: Which path chose the route to the engine answer: "model" when a language
    #: model read the question (and recent turns) and picked one of the engine's
    #: answers, "keywords" otherwise. It never means the model wrote the answer.
    understood_by: str = "keywords"
    #: Curated links (app/advisor/links.py) for answers the advisor cannot give.
    links: list[Link] = field(default_factory=list)
    #: Live voice: the student asked for everything, so speak the whole answer
    #: instead of the brief cut.
    read_in_full: bool = False
    #: Why the student is reading the engine's own text instead of a model-written
    #: answer. None when a model wrote it, or when no model was ever meant to
    #: (fixed text: no record, crisis, "list them all"). Otherwise one of
    #: "rephrase_off", "model_unavailable", "model_error", "check_failed".
    fallback: str | None = None
    #: Seconds the writing model took, when it was called.
    model_seconds: float | None = None


def _known_codes(facts: AdvisorFacts) -> set[str]:
    """Every course code the model is allowed to mention, before the question and
    the prepared answer are added (see `consistency.allowed_codes`)."""
    return allowed_codes(facts, "")


def _invented_codes(
    text: str, allowed: set[str], subjects: frozenset[str] = frozenset()
) -> list[str]:
    """Kept for callers of the original guard; see `consistency.invented_codes`."""
    return invented_codes(text, allowed, subjects)


_THINKING = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def _strip_reasoning(text: str) -> str:
    """Drop a reasoning model's <think> block. It is scratch work, not an answer,
    and it routinely contains numbers and codes the student was never told."""
    text = _THINKING.sub("", text)
    if "<think>" in text.lower():  # unterminated: the answer never started
        return ""
    return text.strip()


#: Engine sentences that say no language model wrote the text. True of the engine's
#: answer, false once a model has rewritten it - seen live, copied by Gemini.
_ENGINE_ONLY_CLAIM = re.compile(
    r"\s*Every line above comes from [^.]*not from a language model's opinion\.", re.IGNORECASE
)


def _drop_engine_only_claims(text: str) -> str:
    return _ENGINE_ONLY_CLAIM.sub("", text).strip()


def _short_reason(exc: Exception) -> str:
    """A provider error as one short line for the student: no response body."""
    text = str(exc).split(": {", 1)[0].splitlines()[0].strip()
    if "(503)" in text:
        return "the model is busy right now (503)"
    return text[:120]


def _history_for_prompt(conversation: Conversation, question: str, keep: int) -> list[ChatTurn]:
    turns = list(conversation.turns[-keep * 2 :])
    turns.append(ChatTurn(role="user", content=question))
    return turns


def ask(
    facts: AdvisorFacts | None,
    question: str,
    *,
    student_id: str,
    conversation_id: str = "default",
    provider: ChatProvider | None = None,
    history_turns: int = 12,
    use_llm: bool = True,
    understanding_provider: ChatProvider | None = None,
    understanding: str | None = None,
    rephrase: bool | None = None,
    live: bool = False,
) -> AdvisorReply:
    """Answer one question. Never raises for a provider problem.

    Routing: if a model is configured for understanding (ADVISOR_UNDERSTANDING=
    model, the default, and a reachable provider), it picks which engine answer
    the question wants, using the recent conversation. Otherwise keywords do. A
    caller that injects `provider` gets keyword routing unless it also injects
    `understanding_provider`, so a stub provider never receives routing calls it
    did not ask for.
    """
    question = question.strip()
    conversation = conversations.get(student_id, conversation_id)
    # "List them all" names no topic: answer the last question again, in full.
    asked = question
    read_in_full = False
    if (
        conversation.last_question
        and detect_intent(question) is Intent.UNKNOWN
        and _LIST_ALL.search(question.lower())
    ):
        asked = conversation.last_question
        read_in_full = live

    route_intent: Intent | None = None
    route_codes: list[str] | None = None
    understood_by = "keywords"
    if use_llm and facts is not None and facts.has_coursework:
        router = understanding_provider
        if router is None and provider is None:
            mode = understanding or get_settings().advisor_understanding
            router = get_understanding_provider() if mode == "model" else None
        if router is not None and router.available()[0]:
            catalog = frozenset(
                c.code.replace(" ", "").upper()
                for c in (facts.program.courses if facts.program else [])
            )
            understood = understand(
                asked, list(conversation.turns), provider=router, catalog_codes=catalog
            )
            if understood.source == "model":
                route_intent, route_codes = understood.intent, understood.codes or None
                understood_by = "model"
                logger.info(
                    "model routed question to %s (codes=%s, follow_up_of=%s) in %ss",
                    understood.intent,
                    understood.codes,
                    understood.follow_up_of,
                    understood.seconds,
                )

    prepared: GroundedAnswer = deterministic_answer(facts, asked, route_intent, route_codes)

    def finish(reply: AdvisorReply) -> AdvisorReply:
        # Links attach here, by intent, on every path - so a rephrased reply
        # carries the same curated links and never a model-supplied one.
        reply = replace(
            reply,
            understood_by=understood_by,
            links=links_for(reply.intent),
            read_in_full=read_in_full,
        )
        if prepared.intent not in (Intent.UNKNOWN, Intent.NO_RECORD):
            conversation.last_question = asked
        conversation.add("user", question, keep=history_turns)
        # Live voice: remember what was SPOKEN. An engine answer is cut down for
        # speech (speakable.brief_text) unless the student asked for all of it;
        # a model summary is spoken as written.
        heard = reply.answer
        if live and reply.source != "engine+llm" and not read_in_full:
            heard = brief_text(reply.answer)
        conversation.add("assistant", heard, keep=history_turns)
        return reply

    base = AdvisorReply(
        answer=prepared.text,
        intent=prepared.intent,
        source="engine",
        grounded=prepared.grounded,
        citations=prepared.citations,
        prepared=prepared.text,
    )

    def engine_answer(
        notice: str,
        *,
        fallback: str,
        model: str | None = None,
        discarded: str | None = None,
        seconds: float | None = None,
    ) -> AdvisorReply:
        return AdvisorReply(
            answer=prepared.text,
            intent=prepared.intent,
            source="engine",
            grounded=prepared.grounded,
            citations=prepared.citations,
            provider=provider.provider_id,
            model=model,
            notice=notice,
            prepared=prepared.text,
            discarded=discarded,
            fallback=fallback,
            model_seconds=seconds,
        )

    # Nothing to rephrase against: with no record, the deterministic text already
    # says the right thing and a model could only pad it.
    # A crisis reply is fixed text: no model rewrites it. Asked aloud for
    # everything: the engine's whole answer, not another summary.
    if (
        not use_llm
        or facts is None
        or prepared.intent in (Intent.NO_RECORD, Intent.CRISIS)
        or read_in_full
    ):
        return finish(base)

    if not (get_settings().advisor_rephrase if rephrase is None else rephrase):
        notice = "Answered from the degree engine; model rephrasing is switched off."
        if understood_by == "model":
            notice = (
                "A language model read your question and chose this answer; the "
                "degree engine wrote it. Model rephrasing is switched off."
            )
        return finish(replace(base, notice=notice, fallback="rephrase_off"))

    provider = provider or get_chat_provider()
    usable, reason = provider.available()
    if not usable:
        logger.info(
            "advisor answer: provider=%s unavailable (%s) source=engine fallback",
            provider.provider_id,
            reason,
        )
        return finish(
            engine_answer(
                f"Answered from the degree engine. The language model is not "
                f"available ({reason}), which changes nothing about the numbers "
                f"above - they are computed, not generated.",
                fallback="model_unavailable",
            )
        )

    system = (LIVE_PROMPT if live else SYSTEM_PROMPT).format(
        institution=facts.institution or "the university",
        program=facts.program_name,
        facts=render_facts(facts),
        prepared=prepared.text,
    )

    started = time.perf_counter()
    try:
        raw = provider.complete(
            system=system,
            messages=_history_for_prompt(conversation, question, history_turns),
        )
    except ProviderUnavailable as exc:
        logger.info("chat provider unavailable, serving deterministic answer: %s", exc)
        return finish(
            engine_answer(
                "Answered from the degree engine. The language model could not be "
                f"reached ({_short_reason(exc)}).",
                fallback="model_unavailable",
                seconds=round(time.perf_counter() - started, 2),
            )
        )
    except Exception:  # noqa: BLE001 - a provider bug must not become a 500
        logger.exception("chat provider failed unexpectedly, serving deterministic answer")
        return finish(
            engine_answer(
                "Answered from the degree engine. The language model returned "
                "something that could not be used.",
                fallback="model_error",
                seconds=round(time.perf_counter() - started, 2),
            )
        )

    seconds = round(time.perf_counter() - started, 2)
    text = _drop_engine_only_claims(_strip_reasoning(raw)) if isinstance(raw, str) else ""
    problems = check_rephrasing(prepared, text, facts, question=asked, brief=live)
    if problems:
        logger.warning(
            "advisor answer: provider=%s model=%s intent=%s seconds=%s check=REJECTED (%s)",
            provider.provider_id,
            provider.name,
            prepared.intent,
            seconds,
            "; ".join(problems),
        )
        return finish(
            engine_answer(
                "The language model's phrasing did not match the degree engine "
                f"({problems[0]}), so it was discarded and you are reading the "
                "engine's answer.",
                model=provider.name,
                discarded=text,
                fallback="check_failed",
                seconds=seconds,
            )
        )

    logger.info(
        "advisor answer: provider=%s model=%s intent=%s seconds=%s check=PASSED source=engine+llm",
        provider.provider_id,
        provider.name,
        prepared.intent,
        seconds,
    )

    return finish(
        AdvisorReply(
            answer=text,
            intent=prepared.intent,
            source="engine+llm",
            grounded=prepared.grounded,
            citations=prepared.citations,
            provider=provider.provider_id,
            model=provider.name,
            notice=(
                "Phrased by a language model and checked against the degree "
                "engine: every number, course code and eligibility statement "
                "comes from the engine."
            ),
            prepared=prepared.text,
            model_seconds=seconds,
        )
    )
