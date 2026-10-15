"""OpenAI speech-to-text over plain HTTP (`urllib`, like Deepgram).

OpenAI's transcription endpoint takes a finished recording, so `OpenAILive` fits
the streaming interface by buffering audio and answering once, on `finish()`:
no partial text while the student talks, one `Final` when they stop. The browser
cannot tell the difference except for the missing live text.

Same two rules as Deepgram: the key travels only in the Authorization header, and
OpenAI's own error text is logged and never raised.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import urllib.error
import urllib.request
import uuid
from collections.abc import AsyncIterator, Sequence

from app.config import Settings, get_settings
from app.speech.base import (
    Final,
    LiveEvent,
    LiveSpeechProvider,
    SpeechError,
    SpeechErrorKind,
    Transcript,
)

log = logging.getLogger(__name__)

_EXTENSIONS = {
    "audio/webm": "webm",
    "audio/ogg": "ogg",
    "audio/mp4": "mp4",
    "audio/mpeg": "mp3",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
}


def _multipart(fields: list[tuple[str, str]], filename: str, mime: str, audio: bytes):
    boundary = uuid.uuid4().hex
    parts: list[bytes] = []
    for name, value in fields:
        head = f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"'
        parts.append(f"{head}\r\n\r\n{value}\r\n".encode())
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {mime}\r\n\r\n".encode()
    )
    parts.append(audio)
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def transcribe(
    audio: bytes,
    mime_type: str,
    keyterms: Sequence[str] = (),
    *,
    settings: Settings | None = None,
) -> Transcript:
    """Transcribe one recording. Raises SpeechError; never returns a partial result."""
    settings = settings or get_settings()
    if not settings.openai_api_key:
        raise SpeechError("unavailable", "no OpenAI key is configured")

    fields = [("model", settings.openai_stt_model), ("response_format", "json")]
    if keyterms:
        # A prompt biases spelling toward these words; it is not an instruction.
        fields.append(("prompt", "Course codes: " + ", ".join(keyterms[:100])))
    if settings.openai_stt_model.startswith("gpt-4o"):
        fields.append(("include[]", "logprobs"))
    body, content_type = _multipart(
        fields, f"audio.{_EXTENSIONS.get(mime_type, 'webm')}", mime_type, audio
    )
    request = urllib.request.Request(
        settings.openai_base_url.rstrip("/") + "/v1/audio/transcriptions",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {settings.openai_api_key}",
            "Content-Type": content_type,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=settings.openai_stt_timeout_seconds) as r:
            raw = r.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:500].decode("utf-8", "replace")
        log.warning("OpenAI returned HTTP %s: %s", exc.code, detail)
        kind: SpeechErrorKind = "unreadable" if exc.code == 400 else "unavailable"
        raise SpeechError(kind, f"OpenAI returned HTTP {exc.code}") from exc
    except OSError as exc:
        log.warning("OpenAI could not be reached: %s", exc)
        raise SpeechError("unavailable", "OpenAI could not be reached") from exc
    return _parse(raw)


def _parse(raw: bytes) -> Transcript:
    try:
        data = json.loads(raw)
        text = str(data["text"]).strip()
        logprobs = [float(t["logprob"]) for t in data.get("logprobs") or []]
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        log.warning("OpenAI reply was not in the documented shape: %r", raw[:200])
        raise SpeechError("unavailable", "OpenAI's reply was not in the expected shape") from exc
    # Geometric-mean token probability. 0.0 when OpenAI gave none: the UI then
    # asks the student to check the text, which is the safe direction.
    confidence = math.exp(sum(logprobs) / len(logprobs)) if logprobs else 0.0
    return Transcript(text=text, confidence=confidence)


class OpenAILive(LiveSpeechProvider):
    """Buffers the stream; transcribes when the student stops."""

    name = "openai"
    streams_partials = False

    def __init__(self) -> None:
        self._settings = get_settings()
        self._audio = bytearray()
        self._keyterms: Sequence[str] = ()
        self._finished = False

    @property
    def model(self) -> str:
        return self._settings.openai_stt_model

    @classmethod
    def availability(cls, settings: Settings) -> tuple[bool, str]:
        if not settings.openai_api_key:
            return False, "no OpenAI key is configured"
        return True, "ok"

    async def connect(
        self, keyterms: Sequence[str] = (), *, settings: Settings | None = None
    ) -> None:
        self._settings = settings or get_settings()
        if not self._settings.openai_api_key:
            raise SpeechError("unavailable", "no OpenAI key is configured")
        self._keyterms = keyterms

    async def send_audio(self, chunk: bytes) -> None:
        self._audio += chunk

    async def finish(self) -> None:
        self._finished = True

    async def events(self) -> AsyncIterator[LiveEvent]:
        # MediaRecorder chunks concatenate into one valid file, so the whole
        # buffer is the recording. The relay's size cap bounds it.
        while not self._finished:
            await asyncio.sleep(0.05)
        if not self._audio:
            return
        try:
            result = await asyncio.to_thread(
                transcribe,
                bytes(self._audio),
                "audio/webm",
                self._keyterms,
                settings=self._settings,
            )
        except SpeechError:
            return  # the relay ends the session with whatever it has heard (nothing)
        if result.text:
            yield Final(result.text, result.confidence)

    async def close(self) -> None:
        self._finished = True
        self._audio.clear()
