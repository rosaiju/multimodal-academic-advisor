"""Text-to-speech through Deepgram Aura: one POST, mp3 bytes back.

Stdlib HTTP, like app/speech/deepgram.py. The key travels only in the
Authorization header.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from app.config import Settings, get_settings
from app.speech.base import SpeechError

log = logging.getLogger(__name__)


def synthesize(text: str, *, settings: Settings | None = None) -> bytes:
    """Speak `text`. Raises SpeechError("unavailable", ...) on any failure."""
    settings = settings or get_settings()
    if not settings.deepgram_api_key:
        raise SpeechError("unavailable", "no Deepgram key is configured")
    query = urllib.parse.urlencode({"model": settings.deepgram_tts_model, "encoding": "mp3"})
    request = urllib.request.Request(
        f"{settings.deepgram_base_url.rstrip('/')}/v1/speak?{query}",
        data=json.dumps({"text": text}).encode(),
        method="POST",
        headers={
            "Authorization": f"Token {settings.deepgram_api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=settings.deepgram_timeout_seconds) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        log.warning("Deepgram TTS returned HTTP %s: %s", exc.code, exc.read()[:300])
        raise SpeechError("unavailable", f"Deepgram TTS returned HTTP {exc.code}") from exc
    except OSError as exc:
        log.warning("Deepgram TTS could not be reached: %s", exc)
        raise SpeechError("unavailable", "Deepgram TTS could not be reached") from exc
