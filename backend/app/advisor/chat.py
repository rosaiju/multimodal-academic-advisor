"""The conversational advisor.

The order of operations is the whole design:

1. Build `AdvisorFacts` from the degree engine. Deterministic.
2. Produce a complete, correct answer from those facts. Deterministic.
3. ONLY THEN, if a chat provider is configured and reachable, ask it to rephrase
   that answer conversationally, constrained to the same facts.
4. Check what came back. If the model invented a course code that is not in the
   catalog or on the student's record, throw its answer away and ship the
   deterministic one.

Step 2 means the advisor is never down. Step 4 means a fluent wrong answer loses
to a plain right one. Neither step is optional, and the LLM is never consulted
about what a student needs - only about how to say what the engine already
decided.
"""

from __future__ import annotations

import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field

from app.advisor.answers import COURSE_CODE, GroundedAnswer, Intent, deterministic_answer
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

--- VERIFIED FACTS FOR THIS STUDENT ---
{facts}
--- END OF FACTS ---

A deterministic answer to the student's question has already been prepared:

--- PREPARED ANSWER ---
{prepared}
--- END PREPARED ANSWER ---

Rephrase the prepared answer in a natural, friendly advising voice. Keep every
fact, number and course code exactly as given. You may reorder and reword. You
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


def _known_codes(facts: AdvisorFacts) -> set[str]:
    """Every course code the model is allowed to mention."""
    codes = {c[0].replace(" ", "").upper() for c in facts.completed}
    for rec in [*facts.recommended, *facts.blocked]:
        codes.add(rec.course.code.replace(" ", "").upper())
    for block in facts.blocks:
        codes.update(c.replace(" ", "").upper() for c in block["options"])
    return codes


def _invented_codes(
    text: str, allowed: set[str], subjects: frozenset[str] = frozenset()
) -> list[str]:
    """Course codes in `text` that are not in `allowed`.

    This is the check that makes the LLM path safe to demo. A model that decides
    a student should take COSC 499 - a course that does not exist - is exactly
    the failure the whole architecture is built to prevent, so its answer is
    discarded rather than shown with a caveat.

    `subjects` is the set of prefixes the catalog actually defines, and it is what
    keeps this from firing on ordinary prose: "you need 101 more credits" parses
    as NEED101 under any naive pattern, and discarding a correct answer over that
    would be its own kind of wrong. With no subjects supplied nothing is flagged,
    because guessing which words are course subjects is exactly the mistake.
    """
    found = {f"{m.group(1).upper()}{m.group(2)}" for m in COURSE_CODE.finditer(text)}
    return sorted(
        code
        for code in found
        if code not in allowed and any(code.startswith(subject) for subject in subjects)
    )


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
    )

    # Nothing to rephrase against: with no record, the deterministic text already
    # says the right thing and a model could only pad it.
    if not use_llm or facts is None or prepared.intent is Intent.NO_RECORD:
        return finish(base)

    provider = provider or get_chat_provider()
    usable, reason = provider.available()
    if not usable:
        return finish(
            AdvisorReply(
                answer=prepared.text,
                intent=prepared.intent,
                source="engine",
                grounded=prepared.grounded,
                citations=prepared.citations,
                provider=provider.provider_id,
                notice=(
                    f"Answered from the degree engine. The language model is not "
                    f"available ({reason}), which changes nothing about the numbers "
                    f"above - they are computed, not generated."
                ),
            )
        )

    system = SYSTEM_PROMPT.format(
        institution=facts.institution or "the university",
        program=facts.program_name,
        facts=render_facts(facts),
        prepared=prepared.text,
    )

    try:
        text = provider.complete(
            system=system,
            messages=_history_for_prompt(conversation, question, history_turns),
        )
    except ProviderUnavailable as exc:
        logger.info("chat provider unavailable, serving deterministic answer: %s", exc)
        return finish(
            AdvisorReply(
                answer=prepared.text,
                intent=prepared.intent,
                source="engine",
                grounded=prepared.grounded,
                citations=prepared.citations,
                provider=provider.provider_id,
                notice=(
                    f"Answered from the degree engine. The language model could not "
                    f"be reached ({exc})."
                ),
            )
        )

    invented = _invented_codes(text, _known_codes(facts), facts.catalog_subjects)
    if invented:
        logger.warning("discarding model answer: invented course codes %s", ", ".join(invented))
        return finish(
            AdvisorReply(
                answer=prepared.text,
                intent=prepared.intent,
                source="engine",
                grounded=prepared.grounded,
                citations=prepared.citations,
                provider=provider.provider_id,
                model=provider.name,
                notice=(
                    "The language model's phrasing mentioned course codes that are "
                    f"not in your catalog or on your record ({', '.join(invented)}), "
                    "so it was discarded and you are reading the engine's answer."
                ),
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
                "Phrased by a language model. Every number and course code comes "
                "from the degree engine."
            ),
        )
    )
