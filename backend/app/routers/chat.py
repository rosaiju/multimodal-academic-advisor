"""Conversational advisor endpoints.

The student id always comes from the access token, never from the request body.
A client that could name the record it asks about could ask about someone else's
degree - the same rule `/ingest/confirm` follows for writes.

Like `routers/audit.py`, this file holds no advising logic. It resolves a record,
hands it to `app.advisor`, and shapes the reply.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, File, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field

from app.advisor.chat import AdvisorReply, ask, conversations
from app.advisor.facts import build_facts
from app.advisor.links import Link
from app.auth.dependencies import CurrentUser
from app.catalog.loader import CatalogError
from app.catalog.registry import registry
from app.config import get_settings
from app.ingestion.store import RecordStore, StoredRecordError
from app.llm.chat import get_chat_provider
from app.speech import registry as speech_registry
from app.speech.base import SpeechError
from app.speech.clarify import (
    named_courses,
    readback_question,
    subjects_in,
    suggest_clarifications,
)
from app.speech.course_codes import normalize_course_mentions
from app.speech.keyterms import spoken_course_codes
from app.speech.registry import transcribe
from app.speech.speakable import to_speech
from app.speech.tts import synthesize

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
    #: "live" = a hands-free voice turn, sent without the student reviewing the
    #: transcript. Such turns are read back ("Did you mean COSC 241?") when unsure.
    mode: Literal["text", "live"] = "text"
    #: Live mode: the student talked over the previous spoken reply.
    interrupted: bool = False
    #: The live transcript's confidence, 0-1. Only read in live mode.
    confidence: float | None = Field(default=None, ge=0, le=1)


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
    #: "model" when a language model chose which engine answer this question
    #: gets, "keywords" when keyword routing did. The answer is the engine's either way.
    understood_by: str = "keywords"
    #: Why the engine's text is shown instead of a model-written answer: None when a
    #: model wrote it (or none was meant to), else "rephrase_off",
    #: "model_unavailable", "model_error" or "check_failed".
    fallback: str | None = None
    #: Seconds the writing model took, when it was called.
    model_seconds: float | None = None
    #: Curated links (never model-supplied) for questions the advisor cannot answer.
    links: list[dict[str, str]] = []
    #: Set instead of an answer when a live turn needs a course code confirmed.
    clarification: Clarification | None = None
    #: Live voice: the engine's full answer when the spoken one is a model
    #: summary, so "the rest is on your screen" is true.
    details: str | None = None
    #: Live voice: speak the whole answer (the student asked for all of it).
    read_in_full: bool = False


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
    #: True when the configured speech provider is usable. The UI hides the mic button otherwise.
    voice_available: bool
    #: Which speech vendor handles voice (SPEECH_PROVIDER); shown in the privacy note.
    speech_provider: str
    suggested_questions: list[str]


class Clarification(BaseModel):
    spoken: str
    candidates: list[str]
    question: str


class TranscribeResponse(BaseModel):
    text: str
    #: Deepgram's 0-1 confidence. The UI asks the student to check the text
    #: below 0.6; it never decides anything on its own.
    confidence: float
    #: Bare course numbers heard ("four fifty nine") offered back as questions.
    #: Omitted when there are none. Never applied to `text`.
    clarifications: list[Clarification] | None = None


def _store() -> RecordStore:
    return RecordStore(get_settings().student_record_dir)


@router.get("/health", response_model=AdvisorHealth)
def advisor_health() -> AdvisorHealth:
    """Asked by the UI on load, so the chat panel can say up front whether it is
    running on the engine alone. For Ollama this genuinely probes the server."""
    provider = get_chat_provider()
    available, reason = provider.available()
    # A key that is set can still be dead (expired, revoked): ask the provider.
    probe = getattr(provider, "probe", None)
    if available and probe is not None:
        available, reason = probe()
    return AdvisorHealth(
        provider=provider.provider_id,
        model=provider.name,
        llm_available=available,
        reason=reason,
        degraded=not available,
        voice_available=speech_registry.availability()[0],
        speech_provider=speech_registry.provider_name(),
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

    catalog_codes = [c.code for p in registry.list_programs() for c in p.courses]
    if body.mode == "live":
        if body.interrupted:
            conversations.mark_interrupted(student_id, body.conversation_id)
        context = subjects_in(
            conversations.recent_text(student_id, body.conversation_id), catalog_codes
        )
        confidence = 1.0 if body.confidence is None else body.confidence
        if question := readback_question(body.message, confidence, catalog_codes, context):
            # Ask back instead of answering: a misheard code must never be
            # answered aloud as if it were fact. The advisor is not called.
            return ChatResponse(
                answer=question.question,
                intent="readback",
                source="readback",
                grounded=True,
                citations=[],
                conversation_id=body.conversation_id,
                clarification=Clarification(**question.as_dict()),
            )

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
        live=body.mode == "live",
    )
    answer = reply.answer
    named = named_courses(body.message)
    if body.mode == "live" and len(named) == 1 and not answer.startswith(named[0]):
        # Say back the code that was heard, so a mishearing is audible.
        answer = f"For {named[0]}: {answer}"
    return ChatResponse(
        answer=answer,
        intent=str(reply.intent),
        source=reply.source,
        grounded=reply.grounded,
        citations=reply.citations,
        provider=reply.provider,
        model=reply.model,
        notice=reply.notice,
        conversation_id=body.conversation_id,
        understood_by=reply.understood_by,
        fallback=reply.fallback,
        model_seconds=reply.model_seconds,
        links=[link.as_dict() for link in reply.links],
        details=(
            reply.prepared
            if body.mode == "live" and reply.source == "engine+llm" and reply.prepared
            else None
        ),
        read_in_full=reply.read_in_full,
    )


@router.delete("/chat/{conversation_id}")
def reset_conversation(user: CurrentUser, conversation_id: str) -> dict[str, bool]:
    """Forget one conversation. Only ever your own - the key includes your id."""
    return {"cleared": conversations.clear(user.student_id, conversation_id)}


@router.post("/transcribe", response_model=TranscribeResponse, response_model_exclude_none=True)
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
    if not speech_registry.availability(settings)[0]:
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

    programs = registry.list_programs()
    try:
        result = transcribe(data, mime_type, spoken_course_codes(programs), settings=settings)
    except SpeechError as exc:
        if exc.kind == "unreadable":
            raise HTTPException(status_code=422, detail=VOICE_UNREADABLE) from None
        raise HTTPException(status_code=503, detail=VOICE_UNAVAILABLE) from None

    # "computer science two forty three" -> "COSC 243", but only for real courses.
    catalog_codes = [course.code for program in programs for course in program.courses]
    text = normalize_course_mentions(result.text, catalog_codes)
    context = subjects_in(conversations.recent_text(user.student_id, "default"), catalog_codes)
    questions = suggest_clarifications(text, catalog_codes, context)
    return TranscribeResponse(
        text=text,
        confidence=result.confidence,
        clarifications=[Clarification(**q.as_dict()) for q in questions] or None,
    )


#: Longer than any advisor answer; a cap so one request can't run up the TTS bill.
SPEAK_MAX_CHARS = 2000


class LinkModel(BaseModel):
    name: str = Field(max_length=200)
    url: str = Field(max_length=500)


class SpeakRequest(BaseModel):
    text: str
    links: list[LinkModel] = []
    #: Live voice: speak a short version (headline, a few items, caveats) of an
    #: engine answer. The full answer stays on screen.
    brief: bool = False


@router.post("/speak", response_class=Response)
def speak(user: CurrentUser, body: SpeakRequest) -> Response:
    """Speak an advisor answer aloud (mp3). The text is reformatted for speech by
    deterministic code only - no model sees it here."""
    if not body.text.strip():
        raise HTTPException(status_code=400, detail="nothing to say")
    if len(body.text) > SPEAK_MAX_CHARS:
        raise HTTPException(status_code=413, detail="text too long to speak")
    links = [Link(link.name, link.url) for link in body.links]
    try:
        audio = synthesize(to_speech(body.text, links, brief=body.brief))
    except SpeechError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return Response(content=audio, media_type="audio/mpeg")
