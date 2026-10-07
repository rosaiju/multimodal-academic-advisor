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
from dataclasses import dataclass, field

from app.advisor.answers import GroundedAnswer, Intent, deterministic_answer
from app.advisor.consistency import allowed_codes, check_rephrasing, invented_codes
from app.advisor.facts import AdvisorFacts, render_facts
from app.llm.chat import ChatProvider, ChatTurn, ProviderUnavailable, get_chat_provider

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

Rephrase the prepared answer in a natural, friendly advising voice. Keep every
fact, number and course code exactly as given, each with the same meaning - a
percentage of progress stays a percentage of progress. You may reorder and reword. You
may not add academic claims that are not in the facts block."""


@dataclass
class Conversation:
    """One session's turns. Capped, so a long chat cannot grow without limit."""

    turns: list[ChatTurn] = field(default_factory=list)
    updated_at: float = field(default_factory=time.time)

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
) -> AdvisorReply:
    """Answer one question. Never raises for a provider problem."""
    question = question.strip()
    prepared: GroundedAnswer = deterministic_answer(facts, question)
    conversation = conversations.get(student_id, conversation_id)

    def finish(reply: AdvisorReply) -> AdvisorReply:
        conversation.add("user", question, keep=history_turns)
        conversation.add("assistant", reply.answer, keep=history_turns)
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
        notice: str, *, model: str | None = None, discarded: str | None = None
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
        )

    # Nothing to rephrase against: with no record, the deterministic text already
    # says the right thing and a model could only pad it. What-if and unlock answers
    # are never rephrased either: their figures come from re-running the engine on a
    # copy of the record, and a model only adds a chance to misstate a difference.
    if (
        not use_llm
        or facts is None
        or prepared.intent in (Intent.NO_RECORD, Intent.WHAT_IF, Intent.UNLOCKS)
    ):
        return finish(base)

    provider = provider or get_chat_provider()
    usable, reason = provider.available()
    if not usable:
        return finish(
            engine_answer(
                f"Answered from the degree engine. The language model is not "
                f"available ({reason}), which changes nothing about the numbers "
                f"above - they are computed, not generated."
            )
        )

    system = SYSTEM_PROMPT.format(
        institution=facts.institution or "the university",
        program=facts.program_name,
        facts=render_facts(facts),
        prepared=prepared.text,
    )

    try:
        raw = provider.complete(
            system=system,
            messages=_history_for_prompt(conversation, question, history_turns),
        )
    except ProviderUnavailable as exc:
        logger.info("chat provider unavailable, serving deterministic answer: %s", exc)
        return finish(
            engine_answer(
                f"Answered from the degree engine. The language model could not "
                f"be reached ({exc})."
            )
        )
    except Exception:  # noqa: BLE001 - a provider bug must not become a 500
        logger.exception("chat provider failed unexpectedly, serving deterministic answer")
        return finish(
            engine_answer(
                "Answered from the degree engine. The language model returned "
                "something that could not be used."
            )
        )

    text = _strip_reasoning(raw) if isinstance(raw, str) else ""
    problems = check_rephrasing(prepared, text, facts, question=question)
    if problems:
        logger.warning("discarding model answer: %s", "; ".join(problems))
        return finish(
            engine_answer(
                "The language model's phrasing did not match the degree engine "
                f"({problems[0]}), so it was discarded and you are reading the "
                "engine's answer.",
                model=provider.name,
                discarded=text,
            )
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
        )
    )
