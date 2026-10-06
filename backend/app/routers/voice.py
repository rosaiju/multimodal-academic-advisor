"""WS /advisor/listen - live voice input, relayed to Deepgram.

The browser never talks to Deepgram and never sees the key. It signs in with
its first message (a token in a URL ends up in logs), streams audio, and gets
back the whole transcript so far after every change, course codes already
converted. The session ends when Deepgram says the student paused, when the
student clicks stop, or at a limit - and always with a `done` carrying whatever
was heard, so nothing the student saw is thrown away.

Voice is an input channel only: nothing here asks the advisor anything.
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket

from app.auth.dependencies import user_for_token, user_store
from app.catalog.registry import registry
from app.config import Settings, get_settings
from app.speech.deepgram import SpeechError
from app.speech.keyterms import spoken_course_codes
from app.speech.live import DeepgramLive, Final, LiveTranscript, Partial, UtteranceEnd

log = logging.getLogger(__name__)

router = APIRouter(prefix="/advisor", tags=["advisor"])

#: Seconds the client has to send its `start` message.
START_TIMEOUT_SECONDS = 5.0
#: Hard ceiling on one session, from `ready`.
MAX_SESSION_SECONDS = 30.0
#: A session that hears nothing for this long ends empty ("Didn't catch that").
NO_SPEECH_SECONDS = 8.0
#: After asking Deepgram to finish, how long to wait for its last phrases.
FINISH_GRACE_SECONDS = 2.0
_WATCHDOG_TICK_SECONDS = 0.05


async def _send(websocket: WebSocket, payload: dict) -> bool:
    """Send, reporting False instead of raising when the browser has gone."""
    try:
        await websocket.send_json(payload)
        return True
    except Exception:  # any send failure means the browser has gone
        return False


async def _close(websocket: WebSocket) -> None:
    try:
        await websocket.close()
    except Exception:  # already closed is fine
        pass


async def _fail(websocket: WebSocket, reason: str) -> None:
    await _send(websocket, {"type": "error", "reason": reason})
    await _close(websocket)


async def _signed_in(websocket: WebSocket, settings: Settings) -> bool:
    """True if the first message is a `start` carrying a valid token, in time."""
    try:
        message = await asyncio.wait_for(websocket.receive(), START_TIMEOUT_SECONDS)
        frame = json.loads(message.get("text") or "")
        token = frame.get("token") if frame.get("type") == "start" else None
    except (TimeoutError, ValueError, AttributeError):
        return False
    if not isinstance(token, str) or not token:
        return False
    return user_for_token(token, settings, user_store(settings)) is not None


@router.websocket("/listen")
async def listen(websocket: WebSocket) -> None:
    await websocket.accept()
    settings = get_settings()
    if not await _signed_in(websocket, settings):
        await _fail(websocket, "unauthorized")
        return
    if not settings.deepgram_api_key:
        await _fail(websocket, "unavailable")
        return

    programs = registry.list_programs()
    catalog_codes = [course.code for program in programs for course in program.courses]
    live = DeepgramLive()
    try:
        await live.connect(spoken_course_codes(programs), settings=settings)
    except SpeechError:
        await _fail(websocket, "unavailable")
        return
    try:
        await _relay(websocket, live, catalog_codes, settings.max_audio_bytes)
    finally:
        await live.close()


async def _relay(
    websocket: WebSocket, live: DeepgramLive, catalog_codes: list[str], max_audio_bytes: int
) -> None:
    transcript = LiveTranscript()
    if not await _send(websocket, {"type": "ready"}):
        return

    async def from_browser() -> str:
        received = 0
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                return "disconnected"
            chunk = message.get("bytes")
            if chunk is not None:
                received += len(chunk)
                if received > max_audio_bytes:
                    return "audio_limit"
                await live.send_audio(chunk)
            elif message.get("text"):
                try:
                    if json.loads(message["text"]).get("type") == "stop":
                        return "stop"
                except (ValueError, AttributeError):
                    pass

    async def from_deepgram() -> str:
        async for event in live.events():
            if isinstance(event, UtteranceEnd):
                if transcript.heard_speech:
                    return "utterance_end"
                continue
            if isinstance(event, Partial):
                transcript.add_partial(event.text)
            elif isinstance(event, Final):
                transcript.add_final(event.text, event.confidence)
            text = transcript.text(catalog_codes)
            if not await _send(websocket, {"type": "transcript", "text": text}):
                return "disconnected"
        return "deepgram_closed"

    async def watchdog() -> str:
        loop = asyncio.get_running_loop()
        started = loop.time()
        while True:
            await asyncio.sleep(_WATCHDOG_TICK_SECONDS)
            elapsed = loop.time() - started
            if elapsed >= MAX_SESSION_SECONDS:
                return "time_limit"
            if not transcript.heard_speech and elapsed >= NO_SPEECH_SECONDS:
                return "no_speech"

    upstream = asyncio.create_task(from_browser())
    downstream = asyncio.create_task(from_deepgram())
    timer = asyncio.create_task(watchdog())
    finished, _ = await asyncio.wait(
        {upstream, downstream, timer}, return_when=asyncio.FIRST_COMPLETED
    )
    reason = next(iter(finished)).result()
    upstream.cancel()
    timer.cancel()
    if reason == "disconnected":
        downstream.cancel()
        return

    # Let Deepgram flush the phrase it was still working on before answering.
    await live.finish()
    if not downstream.done():
        await asyncio.wait({downstream}, timeout=FINISH_GRACE_SECONDS)
        downstream.cancel()
    log.info("voice session ended: %s", reason)
    await _send(
        websocket,
        {
            "type": "done",
            "text": transcript.text(catalog_codes),
            "confidence": round(transcript.confidence, 3),
        },
    )
    await _close(websocket)
