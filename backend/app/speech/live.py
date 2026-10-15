"""Deepgram live transcription: what the student sees while they talk.

Two halves. `LiveTranscript` and `parse_live_message` are pure and decide what
the question box shows. `DeepgramLive` is the one connection to Deepgram's
live API.

The box shows finished phrases plus the phrase still being heard, and course
codes are converted on the combined text - so "computer science two forty" plus
a partial "three" becomes "COSC 243" as soon as the last word lands, and only
because COSC 243 is in the catalog.
"""

from __future__ import annotations

import json
import logging
import urllib.parse
from collections.abc import AsyncIterator, Iterable, Sequence
from dataclasses import dataclass, field

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake

from app.config import Settings, get_settings
from app.speech.base import (
    Final,
    LiveEvent,
    LiveSpeechProvider,
    Partial,
    SpeechError,
    UtteranceEnd,
)
from app.speech.course_codes import normalize_course_mentions

# The event types moved to app.speech.base; they stay importable from here.
__all__ = [
    "DeepgramLive",
    "Final",
    "LiveEvent",
    "LiveTranscript",
    "Partial",
    "UtteranceEnd",
    "live_url",
    "parse_live_message",
]

log = logging.getLogger(__name__)


def parse_live_message(raw: str | bytes) -> LiveEvent | None:
    """One Deepgram live message as an event, or None for anything we ignore."""
    try:
        message = json.loads(raw)
        kind = message.get("type")
        if kind == "UtteranceEnd":
            return UtteranceEnd()
        if kind != "Results":
            return None
        best = message["channel"]["alternatives"][0]
        text = str(best.get("transcript") or "")
        if message.get("is_final"):
            return Final(text, float(best.get("confidence") or 0.0)) if text.strip() else None
        return Partial(text)
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        log.warning("ignoring unexpected Deepgram live message: %r", raw[:200])
        return None


@dataclass
class LiveTranscript:
    """Finished phrases plus the current partial, as the student should see them."""

    _finals: list[str] = field(default_factory=list)
    _confidences: list[float] = field(default_factory=list)
    _partial: str = ""

    def add_partial(self, text: str) -> None:
        self._partial = text.strip()

    def add_final(self, text: str, confidence: float) -> None:
        text = text.strip()
        if text:
            self._finals.append(text)
            self._confidences.append(confidence)
        self._partial = ""

    def text(self, catalog_codes: Iterable[str]) -> str:
        joined = " ".join([*self._finals, self._partial]).strip()
        return normalize_course_mentions(joined, catalog_codes)

    @property
    def confidence(self) -> float:
        if not self._confidences:
            return 0.0
        return sum(self._confidences) / len(self._confidences)

    @property
    def heard_speech(self) -> bool:
        return bool(self._finals or self._partial)


def live_url(settings: Settings, keyterms: Sequence[str]) -> str:
    """The Deepgram live endpoint for these settings. The key is NOT in it."""
    base = settings.deepgram_base_url.rstrip("/")
    if base.startswith("https://"):
        base = "wss://" + base.removeprefix("https://")
    elif base.startswith("http://"):
        base = "ws://" + base.removeprefix("http://")
    params = [
        ("model", settings.deepgram_model),
        ("smart_format", "true"),
        # Students' questions about their records are not training data.
        ("mip_opt_out", "true"),
        # Partial phrases are what make the text appear while the student talks;
        # utterance_end_ms also requires them.
        ("interim_results", "true"),
        ("endpointing", "300"),
        # This long without words ends the session (SPEECH_PAUSE_MS).
        ("utterance_end_ms", str(max(settings.speech_pause_ms, 1000))),
    ]
    params += [("keyterm", term) for term in keyterms]
    return f"{base}/v1/listen?{urllib.parse.urlencode(params)}"


class DeepgramLive(LiveSpeechProvider):
    """One Deepgram live session. Every failure to connect is SpeechError("unavailable");
    a connection that drops later simply ends `events()`.
    """

    name = "deepgram"

    def __init__(self) -> None:
        self._ws: ClientConnection | None = None
        self._model = get_settings().deepgram_model

    @property
    def model(self) -> str:
        return self._model

    @classmethod
    def availability(cls, settings: Settings) -> tuple[bool, str]:
        if not settings.deepgram_api_key:
            return False, "no Deepgram key is configured"
        return True, "ok"

    async def connect(
        self, keyterms: Sequence[str] = (), *, settings: Settings | None = None
    ) -> None:
        settings = settings or get_settings()
        self._model = settings.deepgram_model
        if not settings.deepgram_api_key:
            raise SpeechError("unavailable", "no Deepgram key is configured")
        try:
            self._ws = await connect(
                live_url(settings, keyterms),
                additional_headers={"Authorization": f"Token {settings.deepgram_api_key}"},
                open_timeout=settings.deepgram_timeout_seconds,
            )
        except (InvalidHandshake, OSError, TimeoutError) as exc:
            # The handshake response can name the account; it stays in the log.
            log.warning("Deepgram live connection failed: %s", exc)
            raise SpeechError("unavailable", "Deepgram live connection failed") from exc

    async def send_audio(self, chunk: bytes) -> None:
        if self._ws is None:
            return
        try:
            await self._ws.send(chunk)
        except ConnectionClosed:
            pass  # events() ends on its own; the session winds down from there

    async def finish(self) -> None:
        """Ask Deepgram to flush what it has and close."""
        if self._ws is None:
            return
        try:
            await self._ws.send(json.dumps({"type": "CloseStream"}))
        except ConnectionClosed:
            pass

    async def events(self) -> AsyncIterator[LiveEvent]:
        if self._ws is None:
            return
        try:
            async for raw in self._ws:
                event = parse_live_message(raw)
                if event is not None:
                    yield event
        except ConnectionClosed as exc:
            log.warning("Deepgram live connection dropped: %s", exc)

    async def close(self) -> None:
        if self._ws is not None:
            await self._ws.close()
