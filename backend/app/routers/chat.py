"""Conversational advisor endpoints.

The student id always comes from the access token, never from the request body.
A client that could name the record it asks about could ask about someone else's
degree - the same rule `/ingest/confirm` follows for writes.

Like `routers/audit.py`, this file holds no advising logic. It resolves a record,
hands it to `app.advisor`, and shapes the reply.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app.advisor.chat import AdvisorReply, ask, conversations
from app.advisor.facts import build_facts
from app.auth.dependencies import CurrentUser
from app.catalog.loader import CatalogError
from app.catalog.registry import registry
from app.config import get_settings
from app.ingestion.store import RecordStore, StoredRecordError
from app.llm.chat import get_chat_provider
from app.speech.deepgram import SpeechError, transcribe
from app.speech.keyterms import spoken_course_codes

router = APIRouter(prefix="/advisor", tags=["advisor"])

#: Offered to the UI as starting points. Every one of these is answerable from
#: the engine alone, so the suggestion chips work with no API key configured.
SUGGESTED_QUESTIONS = [
    "What courses do I still need to graduate?",
    "How many credits am I missing?",
    "What should I take next semester?",
    "Have I completed my major requirements?",
    "Why is this course recommended?",
]

#: Fixed strings. Deepgram's own error text can name the account or project, so
#: it is logged in app.speech and never forwarded.
VOICE_UNAVAILABLE = "Voice input isn't available right now."
VOICE_UNREADABLE = "That recording could not be read."
VOICE_NOT_AUDIO = "Upload an audio recording."
VOICE_TOO_LARGE = "That recording is too long. Keep questions under 30 seconds."


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    #: Groups turns into one session. Scoped to the authenticated student, so
    #: guessing another student's value reveals nothing.
    conversation_id: str = Field(default="default", max_length=64)
    program_id: str | None = None
    #: Set false to see the raw engine answer with no model involved. The UI uses
    #: it for a "show me the deterministic answer" toggle during the demo.
    use_llm: bool = True


class ChatResponse(BaseModel):
    answer: str
    intent: str
    #: "engine" or "engine+llm" - which text the student is actually reading.
    source: str
    grounded: bool
    citations: list[str]
    provider: str | None = None
    model: str | None = None
    notice: str | None = None
    conversation_id: str


class AdvisorHealth(BaseModel):
    """Whether the conversational layer can use a model right now.

    `degraded` is not an error state: the advisor answers from the degree engine
    either way, and the UI says which it is doing.
    """

    provider: str
    model: str
    llm_available: bool
    reason: str
    degraded: bool
    #: True when a Deepgram key is set. The UI hides the mic button otherwise.
    voice_available: bool
    suggested_questions: list[str]


class TranscribeResponse(BaseModel):
    text: str
    #: Deepgram's 0-1 confidence. The UI asks the student to check the text
    #: below 0.6; it never decides anything on its own.
    confidence: float


def _store() -> RecordStore:
    return RecordStore(get_settings().student_record_dir)


@router.get("/health", response_model=AdvisorHealth)
def advisor_health() -> AdvisorHealth:
    """Asked by the UI on load, so the chat panel can say up front whether it is
    running on the engine alone. For Ollama this genuinely probes the server."""
    provider = get_chat_provider()
    available, reason = provider.available()
    return AdvisorHealth(
        provider=provider.provider_id,
        model=provider.name,
        llm_available=available,
        reason=reason,
        degraded=not available,
        voice_available=bool(get_settings().deepgram_api_key),
        suggested_questions=SUGGESTED_QUESTIONS,
    )


@router.post("/chat", response_model=ChatResponse)
def chat(user: CurrentUser, body: ChatRequest) -> ChatResponse:
    """Ask the advisor a question about your own degree progress.

    A missing record is not an error here. A student who has not uploaded a
    transcript gets a plain explanation of what to do next, which is more useful
    than a 404 the chat panel would have to translate anyway.
    """
    settings = get_settings()
    student_id = user.student_id
    facts = None

    try:
        stored = _store().load(student_id)
    except StoredRecordError:
        stored = None

    if stored is not None:
        resolved = body.program_id or stored.program_id
        if resolved is not None:
            try:
                program = registry.get(resolved)
            except CatalogError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            facts = build_facts(program, stored.to_student_record())

    reply: AdvisorReply = ask(
        facts,
        body.message,
        student_id=student_id,
        conversation_id=body.conversation_id,
        history_turns=settings.advisor_history_turns,
        use_llm=body.use_llm,
    )
    return ChatResponse(
        answer=reply.answer,
        intent=str(reply.intent),
        source=reply.source,
        grounded=reply.grounded,
        citations=reply.citations,
        provider=reply.provider,
        model=reply.model,
        notice=reply.notice,
        conversation_id=body.conversation_id,
    )


@router.delete("/chat/{conversation_id}")
def reset_conversation(user: CurrentUser, conversation_id: str) -> dict[str, bool]:
    """Forget one conversation. Only ever your own - the key includes your id."""
    return {"cleared": conversations.clear(user.student_id, conversation_id)}


@router.post("/transcribe", response_model=TranscribeResponse)
def transcribe_question(
    user: CurrentUser, audio: Annotated[UploadFile, File()]
) -> TranscribeResponse:
    """Turn a spoken question into text for the student to review.

    Voice is an input channel only: this returns text for the question box and
    never asks the advisor anything itself. The audio is never saved: it exists
    for this request only. (Starlette buffers an upload part over 1 MB in a temp
    file while the request runs; a spoken question is well under that.)

    `user` is unused beyond requiring a signed-in caller - an open endpoint
    would let anyone spend the team's Deepgram credit.
    """
    settings = get_settings()
    if not settings.deepgram_api_key:
        raise HTTPException(status_code=503, detail=VOICE_UNAVAILABLE)

    # Browsers send parameters ("audio/webm;codecs=opus"). Deepgram detects the
    # codec itself, so only the bare type is forwarded.
    mime_type = (audio.content_type or "").split(";", 1)[0].strip().lower()
    if not mime_type.startswith("audio/"):
        raise HTTPException(status_code=415, detail=VOICE_NOT_AUDIO)

    data = audio.file.read(settings.max_audio_bytes + 1)
    if len(data) > settings.max_audio_bytes:
        raise HTTPException(status_code=413, detail=VOICE_TOO_LARGE)
    if not data:
        raise HTTPException(status_code=422, detail=VOICE_UNREADABLE)

    try:
        result = transcribe(
            data, mime_type, spoken_course_codes(registry.list_programs()), settings=settings
        )
    except SpeechError as exc:
        if exc.kind == "unreadable":
            raise HTTPException(status_code=422, detail=VOICE_UNREADABLE) from None
        raise HTTPException(status_code=503, detail=VOICE_UNAVAILABLE) from None

    return TranscribeResponse(text=result.text, confidence=result.confidence)
